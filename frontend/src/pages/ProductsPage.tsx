import React, { useState, useEffect } from 'react';
import MainLayout from '../layouts/MainLayout';
import ProductsTable from '../components/products/ProductsTable';
import { productService, type ProductListResponse } from '../services/productService';
import ProductFiltersPanel from '../components/filters/ProductFilters';
import type { ProductFilter as ProductFilterType, ProductFilters as ProductFiltersType } from '../types/product';
import { useLocation, useNavigate } from 'react-router-dom';
import { useEffect as ReactUseEffect } from 'react';
import { Button } from 'antd';
import Pagination from '../components/common/Pagination';
import { PlusOutlined, EyeOutlined, EyeInvisibleOutlined } from '@ant-design/icons';

// Placeholder for actual filter components for Products
const ProductsFilterPanelContent: React.FC = () => {
  return (
    <div>
      <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Ціна</h3>
      {/* Placeholder for Price Slider */}
      <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Price Slider Area</div>

      <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Заміри (СМ)</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Measurements Slider Area</div>

      <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Розмір</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Size Slider Area</div>

      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Бренд</h3>
      {/* Placeholder for Brand checkboxes with search */}
      <div className="p-2 border border-dashed rounded mb-4 h-24 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Brand Filter Area</div>
      
      {/* Add other filter sections (Вид, Модель, Стать, Країна, Постачальник, Стан) as placeholders */}
      <p className="text-xs text-center text-gray-400 dark:text-gray-500">More filters here...</p>
    </div>
  );
};

interface ProductsPageProps {
  currentSearchTerm: string; // Receive search term from App
}

const ProductsPage: React.FC<ProductsPageProps> = ({ currentSearchTerm }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loading, setLoading] = useState<boolean>(true);
  const [products, setProducts] = useState<ProductListResponse>({ items: [], total: 0, page: 1, per_page: 20, pages: 1 });
  const [page, setPage] = useState<number>(1);
  const [perPage, setPerPage] = useState<number>(20);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [filtersMeta, setFiltersMeta] = useState<ProductFiltersType | null>(null);
  const [selectedFilters, setSelectedFilters] = useState<ProductFilterType>({});
  const navigate = useNavigate();
  const location = useLocation();
  const [onlyUnsold, setOnlyUnsold] = useState<boolean>(false);
  const [visibleOnly, setVisibleOnly] = useState<boolean>(false);
  const [sortBy, setSortBy] = useState<string>('id');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
            
  // Effect to react to global search changes if needed for filtering
    useEffect(() => {
    if (currentSearchTerm !== undefined) {
        console.log('ProductsPage received search term:', currentSearchTerm);
        // TODO: Implement actual filtering logic based on currentSearchTerm
            }
  }, [currentSearchTerm]);

  const fetchProducts = async () => {
    setLoading(true);
    try {
      const res = await productService.getProducts({
        page,
        per_page: perPage,
        sort_by: sortBy,
        sort_dir: sortDir,
        search: currentSearchTerm,
        with_stock_only: onlyUnsold ? true : undefined,
        is_visible: visibleOnly ? true : undefined,
        ...selectedFilters,
      });
      setProducts(res);
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = () => { setIsRefreshing(true); fetchProducts().finally(() => setIsRefreshing(false)); };

  const handleResetFilters = () => {
    console.log('Resetting product filters...');
    // Logic to reset all filter states for products
    };
    
    useEffect(() => { fetchProducts(); }, [page, perPage, currentSearchTerm, selectedFilters]);

    useEffect(() => {
      // Load filter options once
      productService.getFilters().then(setFiltersMeta).catch(() => setFiltersMeta(null));
    }, []);

    // Parse URL -> state on mount
    useEffect(() => {
      const params = new URLSearchParams(location.search);
      const pn = Number(params.get('page')) || 1;
      const ps = Number(params.get('per_page')) || 20;
      const sb = params.get('sort_by') || 'id';
      const sd = (params.get('sort_dir') as 'asc' | 'desc') || 'desc';
      const ou = params.get('only_unsold') === 'true';
      const vo = params.get('visible_only') === 'true';
      setPage(pn);
      setPerPage(ps);
      setSortBy(sb);
      setSortDir(sd);
      setOnlyUnsold(ou);
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
      if (visibleOnly) params.set('visible_only', 'true');
      Object.entries(selectedFilters).forEach(([k, v]) => {
        if (v !== undefined && v !== null && typeof v !== 'object') params.set(k, String(v));
      });
      navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
    }, [page, perPage, sortBy, sortDir, onlyUnsold, visibleOnly, selectedFilters, navigate, location.pathname]);

    return (
    <MainLayout
      filterPanelContent={<ProductsFilterPanelContent />}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      {/* Main content for Products Page */}
      <div className="p-4 bg-white dark:bg-gray-800 shadow-md rounded-lg w-full">
        <div className="flex justify-between items-center mb-2">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Товари</h1>
          {/* Display the search term received from props */}
          {currentSearchTerm && <p className='text-sm text-gray-500 dark:text-gray-400'>Активний пошук: "{currentSearchTerm}"</p>}
        </div>

        {/* Фіксована верхня панель керування (мінімалістична, без зайвих ліній) */}
        <div className="sticky top-0 z-10 -mx-4 px-4 py-1 bg-white/80 dark:bg-gray-800/80">
          <div className="flex items-center justify-end gap-1">
              <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/products/create')}>
                Додати товар
              </Button>
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

        {/* Низ сторінки: ліворуч чекбокси, по центру — пагінація */}
        <div className="mt-5 grid grid-cols-1 md:grid-cols-3 items-center">
          <div className="flex items-center gap-6 order-2 md:order-1 md:justify-start justify-center mt-3 md:mt-0">
            <label className="inline-flex items-center text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={onlyUnsold}
                onChange={(e) => { setOnlyUnsold(e.target.checked); setPage(1); }}
                className="h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 dark:focus:ring-primary-400 dark:bg-gray-700 dark:border-gray-600"
              />
              <span className="ml-2">Показувати тільки непродані</span>
            </label>
            <label className="inline-flex items-center text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={visibleOnly}
                onChange={(e) => { setVisibleOnly(e.target.checked); setPage(1); }}
                className="h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 dark:focus:ring-primary-400 dark:bg-gray-700 dark:border-gray-600"
              />
              <span className="ml-2">Лише видимі</span>
            </label>
          </div>
          <div className="order-1 md:order-2 flex justify-center">
          <Pagination
            currentPage={products.page}
            totalPages={Math.ceil(products.total / products.per_page)}
            totalItems={products.total}
            itemsPerPage={products.per_page}
            onPageChange={(p) => setPage(p)}
          />
          </div>
          <div className="order-3" />
        </div>
      </div>
    </MainLayout>
    );
};

export default ProductsPage; 