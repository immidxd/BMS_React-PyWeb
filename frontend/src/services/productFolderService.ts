/**
 * Папки товарів — робочі набори в «Товарах» (API /api/product-folders).
 *
 * Список папок — модульне сховище поза React (як selectionManager/labelService):
 * його бачать і кнопка «📁 Папки» в шапці, і «Дії → У папку…», тож після
 * будь-якої зміни досить одного refresh(), щоб лічильники всюди збіглися.
 */
import axios from 'axios';
import { useEffect, useSyncExternalStore } from 'react';

export interface ProductFolder {
  id: number;
  name: string;
  count: number;
  sort_order: number;
}

const API = '/api/product-folders';

let folders: ProductFolder[] = [];
let loaded = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => { try { fn(); } catch { /* ignore */ } });

const errText = (e: any) => {
  // Фронт уже новий, а бекенд стартував до оновлення — маршруту ще нема.
  if (e?.response?.status === 404 && e?.response?.data?.detail === 'Not Found') {
    return 'Папки з’являться після перезапуску BMS';
  }
  return e?.response?.data?.detail || e?.message || 'Помилка';
};

export const productFolderService = {
  async refresh(): Promise<ProductFolder[]> {
    const r = await axios.get<{ folders: ProductFolder[] }>(API);
    folders = r.data.folders || [];
    loaded = true;
    emit();
    return folders;
  },
  async create(name: string): Promise<ProductFolder> {
    try {
      const r = await axios.post<ProductFolder>(API, { name });
      await this.refresh();
      return r.data;
    } catch (e) { throw new Error(errText(e)); }
  },
  async rename(id: number, name: string): Promise<void> {
    try {
      await axios.patch(`${API}/${id}`, { name });
      await this.refresh();
    } catch (e) { throw new Error(errText(e)); }
  },
  async remove(id: number): Promise<void> {
    try {
      await axios.delete(`${API}/${id}`);
      await this.refresh();
    } catch (e) { throw new Error(errText(e)); }
  },
  async addItems(id: number, productIds: number[]): Promise<{ added: number; already: number; missing: number; count: number }> {
    try {
      const r = await axios.post(`${API}/${id}/items`, { product_ids: productIds });
      await this.refresh();
      return r.data;
    } catch (e) { throw new Error(errText(e)); }
  },
  async removeItems(id: number, productIds: number[]): Promise<{ removed: number; count: number }> {
    try {
      const r = await axios.post(`${API}/${id}/items/remove`, { product_ids: productIds });
      await this.refresh();
      return r.data;
    } catch (e) { throw new Error(errText(e)); }
  },
};

const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
const getSnapshot = () => folders;

/** Хук: список папок (перше звертання саме підвантажує його з бекенда). */
export function useProductFolders(): ProductFolder[] {
  const list = useSyncExternalStore(subscribe, getSnapshot);
  useEffect(() => {
    if (loaded) return;
    loaded = true; // один запит, навіть якщо хук змонтовано в кількох місцях
    void productFolderService.refresh().catch(() => { loaded = false; });
  }, []);
  return list;
}
