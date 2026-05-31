import axios from 'axios';

// Define types for API responses
export interface ParsingSource {
  id: number;
  name: string;
  url: string;
  description: string | null;
  enabled: boolean;
}

export interface ParsingStyle {
  id: number;
  name: string;
  description: string | null;
  include_images: boolean;
  deep_details: boolean;
}

export interface ParsingLog {
  id: number;
  source_id: number;
  start_time: string;
  end_time: string | null;
  items_processed: number;
  items_added: number;
  items_updated: number;
  items_failed: number;
  status: 'in_progress' | 'completed' | 'failed' | 'cancelled';
  message: string | null;
  source: ParsingSource;
}

export interface ParsingStatus {
  log_id: number;
  status: 'in_progress' | 'completed' | 'failed' | 'cancelled' | 'unknown';
  items_processed: number;
  items_added: number;
  items_updated: number;
  items_failed: number;
  progress: number;
  total_items?: number;
  current_item?: number;
  message: string | null;
  start_time: string;
  end_time: string | null;
  details?: any;
}

export interface ParsingRequest {
  source_id: number;
  style_id: number;
  categories?: string[];
  request_interval?: number;
  max_items?: number;
  custom_options?: Record<string, any>;
}

// API functions
export const fetchParsingSources = async (): Promise<ParsingSource[]> => {
  const response = await axios.get('/api/parsing/sources');
  return response.data;
};

export const fetchParsingStyles = async (): Promise<ParsingStyle[]> => {
  const response = await axios.get('/api/parsing/styles');
  return response.data;
};

export const startParsing = async (request: ParsingRequest): Promise<{ jobId: number }> => {
  const response = await axios.post('/api/parsing/run', request);
  return response.data;
};

export const stopParsing = async (logId: number): Promise<{ log_id: number; status: string; message: string }> => {
  const response = await axios.post(`/api/parsing/stop/${logId}`);
  return response.data;
};

export const fetchParsingStatus = async (logId: number): Promise<ParsingStatus> => {
  const response = await axios.get(`/api/parsing/status/${logId}`);
  return response.data;
};

export const fetchJob = async (jobId: number) => {
  const response = await axios.get(`/api/parsing/jobs/${jobId}`);
  return response.data;
}

export const fetchParsingLogs = async (limit: number = 50): Promise<ParsingLog[]> => {
  const response = await axios.get(`/api/parsing/logs?limit=${limit}`);
  return response.data;
};

export const startSheetsJob = async (mode: string): Promise<{ jobId: number }> => {
  const response = await fetch(`/api/parsing/run?mode=${mode}`, { method: 'POST' });
  if (!response.ok) throw new Error((await response.json()).detail || 'Failed');
  return response.json();
};

export const resetProducts = async (): Promise<void> => {
  const response = await fetch('/api/parsing/sheets/reset-products', { method: 'POST' });
  if (!response.ok) throw new Error((await response.json()).detail || 'Reset failed');
};

export const startWorkspaceParsing = async (): Promise<{ jobId: number }> => {
  const response = await fetch('/api/parsing/sheets/workspace', { method: 'POST' });
  if (!response.ok) throw new Error((await response.json()).detail || 'Failed');
  return response.json();
};

