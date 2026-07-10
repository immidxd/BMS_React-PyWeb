import React, { useEffect, useState, useMemo, useRef } from 'react';
import { productService } from '../../services/productService';
import type { Product, ProductFilters } from '../../types/product';
import { Tag, Spin, Image } from 'antd';
import { CloseOutlined, PictureOutlined, LeftOutlined, RightOutlined, WarningOutlined, EditOutlined, CheckOutlined, PlusOutlined, SyncOutlined, EyeOutlined, EyeInvisibleOutlined, StarFilled, ShoppingOutlined, TableOutlined, InboxOutlined } from '@ant-design/icons';
import { CopyOnClick, formatBrandName, getProductDisplayStatus, getConditionColor } from '../common/displayHelpers';
import { hiddenFieldsForType } from './productCategory';
import { taskManager, emitProductPhotosChanged } from '../../services/taskManager';
import PromPublishDialog from './PromPublishDialog';
import { confirmDialog, notify } from '../../ui/feedback';

interface Props {
  productId: number | null;
  open: boolean;
  onClose: () => void;
  /** Перехід до попередньої/наступної картки (циклічно, в межах поточного списку).
   *  Якщо не передано — крайові стрілки навігації не показуються. */
  onPrev?: () => void;
  onNext?: () => void;
  /** Опц. синхронізація перед завантаженням (напр. точкова синхр. вкладки завозу),
   *  щоб картка показала дані, що збігаються з аркушем. Виконується зі спінером. */
  syncBeforeLoad?: () => Promise<unknown>;
}

type GalleryKind = 'official' | 'real' | 'defect';

interface GalleryImage {
  filename: string;
  url: string;
  index: number;
  is_defect?: boolean;
  kind?: GalleryKind;
}

type FieldType = 'text' | 'number' | 'textarea';

// Поля з безпечною синхронізацією у аркуш (write-back за назвою колонки) + лок у БД.
// Model-level (model/season/marking/year/width/clonednumbers) пишуться на всі рядки
// номера; per-item (sizeeu/size_letter/measurementscm/dimensions) — лише коли номер
// займає один рядок (інакше зберігаються лише в БД, щоб не затерти сусідів ростовки).
const EDITABLE_FIELDS: { field: string; type: FieldType }[] = [
  { field: 'model', type: 'text' },
  { field: 'collection', type: 'text' },
  { field: 'season', type: 'text' },
  { field: 'marking', type: 'text' },
  { field: 'gtin', type: 'text' },
  { field: 'geometric_shape', type: 'text' },
  { field: 'year', type: 'number' },
  { field: 'width', type: 'text' },
  { field: 'clonednumbers', type: 'text' },
  { field: 'sizeeu', type: 'text' },
  { field: 'size_letter', type: 'text' },
  { field: 'measurementscm', type: 'text' },
  { field: 'dimensions', type: 'text' },
  { field: 'price', type: 'number' },
  { field: 'oldprice', type: 'number' },
  { field: 'description', type: 'textarea' },
  { field: 'extranote', type: 'textarea' },
  // Shoe-lookup characteristics edited by NAME (resolved → FK id server-side,
  // written back to the journal). Draft/display read from the `*_name` field.
  { field: 'sole_color_name', type: 'text' },
  { field: 'sole_type_name', type: 'text' },
  { field: 'toe_shape_name', type: 'text' },
  { field: 'fastening_type_name', type: 'text' },
  { field: 'lining_name', type: 'text' },
  { field: 'color_name', type: 'text' },
  { field: 'heel_type_name', type: 'text' },
  { field: 'lace_type_name', type: 'text' },
  { field: 'technology_name', type: 'text' },
  { field: 'packaging_name', type: 'text' },
  // Країна-виробник — FK у `countries`, edited by name.
  { field: 'manufacturer_country_name', type: 'text' },
  // Стан (поточний стан) — per-item FK, edited by name.
  { field: 'current_condition_name', type: 'text' },
];

// Матеріали по позиціях — порядок і підписи (узгоджено з MATERIAL_POSITIONS бекенду).
// Порядок АНАТОМІЧНИЙ, згори вниз: верх → середина → мембрана → устілка →
// проміжна підошва → підошва (той самий порядок, що в публічному каталозі).
const MATERIAL_POSITIONS: { pos: string; label: string }[] = [
  { pos: 'upper', label: 'Верх' },
  { pos: 'middle', label: 'Середина' },
  { pos: 'membrane', label: 'Мембрана' },
  { pos: 'insole', label: 'Устілка' },
  { pos: 'midsole', label: 'Проміжна підошва' },
  { pos: 'sole', label: 'Підошва' },
];

// p.materials (список {position, materialname}) → {position: "шкіра, замша"} CSV.
const groupMaterialsByPosition = (materials?: { position: string; materialname?: string; material_id: number }[]): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const { pos } of MATERIAL_POSITIONS) out[pos] = '';
  if (!materials) return out;
  const acc: Record<string, string[]> = {};
  for (const mat of materials) {
    (acc[mat.position] ||= []).push(mat.materialname || String(mat.material_id));
  }
  for (const pos of Object.keys(acc)) out[pos] = acc[pos].join(', ');
  return out;
};

// Заміри — порядок, підпис, та *_min/*_max ключі (узгоджено з MEASUREMENT_EDIT_FIELDS бекенду).
const MEASUREMENTS: { name: string; label: string; minKey: string; maxKey: string }[] = [
  { name: 'height',         label: 'Висота',     minKey: 'measurements_height_min',         maxKey: 'measurements_height_max' },
  { name: 'sole_thickness', label: 'Підошва',    minKey: 'measurements_sole_thickness_min', maxKey: 'measurements_sole_thickness_max' },
  { name: 'heel',           label: 'Каблук',     minKey: 'measurements_heel_min',           maxKey: 'measurements_heel_max' },
  { name: 'length',         label: 'Довжина',    minKey: 'measurements_length_min',         maxKey: 'measurements_length_max' },
  { name: 'pog',            label: 'Груди (н/о)', minKey: 'measurements_pog_min',           maxKey: 'measurements_pog_max' },
  { name: 'pob',            label: 'Бедра (н/о)', minKey: 'measurements_pob_min',           maxKey: 'measurements_pob_max' },
  { name: 'pot',            label: 'Талія (н/о)', minKey: 'measurements_pot_min',           maxKey: 'measurements_pot_max' },
  { name: 'sleeve',         label: 'Рукав',      minKey: 'measurements_sleeve_min',         maxKey: 'measurements_sleeve_max' },
];

// (min,max) → рядок для інпута: '26' (min==max) / '25-27' / ''. Без суфікса «см».
const measRangeStr = (min: any, max: any): string => {
  const f = (v: any) => { const n = Number(v); return Number.isInteger(n) ? String(n) : String(n); };
  if (min == null && max == null) return '';
  if (min != null && max != null && Number(min) === Number(max)) return f(min);
  if (min != null && max != null) return `${f(min)}-${f(max)}`;
  return f(min != null ? min : max);
};

const measurementsFromProduct = (p: any): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const { name, minKey, maxKey } of MEASUREMENTS) out[name] = measRangeStr(p?.[minKey], p?.[maxKey]);
  return out;
};

// Скільки чекати фото з Drive, перш ніж прибрати спінер галереї (картку показуємо
// одразу — фото вантажаться окремо й «доїжджають» у фоні навіть після таймауту).
const IMAGE_SOFT_TIMEOUT_MS = 3500;

const ProductDetailsModal: React.FC<Props> = ({ productId, open, onClose, onPrev, onNext, syncBeforeLoad }) => {
  const [loading, setLoading] = useState(false);
  const [product, setProduct] = useState<Product | null>(null);
  const [allImages, setAllImages] = useState<GalleryImage[]>([]);
  const [imagesLoading, setImagesLoading] = useState(false);
  const [showDefects, setShowDefects] = useState(false);
  const [activeKind, setActiveKind] = useState<'official' | 'real' | 'defect'>('official');
  const [activeIdx, setActiveIdx] = useState(0);
  const [previewVisible, setPreviewVisible] = useState(false);
  // Inline-редагування «студійні фото з іншого товару» (ростовка-близнюк)
  const [editingPhotoSrc, setEditingPhotoSrc] = useState(false);
  const [photoSrcDraft, setPhotoSrcDraft] = useState('');
  const [savingPhotoSrc, setSavingPhotoSrc] = useState(false);
  // Швидке inline-редагування ОКРЕМОГО поля (опис/примітка/ціна) без загального режиму
  const [editingField, setEditingField] = useState<string | null>(null);
  const [fieldDraft, setFieldDraft] = useState('');
  const [savingField, setSavingField] = useState(false);
  // Менеджер фото (в editMode): додати/видалити/перейменувати(порядок)/замінити
  const [photoBusy, setPhotoBusy] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);  // плитка-ціль під час drag
  const [moveMenuFor, setMoveMenuFor] = useState<string | null>(null);  // filename з відкритим меню «перенести»
  const addPhotoInputRef = React.useRef<HTMLInputElement | null>(null);
  const replacePhotoInputRef = React.useRef<HTMLInputElement | null>(null);
  const replaceTargetRef = React.useRef<string | null>(null);
  // Глобальний режим редагування: всі поля одночасно стають інпутами + «Зберегти все»
  const [editMode, setEditMode] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // Матеріали редагуються окремо від `drafts`: ключ = позиція (upper/middle/...),
  // значення = CSV назв. Ініціалізуються з p.materials у enterEditMode.
  const [materialDrafts, setMaterialDrafts] = useState<Record<string, string>>({});
  // Заміри: ключ = name (height/length/...), значення = рядок-діапазон ('26'/'25-27').
  const [measurementDrafts, setMeasurementDrafts] = useState<Record<string, string>>({});
  // Класифікація: дропдауни наявних значень (FK id як рядок; '' = очистити).
  const [classDrafts, setClassDrafts] = useState<Record<string, string>>({});
  const [filterOpts, setFilterOpts] = useState<ProductFilters | null>(null);
  const [savingAll, setSavingAll] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // «Профіль моделі» (1.5): агрегат по всіх записах бренд+модель у базі —
  // для розумного заповнення ПОРОЖНІХ полів одним кліком (нічого не перезаписує).
  const [modelProfile, setModelProfile] = useState<any | null>(null);
  const [profileNote, setProfileNote] = useState<string | null>(null);
  const [promBusy, setPromBusy] = useState(false);  // експорт/видалення товару на Prom
  const [promPublished, setPromPublished] = useState(false);  // чіп-стан «на Prom»
  const [promPreview, setPromPreview] = useState<any | null>(null);  // дані діалогу публікації
  // Згорнуті підрозділи (Матеріали/Інше/Примітки) — за замовчуванням приховані,
  // розкриваються кліком. У режимі редагування завжди розгорнуті (щоб редагувати).
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({});
  const toggleSection = (id: string) => setOpenSections((s) => ({ ...s, [id]: !s[id] }));

  // ── Публікація в публічний інтернет-каталог (Telegram Mini App) ────────────
  // Окремі endpoint-и (catalog_listings, ключ=productnumber → вся ростовка) →
  // це НЕ зачіпає збереження картки. Стан вантажиться/пишеться незалежно.
  const [catalogStatus, setCatalogStatus] = useState<{ is_published: boolean; is_featured: boolean } | null>(null);
  const [catalogSaving, setCatalogSaving] = useState(false);

  useEffect(() => {
    if (!open || !productId) { setCatalogStatus(null); return; }
    let cancelled = false;
    productService.getCatalogStatus(productId)
      .then((s) => { if (!cancelled) setCatalogStatus({ is_published: s.is_published, is_featured: s.is_featured }); })
      .catch(() => { if (!cancelled) setCatalogStatus({ is_published: false, is_featured: false }); });
    return () => { cancelled = true; };
  }, [open, productId]);

  const toggleCatalogPublished = async () => {
    if (!productId || catalogSaving) return;
    const next = !(catalogStatus?.is_published);
    setCatalogSaving(true);
    try {
      const r = await productService.setCatalogStatus(productId, {
        is_published: next,
        is_featured: next ? (catalogStatus?.is_featured ?? false) : false,
      });
      setCatalogStatus({ is_published: r.is_published, is_featured: r.is_featured });
      notify.success({ message: next ? 'Опубліковано в каталозі' : 'Знято з публікації', duration: 2 });
    } catch {
      notify.error({ message: 'Не вдалося оновити публікацію' });
    } finally { setCatalogSaving(false); }
  };

  const toggleCatalogFeatured = async () => {
    if (!productId || catalogSaving || !catalogStatus?.is_published) return;
    const next = !catalogStatus.is_featured;
    setCatalogSaving(true);
    try {
      const r = await productService.setCatalogStatus(productId, { is_published: true, is_featured: next });
      setCatalogStatus({ is_published: r.is_published, is_featured: r.is_featured });
    } catch {
      notify.error({ message: 'Не вдалося оновити «Рекомендований»' });
    } finally { setCatalogSaving(false); }
  };

  // Чіп «Prom»: живий статус з бекенда (включно з чернеткою/pending) — НЕ залежить від
  // on_display-синку, тож лишається активним одразу після публікації й при поверненні.
  useEffect(() => {
    if (!productId || !open) return;
    let cancelled = false;
    setPromPublished(!!(product as any)?.published_prom);   // швидкий початковий хінт
    fetch(`/api/publications/prom/product-status/${productId}`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (!cancelled && d) setPromPublished(!!d.on_prom); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [productId, open, (product as any)?.published_prom]);

  // Публікація на Prom: прев'ю → ВЛАСНИЙ діалог (редагування назв/ціни/характеристик,
  // попередження про фото/стан всередині) → підтвердження → живий експорт з overrides.
  const promPublishFlow = async () => {
    if (!productId || promBusy) return;
    setPromBusy(true);
    try {
      const pv = await fetch('/api/publications/prom/export-product', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId, preview: true }),
      });
      const d = await pv.json();
      if (!pv.ok) { notify.error({ message: `Prom: ${d.detail || pv.status}` }); return; }
      if (!d.image_count) { notify.warning({ message: 'У товару немає фото — Prom вимагає зображення.' }); return; }
      setPromPreview(d);            // відкриває діалог публікації
    } catch (e: any) { notify.error({ message: `Prom: ${e.message || 'Помилка'}` }); }
    finally { setPromBusy(false); }
  };

  // Підтвердження з діалогу: публікуємо з overrides (відредаговані назви/ціна/характеристики)
  const promConfirmPublish = async (overrides: any) => {
    if (!productId || !promPreview) return;
    setPromBusy(true);
    try {
      const ex = await fetch('/api/publications/prom/export-product', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId, as_draft: false,
                               force: !!promPreview.already_on_prom, overrides }),
      });
      const r = await ex.json();
      if (ex.ok) {
        setPromPublished(true); setPromPreview(null);
        notify.success({ message: r.note || 'Опубліковано на Prom.', duration: 4 });
      } else notify.error({ message: `Prom: ${r.detail || ex.status}` });
    } catch (e: any) { notify.error({ message: `Prom: ${e.message || 'Помилка'}` }); }
    finally { setPromBusy(false); }
  };

  // Видалення товару з Prom (з підтвердженням) — знімає всі лістинги (і розміри ростовки)
  const promDeleteFlow = async () => {
    if (!productId || promBusy) return;
    if (!(await confirmDialog('Видалити цей товар з Prom?\n\nЛістинг(и) буде знято з публікації на Prom. У BMS товар лишається без змін.'))) return;
    setPromBusy(true);
    try {
      const r = await fetch('/api/publications/prom/delete-product', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId }),
      });
      const d = await r.json();
      if (r.ok) { setPromPublished(false); notify.success({ message: `Знято з Prom (${d.deleted ?? 1}).`, duration: 3 }); }
      else notify.error({ message: `Prom: ${d.detail || r.status}` });
    } catch (e: any) { notify.error({ message: `Prom: ${e.message || 'Помилка'}` }); }
    finally { setPromBusy(false); }
  };

  const promToggle = () => (promPublished ? promDeleteFlow() : promPublishFlow());
  // Єдиний стиль кнопок заголовка (Редагувати/Google/Таблиця/Поставка) — однаковий
  // вигляд і поведінка попри різні функції: без підкреслень, плавний перехід.
  const HDR_BTN = "px-3 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 hover:text-gray-900 hover:bg-gray-50 dark:text-gray-300 dark:hover:text-gray-100 dark:hover:bg-gray-800 transition-colors duration-150 flex items-center gap-1.5 no-underline hover:no-underline";
  // Плавна навігація між картками: prevIdRef відрізняє первинне відкриття від ◀/▶;
  // loadSeqRef відкидає застарілі fetch'і при швидкому гортанні.
  const prevIdRef = useRef<number | null>(null);
  const loadSeqRef = useRef(0);
  const bodyRef = useRef<HTMLDivElement>(null);   // для скиду скролу при підміні картки

  const officialCount = useMemo(() => allImages.filter((i) => (i.kind ?? 'official') === 'official').length, [allImages]);
  const realCount = useMemo(() => allImages.filter((i) => i.kind === 'real').length, [allImages]);
  const hasBothKinds = officialCount > 0 && realCount > 0;

  // Visible images:
  //   • активна галерея (official/real) — її фото;
  //   • дефекти — спільні для обох, показуються лише коли увімкнено ⚠.
  const images = useMemo(() => {
    return allImages.filter((i) => {
      const k = (i.kind ?? 'official') as GalleryKind;
      // 'defect' як активна галерея (edit-режим) → показуємо дефекти напряму;
      // інакше дефекти — лише як оверлей ⚠ (showDefects).
      if (k === 'defect') return showDefects || activeKind === 'defect';
      return k === activeKind;
    });
  }, [allImages, showDefects, activeKind]);
  const defectCount = useMemo(() => allImages.filter((i) => i.is_defect).length, [allImages]);

  // Перемкнути показ дефектних фото. При УВІМКНЕННІ — одразу перегортаємо галерею
  // до першого дефектного кадру (а не лишаємось на поточному непошкодженому).
  const toggleDefects = React.useCallback(() => {
    const next = !showDefects;
    if (next) {
      const future = allImages.filter((i) => {
        const k = (i.kind ?? 'official') as GalleryKind;
        return k === 'defect' || k === activeKind;
      });
      const di = future.findIndex((i) => i.is_defect);
      if (di >= 0) setActiveIdx(di);
    }
    setShowDefects(next);
  }, [showDefects, allImages, activeKind]);

  // Завантаження товару — картка показується одразу, НЕ чекаючи фото з Drive.
  // ref на колбек синхронізації (stale-closure-safe) — щоб loadProduct не змінював
  // identity щоразу (інакше re-load loop). Див. feedback_stale_closure_event_listener.
  const syncBeforeLoadRef = useRef(syncBeforeLoad);
  useEffect(() => { syncBeforeLoadRef.current = syncBeforeLoad; }, [syncBeforeLoad]);
  // Завжди свіжий productId — guard, щоб фон-синк не записав дані після навігації геть.
  const curPidRef = useRef(productId);
  curPidRef.current = productId;

  const loadProduct = React.useCallback(async (withSpinner = true) => {
    if (!productId) return;
    const pid = productId;
    if (withSpinner) setLoading(true);
    try {
      const prod = await productService.getProduct(pid);  // МИТТЄВО з БД (не блокуємо на аркуші)
      if (curPidRef.current === pid) setProduct(prod);
    } catch (e) {
      console.error('Failed to load product', e);
    } finally {
      if (withSpinner) setLoading(false);
    }
    // Точкова синхр. з аркушем — У ФОНІ (best-effort). Картку не блокує; якщо аркуш
    // відрізняється — тихо перезавантажуємо. Guard curPidRef — проти навігації геть.
    if (withSpinner && syncBeforeLoadRef.current) {
      Promise.resolve(syncBeforeLoadRef.current())
        .then(() => (curPidRef.current === pid ? productService.getProduct(pid) : null))
        .then(prod => { if (prod && curPidRef.current === pid) setProduct(prod); })
        .catch(() => { /* best-effort */ });
    }
  }, [productId]);

  // Фото вантажимо ОКРЕМО від товару. Спінер галереї тримаємо лише до soft-таймауту:
  // якщо Drive відповідає довго — показуємо плейсхолдер, але запит триває й фото
  // зʼявляться коли доїдуть. Картку це ніколи не блокує.
  const loadImages = React.useCallback(async (silent = false) => {
    if (!productId) return;
    if (!silent) setImagesLoading(true);
    let settled = false;
    const timer = silent ? null : setTimeout(() => { if (!settled) setImagesLoading(false); }, IMAGE_SOFT_TIMEOUT_MS);
    try {
      const res = await productService.getProductImages(productId);
      if (!settled) settled = true;
      setAllImages(res.images || []);
    } catch (e) {
      console.error('Failed to load images', e);
    } finally {
      settled = true;
      if (timer) clearTimeout(timer);
      if (!silent) setImagesLoading(false);
    }
  }, [productId]);

  // ── Менеджер фото (editMode) ────────────────────────────────────────────
  // Працюємо лише з ОФІЦІЙНИМИ фото (локальний мірор + R2). Реальні (Drive) і
  // дефекти — окремі набори, тут не чіпаємо.
  // Менеджер фото показує фото АКТИВНОЇ галереї (official | real). Це дозволяє
  // вантажити/перейменовувати/видаляти і реальні фото з тієї ж панелі.
  const officialImages = useMemo(
    () => allImages.filter((i) => (i.kind ?? 'official') === activeKind),
    [allImages, activeKind]
  );
  // filename → image (для рендеру за порядком mgrOrder під час drag)
  const imgByName = useMemo(() => {
    const m = new Map<string, GalleryImage>();
    officialImages.forEach((i) => m.set(i.filename, i));
    return m;
  }, [officialImages]);

  // Локальний порядок (для живого optimistic-перетягування). Синхронізуємо із
  // сервером, але НЕ під час активного drag (інакше «стрибало» б назад).
  const [mgrOrder, setMgrOrder] = useState<string[]>([]);
  const draggingRef = React.useRef(false);
  const tileRefs = React.useRef<Map<string, HTMLDivElement>>(new Map());
  const prevRects = React.useRef<Map<string, DOMRect>>(new Map());

  useEffect(() => {
    if (draggingRef.current) return;
    const next = officialImages.map((i) => i.filename);
    setMgrOrder((cur) => (cur.join('') === next.join('') ? cur : next));
  }, [officialImages]);

  // FLIP-анімація: плавне ковзання плиток у нові позиції при зміні mgrOrder.
  React.useLayoutEffect(() => {
    const prev = prevRects.current;
    mgrOrder.forEach((fn) => {
      const el = tileRefs.current.get(fn);
      if (!el) return;
      const nr = el.getBoundingClientRect();
      const old = prev.get(fn);
      if (old) {
        const dx = old.left - nr.left, dy = old.top - nr.top;
        if (dx || dy) {
          el.animate(
            [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'translate(0,0)' }],
            { duration: 200, easing: 'cubic-bezier(0.2,0,0,1)' }
          );
        }
      }
    });
    const nextRects = new Map<string, DOMRect>();
    mgrOrder.forEach((fn) => {
      const el = tileRefs.current.get(fn);
      if (el) nextRects.set(fn, el.getBoundingClientRect());
    });
    prevRects.current = nextRects;
  }, [mgrOrder]);

  const handleAddPhotos = React.useCallback((files: FileList | null) => {
    if (!productId || !files || files.length === 0) return;
    const pid = productId;
    const kind = activeKind;
    const arr = Array.from(files);  // знімок ДО скиду input.value
    setPhotoBusy(true);
    // Фонова задача — завантаження доробиться навіть якщо закрити картку.
    // silentSuccess: повідомлення формуємо самі за результатом (частковий збій теж).
    taskManager.run(`Завантаження ${arr.length} фото`, () => productService.addProductPhotos(pid, arr, kind), {
      silentSuccess: true,
      onSuccess: (res) => {
        emitProductPhotosChanged(pid);
        const errs = res.errors || [];
        if (errs.length === 0) {
          notify.success({ message: '✓ Готово', description: `Завантажено ${res.added} фото`, duration: 4 });
        } else {
          // Частина файлів не пройшла (напр. HEIC/битий) — інші збереглись.
          notify.warning({
            message: `Завантажено ${res.added} з ${arr.length}`,
            description: `Не вдалося: ${errs.map(e => e.file).join(', ')}`, duration: 9,
          });
        }
      },
    })
      .then(() => { if (curPidRef.current === pid) loadImages(true); })
      .catch(() => { /* помилку показав taskManager */ })
      .finally(() => { if (curPidRef.current === pid) setPhotoBusy(false); });
  }, [productId, activeKind, loadImages]);

  const handleDeletePhoto = React.useCallback(async (filename: string) => {
    if (!productId) return;
    setPhotoBusy(true);
    try {
      await productService.deleteProductPhoto(productId, filename);
      await loadImages(true);
      setActiveIdx(0);
    } catch (e) { console.error('delete photo failed', e); }
    finally { setPhotoBusy(false); }
  }, [productId, loadImages]);

  const handleReplacePhoto = React.useCallback(async (filename: string, file: File | null) => {
    if (!productId || !file) return;
    setPhotoBusy(true);
    try {
      await productService.replaceProductPhoto(productId, filename, file);
      await loadImages(true);
    } catch (e) { console.error('replace photo failed', e); }
    finally { setPhotoBusy(false); }
  }, [productId, loadImages]);

  // Перенести одне фото в інший набір (official/real/defect) — виправлення помилкового
  // завантаження (напр. дефект потрапив у «Реальні»). Після — перепідтягуємо фото.
  const handleMovePhotoKind = React.useCallback(async (filename: string, toKind: 'official' | 'real' | 'defect') => {
    if (!productId) return;
    setPhotoBusy(true);
    try {
      await productService.movePhotoOne(productId, filename, toKind);
      await loadImages(true);
    } catch (e) { console.error('move photo kind failed', e); }
    finally { setPhotoBusy(false); }
  }, [productId, loadImages]);

  const handleReorderPhotos = React.useCallback(async (order: string[]) => {
    if (!productId || order.length === 0) return;
    setPhotoBusy(true);
    try {
      await productService.reorderProductPhotos(productId, order, activeKind);
      await loadImages(true);  // тихо — порядок уже правильний візуально
    } catch (e) { console.error('reorder photos failed', e); }
    finally { setPhotoBusy(false); }
  }, [productId, loadImages, activeKind]);

  // Живе перетягування: плитки міняються місцями ПОКИ тягнеш (optimistic),
  // FLIP анімує рух; коміт на сервер — на відпускання.
  const onTileDragStart = React.useCallback((fn: string) => {
    draggingRef.current = true;
    const i = mgrOrder.indexOf(fn);
    setDragIdx(i);
    setOverIdx(i);
  }, [mgrOrder]);

  const onTileDragEnter = React.useCallback((fn: string) => {
    setOverIdx(mgrOrder.indexOf(fn));  // лише підсвітка цілі, БЕЗ перестановки під час drag
  }, [mgrOrder]);

  const onTileDrop = React.useCallback(() => {
    draggingRef.current = false;
    const from = dragIdx, to = overIdx;
    setDragIdx(null); setOverIdx(null);
    if (from == null || to == null || from < 0 || to < 0 || from === to) return;
    const a = [...mgrOrder];
    const [moved] = a.splice(from, 1);
    a.splice(to, 0, moved);
    setMgrOrder(a);                       // ОДИН реордер на відпускання → FLIP анімує
    const serverOrder = officialImages.map((i) => i.filename);
    if (a.join('') !== serverOrder.join('')) handleReorderPhotos(a);
  }, [dragIdx, overIdx, mgrOrder, officialImages, handleReorderPhotos]);

  // Кожне свіже відкриття (після закриття) трактуємо як ПЕРВИННЕ (зі спінером).
  useEffect(() => { if (!open) prevIdRef.current = null; }, [open]);

  useEffect(() => {
    if (!open || !productId) return;
    // Навігація (◀/▶) = productId змінився, поки модал відкритий. На відміну від
    // первинного відкриття, НЕ обнуляємо картку й НЕ показуємо повноекранний спінер
    // (саме це давало «блимання»). Лишаємо стару картку, плавно підмінюємо новою.
    const isNavigation = prevIdRef.current !== null && prevIdRef.current !== productId;
    prevIdRef.current = productId;

    // Спільний скид стану галереї/редагування (для будь-якого переходу).
    setShowDefects(false);
    setActiveKind('official');
    setActiveIdx(0);
    setEditingPhotoSrc(false);
    setEditingField(null);
    setEditMode(false);
    setSaveError(null);
    setOpenSections({});

    const seq = ++loadSeqRef.current;
    if (!isNavigation) {
      // Первинне відкриття — спінер, чиста картка.
      setProduct(null);
      setAllImages([]);
      loadProduct(true);
      loadImages();
    } else {
      // Навігація — стара картка лишається видимою, нову готуємо у фоні (без спінера),
      // підмінюємо РІВНО коли дані готові → keyed-fade робить перехід плавним.
      (async () => {
        try {
          const prod = await productService.getProduct(productId);
          if (seq !== loadSeqRef.current) return;   // застарілий запит (швидке гортання)
          setAllImages([]);   // чистимо галерею саме при підміні → спінер під новим текстом, не чужі фото
          setProduct(prod);   // підміна картки (key=p.id → плавний fade)
        } catch (e) {
          console.error('Failed to load product (nav)', e);
        }
        if (seq === loadSeqRef.current) loadImages();
      })();
    }
  }, [open, productId, loadProduct, loadImages]);

  // Скид скролу тіла до верху при підміні картки — інакше нова (коротша) картка
  // успадкувала б позицію скролу попередньої → виглядало б як «скачок».
  useEffect(() => { bodyRef.current?.scrollTo({ top: 0 }); }, [product?.id]);

  // Зберегти official_photos_from і одразу перепідтягнути фото
  const savePhotoSrc = async () => {
    if (!productId) return;
    setSavingPhotoSrc(true);
    try {
      const val = photoSrcDraft.trim();
      await productService.updateProduct(productId, { official_photos_from: val || null });
      setEditingPhotoSrc(false);
      await loadProduct(false);
      await loadImages();
      setActiveIdx(0);
    } catch (e) {
      console.error('Failed to save official_photos_from', e);
    } finally {
      setSavingPhotoSrc(false);
    }
  };

  // Зберегти ОДНЕ редаговане поле (швидкий пенсіл біля опису/примітки/ціни)
  const saveField = async (field: string) => {
    if (!productId) return;
    setSavingField(true);
    try {
      let val: any = fieldDraft.trim();
      if (field === 'price') {
        val = val === '' ? null : Number(val);
        if (val !== null && (isNaN(val) || val < 0)) { setSavingField(false); return; }
      } else if (val === '') {
        val = null;
      }
      await productService.updateProduct(productId, { [field]: val } as any);
      setEditingField(null);
      await loadProduct(false);
    } catch (e) {
      console.error('Failed to save field', field, e);
    } finally {
      setSavingField(false);
    }
  };

  // Прибрати «Стару ціну» одним кліком (додається випадково; повний ланцюг:
  // БД NULL + лок + write-back стирає клітинку «Стара ціна» в журналі).
  const clearOldprice = async () => {
    if (!productId) return;
    setSavingField(true);
    try {
      await productService.updateProduct(productId, { oldprice: null } as any);
      await loadProduct(false);
    } catch (e) {
      console.error('Failed to clear oldprice', e);
    } finally {
      setSavingField(false);
    }
  };

  // Зняти лок з поля («скинути до аркуша») → відновиться при наступному парсингу
  const unlockField = async (field: string) => {
    if (!productId) return;
    try {
      await productService.unlockProductFields(productId, [field]);
      await loadProduct(false);
    } catch (e) {
      console.error('Failed to unlock field', field, e);
    }
  };

  const startEdit = (field: string, raw: string | number | null | undefined) => {
    setEditingField(field);
    setFieldDraft(raw == null ? '' : String(raw));
  };

  // ── Глобальний режим редагування ────────────────────────────────────────────
  const rawFieldStr = React.useCallback((field: string): string => {
    const v = (product as any)?.[field];
    return v === null || v === undefined ? '' : String(v);
  }, [product]);

  const enterEditMode = () => {
    if (!product) return;
    const init: Record<string, string> = {};
    for (const { field } of EDITABLE_FIELDS) init[field] = rawFieldStr(field);
    setDrafts(init);
    setMaterialDrafts(groupMaterialsByPosition((product as any)?.materials));
    setMeasurementDrafts(measurementsFromProduct(product as any));
    const cd: Record<string, string> = {};
    // Стать — і далі дропдаун за id (3 канонічні значення).
    const gid = (product as any)?.genderid;
    cd['genderid'] = gid == null ? '' : String(gid);
    // Бренд/Тип/Підтип/Стиль — комбобокси ЗА НАЗВОЮ (вільний ввід + підказки;
    // сервер робить get-or-create). Драфт = поточна назва.
    for (const f of ['brand_name', 'type_name', 'subtype_name', 'style_name']) {
      cd[f] = String((product as any)?.[f] ?? '');
    }
    setClassDrafts(cd);
    // Дропдауни наявних значень — підвантажуємо довідник раз (лінь).
    if (!filterOpts) {
      productService.getFilters().then(setFilterOpts).catch((e) => console.error('Failed to load filters', e));
    }
    setEditingField(null);
    setEditingPhotoSrc(false);
    setSaveError(null);
    setEditMode(true);
  };

  const cancelEditMode = () => {
    setEditMode(false);
    setDrafts({});
    setMaterialDrafts({});
    setMeasurementDrafts({});
    setClassDrafts({});
    setSaveError(null);
  };

  const setDraft = (field: string, val: string) => setDrafts((d) => ({ ...d, [field]: val }));

  const saveAll = async () => {
    if (!productId) return;
    const payload: Record<string, any> = {};
    for (const { field, type } of EDITABLE_FIELDS) {
      const cur = (drafts[field] ?? '');
      const orig = rawFieldStr(field);
      if (cur.trim() === orig.trim()) continue;
      if (type === 'number') {
        const t = cur.trim();
        if (t === '') { payload[field] = null; continue; }
        const n = Number(t);
        if (isNaN(n) || n < 0) { setSaveError(`Некоректне число у полі «${field}»`); return; }
        payload[field] = n;
      } else {
        payload[field] = cur.trim() === '' ? null : cur;
      }
    }
    // Матеріали: лише позиції, що змінилися (порівняно з вихідним станом картки).
    const origMaterials = groupMaterialsByPosition((product as any)?.materials);
    const matChanges: Record<string, string> = {};
    for (const { pos } of MATERIAL_POSITIONS) {
      const cur = (materialDrafts[pos] ?? '').trim();
      if (cur !== (origMaterials[pos] ?? '').trim()) matChanges[pos] = cur;
    }
    if (Object.keys(matChanges).length > 0) payload.materials_by_position = matChanges;

    // Заміри: лише змінені виміри (порівняно з вихідним станом картки).
    const origMeas = measurementsFromProduct(product as any);
    const measChanges: Record<string, string> = {};
    for (const { name } of MEASUREMENTS) {
      const cur = (measurementDrafts[name] ?? '').trim();
      if (cur !== (origMeas[name] ?? '').trim()) measChanges[name] = cur;
    }
    if (Object.keys(measChanges).length > 0) payload.measurements_edit = measChanges;

    // Класифікація: Бренд/Тип/Підтип/Стиль — ЗА НАЗВОЮ (вільний ввід; сервер
    // резолвить get-or-create, '' → null очищає FK). Стать — FK id (дропдаун).
    for (const f of ['brand_name', 'type_name', 'subtype_name', 'style_name']) {
      const cur = (classDrafts[f] ?? '').trim();
      const orig = String((product as any)?.[f] ?? '').trim();
      if (cur === orig) continue;
      payload[f] = cur === '' ? null : cur;
    }
    {
      const cur = classDrafts['genderid'] ?? '';
      const orig = (product as any)?.genderid == null ? '' : String((product as any).genderid);
      if (cur !== orig) payload['genderid'] = cur === '' ? null : Number(cur);
    }

    if (Object.keys(payload).length === 0) { cancelEditMode(); return; }
    const pid = productId;
    const pnum = (product?.productnumber || '').replace(/^#/, '');
    setSavingAll(true);
    setSaveError(null);
    try {
      // Фонова задача: збереження доробиться навіть якщо закрити картку; сповіщення — уніфіковані.
      await taskManager.run(`Редагування ${pnum || 'товару'}`, () => productService.updateProduct(pid, payload as any), {
        successMsg: `Зміни ${pnum} збережено`,
        errorMsg: `Редагування ${pnum}`,
      });
      if (curPidRef.current !== pid) return;  // картку закрили/перейшли — задача завершилась у фоні
      await loadProduct(false);
      setEditMode(false);
      setDrafts({});
    } catch (e: any) {
      if (curPidRef.current !== pid) return;  // попап показав taskManager
      const detail = e?.response?.data?.detail;
      setSaveError(typeof detail === 'string' ? detail : 'Не вдалося зберегти зміни');
    } finally {
      if (curPidRef.current === pid) setSavingAll(false);
    }
  };

  // Якщо офіційних нема, а реальні є — стартуємо з «Реальні»
  useEffect(() => {
    if (allImages.length === 0) return;
    const hasOfficial = allImages.some((i) => (i.kind ?? 'official') === 'official');
    const hasReal = allImages.some((i) => i.kind === 'real');
    if (!hasOfficial && hasReal) setActiveKind('real');
  }, [allImages]);

  // У edit-режимі для товару БЕЗ фото — стартувати з «Реальні» (типовий сценарій
  // користувача: спершу свої фото, офіційні з'являються пізніше).
  useEffect(() => {
    if (!editMode) return;
    if (allImages.length === 0) setActiveKind('real');
  }, [editMode, allImages.length]);

  // Вихід з edit-режиму на вкладці «Дефекти» → повернути read-галерею на official/real,
  // інакше read-режим показав би порожньо (дефекти там лише оверлей ⚠).
  useEffect(() => {
    if (!editMode && activeKind === 'defect') {
      setActiveKind(officialCount > 0 ? 'official' : 'real');
    }
  }, [editMode, activeKind, officialCount]);

  // Clamp activeIdx коли images повертаються чи перемикається showDefects/activeKind
  useEffect(() => {
    if (activeIdx >= images.length) setActiveIdx(Math.max(0, images.length - 1));
  }, [images.length, activeIdx]);

  // Preload усі фото товару у фоні (browser http-cache) для миттєвого перемикання.
  useEffect(() => {
    if (allImages.length === 0) return;
    const preloaded: HTMLImageElement[] = [];
    for (const img of allImages) {
      const i = new window.Image();
      i.src = img.url;
      preloaded.push(i);
    }
    return () => { preloaded.length = 0; };
  }, [allImages]);

  // Card navigation вимкнено в режимі редагування (щоб не загубити незбережене).
  const navPrev = (!editMode && onPrev) ? onPrev : undefined;
  const navNext = (!editMode && onNext) ? onNext : undefined;

  // Keyboard: Esc closes (або виходить з edit-режиму), ←/→ gallery, < > картки.
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      // Відкритий діалог системи feedback/Prom (клас .bms-dialog-host) обробляє
      // клавіші сам — картка позаду не реагує (Esc не закриє її «наскрізь»).
      if (document.querySelector('.bms-dialog-host')) return;
      if (e.key === 'Escape') {
        if (previewVisible) return;   // antd-прев'ю саме обробляє свій Esc
        // Esc при відкритій картці = ЛИШЕ закрити картку. Гасимо подію, щоб вона не
        // дійшла до вебв'ю/ОС і не «зменшувала» вікно (вихід із fullscreen тощо).
        e.preventDefault();
        e.stopPropagation();
        if (editMode) { cancelEditMode(); return; }
        onClose();
        return;
      }
      const tag = (e.target as HTMLElement)?.tagName;
      const inField = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable;
      if (editMode || inField) return;  // у режимі редагування клавіатурна навігація вимкнена
      if (!previewVisible && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (e.code === 'Comma' && navPrev) { e.preventDefault(); navPrev(); return; }
        if (e.code === 'Period' && navNext) { e.preventDefault(); navNext(); return; }
      }
      if (images.length > 1 && !previewVisible) {
        if (e.key === 'ArrowLeft') setActiveIdx((i) => (i - 1 + images.length) % images.length);
        if (e.key === 'ArrowRight') setActiveIdx((i) => (i + 1) % images.length);
      }
    };
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [open, onClose, images.length, previewVisible, navPrev, navNext, editMode]);

  const p = product;

  const status = useMemo(() => {
    if (!p) return { text: '', color: 'default' };
    // Єдине джерело статусу (спільне з таблицею): живий sold_count, знімок —
    // лише фолбек де живих даних нема. Див. getProductDisplayStatus.
    return getProductDisplayStatus(p);
  }, [p]);

  // Розміри в інших системах (UA/USA/UK) — обчислені, без колонки в аркуші → read-only.
  const derivedSizes = useMemo(() => {
    if (!p) return [];
    const parts: { label: string; val: any }[] = [
      { label: 'UA', val: p.sizeua },
      { label: 'USA', val: p.sizeusa },
      { label: 'UK', val: p.sizeuk },
    ];
    return parts.filter((x) => x.val);
  }, [p]);

  // Поля, залочені в програмі (Phase 2a) — парсер їх не перезатирає
  const lockedFields = useMemo(() => {
    const raw = (p?.manually_edited_fields || '').trim();
    return new Set(raw ? raw.split(',').map((s) => s.trim()).filter(Boolean) : []);
  }, [p]);

  // Тип-залежна видимість полів (як у формі «Додати товар»): ховаємо в edit-режимі
  // поля, що не доречні для категорії товару. ⚠️ Хук — ДО early return (нижче), інакше
  // порядок хуків ламається між рендерами → Minified React error #310.
  const hiddenFields = useMemo(
    () => hiddenFieldsForType((p as any)?.type_name),
    [(p as any)?.type_name]
  );

  // Профіль моделі: тягнемо, коли в режимі редагування задані бренд+модель
  // (дебаунс 600мс; власний запис виключаємо). ⚠️ ХУК — має бути ДО early-return
  // `if (!open)` нижче, інакше React #310 (умовний виклик хука).
  useEffect(() => {
    if (!open || !editMode) { return; }
    const brand = (classDrafts['brand_name'] ?? '').trim();
    const model = (drafts['model'] ?? '').trim();
    if (!brand || model.length < 2) { setModelProfile(null); return; }
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`/api/products/model-profile?brand_name=${encodeURIComponent(brand)}&model=${encodeURIComponent(model)}&exclude_id=${productId}`);
        setModelProfile(r.ok ? await r.json() : null);
      } catch { setModelProfile(null); }
    }, 600);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editMode, classDrafts['brand_name'], drafts['model'], productId]);

  if (!open) return null;

  // ── Дрібні UI-хелпери ─────────────────────────────────────────────────────────
  const inputCls = 'w-full px-2 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-primary-400';

  // Підтипи фільтруються за обраним типом (subtypes мають typeid). Тип у драфті —
  // НАЗВА (комбобокс), тож id шукаємо по довіднику (ci). Якщо тип не розпізнано
  // (нове/порожнє значення) — показуємо всі. Поточний підтип завжди у списку.
  const subtypeOptions = (() => {
    const all = (filterOpts?.subtypes ?? []) as any[];
    const tname = (classDrafts['type_name'] ?? '').trim().toLowerCase();
    const tid = tname
      ? ((filterOpts?.types ?? []) as any[]).find((t) => (t.name || '').toLowerCase() === tname)?.id ?? null
      : null;
    if (tid == null) return all;
    const curSub = (p as any)?.subtypeid;
    return all.filter((s) => s.typeid === tid || s.id === curSub);
  })();

  // Заповнити ЛИШЕ порожні поля значеннями профілю моделі (драфти; в БД нічого
  // не пише — збереження звичайною кнопкою «Зберегти», з локами і write-back).
  const applyModelProfile = () => {
    if (!modelProfile) return;
    const pf = modelProfile.fields || {};
    let filled = 0;
    const cd = { ...classDrafts };
    for (const f of ['type_name', 'subtype_name', 'style_name']) {
      if (!(cd[f] ?? '').trim() && pf[f]) { cd[f] = pf[f].value; filled++; }
    }
    if (!(cd['genderid'] ?? '') && pf['gender_name']) {
      const g = ((filterOpts?.genders ?? []) as any[])
        .find((x) => (x.name || '').toLowerCase() === pf['gender_name'].value.toLowerCase());
      if (g) { cd['genderid'] = String(g.id); filled++; }
    }
    const dr = { ...drafts };
    const SCALARS = ['season', 'collection', 'geometric_shape', 'width',
      'manufacturer_country_name', 'heel_type_name', 'lace_type_name', 'sole_type_name',
      'toe_shape_name', 'fastening_type_name', 'lining_name', 'technology_name', 'packaging_name'];
    for (const f of SCALARS) {
      if (f in dr && !(dr[f] ?? '').trim() && pf[f]) { dr[f] = pf[f].value; filled++; }
    }
    const md = { ...materialDrafts };
    for (const [pos, info] of Object.entries(modelProfile.materials || {})) {
      if (pos in md && !(md[pos] ?? '').trim()) { md[pos] = (info as any).value; filled++; }
    }
    setClassDrafts(cd); setDrafts(dr); setMaterialDrafts(md);
    setProfileNote(filled ? `✓ заповнено полів: ${filled} — перевір і збережи` : 'порожніх полів немає');
  };

  // Бейдж «змінено» показуємо ЛИШЕ (1) у режимі редагування — чистий перегляд
  // без службових позначок, і (2) для СВІЖИХ правок (< 14 днів) — старі й так
  // «прижились». Сам ЛОК від перезапису парсером діє незалежно від бейджа.
  const LOCK_BADGE_TTL_DAYS = 14;
  const lockFresh = (() => {
    const ts = (p as any)?.manually_edited_at;
    if (!ts) return false;
    const age = Date.now() - new Date(ts).getTime();
    return age >= 0 && age < LOCK_BADGE_TTL_DAYS * 86_400_000;
  })();

  // Бейдж «змінено в програмі» + кнопка «скинути до аркуша» для залоченого поля
  const LockBadge: React.FC<{ field: string }> = ({ field }) =>
    editMode && lockFresh && lockedFields.has(field) ? (
      <span className="inline-flex items-center gap-1 align-middle">
        <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300 font-medium"
              title="Змінено в програмі — парсер не перезатирає">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />змінено
        </span>
        <button onClick={() => unlockField(field)}
                className="text-[11px] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                title="Скинути до аркуша (відновиться при наступному парсингу)">↺</button>
      </span>
    ) : null;

  // Маленька крапка-індикатор лока (для компактних місць — лейбли в edit-режимі)
  const LockDot: React.FC<{ field: string }> = ({ field }) =>
    lockFresh && lockedFields.has(field)
      ? <span className="w-1.5 h-1.5 rounded-full bg-amber-500 inline-block" title="Змінено в програмі" />
      : null;

  // Акуратна кнопка-олівець (antd-іконка). Видима на hover рядка/блоку.
  const EditBtn: React.FC<{ onClick: () => void; title?: string; always?: boolean }> = ({ onClick, title, always }) => (
    <button onClick={onClick} title={title || 'Редагувати'}
      className={`inline-flex items-center justify-center w-6 h-6 rounded-md shrink-0
        text-gray-400 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-200
        hover:bg-gray-100 dark:hover:bg-gray-700/60 transition-all
        ${always ? 'opacity-70 hover:opacity-100' : 'opacity-0 group-hover:opacity-100'}`}>
      <EditOutlined style={{ fontSize: 13 }} />
    </button>
  );

  // Згортуваний підрозділ (Матеріали/Інше). Заголовок-кнопка з шевроном; вміст
  // прихований доки користувач не розкриє. У edit-режимі завжди розгорнутий.
  // ⚠️ ВИКЛИКАТИ ЯК ФУНКЦІЮ: {CollapsibleSection({ id, title, children })}, НЕ
  // <CollapsibleSection/>. Оголошено всередині компонента → як JSX-елемент має
  // нову ідентичність типу щорендера → React розмонтовує+монтує вміст на кожну
  // клавішу → інпути характеристик (Матеріали/Інше) ГУБЛЯТЬ ФОКУС після літери.
  // Виклик функцією вбудовує JSX без межі компонента → фокус живе. Без хуків —
  // тож безпечно (як EditCell/classSelect). Див. feedback_inner_component_focus_loss.
  const CollapsibleSection = ({ id, title, children }: { id: string; title: string; children: React.ReactNode }): React.ReactElement => {
    const sectionOpen = editMode || !!openSections[id];
    return (
      <div className="mt-4 pt-4 border-t border-gray-100 dark:border-gray-800">
        <button
          type="button"
          onClick={editMode ? undefined : () => toggleSection(id)}
          className={`flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium ${sectionOpen ? 'mb-3' : ''} ${editMode ? 'cursor-default' : 'hover:text-gray-600 dark:hover:text-gray-300'} transition-colors`}
        >
          {!editMode && (
            <RightOutlined style={{ fontSize: 9 }} className={`transition-transform duration-200 ${sectionOpen ? 'rotate-90' : ''}`} />
          )}
          {title}
          {!sectionOpen && <span className="w-1.5 h-1.5 rounded-full bg-gray-300 dark:bg-gray-600" />}
        </button>
        {sectionOpen && children}
      </div>
    );
  };

  // Read-only клітинка характеристик: лейбл + значення (порожні ховаємо для компактності).
  const RoCell: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) => {
    if (value === null || value === undefined || value === '') return null;
    return (
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium">{label}</span>
        <span className="text-sm text-gray-800 dark:text-gray-200 break-words">
          {(typeof value === 'string' || typeof value === 'number')
            ? <CopyOnClick value={value as string | number} />
            : value}
        </span>
      </div>
    );
  };

  // Редагована клітинка: input у глобальному edit-режимі; інакше — значення (+ лок).
  // У read-режимі порожнє значення ховаємо. Тип number → number input.
  // ⚠️ ВИКЛИКАТИ ЯК ФУНКЦІЮ: {EditCell({ field, label })}, НЕ <EditCell .../>.
  // EditCell оголошено всередині компонента → як JSX-елемент <EditCell/> він має
  // НОВУ ідентичність типу щорендера, тож React РОЗМОНТОВУЄ+монтує input на кожне
  // натискання → інпут губить фокус після першої літери («стан не редагується»).
  // Виклик функцією вбудовує JSX без межі компонента → input зберігає фокус.
  const EditCell = ({ field, label, type = 'text', placeholder, lockField }: { field: string; label: string; type?: FieldType; placeholder?: string; lockField?: string }): React.ReactElement | null => {
    const lf = lockField ?? field;   // lock state may live on a different DB column (e.g. FK id)
    // У edit-режимі ховаємо тип-нерелевантні поля. У read-режимі — лишаємо
    // (бо там empty-guard сам приховає; а якщо значення є — хай користувач його бачить).
    if (editMode && hiddenFields.has(field)) return null;
    if (editMode) {
      return (
        <div className="flex flex-col gap-1 min-w-0">
          <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium flex items-center gap-1.5">
            {label}<LockDot field={lf} />
          </span>
          <input
            type={type === 'number' ? 'number' : 'text'}
            value={drafts[field] ?? ''}
            onChange={(e) => setDraft(field, e.target.value)}
            placeholder={placeholder}
            className={inputCls}
          />
        </div>
      );
    }
    const v = (p as any)?.[field];
    if (v === null || v === undefined || v === '') return null;
    return (
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium">{label}</span>
        <span className="text-sm text-gray-800 dark:text-gray-200 break-words flex items-center gap-2">
          <CopyOnClick value={v as string | number} />
          <LockBadge field={lf} />
        </span>
      </div>
    );
  };

  // Класифікація: дропдаун наявних значень (edit) / RoCell зі значенням (read).
  // ⚠️ Викликати ЯК ФУНКЦІЮ: {classSelect({...})} — як і EditCell (без межі компонента).
  // Комбобокс класифікації (1.4): пошук по довіднику + ВІЛЬНЕ введення нового
  // значення (нативний datalist — фільтрує підказки при наборі, дозволяє свій
  // текст). nameField — драфт/поле payload (brand_name…), lockField — FK для
  // бейджів лока (brandid…). Сервер робить get-or-create за назвою.
  const classCombo = ({ nameField, lockField, label, options, readValue }: {
    nameField: string; lockField: string; label: string;
    options: { id: number; name: string }[]; readValue?: React.ReactNode;
  }): React.ReactElement | null => {
    if (editMode) {
      return (
        <div className="flex flex-col gap-1 min-w-0">
          <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium flex items-center gap-1.5">
            {label}<LockDot field={lockField} />
          </span>
          <input
            list={`dl-${nameField}`}
            value={classDrafts[nameField] ?? ''}
            onChange={(e) => setClassDrafts((d) => ({ ...d, [nameField]: e.target.value }))}
            placeholder="пошук або нове…"
            className={inputCls}
          />
          <datalist id={`dl-${nameField}`}>
            {options.map((o) => <option key={o.id} value={o.name} />)}
          </datalist>
        </div>
      );
    }
    if (readValue === null || readValue === undefined || readValue === '') return null;
    return (
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium">{label}</span>
        <span className="text-sm text-gray-800 dark:text-gray-200 break-words flex items-center gap-2">
          {(typeof readValue === 'string' || typeof readValue === 'number') ? <CopyOnClick value={readValue as string | number} /> : readValue}
          <LockBadge field={lockField} />
        </span>
      </div>
    );
  };

  const classSelect = ({ field, label, options, readValue }: {
    field: string; label: string; options: { id: number; name: string }[]; readValue?: React.ReactNode;
  }): React.ReactElement | null => {
    if (editMode) {
      return (
        <div className="flex flex-col gap-1 min-w-0">
          <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium flex items-center gap-1.5">
            {label}<LockDot field={field} />
          </span>
          <select
            value={classDrafts[field] ?? ''}
            onChange={(e) => setClassDrafts((d) => ({ ...d, [field]: e.target.value }))}
            className={inputCls}
          >
            <option value="">—</option>
            {options.map((o) => <option key={o.id} value={String(o.id)}>{o.name}</option>)}
          </select>
        </div>
      );
    }
    if (readValue === null || readValue === undefined || readValue === '') return null;
    return (
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium">{label}</span>
        <span className="text-sm text-gray-800 dark:text-gray-200 break-words flex items-center gap-2">
          {(typeof readValue === 'string' || typeof readValue === 'number') ? <CopyOnClick value={readValue as string | number} /> : readValue}
          <LockBadge field={field} />
        </span>
      </div>
    );
  };

  const fmtRange = (min?: number | null, max?: number | null): string | null => {
    if (min == null && max == null) return null;
    if (min == null) return `до ${max} см`;
    if (max == null) return `від ${min} см`;
    if (min === max) return `${min} см`;
    return `${min}–${max} см`;
  };

  const activeImage = images[activeIdx];
  const productTitle = p ? ([formatBrandName((p as any).brand_name), p.model].filter(Boolean).join(' ') || (p.productnumber || '').replace(/^#/, '')) : '';
  const pnumClean = (p?.productnumber || '').replace(/^#/, '');
  // Колонка-галерея присутня ЗАВЖДИ (стабільний макет: фото ліворуч, інфо праворуч).
  // Коли фото нема — показуємо плейсхолдер «Фото відсутнє», а не згортаємо колонку
  // (інакше макет «стрибає» між товарами — користувача це збиває).
  const hasGalleryColumn = true;
  // Характеристики живуть у правій колонці ПОРУЧ із фото (заповнюють її висоту) — 2 колонки.
  const charCols = 'grid-cols-2';
  const hasAnySize = !!(p && (p.sizeeu || (p as any).size_letter || p.measurementscm || p.dimensions || (p as any).geometric_shape || derivedSizes.length > 0));
  // «Справжній» розмір (EU/буквений/похідні/СМ) — БЕЗ габаритів. Якщо є лише габарити
  // (сумки), заголовок «Розмір» зайвий над самотнім чипом «Габарити» → ховаємо його.
  const hasRealSize = !!(p && (p.sizeeu || (p as any).size_letter || p.measurementscm || derivedSizes.length > 0));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <style>{`
        @keyframes bmsFadeIn { from { opacity: 0; } to { opacity: 1; } }
        .bms-fade-in { animation: bmsFadeIn 180ms ease-out; }
      `}</style>
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={editMode ? undefined : onClose} />

      {/* Крайові стрілки навігації між картками (поза карткою). Сховані в edit-режимі. */}
      {navPrev && (
        <div
          className="group/nav absolute left-0 top-0 bottom-0 w-14 sm:w-16 z-[60] flex items-center justify-start cursor-pointer"
          onClick={(e) => { e.stopPropagation(); navPrev(); }}
          title="Попередній товар  ( < )"
        >
          <button
            type="button"
            aria-label="Попередній товар"
            className="flex items-center justify-center w-7 h-[20vh] rounded-r-2xl bg-white/15 dark:bg-white/5 backdrop-blur-md text-white/70 ring-1 ring-white/15 opacity-0 group-hover/nav:opacity-100 transition-all duration-300 hover:bg-white/25 hover:text-white"
          >
            <LeftOutlined style={{ fontSize: 16 }} />
          </button>
        </div>
      )}
      {navNext && (
        <div
          className="group/nav absolute right-0 top-0 bottom-0 w-14 sm:w-16 z-[60] flex items-center justify-end cursor-pointer"
          onClick={(e) => { e.stopPropagation(); navNext(); }}
          title="Наступний товар  ( > )"
        >
          <button
            type="button"
            aria-label="Наступний товар"
            className="flex items-center justify-center w-7 h-[20vh] rounded-l-2xl bg-white/15 dark:bg-white/5 backdrop-blur-md text-white/70 ring-1 ring-white/15 opacity-0 group-hover/nav:opacity-100 transition-all duration-300 hover:bg-white/25 hover:text-white"
          >
            <RightOutlined style={{ fontSize: 16 }} />
          </button>
        </div>
      )}

      {/* Modal */}
      {/* Фіксована висота (а не max-h) → бокс НЕ ресайзиться/перецентровується між
          товарами різного обсягу контенту = головна причина «скачків» при ◀/▶. */}
      <div className="relative bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-6xl mx-4 h-[90vh] overflow-hidden flex flex-col">

        {loading && (
          <div className="flex items-center justify-center py-32">
            <Spin size="large" />
          </div>
        )}

        {!loading && !p && (
          <div className="flex items-center justify-center py-32 text-gray-400">
            Товар не знайдено
          </div>
        )}

        {!loading && p && (
          <div className="flex flex-col flex-1 min-h-0 bms-fade-in">
            {/* Header */}
            <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-gray-100 dark:border-gray-800">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-3 mb-1 flex-wrap">
                  <span className="text-xs font-mono text-gray-400 dark:text-gray-500 px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-800">
                    {pnumClean ? <CopyOnClick value={pnumClean} /> : '—'}
                  </span>
                  {(p as any).type_name && (
                    <span className="text-xs text-gray-500 dark:text-gray-400">{(p as any).type_name}{(p as any).subtype_name ? ` · ${(p as any).subtype_name}` : ''}</span>
                  )}
                  {p.is_rostovka && (
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-purple-100 text-purple-700 border border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-700">
                      ▤ Ростовка
                    </span>
                  )}
                  {defectCount > 0 && (
                    <button
                      type="button"
                      onClick={toggleDefects}
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border transition-colors ${
                        showDefects
                          ? 'bg-amber-500 text-white border-amber-600 dark:bg-amber-600 dark:border-amber-500'
                          : 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700 dark:hover:bg-amber-900/50'
                      }`}
                      title={showDefects ? 'Сховати фото дефектів' : `Показати фото дефектів (${defectCount})`}
                    >
                      <WarningOutlined className="text-[11px]" />
                      <span>Дефект{defectCount > 1 ? `·${defectCount}` : ''}</span>
                    </button>
                  )}
                  {editMode && (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-primary-100 text-primary-700 border border-primary-200 dark:bg-primary-900/30 dark:text-primary-300 dark:border-primary-700">
                      Режим редагування
                    </span>
                  )}

                  {/* Публікація в публічний інтернет-каталог (Telegram Mini App) */}
                  {catalogStatus && (
                    <>
                      <button
                        type="button"
                        onClick={toggleCatalogPublished}
                        disabled={catalogSaving}
                        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border transition-colors disabled:opacity-50 ${
                          catalogStatus.is_published
                            ? 'bg-emerald-500 text-white border-emerald-600 dark:bg-emerald-600 dark:border-emerald-500'
                            : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100 dark:bg-gray-800 dark:text-gray-400 dark:border-gray-700 dark:hover:bg-gray-700'
                        }`}
                        title={catalogStatus.is_published ? 'Прибрати з публічного інтернет-каталогу' : 'Показати в публічному інтернет-каталозі'}
                      >
                        {catalogStatus.is_published ? <EyeOutlined className="text-[11px]" /> : <EyeInvisibleOutlined className="text-[11px]" />}
                        <span>{catalogStatus.is_published ? 'У каталозі' : 'Не в каталозі'}</span>
                      </button>
                      {catalogStatus.is_published && (
                        <button
                          type="button"
                          onClick={toggleCatalogFeatured}
                          disabled={catalogSaving}
                          className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border transition-colors disabled:opacity-50 ${
                            catalogStatus.is_featured
                              ? 'bg-amber-400 text-amber-950 border-amber-500 dark:bg-amber-500 dark:border-amber-400'
                              : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100 dark:bg-gray-800 dark:text-gray-400 dark:border-gray-700 dark:hover:bg-gray-700'
                          }`}
                          title={catalogStatus.is_featured ? 'Прибрати «Рекомендований»' : 'Позначити «Рекомендований» (вгору каталогу)'}
                        >
                          <StarFilled className="text-[11px]" />
                          <span>Рекомендований</span>
                        </button>
                      )}
                    </>
                  )}

                  {/* Чіп «Prom»: тумблер публікації (аналог «У каталозі»). Активний = на Prom.
                      Клік: неактивний → публікація; активний → підтвердження й видалення з Prom. */}
                  <button
                    type="button"
                    onClick={promToggle}
                    disabled={promBusy}
                    className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border transition-colors disabled:opacity-50 ${
                      promPublished
                        ? 'bg-violet-700 text-white border-violet-800 dark:bg-violet-600 dark:border-violet-500'
                        : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100 dark:bg-gray-800 dark:text-gray-400 dark:border-gray-700 dark:hover:bg-gray-700'
                    }`}
                    title={promPublished ? 'Прибрати товар з Prom' : 'Опублікувати товар на Prom'}
                  >
                    {promBusy ? <SyncOutlined spin className="text-[11px]" /> : <ShoppingOutlined className="text-[11px]" />}
                    <span>Prom</span>
                  </button>
                </div>
                <h2 className="text-2xl font-semibold text-gray-900 dark:text-gray-50 truncate leading-tight">
                  {productTitle ? <CopyOnClick value={productTitle} /> : productTitle}
                </h2>
              </div>

              {/* Дії: Google + Редагувати / Зберегти все · Скасувати + Закрити */}
              <div className="shrink-0 ml-2 flex items-center gap-2">
                {/* Порядок (зліва направо): Поставка · Таблиця · Google · Редагувати.
                    Усі — єдиний стиль HDR_BTN (однаковий вигляд і поведінка). */}

                {/* «Поставка»: перейти на вкладку Поставки з відкритою карткою завозу */}
                {(p as any)?.deliveryid && (
                  <button
                    onClick={() => {
                      localStorage.setItem('bms:pendingDeliveryCard', String((p as any).deliveryid));
                      window.dispatchEvent(new CustomEvent('bms:switch-to-deliveries'));
                      onClose();
                    }}
                    className={HDR_BTN}
                    title="Відкрити поставку цього товару"
                  >
                    <InboxOutlined style={{ fontSize: 14 }} /><span>Поставка</span>
                  </button>
                )}

                {/* «Таблиця»: відкрити журнал у браузері прямо на аркуші завозу */}
                {(p as any)?.deliveryid && (
                  <button
                    onClick={async () => {
                      try {
                        const r = await fetch(`/api/products/${productId}/journal-url`);
                        if (!r.ok) throw new Error(await r.text());
                        const { url } = await r.json();
                        const a = document.createElement('a');
                        a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer';
                        document.body.appendChild(a); a.click(); a.remove();
                      } catch (e) { console.error('journal-url', e); }
                    }}
                    className={HDR_BTN}
                    title="Відкрити аркуш цього завозу в Google Таблиці"
                  >
                    <TableOutlined style={{ fontSize: 14 }} /><span>Таблиця</span>
                  </button>
                )}

                {/* «Знайти в Google» */}
                {!editMode && (() => {
                  const parts = [(p as any).brand_name, p.model, p.marking].filter(Boolean) as string[];
                  const q = parts.join(' ').replace(/\s+/g, ' ').trim();
                  if (!q) return null;
                  return (
                    <a
                      href={`https://www.google.com/search?q=${encodeURIComponent(q)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={HDR_BTN}
                      title={`Пошук в Google: ${q}`}
                    >
                      <span className="font-bold text-xs">G</span>
                      <span>Знайти в Google</span>
                    </a>
                  );
                })()}

                {/* Редагувати / Зберегти все · Скасувати (найправіше, перед «Закрити») */}
                {!editMode ? (
                  <button
                    onClick={enterEditMode}
                    className={HDR_BTN}
                    title="Редагувати всі поля картки"
                  >
                    <EditOutlined style={{ fontSize: 14 }} />
                    <span>Редагувати</span>
                  </button>
                ) : (
                  <>
                    <button
                      onClick={saveAll}
                      disabled={savingAll}
                      className="px-3 py-2 rounded-lg text-sm font-semibold bg-green-600 hover:bg-green-700 !text-white transition-colors duration-150 flex items-center gap-1.5 disabled:opacity-60"
                      title="Зберегти всі зміни (БД + аркуш)"
                    >
                      <CheckOutlined style={{ fontSize: 14 }} />
                      <span>{savingAll ? 'Збереження…' : 'Зберегти все'}</span>
                    </button>
                    <button
                      onClick={cancelEditMode}
                      disabled={savingAll}
                      className="px-3 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 hover:text-gray-900 hover:bg-gray-50 dark:text-gray-300 dark:hover:text-gray-100 dark:hover:bg-gray-800 transition-colors duration-150 flex items-center gap-1.5"
                    >
                      Скасувати
                    </button>
                  </>
                )}

                <button
                  onClick={editMode ? cancelEditMode : onClose}
                  className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
                  aria-label="Закрити"
                >
                  <CloseOutlined className="text-base" />
                </button>
              </div>
            </div>

            {/* Body */}
            <div ref={bodyRef} className="overflow-y-auto flex-1">
              {saveError && (
                <div className="mx-6 mt-4 px-3 py-2 rounded-lg text-sm bg-red-50 text-red-700 border border-red-200 dark:bg-red-900/20 dark:text-red-300 dark:border-red-800">
                  {saveError}
                </div>
              )}

              {/* Hero: галерея (якщо є/вантажиться) + зведення (ціна/статус/розмір) */}
              <div className={`p-6 ${hasGalleryColumn ? 'grid grid-cols-1 lg:grid-cols-[minmax(0,580px)_minmax(0,1fr)] gap-8' : ''}`}>

                {/* Left: Gallery */}
                {hasGalleryColumn && (
                  <div className="flex flex-col gap-3">
                    <div className="relative w-full aspect-square bg-gray-50 dark:bg-gray-800/40 rounded-xl overflow-hidden border border-gray-100 dark:border-gray-800 flex items-center justify-center group">
                      {activeImage ? (
                        <>
                          <Image
                            key={activeImage.url}
                            src={activeImage.url}
                            alt={activeImage.filename}
                            preview={{ visible: previewVisible, onVisibleChange: setPreviewVisible, src: activeImage.url }}
                            className="!w-full !h-full bms-fade-in"
                            style={{ objectFit: 'contain', width: '100%', height: '100%', cursor: 'zoom-in' }}
                            wrapperStyle={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                          />
                          {activeImage.is_defect && (
                            <div className="absolute top-3 left-3 inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-amber-500/90 text-white shadow-md pointer-events-none">
                              <WarningOutlined className="text-xs" />
                              <span>Дефект</span>
                            </div>
                          )}
                          {images.length > 1 && (
                            <>
                              <button
                                onClick={(e) => { e.stopPropagation(); setActiveIdx((i) => (i - 1 + images.length) % images.length); }}
                                className="absolute left-2 top-1/2 -translate-y-1/2 p-2 rounded-full bg-white/80 dark:bg-gray-900/80 hover:bg-white dark:hover:bg-gray-900 shadow-md text-gray-700 dark:text-gray-200 opacity-0 group-hover:opacity-100 transition-opacity"
                                aria-label="Попереднє фото"
                              >
                                <LeftOutlined />
                              </button>
                              <button
                                onClick={(e) => { e.stopPropagation(); setActiveIdx((i) => (i + 1) % images.length); }}
                                className="absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-full bg-white/80 dark:bg-gray-900/80 hover:bg-white dark:hover:bg-gray-900 shadow-md text-gray-700 dark:text-gray-200 opacity-0 group-hover:opacity-100 transition-opacity"
                                aria-label="Наступне фото"
                              >
                                <RightOutlined />
                              </button>
                              <div className="absolute bottom-3 right-3 px-2 py-1 rounded-md text-xs bg-black/60 text-white font-mono">
                                {activeIdx + 1} / {images.length}
                              </div>
                            </>
                          )}
                        </>
                      ) : imagesLoading ? (
                        <div className="flex flex-col items-center justify-center text-gray-300 dark:text-gray-600">
                          <Spin />
                          <span className="text-[11px] text-gray-400 dark:text-gray-500 mt-3">Завантаження фото…</span>
                        </div>
                      ) : (
                        <div className="flex flex-col items-center justify-center text-gray-300 dark:text-gray-600 px-6 w-full text-center">
                          <PictureOutlined style={{ fontSize: 56 }} />
                          <span className="text-sm mt-3">Фото відсутнє</span>
                          <span className="text-[11px] text-gray-400 dark:text-gray-500 mt-1">додайте файли з префіксом {pnumClean || 'номер'}_</span>
                          <button type="button"
                            onClick={() => { setPhotoSrcDraft((p as any).official_photos_from || ''); setEditingPhotoSrc(true); }}
                            className="mt-4 text-[12px] text-blue-600 dark:text-blue-400 hover:underline">
                            📷 Підтягнути студійні фото з іншого товару…
                          </button>
                        </div>
                      )}
                    </div>

                    {/* Gallery kind switcher */}
                    {hasBothKinds && (
                      <div className="inline-flex self-start items-center rounded-full bg-gray-100 dark:bg-gray-800/60 p-0.5 text-[11px] font-medium select-none">
                        <button type="button"
                          onClick={() => { if (activeKind !== 'official') { setActiveKind('official'); setActiveIdx(0); } }}
                          className={`px-3 py-1 rounded-full transition-all duration-200 ${activeKind === 'official' ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-50 shadow-sm' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}
                          title={`Офіційні фото (${officialCount})`}>Офіційні</button>
                        <button type="button"
                          onClick={() => { if (activeKind !== 'real') { setActiveKind('real'); setActiveIdx(0); } }}
                          className={`px-3 py-1 rounded-full transition-all duration-200 ${activeKind === 'real' ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-50 shadow-sm' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}
                          title={`Мої реальні фото (${realCount})`}>Реальні</button>
                      </div>
                    )}

                    {/* Бейдж джерела студійних фото (коли фото запозичені) */}
                    {images.length > 0 && (p as any).official_photos_from && (
                      <button type="button"
                        onClick={() => { setPhotoSrcDraft((p as any).official_photos_from || ''); setEditingPhotoSrc(true); }}
                        className="self-start inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-[11px] bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-colors"
                        title="Змінити джерело студійних фото">
                        📷 студійні з {String((p as any).official_photos_from).replace(/^#/, '')}
                      </button>
                    )}

                    {/* Є реальні фото, але офіційних нема й донор не заданий —
                        дозволяємо прив'язати студійні з іншого товару (як у порожньому
                        випадку, але тут фото вже є — лише без офіційних). */}
                    {allImages.length > 0 && officialCount === 0 && !(p as any).official_photos_from && (
                      <button type="button"
                        onClick={() => { setPhotoSrcDraft(''); setEditingPhotoSrc(true); }}
                        className="self-start text-[12px] text-blue-600 dark:text-blue-400 hover:underline">
                        📷 Підтягнути студійні фото з іншого товару…
                      </button>
                    )}

                    {/* Thumbnails */}
                    {images.length > 1 && (
                      <div className="flex gap-2 overflow-x-auto py-1.5 -mx-1 px-1">
                        {images.map((img, i) => (
                          <button key={img.filename} onClick={() => setActiveIdx(i)}
                            className={`relative shrink-0 w-16 h-16 rounded-lg overflow-hidden border-2 transition-all ${
                              i === activeIdx
                                ? (img.is_defect ? 'border-amber-500 ring-2 ring-amber-200 dark:ring-amber-800' : 'border-primary-500 ring-2 ring-primary-200 dark:ring-primary-800')
                                : (img.is_defect ? 'border-amber-400/60 hover:border-amber-500 opacity-80 hover:opacity-100' : 'border-gray-200 dark:border-gray-700 hover:border-gray-400 dark:hover:border-gray-500 opacity-70 hover:opacity-100')
                            }`}
                            title={img.is_defect ? `Дефект: ${img.filename}` : img.filename}>
                            <img src={img.url} alt={img.filename} className="w-full h-full object-cover" loading="lazy" />
                            {img.is_defect && (
                              <span className="absolute top-0.5 right-0.5 inline-flex items-center justify-center w-4 h-4 rounded-full bg-amber-500 text-white text-[9px] shadow">
                                <WarningOutlined style={{ fontSize: 9 }} />
                              </span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}

                    {/* Менеджер фото (лише в режимі редагування) */}
                    {editMode && (
                      <div className="w-full mt-1 p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/60 dark:bg-gray-800/40">
                        <input ref={addPhotoInputRef} type="file" accept="image/*" multiple className="hidden"
                          onChange={(e) => { handleAddPhotos(e.target.files); e.target.value = ''; }} />
                        <input ref={replacePhotoInputRef} type="file" accept="image/*" className="hidden"
                          onChange={(e) => { const f = e.target.files?.[0] || null; const t = replaceTargetRef.current; if (t) handleReplacePhoto(t, f); e.target.value = ''; }} />

                        <div className="flex items-center justify-between mb-2.5 gap-2">
                          <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium whitespace-nowrap">
                            Фото товару {photoBusy && <Spin size="small" className="ml-1" />}
                          </span>
                          {/* Перемикач куди завантажувати/чим керувати: офіційні (_NN) vs реальні (_00N) */}
                          <div className="inline-flex rounded-md border border-gray-300 dark:border-gray-600 overflow-hidden text-[11px]">
                            <button type="button" disabled={photoBusy}
                              onClick={() => setActiveKind('official')}
                              title="Студійні/каталожні фото (нумерація _01.._0N)"
                              className={`px-2 py-1 transition-colors ${activeKind === 'official' ? 'bg-gray-900 text-white' : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'}`}>
                              Офіційні
                            </button>
                            <button type="button" disabled={photoBusy}
                              onClick={() => setActiveKind('real')}
                              title="Реальні/власні фото (нумерація _001.._00N)"
                              className={`px-2 py-1 transition-colors border-l border-gray-300 dark:border-gray-600 ${activeKind === 'real' ? 'bg-gray-900 text-white' : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'}`}>
                              Реальні
                            </button>
                            <button type="button" disabled={photoBusy}
                              onClick={() => setActiveKind('defect')}
                              title="Фото дефектів (нумерація _def1.._defN)"
                              className={`px-2 py-1 transition-colors border-l border-gray-300 dark:border-gray-600 ${activeKind === 'defect' ? 'bg-amber-500 text-white' : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'}`}>
                              Дефекти
                            </button>
                          </div>
                          <button type="button" disabled={photoBusy}
                            onClick={() => addPhotoInputRef.current?.click()}
                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[12px] bg-gray-900 text-white hover:bg-black disabled:opacity-50 transition-colors whitespace-nowrap">
                            <PlusOutlined style={{ fontSize: 11 }} /> Додати
                          </button>
                        </div>

                        {officialImages.length === 0 ? (
                          <div onClick={() => addPhotoInputRef.current?.click()}
                            className="flex flex-col items-center justify-center gap-1 py-6 rounded-lg border-2 border-dashed border-gray-300 dark:border-gray-600 text-gray-400 cursor-pointer hover:border-gray-400 dark:hover:border-gray-500">
                            <PictureOutlined style={{ fontSize: 28 }} />
                            <span className="text-[12px]">Натисни «Додати», щоб завантажити фото</span>
                          </div>
                        ) : (
                          <>
                            <div className="grid grid-cols-4 sm:grid-cols-5 gap-2">
                              {mgrOrder.map((fn, i) => { const img = imgByName.get(fn); if (!img) return null; const dragging = dragIdx === i; const isOver = overIdx === i && dragIdx !== null && dragIdx !== i; return (
                                <div key={fn}
                                  ref={(el) => { if (el) tileRefs.current.set(fn, el); else tileRefs.current.delete(fn); }}
                                  draggable={!photoBusy}
                                  onDragStart={(e) => { try { e.dataTransfer.effectAllowed = 'move'; } catch {} onTileDragStart(fn); }}
                                  onDragEnter={() => onTileDragEnter(fn)}
                                  onDragOver={(e) => { e.preventDefault(); onTileDragEnter(fn); }}
                                  onDragEnd={onTileDrop}
                                  onDrop={(e) => { e.preventDefault(); onTileDrop(); }}
                                  className={`relative group/ph aspect-square rounded-lg overflow-hidden border bg-white dark:bg-gray-900 ${i === 0 ? 'border-primary-500' : 'border-gray-200 dark:border-gray-700'} ${dragging ? 'opacity-40' : 'shadow-sm hover:shadow-md'} ${isOver ? 'ring-2 ring-primary-500 scale-105 z-10' : ''} transition-[box-shadow,transform,opacity] duration-150 cursor-grab active:cursor-grabbing`}
                                  title={img.filename}>
                                  <img src={img.url} alt={img.filename} draggable={false} className="w-full h-full object-cover pointer-events-none select-none" loading="lazy" />
                                  {i === 0 && (
                                    <span className="absolute bottom-0 inset-x-0 text-center text-[9px] bg-primary-500/90 text-white py-0.5 pointer-events-none">головне</span>
                                  )}
                                  <div className="absolute top-0.5 right-0.5 flex gap-0.5 opacity-0 group-hover/ph:opacity-100 transition-opacity">
                                    <button type="button" disabled={photoBusy}
                                      onClick={() => setMoveMenuFor((cur) => (cur === img.filename ? null : img.filename))}
                                      className="w-5 h-5 inline-flex items-center justify-center rounded bg-white/90 dark:bg-gray-900/90 text-gray-700 dark:text-gray-200 hover:bg-white shadow text-[11px] leading-none"
                                      title="Перенести в інший набір (Офіційні / Реальні / Дефекти)">
                                      ⇄
                                    </button>
                                    <button type="button" disabled={photoBusy}
                                      onClick={() => { replaceTargetRef.current = img.filename; replacePhotoInputRef.current?.click(); }}
                                      className="w-5 h-5 inline-flex items-center justify-center rounded bg-white/90 dark:bg-gray-900/90 text-gray-700 dark:text-gray-200 hover:bg-white shadow"
                                      title="Замінити цей файл">
                                      <SyncOutlined style={{ fontSize: 10 }} />
                                    </button>
                                    <button type="button" disabled={photoBusy}
                                      onClick={async () => { if ((await confirmDialog(`Видалити фото ${img.filename}?`))) handleDeletePhoto(img.filename); }}
                                      className="w-5 h-5 inline-flex items-center justify-center rounded bg-white/90 dark:bg-gray-900/90 text-red-600 hover:bg-white shadow"
                                      title="Видалити">
                                      <CloseOutlined style={{ fontSize: 10 }} />
                                    </button>
                                  </div>
                                  {/* Міні-меню «перенести в …» — інші два набори */}
                                  {moveMenuFor === img.filename && (
                                    <div className="absolute top-6 right-0.5 z-20 flex flex-col rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg overflow-hidden">
                                      {([
                                        { key: 'official' as const, label: 'Офіційні' },
                                        { key: 'real' as const, label: 'Реальні' },
                                        { key: 'defect' as const, label: 'Дефекти' },
                                      ]).filter((k) => k.key !== activeKind).map((k) => (
                                        <button key={k.key} type="button" disabled={photoBusy}
                                          onClick={() => { setMoveMenuFor(null); handleMovePhotoKind(img.filename, k.key); }}
                                          className="px-2.5 py-1 text-[11px] text-left whitespace-nowrap text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700">
                                          → {k.label}
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              ); })}
                            </div>
                            <span className="block mt-2 text-[10px] text-gray-400 dark:text-gray-500">
                              Перетягни, щоб змінити порядок (перше = головне) · ⇄ перенести (Офіційні/Реальні/Дефекти) · 🔄 замінити · ✕ видалити
                            </span>
                          </>
                        )}
                      </div>
                    )}

                    {/* Inline-редактор джерела студійних фото (під галереєю) */}
                    {editingPhotoSrc && (
                      <div className="flex flex-col gap-2 w-full max-w-sm p-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
                        <span className="text-[11px] text-gray-500 dark:text-gray-400">Номер товару-донора студійних фото (порожньо = власні):</span>
                        <input autoFocus value={photoSrcDraft} onChange={(e) => setPhotoSrcDraft(e.target.value)}
                          onKeyDown={(e) => { if (e.key === 'Enter') savePhotoSrc(); if (e.key === 'Escape') setEditingPhotoSrc(false); }}
                          placeholder="Ф3883" disabled={savingPhotoSrc} className={inputCls} />
                        <div className="flex items-center gap-2 justify-end">
                          <button type="button" onClick={() => setEditingPhotoSrc(false)} disabled={savingPhotoSrc}
                            className="text-[12px] px-2 py-1 rounded text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700">Скасувати</button>
                          <button type="button" onClick={savePhotoSrc} disabled={savingPhotoSrc}
                            className="text-[12px] px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 !text-white disabled:opacity-50">
                            {savingPhotoSrc ? 'Збереження…' : 'Зберегти'}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Right: Summary (price / status / sizes) */}
                <div className="flex flex-col min-w-0">
                  {/* Price */}
                  <div className="flex items-center gap-3 mb-3 group flex-wrap">
                    {editMode ? (
                      <span className="inline-flex items-end gap-2">
                        <span className="flex flex-col gap-1">
                          <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium">Ціна</span>
                          <input type="number" value={drafts['price'] ?? ''} onChange={(e) => setDraft('price', e.target.value)}
                            className="w-28 px-2 py-1.5 text-lg font-bold rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800" />
                        </span>
                        <span className="flex flex-col gap-1">
                          <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium">Стара ціна</span>
                          <span className="inline-flex items-center gap-1">
                            <input type="number" value={drafts['oldprice'] ?? ''} onChange={(e) => setDraft('oldprice', e.target.value)}
                              className="w-28 px-2 py-1.5 text-lg font-bold rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800" />
                            {(drafts['oldprice'] ?? '') !== '' && (
                              <button type="button" onClick={() => setDraft('oldprice', '')}
                                className="text-gray-400 hover:text-red-500 text-base px-0.5"
                                title="Прибрати стару ціну (збережеться при «Зберегти»)">✕</button>
                            )}
                          </span>
                        </span>
                      </span>
                    ) : editingField === 'price' ? (
                      <span className="inline-flex items-center gap-1.5">
                        <input autoFocus type="number" value={fieldDraft} onChange={(e) => setFieldDraft(e.target.value)}
                          className="w-28 px-2 py-1 text-2xl font-bold rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
                          onKeyDown={(e) => { if (e.key === 'Enter') saveField('price'); if (e.key === 'Escape') setEditingField(null); }} />
                        <span className="text-2xl font-bold">₴</span>
                        <button onClick={() => saveField('price')} disabled={savingField} className="text-green-600 hover:text-green-700 text-lg px-1" title="Зберегти">✓</button>
                        <button onClick={() => setEditingField(null)} className="text-gray-400 hover:text-gray-600 text-lg px-1" title="Скасувати">✕</button>
                      </span>
                    ) : (
                      <>
                        {p.price != null && p.price > 0 ? (
                          <span className="text-3xl font-bold text-gray-900 dark:text-gray-50">{Number(p.price).toFixed(0)} ₴</span>
                        ) : (
                          <span className="text-xl text-gray-300 dark:text-gray-600">Ціна не вказана</span>
                        )}
                        {p.oldprice != null && p.oldprice > 0 && p.oldprice !== p.price && (
                          <span className="inline-flex items-center gap-0.5">
                            <span className="text-base text-gray-400 line-through">{Number(p.oldprice).toFixed(0)} ₴</span>
                            <button type="button" onClick={clearOldprice} disabled={savingField}
                              className="opacity-0 group-hover:opacity-100 transition-opacity text-gray-400 hover:text-red-500 text-sm px-0.5"
                              title="Прибрати стару ціну (і в журналі)">✕</button>
                          </span>
                        )}
                        <EditBtn onClick={() => startEdit('price', p.price ?? '')} title="Редагувати ціну" always />
                        <LockBadge field="price" />
                      </>
                    )}
                  </div>

                  {/* Status + condition + availability */}
                  <div className="flex flex-wrap items-center gap-2 mb-5">
                    <Tag color={status.color} style={{ margin: 0 }}>{status.text}</Tag>
                    {/* «Заброньовано» — Підтверджене замовлення без оплати (бронь). Slate, мінімалістично. */}
                    {(p as any).is_reserved && (
                      <span
                        title="Заброньовано: є Підтверджене замовлення без оплати"
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold text-slate-600 bg-slate-100 border border-slate-300 dark:text-slate-300 dark:bg-slate-700/40 dark:border-slate-600"
                      >
                        🔒 Заброньовано
                        {((p as any).reserved_count ?? 0) > 1 ? `·${(p as any).reserved_count}` : ''}
                      </span>
                    )}
                    {/* Відображуваний стан-чіп = ПОТОЧНИЙ стан (current_condition_name),
                        узгоджено з колонкою «Стан» у таблиці. Редагування «Поточного стану»
                        нижче одразу відображається тут. Журнальний «Початковий стан»
                        (condition_name) лишається read-only і не змінюється. */}
                    {((p as any).current_condition_name || (p as any).condition_name) && (
                      <Tag
                        color={getConditionColor((p as any).current_condition_name || (p as any).condition_name)}
                        style={{ margin: 0 }}
                      >
                        {(p as any).current_condition_name || (p as any).condition_name}
                      </Tag>
                    )}
                    {(() => {
                      const total = p.quantity ?? 0;
                      const avail = p.available_qty ?? total;
                      const sold = p.sold_count ?? 0;
                      let label = '', color = '';
                      if (total === 0) { label = '0 в наявності'; color = 'red'; }
                      else if (sold === 0) { label = `${total} в наявності`; color = 'green'; }
                      else if (avail <= 0) { label = `0 / ${total}`; color = 'red'; }
                      else { label = `${avail} / ${total} в наявності`; color = 'orange'; }
                      return <Tag color={color} style={{ margin: 0 }}>{label}</Tag>;
                    })()}
                  </div>

                  {/* Sizes — ховаємо коли розміру нема (напр. сумки), показуємо в edit-режимі */}
                  {(editMode || hasAnySize) && (
                  <div className="mb-3">
                    {(editMode || hasRealSize) && (
                      <div className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2 font-medium">Розмір</div>
                    )}
                    {editMode ? (
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 max-w-md">
                        {([
                          { field: 'sizeeu', label: 'EU' },
                          { field: 'size_letter', label: 'Буквений' },
                          { field: 'measurementscm', label: 'СМ' },
                          { field: 'dimensions', label: 'Габарити' },
                          { field: 'geometric_shape', label: 'Геом. форма' },
                        ] as const)
                          .filter(({ field }) => !hiddenFields.has(field))
                          .map(({ field, label }) => (
                          <div key={field} className="flex flex-col gap-1">
                            <span className="text-[10px] text-gray-400 dark:text-gray-500 font-medium flex items-center gap-1">{label}<LockDot field={field} /></span>
                            <input value={drafts[field] ?? ''} onChange={(e) => setDraft(field, e.target.value)} className={inputCls + ' !py-1 text-center'} />
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {p.sizeeu && (
                          <div className="flex flex-col items-center px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 min-w-[58px]">
                            <span className="text-[10px] text-gray-400 dark:text-gray-500 font-medium">EU</span>
                            <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{p.sizeeu}</span>
                          </div>
                        )}
                        {(p as any).size_letter && (
                          <div className="flex flex-col items-center justify-center px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 min-w-[58px]">
                            <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{(p as any).size_letter}</span>
                          </div>
                        )}
                        {derivedSizes.map(({ label, val }) => (
                          <div key={label} className="flex flex-col items-center px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 min-w-[58px]">
                            <span className="text-[10px] text-gray-400 dark:text-gray-500 font-medium">{label}</span>
                            <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{val}</span>
                          </div>
                        ))}
                        {p.measurementscm && (
                          <div className="flex flex-col items-center px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 min-w-[58px]">
                            <span className="text-[10px] text-gray-400 dark:text-gray-500 font-medium">СМ</span>
                            <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{p.measurementscm}</span>
                          </div>
                        )}
                        {p.dimensions && (
                          <div className="flex flex-col items-center px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 min-w-[58px]">
                            <span className="text-[10px] text-gray-400 dark:text-gray-500 font-medium">Габарити</span>
                            <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{p.dimensions}</span>
                          </div>
                        )}
                        {(p as any).geometric_shape && (
                          <div className="flex flex-col items-center px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 min-w-[58px]">
                            <span className="text-[10px] text-gray-400 dark:text-gray-500 font-medium">Форма</span>
                            <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{(p as any).geometric_shape}</span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  )}

                  {/* Розумне заповнення (1.5): база вже знає цю модель з інших завозів.
                      Кнопка заповнює ЛИШЕ порожні поля; нічого не перезаписує. */}
                  {editMode && modelProfile && modelProfile.records > 0 && (
                    <div className="mt-3 rounded-lg border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-900/20 px-3 py-2 flex items-center gap-3 flex-wrap">
                      <span className="text-xs text-indigo-700 dark:text-indigo-300">
                        База знає цю модель: <b>{modelProfile.records}</b> запис(ів){modelProfile.numbers?.length ? ` (${modelProfile.numbers.slice(0, 4).join(', ')}${modelProfile.numbers.length > 4 ? '…' : ''})` : ''}
                      </span>
                      <button
                        type="button" onClick={applyModelProfile}
                        className="ml-auto px-3 py-1 rounded-md text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-700"
                        title="Заповнити порожні поля найчастішими значеннями цієї моделі"
                      >Заповнити порожні поля</button>
                      {profileNote && <span className="text-xs text-indigo-600 dark:text-indigo-400 w-full">{profileNote}</span>}
                    </div>
                  )}

                  {/* Характеристики — у правій колонці ПОРУЧ із фото (заповнюють висоту) */}
                  <div className="mt-3 border-t border-gray-100 dark:border-gray-800 pt-4">
                    <div className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-3 font-medium">Характеристики</div>
                    <div className={`grid ${charCols} gap-x-6 gap-y-3`}>
                    {classCombo({ nameField: 'brand_name', lockField: 'brandid', label: 'Бренд', options: (filterOpts?.brands ?? []) as any, readValue: formatBrandName((p as any).brand_name) })}
                    {EditCell({ field: 'model', label: 'Модель' })}
                    {EditCell({ field: 'collection', label: 'Колекція' })}
                    {classCombo({ nameField: 'type_name', lockField: 'typeid', label: 'Тип', options: (filterOpts?.types ?? []) as any, readValue: (p as any).type_name })}
                    {classCombo({ nameField: 'subtype_name', lockField: 'subtypeid', label: 'Підтип', options: (subtypeOptions) as any, readValue: (p as any).subtype_name })}
                    {classCombo({ nameField: 'style_name', lockField: 'styleid', label: 'Стиль', options: (filterOpts?.styles ?? []) as any, readValue: (p as any).style_name })}
                    {classSelect({ field: 'genderid', label: 'Стать', options: (filterOpts?.genders ?? []) as any, readValue: (p as any).gender_name })}
                    {EditCell({ field: 'season', label: 'Сезон' })}
                    {EditCell({ field: 'color_name', lockField: 'colorid', label: 'Колір' })}
                    {EditCell({ field: 'width', label: 'Ширина' })}
                    {EditCell({ field: 'current_condition_name', lockField: 'current_conditionid', label: 'Поточний стан' })}
                    {/* «Початковий стан» (журнальний condition_name) показуємо ЛИШЕ коли він
                        відрізняється від поточного — інакше дубль однакового значення поряд
                        виглядає як баг. Журнальне значення лишається read-only. */}
                    {(p as any).condition_name
                      && (p as any).condition_name !== (p as any).current_condition_name && (
                      <RoCell label="Початковий стан" value={(p as any).condition_name} />
                    )}
                    {EditCell({ field: 'marking', label: 'Маркування' })}
                    {EditCell({ field: 'gtin', label: 'GTIN' })}
                    {EditCell({ field: 'year', label: 'Рік', type: 'number' })}
                    {EditCell({ field: 'clonednumbers', label: 'Клони' })}
                    {EditCell({ field: 'manufacturer_country_name', lockField: 'manufacturercountryid', label: 'Виробник' })}
                    <RoCell label="Завоз" value={p.dateadded ? new Date(p.dateadded).toLocaleDateString('uk-UA') : null} />
                    <RoCell label="У базі з" value={p.created_at ? new Date(p.created_at).toLocaleDateString('uk-UA') : null} />
                  </div>

                  {/* Матеріали — ЗАВЖДИ першим підрозділом (згорнуто за замовчуванням).
                      У edit-режимі — інпут на кожну позицію (CSV назв через кому). */}
                  {(editMode || (p.materials && p.materials.length > 0)) && (
                    CollapsibleSection({ id: 'materials', title: 'Матеріали', children: (
                      <div className={`grid ${charCols} gap-x-6 gap-y-3`}>
                        {editMode ? (
                          MATERIAL_POSITIONS
                            .filter(({ pos }) => !hiddenFields.has(`material_${pos}`))
                            .map(({ pos, label }) => (
                            <div key={pos} className="flex flex-col gap-1 min-w-0">
                              <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium">{label}</span>
                              <input
                                type="text"
                                value={materialDrafts[pos] ?? ''}
                                onChange={(e) => setMaterialDrafts((d) => ({ ...d, [pos]: e.target.value }))}
                                placeholder="напр. шкіра, замша"
                                className={inputCls}
                              />
                            </div>
                          ))
                        ) : (
                          (() => {
                            const posLabels: Record<string, string> = {
                              upper: 'Верх', middle: 'Середина', insole: 'Устілка', sole: 'Підошва', membrane: 'Мембрана',
                            };
                            const grouped = new Map<string, string[]>();
                            for (const mat of p.materials!) {
                              const label = posLabels[mat.position] || mat.position;
                              if (!grouped.has(label)) grouped.set(label, []);
                              grouped.get(label)!.push(mat.materialname || String(mat.material_id));
                            }
                            return Array.from(grouped.entries()).map(([pos, names]) => (
                              <RoCell key={pos} label={pos} value={names.join(', ')} />
                            ));
                          })()
                        )}
                      </div>
                    ) })
                  )}

                  {/* Інше — решта характеристик (взуття + усі виміри), єдиним підрозділом без
                      окремих заголовків «Взуття»/«Виміри одягу» (згорнуто за замовчуванням). */}
                  {(editMode || p.sole_type_name || p.toe_shape_name || p.fastening_type_name || p.lining_name ||
                    p.heel_type_name || p.lace_type_name || p.packaging_name || p.technology_name || p.sole_color_name ||
                    p.measurements_height_min != null || p.measurements_sole_thickness_min != null || p.measurements_heel_min != null ||
                    p.measurements_length_min != null || p.measurements_pog_min != null || p.measurements_pob_min != null ||
                    p.measurements_pot_min != null || p.measurements_sleeve_min != null) && (
                    CollapsibleSection({ id: 'other', title: 'Інше', children: (
                      <div className={`grid ${charCols} gap-x-6 gap-y-3`}>
                        {EditCell({ field: 'sole_type_name', lockField: 'soletypeid', label: 'Тип підошви' })}
                        {EditCell({ field: 'sole_color_name', lockField: 'sole_colorid', label: 'Колір підошви' })}
                        {EditCell({ field: 'toe_shape_name', lockField: 'toeshapeid', label: 'Форма носка' })}
                        {EditCell({ field: 'fastening_type_name', lockField: 'fasteningtypeid', label: 'Застібка' })}
                        {EditCell({ field: 'lace_type_name', lockField: 'lacetypeid', label: 'Тип шнурівки' })}
                        {EditCell({ field: 'lining_name', lockField: 'liningid', label: 'Підкладка' })}
                        {EditCell({ field: 'heel_type_name', lockField: 'heeltypeid', label: 'Тип каблука' })}
                        {EditCell({ field: 'technology_name', lockField: 'technologyid', label: 'Технології' })}
                        {EditCell({ field: 'packaging_name', lockField: 'packagingid', label: 'Пакування' })}
                        {MEASUREMENTS
                          .filter(({ name }) => !(editMode && hiddenFields.has(`meas_${name}`)))
                          .map(({ name, label, minKey, maxKey }) => (
                          editMode ? (
                            <div key={name} className="flex flex-col gap-1 min-w-0">
                              <span className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium">{label}</span>
                              <input
                                type="text"
                                value={measurementDrafts[name] ?? ''}
                                onChange={(e) => setMeasurementDrafts((d) => ({ ...d, [name]: e.target.value }))}
                                placeholder="напр. 26 або 25-27"
                                className={inputCls}
                              />
                            </div>
                          ) : (
                            <RoCell key={name} label={label} value={fmtRange((p as any)[minKey], (p as any)[maxKey])} />
                          )
                        ))}
                      </div>
                    ) })
                  )}
                  </div>{/* /Характеристики */}
                </div>{/* /Right panel */}
              </div>{/* /Hero grid */}

              {/* Description */}
              <div className="px-6 pb-4 pt-2 group">
                <div className="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2 font-medium flex items-center gap-2">
                  Опис
                  {!editMode && editingField !== 'description' && (
                    <EditBtn onClick={() => startEdit('description', p.description ?? '')} title="Редагувати опис" />
                  )}
                  <LockBadge field="description" />
                </div>
                {editMode ? (
                  <textarea value={drafts['description'] ?? ''} onChange={(e) => setDraft('description', e.target.value)}
                    placeholder="Опис не вказано"
                    className="w-full px-4 py-3 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 min-h-[80px]" />
                ) : editingField === 'description' ? (
                  <div>
                    <textarea autoFocus value={fieldDraft} onChange={(e) => setFieldDraft(e.target.value)}
                      className="w-full px-4 py-3 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 min-h-[80px]"
                      onKeyDown={(e) => { if (e.key === 'Escape') setEditingField(null); }} />
                    <div className="flex gap-2 mt-1">
                      <button onClick={() => saveField('description')} disabled={savingField}
                        className="text-xs px-3 py-1 rounded bg-green-600 !text-white hover:bg-green-700 disabled:opacity-60">{savingField ? 'Збереження…' : 'Зберегти'}</button>
                      <button onClick={() => setEditingField(null)} disabled={savingField}
                        className="text-xs px-3 py-1 rounded bg-gray-200 dark:bg-gray-700">Скасувати</button>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap leading-relaxed bg-gray-50 dark:bg-gray-800/40 rounded-lg px-4 py-3 first-letter:uppercase">
                    {p.description || <span className="text-gray-300 dark:text-gray-600 italic">опис не вказано</span>}
                  </p>
                )}
              </div>

              {/* Notes — згорнуто за замовчуванням, розкривається кліком */}
              {(() => {
                const notesOpen = editMode || !!openSections['notes'];
                return (
              <div className="px-6 pb-6 group">
                <div className="flex items-center gap-2 mb-2">
                  <button
                    type="button"
                    onClick={editMode ? undefined : () => toggleSection('notes')}
                    className={`flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 font-medium ${editMode ? 'cursor-default' : 'hover:text-gray-600 dark:hover:text-gray-300'} transition-colors`}
                  >
                    {!editMode && (
                      <RightOutlined style={{ fontSize: 9 }} className={`transition-transform duration-200 ${notesOpen ? 'rotate-90' : ''}`} />
                    )}
                    Примітки
                    {!notesOpen && p.extranote && <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />}
                  </button>
                  {notesOpen && !editMode && editingField !== 'extranote' && (
                    <EditBtn onClick={() => startEdit('extranote', p.extranote ?? '')} title="Редагувати примітку" />
                  )}
                  {notesOpen && <LockBadge field="extranote" />}
                </div>
                {!notesOpen ? null : editMode ? (
                  <textarea value={drafts['extranote'] ?? ''} onChange={(e) => setDraft('extranote', e.target.value)}
                    placeholder="Примітку не вказано"
                    className="w-full px-4 py-3 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 min-h-[60px]" />
                ) : editingField === 'extranote' ? (
                  <div>
                    <textarea autoFocus value={fieldDraft} onChange={(e) => setFieldDraft(e.target.value)}
                      className="w-full px-4 py-3 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 min-h-[60px]"
                      onKeyDown={(e) => { if (e.key === 'Escape') setEditingField(null); }} />
                    <div className="flex gap-2 mt-1">
                      <button onClick={() => saveField('extranote')} disabled={savingField}
                        className="text-xs px-3 py-1 rounded bg-green-600 !text-white hover:bg-green-700 disabled:opacity-60">{savingField ? 'Збереження…' : 'Зберегти'}</button>
                      <button onClick={() => setEditingField(null)} disabled={savingField}
                        className="text-xs px-3 py-1 rounded bg-gray-200 dark:bg-gray-700">Скасувати</button>
                    </div>
                  </div>
                ) : (
                  p.extranote ? (
                    <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap leading-relaxed bg-amber-50 dark:bg-amber-900/15 rounded-lg px-4 py-3 border border-amber-100 dark:border-amber-800/30">
                      {p.extranote}
                    </p>
                  ) : (
                    <p className="text-sm text-gray-300 dark:text-gray-600 italic px-4 py-3">примітку не вказано</p>
                  )
                )}
              </div>
                );
              })()}
            </div>
          </div>
        )}
      </div>

      {/* Діалог публікації на Prom (редагування назв/ціни/характеристик перед відправкою) */}
      {promPreview && (
        <PromPublishDialog
          data={promPreview}
          busy={promBusy}
          onCancel={() => { if (!promBusy) setPromPreview(null); }}
          onConfirm={promConfirmPublish}
        />
      )}
    </div>
  );
};

export default ProductDetailsModal;
