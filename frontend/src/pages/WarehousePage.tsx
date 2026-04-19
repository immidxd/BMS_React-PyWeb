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
// Кольори — приглушена, матеріальна палітра (не "веселка")
const SECTORS: Sector[] = [
  { key: 'lito',  label: 'ЛІТО',  x: 0,   y: 0,   w: 150, h: 300, fill: '#f5efe0', fillDark: '#4a3a20', stroke: '#c4a97a', canHoldBoxes: true, z: 1 },
  { key: 'zyma',  label: 'ЗИМА',  x: 150, y: 0,   w: 150, h: 300, fill: '#e8eef5', fillDark: '#1e2d3d', stroke: '#7a9ab8', canHoldBoxes: true, z: 1 },
  { key: 'vesna', label: 'ВЕСНА', x: 0,   y: 300, w: 150, h: 300, fill: '#e8f0e8', fillDark: '#1e3020', stroke: '#7aab80', canHoldBoxes: true, z: 1 },
  { key: 'osin',  label: 'ОСІНЬ', x: 150, y: 300, w: 150, h: 300, fill: '#f2e8e0', fillDark: '#3d2010', stroke: '#b8886a', canHoldBoxes: true, z: 1 },
  { key: 'potochne_valizy', label: 'ПОТОЧНЕ (валізи)', x: 0, y: 600, w: 300, h: 200, fill: '#eceaf5', fillDark: '#252040', stroke: '#8a84b8', canHoldBoxes: true, z: 1 },
  { key: 'potochne', label: 'ПОТОЧНЕ', x: 300, y: 0, w: 300, h: 600, fill: '#f5eaec', fillDark: '#3d1e24', stroke: '#b87a88', canHoldBoxes: true, z: 1 },
  { key: 'shafa', label: 'ШАФА', x: 520, y: 300, w: 80,  h: 300, fill: '#ece8f0', fillDark: '#302040', stroke: '#9080b0', canHoldBoxes: false, z: 2 },
  { key: 'stil',  label: 'СТІЛ', x: 380, y: 540, w: 140, h: 60,  fill: '#dddbd8', fillDark: '#38352f', stroke: '#7a7570', canHoldBoxes: false, z: 2 },
  { key: 'korydor', label: 'КОРИДОР', x: 300, y: 600, w: 300, h: 200, fill: '#ededeb', fillDark: '#252523', stroke: '#8a8a86', canHoldBoxes: true, z: 1 },
  { key: 'robocha_zona', label: 'РОБОЧА ЗОНА', x: 600, y: 0, w: 300, h: 800, fill: '#e8edf5', fillDark: '#1a2230', stroke: '#6a84a8', canHoldBoxes: true, z: 1 },
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
  // Приглушені земляні тони для коробок
  const topFill    = isDark ? '#6b5a3e' : '#d4b896';  // верхня грань (світла)
  const frontFill  = isDark ? '#5a4a30' : '#c4a47e';  // передня грань (середня)
  const sideFill   = isDark ? '#4a3820' : '#a88860';  // бічна грань (темна)
  const strokeColor = isDropTarget ? '#3d8c5a' : isSelected ? '#8c3d3d' : (isDark ? '#7a6040' : '#8a6840');
  const strokeW    = isDropTarget ? 2.5 : isSelected ? 2 : 1;
  const alpha      = isDragging ? 0.65 : 1;

  // Ізометрична 3D-коробка: вид зверху-спереду
  const W = box.w;
  const H = box.h;
  const D = 10; // "глибина" в пікселях SVG — товщина верхньої грані

  // Передня грань (основна)
  const fx = posX;
  const fy = posY + D;
  // Верхня грань (паралелограм)
  // top-left → top-right → top-right-shifted → top-left-shifted
  const topPts = `${fx},${fy} ${fx + W},${fy} ${fx + W + D},${fy - D} ${fx + D},${fy - D}`;
  // Права бічна грань
  const rightPts = `${fx + W},${fy} ${fx + W},${fy + H} ${fx + W + D},${fy + H - D} ${fx + W + D},${fy - D}`;

  // Тінь (тільки якщо не drag)
  const shadowX = posX + D + 3;
  const shadowY = posY + H + 2;

  return (
    <g
      onMouseDown={onMouseDown}
      onClick={onClick}
      style={{ cursor: isDragging ? 'grabbing' : 'grab', opacity: alpha }}
      filter={!isDragging ? 'url(#box-drop-shadow)' : undefined}
    >
      {/* Тінь на підлозі */}
      {!isDragging && (
        <ellipse
          cx={posX + W / 2 + D / 2}
          cy={posY + H + D + 1}
          rx={W / 2 + 2}
          ry={4}
          fill="rgba(0,0,0,0.15)"
        />
      )}

      {/* Передня грань */}
      <rect
        x={fx} y={fy}
        width={W} height={H}
        fill={frontFill}
        stroke={strokeColor}
        strokeWidth={strokeW}
        rx={1}
      />

      {/* Верхня грань (паралелограм) */}
      <polygon points={topPts} fill={topFill} stroke={strokeColor} strokeWidth={strokeW} />

      {/* Права бічна грань */}
      <polygon points={rightPts} fill={sideFill} stroke={strokeColor} strokeWidth={strokeW} />

      {/* Стрічка/лінія на передній грані для деталі */}
      <line
        x1={fx + W * 0.5} y1={fy}
        x2={fx + W * 0.5} y2={fy + H}
        stroke={strokeColor} strokeWidth={0.7} strokeOpacity={0.4}
      />
      <line
        x1={fx} y1={fy + H * 0.45}
        x2={fx + W} y2={fy + H * 0.45}
        stroke={strokeColor} strokeWidth={0.7} strokeOpacity={0.4}
      />

      {/* Підсвічування верхнього лівого кута (gloss) */}
      <rect
        x={fx + 2} y={fy + 2}
        width={W * 0.35} height={H * 0.2}
        rx={1}
        fill="rgba(255,255,255,0.18)"
        style={{ pointerEvents: 'none' }}
      />

      {/* Рамка виділення */}
      {(isSelected || isDropTarget) && (
        <rect
          x={fx - 2} y={fy - D - 2}
          width={W + D + 4} height={H + D + 4}
          rx={3}
          fill="none"
          stroke={isDropTarget ? '#3d8c5a' : '#8c3d3d'}
          strokeWidth={2}
          strokeDasharray={isDropTarget ? '5 3' : undefined}
          style={{ pointerEvents: 'none' }}
        />
      )}
    </g>
  );
};

// Рендер однієї стіни — мінімалістичний (одна тонка лінія + м'яка тінь під нею)
const renderWall = (w: Wall, idx: number, doorColor: string, isDark: boolean) => {
  const segs: Array<{ from: number; to: number }> = [];
  let cursor = w.start;
  const sortedDoors = [...w.doors].sort((a, b) => a.start - b.start);
  for (const d of sortedDoors) {
    if (cursor < d.start) segs.push({ from: cursor, to: d.start });
    cursor = d.end;
  }
  if (cursor < w.end) segs.push({ from: cursor, to: w.end });

  const wallColor = isDark ? '#0d0d0d' : '#1a1714';
  const T = 5;

  return (
    <g key={`w-${idx}`} style={{ pointerEvents: 'none' }}>
      {segs.map((s, i) => {
        const props = w.axis === 'h'
          ? { x1: s.from, y1: w.fixed, x2: s.to, y2: w.fixed }
          : { x1: w.fixed, y1: s.from, x2: w.fixed, y2: s.to };
        return (
          <line key={`seg-${i}`} {...props}
            stroke={wallColor} strokeWidth={T} strokeLinecap="square" />
        );
      })}
      {sortedDoors.map((d, i) => {
        const props = w.axis === 'h'
          ? { x1: d.start, y1: w.fixed, x2: d.end, y2: w.fixed }
          : { x1: w.fixed, y1: d.start, x2: w.fixed, y2: d.end };
        return <line key={`door-${i}`} {...props}
          stroke={doorColor} strokeWidth={1.5} strokeLinecap="round" strokeDasharray="5 4" opacity={0.7} />;
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
                {/* Дрібна сітка підлоги */}
                <pattern id="grid-fine" width="25" height="25" patternUnits="userSpaceOnUse">
                  <path d="M 25 0 L 0 0 0 25" fill="none" stroke={isDark ? '#1f1f1f' : '#ebe9e3'} strokeWidth="0.4" />
                </pattern>
                {/* Тінь коробок */}
                <filter id="box-drop-shadow" x="-20%" y="-20%" width="150%" height="170%">
                  <feDropShadow dx="1" dy="2" stdDeviation="2" floodColor="rgba(0,0,0,0.3)" />
                </filter>
                {/* М'який світловий градієнт зверху для зон */}
                <linearGradient id="zone-light" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="rgba(255,255,255,0.30)" />
                  <stop offset="100%" stopColor="rgba(255,255,255,0)" />
                </linearGradient>
              </defs>

              {/* Фон підлоги */}
              <rect x="-20" y="-20" width="940" height="840" fill={isDark ? '#0e0e0e' : '#f6f4ef'} />
              <rect x="0" y="0" width="900" height="800" fill={isDark ? '#141414' : '#fbfaf6'} />
              <rect x="0" y="0" width="900" height="800" fill="url(#grid-fine)" />

              {/* Сектори — мінімалістично */}
              {sortedSectors.map(s => {
                const isHover = hoveredSector === s.key;
                const isSel = selectedSector === s.key;
                const baseFill = isDark ? s.fillDark : s.fill;
                const { cx, cy } = sectorCenter(s);
                const labelFs = Math.min(15, Math.max(9, Math.min(s.w, s.h) / 9));

                return (
                  <g key={s.key}
                    onMouseEnter={() => setHoveredSector(s.key)}
                    onMouseLeave={() => setHoveredSector(null)}
                    onClick={(e) => { e.stopPropagation(); setSelectedSector(s.key); setSelectedBoxId(null); }}
                    style={{ cursor: 'pointer', transition: 'all 0.15s ease' }}>

                    {/* Заливка */}
                    <rect x={s.x} y={s.y} width={s.w} height={s.h}
                      fill={baseFill}
                      fillOpacity={isHover || isSel ? 1 : 0.95}
                    />
                    {/* М'який світловий градієнт зверху */}
                    <rect x={s.x} y={s.y} width={s.w} height={Math.min(50, s.h * 0.35)}
                      fill="url(#zone-light)"
                      style={{ pointerEvents: 'none' }}
                    />
                    {/* Тонка hairline-рамка (показується тільки на hover/select) */}
                    {(isHover || isSel) && (
                      <rect x={s.x + 0.5} y={s.y + 0.5} width={s.w - 1} height={s.h - 1}
                        fill="none"
                        stroke={s.stroke}
                        strokeWidth={isSel ? 1.4 : 1}
                        style={{ pointerEvents: 'none' }}
                      />
                    )}
                    {/* Підпис */}
                    <text x={cx} y={cy}
                      textAnchor="middle" dominantBaseline="central"
                      fontSize={labelFs}
                      fontWeight="600"
                      letterSpacing="1.2"
                      fontFamily="ui-sans-serif, system-ui, sans-serif"
                      fill={isDark ? 'rgba(235,230,220,0.75)' : 'rgba(40,35,28,0.65)'}
                      style={{ pointerEvents: 'none', userSelect: 'none' }}>
                      {s.label}
                    </text>
                  </g>
                );
              })}

              {/* Стіни і двері */}
              {WALLS.map((w, i) => renderWall(w, i, isDark ? '#c8a060' : '#a16207', isDark))}

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
                  <text key={i} x={x} y={-6} textAnchor="middle" fontSize="9"
                    fill={isDark ? '#888' : '#999'} letterSpacing="0.3">
                    {i === 0 ? '0' : `${i * 3}м`}
                  </text>
                ))}
                {[{ y: 0, label: '0' }, { y: 300, label: '3м' }, { y: 600, label: '6м' }, { y: 800, label: '8м' }].map((m, i) => (
                  <text key={`y${i}`} x={-8} y={m.y + 3} textAnchor="end" fontSize="9"
                    fill={isDark ? '#888' : '#999'} letterSpacing="0.3">
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
                    <span className="inline-block w-3.5 h-3.5 rounded-sm shadow-sm border"
                      style={{
                        background: isDark ? s.fillDark : s.fill,
                        borderColor: s.stroke,
                        boxShadow: `inset 0 1px 0 rgba(255,255,255,0.3), 1px 1px 2px rgba(0,0,0,0.15)`
                      }} />
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
