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
}

export interface ReferenceList<T> {
  items: T[];
}

export interface ClientList {
  items: Client[];
  total: number;
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
    phone_number?: string;
    email?: string;
    gender_id?: number;
    address?: string;
    notes?: string;
  }>
): Promise<Client> => {
  const response = await axios.put(`/api/clients/${id}`, client);
  return response.data;
};

export const deleteClient = async (id: number): Promise<{ message: string }> => {
  const response = await axios.delete(`/api/clients/${id}`);
  return response.data;
}; 