import React, { useState, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import OrderDetailsModal from './OrderDetailsModal';
import { format } from 'date-fns';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faEdit, faTrash, faEye, faFilter, faSearch } from '@fortawesome/free-solid-svg-icons';
import { 
  OrderWithDetails, 
  fetchOrders, 
  fetchOrderFilters, 
  deleteOrder, 
  FilterOption, 
  FilterOptions, 
  OrderFilters,
  bulkUpdateOrders
} from '../../services/orderService';
import Pagination from '../common/Pagination';
import BmsEmpty from '../common/BmsEmpty';
import { CopyOnClick, OrderStatusBadge, PaymentStatusBadge, UnknownIf } from '../common/displayHelpers';
import { confirmDialog } from '../../ui/feedback';

interface OrdersTableProps {
  onViewOrder?: (orderId: number) => void;
  onEditOrder?: (orderId: number) => void;
  onDeleteOrder?: (orderId: number) => void;
}

const OrdersTable: React.FC<OrdersTableProps> = ({
  onViewOrder,
  onEditOrder,
  onDeleteOrder
}) => {
  const navigate = useNavigate();
  const [orders, setOrders] = useState<OrderWithDetails[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<number>(1);
  const [perPage, setPerPage] = useState<number>(10);
  const [sortBy, setSortBy] = useState<'id' | 'order_date' | 'total_amount' | 'priority'>('order_date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [totalOrders, setTotalOrders] = useState<number>(0);
  const [totalPages, setTotalPages] = useState<number>(1);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [filters, setFilters] = useState<OrderFilters>({});
  const [filterOptions, setFilterOptions] = useState<FilterOptions>({
    order_statuses: [],
    payment_statuses: [],
    payment_methods: [],
    delivery_methods: [],
    delivery_statuses: [],
    clients: []
  });
  const [detailsId, setDetailsId] = useState<number | null>(null);
  const [detailsOpen, setDetailsOpen] = useState<boolean>(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [bulkStatusId, setBulkStatusId] = useState<number | ''>('');
  const location = useLocation();

  const handleViewOrder = (orderId: number) => {
    if (onViewOrder) onViewOrder(orderId);
    else { setDetailsId(orderId); setDetailsOpen(true); }
  };
  const handleEditOrder = (orderId: number) => {
    if (onEditOrder) onEditOrder(orderId);
    else navigate(`/orders/${orderId}/edit`);
  };
  const handleDeleteOrder = async (orderId: number) => {
    if ((await confirmDialog('Ви впевнені, що хочете видалити це замовлення?'))) {
      try {
        await deleteOrder(orderId);
        fetchOrdersList();
        if (onDeleteOrder) onDeleteOrder(orderId);
      } catch (err) {
        setError('Помилка при видаленні замовлення');
        console.error('Error deleting order:', err);
      }
    }
  };
  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '—';
    try { return format(new Date(dateStr), 'dd.MM.yyyy'); } catch { return dateStr; }
  };
  const formatPrice = (price: number) => new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH' }).format(price);
  const fetchOrdersList = async () => {
    setLoading(true); setError(null);
    try {
      const queryFilters = { ...filters };
      if (searchQuery) queryFilters.search = searchQuery;
      const params = new URLSearchParams();
      params.append('page', String(page));
      params.append('per_page', String(perPage));
      params.append('sort_by', sortBy);
      params.append('sort_dir', sortDir);
      if (queryFilters.search) params.append('search', queryFilters.search);
      const res = await fetch(`/api/orders?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const response = await res.json();
      setOrders(response.items);
      setTotalOrders(response.total);
      setTotalPages(response.pages);
    } catch (err) {
      setError('Помилка при завантаженні замовлень');
      console.error('Error fetching orders:', err);
    } finally { setLoading(false); }
  };
  const loadFilterOptions = async () => {
    try { setFilterOptions(await fetchOrderFilters()); } catch (err) { console.error('Error fetching filter options:', err); }
  };
  useEffect(() => { loadFilterOptions(); }, []);
  useEffect(() => { fetchOrdersList(); }, [page, perPage, filters, sortBy, sortDir]);

  // Parse URL -> state on mount
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const pn = Number(params.get('page')) || 1;
    const ps = Number(params.get('per_page')) || 10;
    const sb = (params.get('sort_by') as typeof sortBy) || 'order_date';
    const sd = (params.get('sort_dir') as typeof sortDir) || 'desc';
    const q = params.get('search') || '';
    setPage(pn);
    setPerPage(ps);
    setSortBy(sb);
    setSortDir(sd);
    setSearchQuery(q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // State -> URL sync
  useEffect(() => {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('per_page', String(perPage));
    params.set('sort_by', sortBy);
    params.set('sort_dir', sortDir);
    if (searchQuery) params.set('search', searchQuery);
    navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
  }, [page, perPage, sortBy, sortDir, searchQuery, navigate, location.pathname]);
  const handlePageChange = (newPage: number) => setPage(newPage);
  const handleSearch = (e: React.FormEvent) => { e.preventDefault(); fetchOrdersList(); };
  const getStatusColor = (id: number | null, options: FilterOption[]): string => {
    if (!id) return '#6c757d';
    const option = options.find(opt => opt.id === id);
    return option?.color || '#6c757d';
  };
  const getDeliveryDotColor = (name: string | null | undefined): string => {
    const n = (name || '').toLowerCase();
    if (n.includes('нова') || n.includes('нп')) return 'var(--bms-delivery-nova-poshta)';
    if (n.includes('укрпошт')) return 'var(--bms-delivery-ukrposhta)';
    if (n.includes('meest') || n.includes('міст')) return 'var(--bms-delivery-meest)';
    if (n.includes('самовив') || n.includes('pickup')) return 'var(--bms-delivery-pickup)';
    if (n.includes("кур'єр") || n.includes('кур') || n.includes('courier')) return 'var(--bms-delivery-courier)';
    return 'var(--bms-fg-faint)';
  };
  const getOrderItemsCount = (order: OrderWithDetails): number => order.order_items?.length || 0;
  if (loading && orders.length === 0) return <div>Завантаження замовлень...</div>;
  if (error && orders.length === 0) return <div>Помилка: {error}</div>;

  return (
    <div className="p-6 w-full">
      <OrderDetailsModal orderId={detailsId} open={detailsOpen} onClose={() => setDetailsOpen(false)} filterOptions={filterOptions} onSaved={() => { setDetailsOpen(false); fetchOrdersList(); }} />
      <div className="flex flex-col md:flex-row md:justify-between md:items-center gap-4 mb-6">
        <form className="flex flex-1 max-w-xs relative" onSubmit={handleSearch}>
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"><FontAwesomeIcon icon={faSearch} /></span>
          <input
            type="text"
            placeholder="Пошук замовлень..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="pl-10 pr-3 py-2 rounded border border-gray-300 w-full text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
        </form>
        <button type="button" className="flex items-center gap-2 px-4 py-2 rounded border border-gray-300 bg-gray-50 hover:bg-gray-100 text-sm text-gray-700">
          <FontAwesomeIcon icon={faFilter} /> Фільтри
        </button>
      </div>
      <div className="overflow-x-auto overflow-y-auto rounded shadow border border-gray-200 bg-white max-h-[calc(100vh-240px)]">
        {/* Bulk actions */}
        <div className="p-2 flex flex-wrap items-center gap-2 border-b">
          <span className="text-sm text-gray-600">Вибрано: {selectedIds.length}</span>
          <select value={bulkStatusId} onChange={(e) => setBulkStatusId(e.target.value ? Number(e.target.value) : '')} className="px-2 py-1 border rounded text-sm">
            <option value="">Статус замовлення...</option>
            {filterOptions.order_statuses.map(os => (
              <option key={os.id} value={os.id}>{os.name || (os as any).status_name}</option>
            ))}
          </select>
          <button
            disabled={selectedIds.length === 0 || bulkStatusId === ''}
            onClick={async () => { await bulkUpdateOrders(selectedIds, { order_status_id: Number(bulkStatusId) }); setSelectedIds([]); setBulkStatusId(''); fetchOrdersList(); }}
            className="px-3 py-1 text-sm border rounded disabled:opacity-50"
          >
            Застосувати
          </button>
          <button onClick={() => setSelectedIds([])} className="px-3 py-1 text-sm border rounded">Очистити</button>
        </div>
        <table className="min-w-[1600px] xl:min-w-[1800px] 2xl:min-w-[2000px] w-full text-sm [&_th]:text-center [&_td]:text-center">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-2 py-3 text-left"><input type="checkbox" aria-label="select all" onChange={(e) => setSelectedIds(e.target.checked ? orders.map(o=>o.id) : [])} checked={orders.length>0 && selectedIds.length===orders.length} /></th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('id'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Номер</th>
              <th className="px-4 py-3 text-left font-semibold">Клієнт</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('total_amount'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Сума</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('order_date'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Дата замовлення</th>
              <th className="px-4 py-3 text-left font-semibold">Статус</th>
              <th className="px-4 py-3 text-left font-semibold">Оплата</th>
              <th className="px-4 py-3 text-left font-semibold">Доставка</th>
              <th className="px-4 py-3 text-left font-semibold">Трекінг</th>
              <th className="px-4 py-3 text-left font-semibold">Канал</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('priority'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Товарів</th>
              <th className="px-4 py-3 text-left font-semibold">Дії</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 ? (
              <tr>
                <td colSpan={12}><BmsEmpty label="Замовлень не знайдено" /></td>
              </tr>
            ) : (
              orders.map(order => (
                <tr key={order.id} className="border-b last:border-b-0 hover:bg-gray-50">
                  <td className="px-2 py-2"><input type="checkbox" checked={selectedIds.includes(order.id)} onChange={(e) => setSelectedIds(e.target.checked ? [...selectedIds, order.id] : selectedIds.filter(id=>id!==order.id))} /></td>
                  <td className="px-4 py-2 bms-mono">
                    <CopyOnClick value={order.id} display={<>#{order.id}</>} />
                  </td>
                  <td className="px-4 py-2">
                    {order.client_name
                        ? <UnknownIf value={order.client_name} label="Анонімний" />
                        : <span className="text-gray-400 dark:text-gray-500 italic">Анонімний</span>}
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap">{formatPrice(order.total_amount)}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{formatDate(order.order_date)}</td>
                  <td className="px-4 py-2">
                    <div className="flex flex-col gap-1 items-center">
                      <OrderStatusBadge name={order.order_status_name} />
                      {order.deferred_until && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-orange-100 text-orange-700 border border-orange-200" title={`Відкладено до ${formatDate(order.deferred_until)}`}>
                          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                          до {formatDate(order.deferred_until)}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2">
                    <PaymentStatusBadge name={order.payment_status || order.payment_status_name} />
                  </td>
                  <td className="px-4 py-2">
                    {order.delivery_method_name ? (
                      <span className="inline-flex items-center gap-1.5 text-xs">
                        <span className="bms-delivery-dot" style={{ background: getDeliveryDotColor(order.delivery_method_name) }} />
                        {order.delivery_method_name}
                      </span>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {order.tracking_number
                        ? <CopyOnClick value={order.tracking_number} className="bms-mono text-xs" />
                        : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-2">
                    {(() => {
                      const ch = order.sales_channel || 'Ефір';
                      const channelColors: Record<string, string> = {
                        'Telegram': 'bg-blue-100 text-blue-700 border-blue-300',
                        'OLX': 'bg-orange-100 text-orange-700 border-orange-300',
                        'Prom': 'bg-indigo-100 text-indigo-700 border-indigo-300',
                        'Viber': 'bg-violet-100 text-violet-700 border-violet-300',
                        'Instagram': 'bg-pink-100 text-pink-700 border-pink-300',
                        'GRAILED': 'bg-gray-100 text-gray-700 border-gray-300',
                        'Магазин': 'bg-green-100 text-green-700 border-green-300',
                        'Ефір': 'bg-sky-100 text-sky-700 border-sky-300',
                      };
                      return (
                        <span className={`inline-block px-2 py-0.5 rounded border text-xs font-medium whitespace-nowrap ${channelColors[ch] || 'bg-gray-100 text-gray-600 border-gray-300'}`}>
                          {ch}
                        </span>
                      );
                    })()}
                  </td>
                  <td className="px-4 py-2">{getOrderItemsCount(order)}</td>
                  <td className="px-4 py-2">
                    <div className="flex gap-2">
                      <button onClick={() => handleViewOrder(order.id)} title="Перегляд" className="p-1 rounded hover:bg-blue-50 text-gray-600 hover:text-blue-600"><FontAwesomeIcon icon={faEye} /></button>
                      <button onClick={() => handleEditOrder(order.id)} title="Редагувати" className="p-1 rounded hover:bg-yellow-50 text-gray-600 hover:text-yellow-600"><FontAwesomeIcon icon={faEdit} /></button>
                      <button onClick={() => handleDeleteOrder(order.id)} title="Видалити" className="p-1 rounded hover:bg-red-50 text-gray-600 hover:text-red-600"><FontAwesomeIcon icon={faTrash} /></button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="flex justify-center items-center mt-6 mb-2">
        <Pagination
          currentPage={page}
          totalPages={totalPages}
          totalItems={totalOrders}
          itemsPerPage={perPage}
          onPageChange={handlePageChange}
          onPerPageChange={setPerPage}
        />
      </div>
    </div>
  );
};

export default OrdersTable; 