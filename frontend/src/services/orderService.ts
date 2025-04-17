import axios from 'axios';

// Type definitions for Order API
export interface OrderItem {
  id?: number;
  order_id?: number;
  product_id: number;
  quantity: number;
  price: number;
  discount_type?: string | null;
  discount_value?: number | null;
  additional_operation?: string | null;
  additional_operation_value?: number | null;
  notes?: string | null;
  product_number?: string;
  product_name?: string;
}

export interface Order {
  id: number;
  client_id: number;
  order_date: string;
  order_status_id: number | null;
  total_amount: number;
  payment_method_id: number | null;
  payment_status: string | null;
  payment_status_id: number | null;
  delivery_method_id: number | null;
  delivery_address_id: number | null;
  tracking_number: string | null;
  delivery_status_id: number | null;
  notes: string | null;
  deferred_until: string | null;
  priority: number;
  broadcast_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface OrderWithDetails extends Order {
  client_name: string;
  order_status_name: string | null;
  payment_status_name: string | null;
  payment_method_name: string | null;
  delivery_method_name: string | null;
  delivery_status_name: string | null;
  delivery_address_details?: AddressDetails | null;
  broadcast_name: string | null;
  order_items: OrderItem[];
}

export interface AddressDetails {
  id: number;
  city: string;
  street: string;
  building: string;
  apartment: string;
  postal_code: string;
  notes: string | null;
}

export interface OrdersResponse {
  items: OrderWithDetails[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface OrderFilters {
  order_status_ids?: number[];
  payment_status_ids?: number[];
  payment_method_ids?: number[];
  delivery_method_ids?: number[];
  delivery_status_ids?: number[];
  client_id?: number;
  date_from?: string;
  date_to?: string;
  month_min?: number;
  month_max?: number;
  year_min?: number;
  year_max?: number;
  search?: string;
  priority_min?: number;
  priority_max?: number;
  has_tracking?: boolean;
  is_deferred?: boolean;
}

export interface FilterOption {
  id: number;
  name: string;
  color?: string;
}

export interface FilterOptions {
  order_statuses: FilterOption[];
  payment_statuses: FilterOption[];
  payment_methods: FilterOption[];
  delivery_methods: FilterOption[];
  delivery_statuses: FilterOption[];
  clients: {
    id: number;
    name: string;
  }[];
}

export interface OrderCreate {
  client_id: number;
  order_date?: string;
  order_status_id?: number | null;
  total_amount?: number;
  payment_method_id?: number | null;
  payment_status?: string | null;
  payment_status_id?: number | null;
  delivery_method_id?: number | null;
  delivery_address_id?: number | null;
  tracking_number?: string | null;
  delivery_status_id?: number | null;
  notes?: string | null;
  deferred_until?: string | null;
  priority?: number;
  broadcast_id?: number | null;
  order_items: OrderItem[];
}

export interface OrderUpdate {
  client_id?: number;
  order_date?: string;
  order_status_id?: number | null;
  total_amount?: number;
  payment_method_id?: number | null;
  payment_status?: string | null;
  payment_status_id?: number | null;
  delivery_method_id?: number | null;
  delivery_address_id?: number | null;
  tracking_number?: string | null;
  delivery_status_id?: number | null;
  notes?: string | null;
  deferred_until?: string | null;
  priority?: number;
  broadcast_id?: number | null;
  order_items?: OrderItem[];
}

// API functions
export const fetchOrders = async (
  page: number = 1,
  per_page: number = 20,
  filters: OrderFilters = {}
): Promise<OrdersResponse> => {
  // Build query parameters
  const params = new URLSearchParams();
  params.append('page', page.toString());
  params.append('per_page', per_page.toString());
  
  // Add filters to query parameters
  if (filters.search) params.append('search', filters.search);
  if (filters.client_id !== undefined) params.append('client_id', filters.client_id.toString());
  if (filters.date_from) params.append('date_from', filters.date_from);
  if (filters.date_to) params.append('date_to', filters.date_to);
  if (filters.month_min !== undefined) params.append('month_min', filters.month_min.toString());
  if (filters.month_max !== undefined) params.append('month_max', filters.month_max.toString());
  if (filters.year_min !== undefined) params.append('year_min', filters.year_min.toString());
  if (filters.year_max !== undefined) params.append('year_max', filters.year_max.toString());
  if (filters.priority_min !== undefined) params.append('priority_min', filters.priority_min.toString());
  if (filters.priority_max !== undefined) params.append('priority_max', filters.priority_max.toString());
  if (filters.has_tracking !== undefined) params.append('has_tracking', filters.has_tracking.toString());
  if (filters.is_deferred !== undefined) params.append('is_deferred', filters.is_deferred.toString());
  
  // Add array filters
  if (filters.order_status_ids && filters.order_status_ids.length > 0) {
    filters.order_status_ids.forEach(id => params.append('order_status_ids', id.toString()));
  }
  
  if (filters.payment_status_ids && filters.payment_status_ids.length > 0) {
    filters.payment_status_ids.forEach(id => params.append('payment_status_ids', id.toString()));
  }
  
  if (filters.payment_method_ids && filters.payment_method_ids.length > 0) {
    filters.payment_method_ids.forEach(id => params.append('payment_method_ids', id.toString()));
  }
  
  if (filters.delivery_method_ids && filters.delivery_method_ids.length > 0) {
    filters.delivery_method_ids.forEach(id => params.append('delivery_method_ids', id.toString()));
  }
  
  if (filters.delivery_status_ids && filters.delivery_status_ids.length > 0) {
    filters.delivery_status_ids.forEach(id => params.append('delivery_status_ids', id.toString()));
  }
  
  try {
    const response = await axios.get(`/api/orders?${params.toString()}`);
    return response.data;
  } catch (error) {
    console.error('Error fetching orders:', error);
    throw error;
  }
};

export const fetchOrderFilters = async (): Promise<FilterOptions> => {
  try {
    const response = await axios.get('/api/orders/filters');
    return response.data;
  } catch (error) {
    console.error('Error fetching order filter options:', error);
    throw error;
  }
};

export const fetchOrder = async (id: number): Promise<OrderWithDetails> => {
  try {
    const response = await axios.get(`/api/orders/${id}`);
    return response.data;
  } catch (error) {
    console.error(`Error fetching order with id ${id}:`, error);
    throw error;
  }
};

export const createOrder = async (order: OrderCreate): Promise<OrderWithDetails> => {
  try {
    const response = await axios.post('/api/orders', order);
    return response.data;
  } catch (error) {
    console.error('Error creating order:', error);
    throw error;
  }
};

export const updateOrder = async (id: number, order: OrderUpdate): Promise<OrderWithDetails> => {
  try {
    const response = await axios.put(`/api/orders/${id}`, order);
    return response.data;
  } catch (error) {
    console.error(`Error updating order with id ${id}:`, error);
    throw error;
  }
};

export const deleteOrder = async (id: number): Promise<{ message: string, id: number }> => {
  try {
    const response = await axios.delete(`/api/orders/${id}`);
    return response.data;
  } catch (error) {
    console.error(`Error deleting order with id ${id}:`, error);
    throw error;
  }
}; 