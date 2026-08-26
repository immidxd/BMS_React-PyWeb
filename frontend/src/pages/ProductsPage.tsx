import React, { useState, useEffect, useRef } from 'react';
import MainLayout from '../layouts/MainLayout';
import ProductsTable from '../components/products/ProductsTable';
import { productService, type ProductListResponse } from '../services/productService';
import ProductFiltersPanel from '../components/filters/ProductFilters';
import type { ProductFilter as ProductFilterType, ProductFilters as ProductFiltersType } from '../types/product';
import { useLocation, useNavigate } from 'react-router-dom';
import { useEffect as ReactUseEffect } from 'react';
import { Button, Dropdown } from 'antd';
import { toast } from 'react-toastify';
import Pagination from '../components/common/Pagination';
import AddProductModal from '../components/shipments/AddProductModal';
import { PlusOutlined, SendOutlined, CheckSquareOutlined, DownOutlined } from '@ant-design/icons';
import LoadingSpinner from '../components/common/LoadingSpinner';
import { useSelection } from '../services/selectionManager';
import { taskManager } from '../services/taskManager';
import { formatTelegramBatchResult } from '../services/telegramBatchResult';
import { waitForPromImport, type PromImportProgress } from '../services/promImportMonitor';
import {
  markPromImportAccepted, refreshPromLimitWatch, watchPromLimitStatus,
} from '../services/promLimitMonitor';
import { confirmDialog, notify } from '../ui/feedback';
import { useIsActivePage } from '../contexts/ActivePageContext';
import TelegramPublishDialog, {
  type TelegramPreview, type TelegramPublishPayload,
} from '../components/products/TelegramPublishDialog';
import TelegramBatchPublishDialog, {
  type TelegramBatchRequest,
} from '../components/products/TelegramBatchPublishDialog';
import ViberPublishDialog, {
  type ViberPreview, type ViberPublishPayload,
} from '../components/products/ViberPublishDialog';
import ViberBatchPublishDialog, {
  type ViberBatchRequest,
} from '../components/products/ViberBatchPublishDialog';
import InstagramPublishDialog, {
  InstagramMark, type InstagramDraftPayload, type InstagramPreview,
} from '../components/products/InstagramPublishDialog';
import InstagramBatchDraftDialog, { type InstagramBatchRequest } from '../components/products/InstagramBatchDraftDialog';
import FacebookPublishDialog, {
  FacebookMark, type FacebookDraftPayload, type FacebookPreview,
} from '../components/products/FacebookPublishDialog';
import FacebookBatchDraftDialog, { type FacebookBatchRequest } from '../components/products/FacebookBatchDraftDialog';
import CollectionCollageDialog, {
  type CollectionPlatform, type CollectionPublishRequest,
} from '../components/products/CollectionCollageDialog';

// Placeholder for actual filter components for Products

interface ProductsPageProps {
  currentSearchTerm: string;
}

const ProductsPage: React.FC<ProductsPageProps> = ({ currentSearchTerm }) => {
  // Чи ця вкладка зараз на екрані. При keep-alive сторінка лишається змонтованою
  // й на інших вкладках — глобальні гарячі клавіші мусять це враховувати.
  const isActivePage = useIsActivePage();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [showAddProduct, setShowAddProduct] = useState(false);
  const [loading, setLoading] = useState<boolean>(true);
  const [products, setProducts] = useState<ProductListResponse>({ items: [], total: 0, page: 1, per_page: 20, pages: 1 });
  const [page, setPage] = useState<number>(1);
  const [perPage, setPerPage] = useState<number>(20);
  const [filtersMeta, setFiltersMeta] = useState<ProductFiltersType | null>(null);
  const [selectedFilters, setSelectedFilters] = useState<ProductFilterType>({});
  const navigate = useNavigate();
  const location = useLocation();
  const [onlyUnsold, setOnlyUnsold] = useState<boolean>(true);
  const [onlyProblematic, setOnlyProblematic] = useState<boolean>(false);
  const [onlyRostovka, setOnlyRostovka] = useState<boolean>(false);
  // «Тільки з фото»: власне фото за номером АБО підтягнуте з товару-донора —
  // та сама умова, що дає іконку 📷 у таблиці (has_photo).
  const [onlyWithPhoto, setOnlyWithPhoto] = useState<boolean>(false);
  const [selectedShipmentId, setSelectedShipmentId] = useState<number | undefined>(undefined);
  const [visibleOnly, setVisibleOnly] = useState<boolean>(false);
  const [sortBy, setSortBy] = useState<string>('delivery_date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  // Виділення — ЄДИНИЙ глобальний буфер (selectionManager, поза React): переживає
  // відкриття/закриття картки й перемикання вкладок; скидається лише за дією користувача.
  const selection = useSelection();
  const [selectionMode, setSelectionMode] = useState<boolean>(false);
  const abortRef = useRef<AbortController | null>(null);
  const fetchIdRef = useRef(0);
  // Динамічні фасети (розміри + кольори), наявні в поточному відфільтрованому
  // наборі. null = ще не завантажено (панель тоді бере глобальний список).
  const [availableEuSizes, setAvailableEuSizes] = useState<string[] | null>(null);
  const [availableColorGroups, setAvailableColorGroups] = useState<{ id: number; count: number }[] | null>(null);
  const facetsAbortRef = useRef<AbortController | null>(null);
  const facetsFetchIdRef = useRef(0);
  const [telegramPreview, setTelegramPreview] = useState<TelegramPreview | null>(null);
  const [telegramBatchIds, setTelegramBatchIds] = useState<number[] | null>(null);
  const [telegramBusy, setTelegramBusy] = useState(false);
  const [viberPreview, setViberPreview] = useState<ViberPreview | null>(null);
  const [viberBatchIds, setViberBatchIds] = useState<number[] | null>(null);
  const [viberBusy, setViberBusy] = useState(false);
  const [instagramPreview, setInstagramPreview] = useState<InstagramPreview | null>(null);
  const [instagramBatchIds, setInstagramBatchIds] = useState<number[] | null>(null);
  const [instagramBusy, setInstagramBusy] = useState(false);
  const [facebookPreview, setFacebookPreview] = useState<FacebookPreview | null>(null);
  const [facebookBatchIds, setFacebookBatchIds] = useState<number[] | null>(null);
  const [facebookBusy, setFacebookBusy] = useState(false);
  const [collectionRequest, setCollectionRequest] = useState<{ platform: CollectionPlatform; ids: number[] } | null>(null);
  const [collectionBusy, setCollectionBusy] = useState(false);
            
  // Пошук по списку запускає основний effect нижче. Окреме preview живе
  // у SearchBar; раніше тут дублювався той самий важкий global-search із limit=0,
  // який backend коректно відхиляв (limit має бути >=1).
  useEffect(() => { setPage(1); }, [currentSearchTerm]);

  const fetchProducts = async () => {
    // Cancel any in-flight request to prevent stale responses from overwriting fresh ones
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const myFetchId = ++fetchIdRef.current;

    setLoading(true);
    try {
      const params: Record<string, any> = {
        page,
        per_page: perPage,
        sort_by: sortBy,
        sort_dir: sortDir,
        search: currentSearchTerm && currentSearchTerm.trim() ? currentSearchTerm.trim() : undefined,
        only_unsold: onlyUnsold || undefined,
        only_problematic: onlyProblematic || undefined,
        only_rostovka: onlyRostovka || undefined,
        only_with_photo: onlyWithPhoto || undefined,
        shipment_id: selectedShipmentId,
        is_visible: visibleOnly ? true : (selectedFilters.is_visible || undefined),
        min_price: selectedFilters.min_price,
        max_price: selectedFilters.max_price,
      };
      // Append multi-id arrays as repeated query params
      const appendIds = (key: string, ids?: number[]) => { if (ids && ids.length > 0) params[key] = ids; };
      appendIds('typeids', selectedFilters.typeids);
      appendIds('subtypeids', selectedFilters.subtypeids);
      appendIds('brandids', selectedFilters.brandids);
      appendIds('genderids', selectedFilters.genderids);
      appendIds('colorids', selectedFilters.colorids);
      appendIds('color_group_ids', selectedFilters.color_group_ids);
      appendIds('statusids', selectedFilters.statusids);
      appendIds('conditionids', selectedFilters.conditionids);
      // Нові фільтри: сезон, стиль, поточний стан, ширина
      appendIds('styleids', (selectedFilters as any).styleids);
      appendIds('current_conditionids', (selectedFilters as any).current_conditionids);
      if ((selectedFilters as any).seasons && (selectedFilters as any).seasons.length > 0) {
        params['seasons'] = (selectedFilters as any).seasons;
      }
      if ((selectedFilters as any).widths && (selectedFilters as any).widths.length > 0) {
        params['widths'] = (selectedFilters as any).widths;
      }
      if ((selectedFilters as any).published_on && (selectedFilters as any).published_on.length > 0) {
        params['published_on'] = (selectedFilters as any).published_on;
      }
      if ((selectedFilters as any).published_on_not && (selectedFilters as any).published_on_not.length > 0) {
        params['published_on_not'] = (selectedFilters as any).published_on_not;
      }
      if (selectedFilters.sizeeu && selectedFilters.sizeeu.length > 0) params['sizeeu'] = selectedFilters.sizeeu;
      if (selectedFilters.min_sizeeu !== undefined) params['min_sizeeu'] = selectedFilters.min_sizeeu;
      if (selectedFilters.max_sizeeu !== undefined) params['max_sizeeu'] = selectedFilters.max_sizeeu;
      if ((selectedFilters as any).size_letter && (selectedFilters as any).size_letter.length > 0) params['size_letter'] = (selectedFilters as any).size_letter;
      if (selectedFilters.min_measurementscm !== undefined) params['min_measurementscm'] = selectedFilters.min_measurementscm;
      if (selectedFilters.max_measurementscm !== undefined) params['max_measurementscm'] = selectedFilters.max_measurementscm;
      const res = await productService.getProducts(params, controller.signal);
      // Only apply result if this is still the latest request
      if (myFetchId === fetchIdRef.current) {
        setProducts(res);
      }
    } catch (err: any) {
      if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') return; // aborted — ignore
      throw err;
    } finally {
      if (myFetchId === fetchIdRef.current) setLoading(false);
    }
  };

  // Фасети розмірів+кольорів: ті самі фільтри, що й для товарів, БЕЗ пагінації/
  // сорту (свій фільтр кожен фасет ігнорує на бекенді — щоб показувати всі
  // досяжні за іншими фільтрами значення).
  const fetchAvailableFacets = async () => {
    if (facetsAbortRef.current) facetsAbortRef.current.abort();
    const controller = new AbortController();
    facetsAbortRef.current = controller;
    const myFetchId = ++facetsFetchIdRef.current;
    const params: Record<string, any> = {
      search: currentSearchTerm && currentSearchTerm.trim() ? currentSearchTerm.trim() : undefined,
      only_unsold: onlyUnsold || undefined,
      only_problematic: onlyProblematic || undefined,
      only_rostovka: onlyRostovka || undefined,
      only_with_photo: onlyWithPhoto || undefined,
      shipment_id: selectedShipmentId,
      is_visible: visibleOnly ? true : (selectedFilters.is_visible || undefined),
      min_price: selectedFilters.min_price,
      max_price: selectedFilters.max_price,
    };
    const appendIds = (key: string, ids?: number[]) => { if (ids && ids.length > 0) params[key] = ids; };
    appendIds('typeids', selectedFilters.typeids);
    appendIds('subtypeids', selectedFilters.subtypeids);
    appendIds('brandids', selectedFilters.brandids);
    appendIds('genderids', selectedFilters.genderids);
    appendIds('colorids', selectedFilters.colorids);
    appendIds('color_group_ids', selectedFilters.color_group_ids);
    appendIds('statusids', selectedFilters.statusids);
    appendIds('conditionids', selectedFilters.conditionids);
    appendIds('styleids', (selectedFilters as any).styleids);
    appendIds('current_conditionids', (selectedFilters as any).current_conditionids);
    if ((selectedFilters as any).seasons?.length > 0) params['seasons'] = (selectedFilters as any).seasons;
    if ((selectedFilters as any).widths?.length > 0) params['widths'] = (selectedFilters as any).widths;
    if ((selectedFilters as any).published_on?.length > 0) params['published_on'] = (selectedFilters as any).published_on;
    if ((selectedFilters as any).published_on_not?.length > 0) params['published_on_not'] = (selectedFilters as any).published_on_not;
    if ((selectedFilters as any).size_letter?.length > 0) params['size_letter'] = (selectedFilters as any).size_letter;
    if (selectedFilters.min_measurementscm !== undefined) params['min_measurementscm'] = selectedFilters.min_measurementscm;
    if (selectedFilters.max_measurementscm !== undefined) params['max_measurementscm'] = selectedFilters.max_measurementscm;
    try {
      const facets = await productService.getAvailableFacets(params, controller.signal);
      if (myFetchId === facetsFetchIdRef.current) {
        setAvailableEuSizes(facets.eu);
        setAvailableColorGroups(facets.colorGroups);
      }
    } catch (err: any) {
      if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') return;
      // м'яка деградація: лишаємо попередні списки
    }
  };

  const handleRefresh = () => { setIsRefreshing(true); fetchProducts().finally(() => setIsRefreshing(false)); };

  // Окрема read-only задача: лише спостерігає за вже відправленим імпортом.
  // Вона не запускає повторну публікацію і не втручається в чергу/фід.
  const monitorPromCompletion = (
    submission: any,
    submittedProducts: number,
    onConfirmed?: (skus: string[]) => void,
  ) => {
    const skus: string[] = Array.from(new Set<string>(
      (Array.isArray(submission?.skus) ? submission.skus : [])
        .map((sku: unknown) => String(sku).trim())
        .filter((sku: string) => Boolean(sku)),
    ));
    const hasVisibleSkus = Array.isArray(submission?.visible_skus);
    const monitorSkus: string[] = hasVisibleSkus
      ? Array.from(new Set<string>(
          submission.visible_skus
            .map((sku: unknown) => String(sku).trim())
            .filter((sku: string) => Boolean(sku)),
        ))
      : skus;
    const expectedPositions = skus.length;
    const knownUnavailable = hasVisibleSkus
      ? Math.max(0, expectedPositions - monitorSkus.length)
      : 0;
    taskManager.run(
      `Очікування завершення імпорту на Prom (${expectedPositions || submittedProducts})`,
      () => waitForPromImport({ importId: submission?.import_id, skus: monitorSkus }),
      {
        silentSuccess: true,
        errorMsg: 'Контроль завершення імпорту Prom',
        onSuccess: (progress: PromImportProgress) => {
          markPromImportAccepted();
          const positions = expectedPositions || progress.found || progress.expected || 0;
          const productHint = positions && positions !== submittedProducts
            ? ` (${submittedProducts} товар(ів) BMS)`
            : '';
          const unavailable = knownUnavailable || progress.presence?.not_available || 0;
          const availabilityHint = unavailable
            ? ` ${unavailable} позицій завантажено як «немає в наявності».`
            : '';
          notify.success({
            message: '✓ Імпорт на Prom завершено',
            description: positions
              ? `Prom підтвердив успішне завантаження ${positions} позицій${productHint}.${availabilityHint}`
              : `Prom підтвердив успішне завершення імпорту.${availabilityHint}`,
            duration: 9,
          });
          window.dispatchEvent(new CustomEvent('bms:prom-status-refresh'));
          onConfirmed?.(monitorSkus);
        },
      },
    ).catch(() => { /* terminal PARTIAL/FATAL or timeout is shown by taskManager */ });
  };

  // Масова публікація на Prom: N товарів → ОДИН import_file на бекенді (економить
  // доступні запуски імпорту). Фонова задача (taskManager) — не блокує UI; чіпи всіх
  // товарів стають 'pending' одразу (queue-early на бекенді), решта з'явиться за 1-3 хв.
  const sendSelectedToProm = () => {
    const ids = selection.ids.slice();
    if (ids.length === 0) return;
    const n = ids.length;
    selection.clear();
    setSelectionMode(false);
    // Проактивний запобіжник: якщо Prom нещодавно відхиляв імпорт — попереджаємо
    // ОДРАЗУ (не блокуючи публікацію: батч = 1 імпорт, найімовірніше пройде).
    fetch('/api/publications/prom/import-limit')
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return;
        watchPromLimitStatus(d);
        if (d.limit_warning) notify.warning({ message: d.limit_warning, duration: 8 });
      })
      .catch(() => { /* тихо */ });
    taskManager.run(
      `Публікація ${n} товар(ів) на Prom`,
      async () => {
        const res = await fetch('/api/publications/prom/export-products-batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_ids: ids }),
        });
        const r = await res.json();
        if (!res.ok) {
          const err: any = new Error(r.detail || `HTTP ${res.status}`);
          err.response = { data: { detail: r.detail } };
          throw err;
        }
        return r;
      },
      {
        silentSuccess: true,
        errorMsg: `Публікація ${n} товар(ів) на Prom`,
        onSuccess: (r: any) => {
          if (r?.import_id) markPromImportAccepted();
          notify.success({ message: r.note || 'Публікація в черзі на Prom.', duration: 7 });
          window.dispatchEvent(new CustomEvent('bms:prom-status-refresh'));
          monitorPromCompletion(r, n);
        },
      },
    ).catch(() => {
      // Якщо Prom щойно відхилив імпорт, бекенд уже записав час — запускаємо
      // лише локальне нагадування, не повторюючи саму публікацію.
      void refreshPromLimitWatch();
    }).finally(() => { fetchProducts(); });
  };

  // Масова Shafa-дія: вже наявні Prom-товари лише позначаються готовими до
  // глобального мосту; відсутні збираються в ОДИН існуючий Prom import_file.
  // Ніякого приватного/недокументованого Shafa API.
  // Пакетна публікація на OLX: кожен товар → окреме оголошення. OLX бере плату
  // за публікацію з активного пакета — попереджаємо й рахуємо, скільки чекає пакета.
  const sendSelectedToOlx = async () => {
    const ids = selection.ids.slice();
    if (!ids.length) return;
    try {
      const sr = await fetch('/api/publications/olx/status');
      const status = await sr.json();
      if (!sr.ok) throw new Error(status.detail || `HTTP ${sr.status}`);
      if (!status.authorized) {
        notify.error({ message: 'OLX не авторизовано. Пройдіть авторизацію в розділі «Публікації».', duration: 8 });
        return;
      }
    } catch (e: any) {
      notify.error({ message: `OLX: ${e.message || 'Не вдалося перевірити статус'}` });
      return;
    }
    const n = ids.length;
    const confirmed = await confirmDialog({
      title: `Опублікувати ${n} товар(ів) на OLX?`,
      body: 'OLX бере плату за публікацію з активного пакета (≈25–32 грн/оголошення). '
        + 'Товари без активного пакета створяться, але не стануть видимими, доки пакет не активовано. '
        + 'Ціна кожного рахується індивідуально (пакет + реклама + комісія OLX Доставки).',
      okText: 'Опублікувати', kind: 'warning',
    });
    if (!confirmed) return;
    selection.clear();
    setSelectionMode(false);
    taskManager.run(
      `Публікація ${n} товар(ів) на OLX`,
      async () => {
        const res = await fetch('/api/publications/olx/create-batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_ids: ids }),
        });
        const r = await res.json();
        if (!res.ok) {
          const err: any = new Error(r.detail || `HTTP ${res.status}`);
          err.response = { data: { detail: r.detail } };
          throw err;
        }
        return r;
      },
      {
        silentSuccess: true,
        errorMsg: `Публікація ${n} товар(ів) на OLX`,
        onSuccess: (r: any) => {
          notify.success({ message: r.note || 'Опубліковано на OLX.', duration: 9 });
          if (r?.needs_package) {
            notify.warning({
              message: `${r.needs_package} товар(ів) чекають активації пакета публікацій OLX у кабінеті.`,
              duration: 11,
            });
          }
          window.dispatchEvent(new CustomEvent('bms:olx-status-refresh'));
        },
      },
    );
  };

  const sendSelectedToShafa = async () => {
    const ids = selection.ids.slice();
    if (!ids.length) return;
    try {
      const sr = await fetch('/api/publications/shafa/status');
      const status = await sr.json();
      if (!sr.ok) throw new Error(status.detail || `HTTP ${sr.status}`);
      if (!status.bridge_enabled) {
        const confirmed = await confirmDialog({
          title: 'Чи вже увімкнено міст Prom→Shafa?',
          body: 'Перед пакетною дією відкрий у Prom: Маркет → Всі додатки → «Експорт товарів на Shafa.ua», введи телефон Shafa та код. Підтверджуй лише після фактичного ввімкнення.',
          okText: 'Так, увімкнено', kind: 'warning',
        });
        if (!confirmed) return;
        const cr = await fetch('/api/publications/shafa/config', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ bridge_enabled: true }),
        });
        const cd = await cr.json();
        if (!cr.ok) throw new Error(cd.detail || `HTTP ${cr.status}`);
      }
    } catch (e: any) {
      notify.error({ message: `Shafa: ${e.message || 'Не вдалося перевірити міст'}` });
      return;
    }

    const n = ids.length;
    selection.clear();
    setSelectionMode(false);
    taskManager.run(
      `Підготовка ${n} товар(ів) для Shafa`,
      async () => {
        const res = await fetch('/api/publications/shafa/prepare-products-batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_ids: ids }),
        });
        const r = await res.json();
        if (!res.ok) {
          const err: any = new Error(r.detail || `HTTP ${res.status}`);
          err.response = { data: { detail: r.detail } };
          throw err;
        }
        return r;
      },
      {
        silentSuccess: true,
        errorMsg: `Підготовка ${n} товар(ів) для Shafa`,
        onSuccess: (r: any) => {
          if (r?.import_id) markPromImportAccepted();
          notify.success({ message: r.note || 'Товари передано мосту Prom→Shafa.', duration: 8 });
          if (r.limit_warning) notify.warning({ message: r.limit_warning, duration: 10 });
          notify.warning({
            message: 'Замовлення Shafa обробляються у кабінеті Shafa; BMS не має доступу до них через API.',
            duration: 8,
          });
          window.dispatchEvent(new CustomEvent('bms:shafa-status-refresh'));
          if (r?.import_id || (Array.isArray(r?.skus) && r.skus.length)) {
            monitorPromCompletion(r, r.waiting_prom || n, (confirmedSkus) => {
              void (async () => {
                try {
                  const fr = await fetch('/api/publications/shafa/finalize-products-batch', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ product_ids: ids, skus: confirmedSkus }),
                  });
                  const fd = await fr.json();
                  if (!fr.ok) throw new Error(fd.detail || `HTTP ${fr.status}`);
                  notify.success({
                    message: '✓ Пакет Prom підтверджено для Shafa',
                    description: fd.note || 'Глобальний міст Shafa синхронізує товари автоматично.',
                    duration: 9,
                  });
                  window.dispatchEvent(new CustomEvent('bms:shafa-status-refresh'));
                  fetchProducts();
                } catch (e: any) {
                  notify.warning({ message: `Prom підтвердив пакет, але BMS ще синхронізує Shafa-стан: ${e.message || e}`, duration: 8 });
                }
              })();
            });
          }
        },
      },
    ).catch(() => { void refreshPromLimitWatch(); }).finally(() => { fetchProducts(); });
  };

  const openSelectedTelegram = async () => {
    const ids = selection.ids.slice();
    if (!ids.length) return;
    if (ids.length > 1) {
      setTelegramBatchIds(ids);
      return;
    }
    setTelegramBusy(true);
    try {
      const res = await fetch('/api/publications/telegram/preview-post', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: ids[0] }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Не вдалося підготувати Telegram-пост');
      setTelegramPreview(data);
    } catch (e: any) {
      notify.error({ message: `Telegram: ${e.message || 'Не вдалося підготувати пост'}`, duration: 8 });
    } finally {
      setTelegramBusy(false);
    }
  };

  const publishSingleTelegram = (payload: TelegramPublishPayload) => {
    if (!telegramPreview) return;
    const pid = telegramPreview.product_id;
    const pnum = telegramPreview.productnumber;
    setTelegramBusy(true);
    taskManager.run(
      `Telegram-публікація #${pnum}`,
      async () => {
        const res = await fetch('/api/publications/telegram/create-post', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_id: pid, ...payload }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          const err: any = new Error(data.detail || data.error || 'Публікація не вдалася');
          err.response = { data: { detail: data.detail || data.error } };
          throw err;
        }
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => result.failed?.length
          ? {
              status: 'partial',
              detail: result.failed.map((f: any) =>
                `${f.thread_title || f.channel || 'напрямок'}: ${f.error || 'не вдалося'}`,
              ).join(' · '),
            }
          : { status: 'success' },
        onSuccess: (result: any) => {
          if (result.failed?.length) {
            notify.warning({
              message: `#${pnum}: оригінал опубліковано, але ${result.failed.length} напрямків не вдалося`,
              description: result.failed.map((f: any) => f.thread_title || f.channel || f.error).join(' · '),
              duration: 10,
            });
          } else {
            notify.success({ message: `#${pnum} опубліковано в Telegram`, duration: 6 });
          }
          setTelegramPreview(null);
          selection.clear();
          setSelectionMode(false);
          window.dispatchEvent(new CustomEvent('bms:telegram-status-refresh'));
        },
      },
    ).catch(() => undefined).finally(() => {
      setTelegramBusy(false);
      window.dispatchEvent(new CustomEvent('bms:telegram-status-refresh'));
    });
  };

  const publishTelegramBatch = (request: TelegramBatchRequest) => {
    const n = request.items.length;
    setTelegramBusy(true);
    taskManager.run(
      `Пакетна Telegram-публікація: ${n} постів`,
      async () => {
        const res = await fetch('/api/publications/telegram/create-posts-batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(request),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          const err: any = new Error(data.detail || data.error || 'Пакетна публікація не вдалася');
          err.response = { data: { detail: data.detail || data.error } };
          throw err;
        }
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => {
          return {
            status: result.status === 'success' ? 'success' : 'partial',
            detail: formatTelegramBatchResult(result),
          };
        },
        onSuccess: (result: any) => {
          const c = result.counts || {};
          const detail = `${c.success || 0} успішно${c.partial ? ` · ${c.partial} частково` : ''}${c.error ? ` · ${c.error} з помилкою` : ''}${c.skipped ? ` · ${c.skipped} не надсилали` : ''}`;
          if (result.status === 'success') notify.success({ message: 'Пакет Telegram опубліковано', description: detail, duration: 7 });
          else notify.warning({ message: 'Пакет Telegram виконано частково', description: `${detail}. Подробиці збережено у Сповіщеннях.`, duration: 11 });
          setTelegramBatchIds(null);
          selection.clear();
          setSelectionMode(false);
          window.dispatchEvent(new CustomEvent('bms:telegram-status-refresh'));
        },
      },
    ).catch(() => undefined).finally(() => {
      setTelegramBusy(false);
      window.dispatchEvent(new CustomEvent('bms:telegram-status-refresh'));
    });
  };

  const openSelectedViber = async () => {
    const ids = selection.ids.slice();
    if (!ids.length) return;
    if (ids.length > 1) {
      setViberBatchIds(ids);
      return;
    }
    setViberBusy(true);
    try {
      const response = await fetch('/api/publications/viber/preview-post', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: ids[0] }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Не вдалося підготувати Viber-пост');
      setViberPreview(data);
    } catch (error: any) {
      notify.error({ message: `Viber: ${error.message || 'Не вдалося підготувати пост'}`, duration: 8 });
    } finally {
      setViberBusy(false);
    }
  };

  const publishSingleViber = (payload: ViberPublishPayload) => {
    if (!viberPreview) return;
    const productId = viberPreview.product_id;
    const productNumber = viberPreview.productnumber;
    setViberBusy(true);
    taskManager.run(
      `Viber-публікація #${productNumber}`,
      async () => {
        const response = await fetch('/api/publications/viber/create-post', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_id: productId, ...payload }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          const error: any = new Error(data.detail || data.error || 'Viber-публікація не вдалася');
          error.response = { data: { detail: data.detail || data.error } };
          throw error;
        }
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => ({
          status: 'success',
          detail: result.status === 'scheduled'
            ? `Заплановано на ${new Date(result.scheduled_at).toLocaleString('uk-UA')}`
            : result.status === 'published' ? 'Опубліковано у Viber' : 'Прийнято у захищену чергу',
        }),
        onSuccess: (result: any) => {
          notify.success({
            message: result.status === 'scheduled' ? `#${productNumber} заплановано у Viber` : `#${productNumber} передано у Viber`,
            description: result.status === 'scheduled' && result.scheduled_at ? new Date(result.scheduled_at).toLocaleString('uk-UA') : undefined,
            duration: 7,
          });
          setViberPreview(null);
          selection.clear();
          setSelectionMode(false);
          window.dispatchEvent(new CustomEvent('bms:viber-status-refresh'));
        },
      },
    ).catch(() => undefined).finally(() => {
      setViberBusy(false);
      window.dispatchEvent(new CustomEvent('bms:viber-status-refresh'));
    });
  };

  const publishViberBatch = (request: ViberBatchRequest) => {
    const count = request.items.length;
    setViberBusy(true);
    taskManager.run(
      `Пакетна Viber-публікація: ${count} постів`,
      async () => {
        const response = await fetch('/api/publications/viber/create-posts-batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(request),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          const error: any = new Error(data.detail || data.error || 'Пакетна Viber-публікація не вдалася');
          error.response = { data: { detail: data.detail || data.error } };
          throw error;
        }
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => {
          const counts = result.counts || {};
          const issues = (result.results || []).filter((item: any) => item.error)
            .map((item: any) => `#${item.productnumber}: ${item.error}`).join(' · ');
          return {
            status: result.status === 'success' ? 'success' : 'partial',
            detail: [`${counts.success || 0} прийнято`, counts.error ? `${counts.error} з помилкою` : '', issues].filter(Boolean).join(' · '),
          };
        },
        onSuccess: (result: any) => {
          const counts = result.counts || {};
          if (result.status === 'success') notify.success({ message: 'Пакет Viber прийнято', description: `${counts.success || 0} постів у захищеній черзі`, duration: 7 });
          else notify.warning({ message: 'Пакет Viber виконано частково', description: `${counts.success || 0} прийнято · ${counts.error || 0} з помилкою. Подробиці є у Сповіщеннях.`, duration: 11 });
          setViberBatchIds(null);
          selection.clear();
          setSelectionMode(false);
          window.dispatchEvent(new CustomEvent('bms:viber-status-refresh'));
        },
      },
    ).catch(() => undefined).finally(() => {
      setViberBusy(false);
      window.dispatchEvent(new CustomEvent('bms:viber-status-refresh'));
    });
  };

  const openSelectedCollection = (platform: CollectionPlatform) => {
    const ids = selection.ids.slice();
    if (ids.length < 2) {
      notify.warning({ message: 'Для підбірки виділіть щонайменше два товари', duration: 6 });
      return;
    }
    setCollectionRequest({ platform, ids });
  };

  const publishCollection = (request: CollectionPublishRequest, itemCount: number) => {
    const label = request.platform === 'viber' ? 'Viber' : 'Facebook';
    setCollectionBusy(true);
    taskManager.run(
      `Підбірка у ${label}: ${itemCount} товарів`,
      async () => {
        const response = await fetch(`/api/publications/${request.platform}/create-collection`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(request),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          const error: any = new Error(data.detail || data.error || `Підбірку у ${label} не опубліковано`);
          error.response = { data: { detail: data.detail || data.error } };
          throw error;
        }
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => ({
          status: 'success',
          detail: result.scheduled_at
            ? `Заплановано на ${new Date(result.scheduled_at).toLocaleString('uk-UA')}`
            : `Прийнято у захищену чергу · ${(result.product_numbers || []).map((value: string) => `#${value}`).join(' ')}`,
        }),
        onSuccess: (result: any) => {
          notify.success({
            message: result.scheduled_at ? `Підбірку заплановано у ${label}` : `Підбірку передано у ${label}`,
            description: result.scheduled_at
              ? new Date(result.scheduled_at).toLocaleString('uk-UA')
              : `${itemCount} товарів одним банером. Статуси товарів не змінилися.`,
            duration: 8,
          });
          setCollectionRequest(null);
          selection.clear();
          setSelectionMode(false);
          window.dispatchEvent(new CustomEvent(
            request.platform === 'viber' ? 'bms:viber-status-refresh' : 'bms:facebook-status-refresh',
          ));
        },
      },
    ).catch(() => undefined).finally(() => setCollectionBusy(false));
  };

  const openSelectedInstagram = async () => {
    const ids = selection.ids.slice();
    if (!ids.length || instagramBusy) return;
    if (ids.length > 1) {
      setInstagramBatchIds(ids);
      return;
    }
    setInstagramBusy(true);
    try {
      const response = await fetch('/api/publications/instagram/preview-post', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: ids[0] }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Не вдалося підготувати Instagram-чернетку');
      setInstagramPreview(data);
    } catch (error: any) {
      notify.error({ message: `Instagram: ${error.message || 'Не вдалося підготувати чернетку'}`, duration: 8 });
    } finally {
      setInstagramBusy(false);
    }
  };

  const publishSingleInstagram = async (payload: InstagramDraftPayload) => {
    if (!instagramPreview || instagramBusy) return;
    const when = payload.publish_at ? `за розкладом на ${new Date(payload.publish_at).toLocaleString('uk-UA')}` : 'зараз';
    const mirrored = payload.also_facebook === true;
    const approved = await confirmDialog({
      title: payload.publish_at ? 'Запланувати Instagram-публікацію?' : 'Опублікувати в Instagram зараз?',
      body: `#${instagramPreview.productnumber} · ${payload.publish_type === 'feed' ? 'пост/карусель' : payload.publish_type === 'story' ? 'Story' : 'Reel'}\nАкаунт: ${instagramPreview.connection.account}\nЧас: ${when}${mirrored ? '\nІ дзеркально у Facebook — двома окремими публікаціями' : ''}`,
      okText: payload.publish_at ? 'Запланувати' : 'Опублікувати', kind: 'warning',
    });
    if (!approved) return;
    setInstagramBusy(true);
    taskManager.run(
      `Instagram #${instagramPreview.productnumber}`,
      async () => {
        const response = await fetch('/api/publications/instagram/create-post', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Instagram-публікація не вдалася');
        return result;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => ({
          status: mirrored && result.facebook && !result.facebook.ok ? 'partial' : 'success',
          detail: [
            result.scheduled_at ? `Заплановано: ${new Date(result.scheduled_at).toLocaleString('uk-UA')}` : 'Передано у захищену чергу',
            mirrored ? (result.facebook?.ok ? 'Facebook: прийнято' : `Facebook: ${result.facebook?.error || 'не вдалося'}`) : '',
          ].filter(Boolean).join(' · '),
        }),
        onSuccess: (result: any) => {
          // Instagram уже прийнято — про зрив дзеркала кажемо окремо, інакше
          // помилка Facebook читалася б як «нічого не опубліковано».
          if (mirrored && result.facebook && !result.facebook.ok) {
            notify.warning({
              message: 'В Instagram опубліковано, у Facebook — ні',
              description: result.facebook.error || 'Дзеркальну публікацію не прийнято. Подробиці є у Сповіщеннях.',
              duration: 12,
            });
          } else {
            notify.success({
              message: result.scheduled_at ? 'Instagram-публікацію заплановано' : 'Instagram-публікацію передано в чергу',
              description: mirrored ? 'Дзеркальну публікацію у Facebook теж прийнято.' : undefined,
              duration: 7,
            });
          }
          setInstagramPreview(null); selection.clear(); setSelectionMode(false);
          if (mirrored) window.dispatchEvent(new CustomEvent('bms:facebook-status-refresh'));
        },
      },
    ).catch(() => undefined).finally(() => setInstagramBusy(false));
  };

  const publishInstagramBatch = async (request: InstagramBatchRequest) => {
    if (instagramBusy) return;
    const approved = await confirmDialog({
      title: 'Передати пакет в Instagram?',
      body: `${request.items.length} окремих публікацій буде передано у захищену чергу. Кожна картка збере власні медіа й текст.`,
      okText: 'Передати пакет', kind: 'warning',
    });
    if (!approved) return;
    setInstagramBusy(true);
    taskManager.run(
      `Пакет Instagram: ${request.items.length} публікацій`,
      async () => {
        const response = await fetch('/api/publications/instagram/create-posts-batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Instagram-пакет не виконано');
        return result;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => ({ status: result.status === 'success' ? 'success' : 'partial', detail: `${result.counts?.success || 0} прийнято · ${result.counts?.error || 0} помилок` }),
        onSuccess: (result: any) => {
          const counts = result.counts || {};
          if (result.status === 'success') notify.success({ message: 'Instagram-пакет прийнято', description: `${counts.success || 0} публікацій`, duration: 7 });
          else notify.warning({ message: 'Instagram-пакет прийнято частково', description: `${counts.success || 0} прийнято · ${counts.error || 0} помилок`, duration: 10 });
          setInstagramBatchIds(null); selection.clear(); setSelectionMode(false);
        },
      },
    ).catch(() => undefined).finally(() => setInstagramBusy(false));
  };

  const openSelectedFacebook = async () => {
    const ids = selection.ids.slice();
    if (!ids.length || facebookBusy) return;
    if (ids.length > 1) {
      setFacebookBatchIds(ids);
      return;
    }
    setFacebookBusy(true);
    try {
      const response = await fetch('/api/publications/facebook/preview-post', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: ids[0] }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Не вдалося підготувати Facebook-чернетку');
      setFacebookPreview(data);
    } catch (error: any) {
      notify.error({ message: `Facebook: ${error.message || 'Не вдалося підготувати чернетку'}`, duration: 8 });
    } finally {
      setFacebookBusy(false);
    }
  };

  const publishSingleFacebook = async (payload: FacebookDraftPayload) => {
    if (!facebookPreview || facebookBusy) return;
    const when = payload.publish_at ? `за розкладом на ${new Date(payload.publish_at).toLocaleString('uk-UA')}` : 'зараз';
    const mirrored = payload.also_instagram === true;
    const approved = await confirmDialog({
      title: payload.publish_at ? 'Запланувати Facebook-публікацію?' : 'Опублікувати у Facebook зараз?',
      body: `#${facebookPreview.productnumber} · ${payload.publish_type === 'feed' ? 'пост/альбом' : payload.publish_type === 'story' ? 'Story' : 'Reel'}\nСторінка: ${facebookPreview.connection.account}\nЧас: ${when}${mirrored ? '\nІ дзеркально в Instagram — двома окремими публікаціями' : ''}`,
      okText: payload.publish_at ? 'Запланувати' : 'Опублікувати', kind: 'warning',
    });
    if (!approved) return;
    setFacebookBusy(true);
    taskManager.run(
      `Facebook #${facebookPreview.productnumber}`,
      async () => {
        const response = await fetch('/api/publications/facebook/create-post', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Facebook-публікація не вдалася');
        return result;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => ({
          status: mirrored && result.instagram && !result.instagram.ok ? 'partial' : 'success',
          detail: [
            result.scheduled_at ? `Заплановано: ${new Date(result.scheduled_at).toLocaleString('uk-UA')}` : 'Передано у захищену чергу',
            mirrored ? (result.instagram?.ok ? 'Instagram: прийнято' : `Instagram: ${result.instagram?.error || 'не вдалося'}`) : '',
          ].filter(Boolean).join(' · '),
        }),
        onSuccess: (result: any) => {
          if (mirrored && result.instagram && !result.instagram.ok) {
            notify.warning({
              message: 'У Facebook опубліковано, в Instagram — ні',
              description: result.instagram.error || 'Дзеркальну публікацію не прийнято. Подробиці є у Сповіщеннях.',
              duration: 12,
            });
          } else {
            notify.success({
              message: result.scheduled_at ? 'Facebook-публікацію заплановано' : 'Facebook-публікацію передано в чергу',
              description: mirrored ? 'Дзеркальну публікацію в Instagram теж прийнято.' : undefined,
              duration: 7,
            });
          }
          setFacebookPreview(null); selection.clear(); setSelectionMode(false);
          if (mirrored) window.dispatchEvent(new CustomEvent('bms:instagram-status-refresh'));
        },
      },
    ).catch(() => undefined).finally(() => setFacebookBusy(false));
  };

  const publishFacebookBatch = async (request: FacebookBatchRequest) => {
    if (facebookBusy) return;
    const approved = await confirmDialog({
      title: 'Передати пакет у Facebook?',
      body: `${request.items.length} окремих публікацій буде передано у захищену чергу. Кожна картка збере власні медіа й текст.`,
      okText: 'Передати пакет', kind: 'warning',
    });
    if (!approved) return;
    setFacebookBusy(true);
    taskManager.run(
      `Пакет Facebook: ${request.items.length} публікацій`,
      async () => {
        const response = await fetch('/api/publications/facebook/create-posts-batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Facebook-пакет не виконано');
        return result;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => ({ status: result.status === 'success' ? 'success' : 'partial', detail: `${result.counts?.success || 0} прийнято · ${result.counts?.error || 0} помилок` }),
        onSuccess: (result: any) => {
          const counts = result.counts || {};
          if (result.status === 'success') notify.success({ message: 'Facebook-пакет прийнято', description: `${counts.success || 0} публікацій`, duration: 7 });
          else notify.warning({ message: 'Facebook-пакет прийнято частково', description: `${counts.success || 0} прийнято · ${counts.error || 0} помилок`, duration: 10 });
          setFacebookBatchIds(null); selection.clear(); setSelectionMode(false);
        },
      },
    ).catch(() => undefined).finally(() => setFacebookBusy(false));
  };

  // Esc — зняти виділення (дія користувача). Скидання буфера ЛИШЕ явними діями:
  // Esc / кнопка «Зняти виділення» / вихід з режиму «Виділити».
  // isActivePage: при keep-alive «Товари» лишаються змонтованими на будь-якій
  // вкладці, і без цієї перевірки Esc у «Клієнтах» мовчки знімав би виділення
  // товарів, зроблене раніше.
  useEffect(() => {
    if (!isActivePage) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selection.size > 0) selection.clear();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isActivePage, selection.size, selection.clear]);

  const handleResetFilters = () => {
    setSelectedFilters({});
    // «Тільки непродані» — дефолтний фільтр; навмисно НЕ скидаємо при reset (⌘R),
    // бо це базовий режим перегляду, а не накладений користувачем фільтр.
    setOnlyProblematic(false);
    setOnlyRostovka(false);
    setOnlyWithPhoto(false);
    setSelectedShipmentId(undefined);
    setVisibleOnly(false);
    setPage(1);
  };
    
    useEffect(() => { fetchProducts(); }, [page, perPage, currentSearchTerm, selectedFilters, onlyUnsold, onlyProblematic, onlyRostovka, onlyWithPhoto, selectedShipmentId, visibleOnly, sortBy, sortDir]);

    // Динамічний фасет розмірів — оновлюємо при зміні будь-якого «звужуючого»
    // фільтра/пошуку (без page/sort: вони не впливають на наявні розміри).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { fetchAvailableFacets(); }, [currentSearchTerm, selectedFilters, onlyUnsold, onlyProblematic, onlyRostovka, onlyWithPhoto, selectedShipmentId, visibleOnly]);

    useEffect(() => () => {
      abortRef.current?.abort();
      facetsAbortRef.current?.abort();
      fetchIdRef.current += 1;
      facetsFetchIdRef.current += 1;
    }, []);

    // Auto-refresh products when parsing completes — через ref на АКТУАЛЬНИЙ
    // fetchProducts. Інакше listener із порожніми deps захоплює stale-замикання з
    // монтування (порожній пошук/дефолтні фільтри), і коли фоновий авто-парс
    // диспатчить 'parsing-complete' (через ~5-10с), він перезавантажує список з
    // дефолтними параметрами → активний пошук користувача мовчки скидається.
    const fetchProductsRef = useRef(fetchProducts);
    fetchProductsRef.current = fetchProducts;
    useEffect(() => {
      const handler = () => { fetchProductsRef.current(); };
      window.addEventListener('parsing-complete', handler);
      // Після будь-якої дії публікації (у картці чи пакетно, УСПІХ чи ПОМИЛКА)
      // список має показувати РЕАЛЬНИЙ стан бекенда, а не застарілі іконки.
      // Легкий дебаунс — щоб серія подій дала один рефетч.
      let t: any;
      const debounced = () => { clearTimeout(t); t = setTimeout(() => fetchProductsRef.current(), 400); };
      const pubEvents = ['bms:prom-status-refresh', 'bms:shafa-status-refresh', 'bms:olx-status-refresh', 'bms:telegram-status-refresh'];
      pubEvents.forEach((e) => window.addEventListener(e, debounced));
      return () => {
        window.removeEventListener('parsing-complete', handler);
        clearTimeout(t);
        pubEvents.forEach((e) => window.removeEventListener(e, debounced));
      };
    }, []);

    // ⌘/Ctrl+U — увімкнути/вимкнути «Тільки непродані».
    // Слухаємо e.code === 'KeyU' (фізична клавіша), бо e.key на кириличній
    // розкладці повертає 'г' і умова `=== 'u'` не спрацювала б.
    // isActivePage: інакше ⌘U з будь-якої іншої вкладки перемикав би фільтр
    // «Товарів» наосліп — з тостом про зміну, якої не видно.
    useEffect(() => {
      if (!isActivePage) return;
      const onKey = (e: KeyboardEvent) => {
        const mod = e.metaKey || e.ctrlKey;
        if (!mod || e.altKey || e.shiftKey) return;
        if (e.code !== 'KeyU') return;
        const tag = (e.target as HTMLElement)?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable) return;
        e.preventDefault();
        setOnlyUnsold((v) => {
          const next = !v;
          toast.info(next ? 'Лише непродані' : 'Усі товари', { autoClose: 1000, hideProgressBar: true });
          return next;
        });
        setPage(1);
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [isActivePage]);

    useEffect(() => {
      // Load filter options once
      productService.getFilters().then(setFiltersMeta).catch(() => setFiltersMeta(null));
    }, []);

    // Parse URL -> state on mount
    useEffect(() => {
      const params = new URLSearchParams(location.search);
      const pn = Number(params.get('page')) || 1;
      const ps = Number(params.get('per_page')) || 20;
      // ВАЖЛИВО: вкладки (Товари / Клієнти / Поставки / ...) живуть у тому ж
      // URL і кожна перезаписує `?sort_by=...`. Після візиту в «Клієнти»
      // (sort_by=last_name) повернення в «Товари» дає невідомий бекенду ключ,
      // що мовчки падає у fallback `ORDER BY created_at DESC`, а дропдаун
      // продовжує показувати «За датою завозу» (перший option) — користувач
      // не розуміє, чому сортування «стрибає». Фільтруємо чужі значення.
      const ALLOWED_SORT_BY = new Set([
        'delivery_date', 'delivery_date_asc',
        'created_at', 'created_at_asc',
        'last_sold', 'price_desc', 'price_asc',
        'id', 'price', 'quantity', // AntD column-header sorts
      ]);
      const rawSb = params.get('sort_by');
      const sb = rawSb && ALLOWED_SORT_BY.has(rawSb) ? rawSb : 'delivery_date';
      const sd = (params.get('sort_dir') as 'asc' | 'desc') || 'desc';
      const ou = params.has('only_unsold') ? params.get('only_unsold') === 'true' : true;
      const op = params.get('only_problematic') === 'true';
      const or_ = params.get('only_rostovka') === 'true';
      const owp = params.get('only_with_photo') === 'true';
      const sh = params.get('shipment_id') ? Number(params.get('shipment_id')) : undefined;
      const vo = params.get('visible_only') === 'true';
      setPage(pn);
      setPerPage(ps);
      setSortBy(sb);
      setSortDir(sd);
      setOnlyUnsold(ou);
      setOnlyProblematic(op);
      setOnlyRostovka(or_);
      setOnlyWithPhoto(owp);
      setSelectedShipmentId(sh);
      setVisibleOnly(vo);
      // basic selected filters
      const nf: ProductFilterType = {};
      const rawKeys = ['typeid','subtypeid','brandid','genderid','colorid','statusid','conditionid'] as const;
      rawKeys.forEach(k => {
        const v = params.get(String(k));
        if (v) (nf as any)[k] = Number(v);
      });
      const minp = params.get('min_price');
      const maxp = params.get('max_price');
      if (minp) nf.min_price = Number(minp);
      if (maxp) nf.max_price = Number(maxp);
      setSelectedFilters(nf);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // State -> URL sync
    useEffect(() => {
      const params = new URLSearchParams();
      params.set('page', String(page));
      params.set('per_page', String(perPage));
      params.set('sort_by', sortBy);
      params.set('sort_dir', sortDir);
      if (onlyUnsold) params.set('only_unsold', 'true');
      if (onlyProblematic) params.set('only_problematic', 'true');
      if (onlyRostovka) params.set('only_rostovka', 'true');
      if (onlyWithPhoto) params.set('only_with_photo', 'true');
      if (selectedShipmentId) params.set('shipment_id', String(selectedShipmentId));
      if (visibleOnly) params.set('visible_only', 'true');
      Object.entries(selectedFilters).forEach(([k, v]) => {
        if (v !== undefined && v !== null && typeof v !== 'object') params.set(k, String(v));
      });
      navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
    }, [page, perPage, sortBy, sortDir, onlyUnsold, onlyProblematic, onlyRostovka, onlyWithPhoto, selectedShipmentId, visibleOnly, selectedFilters, navigate, location.pathname]);

    return (
    <MainLayout
      filterPanelContent={
        filtersMeta ? (
          <ProductFiltersPanel
            filters={filtersMeta}
            selectedFilters={selectedFilters}
            availableEuSizes={availableEuSizes}
            availableColorGroups={availableColorGroups}
            onFilterChange={(f) => { setSelectedFilters(f); setPage(1); }}
          />
        ) : (
          <LoadingSpinner variant="section" text="Завантаження фільтрів…" />
        )
      }
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      {/* Main content for Products Page */}
      <div className="p-4 pb-12 bg-white dark:bg-gray-800 shadow-md rounded-lg w-full">
        <div className="flex justify-between items-center mb-2">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Товари</h1>
            {currentSearchTerm && (
              <span className='text-xs text-gray-500 dark:text-gray-400'>Пошук: "{currentSearchTerm}"</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <select
              value={selectedShipmentId ?? ''}
              onChange={(e) => { setSelectedShipmentId(e.target.value ? Number(e.target.value) : undefined); setPage(1); }}
              className="text-xs border border-gray-200 dark:border-gray-600 rounded px-2 py-1.5 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-400 max-w-[200px]"
            >
              <option value="">Всі завози</option>
              {filtersMeta?.shipments?.map((s: any) => (
                <option key={s.id} value={s.id}>{s.name} ({s.count})</option>
              ))}
            </select>
            <select value={sortBy}
              onChange={e => { setSortBy(e.target.value); setSortDir('desc'); setPage(1); }}
              className="text-xs border border-gray-200 dark:border-gray-600 rounded px-2 py-1.5 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-400">
              <option value="delivery_date">За датою завозу</option>
              <option value="delivery_date_asc">За датою завозу (спочатку старі)</option>
              <option value="created_at">Найновіші (додані в базу)</option>
              <option value="created_at_asc">Найстаріші (додані в базу)</option>
              <option value="last_sold">Останні продані</option>
              <option value="price_desc">Від найдорожчого</option>
              <option value="price_asc">Від найдешевшого</option>
            </select>
            {/* Виділення (єдиний буфер selectionManager) + меню «Дії» над виділеним */}
            <Button
              icon={<CheckSquareOutlined />}
              type={selectionMode ? 'primary' : 'default'}
              onClick={() => setSelectionMode((m) => { const next = !m; if (!next) selection.clear(); return next; })}
              title="Виділяти рядки товарів для масових дій"
            >
              {selectionMode ? (selection.size > 0 ? `Виділено: ${selection.size}` : 'Режим виділення') : 'Виділити'}
            </Button>
            {selection.size > 0 && (
              <Dropdown
                trigger={['click']}
                menu={{
                  items: [
                    { key: 'prom', icon: <SendOutlined />, label: 'Відправити на PROM' },
                    { key: 'shafa', icon: <span className="inline-flex h-4 w-4 items-center justify-center rounded bg-black text-[9px] leading-none text-white font-black">S</span>, label: 'Відправити на Shafa' },
                    { key: 'olx', icon: <span className="inline-flex h-4 items-center justify-center rounded bg-[#002f34] px-1 text-[8px] leading-none text-[#a9e000] font-black">OLX</span>, label: 'Відправити на OLX' },
                    { key: 'telegram', icon: <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-[#229ED9] text-[9px] leading-none text-white">➤</span>, label: 'Відправити в Telegram' },
                    { key: 'viber', icon: <span className="inline-flex h-4 w-4 items-center justify-center rounded bg-[#7360F2] text-[9px] leading-none text-white font-black">V</span>, label: 'Відправити у Viber' },
                    { key: 'instagram', icon: <InstagramMark className="h-4 w-4 text-[10px]" />, label: 'Підготувати для Instagram' },
                    { key: 'facebook', icon: <FacebookMark className="h-4 w-4 text-[10px]" />, label: 'Підготувати для Facebook' },
                    ...(selection.size > 1 ? [
                      { type: 'divider' as const },
                      {
                        key: 'viber-collection',
                        icon: <span className="inline-flex h-4 w-4 items-center justify-center rounded bg-[#7360F2] text-[9px] leading-none text-white font-black">V</span>,
                        label: `Підбірка у Viber (${selection.size} у сітці)`,
                      },
                      {
                        key: 'facebook-collection',
                        icon: <FacebookMark className="h-4 w-4 text-[10px]" />,
                        label: `Підбірка у Facebook (${selection.size} у сітці)`,
                      },
                    ] : []),
                    { type: 'divider' as const },
                    { key: 'clear', label: 'Зняти виділення' },
                  ],
                  onClick: ({ key }) => {
                    if (key === 'prom') sendSelectedToProm();
                    else if (key === 'shafa') void sendSelectedToShafa();
                    else if (key === 'olx') void sendSelectedToOlx();
                    else if (key === 'telegram') void openSelectedTelegram();
                    else if (key === 'viber') void openSelectedViber();
                    else if (key === 'instagram') void openSelectedInstagram();
                    else if (key === 'facebook') void openSelectedFacebook();
                    else if (key === 'viber-collection') openSelectedCollection('viber');
                    else if (key === 'facebook-collection') openSelectedCollection('facebook');
                    else if (key === 'clear') selection.clear();
                  },
                }}
              >
                <Button>Дії ({selection.size}) <DownOutlined /></Button>
              </Dropdown>
            )}
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setShowAddProduct(true)}>
              Додати товар
            </Button>
            <AddProductModal
              open={showAddProduct}
              onClose={() => setShowAddProduct(false)}
              onAdded={() => fetchProducts()}
            />
          </div>
        </div>

        {/* Видалено окремий sticky-бар дій, кнопки перенесені у шапку */}
        <div className="w-full overflow-x-auto">
        <ProductsTable
          products={products}
          loading={loading}
          onDelete={async (id) => { /* видалення буде додано пізніше */ }}
          onPageChange={(p) => setPage(p)}
          onVisibilityChange={async (id, isVisible) => { await productService.updateProductVisibility(id, isVisible); await fetchProducts(); }}
          onSortChange={(sb, sd) => { setSortBy(sb); setSortDir(sd); setPage(1); }}
          onProductSaved={() => { void fetchProducts(); }}
            selectionEnabled={selectionMode}
            selectedRowKeys={selection.ids as React.Key[]}
            onSelectedRowKeysChange={(keys) => selection.set(keys as number[])}
        />
        </div>

      {/* Фіксований (fixed) нижній бар для стабільної пагінації незалежно від скролу */}
        <div className="fixed bottom-0 left-0 right-0 px-0 py-3 bg-white/95 dark:bg-gray-800/95 backdrop-blur supports-backdrop-blur:backdrop-blur-md border-t border-gray-100 dark:border-gray-700 z-20 shadow-[0_-2px_10px_rgba(0,0,0,0.04)]">
          <div className="w-full grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] items-center px-4 lg:px-6 gap-4">
            {/* Спільний підпис «Тільки:» замість чотирьох повторів того самого
                слова — рядок стає вдвічі коротшим і вміщується в один рядок,
                а кожен чекбокс лишається тим самим фільтром «тільки X». */}
            <div className="order-2 md:order-none flex flex-wrap items-center gap-x-3.5 2xl:gap-x-5 gap-y-2 justify-self-start justify-start mt-3 md:mt-0 pl-2 md:pl-0">
              <span className="text-[13px] font-medium text-gray-400 dark:text-gray-500 whitespace-nowrap">Тільки:</span>
              <label className="inline-flex items-center text-[13px] whitespace-nowrap text-gray-700 dark:text-gray-300" title="Тільки непродані · перемкнути: ⌘/Ctrl + U">
                <input
                  type="checkbox"
                  checked={onlyUnsold}
                  onChange={(e) => { setOnlyUnsold(e.target.checked); setPage(1); }}
                  className="h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 dark:focus:ring-primary-400 dark:bg-gray-700 dark:border-gray-600"
                />
                <span className="ml-2">непродані</span>
              </label>
              <label className="inline-flex items-center text-[13px] whitespace-nowrap text-gray-700 dark:text-gray-300" title="Тільки проблемні">
                <input
                  type="checkbox"
                  checked={onlyProblematic}
                  onChange={(e) => { setOnlyProblematic(e.target.checked); setPage(1); }}
                  className="h-4 w-4 text-orange-500 border-gray-300 rounded focus:ring-orange-400 dark:focus:ring-orange-400 dark:bg-gray-700 dark:border-gray-600"
                />
                <span className="ml-2">проблемні</span>
              </label>
              <label className="inline-flex items-center text-[13px] whitespace-nowrap text-gray-700 dark:text-gray-300" title="Тільки ростовки">
                <input
                  type="checkbox"
                  checked={onlyRostovka}
                  onChange={(e) => { setOnlyRostovka(e.target.checked); setPage(1); }}
                  className="h-4 w-4 text-blue-500 border-gray-300 rounded focus:ring-blue-400 dark:focus:ring-blue-400 dark:bg-gray-700 dark:border-gray-600"
                />
                <span className="ml-2">ростовки</span>
              </label>
              <label className="inline-flex items-center text-[13px] whitespace-nowrap text-gray-700 dark:text-gray-300"
                title="Тільки з фото — власним або підтягнутим з товару-донора">
                <input
                  type="checkbox"
                  checked={onlyWithPhoto}
                  onChange={(e) => { setOnlyWithPhoto(e.target.checked); setPage(1); }}
                  className="h-4 w-4 text-emerald-500 border-gray-300 rounded focus:ring-emerald-400 dark:focus:ring-emerald-400 dark:bg-gray-700 dark:border-gray-600"
                />
                <span className="ml-2">з фото</span>
              </label>
            </div>
            <div className="order-1 md:order-none justify-self-center flex justify-center">
              <Pagination
                currentPage={products.page}
                totalPages={products.pages || Math.ceil(products.total / (products.per_page || perPage))}
                totalItems={products.total}
                itemsPerPage={products.per_page}
                onPageChange={(p) => setPage(p)}
                showRange={false}
              />
            </div>
            <div className="order-3 md:order-none justify-self-end flex justify-end text-[13px] text-gray-500 pr-2 md:pr-0">
              <span className="whitespace-nowrap">Показано {products.items.length ? (products.page - 1) * products.per_page + 1 : 0}-{Math.min(products.page * products.per_page, products.total)} з {products.total} записів</span>
            </div>
          </div>
        </div>
        {/* Спейсер, щоб контент не накривався fixed-панеллю */}
        <div className="h-10" />
      </div>
      {telegramPreview && (
        <TelegramPublishDialog
          data={telegramPreview}
          busy={telegramBusy}
          onPreviewChange={setTelegramPreview}
          onCancel={() => { if (!telegramBusy) setTelegramPreview(null); }}
          onConfirm={publishSingleTelegram}
        />
      )}
      {telegramBatchIds && (
        <TelegramBatchPublishDialog
          productIds={telegramBatchIds}
          busy={telegramBusy}
          onCancel={() => { if (!telegramBusy) setTelegramBatchIds(null); }}
          onPublish={publishTelegramBatch}
        />
      )}
      {viberPreview && (
        <ViberPublishDialog
          data={viberPreview}
          busy={viberBusy}
          onPreviewChange={setViberPreview}
          onCancel={() => { if (!viberBusy) setViberPreview(null); }}
          onConfirm={publishSingleViber}
        />
      )}
      {viberBatchIds && (
        <ViberBatchPublishDialog
          productIds={viberBatchIds}
          busy={viberBusy}
          onCancel={() => { if (!viberBusy) setViberBatchIds(null); }}
          onPublish={publishViberBatch}
        />
      )}
      {collectionRequest && (
        <CollectionCollageDialog
          platform={collectionRequest.platform}
          productIds={collectionRequest.ids}
          busy={collectionBusy}
          onCancel={() => { if (!collectionBusy) setCollectionRequest(null); }}
          onPublish={publishCollection}
        />
      )}
      {instagramPreview && (
        <InstagramPublishDialog
          data={instagramPreview}
          busy={instagramBusy}
          onCancel={() => { if (!instagramBusy) setInstagramPreview(null); }}
          onConfirm={publishSingleInstagram}
        />
      )}
      {instagramBatchIds && (
        <InstagramBatchDraftDialog
          productIds={instagramBatchIds}
          busy={instagramBusy}
          onCancel={() => { if (!instagramBusy) setInstagramBatchIds(null); }}
          onPublish={publishInstagramBatch}
        />
      )}
      {facebookPreview && (
        <FacebookPublishDialog
          data={facebookPreview}
          busy={facebookBusy}
          onCancel={() => { if (!facebookBusy) setFacebookPreview(null); }}
          onConfirm={publishSingleFacebook}
        />
      )}
      {facebookBatchIds && (
        <FacebookBatchDraftDialog
          productIds={facebookBatchIds}
          busy={facebookBusy}
          onCancel={() => { if (!facebookBusy) setFacebookBatchIds(null); }}
          onPublish={publishFacebookBatch}
        />
      )}
    </MainLayout>
    );
};

export default ProductsPage;
