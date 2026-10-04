import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { notify } from '../../ui/feedback';
import type { Product } from '../../types/product';
import PhotoCropEditor, { CroppedPreview, centerSquareEdit, isSquare, loadImageSize } from '../common/PhotoCropEditor';
import type { CropItem, PhotoEdit } from '../common/PhotoCropEditor';
import ProductNumberText from '../common/ProductNumberText';

/**
 * «Розкласти фото» — з теки «до розбору» по картках товарів.
 *
 * Знімки після зйомки ПЕРЕМІШАНІ: різні товари впереміш, жодної нумерації.
 * Тому тут немає «виділи діапазон» за замовчуванням — кожен знімок клікається
 * окремо (Shift + клік — діапазон, як у картці), а щоб швидко читати цінник,
 * поруч із сіткою стоїть велике превʼю того, на що навів. Ввів номер → Enter →
 * вибрані лягли в картку і зникли з сітки.
 *
 * Прикріплення йде тим самим шляхом, що й кнопка «Додати» в картці
 * (add_photos: іменування, WebP, R2) — паралельного шляху немає. Неквадратні
 * знімки перед прикріпленням проходять кадр 1:1 (PhotoCropEditor); ріже бекенд
 * з оригіналу, тож у R2 лягає вже квадрат.
 *
 * ШВИДКІСТЬ (тека «Взуття» — 2233 знімки):
 *  • плитка — окремий memo-компонент: наведення міняє лише превʼю, а не
 *    перемальовує 2000+ плиток (раніше кожен рух миші = повний ререндер сітки);
 *  • превʼю на наведення — із затримкою 90 мс і миттєвою підкладкою з уже
 *    завантаженої мініатюри: пробіг мишею по сітці не ставить у чергу десятки
 *    важких 1200-px запитів;
 *  • сітка домальовується порціями при прокручуванні + `content-visibility`;
 *  • мініатюри мають `v=mtime` → браузер кешує їх назавжди, бекенд — на диску.
 */

interface StagedFile { name: string; size: number; mtime: number; added?: number; }
interface Props {
  open: boolean;
  onClose: () => void;
  /** товари завозу — для швидкого вибору номера чіпом */
  products: Product[];
  /** категорія теки «до розбору»; типово за товарами завозу */
  defaultCategory?: string;
  /** Режим картки товару: номер відомий і НЕ змінюється — вибрані знімки
   *  лягають лише в цей товар, а поле номера стає підписом. */
  fixedNumber?: string;
  /** Куди класти за замовчуванням (у картці — активна вкладка галереї). */
  defaultKind?: 'real' | 'official';
  onAttached?: () => void;
}

const CATEGORIES = ['Взуття', 'Сумки', 'Одяг', 'Аксесуари', 'Інше'];
const TILE = 200;            // мінімальна ширина плитки в сітці, css px
const GRID_W = 400;          // ширина мініатюри (менша сторона) — ≈ плитка @2x
const PREVIEW_W = 1200;      // велике превʼю й редактор кадру
const PAGE = 240;            // скільки плиток домальовувати за раз

type SortKey = 'added' | 'shot' | 'name';
const SORTS: { key: SortKey; label: string; title: string }[] = [
  { key: 'added', label: 'Нові спершу', title: 'Останні додані в теку — зверху; всередині партії — у порядку зйомки' },
  { key: 'shot', label: 'За часом зйомки', title: 'Від найстаріших знімків до найновіших' },
  { key: 'name', label: 'За назвою', title: 'За назвою файлу (числа — як числа: 2 перед 10)' },
];
const SORT_STORE = 'bms.photoStaging.sort';
const readSort = (): SortKey => {
  try { const v = window.localStorage.getItem(SORT_STORE); if (v === 'added' || v === 'shot' || v === 'name') return v; } catch { /* приватний режим */ }
  return 'added';
};

const collator = new Intl.Collator('uk', { numeric: true, sensitivity: 'base' });
// Партія = файли, що лягли в теку разом (копіювання 500 знімків триває хвилини).
// Нова партія — якщо між сусідніми додаваннями пауза довша за 10 хв.
const BATCH_GAP_SEC = 600;

function sortFiles(files: StagedFile[], key: SortKey): StagedFile[] {
  const byShot = (a: StagedFile, b: StagedFile) => (a.mtime - b.mtime) || collator.compare(a.name, b.name);
  if (key === 'name') return [...files].sort((a, b) => collator.compare(a.name, b.name));
  if (key === 'shot') return [...files].sort(byShot);
  const byAdded = [...files].sort((a, b) => ((a.added ?? a.mtime) - (b.added ?? b.mtime)) || byShot(a, b));
  const batches: StagedFile[][] = [];
  let prev = -Infinity;
  for (const f of byAdded) {
    const t = f.added ?? f.mtime;
    if (t - prev > BATCH_GAP_SEC || batches.length === 0) batches.push([]);
    batches[batches.length - 1].push(f);
    prev = t;
  }
  return batches.reverse().flatMap((b) => b.sort(byShot));
}

const imgUrl = (cat: string, f: Pick<StagedFile, 'name' | 'mtime'>, w: number) =>
  `/api/photo-staging/image?category=${encodeURIComponent(cat)}&name=${encodeURIComponent(f.name)}&w=${w}&v=${f.mtime}`;

// ── Плитка ──────────────────────────────────────────────────────────────────
interface TileProps {
  file: StagedFile;
  src: string;
  order: number;            // 0 — не вибрано, інакше порядковий номер вибору
  edited: boolean;
  busy: boolean;
  onPick: (name: string, shift: boolean) => void;
  onHover: (name: string) => void;
  onRemove: (name: string) => void;
  onCrop: (name: string) => void;
  onSize: (name: string, w: number, h: number) => void;
}

const Tile = memo(function Tile({ file, src, order, edited, busy, onPick, onHover, onRemove, onCrop, onSize }: TileProps) {
  const isSel = order > 0;
  return (
    <div className="relative group" style={{ contentVisibility: 'auto', containIntrinsicSize: `auto ${TILE}px` } as React.CSSProperties}>
      <button type="button"
        onClick={(e) => onPick(file.name, e.shiftKey)}
        // Кожен рух скидає таймер превʼю: воно перемикається лише там, де курсор
        // ЗУПИНИВСЯ, а не на кожній плитці, через яку його провели.
        onMouseEnter={() => onHover(file.name)} onMouseMove={() => onHover(file.name)}
        className={`relative w-full aspect-square rounded-lg overflow-hidden border-2 bg-gray-100 dark:bg-gray-800 transition-[border-color,box-shadow] duration-100 ${
          isSel ? 'border-gray-900 dark:border-gray-100 ring-2 ring-gray-900/30' : 'border-transparent'}`}>
        <img src={src} alt="" loading="lazy" decoding="async" draggable={false}
          onLoad={(e) => { const im = e.currentTarget; onSize(file.name, im.naturalWidth, im.naturalHeight); }}
          className="w-full h-full object-cover pointer-events-none" />
        {isSel && (
          <span className="absolute top-1.5 left-1.5 min-w-[1.5rem] h-6 px-1 rounded-full bg-gray-900 text-white text-xs flex items-center justify-center">
            {order}
          </span>
        )}
        {edited && (
          <span className="absolute bottom-1.5 left-1.5 px-1.5 h-5 rounded-full bg-white/90 text-gray-900 text-[10px] font-semibold flex items-center shadow-sm"
            title="Кадр 1:1 вибрано">1:1</span>
        )}
      </button>
      {/* Кадр 1:1 саме цього знімка — щоб не вести курсор до превʼю через сусідні плитки. */}
      <button type="button" onClick={(e) => { e.stopPropagation(); onCrop(file.name); }} disabled={busy}
        title="Кадр 1:1"
        className="absolute top-1.5 right-9 w-6 h-6 rounded-full bg-black/55 text-white flex items-center justify-center
          opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-black/80 transition-opacity">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M6 2v14a2 2 0 0 0 2 2h14" /><path d="M18 22V8a2 2 0 0 0-2-2H2" /></svg>
      </button>
      {/* × — видалити САМЕ цей кадр; зʼявляється при наведенні, щоб сітка лишалась чистою. */}
      <button type="button" onClick={(e) => { e.stopPropagation(); onRemove(file.name); }} disabled={busy}
        title="Видалити з розбору"
        className="absolute top-1.5 right-1.5 w-6 h-6 rounded-full bg-black/55 text-white flex items-center justify-center
          opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-red-600 transition-opacity">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
      </button>
    </div>
  );
});

// ── Модал ───────────────────────────────────────────────────────────────────
const PhotoStagingModal: React.FC<Props> = ({ open, onClose, products, defaultCategory, fixedNumber, defaultKind, onAttached }) => {
  const [category, setCategory] = useState<string>(defaultCategory || 'Взуття');
  const [files, setFiles] = useState<StagedFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focused, setFocused] = useState<string | null>(null);
  const [pnum, setPnum] = useState(fixedNumber ? fixedNumber.replace(/^#/, '') : '');
  const [kind, setKind] = useState<'real' | 'official'>(defaultKind || 'real');
  const [sort, setSort] = useState<SortKey>(readSort);
  const [limit, setLimit] = useState(PAGE);
  // Кадри 1:1 за назвою файлу: PhotoEdit — кадр; null — «залишити як є» (вирішено).
  const [edits, setEdits] = useState<Record<string, PhotoEdit | null>>({});
  const [cropFor, setCropFor] = useState<{ items: CropItem[]; then?: (e: Record<string, PhotoEdit | null>) => void } | null>(null);
  const locked = !!fixedNumber;
  // Відкрили з іншої картки / іншої вкладки галереї — підхопити.
  useEffect(() => {
    if (!open) return;
    if (fixedNumber) setPnum(fixedNumber.replace(/^#/, ''));
    if (defaultKind) setKind(defaultKind);
    if (defaultCategory) setCategory(defaultCategory);
  }, [open, fixedNumber, defaultKind, defaultCategory]);
  const [busy, setBusy] = useState(false);
  // скільки знімків прикріплено до кожного номера ЗА ЦЮ СЕСІЮ — щоб бачити,
  // кому вже роздано, а кому ще ні
  const [given, setGiven] = useState<Record<string, number>>({});
  // …і скільки в картці ВЖЕ Є (реальних / офіційних) — інакше після
  // перезапуску чи з іншого сеансу все виглядає «без фото», і власник
  // підвʼязує повторно.
  const [existing, setExisting] = useState<Record<string, { real: number; official: number }>>({});
  const numbersKey = useMemo(() => {
    const seen = new Set<string>();
    for (const p of products) { const n = String(p.productnumber || ''); if (n) seen.add(n); }
    return Array.from(seen).join(',');
  }, [products]);
  const loadExisting = useCallback(async () => {
    if (!numbersKey) { setExisting({}); return; }
    try {
      const r = await fetch(`/api/photo-staging/counts?numbers=${encodeURIComponent(numbersKey)}`);
      if (!r.ok) return;
      const d = await r.json();
      setExisting(d.counts || {});
    } catch { /* лічильник допоміжний — тиша краща за помилку */ }
  }, [numbersKey]);
  useEffect(() => { if (open) loadExisting(); }, [open, loadExisting]);
  const inputRef = useRef<HTMLInputElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  const sorted = useMemo(() => sortFiles(files, sort), [files, sort]);
  const sortRef = useRef(sort); sortRef.current = sort;
  const byName = useMemo(() => new Map(files.map((f) => [f.name, f])), [files]);
  const changeSort = (k: SortKey) => {
    setSort(k);
    try { window.localStorage.setItem(SORT_STORE, k); } catch { /* не критично */ }
  };
  // Нове впорядкування / тека — з початку сітки.
  useEffect(() => { setLimit(PAGE); gridRef.current?.scrollTo({ top: 0 }); }, [sort, category]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/photo-staging?category=${encodeURIComponent(category)}`);
      const d = await r.json();
      const list: StagedFile[] = d.files || [];
      setFiles(list);
      setSelected(new Set());
      setEdits({});
      autoRef.current.clear();
      setFocused(sortFiles(list, sortRef.current)[0]?.name ?? null);
    } catch {
      notify.error('Не вдалося прочитати теку «до розбору»');
    } finally {
      setLoading(false);
    }
  }, [category]);

  useEffect(() => { if (open) load(); }, [open, load]);
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 50); }, [open]);

  // Домальовувати сітку, коли до низу лишилось кілька рядів.
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    const root = gridRef.current;
    if (!open || !el || !root) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setLimit((l) => l + PAGE);
    }, { root, rootMargin: '1200px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, [open, sorted.length, limit]);

  // ── Вибір ──
  // Стабільні обробники для memo-плиток: читають свіжий стан через ref.
  const sortedRef = useRef(sorted); sortedRef.current = sorted;
  const lastPickRef = useRef<string | null>(null);
  const hoverTimer = useRef<number | undefined>(undefined);
  const onPick = useCallback((name: string, shift: boolean) => {
    window.clearTimeout(hoverTimer.current);   // клік важливіший за відкладене наведення
    const last = lastPickRef.current;
    if (shift && last && last !== name) {
      // Shift + клік — діапазон від попереднього кліку в тому порядку, який
      // бачить око (як у картці й Finder); ДОДАЄТЬСЯ до вибраного.
      const order = sortedRef.current;
      const a = order.findIndex((f) => f.name === last);
      const b = order.findIndex((f) => f.name === name);
      if (a >= 0 && b >= 0) {
        const [from, to] = a <= b ? [a, b] : [b, a];
        setSelected((cur) => {
          const next = new Set(cur);
          for (let i = from; i <= to; i++) next.add(order[i].name);
          return next;
        });
        lastPickRef.current = name;
        setFocused(name);
        return;
      }
    }
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
    lastPickRef.current = name;
    setFocused(name);
  }, []);

  // Hover-intent: превʼю міняється, коли курсор ЗУПИНИВСЯ на плитці (~150 мс без
  // руху). Шлях від вибраного знімка до кнопок превʼю веде через сусідні
  // плитки — раніше превʼю перескакувало на них, і кадрувати доводилось не те.
  const onHover = useCallback((name: string) => {
    window.clearTimeout(hoverTimer.current);
    hoverTimer.current = window.setTimeout(() => setFocused(name), 150);
  }, []);
  const cancelHover = useCallback(() => { window.clearTimeout(hoverTimer.current); }, []);
  useEffect(() => () => window.clearTimeout(hoverTimer.current), []);

  // Пропорції з уже завантажених мініатюр — щоб знати, кому потрібен кадр 1:1,
  // без окремого запиту.
  const sizesRef = useRef<Map<string, { w: number; h: number }>>(new Map());
  const onSize = useCallback((name: string, w: number, h: number) => { sizesRef.current.set(name, { w, h }); }, []);

  // ── Автокадр 1:1 ──
  // Вибраний неквадратний знімок одразу отримує центральний квадрат — у
  // превʼю видно результат, на плитці «1:1», і при «Прикріпити» редактор уже не
  // вискакує. Підправити — кнопкою кадру; «Без кадру» — лишити оригінал.
  // Автокадр знімається разом із вибором; ручний — лишається.
  const autoRef = useRef<Set<string>>(new Set());
  const editsRef = useRef(edits); editsRef.current = edits;
  const selectedRef = useRef(selected); selectedRef.current = selected;
  useEffect(() => {
    const drop = Array.from(autoRef.current).filter((n) => !selected.has(n));
    if (drop.length) {
      drop.forEach((n) => autoRef.current.delete(n));
      setEdits((c) => { const x = { ...c }; drop.forEach((n) => { delete x[n]; }); return x; });
    }
    const todo = Array.from(selected).filter((n) => !(n in editsRef.current));
    if (!todo.length) return;
    let cancelled = false;
    void (async () => {
      const add: Record<string, PhotoEdit> = {};
      for (const n of todo) {
        let sz = sizesRef.current.get(n);
        const f = byName.get(n);
        if (!sz && f) {
          const got = await loadImageSize(imgUrl(category, f, GRID_W));
          if (got) { sz = got; sizesRef.current.set(n, got); }
        }
        const e = sz ? centerSquareEdit(sz.w, sz.h) : null;
        if (e) add[n] = e;
      }
      if (cancelled || !Object.keys(add).length) return;
      setEdits((c) => {
        const x = { ...c };
        Object.entries(add).forEach(([n, e]) => {
          if (!(n in x) && selectedRef.current.has(n)) { x[n] = e; autoRef.current.add(n); }
        });
        return x;
      });
    })();
    return () => { cancelled = true; };
  }, [selected, byName, category]);

  const order = useMemo(() => {
    const m = new Map<string, number>();
    let i = 0;
    selected.forEach((n) => m.set(n, ++i));
    return m;
  }, [selected]);

  // ── Кадр 1:1 ──
  const cropItems = useCallback((names: string[]): CropItem[] => names
    .map((n) => byName.get(n)).filter(Boolean)
    .map((f) => ({ key: f!.name, src: imgUrl(category, f!, PREVIEW_W), label: f!.name })), [byName, category]);

  const openCrop = useCallback((names: string[]) => {
    const items = cropItems(names);
    if (items.length) setCropFor({ items });
  }, [cropItems]);
  // Стабільний обробник для memo-плиток (openCrop міняється разом зі списком).
  const openCropRef = useRef(openCrop); openCropRef.current = openCrop;
  const onCropTile = useCallback((name: string) => {
    window.clearTimeout(hoverTimer.current);
    setFocused(name);
    openCropRef.current([name]);
  }, []);

  // Неквадратні серед вибраних, яким кадр ще не вирішено.
  const needsCrop = useCallback(async (names: string[]) => {
    const out: string[] = [];
    for (const n of names) {
      if (n in edits) continue;
      let sz = sizesRef.current.get(n);
      const f = byName.get(n);
      if (!sz && f) {
        const got = await loadImageSize(imgUrl(category, f, GRID_W));
        if (got) { sz = got; sizesRef.current.set(n, got); }
      }
      if (sz && !isSquare(sz.w, sz.h)) out.push(n);
    }
    return out;
  }, [edits, byName, category]);

  // ── Прикріпити ──
  const doAttach = useCallback(async (names: string[], cropEdits: Record<string, PhotoEdit | null>) => {
    const number = pnum.trim();
    setBusy(true);
    try {
      const sendEdits: Record<string, PhotoEdit> = {};
      names.forEach((n) => { const e = cropEdits[n]; if (e) sendEdits[n] = e; });
      const r = await fetch('/api/photo-staging/attach', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category, productnumber: number, files: names, kind, edits: sendEdits }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d?.ok) {
        notify.error(d?.detail || d?.reason || 'Не вдалося прикріпити');
        return;
      }
      const done = new Set<string>(names);
      (d.errors || []).forEach((e: any) => done.delete(e.file));
      setFiles((cur) => cur.filter((f) => !done.has(f.name)));
      setSelected(new Set());
      setEdits((cur) => { const n = { ...cur }; done.forEach((x) => delete n[x]); return n; });
      setGiven((g) => ({ ...g, [d.productnumber]: (g[d.productnumber] || 0) + (d.added || 0) }));
      loadExisting();
      notify.success(`${d.productnumber}: +${d.added} фото`
        + ((d.errors || []).length ? `, не вдалось ${(d.errors || []).length}` : ''));
      // Знімки перемішані — наступна група майже напевно ІНШИЙ товар, тож
      // поле очищаємо і лишаємо фокус у ньому. У картці номер фіксований.
      if (!locked) { setPnum(''); inputRef.current?.focus(); }
      onAttached?.();
    } catch {
      notify.error('Не вдалося прикріпити');
    } finally {
      setBusy(false);
    }
  }, [pnum, category, kind, locked, onAttached, loadExisting]);

  const attach = useCallback(async () => {
    const number = pnum.trim();
    if (!number || selected.size === 0 || busy || cropFor) return;
    const names = Array.from(selected);
    // Стандарт картки — квадрат. Неквадратним спершу кадр (Enter у редакторі —
    // центр, «Решта по центру» — усім одразу, «Без кадрування» — як є).
    const todo = await needsCrop(names);
    if (todo.length) {
      setCropFor({
        items: cropItems(todo),
        then: (res) => {
          const merged = { ...edits, ...res };
          setEdits(merged);
          void doAttach(names, merged);
        },
      });
      return;
    }
    void doAttach(names, edits);
  }, [pnum, selected, busy, cropFor, needsCrop, cropItems, edits, doAttach]);

  // Видалити з розбору — × на кадрі або × біля лічильника вибраних. Без
  // діалогу: файли йдуть у _trash/, а в тості є «Повернути» — це дешевше і
  // безпечніше за зайве питання на кожен кадр.
  const restore = useCallback(async (names: string[]) => {
    try {
      const r = await fetch('/api/photo-staging/restore', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category, files: names }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d?.ok) { notify.error(d?.detail || 'Не вдалося повернути'); return; }
      await load();
      notify.success(`Повернуто: ${(d.restored || []).length}`);
    } catch { notify.error('Не вдалося повернути'); }
  }, [category, load]);

  const busyRef = useRef(busy); busyRef.current = busy;
  const remove = useCallback(async (names: string[]) => {
    if (names.length === 0 || busyRef.current) return;
    setBusy(true);
    try {
      const r = await fetch('/api/photo-staging/delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category, files: names }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d?.ok) { notify.error(d?.detail || 'Не вдалося видалити'); return; }
      const gone: string[] = d.files || [];
      const goneSet = new Set(gone);
      setFiles((cur) => cur.filter((f) => !goneSet.has(f.name)));
      setSelected((cur) => { const n = new Set(cur); gone.forEach((x) => n.delete(x)); return n; });
      setFocused((f) => (f && goneSet.has(f) ? null : f));
      const key = `staging-del-${Date.now()}`;
      notify.info({
        key, message: gone.length === 1 ? 'Знімок видалено' : `Видалено: ${gone.length}`,
        description: 'Лежить у _trash поруч із текою — можна повернути.', duration: 6,
        btn: <button type="button" className="text-sm font-semibold underline underline-offset-2"
          onClick={() => { void restore(gone); }}>Повернути</button>,
      });
    } catch {
      notify.error('Не вдалося видалити');
    } finally {
      setBusy(false);
    }
  }, [category, restore]);
  const onRemove = useCallback((name: string) => { void remove([name]); }, [remove]);

  // Enter — прикріпити; Esc — зняти виділення; Delete/Backspace поза полем — видалити вибрані;
  // C — кадр 1:1 для знімка під курсором.
  useEffect(() => {
    if (!open || cropFor) return;
    const onKey = (e: KeyboardEvent) => {
      if (document.querySelector('.bms-dialog-host')) return;   // поверх — редактор кадру/діалог
      const inField = document.activeElement === inputRef.current;
      if (e.key === 'Escape') { setSelected(new Set()); }
      if (e.key === 'Enter' && (locked || inField)) { e.preventDefault(); void attach(); }
      if ((e.key === 'Delete' || e.key === 'Backspace') && !inField && selected.size > 0) { e.preventDefault(); void remove(Array.from(selected)); }
      if ((e.key === 'c' || e.key === 'C' || e.key === 'с' || e.key === 'С') && !inField && !e.metaKey && !e.ctrlKey && focused) {
        e.preventDefault(); openCrop([focused]);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, cropFor, attach, locked, remove, selected, focused, openCrop]);

  const chips = useMemo(() => {
    const seen = new Set<string>();
    return products.filter((p) => {
      const n = String(p.productnumber || '');
      if (!n || seen.has(n)) return false;
      seen.add(n); return true;
    });
  }, [products]);

  if (!open) return null;

  const focusedFile = focused ? byName.get(focused) : undefined;
  const focusedEdit = focused ? edits[focused] : undefined;
  const visible = sorted.slice(0, limit);

  return (
    <div className="fixed inset-0 z-[60] bg-black/60 flex items-stretch justify-center p-3" onClick={onClose}>
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-[1500px] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}>
        {/* Шапка */}
        <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 whitespace-nowrap">
            {locked ? `Фото для ${fixedNumber}` : 'Розкласти фото'}
          </h2>
          <select value={category} onChange={(e) => setCategory(e.target.value)}
            className="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm">
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <select value={sort} onChange={(e) => changeSort(e.target.value as SortKey)}
            title={SORTS.find((s) => s.key === sort)?.title}
            className="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm">
            {SORTS.map((s) => <option key={s.key} value={s.key} title={s.title}>{s.label}</option>)}
          </select>
          <span className="text-sm text-gray-500 whitespace-nowrap">{loading ? '…' : `${files.length} до розбору`}</span>
          {selected.size > 0 && (
            <span className="inline-flex items-center gap-1 text-sm text-gray-700 dark:text-gray-200 whitespace-nowrap">
              · вибрано {selected.size}
              <button type="button" onClick={() => openCrop(Array.from(selected))} disabled={busy}
                title="Кадрувати вибрані 1:1"
                className="ml-1 px-2 h-6 rounded-full text-xs text-gray-500 hover:text-gray-900 hover:bg-gray-100 dark:hover:bg-gray-800 dark:hover:text-gray-100 transition-colors">
                1:1
              </button>
              <button type="button" onClick={() => void remove(Array.from(selected))} disabled={busy}
                title="Видалити вибрані з розбору (Delete)"
                className="inline-flex items-center justify-center w-6 h-6 rounded-full text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
              </button>
            </span>
          )}
          <span className="flex-1" />
          <span className="text-xs text-gray-400 truncate hidden xl:inline">
            клік — вибрати · Shift — діапазон · Enter — прикріпити · C — кадр 1:1 · Delete — видалити · Esc — зняти
          </span>
          <button onClick={onClose} aria-label="Закрити" className="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 text-2xl leading-none ml-2">×</button>
        </div>

        <div className="flex-1 min-h-0 grid grid-cols-[minmax(320px,38%)_1fr]">
          {/* Ліва: велике превʼю + номер + чіпи */}
          <div className="border-r border-gray-200 dark:border-gray-700 flex flex-col min-h-0">
            <div className="p-4 flex-1 min-h-0 flex flex-col gap-3">
              {/* Превʼю — щоб читати цінник, не відкриваючи файл. Підкладка —
                  уже завантажена мініатюра (миттєво), поверх — 1200 px. */}
              <div className="relative flex-1 min-h-0 rounded-xl bg-gray-100 dark:bg-gray-800 flex items-center justify-center overflow-hidden group/pv"
                style={{ containerType: 'size' } as React.CSSProperties}>
                {focusedFile ? (
                  focusedEdit ? (
                    // Квадрат, що вписується в панель будь-яких пропорцій.
                    <CroppedPreview src={imgUrl(category, focusedFile, PREVIEW_W)} edit={focusedEdit}
                      className="shadow-sm" style={{ width: 'min(100cqw, 100cqh)', height: 'min(100cqw, 100cqh)' }} />
                  ) : (
                    <div key={focusedFile.name} className="relative w-full h-full">
                      <img src={imgUrl(category, focusedFile, GRID_W)} alt="" aria-hidden
                        className="absolute inset-0 w-full h-full object-contain blur-[1px]" />
                      <img src={imgUrl(category, focusedFile, PREVIEW_W)} alt={focusedFile.name} decoding="async"
                        className="absolute inset-0 w-full h-full object-contain opacity-0 transition-opacity duration-150"
                        onLoad={(e) => { e.currentTarget.style.opacity = '1'; }} />
                    </div>
                  )
                ) : <span className="text-gray-400 text-sm">Наведи на знімок</span>}
                {focusedFile && (
                  <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-0.5 p-1 rounded-full bg-gray-950/70 text-white shadow-lg backdrop-blur-md
                    opacity-0 group-hover/pv:opacity-100 focus-within:opacity-100 transition-opacity">
                    <button type="button" onClick={() => openCrop([focusedFile.name])}
                      className="h-8 px-3 inline-flex items-center gap-1.5 rounded-full text-[12px] hover:bg-white/20 active:scale-95 transition"
                      title="Кадрувати 1:1 (C) — у картку ляже квадрат">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 2v14a2 2 0 0 0 2 2h14" /><path d="M18 22V8a2 2 0 0 0-2-2H2" /></svg>
                      Кадр 1:1
                    </button>
                    {focusedEdit && (
                      <button type="button" onClick={() => {
                        autoRef.current.delete(focusedFile.name);
                        setEdits((c) => ({ ...c, [focusedFile.name]: null }));
                      }}
                        className="h-8 px-3 inline-flex items-center rounded-full text-[12px] hover:bg-white/20 active:scale-95 transition"
                        title="Прикріпити оригінал, без кадрування">
                        Без кадру
                      </button>
                    )}
                  </div>
                )}
              </div>
              <div className="text-[11px] text-gray-400 truncate">{focused || ''}</div>

              <div className="flex items-center gap-2">
                {locked ? (
                  <div className="flex-1 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/60 px-3 py-2 text-base font-medium text-gray-900 dark:text-gray-100"
                    title="Знімки лягають лише в цю картку">{fixedNumber}</div>
                ) : (
                  <input ref={inputRef} value={pnum} onChange={(e) => setPnum(e.target.value)}
                    placeholder="Номер, напр. Ф4400"
                    className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-base font-medium focus:outline-none focus:ring-2 focus:ring-gray-400" />
                )}
                <div className="inline-flex rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden text-xs">
                  {(['real', 'official'] as const).map((k) => (
                    <button key={k} type="button" onClick={() => setKind(k)}
                      className={`px-2.5 py-2 ${kind === k ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900' : 'text-gray-600 dark:text-gray-300'}`}>
                      {k === 'real' ? 'Реальні' : 'Офіційні'}
                    </button>
                  ))}
                </div>
              </div>
              {kind === 'official' && (
                <div className="text-[11px] text-amber-600 dark:text-amber-400 -mt-1">
                  «Офіційні» — студійні знімки. Живі фото з телефона кладуться в «Реальні»:
                  саме з них працює ШІ-розпізнавання.
                </div>
              )}
              <button type="button" onClick={() => void attach()} disabled={busy || !pnum.trim() || selected.size === 0}
                className="w-full rounded-lg py-2.5 text-sm font-semibold bg-black text-white hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed">
                {busy ? 'Прикріплюю…' : `Прикріпити ${selected.size ? `(${selected.size})` : ''}`}
              </button>


              {/* Чіпи товарів завозу: клік ставить номер. Число — скільки знімків
                  у картці ВЖЕ Є (реальні + офіційні); залитий чіп = фото є,
                  контурний = ще без фото. «+N» — прикріплено за цю сесію. */}
              {!locked && chips.length > 0 && (
                <div className="min-h-0 overflow-y-auto">
                  <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1.5 flex items-center gap-2">
                    <span>Товари цього завозу</span>
                    <span className="normal-case tracking-normal text-gray-400">
                      без фото: {chips.filter((p) => { const e = existing[String(p.productnumber)]; return !e || (e.real + e.official) === 0; }).length}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {chips.map((p) => {
                      const n = String(p.productnumber);
                      const g = given[n] || 0;
                      const e = existing[n];
                      const have = e ? e.real + e.official : 0;
                      const cls = have
                        ? 'bg-gray-900 text-white border-gray-900 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-100'
                        : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800';
                      return (
                        <button key={n} type="button" onClick={() => { setPnum(n.replace(/^#/, '')); inputRef.current?.focus(); }}
                          className={`px-2 py-1 rounded-md text-xs border ${cls}`}
                          title={`${p.brand_name || ''} ${p.model || ''}`.trim()
                            + (e ? ` — реальних ${e.real}, офіційних ${e.official}` : '')}>
                          <ProductNumberText value={n} onDark={!!have} />{have ? <span className="ml-1 opacity-70">·{have}</span> : null}
                          {g ? <span className="ml-1 opacity-70">+{g}</span> : null}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Права: сітка */}
          <div ref={gridRef} className="min-h-0 overflow-y-auto p-3" onMouseLeave={cancelHover}>
            {files.length === 0 && !loading && (
              <div className="h-full flex items-center justify-center text-gray-400 text-sm">
                У теці «{category}» нічого до розбору
              </div>
            )}
            <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${TILE}px, 1fr))` }}>
              {visible.map((f) => (
                <Tile key={f.name} file={f} src={imgUrl(category, f, GRID_W)}
                  order={order.get(f.name) || 0} edited={!!edits[f.name]} busy={busy}
                  onPick={onPick} onHover={onHover} onRemove={onRemove} onCrop={onCropTile} onSize={onSize} />
              ))}
            </div>
            {visible.length < sorted.length && <div ref={sentinelRef} className="h-px" />}
          </div>
        </div>
      </div>

      {cropFor && (
        <PhotoCropEditor items={cropFor.items} initial={edits}
          title={cropFor.then ? 'Кадр 1:1 перед прикріпленням' : 'Кадр 1:1'}
          confirmLabel={cropFor.then ? 'Прикріпити' : undefined}
          onCancel={() => setCropFor(null)}
          onDone={(res) => {
            const then = cropFor.then;
            setCropFor(null);
            Object.keys(res).forEach((n) => autoRef.current.delete(n));   // тепер це рішення людини
            if (then) then(res);
            else setEdits((cur) => ({ ...cur, ...res }));
          }} />
      )}
    </div>
  );
};

export default PhotoStagingModal;
