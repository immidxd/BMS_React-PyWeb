/**
 * Склад (коробки) — клієнт до /api/warehouse/* цього ж бекенда, який проксує
 * запити в хмару (BMS_catalog на Railway; там же пише Mini App працівників).
 * Токен хмари лишається на бекенді. Дані складу локально не зберігаються.
 */
import axios from 'axios';

export interface WhLocation {
  box_code: string; box_title: string | null; box_location: string | null;
  box_status: 'open' | 'sealed' | 'archived'; needs_check: boolean; qty: number; packed_at: string;
}

export interface WhProduct {
  id: number; productnumber: string; number: string; size: string; insole: string;
  brand: string | null; model: string | null; type: string | null; color: string | null;
  gender: string | null; season: string | null; condition: string | null;
  price: number | null; quantity: number; sold_count: number; available_qty: number;
  image: string | null; locations?: WhLocation[]; missing?: boolean;
}

export interface WhBoxItem { item_id: number; product_id: number; qty: number; packed_at: string; packed_by: string | null; product: WhProduct }

export interface WhBox {
  id: number; code: string; category: string | null; title: string | null; location: string | null;
  status: 'open' | 'sealed' | 'archived'; needs_check: boolean; note: string | null;
  items: number; units: number; value: number; created_at: string; sealed_at: string | null;
  checked_at: string | null; updated_at: string; created_by: string | null; contents?: WhBoxItem[];
}

export interface WhEvent {
  id: number; at: string; actor: string | null; kind: string; box_code: string | null;
  product_id: number | null; productnumber: string | null; qty: number | null; details: Record<string, unknown> | null;
}

export interface WhStatus { configured: boolean; reachable: boolean; message: string; cloud: string }
export interface WhStaff {
  tg_id: number; name: string; username: string; status: 'pending' | 'active' | 'blocked';
  requested_at: string | null; approved_at: string | null; approved_by: string | null; last_seen_at: string | null; note: string | null;
}
export interface WhStaffList { owners: number[]; staff: WhStaff[]; pending: number }

export type WhScan =
  | { kind: 'product'; product: WhProduct }
  | { kind: 'products'; products: WhProduct[]; stale_sticker?: boolean }
  | { kind: 'box'; box: WhBox };

export function whErr(e: any, fallback = 'Помилка складу'): string {
  const d = e?.response?.data?.detail;
  if (typeof d === 'string') return d;
  if (d?.message) return d.message;
  return e?.message || fallback;
}

const enc = encodeURIComponent;

export const warehouseService = {
  async status(): Promise<WhStatus> { return (await axios.get('/api/warehouse/status')).data; },
  async boxes(status?: string): Promise<WhBox[]> {
    return (await axios.get('/api/warehouse/boxes', { params: status ? { status } : {} })).data.boxes;
  },
  async box(code: string): Promise<WhBox> { return (await axios.get(`/api/warehouse/boxes/${enc(code)}`)).data; },
  async nextCode(category: string): Promise<string> {
    return (await axios.get('/api/warehouse/boxes/next-code', { params: { category } })).data.code;
  },
  async createBox(p: { code?: string; category?: string; title?: string; location?: string; needs_check?: boolean }): Promise<WhBox> {
    return (await axios.post('/api/warehouse/boxes', p)).data;
  },
  async patchBox(code: string, p: Partial<Pick<WhBox, 'title' | 'location' | 'note' | 'needs_check' | 'category'>>): Promise<WhBox> {
    return (await axios.patch(`/api/warehouse/boxes/${enc(code)}`, p)).data;
  },
  async seal(code: string): Promise<WhBox> { return (await axios.post(`/api/warehouse/boxes/${enc(code)}/seal`)).data; },
  async open(code: string): Promise<WhBox> { return (await axios.post(`/api/warehouse/boxes/${enc(code)}/open`)).data; },
  async check(code: string): Promise<WhBox> { return (await axios.post(`/api/warehouse/boxes/${enc(code)}/check`)).data; },
  async deleteBox(code: string, force = false): Promise<{ deleted: string; unpacked_items: number }> {
    return (await axios.delete(`/api/warehouse/boxes/${enc(code)}`, { params: force ? { force: true } : {} })).data;
  },
  async pack(code: string, product_id: number, qty = 1, move = false) {
    return (await axios.post(`/api/warehouse/boxes/${enc(code)}/pack`, { product_id, qty, move })).data as
      { ok: boolean; box: string; product: WhProduct; moved_from: string[]; warning: string | null };
  },
  async unpackFrom(code: string, product_id: number, qty?: number) {
    return (await axios.post(`/api/warehouse/boxes/${enc(code)}/unpack`, { product_id, qty: qty ?? null })).data;
  },
  async unpack(product_id: number, qty?: number) {
    return (await axios.post('/api/warehouse/unpack', { product_id, qty: qty ?? null })).data as
      { ok: boolean; unpacked: { box_code: string; qty: number }[] };
  },
  async unpackAll(code: string) {
    return (await axios.post(`/api/warehouse/boxes/${enc(code)}/unpack-all`)).data as { ok: boolean; unpacked_items: number; units: number };
  },
  async events(p: { box?: string; product_id?: number; limit?: number }): Promise<WhEvent[]> {
    return (await axios.get('/api/warehouse/events', { params: p })).data.events;
  },
  /** Де лежать товари: {product_id: WhLocation[]}. Порожньо, якщо хмара недоступна. */
  async locations(ids: number[]): Promise<Record<number, WhLocation[]>> {
    if (ids.length === 0) return {};
    try {
      const r = await axios.get('/api/warehouse/locations', { params: { product_ids: ids.join(',') } });
      const out: Record<number, WhLocation[]> = {};
      Object.entries(r.data.locations || {}).forEach(([k, v]) => { out[Number(k)] = v as WhLocation[]; });
      return out;
    } catch { return {}; }
  },
  async search(q: string): Promise<WhProduct[]> { return (await axios.get('/api/warehouse/search', { params: { q } })).data.products; },
  async scan(code: string): Promise<WhScan> { return (await axios.get('/api/warehouse/scan', { params: { code } })).data; },
  async staff(): Promise<WhStaffList> { return (await axios.get('/api/warehouse/staff')).data; },
  async staffAdd(p: { tg_id: number; name?: string; note?: string }): Promise<WhStaff> { return (await axios.post('/api/warehouse/staff', p)).data; },
  async staffStatus(tg_id: number, status: 'active' | 'blocked'): Promise<WhStaff> { return (await axios.post(`/api/warehouse/staff/${tg_id}/status`, { status })).data; },
  async staffDelete(tg_id: number): Promise<void> { await axios.delete(`/api/warehouse/staff/${tg_id}`); },
  labelPngUrl(code: string): string { return `/api/warehouse/boxes/${enc(code)}/label.png?t=${Date.now()}`; },
  async printLabel(code: string, mode: 'print' | 'save' | 'download', printer?: string | null, copies = 1) {
    if (mode === 'download') {
      return (await axios.post(`/api/warehouse/boxes/${enc(code)}/label`, { mode, copies }, { responseType: 'blob' })).data as Blob;
    }
    return (await axios.post(`/api/warehouse/boxes/${enc(code)}/label`, { mode, printer: printer ?? null, copies })).data as
      { path: string; filename: string; printed: boolean; printer: string | null; message: string };
  },
};

export const KIND_UA: Record<string, string> = {
  pack: 'запаковано', unpack: 'вийнято', move: 'перенесено', seal: 'запечатано', open: 'відкрито',
  check: 'звірено', box_create: 'коробку створено', box_delete: 'коробку видалено', box_edit: 'змінено',
};

export const CATEGORIES: { letter: string; label: string }[] = [
  { letter: 'Z', label: 'Зима' }, { letter: 'D', label: 'Демі' }, { letter: 'L', label: 'Літо' },
  { letter: 'T', label: 'Трекінг' }, { letter: 'V', label: 'Весна' }, { letter: 'O', label: 'Одяг' },
];

export function actorName(actor: string | null): string {
  if (!actor) return '';
  if (actor === 'bms') return 'BMS';
  return actor.replace(/^tg:\d+\s*/, '') || 'Telegram';
}
