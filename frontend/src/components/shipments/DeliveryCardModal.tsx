import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';

import { productService } from '../../services/productService';
import type { Product, ProductFilters } from '../../types/product';
import {
  deleteProductFromDelivery, syncDelivery, sortDeliveryRows, renameDeliveryProductNumber,
  getDeliveryInfo, updateDeliveryInfo, type DeliveryInfoField, type Shipment,
} from '../../services/referenceService';
import QuickAddProductForm from './QuickAddProductForm';
import ProductDetailsModal from '../products/ProductDetailsModal';
import { alertDialog, confirmDialog, notify } from '../../ui/feedback';
import LoadingSpinner from '../common/LoadingSpinner';

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

// Товар → значення для форми додавання (дублювання). Номер НЕ копіюємо (новий).
const productToPrefill = (p: any): Record<string, string> => {
  const out: Record<string, string> = {};
  const set = (k: string, v: any) => { if (v != null && String(v).trim() !== '') out[k] = String(v); };
  set('type_name', p.type_name); set('brand_name', p.brand_name); set('model', p.model);
  set('marking', p.marking); set('gender_name', p.gender_name); set('color_name', p.color_name);
  set('condition_name', p.current_condition_name || p.condition_name);
  set('season', p.season); set('style_name', p.style_name); set('subtype_name', p.subtype_name);
  set('collection', p.collection); set('gtin', p.gtin); set('year', p.year);
  set('price', p.price); set('oldprice', p.oldprice);
  set('description', p.description); set('extranote', p.extranote);
  set('width', p.width); set('geometric_shape', p.geometric_shape);
  set('manufacturer_name', p.manufacturer_country_name); set('packaging_name', p.packaging_name);
  set('sizeeu', p.sizeeu); set('size_letter', p.size_letter);
  set('measurementscm', p.measurementscm); set('dimensions', p.dimensions);
  set('sole_type_name', p.sole_type_name); set('fastening_type_name', p.fastening_type_name);
  set('sole_color_name', p.sole_color_name); set('toe_shape_name', p.toe_shape_name);
  set('technology_name', p.technology_name); set('heel_type_name', p.heel_type_name);
  set('lace_type_name', p.lace_type_name); set('lining_name', p.lining_name);
  if (Array.isArray(p.materials)) {
    const byPos: Record<string, string[]> = {};
    for (const m of p.materials) { (byPos[m.position] ||= []).push(m.materialname || ''); }
    for (const pos of Object.keys(byPos)) set('material_' + pos, byPos[pos].filter(Boolean).join(', '));
  }
  const meas = (minKey: string, fk: string) => set(fk, p[minKey]);
  meas('measurements_height_min', 'height'); meas('measurements_sole_thickness_min', 'sole_thickness');
  meas('measurements_length_min', 'length'); meas('measurements_pog_min', 'chest');
  meas('measurements_pot_min', 'waist'); meas('measurements_pob_min', 'hips');
  meas('measurements_sleeve_min', 'sleeve');
  return out;
};

/** Скільки ФІЗИЧНИХ речей стоїть за записом товару.
 *  Ростовка (кілька однакових пар одного розміру) зберігається ОДНИМ рядком із
 *  quantity>1 — унікальний індекс (номер, розмір, колір) не дозволяє дублювати
 *  записи. Тож усюди, де рахуємо «скільки товарів у завозі», беремо quantity. */
const qtyOf = (p: Product): number => Math.max(1, Number(p.quantity) || 1);

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
  // Речей у завозі = сума quantity (ростовка з 5 розмірів може бути 10 пар).
  const itemsCount = useMemo(() => products.reduce((s, p) => s + qtyOf(p), 0), [products]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<ProductFilters | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [syncInfo, setSyncInfo] = useState<string | null>(null);
  const [bgSyncing, setBgSyncing] = useState(false);  // фоновий синк з журналом (не блокує)
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
  // Контекст-меню (right-click) + дублювання
  const [ctx, setCtx] = useState<{ x: number; y: number; p: Product } | null>(null);
  const [prefill, setPrefill] = useState<Record<string, string> | null>(null);
  const [prefillNonce, setPrefillNonce] = useState(0);

  // Швидке завантаження товарів з БД (без re-sync) — для рефрешу після add/delete.
  const loadProducts = useCallback(() => {
    if (!shipment) return Promise.resolve();
    return productService
      .getProducts({ shipment_id: shipment.id, per_page: 200 })
      .then(r => setProducts([...(r.items || [])].sort(byNumber)))  // числовий сорт у картці
      .catch(() => setError('Помилка завантаження товарів завозу'));
  }, [shipment]);

  // Відкриття картки: МИТТЄВО показуємо дані з БД (швидко), а синк з журналом — у ФОНІ
  // (stale-while-revalidate). БД майже завжди вже синхронізована (startup-парс + 90с-полер),
  // тож блокувати показ на читанні аркуша не треба. Фоновий синк тихо оновить при розбіжності.
  const loadFirstThenSync = useCallback(async () => {
    if (!shipment) return;
    setError(null);
    setLoading(true);
    await loadProducts();          // 1) миттєво з БД
    setLoading(false);
    setBgSyncing(true);            // 2) синк з журналом у фоні (не блокує перегляд/додавання)
    try {
      const r = await syncDelivery(shipment.id);
      const changed = (r.added || 0) + (r.updated || 0) + (r.deleted || 0) > 0;
      if (changed) { await loadProducts(); setSyncInfo('Оновлено з журналу'); }
    } catch {
      setSyncInfo('⚠ Журнал недоступний — показано дані з програми');
    } finally {
      setBgSyncing(false);
    }
  }, [shipment, loadProducts]);

  useEffect(() => {
    if (!open || !shipment) return;
    setShowForm(false); setProducts([]); setSyncInfo(null);
    setInfoOpen(false); setInfoFields(null); setInfoEditing(false);
    loadFirstThenSync();
    productService.getFilters().then(setFilters).catch(() => {});
  }, [open, shipment, loadFirstThenSync]);

  // Фонова задача (напр. додавання товару) завершилась → оновити список, якщо ця картка
  // відкрита й це її завіз. Ref проти stale-closure (feedback_stale_closure_event_listener).
  const loadProductsRef = useRef(loadProducts);
  loadProductsRef.current = loadProducts;
  useEffect(() => {
    if (!open || !shipment) return;
    const did = shipment.id;
    const handler = (e: Event) => {
      const ce = e as CustomEvent<{ deliveryId?: number }>;
      if (ce.detail?.deliveryId === did) loadProductsRef.current();
    };
    window.addEventListener('bms:delivery-changed', handler as EventListener);
    return () => window.removeEventListener('bms:delivery-changed', handler as EventListener);
  }, [open, shipment]);

  if (!open || !shipment) return null;
  const sid = shipment.id;

  const removeProduct = async (p: Product) => {
    if (!(await confirmDialog(`Видалити товар ${p.productnumber}?`))) return;
    try {
      await deleteProductFromDelivery(sid, p.id);
      loadProducts();
    } catch (e: any) {
      (await alertDialog(e?.response?.data?.detail || 'Не вдалося видалити товар'));
    }
  };

  // ⧉ Дублювати товар → відкрити форму, заповнену даними (повними — через getProduct).
  const duplicateProduct = async (p: Product) => {
    setCtx(null);
    try {
      const full = await productService.getProduct(p.id).catch(() => p);
      setPrefill(productToPrefill(full || p));
      setPrefillNonce(n => n + 1);
      setShowForm(true);
      notify.info({ message: `Дублюю ${p.productnumber} — вкажіть новий номер` });
    } catch {
      notify.error({ message: 'Не вдалося дублювати' });
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
      if (r.renamed) notify.success({ message: `Номер змінено: ${r.old} → ${r.productnumber}` });
      cancelNumEdit();
      await loadProducts();
    } catch (e: any) {
      const st = e?.response?.status; const d = e?.response?.data?.detail;
      notify.error({
        message: st === 409 ? 'Конфлікт номера' : 'Не вдалося змінити номер',
        description: d || 'Помилка', duration: 7,
      });
    } finally { setSavingNum(false); }
  };

  // ⇅ Впорядкувати рядки завозу за номером (UI вже сортований; синкаємо журнал).
  const handleSort = async () => {
    setSorting(true);
    try {
      const r = await sortDeliveryRows(sid);
      notify.success({ message: r.noop ? 'Уже впорядковано' : `Журнал упорядковано за номером (${r.reordered})` });
      await loadProducts();
    } catch (e: any) {
      notify.error({ message: 'Не вдалося впорядкувати', description: e?.response?.data?.detail || 'Помилка журналу' });
    } finally { setSorting(false); }
  };

  // ℹ Інфо-блок: завантажити з аркуша (ліниво, при першому розкритті).
  const loadInfo = async () => {
    setInfoLoading(true);
    try {
      const r = await getDeliveryInfo(sid);
      setInfoFields(r.fields);
    } catch (e: any) {
      notify.error({ message: 'Не вдалося прочитати інфо завозу', description: e?.response?.data?.detail || 'Помилка журналу' });
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
      notify.success({ message: 'Інформацію про завоз збережено' });
      setInfoEditing(false);
      await loadInfo();
    } catch (e: any) {
      notify.error({ message: 'Не вдалося зберегти інфо', description: e?.response?.data?.detail || 'Помилка журналу' });
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
              {/* Ростовка = ОДИН запис у БД на розмір із quantity>1 (унікальний
                  індекс не дає завести 10 однакових рядків). Тому «скільки речей
                  у завозі» — це сума quantity, а не кількість записів: 5 розмірів
                  Ф4083 = 10 фізичних пар. Показуємо і те, і те. */}
              <span title={itemsCount !== products.length
                ? `${products.length} позицій (розмірів), ${itemsCount} речей разом`
                : undefined}>
                📦 {itemsCount} товарів
                {itemsCount !== products.length && (
                  <span className="text-gray-400"> · {products.length} позицій</span>
                )}
              </span>
              {bgSyncing && (
                <span className="inline-flex items-center gap-1 text-gray-400" title="Фонова синхронізація з журналом">
                  <LoadingSpinner variant="inline" size="small" text={null} />
                  синхронізація…
                </span>
              )}
              {/* Сума продажних цін товарів — live, рахується з реально завантажених,
                  а не зі stale shipment.total_cost зі списку завозів. */}
              {products.length > 0 && (
                <span title="Сума продажних цін товарів цього завозу (з урахуванням кількості в ростовках)">
                  💰 {fmtPrice(products.reduce((s, p) => s + (Number(p.price) || 0) * qtyOf(p), 0))}
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
            <QuickAddProductForm deliveryId={shipment.id} onSaved={loadProducts} filters={filters}
              prefill={prefill} prefillNonce={prefillNonce} />
          </div>
        )}

        {/* Body */}
        <div className="flex-1 min-h-0 overflow-auto px-6 py-4">
          {loading && (
            <LoadingSpinner variant="modal" size="large" text="Завантаження поставки…" />
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
                        onContextMenu={e => { e.preventDefault(); setCtx({ x: e.clientX, y: e.clientY, p }); }}
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
                        <td className="px-2 py-2 text-center tabular-nums">
                          {p.sizeeu || p.size_letter || '—'}
                          {/* ×N — скільки пар цього розміру приїхало (ростовка) */}
                          {qtyOf(p) > 1 && (
                            <span className="text-purple-500 dark:text-purple-400 ml-0.5"
                              title={`${qtyOf(p)} шт. цього розміру`}>×{qtyOf(p)}</span>
                          )}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums">
                          {p.price ? fmtPrice(p.price) : '—'}
                          {qtyOf(p) > 1 && p.price && (
                            <span className="block text-[11px] text-gray-400">
                              = {fmtPrice(Number(p.price) * qtyOf(p))}
                            </span>
                          )}
                        </td>
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

      {/* Контекст-меню (right-click по рядку) */}
      {ctx && (
        <div className="fixed inset-0 z-[60]" onMouseDown={() => setCtx(null)} onContextMenu={e => { e.preventDefault(); setCtx(null); }}>
          <div className="absolute min-w-[170px] bg-white dark:bg-gray-800 rounded-lg shadow-2xl border border-gray-200 dark:border-gray-700 py-1 text-sm"
            style={{ top: ctx.y, left: ctx.x }} onMouseDown={e => e.stopPropagation()}>
            <div className="px-3 py-1 text-[11px] text-gray-400 truncate">{ctx.p.productnumber}</div>
            <button onClick={() => duplicateProduct(ctx.p)}
              className="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2">⧉ Дублювати</button>
            <button onClick={() => { setCtx(null); setDetailId(ctx.p.id); }}
              className="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2">↗ Відкрити картку</button>
            <button onClick={() => { const pp = ctx.p; setCtx(null); removeProduct(pp); }}
              className="w-full text-left px-3 py-1.5 hover:bg-red-50 dark:hover:bg-red-900/20 text-red-600 flex items-center gap-2">🗑 Видалити</button>
          </div>
        </div>
      )}

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
