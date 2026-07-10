import React, { useState, useEffect, useRef } from 'react';
import MainLayout from '../layouts/MainLayout';
import ProductsTable from '../components/products/ProductsTable';
import { productService, type ProductListResponse } from '../services/productService';
import { searchService } from '../services/searchService';
import SearchInsights from '../components/search/SearchInsights';
import ProductFiltersPanel from '../components/filters/ProductFilters';
import type { ProductFilter as ProductFilterType, ProductFilters as ProductFiltersType } from '../types/product';
import { useLocation, useNavigate } from 'react-router-dom';
import { useEffect as ReactUseEffect } from 'react';
import { Button } from 'antd';
import { toast } from 'react-toastify';
import Pagination from '../components/common/Pagination';
import AddProductModal from '../components/shipments/AddProductModal';
import { PlusOutlined, EyeOutlined, EyeInvisibleOutlined } from '@ant-design/icons';
import LoadingSpinner from '../components/common/LoadingSpinner';

// Placeholder for actual filter components for Products

interface ProductsPageProps {
  currentSearchTerm: string;
}

const ProductsPage: React.FC<ProductsPageProps> = ({ currentSearchTerm }) => {
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
  const [selectedShipmentId, setSelectedShipmentId] = useState<number | undefined>(undefined);
  const [visibleOnly, setVisibleOnly] = useState<boolean>(false);
  const [sortBy, setSortBy] = useState<string>('delivery_date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [searchInsights, setSearchInsights] = useState<any>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fetchIdRef = useRef(0);
  // Динамічні фасети (розміри + кольори), наявні в поточному відфільтрованому
  // наборі. null = ще не завантажено (панель тоді бере глобальний список).
  const [availableEuSizes, setAvailableEuSizes] = useState<string[] | null>(null);
  const [availableColorGroups, setAvailableColorGroups] = useState<{ id: number; count: number }[] | null>(null);
  const facetsAbortRef = useRef<AbortController | null>(null);
            
  // Effect to react to global search changes and fetch insights
  useEffect(() => {
    if (currentSearchTerm !== undefined) {
        console.log('ProductsPage received search term:', currentSearchTerm);
        
        // Якщо є пошуковий запит, отримуємо інсайти
        if (currentSearchTerm.trim().length >= 2) {
          fetchSearchInsights(currentSearchTerm.trim());
        } else {
          // Очищаємо інсайти коли пошук порожній
          setSearchInsights(null);
          // Скидаємо сторінку до першої при очищенні пошуку
          if (page !== 1) {
            setPage(1);
          }
        }
    }
  }, [currentSearchTerm]); // Прибрав page з залежностей

  const fetchSearchInsights = async (query: string) => {
    try {
      const results = await searchService.globalSearch(query, {
        scope: 'products',
        limit: 0, // Нам потрібні тільки інсайти
        include_insights: true
      });
      setSearchInsights(results.insights);
    } catch (error) {
      console.error('Failed to fetch search insights:', error);
      setSearchInsights(null);
    }
  };

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
    const params: Record<string, any> = {
      search: currentSearchTerm && currentSearchTerm.trim() ? currentSearchTerm.trim() : undefined,
      only_unsold: onlyUnsold || undefined,
      only_problematic: onlyProblematic || undefined,
      only_rostovka: onlyRostovka || undefined,
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
    if ((selectedFilters as any).size_letter?.length > 0) params['size_letter'] = (selectedFilters as any).size_letter;
    if (selectedFilters.min_measurementscm !== undefined) params['min_measurementscm'] = selectedFilters.min_measurementscm;
    if (selectedFilters.max_measurementscm !== undefined) params['max_measurementscm'] = selectedFilters.max_measurementscm;
    try {
      const facets = await productService.getAvailableFacets(params, controller.signal);
      setAvailableEuSizes(facets.eu);
      setAvailableColorGroups(facets.colorGroups);
    } catch (err: any) {
      if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') return;
      // м'яка деградація: лишаємо попередні списки
    }
  };

  const handleRefresh = () => { setIsRefreshing(true); fetchProducts().finally(() => setIsRefreshing(false)); };

  const handleResetFilters = () => {
    setSelectedFilters({});
    // «Тільки непродані» — дефолтний фільтр; навмисно НЕ скидаємо при reset (⌘R),
    // бо це базовий режим перегляду, а не накладений користувачем фільтр.
    setOnlyProblematic(false);
    setOnlyRostovka(false);
    setSelectedShipmentId(undefined);
    setVisibleOnly(false);
    setPage(1);
  };
    
    useEffect(() => { fetchProducts(); }, [page, perPage, currentSearchTerm, selectedFilters, onlyUnsold, onlyProblematic, onlyRostovka, selectedShipmentId, visibleOnly, sortBy, sortDir]);

    // Динамічний фасет розмірів — оновлюємо при зміні будь-якого «звужуючого»
    // фільтра/пошуку (без page/sort: вони не впливають на наявні розміри).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { fetchAvailableFacets(); }, [currentSearchTerm, selectedFilters, onlyUnsold, onlyProblematic, onlyRostovka, selectedShipmentId, visibleOnly]);

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
      return () => window.removeEventListener('parsing-complete', handler);
    }, []);

    // ⌘/Ctrl+U — увімкнути/вимкнути «Тільки непродані».
    // Слухаємо e.code === 'KeyU' (фізична клавіша), бо e.key на кириличній
    // розкладці повертає 'г' і умова `=== 'u'` не спрацювала б.
    useEffect(() => {
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
    }, []);

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
      const sh = params.get('shipment_id') ? Number(params.get('shipment_id')) : undefined;
      const vo = params.get('visible_only') === 'true';
      setPage(pn);
      setPerPage(ps);
      setSortBy(sb);
      setSortDir(sd);
      setOnlyUnsold(ou);
      setOnlyProblematic(op);
      setOnlyRostovka(or_);
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
      if (selectedShipmentId) params.set('shipment_id', String(selectedShipmentId));
      if (visibleOnly) params.set('visible_only', 'true');
      Object.entries(selectedFilters).forEach(([k, v]) => {
        if (v !== undefined && v !== null && typeof v !== 'object') params.set(k, String(v));
      });
      navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
    }, [page, perPage, sortBy, sortDir, onlyUnsold, onlyProblematic, onlyRostovka, selectedShipmentId, visibleOnly, selectedFilters, navigate, location.pathname]);

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
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setShowAddProduct(true)}>
              Додати товар
            </Button>
            <AddProductModal
              open={showAddProduct}
              onClose={() => setShowAddProduct(false)}
              onAdded={() => fetchProducts()}
            />
            <Button
              disabled={selectedRowKeys.length === 0}
              icon={<EyeOutlined />}
              onClick={async () => {
                if (selectedRowKeys.length === 0) return;
                await productService.bulkUpdateProducts(selectedRowKeys as number[], { is_visible: true });
                setSelectedRowKeys([]);
                await fetchProducts();
              }}
            >
              Увімкнути видимість
            </Button>
            <Button
              disabled={selectedRowKeys.length === 0}
              danger
              icon={<EyeInvisibleOutlined />}
              onClick={async () => {
                if (selectedRowKeys.length === 0) return;
                await productService.bulkUpdateProducts(selectedRowKeys as number[], { is_visible: false });
                setSelectedRowKeys([]);
                await fetchProducts();
              }}
            >
              Вимкнути видимість
            </Button>
          </div>
        </div>

        {/* Інсайти пошуку */}
        {currentSearchTerm && searchInsights && (
          <SearchInsights 
            insights={searchInsights}
            query={currentSearchTerm}
            onNavigateToCategory={(category, query) => {
              console.log(`Navigate to ${category} with query: ${query}`);
              // TODO: Реалізувати навігацію до інших категорій
            }}
          />
        )}

        {/* Видалено окремий sticky-бар дій, кнопки перенесені у шапку */}
        <div className="w-full overflow-x-auto">
        <ProductsTable
          products={products}
          loading={loading}
          onDelete={async (id) => { /* видалення буде додано пізніше */ }}
          onPageChange={(p) => setPage(p)}
          onVisibilityChange={async (id, isVisible) => { await productService.updateProductVisibility(id, isVisible); await fetchProducts(); }}
          onSortChange={(sb, sd) => { setSortBy(sb); setSortDir(sd); setPage(1); }}
            selectedRowKeys={selectedRowKeys}
            onSelectedRowKeysChange={setSelectedRowKeys}
        />
        </div>

      {/* Фіксований (fixed) нижній бар для стабільної пагінації незалежно від скролу */}
        <div className="fixed bottom-0 left-0 right-0 px-0 py-3 bg-white/95 dark:bg-gray-800/95 backdrop-blur supports-backdrop-blur:backdrop-blur-md border-t border-gray-100 dark:border-gray-700 z-20 shadow-[0_-2px_10px_rgba(0,0,0,0.04)]">
          <div className="w-full grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] items-center px-4 lg:px-6 gap-4">
            <div className="order-2 md:order-none flex items-center gap-6 justify-self-start justify-start mt-3 md:mt-0 pl-2 md:pl-0">
              <label className="inline-flex items-center text-sm text-gray-700 dark:text-gray-300" title="Перемкнути: ⌘/Ctrl + U">
                <input
                  type="checkbox"
                  checked={onlyUnsold}
                  onChange={(e) => { setOnlyUnsold(e.target.checked); setPage(1); }}
                  className="h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 dark:focus:ring-primary-400 dark:bg-gray-700 dark:border-gray-600"
                />
                <span className="ml-2">Тільки непродані</span>
              </label>
              <label className="inline-flex items-center text-sm text-gray-700 dark:text-gray-300">
                <input
                  type="checkbox"
                  checked={onlyProblematic}
                  onChange={(e) => { setOnlyProblematic(e.target.checked); setPage(1); }}
                  className="h-4 w-4 text-orange-500 border-gray-300 rounded focus:ring-orange-400 dark:focus:ring-orange-400 dark:bg-gray-700 dark:border-gray-600"
                />
                <span className="ml-2">Тільки проблемні</span>
              </label>
              <label className="inline-flex items-center text-sm text-gray-700 dark:text-gray-300">
                <input
                  type="checkbox"
                  checked={onlyRostovka}
                  onChange={(e) => { setOnlyRostovka(e.target.checked); setPage(1); }}
                  className="h-4 w-4 text-blue-500 border-gray-300 rounded focus:ring-blue-400 dark:focus:ring-blue-400 dark:bg-gray-700 dark:border-gray-600"
                />
                <span className="ml-2">Тільки ростовки</span>
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
            <div className="order-3 md:order-none justify-self-end flex justify-end text-xs md:text-sm lg:text-base text-gray-500 pr-2 md:pr-0">
              <span className="whitespace-nowrap">Показано {products.items.length ? (products.page - 1) * products.per_page + 1 : 0}-{Math.min(products.page * products.per_page, products.total)} з {products.total} записів</span>
            </div>
          </div>
        </div>
        {/* Спейсер, щоб контент не накривався fixed-панеллю */}
        <div className="h-10" />
      </div>
    </MainLayout>
    );
};

export default ProductsPage;
