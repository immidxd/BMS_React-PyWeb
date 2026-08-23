import React, { useCallback, useEffect, useRef, useState } from 'react';
import { textBlockHeight } from './svg';
import type { Background, CanvasFormat, ImageLayer, Layer, PostSpec, TextLayer } from './types';

/**
 * Полотно: те, що людина бачить, і те, за що вона тягне.
 *
 * Тут свідомо змішані два шари. Нижній — SVG-документ, який ЗБІГАЄТЬСЯ з
 * майбутнім растром до пікселя. Верхній — прозорі рамки шарів, за які можна
 * взятися мишею. Малювати рамки всередині SVG не можна: вони потрапили б у
 * готовий кадр.
 *
 * Фон рухається пальцем, а не повзунками: масштабувати й кадрувати
 * перетягуванням — це те, як влаштований будь-який редактор у телефоні, і
 * будь-яке інше рішення тут відчувалося б як крок назад.
 */

type DragState =
  | { kind: 'layer'; id: string; mode: 'move' | 'resize'; startX: number; startY: number;
      originX: number; originY: number; originW: number; originH: number; dragHeight: number }
  | { kind: 'background'; startX: number; startY: number; originX: number; originY: number };

type Props = {
  spec: PostSpec;
  format: CanvasFormat;
  svg: string;
  width: number;
  selectedId: string | null;
  /** Активний інструмент вирішує, що робить перетягування по порожньому місцю. */
  backgroundMode: boolean;
  showGrid: boolean;
  onSelect: (id: string | null) => void;
  onPatchLayer: (id: string, patch: Partial<TextLayer> & Partial<ImageLayer>) => void;
  onPatchBackground: (patch: Partial<Background>) => void;
  onSnapGuides?: (guides: { x: number | null; y: number | null }) => void;
};

const SNAP_TOLERANCE = 14;

const CanvasStage: React.FC<Props> = ({
  spec, format, svg, width, selectedId, backgroundMode, showGrid,
  onSelect, onPatchLayer, onPatchBackground,
}) => {
  const scale = width / format.width;
  const height = Math.round(format.height * scale);
  const dragRef = useRef<DragState | null>(null);
  const [guides, setGuides] = useState<{ x: number | null; y: number | null }>({ x: null, y: null });
  const [panning, setPanning] = useState(false);

  /** Прилипання до країв, полів і центру. Числа «на око» рідко бувають
   *  правильними — хай заголовок сам стає рівно. */
  const snap = useCallback((x: number, y: number, w: number, h: number) => {
    const margin = Math.round(format.width * 0.08);
    const xs: Array<[number, number]> = [
      [0, 0], [margin, margin], [format.width / 2 - w / 2, format.width / 2],
      [format.width - w, format.width], [format.width - margin - w, format.width - margin],
    ];
    const ys: Array<[number, number]> = [
      [0, 0], [margin, margin], [format.height / 2 - h / 2, format.height / 2],
      [format.height - h, format.height], [format.height - margin - h, format.height - margin],
    ];
    let guideX: number | null = null;
    let guideY: number | null = null;
    for (const [candidate, guide] of xs) {
      if (Math.abs(x - candidate) <= SNAP_TOLERANCE) { x = Math.round(candidate); guideX = guide; break; }
    }
    for (const [candidate, guide] of ys) {
      if (Math.abs(y - candidate) <= SNAP_TOLERANCE) { y = Math.round(candidate); guideY = guide; break; }
    }
    return { x, y, guideX, guideY };
  }, [format.height, format.width]);

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const dx = (event.clientX - drag.startX) / scale;
      const dy = (event.clientY - drag.startY) / scale;
      if (drag.kind === 'background') {
        onPatchBackground({
          offsetX: Math.round(drag.originX + dx),
          offsetY: Math.round(drag.originY + dy),
        });
        return;
      }
      if (drag.mode === 'move') {
        const snapped = snap(drag.originX + dx, drag.originY + dy, drag.originW, drag.dragHeight);
        onPatchLayer(drag.id, { x: snapped.x, y: snapped.y });
        setGuides({ x: snapped.guideX, y: snapped.guideY });
      } else {
        onPatchLayer(drag.id, {
          width: Math.max(40, Math.round(drag.originW + dx)),
          ...(drag.originH ? { height: Math.max(40, Math.round(drag.originH + dy)) } : {}),
        });
      }
    };
    const up = () => {
      dragRef.current = null;
      setGuides({ x: null, y: null });
      setPanning(false);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
  }, [onPatchBackground, onPatchLayer, scale, snap]);

  const startLayerDrag = (event: React.PointerEvent, layer: Layer, mode: 'move' | 'resize') => {
    event.stopPropagation();
    onSelect(layer.id);
    dragRef.current = {
      kind: 'layer', id: layer.id, mode,
      startX: event.clientX, startY: event.clientY,
      originX: layer.x, originY: layer.y, originW: layer.width,
      originH: layer.type === 'image' ? layer.height : 0,
      dragHeight: layer.type === 'image' ? layer.height : textBlockHeight(layer),
    };
  };

  const startBackgroundDrag = (event: React.PointerEvent) => {
    onSelect(null);
    if (!backgroundMode || spec.background.type !== 'asset') return;
    setPanning(true);
    dragRef.current = {
      kind: 'background',
      startX: event.clientX, startY: event.clientY,
      originX: spec.background.offsetX || 0,
      originY: spec.background.offsetY || 0,
    };
  };

  /** Колесо = масштаб фону. Саме так це працює скрізь, де є кадрування. */
  const onWheel = (event: React.WheelEvent) => {
    if (!backgroundMode || spec.background.type !== 'asset') return;
    event.preventDefault();
    const next = Math.min(4, Math.max(0.5,
      (spec.background.scale || 1) * (event.deltaY > 0 ? 0.96 : 1.04)));
    onPatchBackground({ scale: Number(next.toFixed(3)) });
  };

  const photoBackground = backgroundMode && spec.background.type === 'asset';

  return (
    <div className="flex flex-col items-center gap-2">
      <div
        onPointerDown={startBackgroundDrag}
        onWheel={onWheel}
        className={`relative overflow-hidden rounded-xl border border-gray-200 shadow-sm dark:border-gray-700 ${
          photoBackground ? (panning ? 'cursor-grabbing' : 'cursor-grab') : ''}`}
        style={{ width, height }}
      >
        <div className="pointer-events-none absolute inset-0 [&>svg]:h-full [&>svg]:w-full"
          dangerouslySetInnerHTML={{ __html: svg }} />

        {/* Сітка третин — та сама, що в камері телефона: композицію рахують
            по ній, а не «приблизно посередині». */}
        {(showGrid || panning) && (
          <div className="pointer-events-none absolute inset-0">
            {[1, 2].map(index => (
              <React.Fragment key={index}>
                <div className="absolute top-0 h-full w-px bg-white/40 mix-blend-difference"
                  style={{ left: `${(index * 100) / 3}%` }} />
                <div className="absolute left-0 w-full border-t border-white/40 mix-blend-difference"
                  style={{ top: `${(index * 100) / 3}%` }} />
              </React.Fragment>
            ))}
            <div className="absolute inset-[8%] border border-dashed border-white/30 mix-blend-difference" />
          </div>
        )}

        {guides.x !== null && (
          <div className="pointer-events-none absolute top-0 h-full w-px bg-[var(--bms-accent)]"
            style={{ left: guides.x * scale }} />
        )}
        {guides.y !== null && (
          <div className="pointer-events-none absolute left-0 w-full border-t border-[var(--bms-accent)]"
            style={{ top: guides.y * scale }} />
        )}

        {spec.layers.map(layer => {
          const layerHeight = layer.type === 'text' ? textBlockHeight(layer) : layer.height;
          const isSelected = layer.id === selectedId;
          return (
            <div
              key={layer.id}
              onPointerDown={event => startLayerDrag(event, layer, 'move')}
              className={`absolute cursor-move ${isSelected
                ? 'outline outline-1 outline-[var(--bms-accent)]'
                : 'outline outline-1 outline-transparent hover:outline-gray-300/70'}`}
              style={{
                left: layer.x * scale, top: layer.y * scale,
                width: layer.width * scale, height: Math.max(12, layerHeight * scale),
                transform: layer.rotation ? `rotate(${layer.rotation}deg)` : undefined,
              }}
            >
              {isSelected && (
                <span
                  onPointerDown={event => startLayerDrag(event, layer, 'resize')}
                  className="absolute -bottom-1.5 -right-1.5 h-3 w-3 cursor-nwse-resize rounded-sm bg-[var(--bms-accent)]"
                />
              )}
            </div>
          );
        })}
      </div>

      <div className="text-[10px] text-gray-400">
        {photoBackground
          ? 'Фон: тягніть кадр мишею, колесо — масштаб'
          : `${format.label} · ${format.width}×${format.height} px`}
      </div>
    </div>
  );
};

export default CanvasStage;
