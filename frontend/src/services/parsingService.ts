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

export const startParsing = async (request: ParsingRequest): Promise<{ log_id: number; status: string; message: string }> => {
  const response = await axios.post('/api/parsing/start', request);
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

export const fetchParsingLogs = async (limit: number = 50): Promise<ParsingLog[]> => {
  const response = await axios.get(`/api/parsing/logs?limit=${limit}`);
  return response.data;
};

// Function to start orders parsing script
export const startOrdersParsing = async () => {
  try {
    const response = await fetch(`/api/parsing/orders`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    });
    
    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.detail || 'Failed to start orders parsing');
    }
    
    return await response.json();
  } catch (error) {
    console.error('Error starting orders parsing:', error);
    throw error;
  }
};

// Function to start Google Sheets parsing script
export const startGoogleSheetsParsing = async () => {
  try {
    const response = await fetch(`/api/parsing/googlesheets`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    });
    
    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.detail || 'Failed to start Google Sheets parsing');
    }
    
    return await response.json();
  } catch (error) {
    console.error('Error starting Google Sheets parsing:', error);
    throw error;
  }
}; 