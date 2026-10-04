import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { PhotoCrop, PhotoEdit } from '../../types/photoEdit';

/**
 * Кадрування 1:1 — як «Обрізати» в галереї iPhone.
 *
 * Рамка квадратна й нерухома, під нею рухається знімок: тягни — зсув,
 * щипок/⌘+колесо — масштаб, колесо — зсув, ↺ — поворот на 90°. Можна
 * віддалити так, що знімок стане меншим за рамку: поля заллються білим
 * (вертикальне взуття влазить у квадрат цілим).
 *
 * Компонент НІЧОГО не ріже сам — повертає опис кадру в частках знімка
 * (`PhotoEdit`), а пікселі вирізає бекенд з ОРИГІНАЛУ (services/photo_edit.py).
 * Тому прев'ю тут може бути будь-якого розміру — результат від нього не залежить.
 *
 * Кілька знімків — черга: Enter/«Готово» переходить до наступного,
 * «Решта по центру» віддає всім неопрацьованим центральний квадрат.
 */

export type { PhotoCrop, PhotoEdit };
export interface CropItem { key: string; src: string; label?: string; }

interface Props {
  items: CropItem[];
  /** вже вибрані кадри (повторне відкриття) */
  initial?: Record<string, PhotoEdit | null | undefined>;
  title?: string;
  confirmLabel?: string;
  /** null для ключа = «залишити як є» (без кадрування) */
  onDone: (edits: Record<string, PhotoEdit | null>) => void;
  onCancel: () => void;
}

/** Знімок уже квадратний (з допуском на округлення камер/експорту). */
export const isSquare = (w: number, h: number) => w > 0 && h > 0 && Math.abs(w / h - 1) < 0.01;

/** Центральний квадрат знімка w×h (як «Решта по центру»); null — уже квадрат. */
export const centerSquareEdit = (w: number, h: number): PhotoEdit | null => {
  if (!(w > 0 && h > 0) || isSquare(w, h)) return null;
  const s = Math.min(w, h);
  const r = (n: number) => Math.round(n * 1e5) / 1e5;
  return { rotate: 0, crop: { x: r((1 - s / w) / 2), y: r((1 - s / h) / 2), w: r(s / w), h: r(s / h) } };
};

/** Розміри знімка (з урахуванням EXIF — так, як його малює браузер). */
export const loadImageSize = (src: string): Promise<{ w: number; h: number } | null> =>
  new Promise((resolve) => {
    const im = new Image();
    im.onload = () => resolve({ w: im.naturalWidth, h: im.naturalHeight });
    im.onerror = () => resolve(null);
    im.src = src;
  });

// Стан кадру одного знімка. Зсув — у частках сторони рамки, щоб зміна розміру
// вікна не зсувала кадр. `spin` — накопичений кут для анімації (−90, −180, …):
// з 0° у 270° CSS крутив би на три чверті в інший бік, а не на чверть назад.
interface View { rotate: 0 | 90 | 180 | 270; spin: number; zoom: number; px: number; py: number; }
const FRESH: View = { rotate: 0, spin: 0, zoom: 1, px: 0, py: 0 };
const MAX_ZOOM = 8;

function geometry(nw: number, nh: number, S: number, v: View) {
  const turned = v.rotate % 180 !== 0;
  const rw = turned ? nh : nw;
  const rh = turned ? nw : nh;
  const cover = S / Math.min(rw, rh);
  const minZoom = Math.min(rw, rh) / Math.max(rw, rh);   // «вмістити цілим»
  const zoom = Math.min(MAX_ZOOM, Math.max(minZoom, v.zoom));
  const scale = cover * zoom;
  const dw = rw * scale;
  const dh = rh * scale;
  // По кожній осі: більший за рамку знімок не відкриває порожнечу; менший — не
  // вилазить за рамку. Обидва випадки — одна межа |d − S| / 2.
  const limX = Math.abs(dw - S) / 2;
  const limY = Math.abs(dh - S) / 2;
  const px = Math.max(-limX, Math.min(limX, v.px * S));
  const py = Math.max(-limY, Math.min(limY, v.py * S));
  return { rw, rh, zoom, minZoom, scale, dw, dh, px, py };
}

function clampView(nw: number, nh: number, S: number, v: View): View {
  const g = geometry(nw, nh, S, v);
  return { rotate: v.rotate, spin: v.spin, zoom: g.zoom, px: g.px / S, py: g.py / S };
}

function viewToEdit(nw: number, nh: number, v: View): PhotoEdit | null {
  const S = 1000;                                   // будь-який — результат у частках
  const g = geometry(nw, nh, S, v);
  const ix = S / 2 + g.px - g.dw / 2;
  const iy = S / 2 + g.py - g.dh / 2;
  const crop = { x: -ix / g.dw, y: -iy / g.dh, w: S / g.dw, h: S / g.dh };
  const r = (n: number) => Math.round(n * 1e5) / 1e5;
  const full = Math.abs(crop.x) < 1e-3 && Math.abs(crop.y) < 1e-3
    && Math.abs(crop.w - 1) < 1e-3 && Math.abs(crop.h - 1) < 1e-3;
  if (v.rotate === 0 && full) return null;          // квадрат без змін — нічого різати
  return { rotate: v.rotate, crop: { x: r(crop.x), y: r(crop.y), w: r(crop.w), h: r(crop.h) } };
}

function editToView(nw: number, nh: number, e: PhotoEdit): View {
  const v: View = { ...FRESH, rotate: e.rotate, spin: e.rotate };
  if (!e.crop) return v;
  const S = 1000;
  const turned = e.rotate % 180 !== 0;
  const rw = turned ? nh : nw;
  const rh = turned ? nw : nh;
  const scale = S / (e.crop.w * rw);
  const dw = rw * scale;
  const dh = rh * scale;
  const ix = -e.crop.x * dw;
  const iy = -e.crop.y * dh;
  return {
    rotate: e.rotate,
    spin: e.rotate,
    zoom: scale / (S / Math.min(rw, rh)),
    px: (ix + dw / 2 - S / 2) / S,
    py: (iy + dh / 2 - S / 2) / S,
  };
}

const PhotoCropEditor: React.FC<Props> = ({ items, initial, title, confirmLabel, onDone, onCancel }) => {
  const [idx, setIdx] = useState(0);
  // Результат по ключах: undefined — ще не дійшли; null — «як є»; PhotoEdit — кадр.
  const [results, setResults] = useState<Record<string, PhotoEdit | null | undefined>>(() => ({ ...(initial || {}) }));
  const [views, setViews] = useState<Record<string, View>>({});
  const [sizes, setSizes] = useState<Record<string, { w: number; h: number } | null>>({});
  const [S, setS] = useState(0);
  const [active, setActive] = useState(false);       // зараз тягнуть/щипають — без анімації
  const [loaded, setLoaded] = useState<Record<string, boolean>>({});
  const stageRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; x: number; y: number; px: number; py: number } | null>(null);
  const idleTimer = useRef<number | undefined>(undefined);

  const item = items[idx];
  const size = item ? sizes[item.key] : undefined;
  const view = (item && views[item.key]) || FRESH;

  // Розмір рамки — від сцени (ResizeObserver: вікно BMS змінюють часто).
  useLayoutEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      setS(Math.max(160, Math.floor(Math.min(r.width - 80, r.height - 48))));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Розміри знімків — наперед, для всієї черги (і для мініатюр унизу).
  useEffect(() => {
    let alive = true;
    items.forEach((it) => {
      if (sizes[it.key] !== undefined) return;
      void loadImageSize(it.src).then((sz) => {
        if (!alive) return;
        setSizes((cur) => ({ ...cur, [it.key]: sz }));
        const init = initial?.[it.key];
        if (sz && init) setViews((cur) => (cur[it.key] ? cur : { ...cur, [it.key]: editToView(sz.w, sz.h, init) }));
      });
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  const setView = useCallback((next: View | ((v: View) => View)) => {
    if (!item || !size || !S) return;
    setViews((cur) => {
      const prev = cur[item.key] || FRESH;
      const v = typeof next === 'function' ? (next as (v: View) => View)(prev) : next;
      return { ...cur, [item.key]: clampView(size.w, size.h, S, v) };
    });
  }, [item, size, S]);

  const pulse = useCallback(() => {
    setActive(true);
    window.clearTimeout(idleTimer.current);
    idleTimer.current = window.setTimeout(() => setActive(false), 160);
  }, []);

  // Масштаб навколо точки (курсор/центр щипка) — як на телефоні: точка під
  // пальцем лишається під пальцем.
  const zoomAt = useCallback((factor: number, clientX?: number, clientY?: number) => {
    if (!size || !S || !stageRef.current) return;
    const r = stageRef.current.getBoundingClientRect();
    const cx = clientX === undefined ? 0 : clientX - (r.left + r.width / 2);
    const cy = clientY === undefined ? 0 : clientY - (r.top + r.height / 2);
    setView((v) => {
      const g0 = geometry(size.w, size.h, S, v);
      const z1 = Math.min(MAX_ZOOM, Math.max(g0.minZoom, v.zoom * factor));
      const k = z1 / g0.zoom;
      return { ...v, zoom: z1, px: (cx - (cx - g0.px) * k) / S, py: (cy - (cy - g0.py) * k) / S };
    });
  }, [size, S, setView]);

  // Колесо/щипок — неpassive слухач, інакше preventDefault не спрацює і
  // прокрутиться модал позаду.
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      pulse();
      if (e.ctrlKey || e.metaKey) {
        zoomAt(Math.exp(-e.deltaY * 0.01), e.clientX, e.clientY);
      } else if (S) {
        setView((v) => ({ ...v, px: v.px - e.deltaX / S, py: v.py - e.deltaY / S }));
      }
    };
    // Safari/WKWebView (вебвʼю BMS на Mac) щипок трекпада шле як gesture*-події.
    let lastScale = 1;
    const onGestureStart = (e: any) => { e.preventDefault(); lastScale = 1; };
    const onGestureChange = (e: any) => {
      e.preventDefault(); pulse();
      zoomAt(e.scale / lastScale, e.clientX, e.clientY);
      lastScale = e.scale;
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('gesturestart', onGestureStart as any, { passive: false } as any);
    el.addEventListener('gesturechange', onGestureChange as any, { passive: false } as any);
    return () => {
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('gesturestart', onGestureStart as any);
      el.removeEventListener('gesturechange', onGestureChange as any);
    };
  }, [zoomAt, setView, pulse, S]);

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { id: e.pointerId, x: e.clientX, y: e.clientY, px: view.px, py: view.py };
    setActive(true);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId || !S) return;
    setView((v) => ({ ...v, px: d.px + (e.clientX - d.x) / S, py: d.py + (e.clientY - d.y) / S }));
  };
  const onPointerUp = (e: React.PointerEvent) => {
    if (drag.current?.id === e.pointerId) drag.current = null;
    setActive(false);
  };

  const rotate = useCallback(() => {
    // Як на iPhone — проти годинникової. В описі кадру поворот за годинниковою.
    setView((v) => ({ ...v, rotate: (((v.rotate + 270) % 360) as View['rotate']), spin: v.spin - 90, zoom: 1, px: 0, py: 0 }));
  }, [setView]);
  // Скинути кадр, але не повертати знімок «назад через пів кола».
  const reset = useCallback(() => setView((v) => ({ ...FRESH, spin: Math.round(v.spin / 360) * 360 })), [setView]);
  const fitWhole = useCallback(() => setView((v) => ({ ...v, zoom: 0, px: 0, py: 0 })), [setView]);

  const finish = useCallback((res: Record<string, PhotoEdit | null | undefined>) => {
    const out: Record<string, PhotoEdit | null> = {};
    items.forEach((it) => {
      const r = res[it.key];
      if (r !== undefined) { out[it.key] = r; return; }
      // Не дійшли — центральний квадрат (те саме, що «Решта по центру»).
      const sz = sizes[it.key];
      out[it.key] = sz ? viewToEdit(sz.w, sz.h, views[it.key] || FRESH) : null;
    });
    onDone(out);
  }, [items, sizes, views, onDone]);

  const commit = useCallback((value: PhotoEdit | null) => {
    if (!item) return;
    const next = { ...results, [item.key]: value };
    setResults(next);
    // Наступний неопрацьований, інакше — кінець.
    const after = items.findIndex((it, i) => i > idx && next[it.key] === undefined);
    const before = items.findIndex((it) => next[it.key] === undefined);
    const to = after >= 0 ? after : before;
    if (to >= 0) setIdx(to); else finish(next);
  }, [item, items, idx, results, finish]);

  const confirm = useCallback(() => {
    if (!item || !size) { commit(null); return; }   // не відкрився — лишаємо як є
    commit(viewToEdit(size.w, size.h, view));
  }, [item, size, view, commit]);

  // Клавіші — у фазі перехоплення, щоб «Розкласти фото» і картка позаду їх не
  // бачили (Enter там = прикріпити, Esc = закрити).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = e.key;
      const handled = () => { e.preventDefault(); e.stopPropagation(); };
      if (k === 'Escape') { handled(); onCancel(); return; }
      if (k === 'Enter') { handled(); confirm(); return; }
      if (k === 'r' || k === 'R' || k === 'к' || k === 'К') { handled(); rotate(); return; }
      if (k === '0') { handled(); reset(); return; }
      if (k === '+' || k === '=') { handled(); zoomAt(1.15); return; }
      if (k === '-' || k === '_') { handled(); zoomAt(1 / 1.15); return; }
      const step = (e.shiftKey ? 40 : 8) / (S || 1);
      if (k === 'ArrowLeft') { handled(); setView((v) => ({ ...v, px: v.px - step })); return; }
      if (k === 'ArrowRight') { handled(); setView((v) => ({ ...v, px: v.px + step })); return; }
      if (k === 'ArrowUp') { handled(); setView((v) => ({ ...v, py: v.py - step })); return; }
      if (k === 'ArrowDown') { handled(); setView((v) => ({ ...v, py: v.py + step })); return; }
      // Решту клавіш не пускаємо в модали позаду (Delete там видаляє знімки),
      // але й не гасимо: системні ⌘-сполучення мають працювати.
      e.stopPropagation();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onCancel, confirm, rotate, reset, zoomAt, setView, S]);

  const g = size && S ? geometry(size.w, size.h, S, view) : null;
  const pending = useMemo(() => items.filter((it) => results[it.key] === undefined).length, [items, results]);
  const many = items.length > 1;
  const ease = active ? 'none' : 'transform 240ms cubic-bezier(.2,.8,.2,1), width 240ms cubic-bezier(.2,.8,.2,1), height 240ms cubic-bezier(.2,.8,.2,1)';

  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  const btn = 'inline-flex items-center justify-center h-9 px-3 rounded-full text-[13px] transition-colors disabled:opacity-40';

  return createPortal(
    // Портал рендериться в body, але React-події з нього СПЛИВАЮТЬ до батьків у
    // дереві компонентів — до фону модала з onClick={onClose}. Без цього бар'єра
    // будь-який клік у редакторі закривав «Розкласти фото» разом із ним.
    <div className="bms-dialog-host fixed inset-0 z-[90] flex flex-col bg-neutral-950 text-white select-none"
      role="dialog" aria-modal="true" aria-label="Кадрування 1:1"
      onClick={stop} onMouseDown={stop} onMouseUp={stop} onPointerDown={stop} onPointerUp={stop}
      onDoubleClick={stop} onContextMenu={stop} onWheel={stop}>
      {/* Шапка */}
      <div className="flex items-center gap-3 px-5 h-14 shrink-0">
        <button type="button" onClick={onCancel} className={`${btn} text-white/70 hover:text-white hover:bg-white/10`}>Скасувати</button>
        <div className="flex-1 text-center text-[13px] text-white/70 truncate">
          {title || 'Кадр 1:1'}
          {many && <span className="ml-2 text-white/40">{idx + 1} з {items.length}</span>}
        </div>
        <button type="button" onClick={confirm}
          className={`${btn} bg-white text-black font-semibold hover:bg-white/90`}>
          {confirmLabel && pending <= 1 ? confirmLabel : (pending > 1 ? 'Далі' : 'Готово')}
        </button>
      </div>

      {/* Сцена */}
      <div ref={stageRef}
        className="relative flex-1 min-h-0 overflow-hidden cursor-grab active:cursor-grabbing touch-none"
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
        onDoubleClick={(e) => { if (!g) return; pulse(); if (g.zoom > 1.01) reset(); else zoomAt(2, e.clientX, e.clientY); }}>
        {/* Біле поле під рамкою — те, чим заллються поля, якщо віддалити */}
        {S > 0 && (
          <div className="absolute left-1/2 top-1/2 bg-white"
            style={{ width: S, height: S, transform: 'translate(-50%,-50%)' }} />
        )}
        {item && g && (
          <div className="absolute left-1/2 top-1/2 will-change-transform"
            style={{
              width: g.dw, height: g.dh, transition: ease,
              transform: `translate(calc(-50% + ${g.px}px), calc(-50% + ${g.py}px))`,
            }}>
            <img key={item.key} src={item.src} alt="" draggable={false}
              onLoad={() => setLoaded((c) => ({ ...c, [item.key]: true }))}
              className="absolute left-1/2 top-1/2 max-w-none pointer-events-none"
              style={{
                width: view.rotate % 180 ? g.dh : g.dw,
                height: view.rotate % 180 ? g.dw : g.dh,
                transform: `translate(-50%,-50%) rotate(${view.spin}deg)`,
                transition: ease,
                opacity: loaded[item.key] ? 1 : 0,
              }} />
          </div>
        )}
        {item && size === null && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-white/60">
            Не вдалося відкрити знімок — його можна лишити як є
          </div>
        )}
        {/* Рамка: затемнення поза нею + сітка третин, поки рухають */}
        {S > 0 && (
          <div className="absolute left-1/2 top-1/2 pointer-events-none"
            style={{ width: S, height: S, transform: 'translate(-50%,-50%)', boxShadow: '0 0 0 200vmax rgba(10,10,10,0.72)' }}>
            <div className="absolute inset-0 ring-1 ring-white/90" />
            <div className="absolute inset-0 transition-opacity duration-200" style={{ opacity: active ? 1 : 0 }}>
              <div className="absolute inset-y-0 left-1/3 w-px bg-white/45" />
              <div className="absolute inset-y-0 left-2/3 w-px bg-white/45" />
              <div className="absolute inset-x-0 top-1/3 h-px bg-white/45" />
              <div className="absolute inset-x-0 top-2/3 h-px bg-white/45" />
            </div>
            {/* Кутики — як у системному редакторі */}
            {(['left-0 top-0 border-l-[3px] border-t-[3px]', 'right-0 top-0 border-r-[3px] border-t-[3px]',
              'left-0 bottom-0 border-l-[3px] border-b-[3px]', 'right-0 bottom-0 border-r-[3px] border-b-[3px]'] as const).map((c) => (
              <span key={c} className={`absolute w-5 h-5 border-white -m-[2px] ${c}`} />
            ))}
          </div>
        )}
      </div>

      {/* Інструменти */}
      <div className="shrink-0 flex items-center justify-center gap-2 px-5 pt-3 pb-2">
        <button type="button" onClick={rotate} title="Повернути на 90° (R)" className={`${btn} text-white/80 hover:bg-white/10`}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 3v5h5" /></svg>
        </button>
        <button type="button" onClick={fitWhole} title="Вмістити цілим — поля стануть білими" className={`${btn} text-white/80 hover:bg-white/10`}>Цілим</button>
        <button type="button" onClick={reset} title="Скинути (0)" className={`${btn} text-white/80 hover:bg-white/10`}>Скинути</button>
        <span className="w-px h-5 bg-white/15 mx-1" />
        <button type="button" onClick={() => commit(null)} title="Залишити цей знімок без кадрування"
          className={`${btn} text-white/60 hover:text-white hover:bg-white/10`}>Без кадрування</button>
        {many && pending > 1 && (
          <button type="button" onClick={() => finish(results)} title="Усім неопрацьованим — центральний квадрат"
            className={`${btn} text-white/60 hover:text-white hover:bg-white/10`}>Решта по центру</button>
        )}
      </div>

      {/* Черга */}
      {many && (
        <div className="shrink-0 flex justify-center gap-1.5 px-5 pb-4 overflow-x-auto">
          {items.map((it, i) => {
            const r = results[it.key];
            return (
              <button key={it.key} type="button" onClick={() => setIdx(i)} title={it.label || it.key}
                className={`relative w-12 h-12 rounded-md overflow-hidden shrink-0 transition ${
                  i === idx ? 'ring-2 ring-white' : 'opacity-60 hover:opacity-100'}`}>
                <img src={it.src} alt="" className="w-full h-full object-cover pointer-events-none" draggable={false} />
                {r !== undefined && (
                  <span className="absolute bottom-0.5 right-0.5 w-4 h-4 rounded-full bg-white text-black text-[9px] font-bold flex items-center justify-center">
                    {r ? '1:1' : '✓'}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
      <div className="shrink-0 pb-3 text-center text-[11px] text-white/35">
        тягни — зсув · щипок або ⌘+колесо — масштаб · подвійний клік — 2× · R — поворот · Enter — готово · Esc — скасувати
      </div>
    </div>,
    document.body,
  );
};

/** Прев'ю вибраного кадру без перерахунку пікселів: той самий знімок,
 *  зсунутий і масштабований CSS-ом у квадраті (поля — білі, як і в результаті). */
export const CroppedPreview: React.FC<{ src: string; edit: PhotoEdit; className?: string; style?: React.CSSProperties }> = ({ src, edit, className, style }) => {
  const c = edit.crop || { x: 0, y: 0, w: 1, h: 1 };
  const turned = edit.rotate % 180 !== 0;
  return (
    <div className={`relative aspect-square overflow-hidden bg-white ${className || ''}`} style={style}>
      <div className="absolute" style={{
        left: `${(-c.x / c.w) * 100}%`, top: `${(-c.y / c.h) * 100}%`,
        width: `${100 / c.w}%`, height: `${100 / c.h}%`,
      }}>
        <img src={src} alt="" draggable={false} className="absolute left-1/2 top-1/2 max-w-none pointer-events-none"
          style={{
            width: turned ? `${(c.w / c.h) * 100}%` : '100%',
            height: turned ? `${(c.h / c.w) * 100}%` : '100%',
            transform: `translate(-50%,-50%) rotate(${edit.rotate}deg)`,
          }} />
      </div>
    </div>
  );
};

export default PhotoCropEditor;
