import React, { useState, useEffect, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import MainLayout from '../layouts/MainLayout';
import Pagination from '../components/common/Pagination';

interface OrderItem {
  id: number;
  product_id: number | null;
  product_number: string;
  product_name: string;
  quantity: number;
  price: number;
  notes: string | null;
}

interface Order {
  id: number;
  client_id: number;
  client_name: string;
  order_date: string;
  order_status_id: number | null;
  order_status_name: string | null;
  payment_status_id: number | null;
  payment_status: string | null;
  payment_status_name: string | null;
  delivery_method_id: number | null;
  delivery_method_name: string | null;
  tracking_number: string | null;
  total_amount: number;
  notes: string | null;
  priority: number;
  order_items: OrderItem[];
}

interface OrdersPageProps {
  currentSearchTerm: string;
}

type SortField = 'id' | 'order_date' | 'total_amount' | 'priority';

const STATUS_COLORS: Record<string, string> = {
  'Нове': '#3b82f6',
  'Підтверджено': '#8b5cf6',
  'Відправлено': '#f59e0b',
  'Виконано': '#10b981',
  'Скасовано': '#ef4444',
};

const PAYMENT_COLORS: Record<string, string> = {
  'Оплачено': '#10b981',
  'Не оплачено': '#ef4444',
  'Часткова': '#f59e0b',
};

function fmtDate(d: string | null) {
  if (!d) return '—';
  try { return new Date(d).toLocaleDateString('uk-UA'); } catch { return d; }
}
function fmtMoney(n: number) {
  return new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH', maximumFractionDigits: 0 }).format(n);
}

function getOrderConflicts(order: Order): string[] {
  const issues: string[] = [];
  const unresolved = order.order_items?.filter(i => i.product_id === null || i.product_id === undefined);
  if (unresolved?.length) {
    const nums = unresolved.map(i => i.product_number || i.notes || '?').join(', ');
    issues.push(`${unresolved.length} товар(ів) не знайдено в базі: ${nums}`);
  }
  if (!order.order_status_name) {
    issues.push('Не вказано статус замовлення');
  }
  if (order.total_amount === 0) {
    issues.push('Сума замовлення = 0');
  }
  return issues;
}

const OrdersFilterPanelContent: React.FC = () => (
  <div>
    <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Статус замовлення</h3>
    <div className="p-2 border border-dashed rounded mb-4 h-24 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Order Status Filter</div>
    <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Статус оплати</h3>
    <div className="p-2 border border-dashed rounded mb-4 h-24 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Payment Status Filter</div>
    <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Доставка</h3>
    <div className="p-2 border border-dashed rounded mb-4 h-24 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Delivery Filter</div>
    <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Рік / Місяць</h3>
    <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Date Range</div>
  </div>
);

const OrdersPage: React.FC<OrdersPageProps> = ({ currentSearchTerm }) => {
  const navigate = useNavigate();
  const location = useLocation();

  const [orders, setOrders] = useState<Order[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(20);
  const [sortBy, setSortBy] = useState<SortField>('order_date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const fetchOrders = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(perPage),
        sort_by: sortBy,
        sort_dir: sortDir,
      });
      if (currentSearchTerm) params.set('search', currentSearchTerm);
      const res = await fetch(`/api/orders?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setOrders(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 1);
    } catch (e: any) {
      setError(e.message || 'Помилка завантаження');
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  }, [page, perPage, sortBy, sortDir, currentSearchTerm]);

  useEffect(() => { fetchOrders(); }, [fetchOrders]);

  // Sync URL
  useEffect(() => {
    const p = new URLSearchParams();
    p.set('page', String(page));
    p.set('per_page', String(perPage));
    p.set('sort_by', sortBy);
    p.set('sort_dir', sortDir);
    navigate({ pathname: location.pathname, search: p.toString() }, { replace: true });
  }, [page, perPage, sortBy, sortDir]);

  const handleSort = (field: SortField) => {
    if (sortBy === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortBy(field); setSortDir('desc'); }
    setPage(1);
  };

  const SortIcon = ({ field }: { field: SortField }) => (
    <span className="ml-1 text-gray-400 text-xs">
      {sortBy === field ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
    </span>
  );

  const handleRefresh = () => { setIsRefreshing(true); fetchOrders(); };
  const handleResetFilters = () => { setPage(1); setSortBy('order_date'); setSortDir('desc'); };

  return (
    <MainLayout
      filterPanelContent={<OrdersFilterPanelContent />}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      <div className="p-4 pb-24 bg-white dark:bg-gray-800 shadow-md rounded-lg w-full">
        {/* Header */}
        <div className="sticky top-0 z-20 bg-white/90 dark:bg-gray-800/90 backdrop-blur px-2 py-2 -mx-2 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center mb-3">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">
            Замовлення
            <span className="ml-2 text-base font-normal text-gray-400">({total})</span>
          </h1>
          {currentSearchTerm && (
            <span className="text-sm text-gray-500 dark:text-gray-400">Пошук: «{currentSearchTerm}»</span>
          )}
        </div>

        {/* Table */}
        {loading && orders.length === 0 ? (
          <div className="flex justify-center items-center h-48 text-gray-400">Завантаження...</div>
        ) : error ? (
          <div className="flex justify-center items-center h-48 text-red-500">{error}</div>
        ) : (
          <div className="overflow-x-auto rounded border border-gray-200 dark:border-gray-700">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600">
                <tr>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('id')}>
                    №<SortIcon field="id" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('order_date')}>
                    Дата<SortIcon field="order_date" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Клієнт</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Товари</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('total_amount')}>
                    Сума<SortIcon field="total_amount" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Статус</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Оплата</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Доставка</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Трекінг</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Нотатки</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {orders.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="text-center py-12 text-gray-400">Замовлень не знайдено</td>
                  </tr>
                ) : orders.map(order => {
                  const conflicts = getOrderConflicts(order);
                  const hasConflict = conflicts.length > 0;
                  const conflictTitle = conflicts.join(' • ');
                  return (
                  <React.Fragment key={order.id}>
                    <tr
                      className={`transition-colors cursor-pointer ${hasConflict
                        ? 'bg-orange-50 dark:bg-orange-900/20 border-l-4 border-orange-400 hover:bg-orange-100 dark:hover:bg-orange-900/40'
                        : 'hover:bg-gray-50 dark:hover:bg-gray-700/50'}`}
                      title={hasConflict ? `⚠ ${conflictTitle}` : undefined}
                      onClick={() => setExpandedId(expandedId === order.id ? null : order.id)}
                    >
                      <td className="px-3 py-2 font-mono text-xs">
                        <span className={hasConflict ? 'text-orange-600 dark:text-orange-400 font-bold' : 'text-gray-500 dark:text-gray-400'}>
                          {order.id}{hasConflict && ' ⚠'}
                        </span>
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{fmtDate(order.order_date)}</td>
                      <td className="px-3 py-2 font-medium text-gray-900 dark:text-gray-100 max-w-[160px] truncate">{order.client_name}</td>
                      <td className="px-3 py-2 text-gray-500 dark:text-gray-400 text-center">
                        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-gray-100 dark:bg-gray-600 text-xs font-semibold">
                          {order.order_items?.length || 0}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-semibold whitespace-nowrap text-gray-900 dark:text-gray-100">{fmtMoney(order.total_amount)}</td>
                      <td className="px-3 py-2">
                        {order.order_status_name ? (
                          <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium text-white"
                            style={{ background: STATUS_COLORS[order.order_status_name] || '#6b7280' }}>
                            {order.order_status_name}
                          </span>
                        ) : <span className="text-gray-400 text-xs">—</span>}
                      </td>
                      <td className="px-3 py-2">
                        {(order.payment_status || order.payment_status_name) ? (
                          <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium text-white"
                            style={{ background: PAYMENT_COLORS[order.payment_status || order.payment_status_name || ''] || '#6b7280' }}>
                            {order.payment_status || order.payment_status_name}
                          </span>
                        ) : <span className="text-gray-400 text-xs">—</span>}
                      </td>
                      <td className="px-3 py-2 text-gray-600 dark:text-gray-300 text-xs max-w-[120px] truncate">
                        {order.delivery_method_name || '—'}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-500 dark:text-gray-400">
                        {order.tracking_number || '—'}
                      </td>
                      <td className="px-3 py-2 text-gray-500 dark:text-gray-400 text-xs max-w-[160px] truncate">
                        {order.notes || '—'}
                      </td>
                    </tr>
                    {expandedId === order.id && order.order_items?.length > 0 && (
                      <tr className="bg-blue-50 dark:bg-blue-900/20">
                        <td colSpan={10} className="px-6 py-3">
                          <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-2">Позиції замовлення:</div>
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="text-gray-500 dark:text-gray-400">
                                <th className="text-left pr-4 pb-1">Номер товару</th>
                                <th className="text-left pr-4 pb-1">Назва</th>
                                <th className="text-left pr-4 pb-1">К-сть</th>
                                <th className="text-left pb-1">Ціна</th>
                              </tr>
                            </thead>
                            <tbody>
                              {order.order_items.map(item => (
                                <tr key={item.id} className="border-t border-blue-100 dark:border-blue-800">
                                  <td className="pr-4 py-1 font-mono text-blue-600 dark:text-blue-400">{item.product_number}</td>
                                  <td className="pr-4 py-1 text-gray-700 dark:text-gray-300">{item.product_name}</td>
                                  <td className="pr-4 py-1">{item.quantity}</td>
                                  <td className="py-1 font-semibold">{fmtMoney(item.price)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Footer pagination */}
        <div className="fixed bottom-0 left-0 right-0 px-4 py-3 bg-white/95 dark:bg-gray-800/95 backdrop-blur border-t border-gray-100 dark:border-gray-700 z-20">
          <div className="max-w-screen-2xl mx-auto flex items-center justify-between gap-4">
            <span className="text-sm text-gray-500 dark:text-gray-400">
              Всього: <strong>{total}</strong> замовлень
            </span>
            <Pagination
              currentPage={page}
              totalPages={pages}
              totalItems={total}
              itemsPerPage={perPage}
              onPageChange={setPage}
              onPerPageChange={(n) => { setPerPage(n); setPage(1); }}
            />
            <span className="text-xs text-gray-400">
              Стор. {page} з {pages}
            </span>
          </div>
        </div>
        <div className="h-20" />
      </div>
    </MainLayout>
  );
};

export default OrdersPage;