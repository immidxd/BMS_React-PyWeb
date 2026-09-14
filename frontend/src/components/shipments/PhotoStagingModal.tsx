import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { notify } from '../../ui/feedback';
import type { Product } from '../../types/product';

/**
 * «Розкласти фото» — з теки «до розбору» по картках товарів.
 *
 * Знімки після зйомки ПЕРЕМІШАНІ: різні товари впереміш, жодної нумерації.
 * Тому тут немає «виділи діапазон» — кожен знімок клікається окремо, а щоб
 * швидко читати цінник, поруч із сіткою стоїть велике превʼю того, на що
 * навів. Ввів номер → Enter → вибрані лягли в картку і зникли з сітки.
 *
 * Прикріплення йде тим самим шляхом, що й кнопка «Додати» в картці
 * (add_photos: іменування, WebP, R2) — паралельного шляху немає.
 */

interface StagedFile { name: string; size: number; mtime: number; }
interface Props {
  open: boolean;
  onClose: () => void;
  /** товари завозу — для швидкого вибору номера чіпом */
  products: Product[];
  /** категорія теки «до розбору»; типово за товарами завозу */
  defaultCategory?: string;
  onAttached?: () => void;
}

const CATEGORIES = ['Взуття', 'Сумки', 'Одяг', 'Аксесуари', 'Інше'];
const THUMB = 220;

const imgUrl = (cat: string, name: string, w: number) =>
  `/api/photo-staging/image?category=${encodeURIComponent(cat)}&name=${encodeURIComponent(name)}&w=${w}`;

const PhotoStagingModal: React.FC<Props> = ({ open, onClose, products, defaultCategory, onAttached }) => {
  const [category, setCategory] = useState<string>(defaultCategory || 'Взуття');
  const [files, setFiles] = useState<StagedFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focused, setFocused] = useState<string | null>(null);
  const [pnum, setPnum] = useState('');
  const [kind, setKind] = useState<'real' | 'official'>('real');
  const [busy, setBusy] = useState(false);
  // скільки знімків прикріплено до кожного номера ЗА ЦЮ СЕСІЮ — щоб бачити,
  // кому вже роздано, а кому ще ні
  const [given, setGiven] = useState<Record<string, number>>({});
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/photo-staging?category=${encodeURIComponent(category)}`);
      const d = await r.json();
      setFiles(d.files || []);
      setSelected(new Set());
      setFocused((d.files || [])[0]?.name ?? null);
    } catch {
      notify.error('Не вдалося прочитати теку «до розбору»');
    } finally {
      setLoading(false);
    }
  }, [category]);

  useEffect(() => { if (open) load(); }, [open, load]);
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 50); }, [open]);

  const toggle = (name: string) => {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
    setFocused(name);
  };

  const attach = useCallback(async () => {
    const number = pnum.trim();
    if (!number || selected.size === 0 || busy) return;
    setBusy(true);
    try {
      const r = await fetch('/api/photo-staging/attach', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category, productnumber: number, files: Array.from(selected), kind }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d?.ok) {
        notify.error(d?.detail || d?.reason || 'Не вдалося прикріпити');
        return;
      }
      const done = new Set<string>(Array.from(selected));
      (d.errors || []).forEach((e: any) => done.delete(e.file));
      setFiles((cur) => cur.filter((f) => !done.has(f.name)));
      setSelected(new Set());
      setGiven((g) => ({ ...g, [d.productnumber]: (g[d.productnumber] || 0) + (d.added || 0) }));
      notify.success(`${d.productnumber}: +${d.added} фото`
        + ((d.errors || []).length ? `, не вдалось ${(d.errors || []).length}` : ''));
      // Знімки перемішані — наступна група майже напевно ІНШИЙ товар, тож
      // поле очищаємо і лишаємо фокус у ньому.
      setPnum('');
      inputRef.current?.focus();
      onAttached?.();
    } catch {
      notify.error('Не вдалося прикріпити');
    } finally {
      setBusy(false);
    }
  }, [pnum, selected, busy, category, kind, onAttached]);

  // Enter — прикріпити; Esc — зняти виділення
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setSelected(new Set()); }
      if (e.key === 'Enter' && document.activeElement === inputRef.current) { e.preventDefault(); attach(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, attach]);

  const chips = useMemo(() => {
    const seen = new Set<string>();
    return products.filter((p) => {
      const n = String(p.productnumber || '');
      if (!n || seen.has(n)) return false;
      seen.add(n); return true;
    });
  }, [products]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] bg-black/60 flex items-stretch justify-center p-3" onClick={onClose}>
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-[1500px] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}>
        {/* Шапка */}
        <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Розкласти фото</h2>
          <select value={category} onChange={(e) => setCategory(e.target.value)}
            className="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm">
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <span className="text-sm text-gray-500">{loading ? '…' : `${files.length} до розбору`}</span>
          <span className="flex-1" />
          <span className="text-sm text-gray-500">клік — вибрати · Enter — прикріпити · Esc — зняти</span>
          <button onClick={onClose} aria-label="Закрити" className="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 text-2xl leading-none ml-2">×</button>
        </div>

        <div className="flex-1 min-h-0 grid grid-cols-[minmax(320px,38%)_1fr]">
          {/* Ліва: велике превʼю + номер + чіпи */}
          <div className="border-r border-gray-200 dark:border-gray-700 flex flex-col min-h-0">
            <div className="p-4 flex-1 min-h-0 flex flex-col gap-3">
              {/* Превʼю — щоб читати цінник, не відкриваючи файл */}
              <div className="flex-1 min-h-0 rounded-xl bg-gray-100 dark:bg-gray-800 flex items-center justify-center overflow-hidden">
                {focused
                  ? <img src={imgUrl(category, focused, 1200)} alt={focused}
                      className="max-w-full max-h-full object-contain" />
                  : <span className="text-gray-400 text-sm">Наведи на знімок</span>}
              </div>
              <div className="text-[11px] text-gray-400 truncate">{focused || ''}</div>

              <div className="flex items-center gap-2">
                <input ref={inputRef} value={pnum} onChange={(e) => setPnum(e.target.value)}
                  placeholder="Номер, напр. Ф4400"
                  className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-base font-medium focus:outline-none focus:ring-2 focus:ring-gray-400" />
                <div className="inline-flex rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden text-xs">
                  {(['real', 'official'] as const).map((k) => (
                    <button key={k} type="button" onClick={() => setKind(k)}
                      className={`px-2.5 py-2 ${kind === k ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900' : 'text-gray-600 dark:text-gray-300'}`}>
                      {k === 'real' ? 'Реальні' : 'Офіційні'}
                    </button>
                  ))}
                </div>
              </div>
              <button type="button" onClick={attach} disabled={busy || !pnum.trim() || selected.size === 0}
                className="w-full rounded-lg py-2.5 text-sm font-semibold bg-black text-white hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed">
                {busy ? 'Прикріплюю…' : `Прикріпити ${selected.size ? `(${selected.size})` : ''}`}
              </button>

              {/* Чіпи товарів завозу: клік ставить номер; число — скільки роздано за сесію */}
              {chips.length > 0 && (
                <div className="min-h-0 overflow-y-auto">
                  <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1.5">Товари цього завозу</div>
                  <div className="flex flex-wrap gap-1.5">
                    {chips.map((p) => {
                      const n = String(p.productnumber);
                      const g = given[n] || 0;
                      return (
                        <button key={n} type="button" onClick={() => { setPnum(n.replace(/^#/, '')); inputRef.current?.focus(); }}
                          className={`px-2 py-1 rounded-md text-xs border ${g
                            ? 'border-gray-900 dark:border-gray-100 text-gray-900 dark:text-gray-100'
                            : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300'} hover:bg-gray-100 dark:hover:bg-gray-800`}
                          title={`${p.brand_name || ''} ${p.model || ''}`.trim()}>
                          {n}{g ? <span className="ml-1 opacity-70">·{g}</span> : null}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Права: сітка */}
          <div className="min-h-0 overflow-y-auto p-3">
            {files.length === 0 && !loading && (
              <div className="h-full flex items-center justify-center text-gray-400 text-sm">
                У теці «{category}» нічого до розбору
              </div>
            )}
            <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${THUMB}px, 1fr))` }}>
              {files.map((f) => {
                const isSel = selected.has(f.name);
                return (
                  <button key={f.name} type="button"
                    onClick={() => toggle(f.name)} onMouseEnter={() => setFocused(f.name)}
                    className={`relative aspect-square rounded-lg overflow-hidden border-2 bg-gray-100 dark:bg-gray-800 ${
                      isSel ? 'border-gray-900 dark:border-gray-100 ring-2 ring-gray-900/30' : 'border-transparent'}`}>
                    <img src={imgUrl(category, f.name, THUMB)} alt="" loading="lazy" decoding="async"
                      className="w-full h-full object-cover pointer-events-none" />
                    {isSel && (
                      <span className="absolute top-1.5 left-1.5 w-6 h-6 rounded-full bg-gray-900 text-white text-xs flex items-center justify-center">
                        {Array.from(selected).indexOf(f.name) + 1}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PhotoStagingModal;
