/**
 * Стікери товарів із QR (склад): API /api/labels/* + лічильник черги друку.
 *
 * Аркуш 100×100 мм друкують пакетом (2×2 / 2×3 / 3×3 стікери), тому «Додати
 * товар» ставить стікер у ЧЕРГУ (бекенд робить це сам), а друкують потім
 * усе разом із дровера «Стікери». Лічильник черги — модульне сховище поза
 * React (як selectionManager): оновлюється після наших мутацій і за подією
 * `bms:delivery-changed` (додавання товару робить бекенд, фронт про це не
 * знає інакше).
 */
import axios from 'axios';
import { useSyncExternalStore } from 'react';

export interface LabelLayout {
  key: string;            // '2x2' | '2x3' | '3x3' — колонки × рядки
  label: string;
  cols: number;
  rows: number;
  per_page: number;
  sticker_mm: [number, number];
  media_mm: [number, number];
}

export interface LabelPrinter { name: string; default: boolean }

export interface LabelsConfig {
  layouts: LabelLayout[];
  default_layout: string;
  printers: LabelPrinter[];
  preferred_printer: string | null;
  can_print: boolean;     // є CUPS lp на цій машині
  desktop: boolean;       // PyWebView — файли зберігає бекенд
  platform: string;
  queue_count: number;
}

export interface LabelItem {
  product_id: number;
  productnumber: string | null;
  number: string;
  size: string;
  insole: string;
  brand: string | null;
  model: string | null;
  type: string | null;
  color: string | null;
  price: number | null;
  quantity: number;
  sold_count: number;
  available_qty: number;
  sold: boolean;          // наявних 0 — стікер за замовчуванням не друкується
  mainimage: string | null;
  label_printed_at: string | null;
  copies: number;
  queue_id: number | null;
  source: string | null;
  added_at: string | null;
}

/** Звідки брати стікери: виділення, завіз або черга. */
export type LabelSource =
  | { product_ids: number[] }
  | { delivery_id: number }
  | { from_queue: true };

export interface RenderOpts {
  items: { product_id: number; copies: number }[];
  layout: string;
  show_price: boolean;
  cut_marks: boolean;
}

export interface PreviewResult { png: string; stickers: number; pages: number; layout: LabelLayout }

export interface PrintResult {
  job_id: number;
  path: string;
  filename: string;
  stickers: number;
  pages: number;
  printed: boolean;
  printer: string | null;
  message: string;
  queue_count: number;
}

export type PrintMode = 'print' | 'save' | 'download';

function errMsg(e: any, fallback: string): string {
  return e?.response?.data?.detail || e?.message || fallback;
}

export const labelService = {
  async getConfig(): Promise<LabelsConfig> {
    const r = await axios.get('/api/labels/config');
    return r.data;
  },
  async getQueue(): Promise<{ items: LabelItem[]; count: number; stickers: number }> {
    const r = await axios.get('/api/labels/queue');
    return r.data;
  },
  async resolve(src: LabelSource): Promise<LabelItem[]> {
    const r = await axios.post('/api/labels/resolve', src);
    return r.data.items as LabelItem[];
  },
  async enqueue(src: Exclude<LabelSource, { from_queue: true }>, source: string, copies?: number)
    : Promise<{ added: number; updated: number; skipped_sold: number; count: number }> {
    const r = await axios.post('/api/labels/queue', { ...src, source, copies: copies ?? null });
    labelQueue.set(r.data.count);
    return r.data;
  },
  async setQueueCopies(queueId: number, copies: number): Promise<void> {
    await axios.patch(`/api/labels/queue/${queueId}`, { copies });
  },
  async removeFromQueue(queueId: number): Promise<number> {
    const r = await axios.delete(`/api/labels/queue/${queueId}`);
    labelQueue.set(r.data.count);
    return r.data.count;
  },
  async clearQueue(): Promise<void> {
    await axios.delete('/api/labels/queue');
    labelQueue.set(0);
  },
  async preview(opts: RenderOpts): Promise<PreviewResult> {
    const r = await axios.post('/api/labels/preview', opts);
    return r.data;
  },
  /** print/save → JSON зі шляхом; download → PDF-blob (звичайний браузер). */
  async print(opts: RenderOpts & { mode: PrintMode; printer?: string | null }): Promise<PrintResult | Blob> {
    if (opts.mode === 'download') {
      const r = await axios.post('/api/labels/print', opts, { responseType: 'blob' });
      labelQueue.refresh();
      return r.data as Blob;
    }
    try {
      const r = await axios.post('/api/labels/print', opts);
      labelQueue.set(r.data.queue_count);
      return r.data as PrintResult;
    } catch (e: any) {
      throw new Error(errMsg(e, 'Не вдалося надрукувати стікери'));
    }
  },
};

/* ── Лічильник черги (модульне сховище поза React) ────────────────────────── */

type Listener = () => void;

class LabelQueueStore {
  private count = 0;
  private listeners = new Set<Listener>();
  private inflight: Promise<void> | null = null;
  private bound = false;

  subscribe = (fn: Listener): (() => void) => {
    this.listeners.add(fn);
    this.bindEvents();
    return () => { this.listeners.delete(fn); };
  };
  getSnapshot = (): number => this.count;

  set(n: number) {
    if (n === this.count) return;
    this.count = n;
    this.listeners.forEach(fn => { try { fn(); } catch { /* ignore */ } });
  }

  /** Перечитати з бекенда (дешевий COUNT). Паралельні виклики склеюються. */
  refresh(): Promise<void> {
    if (this.inflight) return this.inflight;
    this.inflight = axios.get('/api/labels/config')
      .then(r => this.set(Number(r.data?.queue_count ?? 0)))
      .catch(() => { /* бекенд без модуля стікерів — лишаємо як є */ })
      .finally(() => { this.inflight = null; });
    return this.inflight;
  }

  private bindEvents() {
    if (this.bound) return;
    this.bound = true;
    // «Додати товар» ставить стікер у чергу на бекенді — про це фронт дізнається
    // лише з події завозу; після неї перечитуємо лічильник.
    window.addEventListener('bms:delivery-changed', () => { void this.refresh(); });
    window.addEventListener('bms:labels-changed', () => { void this.refresh(); });
  }
}

export const labelQueue = new LabelQueueStore();

export function useLabelQueueCount(): number {
  return useSyncExternalStore(labelQueue.subscribe, labelQueue.getSnapshot, labelQueue.getSnapshot);
}

export function emitLabelsChanged() {
  window.dispatchEvent(new CustomEvent('bms:labels-changed'));
}
