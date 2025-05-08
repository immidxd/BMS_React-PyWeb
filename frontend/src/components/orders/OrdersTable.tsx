import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
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
  OrderFilters 
} from '../../services/orderService';
import Pagination from '../common/Pagination';

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

  const handleViewOrder = (orderId: number) => {
    if (onViewOrder) onViewOrder(orderId);
    else navigate(`/orders/${orderId}`);
  };
  const handleEditOrder = (orderId: number) => {
    if (onEditOrder) onEditOrder(orderId);
    else navigate(`/orders/${orderId}/edit`);
  };
  const handleDeleteOrder = async (orderId: number) => {
    if (window.confirm('Ви впевнені, що хочете видалити це замовлення?')) {
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
      const response = await fetchOrders(page, perPage, queryFilters);
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
  useEffect(() => { fetchOrdersList(); }, [page, perPage, filters]);
  const handlePageChange = (newPage: number) => setPage(newPage);
  const handleSearch = (e: React.FormEvent) => { e.preventDefault(); fetchOrdersList(); };
  const getStatusColor = (id: number | null, options: FilterOption[]): string => {
    if (!id) return '#6c757d';
    const option = options.find(opt => opt.id === id);
    return option?.color || '#6c757d';
  };
  const getOrderItemsCount = (order: OrderWithDetails): number => order.order_items?.length || 0;
  if (loading && orders.length === 0) return <div>Завантаження замовлень...</div>;
  if (error && orders.length === 0) return <div>Помилка: {error}</div>;

  return (
    <div className="p-6 w-full">
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
      <div className="overflow-x-auto rounded shadow border border-gray-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-4 py-3 text-left font-semibold">№</th>
              <th className="px-4 py-3 text-left font-semibold">Клієнт</th>
              <th className="px-4 py-3 text-left font-semibold">Сума</th>
              <th className="px-4 py-3 text-left font-semibold">Дата замовлення</th>
              <th className="px-4 py-3 text-left font-semibold">Статус</th>
              <th className="px-4 py-3 text-left font-semibold">Оплата</th>
              <th className="px-4 py-3 text-left font-semibold">Доставка</th>
              <th className="px-4 py-3 text-left font-semibold">Трекінг</th>
              <th className="px-4 py-3 text-left font-semibold">Товарів</th>
              <th className="px-4 py-3 text-left font-semibold">Дії</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 ? (
              <tr>
                <td colSpan={10} className="text-center py-8 text-gray-400">Замовлень не знайдено</td>
              </tr>
            ) : (
              orders.map(order => (
                <tr key={order.id} className="border-b last:border-b-0 hover:bg-gray-50">
                  <td className="px-4 py-2">{order.id}</td>
                  <td className="px-4 py-2">{order.client_name}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{formatPrice(order.total_amount)}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{formatDate(order.order_date)}</td>
                  <td className="px-4 py-2">
                    <span className="inline-block px-2 py-1 rounded text-xs font-semibold" style={{background:getStatusColor(order.order_status_id, filterOptions.order_statuses),color:'#fff'}}>
                      {order.order_status_name || 'Не вказано'}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span className="inline-block px-2 py-1 rounded text-xs font-semibold" style={{background:getStatusColor(order.payment_status_id, filterOptions.payment_statuses),color:'#fff'}}>
                      {order.payment_status || order.payment_status_name || 'Не вказано'}
                    </span>
                  </td>
                  <td className="px-4 py-2">{order.delivery_method_name || 'Не вказано'}</td>
                  <td className="px-4 py-2">{order.tracking_number || '—'}</td>
                  <td className="px-4 py-2 text-center">{getOrderItemsCount(order)}</td>
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