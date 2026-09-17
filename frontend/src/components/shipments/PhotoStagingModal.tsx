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
  /** Режим картки товару: номер відомий і НЕ змінюється — вибрані знімки
   *  лягають лише в цей товар, а поле номера стає підписом. */
  fixedNumber?: string;
  /** Куди класти за замовчуванням (у картці — активна вкладка галереї). */
  defaultKind?: 'real' | 'official';
  onAttached?: () => void;
}

const CATEGORIES = ['Взуття', 'Сумки', 'Одяг', 'Аксесуари', 'Інше'];
const THUMB = 220;

const imgUrl = (cat: string, name: string, w: number) =>
  `/api/photo-staging/image?category=${encodeURIComponent(cat)}&name=${encodeURIComponent(name)}&w=${w}`;

const PhotoStagingModal: React.FC<Props> = ({ open, onClose, products, defaultCategory, fixedNumber, defaultKind, onAttached }) => {
  const [category, setCategory] = useState<string>(defaultCategory || 'Взуття');
  const [files, setFiles] = useState<StagedFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focused, setFocused] = useState<string | null>(null);
  const [pnum, setPnum] = useState(fixedNumber ? fixedNumber.replace(/^#/, '') : '');
  const [kind, setKind] = useState<'real' | 'official'>(defaultKind || 'real');
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
  }, [pnum, selected, busy, category, kind, locked, onAttached, loadExisting]);

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

  const remove = useCallback(async (names: string[]) => {
    if (names.length === 0 || busy) return;
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
  }, [busy, category, restore]);

  // Enter — прикріпити; Esc — зняти виділення; Delete/Backspace поза полем — видалити вибрані
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setSelected(new Set()); }
      if (e.key === 'Enter' && (locked || document.activeElement === inputRef.current)) { e.preventDefault(); attach(); }
      const inField = document.activeElement === inputRef.current;
      if ((e.key === 'Delete' || e.key === 'Backspace') && !inField && selected.size > 0) { e.preventDefault(); void remove(Array.from(selected)); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, attach, locked, remove, selected]);

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
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            {locked ? `Фото для ${fixedNumber}` : 'Розкласти фото'}
          </h2>
          <select value={category} onChange={(e) => setCategory(e.target.value)}
            className="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm">
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <span className="text-sm text-gray-500">{loading ? '…' : `${files.length} до розбору`}</span>
          {selected.size > 0 && (
            <span className="inline-flex items-center gap-1 text-sm text-gray-700 dark:text-gray-200">
              · вибрано {selected.size}
              <button type="button" onClick={() => void remove(Array.from(selected))} disabled={busy}
                title="Видалити вибрані з розбору (Delete)"
                className="ml-1 inline-flex items-center justify-center w-6 h-6 rounded-full text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
              </button>
            </span>
          )}
          <span className="flex-1" />
          <span className="text-sm text-gray-500">клік — вибрати · Enter — прикріпити · × або Delete — видалити · Esc — зняти</span>
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
              <button type="button" onClick={attach} disabled={busy || !pnum.trim() || selected.size === 0}
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
                          {n}{have ? <span className="ml-1 opacity-70">·{have}</span> : null}
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
                  <div key={f.name} className="relative group">
                    <button type="button"
                      onClick={() => toggle(f.name)} onMouseEnter={() => setFocused(f.name)}
                      className={`relative w-full aspect-square rounded-lg overflow-hidden border-2 bg-gray-100 dark:bg-gray-800 ${
                        isSel ? 'border-gray-900 dark:border-gray-100 ring-2 ring-gray-900/30' : 'border-transparent'}`}>
                      <img src={imgUrl(category, f.name, THUMB)} alt="" loading="lazy" decoding="async"
                        className="w-full h-full object-cover pointer-events-none" />
                      {isSel && (
                        <span className="absolute top-1.5 left-1.5 w-6 h-6 rounded-full bg-gray-900 text-white text-xs flex items-center justify-center">
                          {Array.from(selected).indexOf(f.name) + 1}
                        </span>
                      )}
                    </button>
                    {/* × — видалити САМЕ цей кадр; зʼявляється при наведенні, щоб сітка лишалась чистою. */}
                    <button type="button" onClick={(e) => { e.stopPropagation(); void remove([f.name]); }} disabled={busy}
                      title="Видалити з розбору"
                      className="absolute top-1.5 right-1.5 w-6 h-6 rounded-full bg-black/55 text-white flex items-center justify-center
                        opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-red-600 transition-opacity">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
                    </button>
                  </div>
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
