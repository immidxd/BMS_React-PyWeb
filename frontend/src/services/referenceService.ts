import axios from 'axios';

// Type definitions for reference data
export interface ReferenceItem {
  id: number;
  name: string;
}

export interface ReferenceItemWithColor extends ReferenceItem {
  description: string | null;
  color_code: string | null;
}

export interface Gender extends ReferenceItem {}

export interface OrderStatus extends ReferenceItemWithColor {}

export interface PaymentStatus extends ReferenceItemWithColor {}

export interface DeliveryMethod extends ReferenceItemWithColor {}

export interface Client {
  id: number;
  first_name: string;
  last_name: string;
  full_name: string;
  phone_number: string | null;
  email: string | null;
  gender_id: number | null;
  address: string | null;
  notes: string | null;
  order_count?: number;
  total_order_amount?: number;
  average_order_value?: number;
  city_of_residence?: string | null;
  confirmed_orders: number;
  cancelled_count: number;
  ignored_count: number;
  return_exchange_count: number;
  has_deferred: boolean;
  rating: number | null;
  // Step 4: identity flags
  has_active_flags?: boolean;
  top_flag_type?: string | null;
  manually_edited_at?: string | null;
}

export interface ReferenceList<T> {
  items: T[];
}

export interface ClientList {
  items: Client[];
  total: number;
}

export interface Supplier {
  id: number;
  name: string;
  notes: string | null;
  product_count: number;
  shipments_count: number;
  total_spent: number;
  avg_price: number;
  top_brands: string | null;
  aliases?: { id: number; alias_name: string }[];
  revenue?: number;
  group_id: number | null;
  group_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SupplierGroup {
  id: number;
  name: string;
  country: string | null;
  description: string | null;
  supplier_count: number;
}

export interface SupplierList {
  items: Supplier[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface Shipment {
  id: number;
  sheet_name: string | null;
  shipment_date: string | null;
  supplier_id: number | null;
  supplier_name: string | null;
  items_count: number;
  total_cost: number;
  delivery_cost: number | null;
  notes: string | null;
  group_id: number | null;
  group_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ShipmentList {
  items: Shipment[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface ShipmentGroup {
  id: number;
  name: string;
  notes: string | null;
  shipments_count: number;
  total_cost: number;
  total_items: number;
  created_at: string | null;
  updated_at: string | null;
}

// API functions
export const fetchGenders = async (): Promise<ReferenceList<Gender>> => {
  const response = await axios.get('/api/genders');
  return response.data;
};

export const fetchOrderStatuses = async (): Promise<ReferenceList<OrderStatus>> => {
  const response = await axios.get('/api/order-statuses');
  return response.data;
};

export const fetchPaymentStatuses = async (): Promise<ReferenceList<PaymentStatus>> => {
  const response = await axios.get('/api/payment-statuses');
  return response.data;
};

export const fetchDeliveryMethods = async (): Promise<ReferenceList<DeliveryMethod>> => {
  const response = await axios.get('/api/delivery-methods');
  return response.data;
};

export const fetchClients = async (
  search?: string,
  gender_id?: number,
  page: number = 1,
  perPage: number = 20
): Promise<ClientList> => {
  const params = new URLSearchParams();
  params.append('page', page.toString());
  params.append('per_page', perPage.toString());
  
  if (search) params.append('search', search);
  if (gender_id !== undefined) params.append('gender_id', gender_id.toString());
  
  const response = await axios.get(`/api/clients?${params.toString()}`);
  return response.data;
};

export const fetchClient = async (id: number): Promise<Client> => {
  const response = await axios.get(`/api/clients/${id}`);
  return response.data;
};

export const createClient = async (client: {
  first_name: string;
  last_name: string;
  phone_number?: string;
  email?: string;
  gender_id?: number;
  address?: string;
  notes?: string;
}): Promise<Client> => {
  const response = await axios.post('/api/clients', client);
  return response.data;
};

export const updateClient = async (
  id: number,
  client: Partial<{
    first_name: string;
    last_name: string;
    middle_name: string;
    maiden_name: string;
    nickname: string;
    phone_number: string;
    email: string;
    gender_id: number;
    notes: string;
    city_of_residence: string;
    client_discount: number;
    bonus_account: number;
    facebook: string;
    instagram: string;
    telegram: string;
    viber: string;
    messenger: string;
    tiktok: string;
    olx: string;
  }>
): Promise<Client> => {
  const response = await axios.put(`/api/clients/${id}`, client);
  return response.data;
};

export const deleteClient = async (id: number): Promise<{ message: string }> => {
  const response = await axios.delete(`/api/clients/${id}`);
  return response.data;
};

// ── Client Addresses ────────────────────────────────────────────────────────
export interface ClientAddress {
  id: number;
  client_id: number;
  label?: string | null;
  delivery_type: string; // np_warehouse | np_courier | up_warehouse | up_courier | pickup | other
  recipient_name?: string | null;
  recipient_phone?: string | null;
  city?: string | null;
  city_ref?: string | null;
  region?: string | null;
  warehouse_number?: string | null;
  warehouse_ref?: string | null;
  street?: string | null;
  building?: string | null;
  apartment?: string | null;
  postal_code?: string | null;
  is_primary: boolean;
  is_active: boolean;
  notes?: string | null;
  source?: string | null;
  source_order_id?: number | null;
  fingerprint?: string | null;
  usage_count: number;
  last_used_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export const fetchClientAddresses = async (clientId: number): Promise<ClientAddress[]> => {
  const response = await axios.get(`/api/clients/${clientId}/addresses`);
  return response.data;
};

export const createClientAddress = async (
  clientId: number,
  data: Partial<ClientAddress>,
): Promise<ClientAddress> => {
  const response = await axios.post(`/api/clients/${clientId}/addresses`, data);
  return response.data;
};

export const updateClientAddress = async (
  clientId: number,
  addressId: number,
  data: Partial<ClientAddress>,
): Promise<ClientAddress> => {
  const response = await axios.put(`/api/clients/${clientId}/addresses/${addressId}`, data);
  return response.data;
};

export const deleteClientAddress = async (
  clientId: number,
  addressId: number,
): Promise<{ ok: boolean }> => {
  const response = await axios.delete(`/api/clients/${clientId}/addresses/${addressId}`);
  return response.data;
};

export const setPrimaryClientAddress = async (
  clientId: number,
  addressId: number,
): Promise<ClientAddress> => {
  const response = await axios.post(`/api/clients/${clientId}/addresses/${addressId}/set-primary`);
  return response.data;
};

export const importClientAddressesFromOrders = async (
  clientId: number,
): Promise<{ ok: boolean; imported: number; skipped: number }> => {
  const response = await axios.post(`/api/clients/${clientId}/addresses/import-from-orders`);
  return response.data;
};

// ── Client Relations (родичі / друзі / разом замовляють) ───────────────────
export type RelationType = 'together' | 'family' | 'friend' | 'spouse' | 'other';

export interface ClientRelation {
  id: number;
  client_id: number;
  related_id: number;
  related_full_name?: string | null;
  relation_type: RelationType;
  label?: string | null;
  source?: string | null;        // 'manual' | 'order_import'
  confirmed: boolean;
  notes?: string | null;
  joint_orders: number;
  last_order_id?: number | null;
  last_order_date?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export const fetchClientRelations = async (clientId: number): Promise<ClientRelation[]> => {
  const r = await axios.get(`/api/clients/${clientId}/relations`);
  return r.data;
};

export const createClientRelation = async (
  clientId: number,
  data: { related_id: number; relation_type?: RelationType; label?: string; notes?: string },
): Promise<{ ok: boolean }> => {
  const r = await axios.post(`/api/clients/${clientId}/relations`, data);
  return r.data;
};

export const updateClientRelation = async (
  clientId: number,
  relationId: number,
  data: Partial<{ relation_type: RelationType; label: string; notes: string; confirmed: boolean }>,
): Promise<{ ok: boolean; id: number }> => {
  const r = await axios.put(`/api/clients/${clientId}/relations/${relationId}`, data);
  return r.data;
};

export const deleteClientRelation = async (
  clientId: number,
  relationId: number,
  both: boolean = true,
): Promise<{ ok: boolean }> => {
  const r = await axios.delete(`/api/clients/${clientId}/relations/${relationId}`, { params: { both } });
  return r.data;
};

export const importClientRelationsFromOrders = async (
  clientId: number,
): Promise<{ ok: boolean; matches: number; pairs_processed: number }> => {
  const r = await axios.post(`/api/clients/${clientId}/relations/import-from-orders`);
  return r.data;
};


export const fetchSuppliers = async (
  search?: string,
  page: number = 1,
  perPage: number = 100,
  sortBy: 'id' | 'name' | 'product_count' = 'name',
  sortDir: 'asc' | 'desc' = 'asc',
): Promise<SupplierList> => {
  const params = new URLSearchParams();
  params.append('page', String(page));
  params.append('per_page', String(perPage));
  if (search) params.append('search', search);
  if (sortBy) params.append('sort_by', sortBy);
  if (sortDir) params.append('sort_dir', sortDir);
  const response = await axios.get(`/api/suppliers?${params.toString()}`);
  return response.data;
};

export const mergeSuppliers = async (targetId: number, sourceIds: number[], newName?: string): Promise<any> => {
  const response = await axios.post('/api/suppliers/merge', {
    target_id: targetId,
    source_ids: sourceIds,
    ...(newName ? { new_name: newName } : {}),
  });
  return response.data;
};

export const updateSupplier = async (id: number, data: { name?: string; notes?: string; group_id?: number | null }): Promise<Supplier> => {
  const response = await axios.put(`/api/suppliers/${id}`, data);
  return response.data;
};

export const deleteSupplier = async (id: number): Promise<any> => {
  const response = await axios.delete(`/api/suppliers/${id}`);
  return response.data;
};

export const fetchSupplierAliases = async (supplierId: number): Promise<{ id: number; alias_name: string; delivery_count: number }[]> => {
  const response = await axios.get(`/api/suppliers/${supplierId}/aliases`);
  return response.data;
};

export const addSupplierAlias = async (supplierId: number, aliasName: string): Promise<any> => {
  const response = await axios.post(`/api/suppliers/${supplierId}/aliases`, { alias_name: aliasName });
  return response.data;
};

export const deleteSupplierAlias = async (aliasId: number): Promise<any> => {
  const response = await axios.delete(`/api/suppliers/aliases/${aliasId}`);
  return response.data;
};

export const splitSupplier = async (aliasId: number): Promise<{
  ok: boolean;
  new_supplier_id: number;
  alias_name: string;
  moved_deliveries: number;
}> => {
  const response = await axios.post('/api/suppliers/split', { alias_id: aliasId });
  return response.data;
};

// ── Supplier Groups ─────────────────────────────────────────────────────────

export const fetchSupplierGroups = async (): Promise<SupplierGroup[]> => {
  const response = await axios.get('/api/supplier-groups');
  return response.data;
};

export const createSupplierGroup = async (data: { name: string; country?: string; description?: string }): Promise<{ ok: boolean; id: number }> => {
  const response = await axios.post('/api/supplier-groups', data);
  return response.data;
};

export const updateSupplierGroup = async (id: number, data: { name?: string; country?: string; description?: string }): Promise<any> => {
  const response = await axios.put(`/api/supplier-groups/${id}`, data);
  return response.data;
};

export const deleteSupplierGroup = async (id: number): Promise<any> => {
  const response = await axios.delete(`/api/supplier-groups/${id}`);
  return response.data;
};

// ── Shipments ────────────────────────────────────────────────────────────────
export const fetchShipments = async (
  search?: string,
  page: number = 1,
  perPage: number = 50,
  sortBy: string = 'shipment_date',
  sortDir: string = 'desc',
  supplierId?: number,
  groupId?: number,
): Promise<ShipmentList> => {
  const params = new URLSearchParams();
  params.append('page', String(page));
  params.append('per_page', String(perPage));
  if (search) params.append('search', search);
  if (sortBy) params.append('sort_by', sortBy);
  if (sortDir) params.append('sort_dir', sortDir);
  if (supplierId) params.append('supplier_id', String(supplierId));
  if (groupId) params.append('group_id', String(groupId));
  const response = await axios.get(`/api/shipments?${params.toString()}`);
  return response.data;
};

export const updateShipment = async (id: number, data: { notes?: string; delivery_cost?: number; group_id?: number | null }): Promise<any> => {
  const response = await axios.put(`/api/shipments/${id}`, data);
  return response.data;
};

export const fetchShipmentGroups = async (): Promise<ShipmentGroup[]> => {
  const response = await axios.get('/api/shipment-groups');
  return response.data;
};

export const createShipmentGroup = async (name: string, shipmentIds?: number[], notes?: string): Promise<any> => {
  const response = await axios.post('/api/shipment-groups', { name, shipment_ids: shipmentIds, notes });
  return response.data;
};

export const deleteShipmentGroup = async (id: number): Promise<any> => {
  const response = await axios.delete(`/api/shipment-groups/${id}`);
  return response.data;
};

export const groupShipments = async (shipmentIds: number[], groupId?: number, groupName?: string): Promise<any> => {
  const response = await axios.post('/api/shipments/group', {
    shipment_ids: shipmentIds,
    ...(groupId ? { group_id: groupId } : {}),
    ...(groupName ? { group_name: groupName } : {}),
  });
  return response.data;
};

export const ungroupShipments = async (shipmentIds: number[]): Promise<any> => {
  const response = await axios.post('/api/shipments/ungroup', { shipment_ids: shipmentIds });
  return response.data;
};

// ── Identity & Aliases (Step 4) ────────────────────────────────────────────
export interface ClientAlias {
  id: number;
  client_id: number;
  first_name: string | null;
  last_name: string | null;
  nickname: string | null;
  full_raw: string | null;
  source: string;
  seen_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export type ClientFlagType =
  | 'possible_duplicate'
  | 'ambiguous_name_at_parse'
  | 'phone_mismatch_with_alias'
  | 'merged_into';

export interface ClientFlag {
  id: number;
  client_id: number;
  flag_type: ClientFlagType | string;
  severity: 'info' | 'warn' | 'error' | string;
  peer_client_ids: number[];
  peer_clients?: { id: number; full_name: string | null; nickname: string | null }[];
  details: string | null;
  dismissed: boolean;
  dismissed_at: string | null;
  dismissed_by: string | null;
  created_at: string | null;
}

export const fetchClientAliases = async (clientId: number): Promise<ClientAlias[]> => {
  const r = await axios.get(`/api/clients/${clientId}/aliases`);
  return r.data;
};

export const createClientAlias = async (
  clientId: number,
  data: { first_name?: string | null; last_name?: string | null; nickname?: string | null; full_raw?: string | null },
): Promise<{ ok: boolean }> => {
  const r = await axios.post(`/api/clients/${clientId}/aliases`, data);
  return r.data;
};

export const deleteClientAlias = async (clientId: number, aliasId: number): Promise<{ ok: boolean }> => {
  const r = await axios.delete(`/api/clients/${clientId}/aliases/${aliasId}`);
  return r.data;
};

export const fetchClientFlags = async (
  clientId: number,
  includeDismissed: boolean = false,
): Promise<ClientFlag[]> => {
  const r = await axios.get(`/api/clients/${clientId}/flags`, {
    params: includeDismissed ? { include_dismissed: true } : {},
  });
  return r.data;
};

export const dismissClientFlag = async (
  clientId: number,
  flagId: number,
  note?: string,
): Promise<{ ok: boolean }> => {
  const r = await axios.post(`/api/clients/${clientId}/flags/${flagId}/dismiss`, { note });
  return r.data;
};

export const mergeClients = async (
  sourceId: number,
  targetId: number,
): Promise<{ ok: boolean; target_id: number; merged_from: number; moved: Record<string, number> }> => {
  const r = await axios.post(`/api/clients/${sourceId}/merge`, { target_id: targetId });
  return r.data;
};