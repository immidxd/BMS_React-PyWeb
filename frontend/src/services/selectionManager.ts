import { useSyncExternalStore } from 'react';

/**
 * Глобальний буфер «виділених товарів» (product_id).
 *
 * Живе в МОДУЛЬНОМУ scope (поза React) — тож переживає перемикання вкладок,
 * відкриття/закриття картки та будь-який ре-рендер. Єдине джерело правди для
 * виділення (індивідуального й масового) і подальших дій над ним (наприклад,
 * масова публікація на Prom). Дзеркалить патерн `taskManager` (subscribe/emit).
 *
 * ⚠️ Скидається ЛИШЕ за дією користувача (`clear()`), не автоматично при навігації.
 */
type Listener = () => void;

class SelectionManager {
  private ids = new Set<number>();
  private listeners = new Set<Listener>();
  private snapshot: readonly number[] = [];   // стабільний масив для useSyncExternalStore

  private emit() {
    this.snapshot = Array.from(this.ids);
    this.listeners.forEach(fn => { try { fn(); } catch { /* ignore */ } });
  }

  subscribe = (fn: Listener): (() => void) => {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  };

  getSnapshot = (): readonly number[] => this.snapshot;

  has(id: number): boolean { return this.ids.has(id); }
  size(): number { return this.ids.size; }
  get(): number[] { return Array.from(this.ids); }

  toggle(id: number) {
    if (this.ids.has(id)) this.ids.delete(id); else this.ids.add(id);
    this.emit();
  }
  add(ids: number[]) {
    let changed = false;
    for (const id of ids) if (!this.ids.has(id)) { this.ids.add(id); changed = true; }
    if (changed) this.emit();
  }
  remove(ids: number[]) {
    let changed = false;
    for (const id of ids) if (this.ids.delete(id)) changed = true;
    if (changed) this.emit();
  }
  set(ids: number[]) {
    this.ids = new Set(ids);
    this.emit();
  }
  clear() {
    if (this.ids.size === 0) return;
    this.ids.clear();
    this.emit();
  }
}

export const selectionManager = new SelectionManager();

/** Хук: підписка на буфер виділення. Повертає стабільний масив id + хелпери. */
export function useSelection() {
  const ids = useSyncExternalStore(selectionManager.subscribe, selectionManager.getSnapshot);
  return {
    ids,                                   // readonly number[]
    size: ids.length,
    has: (id: number) => selectionManager.has(id),
    toggle: (id: number) => selectionManager.toggle(id),
    add: (list: number[]) => selectionManager.add(list),
    remove: (list: number[]) => selectionManager.remove(list),
    set: (list: number[]) => selectionManager.set(list),
    clear: () => selectionManager.clear(),
  };
}
