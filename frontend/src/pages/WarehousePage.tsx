import React, { useState, useMemo, useRef, useCallback, useEffect } from 'react';

// ── Типи ──────────────────────────────────────────────────────────────────────
type SectorKey =
  | 'lito'
  | 'zyma'
  | 'vesna'
  | 'osin'
  | 'potochne_valizy'
  | 'potochne'
  | 'shafa'
  | 'stil'
  | 'korydor'
  | 'robocha_zona';

interface Sector {
  key: SectorKey;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  fill: string;
  fillDark: string;
  stroke: string;
  canHoldBoxes?: boolean;
  z?: number;
}

interface Box {
  id: string;
  // Позиція "на підлозі" (використовується якщо parentId===null)
  absX: number;
  absY: number;
  sectorKey: SectorKey;
  parentId: string | null; // якщо стоїть на іншій коробці
  w: number;
  h: number;
  productNumbers?: string[];
}

// ── Сектори (макет 9м × 8м, 1 одиниця SVG = 1 см) ────────────────────────────
const SECTORS: Sector[] = [
  { key: 'lito',  label: 'ЛІТО',  x: 0,   y: 0,   w: 150, h: 300, fill: '#fef3c7', fillDark: '#78350f', stroke: '#f59e0b', canHoldBoxes: true, z: 1 },
  { key: 'zyma',  label: 'ЗИМА',  x: 150, y: 0,   w: 150, h: 300, fill: '#dbeafe', fillDark: '#1e3a8a', stroke: '#60a5fa', canHoldBoxes: true, z: 1 },
  { key: 'vesna', label: 'ВЕСНА', x: 0,   y: 300, w: 150, h: 300, fill: '#dcfce7', fillDark: '#14532d', stroke: '#22c55e', canHoldBoxes: true, z: 1 },
  { key: 'osin',  label: 'ОСІНЬ', x: 150, y: 300, w: 150, h: 300, fill: '#ffedd5', fillDark: '#7c2d12', stroke: '#f97316', canHoldBoxes: true, z: 1 },
  { key: 'potochne_valizy', label: 'ПОТОЧНЕ (валізи)', x: 0, y: 600, w: 300, h: 200, fill: '#e0e7ff', fillDark: '#312e81', stroke: '#6366f1', canHoldBoxes: true, z: 1 },
  { key: 'potochne', label: 'ПОТОЧНЕ', x: 300, y: 0, w: 300, h: 600, fill: '#fce7f3', fillDark: '#831843', stroke: '#ec4899', canHoldBoxes: true, z: 1 },
  { key: 'shafa', label: 'ШАФА', x: 520, y: 300, w: 80,  h: 300, fill: '#f3e8ff', fillDark: '#581c87', stroke: '#a855f7', canHoldBoxes: false, z: 2 },
  { key: 'stil',  label: 'СТІЛ', x: 380, y: 540, w: 140, h: 60,  fill: '#cbd5e1', fillDark: '#475569', stroke: '#475569', canHoldBoxes: false, z: 2 },
  { key: 'korydor', label: 'КОРИДОР', x: 300, y: 600, w: 300, h: 200, fill: '#f1f5f9', fillDark: '#1e293b', stroke: '#64748b', canHoldBoxes: true, z: 1 },
  { key: 'robocha_zona', label: 'РОБОЧА ЗОНА', x: 600, y: 0, w: 300, h: 800, fill: '#dbeafe', fillDark: '#1e3a8a', stroke: '#3b82f6', canHoldBoxes: true, z: 1 },
];

// ── Стіни і двері ────────────────────────────────────────────────────────────
const DOOR_W = 70;
const WALL_THICKNESS = 8;
const MAX_STACK = 3;          // максимум коробок одна на одній
const STACK_OFFSET = 6;       // зсув кожної верхньої коробки (вгору-вправо)

interface Wall {
  axis: 'h' | 'v';
  fixed: number;
  start: number;
  end: number;
  doors: Array<{ start: number; end: number }>;
}

const DOOR1_Y = 670;
const DOOR2_Y = 670;
const DOOR3_X = 100;

const WALLS: Wall[] = [
  { axis: 'h', fixed: 0,   start: 0, end: 900, doors: [] },
  { axis: 'h', fixed: 800, start: 0, end: 900, doors: [{ start: DOOR3_X, end: DOOR3_X + DOOR_W }] },
  { axis: 'v', fixed: 0,   start: 0, end: 800, doors: [] },
  { axis: 'v', fixed: 900, start: 0, end: 800, doors: [] },
  { axis: 'v', fixed: 300, start: 0,   end: 600, doors: [] },
  { axis: 'v', fixed: 300, start: 600, end: 800, doors: [{ start: DOOR1_Y, end: DOOR1_Y + DOOR_W }] },
  { axis: 'v', fixed: 600, start: 0,   end: 600, doors: [] },
  { axis: 'v', fixed: 600, start: 600, end: 800, doors: [{ start: DOOR2_Y, end: DOOR2_Y + DOOR_W }] },
  { axis: 'h', fixed: 600, start: 300, end: 600, doors: [] },
];

// ── Утиліти ──────────────────────────────────────────────────────────────────
const sectorByKey = (k: SectorKey) => SECTORS.find(s => s.key === k)!;
const sectorCenter = (s: Sector) => ({ cx: s.x + s.w / 2, cy: s.y + s.h / 2 });

const findSectorAt = (x: number, y: number): Sector | null => {
  const matches = SECTORS.filter(s =>
    s.canHoldBoxes && x >= s.x && x <= s.x + s.w && y >= s.y && y <= s.y + s.h
  );
  if (!matches.length) return null;
  matches.sort((a, b) => (b.z ?? 1) - (a.z ?? 1));
  return matches[0];
};

// Глибина коробки від підлоги (0 = на підлозі)
const depthFromFloor = (boxId: string, byId: Record<string, Box>): number => {
  const b = byId[boxId];
  if (!b || !b.parentId) return 0;
  return 1 + depthFromFloor(b.parentId, byId);
};

// Висота піддерева — найдовший ланцюг від цієї коробки до листка (1 = тільки сама коробка)
const subtreeHeight = (boxId: string, boxes: Box[]): number => {
  const children = boxes.filter(b => b.parentId === boxId);
  if (!children.length) return 1;
  return 1 + Math.max(...children.map(c => subtreeHeight(c.id, boxes)));
};

// Ефективна позиція коробки (з урахуванням стака)
const effectivePos = (box: Box, byId: Record<string, Box>): { x: number; y: number } => {
  if (!box.parentId) return { x: box.absX, y: box.absY };
  const parent = byId[box.parentId];
  if (!parent) return { x: box.absX, y: box.absY };
  const p = effectivePos(parent, byId);
  return { x: p.x + STACK_OFFSET, y: p.y - STACK_OFFSET };
};

// Чи містить коробка точку (по ефективній позиції)
const boxContains = (box: Box, byId: Record<string, Box>, x: number, y: number): boolean => {
  const p = effectivePos(box, byId);
  return x >= p.x && x <= p.x + box.w && y >= p.y && y <= p.y + box.h;
};

// Знайти топову коробку у точці (виключаючи список ID)
const findBoxAt = (x: number, y: number, boxes: Box[], byId: Record<string, Box>, exclude: Set<string>): Box | null => {
  // Сортуємо за depth (вищі зверху → беруться першими)
  const sorted = [...boxes]
    .filter(b => !exclude.has(b.id))
    .sort((a, b) => depthFromFloor(b.id, byId) - depthFromFloor(a.id, byId));
  for (const b of sorted) {
    if (boxContains(b, byId, x, y)) return b;
  }
  return null;
};

// Усі нащадки коробки (включно з нею) — для виключення під час пошуку drop-target
const collectSubtree = (boxId: string, boxes: Box[], acc: Set<string>) => {
  acc.add(boxId);
  for (const c of boxes.filter(b => b.parentId === boxId)) collectSubtree(c.id, boxes, acc);
};

// ── Початкові коробки (демо: одна на одній) ─────────────────────────────────
const INITIAL_BOXES: Box[] = [
  { id: 'b1', absX: 20, absY: 30, sectorKey: 'lito', parentId: null, w: 50, h: 40, productNumbers: [] },
  { id: 'b2', absX: 0,  absY: 0,  sectorKey: 'lito', parentId: 'b1', w: 50, h: 40, productNumbers: [] },
];

// ── Компонент коробки ───────────────────────────────────────────────────────
interface BoxSVGProps {
  box: Box;
  posX: number;
  posY: number;
  isDark: boolean;
  isSelected: boolean;
  isDragging: boolean;
  isDropTarget: boolean;
  layerLevel: number; // 0..MAX_STACK-1 для відтінку
  onMouseDown: (e: React.MouseEvent) => void;
  onClick: (e: React.MouseEvent) => void;
}

const BoxSVG: React.FC<BoxSVGProps> = ({ box, posX, posY, isDark, isSelected, isDragging, isDropTarget, layerLevel, onMouseDown, onClick }) => {
  const baseFill = isDark ? '#92400e' : '#fbbf24';
  const baseStroke = isDark ? '#fde68a' : '#92400e';
  const stroke = isDropTarget ? '#10b981' : (isSelected ? '#ef4444' : baseStroke);
  const strokeW = isDropTarget ? 3 : (isSelected ? 2 : 1);
  const opacity = isDragging ? 0.7 : (0.75 + layerLevel * 0.08);
  return (
    <g
      onMouseDown={onMouseDown}
      onClick={onClick}
      style={{ cursor: isDragging ? 'grabbing' : 'grab' }}
    >
      {/* Тінь під коробкою */}
      {!isDragging && (
        <rect
          x={posX + 2} y={posY + 3}
          width={box.w} height={box.h}
          rx={3}
          fill="rgba(0,0,0,0.18)"
        />
      )}
      <rect
        x={posX} y={posY}
        width={box.w} height={box.h}
        rx={3}
        fill={baseFill}
        opacity={opacity}
        stroke={stroke}
        strokeWidth={strokeW}
      />
    </g>
  );
};

// Рендер однієї стіни як набору сегментів
const renderWall = (w: Wall, idx: number, doorColor: string) => {
  const segs: Array<{ from: number; to: number }> = [];
  let cursor = w.start;
  const sortedDoors = [...w.doors].sort((a, b) => a.start - b.start);
  for (const d of sortedDoors) {
    if (cursor < d.start) segs.push({ from: cursor, to: d.start });
    cursor = d.end;
  }
  if (cursor < w.end) segs.push({ from: cursor, to: w.end });
  return (
    <g key={`w-${idx}`} style={{ pointerEvents: 'none' }}>
      {segs.map((s, i) => {
        const props = w.axis === 'h'
          ? { x1: s.from, y1: w.fixed, x2: s.to, y2: w.fixed }
          : { x1: w.fixed, y1: s.from, x2: w.fixed, y2: s.to };
        return <line key={`seg-${i}`} {...props} stroke="#000" strokeWidth={WALL_THICKNESS} strokeLinecap="square" />;
      })}
      {sortedDoors.map((d, i) => {
        const props = w.axis === 'h'
          ? { x1: d.start, y1: w.fixed, x2: d.end, y2: w.fixed }
          : { x1: w.fixed, y1: d.start, x2: w.fixed, y2: d.end };
        return <line key={`door-${i}`} {...props} stroke={doorColor} strokeWidth={WALL_THICKNESS - 2} strokeLinecap="round" strokeDasharray="6 4" />;
      })}
    </g>
  );
};

// ── Основний компонент ──────────────────────────────────────────────────────
const VIEWBOX = { minX: -20, minY: -20, w: 940, h: 840 };

const WarehousePage: React.FC = () => {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hoveredSector, setHoveredSector] = useState<SectorKey | null>(null);
  const [selectedSector, setSelectedSector] = useState<SectorKey | null>(null);
  const [boxes, setBoxes] = useState<Box[]>(INITIAL_BOXES);
  const [selectedBoxId, setSelectedBoxId] = useState<string | null>(null);

  // drag state
  const dragRef = useRef<{
    boxId: string;
    grabOffsetX: number;  // зсув курсора відносно лівого-верхнього кута коробки
    grabOffsetY: number;
    startAbsX: number;
    startAbsY: number;
    startParentId: string | null;
    moved: boolean;
  } | null>(null);
  const [draggingBoxId, setDraggingBoxId] = useState<string | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);

  const [isDark, setIsDark] = useState<boolean>(() =>
    typeof document !== 'undefined' && document.documentElement.classList.contains('dark')
  );
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const obs = new MutationObserver(() => setIsDark(document.documentElement.classList.contains('dark')));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => obs.disconnect();
  }, []);

  const clientToSvg = useCallback((clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    const x = VIEWBOX.minX + ((clientX - rect.left) / rect.width) * VIEWBOX.w;
    const y = VIEWBOX.minY + ((clientY - rect.top) / rect.height) * VIEWBOX.h;
    return { x, y };
  }, []);

  // Швидкий доступ до коробок за id
  const boxesById = useMemo(() => {
    const o: Record<string, Box> = {};
    for (const b of boxes) o[b.id] = b;
    return o;
  }, [boxes]);

  // Початок drag — піднімає коробку (відʼєднує від батька, а її дітей лишає на місці зі збереженням позиції)
  const handleBoxMouseDown = (e: React.MouseEvent, box: Box) => {
    e.stopPropagation();
    e.preventDefault();
    const { x, y } = clientToSvg(e.clientX, e.clientY);
    const eff = effectivePos(box, boxesById);

    // Діти dragged box залишаються на тих самих абсолютних позиціях, просто стають "плаваючими" (parentId=null)
    // А сама dragged тимчасово отримує abs = ефективна (поки користувач не відпустить)
    setBoxes(prev => prev.map(b => {
      if (b.id === box.id) {
        return { ...b, absX: eff.x, absY: eff.y, parentId: null };
      }
      if (b.parentId === box.id) {
        const ce = effectivePos(b, boxesById);
        return { ...b, absX: ce.x, absY: ce.y, parentId: null };
      }
      return b;
    }));

    dragRef.current = {
      boxId: box.id,
      grabOffsetX: x - eff.x,
      grabOffsetY: y - eff.y,
      startAbsX: eff.x,
      startAbsY: eff.y,
      startParentId: box.parentId,
      moved: false,
    };
    setDraggingBoxId(box.id);
  };

  // Глобальні move/up
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const { x, y } = clientToSvg(e.clientX, e.clientY);
      const newX = x - d.grabOffsetX;
      const newY = y - d.grabOffsetY;
      if (Math.abs(newX - d.startAbsX) > 1 || Math.abs(newY - d.startAbsY) > 1) d.moved = true;

      // Оновлюємо позицію
      setBoxes(prev => prev.map(b => b.id === d.boxId ? { ...b, absX: newX, absY: newY } : b));

      // Шукаємо потенційну ціль для стакування — коробку під центром dragged
      const cx = newX + (boxesById[d.boxId]?.w || 50) / 2;
      const cy = newY + (boxesById[d.boxId]?.h || 40) / 2;
      const draggedSubtree = new Set<string>();
      collectSubtree(d.boxId, boxes, draggedSubtree);
      const target = findBoxAt(cx, cy, boxes, boxesById, draggedSubtree);
      if (target) {
        // Перевіряємо що стак ≤ MAX_STACK
        const draggedHeight = subtreeHeight(d.boxId, boxes); // тільки dragged (без дітей)=1, але можуть бути плаваючі діти
        const newChainLen = depthFromFloor(target.id, boxesById) + 1 + (draggedHeight - 1);
        if (newChainLen <= MAX_STACK) {
          setDropTargetId(target.id);
        } else {
          setDropTargetId(null);
        }
      } else {
        setDropTargetId(null);
      }
    };

    const onUp = () => {
      const d = dragRef.current;
      if (!d) return;
      const dragged = boxesById[d.boxId];
      if (!dragged) {
        dragRef.current = null;
        setDraggingBoxId(null);
        setDropTargetId(null);
        return;
      }

      const cx = dragged.absX + dragged.w / 2;
      const cy = dragged.absY + dragged.h / 2;

      // Чи є валідна ціль для стакування?
      const draggedSubtree = new Set<string>();
      collectSubtree(d.boxId, boxes, draggedSubtree);
      const target = findBoxAt(cx, cy, boxes, boxesById, draggedSubtree);

      setBoxes(prev => {
        const byId: Record<string, Box> = {};
        for (const b of prev) byId[b.id] = b;

        if (target) {
          const draggedH = subtreeHeight(d.boxId, prev);
          const newChain = depthFromFloor(target.id, byId) + 1 + (draggedH - 1);
          if (newChain <= MAX_STACK) {
            // Стакуємо: dragged.parentId = target; його сектор = сектор target
            return prev.map(b => b.id === d.boxId ? { ...b, parentId: target.id, sectorKey: target.sectorKey } : b);
          }
        }

        // Інакше — кидаємо на підлогу. Знаходимо сектор під центром.
        const sec = findSectorAt(cx, cy);
        if (sec) {
          const clampedX = Math.max(sec.x + 2, Math.min(sec.x + sec.w - dragged.w - 2, dragged.absX));
          const clampedY = Math.max(sec.y + 2, Math.min(sec.y + sec.h - dragged.h - 2, dragged.absY));
          return prev.map(b => b.id === d.boxId ? { ...b, absX: clampedX, absY: clampedY, sectorKey: sec.key, parentId: null } : b);
        }
        // Поза будь-яким сектором — повертаємо на старт
        return prev.map(b => b.id === d.boxId ? { ...b, absX: d.startAbsX, absY: d.startAbsY, parentId: null } : b);
      });

      dragRef.current = null;
      setDraggingBoxId(null);
      setDropTargetId(null);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [clientToSvg, boxes, boxesById]);

  const handleBoxClick = (e: React.MouseEvent, box: Box) => {
    e.stopPropagation();
    if (dragRef.current?.moved) return;
    setSelectedBoxId(box.id);
    setSelectedSector(box.sectorKey);
  };

  const selectedBox = boxes.find(b => b.id === selectedBoxId) || null;
  const selectedSectorData = selectedSector ? sectorByKey(selectedSector) : null;

  const addTestBox = (sectorKey: SectorKey) => {
    const sec = sectorByKey(sectorKey);
    const id = `box-${Date.now()}`;
    const offset = boxes.filter(b => b.sectorKey === sectorKey && !b.parentId).length;
    setBoxes(prev => [...prev, {
      id,
      sectorKey,
      absX: sec.x + 20 + (offset % 4) * 60,
      absY: sec.y + 20 + Math.floor(offset / 4) * 50,
      w: 50, h: 40,
      parentId: null,
      productNumbers: [],
    }]);
    setSelectedBoxId(id);
  };

  const removeBox = () => {
    if (!selectedBox) return;
    // При видаленні коробки — її діти стають "плаваючими" на тих самих позиціях
    setBoxes(prev => {
      const byId: Record<string, Box> = {};
      for (const b of prev) byId[b.id] = b;
      return prev
        .filter(b => b.id !== selectedBox.id)
        .map(b => {
          if (b.parentId === selectedBox.id) {
            const ep = effectivePos(b, byId);
            return { ...b, absX: ep.x, absY: ep.y, parentId: null };
          }
          return b;
        });
    });
    setSelectedBoxId(null);
  };

  const sortedSectors = useMemo(() => [...SECTORS].sort((a, b) => (a.z ?? 1) - (b.z ?? 1)), []);

  // Сортуємо коробки для рендеру: нижні (depth=0) спершу, верхні (вищий depth) — пізніше
  const renderOrder = useMemo(() => {
    return [...boxes].sort((a, b) => depthFromFloor(a.id, boxesById) - depthFromFloor(b.id, boxesById));
  }, [boxes, boxesById]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Склад</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Перетягуйте коробки мишкою. Щоб поставити одну на іншу — перетягніть і відпустіть зверху (зелена рамка = можна стакувати, макс ×{MAX_STACK}). Щоб зняти — просто потягніть верхню вбік.
          </p>
        </div>
        <div className="text-xs text-gray-500 dark:text-gray-400">Масштаб: 1 клітинка ≈ 1 м</div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-3 shadow-sm">
          <div className="w-full overflow-auto">
            <svg
              ref={svgRef}
              viewBox={`${VIEWBOX.minX} ${VIEWBOX.minY} ${VIEWBOX.w} ${VIEWBOX.h}`}
              className="w-full h-auto select-none"
              style={{ maxHeight: '75vh' }}
              onClick={() => { setSelectedSector(null); setSelectedBoxId(null); }}
            >
              <defs>
                <pattern id="grid" width="100" height="100" patternUnits="userSpaceOnUse">
                  <path d="M 100 0 L 0 0 0 100" fill="none" stroke={isDark ? '#374151' : '#e5e7eb'} strokeWidth="0.5" />
                </pattern>
              </defs>
              <rect x="0" y="0" width="900" height="800" fill="url(#grid)" />

              {/* Сектори */}
              {sortedSectors.map(s => {
                const isHover = hoveredSector === s.key;
                const isSel = selectedSector === s.key;
                const fill = isDark ? s.fillDark : s.fill;
                return (
                  <g key={s.key}
                    onMouseEnter={() => setHoveredSector(s.key)}
                    onMouseLeave={() => setHoveredSector(null)}
                    onClick={(e) => { e.stopPropagation(); setSelectedSector(s.key); setSelectedBoxId(null); }}
                    style={{ cursor: 'pointer' }}>
                    <rect x={s.x} y={s.y} width={s.w} height={s.h}
                      fill={fill}
                      fillOpacity={isHover || isSel ? 0.95 : 0.7}
                      stroke={isSel || isHover ? s.stroke : 'transparent'}
                      strokeWidth={isSel ? 2 : isHover ? 1.5 : 0}
                      strokeDasharray={s.canHoldBoxes === false ? undefined : '4 3'}
                      style={{ filter: isHover ? 'brightness(1.05)' : 'none', transition: 'all 0.2s ease' }} />
                    <text x={sectorCenter(s).cx} y={sectorCenter(s).cy}
                      textAnchor="middle" dominantBaseline="central"
                      fontSize={Math.min(28, Math.max(10, Math.min(s.w, s.h) / 6))}
                      fontWeight="600"
                      fill={isDark ? '#f3f4f6' : '#1f2937'}
                      style={{ pointerEvents: 'none', userSelect: 'none' }}>
                      {s.label}
                    </text>
                  </g>
                );
              })}

              {/* Стіни і двері */}
              {WALLS.map((w, i) => renderWall(w, i, isDark ? '#fbbf24' : '#a16207'))}

              {/* Коробки */}
              {renderOrder.map(b => {
                const pos = effectivePos(b, boxesById);
                const layer = depthFromFloor(b.id, boxesById);
                return (
                  <BoxSVG
                    key={b.id}
                    box={b}
                    posX={pos.x}
                    posY={pos.y}
                    isDark={isDark}
                    isSelected={selectedBoxId === b.id}
                    isDragging={draggingBoxId === b.id}
                    isDropTarget={dropTargetId === b.id}
                    layerLevel={layer}
                    onMouseDown={(e) => handleBoxMouseDown(e, b)}
                    onClick={(e) => handleBoxClick(e, b)}
                  />
                );
              })}

              {/* Підписи метрів */}
              <g style={{ pointerEvents: 'none' }}>
                {[0, 300, 600, 900].map((x, i) => (
                  <text key={i} x={x} y={-6} textAnchor="middle" fontSize="10" fill={isDark ? '#9ca3af' : '#6b7280'}>
                    {i === 0 ? '0' : `${i * 3}м`}
                  </text>
                ))}
                {[{ y: 0, label: '0' }, { y: 300, label: '3м' }, { y: 600, label: '6м' }, { y: 800, label: '8м' }].map((m, i) => (
                  <text key={`y${i}`} x={-8} y={m.y + 3} textAnchor="end" fontSize="10" fill={isDark ? '#9ca3af' : '#6b7280'}>
                    {m.label}
                  </text>
                ))}
              </g>
            </svg>
          </div>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 shadow-sm flex flex-col gap-3">
          {selectedBox ? (() => {
            const depth = depthFromFloor(selectedBox.id, boxesById);
            const subH = subtreeHeight(selectedBox.id, boxes);
            return (
              <>
                <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Коробка</h2>
                <div className="text-sm text-gray-600 dark:text-gray-300 space-y-1">
                  <div>Сектор: <span className="font-medium">{sectorByKey(selectedBox.sectorKey).label}</span></div>
                  <div>Розмір: {selectedBox.w} × {selectedBox.h} см</div>
                  <div>Рівень у стаку: <span className="font-medium">{depth + 1}</span> з {MAX_STACK}</div>
                  {subH > 1 && <div>Зверху: {subH - 1} коробок</div>}
                  {selectedBox.parentId && <div className="text-xs text-gray-500">Стоїть на: <code>{selectedBox.parentId}</code></div>}
                  <div>Товарів: {selectedBox.productNumbers?.length || 0}</div>
                </div>
                <div className="flex items-center gap-2 mt-2 flex-wrap">
                  <button onClick={removeBox}
                    className="ml-auto px-3 py-1 text-sm rounded border border-red-300 text-red-600 hover:bg-red-50 dark:border-red-700 dark:text-red-400 dark:hover:bg-red-900/20">Видалити</button>
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Перетягніть на іншу коробку щоб стакувати, або в порожнє місце сектора щоб поставити окремо.
                </p>
              </>
            );
          })() : selectedSectorData ? (
            <>
              <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Сектор: {selectedSectorData.label}</h2>
              <div className="text-sm text-gray-600 dark:text-gray-300 space-y-1">
                <div>Габарити: {selectedSectorData.w} × {selectedSectorData.h} см</div>
                <div>Коробок у секторі: {boxes.filter(b => b.sectorKey === selectedSectorData.key).length}</div>
              </div>
              {selectedSectorData.canHoldBoxes ? (
                <button onClick={() => addTestBox(selectedSectorData.key)}
                  className="mt-2 px-3 py-1.5 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white font-medium">+ Додати тестову коробку</button>
              ) : (
                <p className="text-xs text-gray-500 dark:text-gray-400">Цей сектор не призначений для коробок (меблі).</p>
              )}
            </>
          ) : (
            <>
              <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Легенда</h2>
              <ul className="text-sm space-y-1.5">
                {SECTORS.map(s => (
                  <li key={s.key}
                    className="flex items-center gap-2 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/40 px-1 py-0.5 rounded"
                    onMouseEnter={() => setHoveredSector(s.key)}
                    onMouseLeave={() => setHoveredSector(null)}
                    onClick={() => setSelectedSector(s.key)}>
                    <span className="inline-block w-3 h-3 rounded-sm border"
                      style={{ background: isDark ? s.fillDark : s.fill, borderColor: s.stroke }} />
                    <span className="text-gray-700 dark:text-gray-200">{s.label}</span>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
                Натисніть на сектор для додавання коробок. Стакувати — перетягуванням однієї коробки на іншу (макс {MAX_STACK} в висоту). Знімати — потягнути вбік.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default WarehousePage;
