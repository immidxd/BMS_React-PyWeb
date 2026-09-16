/**
 * Діалог «Стікери»: список стікерів (копії редагуються), розкладка на аркуш
 * 100×100, ціна/лінії різу, живе прев'ю першого аркуша, друк / PDF / у чергу.
 *
 * Джерело стікерів (`source`): виділені товари, завіз або черга друку. Копії
 * за замовчуванням = наявні пари (quantity − продано); продане показується
 * сірим із 0 копій — людина може виставити вручну.
 *
 * Друк: у десктоп-режимі бекенд сам зберігає PDF у «Завантаження» і, якщо є
 * CUPS-принтер, шле на нього; у браузері — звичайне завантаження PDF. Після
 * друку/збереження товари позначаються «стікер надруковано» і зникають із черги.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CloseOutlined, PrinterOutlined, TagOutlined, DeleteOutlined, FilePdfOutlined,
  InboxOutlined, WarningOutlined,
} from '@ant-design/icons';
import {
  labelService, labelQueue, emitLabelsChanged,
  type LabelItem, type LabelSource, type LabelsConfig, type PrintResult,
} from '../../services/labelService';
import { isDesktopShell, saveBlob } from '../../services/imageTransfer';
import { notify, confirmDialog } from '../../ui/feedback';

interface Props {
  open: boolean;
  source: LabelSource | null;
  title?: string;
  subtitle?: string;
  onClose: () => void;
  onDone?: (res: PrintResult | null) => void;
}

const LS_LAYOUT = 'bms.labels.layout';
const LS_PRICE = 'bms.labels.showPrice';
const LS_CUT = 'bms.labels.cutMarks';

function lsGet(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function lsSet(key: string, v: string) {
  try { localStorage.setItem(key, v); } catch { /* ignore */ }
}

/** 1 стікер · 2–4 стікери · 0/5+ стікерів (з урахуванням 11–14). */
function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

const PILL = 'px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors';
const PILL_ON = `${PILL} bg-black text-white border-black dark:bg-white dark:text-black dark:border-white`;
const PILL_OFF = `${PILL} border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800`;
const BTN = 'px-3.5 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors disabled:opacity-50 inline-flex items-center gap-1.5';
const BTN_PRIMARY = 'px-4 py-2 rounded-lg text-sm font-semibold bg-black text-white hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-gray-200 transition-colors disabled:opacity-50 inline-flex items-center gap-1.5';

const LabelPrintDialog: React.FC<Props> = ({ open, source, title, subtitle, onClose, onDone }) => {
  const [config, setConfig] = useState<LabelsConfig | null>(null);
  const [items, setItems] = useState<LabelItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState<string>(() => lsGet(LS_LAYOUT, '2x2'));
  const [showPrice, setShowPrice] = useState<boolean>(() => lsGet(LS_PRICE, '1') === '1');
  const [cutMarks, setCutMarks] = useState<boolean>(() => lsGet(LS_CUT, '1') === '1');
  const [printer, setPrinter] = useState<string>('');
  const [preview, setPreview] = useState<{ png: string; stickers: number; pages: number } | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [busy, setBusy] = useState<'print' | 'save' | 'queue' | 'discover' | 'test' | null>(null);
  const [found, setFound] = useState<string[] | null>(null);   // результат «Знайти в мережі»
  const previewSeq = useRef(0);

  const fromQueue = !!source && 'from_queue' in source;
  const printable = useMemo(() => items.filter(i => i.copies > 0), [items]);
  const totalStickers = useMemo(() => printable.reduce((s, i) => s + i.copies, 0), [printable]);
  const soldCount = useMemo(() => items.filter(i => i.sold).length, [items]);
  const layoutSpec = config?.layouts.find(l => l.key === layout) || config?.layouts[0];
  const pages = layoutSpec ? Math.ceil(totalStickers / layoutSpec.per_page) : 0;

  // Завантаження конфігурації + стікерів при відкритті.
  useEffect(() => {
    if (!open || !source) return;
    let alive = true;
    setLoading(true); setError(null); setPreview(null);
    Promise.all([labelService.getConfig(), labelService.resolve(source)])
      .then(([cfg, list]) => {
        if (!alive) return;
        setConfig(cfg);
        if (!cfg.layouts.some(l => l.key === layout)) setLayout(cfg.default_layout);
        setPrinter(cfg.preferred_printer || cfg.printers[0]?.name || '');
        setItems(list);
      })
      .catch((e: any) => { if (alive) setError(e?.response?.data?.detail || e?.message || 'Не вдалося завантажити стікери'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, source]);

  // Живе прев'ю першого аркуша (з дебаунсом; відповіді «не в черзі» відкидаються).
  useEffect(() => {
    if (!open || loading || !config) return;
    if (printable.length === 0) { setPreview(null); return; }
    const seq = ++previewSeq.current;
    const t = setTimeout(() => {
      setPreviewBusy(true);
      labelService.preview({
        items: printable.map(i => ({ product_id: i.product_id, copies: i.copies })),
        layout, show_price: showPrice, cut_marks: cutMarks,
      })
        .then(r => { if (seq === previewSeq.current) setPreview({ png: r.png, stickers: r.stickers, pages: r.pages }); })
        .catch(() => { /* прев'ю не критичне */ })
        .finally(() => { if (seq === previewSeq.current) setPreviewBusy(false); });
    }, 250);
    return () => clearTimeout(t);
  }, [open, loading, config, printable, layout, showPrice, cutMarks]);

  // Esc закриває (поки нічого не друкується).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, busy, onClose]);

  const pickLayout = (k: string) => { setLayout(k); lsSet(LS_LAYOUT, k); };
  const togglePrice = () => setShowPrice(v => { lsSet(LS_PRICE, v ? '0' : '1'); return !v; });
  const toggleCut = () => setCutMarks(v => { lsSet(LS_CUT, v ? '0' : '1'); return !v; });

  const setCopies = (pid: number, copies: number) => {
    const c = Math.max(0, Math.min(99, Math.floor(Number(copies) || 0)));
    setItems(prev => prev.map(i => (i.product_id === pid ? { ...i, copies: c } : i)));
    const it = items.find(i => i.product_id === pid);
    if (fromQueue && it?.queue_id && c > 0) void labelService.setQueueCopies(it.queue_id, c).catch(() => {});
  };

  const removeItem = async (it: LabelItem) => {
    setItems(prev => prev.filter(i => i.product_id !== it.product_id));
    if (fromQueue && it.queue_id) {
      try { await labelService.removeFromQueue(it.queue_id); } catch { /* лічильник оновиться при наступному refresh */ }
    }
  };

  const clearAll = async () => {
    if (!(await confirmDialog({ title: 'Очистити чергу друку?', body: `${items.length} товарів буде прибрано з черги. Стікери не надрукуються.`, okText: 'Очистити', kind: 'delete', danger: true }))) return;
    try { await labelService.clearQueue(); setItems([]); emitLabelsChanged(); onClose(); }
    catch (e: any) { notify.error({ message: 'Не вдалося очистити чергу', description: e?.message }); }
  };

  const toQueue = async () => {
    if (printable.length === 0) return;
    setBusy('queue');
    try {
      // Копії — як виставлено в діалозі: ставимо кожен товар окремо зі своєю кількістю.
      const groups = new Map<number, number[]>();
      printable.forEach(i => { groups.set(i.copies, [...(groups.get(i.copies) || []), i.product_id]); });
      let added = 0, updated = 0;
      for (const [copies, ids] of Array.from(groups.entries())) {
        const r = await labelService.enqueue({ product_ids: ids }, 'selection', copies);
        added += r.added; updated += r.updated;
      }
      emitLabelsChanged();
      notify.success({ message: 'Стікери в черзі друку', description: `Додано ${added}${updated ? `, оновлено ${updated}` : ''}. Черга: ${labelQueue.getSnapshot()}.` });
      onDone?.(null);
      onClose();
    } catch (e: any) {
      notify.error({ message: 'Не вдалося поставити в чергу', description: e?.response?.data?.detail || e?.message });
    } finally { setBusy(null); }
  };

  const run = useCallback(async (mode: 'print' | 'save') => {
    if (printable.length === 0 || !config) return;
    setBusy(mode);
    try {
      const desktop = await isDesktopShell();
      const opts = {
        items: printable.map(i => ({ product_id: i.product_id, copies: i.copies })),
        layout, show_price: showPrice, cut_marks: cutMarks,
      };
      if (!desktop) {
        // Браузер: PDF завантажується штатно; на принтер шле людина сама.
        const blob = (await labelService.print({ ...opts, mode: 'download' })) as Blob;
        saveBlob(blob, `BMS стікери (${totalStickers} шт, ${layout}).pdf`);
        notify.success({ message: 'PDF зі стікерами завантажено', description: `${totalStickers} стікерів · ${pages} арк.` });
        emitLabelsChanged();
        onDone?.(null);
        onClose();
        return;
      }
      const res = (await labelService.print({ ...opts, mode, printer: printer || null })) as PrintResult;
      if (mode === 'print' && res.printed) {
        notify.success({ message: `Надіслано на принтер ${res.printer}`, description: `${res.stickers} стікерів · ${res.pages} арк. · PDF також у «Завантаженнях»: ${res.filename}` });
      } else if (mode === 'print') {
        notify.warning({ message: 'PDF збережено, але не надруковано', description: `${res.message || 'Принтер недоступний'} — файл: ${res.path}`, duration: 8 });
      } else {
        notify.success({ message: 'PDF зі стікерами збережено', description: res.path });
      }
      emitLabelsChanged();
      onDone?.(res);
      onClose();
    } catch (e: any) {
      notify.error({ message: mode === 'print' ? 'Друк не вдався' : 'Не вдалося зберегти PDF', description: e?.message });
    } finally { setBusy(null); }
  }, [printable, config, layout, showPrice, cutMarks, printer, totalStickers, pages, onClose, onDone]);

  const reloadConfig = useCallback(async () => {
    const cfg = await labelService.getConfig();
    setConfig(cfg);
    setPrinter(cfg.preferred_printer || cfg.printers[0]?.name || '');
  }, []);

  const discover = async () => {
    setBusy('discover'); setFound(null);
    try {
      const hosts = await labelService.discover();
      setFound(hosts);
      if (hosts.length === 0) notify.warning({ message: 'Принтерів у мережі не знайдено', description: 'Перевірте, що Xprinter увімкнений і підключений до цього ж Wi-Fi (порт 9100).' });
    } catch (e: any) { notify.error({ message: 'Пошук не вдався', description: e?.message }); }
    finally { setBusy(null); }
  };

  const useHost = async (host: string) => {
    setBusy('discover');
    try { await labelService.setNetworkPrinter(host); setFound(null); await reloadConfig(); notify.success({ message: `Принтер ${host} збережено` }); }
    catch (e: any) { notify.error({ message: 'Принтер', description: e?.message }); }
    finally { setBusy(null); }
  };

  const testPrint = async () => {
    setBusy('test');
    try { const r = await labelService.testPrint(printer || undefined); notify.success({ message: 'Тестовий аркуш надіслано', description: `${r.pages} арк. — перевірте, що стікери в межах наклейки і достатньо чорні.` }); }
    catch (e: any) { notify.error({ message: 'Тестовий друк', description: e?.message }); }
    finally { setBusy(null); }
  };

  if (!open) return null;

  const selected = config?.printers.find(p => p.name === printer);
  const canPrintHere = !!config?.desktop && !!printer && (selected?.kind === 'network' ? !!selected.reachable : !!config?.can_print);
  const heading = title || (fromQueue ? 'Черга друку стікерів' : 'Друк стікерів');

  return (
    <div className="bms-dialog-host fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={busy ? undefined : onClose} />
      <div className="relative w-full max-w-4xl max-h-[90vh] flex flex-col rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-2xl overflow-hidden bms-fade-in">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <span className="w-9 h-9 rounded-xl flex items-center justify-center bg-black text-white dark:bg-white dark:text-black shrink-0">
            <TagOutlined style={{ fontSize: 17 }} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-base font-semibold text-gray-900 dark:text-gray-50 leading-tight">{heading}</div>
            <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
              {subtitle || 'QR-стікери для складу · аркуш 100×100 мм, термопринтер'}
            </div>
          </div>
          <button onClick={busy ? undefined : onClose} className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors" aria-label="Закрити">
            <CloseOutlined className="text-sm" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_320px]">
          {/* Список */}
          <div className="min-h-0 flex flex-col border-b md:border-b-0 md:border-r border-gray-100 dark:border-gray-800">
            <div className="px-5 pt-4 pb-2 flex flex-wrap items-center gap-x-4 gap-y-2">
              <div className="flex items-center gap-1.5">
                {(config?.layouts || []).map(l => (
                  <button key={l.key} onClick={() => pickLayout(l.key)} className={layout === l.key ? PILL_ON : PILL_OFF}
                    title={`${l.label} (${l.sticker_mm[0]}×${l.sticker_mm[1]} мм)`}>
                    {l.cols}×{l.rows}
                  </button>
                ))}
                {layoutSpec && <span className="text-xs text-gray-400 ml-1">{layoutSpec.label}</span>}
              </div>
              <label className="inline-flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300 cursor-pointer select-none">
                <input type="checkbox" checked={showPrice} onChange={togglePrice} className="accent-black" /> Ціна на стікері
              </label>
              <label className="inline-flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300 cursor-pointer select-none">
                <input type="checkbox" checked={cutMarks} onChange={toggleCut} className="accent-black" /> Лінії різу
              </label>
            </div>

            {error && (
              <div className="mx-5 mb-2 flex items-start gap-2 px-3 py-2 rounded-lg text-xs bg-rose-50 text-rose-800 border border-rose-200 dark:bg-rose-900/20 dark:text-rose-300 dark:border-rose-800">
                <WarningOutlined className="mt-0.5 shrink-0" /><span>{error}</span>
              </div>
            )}
            {soldCount > 0 && (
              <div className="mx-5 mb-2 px-3 py-2 rounded-lg text-xs bg-amber-50 text-amber-800 border border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-800">
                {soldCount} {soldCount === 1 ? 'товар проданий' : 'товарів продано'} — для них копій 0. Потрібен стікер попри це — впишіть кількість.
              </div>
            )}

            <div className="flex-1 min-h-0 overflow-y-auto px-5 pb-3">
              {loading ? (
                <div className="py-10 text-center text-sm text-gray-400">Завантаження…</div>
              ) : items.length === 0 ? (
                <div className="py-10 text-center text-sm text-gray-400">
                  {fromQueue ? 'Черга друку порожня. «Додати товар» ставить стікер сюди автоматично.' : 'Немає товарів'}
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-[11px] uppercase tracking-wide text-gray-400">
                    <tr>
                      <th className="text-left font-medium py-1.5">Товар</th>
                      <th className="text-left font-medium py-1.5">Розмір</th>
                      <th className="text-left font-medium py-1.5">Наявно</th>
                      <th className="text-right font-medium py-1.5 pr-1">Копій</th>
                      <th className="w-8" />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map(it => (
                      <tr key={it.product_id} className={`border-t border-gray-100 dark:border-gray-800 ${it.copies === 0 ? 'text-gray-400' : 'text-gray-800 dark:text-gray-100'}`}>
                        <td className="py-1.5 pr-2">
                          <div className="font-semibold leading-tight">{it.number}
                            {it.label_printed_at && <span className="ml-1.5 text-[10px] font-normal text-gray-400" title={`Стікер уже друкувався: ${new Date(it.label_printed_at).toLocaleString('uk-UA')}`}>надруковано</span>}
                            {it.sold && <span className="ml-1.5 text-[10px] font-semibold text-rose-500">продано</span>}
                          </div>
                          <div className="text-xs text-gray-400 truncate max-w-[260px]">{[it.brand, it.model, it.type].filter(Boolean).join(' · ')}</div>
                        </td>
                        <td className="py-1.5 pr-2 whitespace-nowrap">{it.size}{it.insole ? <span className="text-gray-400"> · {it.insole}</span> : null}</td>
                        <td className="py-1.5 pr-2 whitespace-nowrap text-xs">{it.available_qty}{it.quantity > 1 ? <span className="text-gray-400"> з {it.quantity}</span> : null}</td>
                        <td className="py-1.5 text-right">
                          <input type="number" min={0} max={99} value={it.copies}
                            onChange={e => setCopies(it.product_id, Number(e.target.value))}
                            className="w-14 px-2 py-1 text-right text-sm rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 focus:outline-none focus:ring-1 focus:ring-black dark:focus:ring-white" />
                        </td>
                        <td className="py-1.5 text-right">
                          <button onClick={() => void removeItem(it)} className="p-1 rounded text-gray-300 hover:text-rose-500" title="Прибрати зі списку">
                            <DeleteOutlined />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* Прев'ю */}
          <div className="min-h-0 flex flex-col p-4 bg-gray-50 dark:bg-gray-950/40">
            <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-2">Перший аркуш · 100×100 мм</div>
            <div className="relative aspect-square w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white overflow-hidden">
              {preview ? (
                <img src={`data:image/png;base64,${preview.png}`} alt="Прев'ю аркуша" className="w-full h-full object-contain" />
              ) : (
                <div className="absolute inset-0 flex items-center justify-center text-xs text-gray-300">{printable.length ? '' : 'нема що друкувати'}</div>
              )}
              {previewBusy && <div className="absolute inset-0 bg-white/50 dark:bg-black/20" />}
            </div>
            <div className="mt-3 text-sm text-gray-800 dark:text-gray-100 font-medium">
              {totalStickers} {plural(totalStickers, 'стікер', 'стікери', 'стікерів')} · {pages} {plural(pages, 'аркуш', 'аркуші', 'аркушів')}
            </div>
            {config?.desktop && (
              <div className="mt-3">
                <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Принтер</div>
                {config.printers.length > 0 ? (
                  <select value={printer} onChange={e => setPrinter(e.target.value)}
                    className="w-full px-2 py-1.5 text-sm rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
                    {config.printers.map(p => <option key={p.name} value={p.name}>{p.label || p.name}{p.kind === 'network' && !p.reachable ? ' — не відповідає' : ''}</option>)}
                  </select>
                ) : (
                  <div className="text-xs text-gray-500">Принтер не знайдено — PDF збережеться у «Завантаження».</div>
                )}
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <button onClick={() => void discover()} disabled={!!busy} className="text-xs px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
                    {busy === 'discover' ? 'Шукаю…' : 'Знайти Xprinter у Wi-Fi'}
                  </button>
                  {selected?.kind === 'network' && selected.reachable && (
                    <button onClick={() => void testPrint()} disabled={!!busy} className="text-xs px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
                      {busy === 'test' ? 'Друкую…' : 'Тестовий аркуш'}
                    </button>
                  )}
                </div>
                {found && found.length > 0 && (
                  <div className="mt-2 text-xs">
                    <div className="text-gray-400 mb-1">Знайдено в мережі — оберіть:</div>
                    <div className="flex flex-wrap gap-1.5">
                      {found.map(h => (
                        <button key={h} onClick={() => void useHost(h)} disabled={!!busy} className="px-2 py-1 rounded-md bg-black text-white dark:bg-white dark:text-black font-semibold">{h}</button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center gap-2 px-5 py-3 border-t border-gray-100 dark:border-gray-800">
          {fromQueue ? (
            <button onClick={() => void clearAll()} disabled={!!busy || items.length === 0} className={`${BTN} text-rose-600 dark:text-rose-400`}>
              <DeleteOutlined /> Очистити чергу
            </button>
          ) : (
            <button onClick={() => void toQueue()} disabled={!!busy || printable.length === 0} className={BTN} title="Не друкувати зараз — додати до спільної черги, щоб надрукувати разом з іншими">
              <InboxOutlined /> {busy === 'queue' ? 'Додаю…' : 'У чергу'}
            </button>
          )}
          <div className="flex-1" />
          <button onClick={() => void run('save')} disabled={!!busy || printable.length === 0} className={canPrintHere ? BTN : BTN_PRIMARY}>
            <FilePdfOutlined /> {busy === 'save' ? 'Зберігаю…' : config?.desktop ? 'Зберегти PDF' : 'Завантажити PDF'}
          </button>
          {canPrintHere && (
            <button onClick={() => void run('print')} disabled={!!busy || printable.length === 0} className={BTN_PRIMARY}>
              <PrinterOutlined /> {busy === 'print' ? 'Друкую…' : `Друк · ${pages} арк.`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default LabelPrintDialog;
