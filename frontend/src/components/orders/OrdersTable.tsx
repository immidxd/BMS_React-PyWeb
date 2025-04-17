import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import styled from 'styled-components';
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

// Styled components
const Container = styled.div`
  padding: 20px;
`;

const SearchContainer = styled.div`
  display: flex;
  justify-content: space-between;
  margin-bottom: 20px;
  align-items: center;
`;

const SearchInput = styled.div`
  flex: 1;
  max-width: 300px;
  position: relative;
  
  input {
    padding: 10px 15px;
    border-radius: 4px;
    border: 1px solid #dee2e6;
    width: 100%;
    font-size: 14px;
    padding-left: 35px;
    
    &:focus {
      outline: none;
      border-color: #80bdff;
      box-shadow: 0 0 0 0.2rem rgba(0, 123, 255, 0.25);
    }
  }
  
  svg {
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    color: #6c757d;
  }
`;

const FilterButton = styled.button`
  background-color: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 4px;
  padding: 10px 15px;
  display: flex;
  align-items: center;
  cursor: pointer;
  
  &:hover {
    background-color: #e9ecef;
  }
  
  svg {
    margin-right: 8px;
  }
`;

const Table = styled.table`
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.24);
`;

const TableHead = styled.thead`
  background-color: #f8f9fa;
  border-bottom: 2px solid #dee2e6;
`;

const TableHeaderCell = styled.th`
  padding: 12px 15px;
  text-align: left;
  font-weight: 600;
  position: sticky;
  top: 0;
  background-color: #f8f9fa;
  z-index: 1;
`;

const TableBody = styled.tbody``;

const TableRow = styled.tr`
  &:nth-child(even) {
    background-color: #f2f2f2;
  }
  
  &:hover {
    background-color: #e9ecef;
  }
  
  border-bottom: 1px solid #dee2e6;
`;

const TableCell = styled.td`
  padding: 10px 15px;
`;

const ActionButtonsContainer = styled.div`
  display: flex;
  gap: 10px;
`;

const ActionButton = styled.button`
  background: none;
  border: none;
  cursor: pointer;
  padding: 5px;
  color: #6c757d;
  
  &:hover {
    color: #007bff;
  }
`;

const StatusBadge = styled.span<{ color?: string }>`
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  background-color: ${props => props.color || '#6c757d'};
  color: white;
`;

const PaginationContainer = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 20px;
`;

const PaginationInfo = styled.div`
  font-size: 14px;
  color: #6c757d;
`;

const PaginationButtons = styled.div`
  display: flex;
  gap: 5px;
`;

const PageButton = styled.button<{ active?: boolean }>`
  background-color: ${props => props.active ? '#007bff' : '#f8f9fa'};
  color: ${props => props.active ? 'white' : '#212529'};
  border: 1px solid #dee2e6;
  border-radius: 4px;
  padding: 6px 12px;
  cursor: ${props => props.active ? 'default' : 'pointer'};
  
  &:hover {
    background-color: ${props => props.active ? '#007bff' : '#e9ecef'};
  }
  
  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
`;

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
  
  // Handle navigation
  const handleViewOrder = (orderId: number) => {
    if (onViewOrder) {
      onViewOrder(orderId);
    } else {
      navigate(`/orders/${orderId}`);
    }
  };
  
  const handleEditOrder = (orderId: number) => {
    if (onEditOrder) {
      onEditOrder(orderId);
    } else {
      navigate(`/orders/${orderId}/edit`);
    }
  };
  
  const handleDeleteOrder = async (orderId: number) => {
    if (window.confirm('Ви впевнені, що хочете видалити це замовлення?')) {
      try {
        await deleteOrder(orderId);
        
        // Refresh the orders list
        fetchOrdersList();
        
        if (onDeleteOrder) {
          onDeleteOrder(orderId);
        }
      } catch (err) {
        setError('Помилка при видаленні замовлення');
        console.error('Error deleting order:', err);
      }
    }
  };
  
  // Format helpers
  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '—';
    try {
      return format(new Date(dateStr), 'dd.MM.yyyy');
    } catch (err) {
      return dateStr;
    }
  };
  
  const formatPrice = (price: number) => {
    return new Intl.NumberFormat('uk-UA', {
      style: 'currency',
      currency: 'UAH'
    }).format(price);
  };
  
  // Fetch orders with current pagination and filters
  const fetchOrdersList = async () => {
    setLoading(true);
    setError(null);
    
    try {
      // Apply search query to filters
      const queryFilters = { ...filters };
      if (searchQuery) {
        queryFilters.search = searchQuery;
      }
      
      const response = await fetchOrders(page, perPage, queryFilters);
      setOrders(response.items);
      setTotalOrders(response.total);
      setTotalPages(response.pages);
    } catch (err) {
      setError('Помилка при завантаженні замовлень');
      console.error('Error fetching orders:', err);
    } finally {
      setLoading(false);
    }
  };
  
  // Fetch filter options
  const loadFilterOptions = async () => {
    try {
      const options = await fetchOrderFilters();
      setFilterOptions(options);
    } catch (err) {
      console.error('Error fetching filter options:', err);
    }
  };
  
  // Load data on component mount and when dependencies change
  useEffect(() => {
    loadFilterOptions();
  }, []);
  
  useEffect(() => {
    fetchOrdersList();
  }, [page, perPage, filters]);
  
  // Pagination controls
  const handlePageChange = (newPage: number) => {
    setPage(newPage);
  };
  
  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchOrdersList();
  };
  
  // Get status badge color from options
  const getStatusColor = (id: number | null, options: FilterOption[]): string => {
    if (!id) return '#6c757d';
    const option = options.find(opt => opt.id === id);
    return option?.color || '#6c757d';
  };
  
  // Render the order items count
  const getOrderItemsCount = (order: OrderWithDetails): number => {
    return order.order_items?.length || 0;
  };
  
  // Render loading state
  if (loading && orders.length === 0) {
    return <div>Завантаження замовлень...</div>;
  }
  
  // Render error state
  if (error && orders.length === 0) {
    return <div>Помилка: {error}</div>;
  }
  
  return (
    <Container>
      <SearchContainer>
        <SearchInput>
          <FontAwesomeIcon icon={faSearch} />
          <input 
            type="text" 
            placeholder="Пошук замовлень..." 
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleSearch(e)}
          />
        </SearchInput>
        
        <FilterButton>
          <FontAwesomeIcon icon={faFilter} />
          Фільтри
        </FilterButton>
      </SearchContainer>
      
      <Table>
        <TableHead>
          <tr>
            <TableHeaderCell>№</TableHeaderCell>
            <TableHeaderCell>Клієнт</TableHeaderCell>
            <TableHeaderCell>Сума</TableHeaderCell>
            <TableHeaderCell>Дата замовлення</TableHeaderCell>
            <TableHeaderCell>Статус</TableHeaderCell>
            <TableHeaderCell>Оплата</TableHeaderCell>
            <TableHeaderCell>Доставка</TableHeaderCell>
            <TableHeaderCell>Трекінг</TableHeaderCell>
            <TableHeaderCell>Товарів</TableHeaderCell>
            <TableHeaderCell>Дії</TableHeaderCell>
          </tr>
        </TableHead>
        <TableBody>
          {orders.length === 0 ? (
            <TableRow>
              <TableCell colSpan={10} style={{ textAlign: 'center' }}>
                Замовлень не знайдено
              </TableCell>
            </TableRow>
          ) : (
            orders.map(order => (
              <TableRow key={order.id}>
                <TableCell>{order.id}</TableCell>
                <TableCell>{order.client_name}</TableCell>
                <TableCell>{formatPrice(order.total_amount)}</TableCell>
                <TableCell>{formatDate(order.order_date)}</TableCell>
                <TableCell>
                  <StatusBadge color={getStatusColor(order.order_status_id, filterOptions.order_statuses)}>
                    {order.order_status_name || 'Не вказано'}
                  </StatusBadge>
                </TableCell>
                <TableCell>
                  <StatusBadge color={getStatusColor(order.payment_status_id, filterOptions.payment_statuses)}>
                    {order.payment_status || order.payment_status_name || 'Не вказано'}
                  </StatusBadge>
                </TableCell>
                <TableCell>
                  {order.delivery_method_name || 'Не вказано'}
                </TableCell>
                <TableCell>
                  {order.tracking_number || '—'}
                </TableCell>
                <TableCell>{getOrderItemsCount(order)}</TableCell>
                <TableCell>
                  <ActionButtonsContainer>
                    <ActionButton onClick={() => handleViewOrder(order.id)} title="Перегляд">
                      <FontAwesomeIcon icon={faEye} />
                    </ActionButton>
                    <ActionButton onClick={() => handleEditOrder(order.id)} title="Редагувати">
                      <FontAwesomeIcon icon={faEdit} />
                    </ActionButton>
                    <ActionButton onClick={() => handleDeleteOrder(order.id)} title="Видалити">
                      <FontAwesomeIcon icon={faTrash} />
                    </ActionButton>
                  </ActionButtonsContainer>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
      
      <PaginationContainer>
        <PaginationInfo>
          Показано {orders.length} з {totalOrders} замовлень
        </PaginationInfo>
        
        <PaginationButtons>
          <PageButton 
            onClick={() => handlePageChange(1)} 
            disabled={page === 1}
          >
            &laquo;
          </PageButton>
          
          <PageButton 
            onClick={() => handlePageChange(page - 1)} 
            disabled={page === 1}
          >
            &lsaquo;
          </PageButton>
          
          {/* Generate page buttons */}
          {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
            // Show pages around current page
            let pageNum;
            if (totalPages <= 5) {
              pageNum = i + 1;
            } else if (page <= 3) {
              pageNum = i + 1;
            } else if (page >= totalPages - 2) {
              pageNum = totalPages - 4 + i;
            } else {
              pageNum = page - 2 + i;
            }
            
            return (
              <PageButton 
                key={pageNum}
                active={pageNum === page} 
                onClick={() => handlePageChange(pageNum)}
              >
                {pageNum}
              </PageButton>
            );
          })}
          
          <PageButton 
            onClick={() => handlePageChange(page + 1)} 
            disabled={page === totalPages}
          >
            &rsaquo;
          </PageButton>
          
          <PageButton 
            onClick={() => handlePageChange(totalPages)} 
            disabled={page === totalPages}
          >
            &raquo;
          </PageButton>
        </PaginationButtons>
      </PaginationContainer>
    </Container>
  );
};

export default OrdersTable; 