import axios from 'axios';

export interface SalesPeriodData {
  period: string;
  orders: number;
  items_sold: number;
  revenue: number;
  cost: number;
  profit: number;
}

export interface SalesStatsResponse {
  period_type: string;
  data: SalesPeriodData[];
}

export interface ShipmentPeriodData {
  period: string;
  shipments: number;
  items: number;
  total_cost: number;
  avg_price: number;
  revenue: number;
  profit: number;
  sold_items: number;
  sell_rate: number;
}

export interface ShipmentsStatsResponse {
  period_type: string;
  data: ShipmentPeriodData[];
}

export interface SupplierTotalData {
  id: number;
  name: string;
  product_count: number;
  total_cost: number;
  avg_price: number;
  revenue: number;
  sold_items: number;
}

export interface SupplierPeriodData {
  supplier_name: string;
  period_label: string;
  total_cost: number;
  items_count: number;
  avg_price: number;
}

export interface SuppliersStatsResponse {
  period_type: string;
  data: (SupplierTotalData | SupplierPeriodData)[];
}

export interface SummaryStats {
  total_products: number;
  total_orders: number;
  products_sold: number;
  total_revenue: number;
  total_purchase_cost: number;
  total_inventory_cost: number;
  total_suppliers: number;
  total_shipments: number;
  total_shipment_cost: number;
}

export interface YearsResponse {
  years: number[];
}

// Delivery detail stats
export interface DeliveryDetailStats {
  delivery: { id: number; deliveryname: string; deliverydate: string | null; delivery_cost: number; supplier_name: string | null };
  total_pairs: number;
  sold_count: number;
  remaining_count: number;
  sell_rate: number;
  purchase_cost: number;
  delivery_cost: number;
  total_cost: number;
  cost_per_pair: number;
  revenue: number;
  net_revenue: number;
  size_distribution: { size: string; count: number }[];
  measurement_distribution: { measurement: string; count: number }[];
  type_distribution: { type_name: string; count: number }[];
  status_distribution: { status_name: string; count: number }[];
}

// Deliveries list
export interface DeliveryListItem {
  id: number;
  deliveryname: string;
  deliverydate: string | null;
  delivery_cost: number;
  supplier_name: string | null;
  total_pairs: number;
  purchase_cost: number;
  sold_count: number;
  sell_rate: number;
  revenue: number;
  profit: number;
}

export interface DeliveriesListResponse {
  items: DeliveryListItem[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

// Supplier detail stats
export interface SupplierDetailStats {
  supplier: { id: number; name: string };
  total_deliveries: number;
  total_products: number;
  total_spent: number;
  revenue: number;
  profit: number;
  sell_through_rate: number;
  sold_items: number;
  top_brands: { name: string; count: number }[];
  top_types: { name: string; count: number }[];
  monthly_trend: { month: string; products: number; cost: number; revenue: number }[];
}

// Client stats
export interface ClientsStatsResponse {
  top_by_revenue: { id: number; name: string; orders_count: number; total_revenue: number }[];
  top_by_orders: { id: number; name: string; orders_count: number; total_revenue: number }[];
  new_clients_trend: { month: string; new_clients: number }[];
  avg_check_trend: { month: string; avg_check: number; orders_count: number }[];
  rating_distribution: { category: string; count: number }[];
}

export const statisticsService = {
  async getSalesStats(period: string = 'month', year?: number, supplierId?: number): Promise<SalesStatsResponse> {
    const params = new URLSearchParams({ period });
    if (year) params.append('year', String(year));
    if (supplierId) params.append('supplier_id', String(supplierId));
    const res = await axios.get(`/api/statistics/sales?${params}`);
    return res.data;
  },

  async getShipmentsStats(period: string = 'month', year?: number, supplierId?: number): Promise<ShipmentsStatsResponse> {
    const params = new URLSearchParams({ period });
    if (year) params.append('year', String(year));
    if (supplierId) params.append('supplier_id', String(supplierId));
    const res = await axios.get(`/api/statistics/shipments?${params}`);
    return res.data;
  },

  async getSuppliersStats(period: string = 'total', year?: number, limit?: number): Promise<SuppliersStatsResponse> {
    const params = new URLSearchParams({ period });
    if (year) params.append('year', String(year));
    if (limit) params.append('limit', String(limit));
    const res = await axios.get(`/api/statistics/suppliers?${params}`);
    return res.data;
  },

  async getSummary(): Promise<SummaryStats> {
    const res = await axios.get('/api/statistics/summary');
    return res.data;
  },

  async getYears(): Promise<YearsResponse> {
    const res = await axios.get('/api/statistics/years');
    return res.data;
  },

  async getDeliveryDetail(deliveryId: number): Promise<DeliveryDetailStats> {
    const res = await axios.get(`/api/statistics/delivery/${deliveryId}`);
    return res.data;
  },

  async getDeliveriesList(page = 1, perPage = 20, supplierId?: number, year?: number): Promise<DeliveriesListResponse> {
    const params = new URLSearchParams({ page: String(page), per_page: String(perPage) });
    if (supplierId) params.append('supplier_id', String(supplierId));
    if (year) params.append('year', String(year));
    const res = await axios.get(`/api/statistics/deliveries?${params}`);
    return res.data;
  },

  async getSupplierDetail(supplierId: number): Promise<SupplierDetailStats> {
    const res = await axios.get(`/api/statistics/supplier/${supplierId}`);
    return res.data;
  },

  async getClientsStats(limit = 15): Promise<ClientsStatsResponse> {
    const res = await axios.get(`/api/statistics/clients?limit=${limit}`);
    return res.data;
  },
};
