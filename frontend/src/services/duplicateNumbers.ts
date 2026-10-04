import { useSyncExternalStore } from 'react';

/**
 * Номери товарів, що дублюються (GET /api/product-numbers/duplicates) — для
 * червоного номера всюди в інтерфейсі. Одне сховище на весь застосунок:
 * завантажується при першому використанні, оновлюється після парсингу,
 * правки завозу, зміни номера і (не частіше ніж раз на хвилину) при поверненні
 * у вікно. Запит іде в локальну базу — хмару не чіпає.
 */

export interface DuplicateInfo {
  records: number;
  deliveries: string[];
  reason: 'deliveries' | 'conflict';
}

let numbers: Record<string, DuplicateInfo> = {};
let loadedAt = 0;
let inflight: Promise<void> | null = null;
let wired = false;
const listeners = new Set<() => void>();
const MIN_INTERVAL_MS = 60_000;

const emit = () => listeners.forEach((l) => l());

export function refreshDuplicateNumbers(force = false): Promise<void> {
  if (inflight) return inflight;
  if (!force && Date.now() - loadedAt < MIN_INTERVAL_MS) return Promise.resolve();
  inflight = fetch('/api/product-numbers/duplicates')
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (d && d.numbers && typeof d.numbers === 'object') {
        numbers = d.numbers;
        loadedAt = Date.now();
        emit();
      }
    })
    .catch(() => { /* позначка допоміжна — без неї інтерфейс працює як раніше */ })
    .finally(() => { inflight = null; });
  return inflight;
}

function wire() {
  if (wired || typeof window === 'undefined') return;
  wired = true;
  const force = () => { void refreshDuplicateNumbers(true); };
  window.addEventListener('parsing-complete', force);
  window.addEventListener('bms:delivery-changed', force);
  window.addEventListener('bms:product-number-changed', force);
  window.addEventListener('focus', () => { void refreshDuplicateNumbers(false); });
  void refreshDuplicateNumbers(true);
}

/** Повідомити, що номер товару змінився (після перейменування). */
export function emitProductNumberChanged() {
  try { window.dispatchEvent(new Event('bms:product-number-changed')); } catch { /* тести */ }
}

const subscribe = (l: () => void) => { wire(); listeners.add(l); return () => { listeners.delete(l); }; };
const snapshot = () => numbers;

/** Відомості про дубль або undefined. Номер можна передати з «#» чи без. */
export function lookupDuplicate(map: Record<string, DuplicateInfo>, value?: string | null): DuplicateInfo | undefined {
  const v = (value || '').trim();
  if (!v) return undefined;
  if (v.startsWith('#')) return map[v];
  return map[`#${v}`] ?? map[v];
}

export function useDuplicateNumber(value?: string | null): DuplicateInfo | undefined {
  const map = useSyncExternalStore(subscribe, snapshot, snapshot);
  return lookupDuplicate(map, value);
}

export function duplicateTitle(info: DuplicateInfo): string {
  if (info.reason === 'deliveries') {
    return `Номер дублюється: ${info.records} записів у різних завозах`
      + (info.deliveries.length ? ` — ${info.deliveries.join(', ')}` : '');
  }
  return `Номер дублюється: ${info.records} записів одного завозу з різним брендом або видом`;
}
