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
};
