/** API «Розкладу каналу» (backend/services/telegram_channel_queue.py). */

export type ChannelQueueState = 'planned' | 'queued' | 'cancelling' | 'sent' | 'failed' | 'cancelled' | 'scheduled';

export interface ChannelQueueItem {
  id: number;
  product_id: number | null;
  productnumber: string;
  brand: string | null;
  model: string | null;
  price: number | null;
  product_status: string | null;
  available: boolean;
  state: ChannelQueueState;
  attempts: number;
  last_error: string | null;
  scheduled_at: string | null;
  original_scheduled_at: string | null;
  sent_at: string | null;
  sent_via: 'telegram' | 'bms' | 'manual' | null;
  photo_count: number | null;
  silent: boolean;
  channel_message_id: number | null;
}

export interface ChannelQueueResponse {
  ok: boolean;
  items: ChannelQueueItem[];
  counts: Partial<Record<ChannelQueueState, number>>;
  queued_photos: number;
  queue_budget: number;
  next_at: string | null;
  last_at: string | null;
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || 'Не вдалося виконати дію');
  return data as T;
}

const BASE = '/api/publications/telegram/channel-queue';
const JSON_POST = (body?: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const channelQueueApi = {
  list: (historyDays = 14) => call<ChannelQueueResponse>(`${BASE}?history_days=${historyDays}`),
  sync: () => call<Record<string, unknown>>(`${BASE}/sync`, JSON_POST()),
  cancel: (id: number) => call<Record<string, unknown>>(`${BASE}/${id}/cancel`, JSON_POST()),
  reschedule: (id: number, at: string, deferSync = false) =>
    call<{ ok: boolean; scheduled_at: string; shifted: boolean }>(
      `${BASE}/${id}/reschedule`, JSON_POST({ at, defer_sync: deferSync }),
    ),
  sendNow: (id: number) => call<Record<string, unknown>>(`${BASE}/${id}/send-now`, JSON_POST()),
};
