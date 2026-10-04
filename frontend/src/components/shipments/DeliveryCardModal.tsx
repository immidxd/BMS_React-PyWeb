import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';

import { productService } from '../../services/productService';
import type { Product, ProductFilters } from '../../types/product';
import {
  deleteProductFromDelivery, syncDelivery, sortDeliveryRows, renameDeliveryProductNumber,
  getDeliveryInfo, updateDeliveryInfo, type DeliveryInfoField, type Shipment,
} from '../../services/referenceService';
import QuickAddProductForm from './QuickAddProductForm';
import PhotoStagingModal from './PhotoStagingModal';
import LabelPrintDialog from '../labels/LabelPrintDialog';
import ProductDetailsModal from '../products/ProductDetailsModal';
import ProductHoverPreview from '../products/ProductHoverPreview';
import { warehouseService, type WhLocation } from '../../services/warehouseService';
import {
  InboxOutlined, PictureOutlined, InfoCircleOutlined, SortAscendingOutlined, ScanOutlined,
  TagsOutlined, PlusOutlined, DeleteOutlined, EditOutlined, CheckOutlined, CloseOutlined,
  CopyOutlined, SelectOutlined, CalendarOutlined, ShopOutlined, ShoppingOutlined,
  DollarOutlined, LoadingOutlined,
} from '@ant-design/icons';
import { alertDialog, confirmDialog, notify } from '../../ui/feedback';
import * as autofillBatch from '../../services/autofillBatch';
import LoadingSpinner from '../common/LoadingSpinner';
import ProductNumberText from '../common/ProductNumberText';
import { productRowState } from '../products/productRowState';
import { emitProductNumberChanged } from '../../services/duplicateNumbers';

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
  set('tread_type_name', p.tread_type_name);
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
  meas('measurements_insole_width_min', 'insole_width');
  meas('measurements_shaft_circumference_min', 'shaft_circumference');
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

// Кнопки шапки завозу. Один клас на всіх — інакше довгий підпис переносився
// в два рядки й та кнопка ставала вищою за сусідні («Розкласти фото»).
// `whitespace-nowrap` тримає підпис в один рядок, `h-9` — однакову висоту.
const HEAD_BTN =
  'inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-lg border px-2.5 text-[13px] font-medium '
  + 'transition-colors disabled:opacity-50';
const HEAD_BTN_PLAIN = `${HEAD_BTN} border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 `
  + 'hover:bg-gray-100 dark:hover:bg-gray-800';
// Розмір іконок однаковий усюди — це і є «пропорційно».
const ICON = { fontSize: 13 } as const;
// Значок метаданих у підзаголовку (дата, постачальник, кількість, сума).
const META_ICON = { fontSize: 11 } as const;

const DeliveryCardModal: React.FC<Props> = ({ shipment, open, onClose }) => {
  const [products, setProducts] = useState<Product[]>([]);
  const [stagingOpen, setStagingOpen] = useState(false);
  const [labelsOpen, setLabelsOpen] = useState(false);  // стікери з QR на всі товари завозу
  // Стабільний об'єкт: діалог перечитує список, коли змінюється source.
  const shipmentId = shipment?.id ?? null;
  const labelSource = useMemo(() => (shipmentId ? { delivery_id: shipmentId } : null), [shipmentId]);
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
  // Склад: де лежать товари завозу (колонка-значок ⌂). Довантажується після
  // списку й не блокує його; хмара недоступна — просто порожньо, як у «Товарах».
  const [boxLocations, setBoxLocations] = useState<Record<number, WhLocation[]>>({});
  // Швидкий перегляд картки при наведенні — той самий компонент, що й у
  // «Товарах»: гортати завіз, не відкриваючи повну картку.
  const [hover, setHover] = useState<{ record: Product; x: number; y: number } | null>(null);
  const hoverTimerRef = useRef<number | null>(null);
  const mousePosRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  // Пакетне ШІ-розпізнавання завозу. Робота йде на бекенді; тут лише живий
  // прогрес і підтвердження. ⚠️ Хуки — ТІЛЬКИ вище раннього виходу (#310).
  const [batch, setBatch] = useState<autofillBatch.BatchJob | null>(autofillBatch.currentJob());
  const [accepting, setAccepting] = useState(false);
  // Скільки товарів завозу чекають на підтвердження пропозицій.
  const pendingIds = useMemo(
    () => products.filter(p => (p.proposals_count || 0) > 0).map(p => p.id),
    [products]);
  const pendingFields = useMemo(
    () => products.reduce((s, p) => s + (p.proposals_count || 0), 0),
    [products]);

  // Швидке завантаження товарів з БД (без re-sync) — для рефрешу після add/delete.
  const loadProducts = useCallback(() => {
    if (!shipment) return Promise.resolve();
    return productService
      .getProducts({ shipment_id: shipment.id, per_page: 200 })
      .then(r => {
        const items = [...(r.items || [])].sort(byNumber);   // числовий сорт у картці
        setProducts(items);
        // Коробки — окремим запитом у хмару, без очікування: значок ⌂ або
        // з'явиться, або ні, і список від цього не затримується.
        void warehouseService.locations(items.map(p => p.id)).then(setBoxLocations);
      })
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

  // Живий прогрес пакетного розпізнавання. Поки воно йде, картка час від
  // часу перечитує список — щоб чіпи «Підтвердити» з'являлись самі, а не
  // після ручного переоткриття. Перечитуємо на ЗМІНУ лічильника `done`, а не
  // на кожне опитування: інакше список смикався б кожні дві секунди.
  const lastDoneRef = useRef(-1);
  useEffect(() => {
    if (!open) return;
    autofillBatch.watch();
    return autofillBatch.subscribe((job) => {
      setBatch(job);
      const finished = job.state === 'done' || job.state === 'cancelled' || job.state === 'error';
      if (job.done !== lastDoneRef.current || finished) {
        lastDoneRef.current = job.done;
        loadProductsRef.current();
      }
    });
  }, [open]);

  // Ховаємо прев'ю при скролі (позиція біля курсора стає нерелевантною).
  // ⚠️ Гасимо ТУТ, а не через `cancelHover`: той оголошений нижче раннього
  // виходу, тож у закритому вікні хук звернувся б до неініціалізованої змінної.
  useEffect(() => {
    if (!open) return;
    const onScroll = () => {
      if (hoverTimerRef.current) { window.clearTimeout(hoverTimerRef.current); hoverTimerRef.current = null; }
      setHover(prev => (prev ? null : prev));
    };
    window.addEventListener('scroll', onScroll, true);
    return () => window.removeEventListener('scroll', onScroll, true);
  }, [open]);

  if (!open || !shipment) return null;
  const sid = shipment.id;
  const batchRunning = !!batch && (batch.state === 'running' || batch.state === 'waiting');

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
      if (r.renamed) { notify.success({ message: `Номер змінено: ${r.old} → ${r.productnumber}` }); emitProductNumberChanged(); }
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

  // ── Швидкий перегляд при наведенні (як у «Товарах») ──────────────────────
  const cancelHover = () => {
    if (hoverTimerRef.current) { window.clearTimeout(hoverTimerRef.current); hoverTimerRef.current = null; }
    setHover(prev => (prev ? null : prev));
  };
  const scheduleHover = (record: Product, e: React.MouseEvent) => {
    // Не заважаємо відкритим вікнам, меню й інлайн-правці номера.
    if (detailId || ctx || stagingOpen || labelsOpen || showForm || editNumId) return;
    mousePosRef.current = { x: e.clientX, y: e.clientY };
    if (hoverTimerRef.current) window.clearTimeout(hoverTimerRef.current);
    // Позицію фіксуємо в мить появи — без слідування за мишею, щоб не
    // перерендерювати таблицю на кожен рух.
    hoverTimerRef.current = window.setTimeout(
      () => setHover({ record, x: mousePosRef.current.x, y: mousePosRef.current.y }), 420);
  };
  // Лише ref, без setState: поки картка не з'явилась, стежимо за курсором дешево.
  const moveHover = (e: React.MouseEvent) => { mousePosRef.current = { x: e.clientX, y: e.clientY }; };

  // ✨ Розпізнати весь завіз. Сама робота — фонова задача бекенда, тож
  // закриття картки її не перериває.
  const runBatchAutofill = async () => {
    await autofillBatch.confirmAndStart({
      count: products.length, deliveryId: sid,
      label: shipment.sheet_name || `Завіз #${sid}`,
    });
  };

  // ✓ Підтвердити пропозиції одного товару — той самий шлях, що й «Прийняти
  // всі» в картці товару (update_product + черга журналу).
  const acceptOne = async (p: Product) => {
    setAccepting(true);
    try {
      const r = await autofillBatch.acceptAllFor(p.id);
      notify.success({ message: `${p.productnumber}: прийнято ${r.accepted} полів`, duration: 3 });
      await loadProducts();
    } catch (e: any) {
      notify.error({ message: 'Не вдалося підтвердити', description: String(e?.message || e) });
    } finally { setAccepting(false); }
  };

  // ✓ Підтвердити весь пакет одразу.
  const acceptAllPending = async () => {
    const ok = await confirmDialog({
      title: `Прийняти ${pendingFields} пропозицій на ${pendingIds.length} товарах?`,
      body: 'Значення запишуться в картки звичайним шляхом і підуть у журнал у фоні.\n'
        + 'Якщо хочете переглянути щось окремо — відкрийте картку товару замість цього.',
      okText: 'Прийняти всі', kind: 'confirm',
    });
    if (!ok) return;
    setAccepting(true);
    try {
      const r = await autofillBatch.acceptForProducts(pendingIds);
      notify.success({
        message: `Прийнято ${r.fields} полів на ${r.products} товарах`,
        description: r.errors?.length ? `Не вдалося: ${r.errors.length}` : 'Записуються в журнал у фоні.',
        duration: 6,
      });
      await loadProducts();
    } catch (e: any) {
      notify.error({ message: 'Не вдалося підтвердити', description: String(e?.message || e) });
    } finally { setAccepting(false); }
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
      <div className="relative bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-6xl h-[88vh] flex flex-col" onMouseDown={e => e.stopPropagation()}>
        {/* Header — ДВА яруси, не три. Перший: назва ліворуч, дії праворуч
            у ТОМУ Ж рядку; другий: метадані одним компактним рядком під ними.
            Раніше назва, метадані й кнопки стояли трьома поверхами й шапка
            з'їдала забагато висоти. Назва стискається (min-w-0 + truncate),
            ряд дій не тримає свою max-content ширину, тож на вузькому вікні
            він спершу падає на власний рядок, а вже потім переноситься
            всередині себе — і ніколи не вилазить за край картки. */}
        {/* Хрестик живе в КУТІ картки, а не в ряду дій: усередині ряду він
            переносився разом із кнопками й на вузькому вікні зависав сам
            на другому рядку. Праве поле першого ярусу (pr-9) лишає йому
            місце, тож кнопки під нього не заїжджають. */}
        <button onClick={onClose} aria-label="Закрити" title="Закрити"
          className="absolute right-3 top-3 z-10 inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-200">
          <CloseOutlined style={ICON} />
        </button>
        <div className="px-6 pt-4 pb-3 border-b border-gray-100 dark:border-gray-800">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 pr-9">
            <h2 className="min-w-0 flex-1 basis-[240px] truncate text-lg font-semibold text-gray-900 dark:text-gray-100"
              title={shipment.sheet_name || `Завіз #${shipment.id}`}>{shipment.sheet_name || `Завіз #${shipment.id}`}</h2>
          {/* ⚠️ БЕЗ `shrink-0`: із ним ряд тримав свою max-content ширину й не
              переносився всередині себе — на вузькому вікні кнопки просто
              вилазили за край картки. Тепер, коли не вміщаються навіть на
              власному рядку, вони переходять на наступний. */}
          <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-1.5">
            <button onClick={toggleInfo} disabled={loading} title="Інформація про завоз"
              className={`${HEAD_BTN} ${infoOpen
                ? 'border-gray-400 bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-100'
                : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'}`}>
              <InfoCircleOutlined style={ICON} /> Завіз
            </button>
            <button onClick={handleSort} disabled={loading || sorting || products.length < 2} title="Впорядкувати за номером (і в журналі)"
              className={HEAD_BTN_PLAIN}>
              {sorting ? <LoadingOutlined style={ICON} /> : <SortAscendingOutlined style={ICON} />} Впорядкувати
            </button>
            <button onClick={runBatchAutofill} disabled={loading || products.length === 0 || batchRunning}
              title="ШІ перегляне живі знімки кожного товару завозу й складе пропозиції. У картки нічого не запишеться без вашого підтвердження."
              className={HEAD_BTN_PLAIN}>
              {batchRunning ? <LoadingOutlined style={ICON} /> : <ScanOutlined style={ICON} />} Розпізнати
            </button>
            <button onClick={() => setLabelsOpen(true)} disabled={loading || products.length === 0}
              title="Надрукувати QR-стікери на всі товари цього завозу (аркуш 100×100)"
              className={HEAD_BTN_PLAIN}>
              <TagsOutlined style={ICON} /> Стікери
            </button>
            <button onClick={() => setStagingOpen(true)} disabled={loading}
              title="Розкласти знімки з теки «до розбору» по товарах цього завозу"
              className={HEAD_BTN_PLAIN}>
              <PictureOutlined style={ICON} /> Розкласти фото
            </button>
            <button onClick={() => setShowForm(s => !s)} disabled={loading}
              className={`${HEAD_BTN} border-transparent bg-black text-white hover:bg-gray-800`}>
              <PlusOutlined style={ICON} /> Додати товар
            </button>
          </div>
          </div>
          {/* Метадані завозу одним рядком. Ростовка = ОДИН запис у БД на
              розмір із quantity>1 (унікальний індекс не дає завести 10
              однакових рядків), тому «скільки речей у завозі» — це сума
              quantity, а не кількість записів: 5 розмірів Ф4083 = 10 пар. */}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[13px] text-gray-500 dark:text-gray-400">
            <span className="inline-flex items-center gap-1.5"><CalendarOutlined style={META_ICON} />{fmtDate(shipment.shipment_date)}</span>
            <span className="inline-flex items-center gap-1.5"><ShopOutlined style={META_ICON} />{shipment.supplier_name || 'Без постачальника'}</span>
            <span className="inline-flex items-center gap-1.5" title={itemsCount !== products.length
              ? `${products.length} позицій (розмірів), ${itemsCount} речей разом`
              : undefined}>
              <ShoppingOutlined style={META_ICON} />{itemsCount} товарів
              {itemsCount !== products.length && (
                <span className="text-gray-400">· {products.length} позицій</span>
              )}
            </span>
            {/* Сума продажних цін — live, з реально завантажених товарів, а не
                зі stale shipment.total_cost зі списку завозів. */}
            {products.length > 0 && (
              <span className="inline-flex items-center gap-1.5" title="Сума продажних цін товарів цього завозу (з урахуванням кількості в ростовках)">
                <DollarOutlined style={META_ICON} />{fmtPrice(products.reduce((s, p) => s + (Number(p.price) || 0) * qtyOf(p), 0))}
              </span>
            )}
            {bgSyncing && (
              <span className="inline-flex items-center gap-1 text-gray-400" title="Фонова синхронізація з журналом">
                <LoadingSpinner variant="inline" size="small" text={null} />
                синхронізація…
              </span>
            )}
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
                  className="text-[12px] px-2.5 py-1 rounded-md border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"><EditOutlined style={META_ICON} /> Редагувати</button>
              )}
              {infoEditing && (
                <div className="flex items-center gap-2">
                  <button onClick={() => setInfoEditing(false)} disabled={infoSaving}
                    className="text-[12px] px-2.5 py-1 rounded-md text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700">Скасувати</button>
                  <button onClick={saveInfo} disabled={infoSaving}
                    className="inline-flex items-center gap-1.5 text-[12px] px-3 py-1 rounded-md bg-green-600 hover:bg-green-700 !text-white disabled:opacity-50">
                    {infoSaving ? 'Збереження…' : <><CheckOutlined style={META_ICON} /> Зберегти</>}
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

        {/* Стікери з QR на весь завіз — окремий модал, як і розкладання фото. */}
        <LabelPrintDialog
          open={labelsOpen}
          source={labelsOpen ? labelSource : null}
          title={`Стікери завозу ${shipment?.sheet_name || `#${shipmentId ?? ''}`}`}
          onClose={() => setLabelsOpen(false)}
        />
        {/* Розкладання фото — окремий модал, НЕ всередині форми додавання:
            інакше він рендерився б лише поки та форма розгорнута. */}
        <PhotoStagingModal open={stagingOpen} onClose={() => setStagingOpen(false)}
          products={products} onAttached={loadProducts} />

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
              {/* Пакетне розпізнавання: живий стан + чесна можливість спинити.
                  Показуємо навіть коли пакет запустили не звідси — задача одна
                  на програму, і бачити її тут корисніше, ніж гадати. */}
              {batch && (
                <div className="mb-3 flex items-center gap-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/40 px-3 py-2 text-xs">
                  {batchRunning && <span className="w-3.5 h-3.5 border-2 border-gray-300 border-t-gray-600 rounded-full animate-spin shrink-0" />}
                  <span className="text-gray-600 dark:text-gray-300 truncate">
                    {batchRunning ? 'Розпізнавання' : 'Розпізнано'} · {autofillBatch.describe(batch)}
                  </span>
                  <div className="ml-auto flex items-center gap-2 shrink-0">
                    {batchRunning && (
                      <button onClick={() => autofillBatch.cancel(batch.id)} disabled={batch.cancel_requested}
                        title="Поточний товар дороблюється до кінця — обірвати виклик моделі посеред роботи означало б заплатити й викинути"
                        className="px-2 py-0.5 rounded border border-gray-300 dark:border-gray-600 text-gray-500 hover:bg-white dark:hover:bg-gray-700 disabled:opacity-50">
                        {batch.cancel_requested ? 'Зупиняється…' : 'Спинити'}
                      </button>
                    )}
                    {!batchRunning && (
                      <button onClick={() => setBatch(null)}
                        title="Прибрати підсумок"
                        className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"><CloseOutlined style={META_ICON} /></button>
                    )}
                  </div>
                </div>
              )}
              {pendingIds.length > 0 && (
                <div className="mb-3 flex items-center gap-3 rounded-lg border border-gray-900/15 dark:border-gray-100/15 bg-white dark:bg-gray-800 px-3 py-2 text-xs">
                  <span className="text-gray-700 dark:text-gray-200">
                    Пропозицій ШІ: <b>{pendingFields}</b> на {pendingIds.length} товарах
                  </span>
                  <button onClick={acceptAllPending} disabled={accepting}
                    title="Прийняти всі пропозиції цього завозу одним записом"
                    className="ml-auto inline-flex items-center gap-1.5 px-3 py-1 rounded-md bg-black text-white hover:bg-gray-800 disabled:opacity-50">
                    {accepting ? 'Записую…' : <><CheckOutlined style={META_ICON} /> Підтвердити все ({pendingIds.length})</>}
                  </button>
                </div>
              )}
              {error && <div className="py-16 text-center text-red-500">{error}</div>}
              {!error && products.length === 0 && (
                <div className="py-16 text-center text-gray-400">У цьому завозі ще немає товарів</div>
              )}
              {!error && products.length > 0 && (
                <table className="bms-delivery-table w-full text-sm">
                  <thead className="text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-gray-800">
                    <tr>
                      <th className="px-2 py-2 text-left font-semibold">Номер</th>
                      <th className="px-2 py-2 text-left font-semibold">Тип</th>
                      <th className="px-2 py-2 text-left font-semibold">Бренд</th>
                      <th className="px-2 py-2 text-left font-semibold">Модель</th>
                      <th className="px-2 py-2 text-center font-semibold">Колір</th>
                      <th className="px-2 py-2 text-center font-semibold">Розмір</th>
                      <th className="px-2 py-2 text-right font-semibold">Ціна</th>
                      <th className="px-2 py-2 text-center font-semibold">Статус</th>
                      <th className="px-2 py-2 w-10 text-center font-semibold"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {products.map(p => {
                      const st = statusOf(p);
                      const rowInfo = productRowState(p);
                      return (
                      <tr key={p.id} onClick={() => { if (editNumId !== p.id) { cancelHover(); setDetailId(p.id); } }}
                        onContextMenu={e => { e.preventDefault(); cancelHover(); setCtx({ x: e.clientX, y: e.clientY, p }); }}
                        onMouseEnter={e => scheduleHover(p, e)}
                        onMouseMove={moveHover}
                        onMouseLeave={cancelHover}
                        title={rowInfo.title}
                        className={`border-b last:border-b-0 border-gray-50 dark:border-gray-800/50 cursor-pointer ${
                          rowInfo.className || 'hover:bg-gray-50 dark:hover:bg-gray-800/40'}`}>
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
                              <ProductNumberText value={p.productnumber} />
                              <button title="Редагувати номер"
                                onClick={e => { e.stopPropagation(); startNumEdit(p); }}
                                className="opacity-0 group-hover/num:opacity-100 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-opacity"><EditOutlined style={{ fontSize: 11 }} /></button>
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-2">{p.type_name || '—'}</td>
                        <td className="px-2 py-2">{p.brand_name || '—'}</td>
                        <td className="px-2 py-2 text-gray-600 dark:text-gray-300 max-w-[200px] truncate" title={p.model || ''}>{p.model || '—'}</td>
                        <td className="px-2 py-2 text-center text-gray-600 dark:text-gray-300 max-w-[120px] truncate" title={p.color_name || ''}>
                          {p.color_name || <span className="text-gray-300 dark:text-gray-600">—</span>}
                        </td>
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
                          {/* ⚠️ Саме `> 0`, а не `&& p.price`. У JSX `true && 0`
                              повертає 0, і React друкує цей нуль як текст —
                              рядок із ціною 0 і кількістю >1 показував «—0». */}
                          {qtyOf(p) > 1 && Number(p.price) > 0 && (
                            <span className="block text-[11px] text-gray-400">
                              = {fmtPrice(Number(p.price) * qtyOf(p))}
                            </span>
                          )}
                        </td>
                        <td className={`px-2 py-2 text-xs font-medium ${st.cls}`}>
                          {/* Значки не накладаються на текст: під них зарезервовано
                              однакову смужку праворуч, як у «Товарах», — тож вони
                              стоять рівним стовпчиком і не втискаються у слово. */}
                          <div className="grid w-full items-center" style={{ gridTemplateColumns: '1fr 34px' }}>
                            <span className="text-center">{st.label}</span>
                            <span className="inline-flex items-center justify-end gap-1.5 pr-0.5 text-gray-400 dark:text-gray-500">
                              {(boxLocations[p.id] || []).length > 0 && (
                                <span title={`У коробці ${(boxLocations[p.id] || []).map(l => `${l.box_code}${l.qty > 1 ? ` ×${l.qty}` : ''}`).join(', ')}`}>
                                  <InboxOutlined style={{ fontSize: 12, lineHeight: 1 }} />
                                </span>
                              )}
                              {(p as any).has_photo && (
                                <span title="Є фото"><PictureOutlined style={{ fontSize: 12, lineHeight: 1 }} /></span>
                              )}
                            </span>
                          </div>
                        </td>
                        <td className="px-2 py-2 text-center whitespace-nowrap">
                          {/* Пропозиції ШІ — підтверджуються просто в рядку.
                              Після розпізнавання завозу інакше довелось би
                              відкривати двадцять карток підряд. */}
                          {(p.proposals_count || 0) > 0 && (
                            <button onClick={e => { e.stopPropagation(); acceptOne(p); }} disabled={accepting}
                              title={`Прийняти ${p.proposals_count} пропозицій ШІ для цього товару`}
                              className="mr-1 inline-flex items-center gap-1 rounded border border-gray-900/20 dark:border-gray-100/25 px-1.5 py-0.5 text-[11px] font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50">
                              <CheckOutlined style={{ fontSize: 11 }} /> {p.proposals_count}
                            </button>
                          )}
                          <button onClick={e => { e.stopPropagation(); removeProduct(p); }} title="Видалити товар"
                            className="text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded px-1.5 py-0.5"><DeleteOutlined style={{ fontSize: 13 }} /></button>
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

      {/* Швидкий перегляд (наведення на рядок) — той самий компонент, що й у
          «Товарах»; рендериться в портал поверх цього вікна. */}
      {hover && <ProductHoverPreview record={hover.record} x={hover.x} y={hover.y} />}

      {/* Контекст-меню (right-click по рядку) */}
      {ctx && (
        <div className="fixed inset-0 z-[60]" onMouseDown={() => setCtx(null)} onContextMenu={e => { e.preventDefault(); setCtx(null); }}>
          <div className="absolute min-w-[170px] bg-white dark:bg-gray-800 rounded-lg shadow-2xl border border-gray-200 dark:border-gray-700 py-1 text-sm"
            style={{ top: ctx.y, left: ctx.x }} onMouseDown={e => e.stopPropagation()}>
            <div className="px-3 py-1 text-[11px] text-gray-400 truncate">{ctx.p.productnumber}</div>
            <button onClick={() => duplicateProduct(ctx.p)}
              className="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"><CopyOutlined style={META_ICON} /> Дублювати</button>
            <button onClick={() => { setCtx(null); setDetailId(ctx.p.id); }}
              className="w-full text-left px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"><SelectOutlined style={META_ICON} /> Відкрити картку</button>
            <button onClick={() => { const pp = ctx.p; setCtx(null); removeProduct(pp); }}
              className="w-full text-left px-3 py-1.5 hover:bg-red-50 dark:hover:bg-red-900/20 text-red-600 flex items-center gap-2"><DeleteOutlined style={META_ICON} /> Видалити</button>
          </div>
        </div>
      )}

      <div onMouseDown={e => e.stopPropagation()}>
        <ProductDetailsModal
          productId={detailId}
          open={!!detailId}
          onPrev={products.length > 1 ? () => gotoOffset(-1) : undefined}
          onNext={products.length > 1 ? () => gotoOffset(1) : undefined}
          onClose={() => { setDetailId(null); loadProducts(); }}
        />
      </div>
    </div>
  );
};

export default DeliveryCardModal;
