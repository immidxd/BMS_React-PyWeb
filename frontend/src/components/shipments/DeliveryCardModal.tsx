import React, { useEffect, useState, useCallback } from 'react';
import { message, notification } from 'antd';
import { productService } from '../../services/productService';
import type { Product, ProductFilters } from '../../types/product';
import {
  deleteProductFromDelivery, syncDelivery, sortDeliveryRows, renameDeliveryProductNumber,
  getDeliveryInfo, updateDeliveryInfo, type DeliveryInfoField, type Shipment,
} from '../../services/referenceService';
import QuickAddProductForm from './QuickAddProductForm';
import ProductDetailsModal from '../products/ProductDetailsModal';

// Числовий ключ сортування номера (як бекенд _pn_sort_key): (prefix, base, suffix).
// Бекенд get_products НЕ підтримує sort_by=productnumber → сортуємо тут, у картці.
const pnSortKey = (pn?: string): [number, string, number, number] => {
  const s = (pn || '').trim().replace(/^#/, '').replace(/;$/, '');
  const m = s.match(/^(\D*)(\d+)(?:-(\d+))?$/);
  if (!m) return [1, '￿', 0, 0];
  return [0, (m[1] || '').toUpperCase(), parseInt(m[2], 10), m[3] ? parseInt(m[3], 10) : 0];
};
const byNumber = (a: Product, b: Product): number => {
  const ka = pnSortKey(a.productnumber), kb = pnSortKey(b.productnumber);
  for (let i = 0; i < 4; i++) {
    if (ka[i] < kb[i]) return -1;
    if (ka[i] > kb[i]) return 1;
  }
  return 0;
};

// Рахований статус продажу (як у таблиці Товарів) — не сирий журнальний statusid.
const statusOf = (p: Product): { label: string; cls: string } => {
  const sold = p.sold_count || 0;
  const qty = p.quantity || 1;
  // Продано = order-based (sold_count покриває кількість) АБО журнал «Статус»=Продано
  if ((qty > 0 && sold >= qty) || p.status_name === 'Продано') return { label: 'Продано', cls: 'text-red-600' };
  if (p.is_reserved) return { label: 'Заброньовано', cls: 'text-amber-600' };
  if (p.status_name === 'Подаровано') return { label: 'Подаровано', cls: 'text-purple-600' };
  return { label: 'Непродано', cls: 'text-green-600' };
};

interface Props {
  shipment: Shipment | null;
  open: boolean;
  onClose: () => void;
}

const fmtDate = (d: string | null) => {
  if (!d) return '—';
  try { return new Date(d).toLocaleDateString('uk-UA'); } catch { return d; }
};
const fmtPrice = (n?: number | null) =>
  (n ?? 0).toLocaleString('uk-UA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const DeliveryCardModal: React.FC<Props> = ({ shipment, open, onClose }) => {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<ProductFilters | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [syncInfo, setSyncInfo] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [sorting, setSorting] = useState(false);
  // Інфо-блок «Інформація про завоз»
  const [infoOpen, setInfoOpen] = useState(false);
  const [infoFields, setInfoFields] = useState<DeliveryInfoField[] | null>(null);
  const [infoLoading, setInfoLoading] = useState(false);
  const [infoEditing, setInfoEditing] = useState(false);
  const [infoDrafts, setInfoDrafts] = useState<Record<string, string>>({});
  const [infoSaving, setInfoSaving] = useState(false);
  // Інлайн-редагування номера товару в списку
  const [editNumId, setEditNumId] = useState<number | null>(null);
  const [editNumVal, setEditNumVal] = useState('');
  const [savingNum, setSavingNum] = useState(false);

  // Швидке завантаження товарів з БД (без re-sync) — для рефрешу після add/delete.
  const loadProducts = useCallback(() => {
    if (!shipment) return Promise.resolve();
    return productService
      .getProducts({ shipment_id: shipment.id, per_page: 200 })
      .then(r => setProducts([...(r.items || [])].sort(byNumber)))  // числовий сорт у картці
      .catch(() => setError('Помилка завантаження товарів завозу'));
  }, [shipment]);

  // Відкриття картки: спершу точкова синхронізація цієї вкладки з аркушем, тоді показ.
  const syncAndLoad = useCallback(async () => {
    if (!shipment) return;
    setLoading(true); setError(null); setSyncInfo(null);
    try {
      const r = await syncDelivery(shipment.id);
      if (r.deleted > 0) setSyncInfo(`Синхронізовано з журналом · прибрано ${r.deleted}`);
    } catch {
      setSyncInfo('⚠ Не вдалось звірити з журналом — показано дані з програми');
    }
    await loadProducts();
    setLoading(false);
  }, [shipment, loadProducts]);

  useEffect(() => {
    if (!open || !shipment) return;
    setShowForm(false); setProducts([]); setSyncInfo(null);
    setInfoOpen(false); setInfoFields(null); setInfoEditing(false);
    syncAndLoad();
    productService.getFilters().then(setFilters).catch(() => {});
  }, [open, shipment, syncAndLoad]);

  if (!open || !shipment) return null;
  const sid = shipment.id;

  const removeProduct = async (p: Product) => {
    if (!window.confirm(`Видалити товар ${p.productnumber}?`)) return;
    try {
      await deleteProductFromDelivery(sid, p.id);
      loadProducts();
    } catch (e: any) {
      window.alert(e?.response?.data?.detail || 'Не вдалося видалити товар');
    }
  };

  // ✎ Інлайн-редагування номера товару
  const startNumEdit = (p: Product) => { setEditNumId(p.id); setEditNumVal((p.productnumber || '').replace(/^#/, '')); };
  const cancelNumEdit = () => { setEditNumId(null); setEditNumVal(''); };
  const saveNumEdit = async (p: Product) => {
    const v = editNumVal.trim();
    if (!v || v === (p.productnumber || '').replace(/^#/, '')) { cancelNumEdit(); return; }
    setSavingNum(true);
    try {
      const r = await renameDeliveryProductNumber(sid, p.id, v);
      if (r.renamed) message.success(`Номер змінено: ${r.old} → ${r.productnumber}`);
      cancelNumEdit();
      await loadProducts();
    } catch (e: any) {
      const st = e?.response?.status; const d = e?.response?.data?.detail;
      notification.error({
        message: st === 409 ? 'Конфлікт номера' : 'Не вдалося змінити номер',
        description: d || 'Помилка', duration: 7, placement: 'topRight',
      });
    } finally { setSavingNum(false); }
  };

  // ⇅ Впорядкувати рядки завозу за номером (UI вже сортований; синкаємо журнал).
  const handleSort = async () => {
    setSorting(true);
    try {
      const r = await sortDeliveryRows(sid);
      message.success(r.noop ? 'Уже впорядковано' : `Журнал упорядковано за номером (${r.reordered})`);
      await loadProducts();
    } catch (e: any) {
      notification.error({ message: 'Не вдалося впорядкувати', description: e?.response?.data?.detail || 'Помилка журналу', placement: 'topRight' });
    } finally { setSorting(false); }
  };

  // ℹ Інфо-блок: завантажити з аркуша (ліниво, при першому розкритті).
  const loadInfo = async () => {
    setInfoLoading(true);
    try {
      const r = await getDeliveryInfo(sid);
      setInfoFields(r.fields);
    } catch (e: any) {
      notification.error({ message: 'Не вдалося прочитати інфо завозу', description: e?.response?.data?.detail || 'Помилка журналу', placement: 'topRight' });
    } finally { setInfoLoading(false); }
  };

  const toggleInfo = () => {
    const next = !infoOpen;
    setInfoOpen(next);
    if (next && infoFields === null) loadInfo();
  };

  const startInfoEdit = () => {
    const d: Record<string, string> = {};
    (infoFields || []).forEach(f => { if (f.editable) d[f.label] = f.value; });
    setInfoDrafts(d); setInfoEditing(true);
  };

  const saveInfo = async () => {
    const orig: Record<string, string> = {};
    (infoFields || []).forEach(f => { if (f.editable) orig[f.label] = f.value; });
    const changes: Record<string, string> = {};
    Object.keys(infoDrafts).forEach(k => { if ((infoDrafts[k] ?? '') !== (orig[k] ?? '')) changes[k] = infoDrafts[k]; });
    if (Object.keys(changes).length === 0) { setInfoEditing(false); return; }
    setInfoSaving(true);
    try {
      await updateDeliveryInfo(sid, changes);
      message.success('Інформацію про завоз збережено');
      setInfoEditing(false);
      await loadInfo();
    } catch (e: any) {
      notification.error({ message: 'Не вдалося зберегти інфо', description: e?.response?.data?.detail || 'Помилка журналу', placement: 'topRight' });
    } finally { setInfoSaving(false); }
  };

  // ◀▶ навігація між картками товару в межах завозу (циклічно).
  const detailIdx = detailId == null ? -1 : products.findIndex(p => p.id === detailId);
  const gotoOffset = (off: number) => {
    if (detailIdx < 0 || products.length === 0) return;
    const n = products.length;
    setDetailId(products[(detailIdx + off + n) % n].id);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" onMouseDown={onClose}>
      <div className="relative bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-5xl h-[88vh] flex flex-col" onMouseDown={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-gray-100 dark:border-gray-800">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{shipment.sheet_name || `Завіз #${shipment.id}`}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-500 dark:text-gray-400">
              <span>📅 {fmtDate(shipment.shipment_date)}</span>
              <span>🏷 {shipment.supplier_name || 'Без постачальника'}</span>
              <span>📦 {products.length} товарів</span>
              {/* Сума продажних цін товарів — live, рахується з реально завантажених,
                  а не зі stale shipment.total_cost зі списку завозів. */}
              {products.length > 0 && (
                <span title="Сума продажних цін товарів цього завозу">
                  💰 {fmtPrice(products.reduce((s, p) => s + (Number(p.price) || 0), 0))}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={toggleInfo} disabled={loading} title="Інформація про завоз"
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors disabled:opacity-50 ${infoOpen
                ? 'border-gray-400 bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-100'
                : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'}`}>
              ℹ Завіз
            </button>
            <button onClick={handleSort} disabled={loading || sorting || products.length < 2} title="Впорядкувати за номером (і в журналі)"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50">
              {sorting ? '…' : '⇅'} Впорядкувати
            </button>
            <button onClick={() => setShowForm(s => !s)} disabled={loading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-black text-white hover:bg-gray-800 disabled:opacity-50">
              <span className="text-base leading-none">＋</span> Додати товар
            </button>
            <button onClick={onClose} aria-label="Закрити" className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-2xl leading-none">×</button>
          </div>
        </div>

        {/* Інфо-блок «Інформація про завоз» (collapsible, з аркуша) */}
        {infoOpen && (
          <div className="px-6 py-4 border-b border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-800/40">
            <div className="flex items-center justify-between mb-2.5">
              <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium">
                Інформація про завоз {infoLoading && '…'}
              </span>
              {infoFields && !infoEditing && (
                <button onClick={startInfoEdit}
                  className="text-[12px] px-2.5 py-1 rounded-md border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">✎ Редагувати</button>
              )}
              {infoEditing && (
                <div className="flex items-center gap-2">
                  <button onClick={() => setInfoEditing(false)} disabled={infoSaving}
                    className="text-[12px] px-2.5 py-1 rounded-md text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700">Скасувати</button>
                  <button onClick={saveInfo} disabled={infoSaving}
                    className="text-[12px] px-3 py-1 rounded-md bg-green-600 hover:bg-green-700 !text-white disabled:opacity-50">
                    {infoSaving ? 'Збереження…' : '✓ Зберегти'}
                  </button>
                </div>
              )}
            </div>
            {infoFields && infoFields.length > 0 ? (
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-5 gap-y-2.5">
                {infoFields.map(f => (
                  <div key={f.label} className="flex flex-col gap-0.5 min-w-0">
                    <span className="text-[10px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium">{f.label}</span>
                    {infoEditing && f.editable ? (
                      <input value={infoDrafts[f.label] ?? ''} onChange={e => setInfoDrafts(d => ({ ...d, [f.label]: e.target.value }))}
                        autoCapitalize="none" autoCorrect="off" spellCheck={false}
                        className="w-full rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-gray-400" />
                    ) : (
                      <span className={`text-sm break-words ${f.value ? 'text-gray-800 dark:text-gray-100' : 'text-gray-300 dark:text-gray-600'}`}>{f.value || '—'}</span>
                    )}
                  </div>
                ))}
              </div>
            ) : (infoFields && !infoLoading && (
              <div className="text-sm text-gray-400">Блок «Інформація про завоз» не знайдено в аркуші</div>
            ))}
          </div>
        )}

        {showForm && (
          <div className="px-6 py-4 border-b border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-800/40">
            <QuickAddProductForm deliveryId={shipment.id} onSaved={loadProducts} filters={filters} />
          </div>
        )}

        {/* Body */}
        <div className="flex-1 min-h-0 overflow-auto px-6 py-4">
          {loading && (
            <div className="py-24 flex flex-col items-center justify-center gap-3 text-gray-400">
              <div className="w-8 h-8 border-2 border-gray-300 border-t-gray-600 rounded-full animate-spin" />
              <span className="text-sm">Синхронізація з журналом…</span>
            </div>
          )}
          {!loading && (
            <>
              {syncInfo && (
                <div className="mb-3 text-xs text-gray-400">{syncInfo}</div>
              )}
              {error && <div className="py-16 text-center text-red-500">{error}</div>}
              {!error && products.length === 0 && (
                <div className="py-16 text-center text-gray-400">У цьому завозі ще немає товарів</div>
              )}
              {!error && products.length > 0 && (
                <table className="w-full text-sm">
                  <thead className="text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-gray-800">
                    <tr>
                      <th className="px-2 py-2 text-left font-semibold">Номер</th>
                      <th className="px-2 py-2 text-left font-semibold">Тип</th>
                      <th className="px-2 py-2 text-left font-semibold">Бренд</th>
                      <th className="px-2 py-2 text-left font-semibold">Модель</th>
                      <th className="px-2 py-2 text-center font-semibold">Розмір</th>
                      <th className="px-2 py-2 text-right font-semibold">Ціна</th>
                      <th className="px-2 py-2 text-center font-semibold">Статус</th>
                      <th className="px-2 py-2 w-10 text-center font-semibold"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {products.map(p => {
                      const st = statusOf(p);
                      return (
                      <tr key={p.id} onClick={() => { if (editNumId !== p.id) setDetailId(p.id); }}
                        className="border-b last:border-b-0 border-gray-50 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/40 cursor-pointer">
                        <td className="px-2 py-2 font-medium tabular-nums">
                          {editNumId === p.id ? (
                            <input autoFocus value={editNumVal}
                              onClick={e => e.stopPropagation()}
                              onChange={e => setEditNumVal(e.target.value)}
                              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); saveNumEdit(p); } if (e.key === 'Escape') cancelNumEdit(); }}
                              onBlur={() => saveNumEdit(p)}
                              disabled={savingNum}
                              autoCapitalize="none" autoCorrect="off" spellCheck={false}
                              className="w-24 rounded border border-gray-400 dark:border-gray-500 bg-white dark:bg-gray-800 px-1.5 py-0.5 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400" />
                          ) : (
                            <span className="group/num inline-flex items-center gap-1">
                              {p.productnumber}
                              <button title="Редагувати номер"
                                onClick={e => { e.stopPropagation(); startNumEdit(p); }}
                                className="opacity-0 group-hover/num:opacity-100 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 text-xs transition-opacity">✎</button>
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-2">{p.type_name || '—'}</td>
                        <td className="px-2 py-2">{p.brand_name || '—'}</td>
                        <td className="px-2 py-2 text-gray-600 dark:text-gray-300 max-w-[200px] truncate" title={p.model || ''}>{p.model || '—'}</td>
                        <td className="px-2 py-2 text-center tabular-nums">{p.sizeeu || p.size_letter || '—'}</td>
                        <td className="px-2 py-2 text-right tabular-nums">{p.price ? fmtPrice(p.price) : '—'}</td>
                        <td className={`px-2 py-2 text-center text-xs font-medium ${st.cls}`}>{st.label}</td>
                        <td className="px-2 py-2 text-center">
                          <button onClick={e => { e.stopPropagation(); removeProduct(p); }} title="Видалити товар"
                            className="text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded px-1.5 py-0.5">🗑</button>
                        </td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      </div>

      <div onMouseDown={e => e.stopPropagation()}>
        <ProductDetailsModal
          productId={detailId}
          open={!!detailId}
          syncBeforeLoad={() => syncDelivery(sid)}
          onPrev={products.length > 1 ? () => gotoOffset(-1) : undefined}
          onNext={products.length > 1 ? () => gotoOffset(1) : undefined}
          onClose={() => { setDetailId(null); loadProducts(); }}
        />
      </div>
    </div>
  );
};

export default DeliveryCardModal;
