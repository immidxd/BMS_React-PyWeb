import axios from 'axios';
import {
    Product,
    ProductFilters
} from '../types/product';
import { filenameFromDisposition } from './imageTransfer';

// Базовий URL для API товарів
const API_URL = '/api/products';

// Додаю тип ProductListResponse тут, якщо його немає
export type ProductListResponse = {
    items: Product[];
    total: number;
    page: number;
    per_page: number;
    pages: number;
};

export type JournalSyncItem = {
    id: number;
    field: string;
    status: 'pending' | 'processing' | 'failed' | 'skipped';
    attempts: number;
    last_error?: string | null;
    sheet_title?: string | null;
    next_attempt_at?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
};

export type JournalSyncState = {
    pending: number;
    processing: number;
    active: number;
    retrying: number;
    stale: number;
    failed: number;
    blocked: number;
    ignored: number;
    skipped: number;
    unsynced: number;
    stuck: boolean;
    state: 'idle' | 'syncing' | 'delayed' | 'error';
    items?: JournalSyncItem[];
};

export type ProductJournalPullResult = {
    ok: boolean;
    waiting?: boolean;
    reason?: string;
    sheet?: string | null;
    moved_sheet?: boolean;
    added?: number;
    updated?: number;
    restored_locks?: number;
    queued_writebacks?: number;
};

/**
 * Сервіс для роботи з API товарів
 */
export const productService = {
    /**
     * Отримати список товарів з пагінацією та фільтрацією
     */
    async getProducts(params: Record<string, any> = {}, signal?: AbortSignal): Promise<ProductListResponse> {
        try {
            const { 
                page,
                per_page,
                skip, 
                limit, 
                sort_by = 'id', 
                sort_dir = 'desc',
                ...filters 
            } = params;
            
            // Будуємо URL з параметрами
            const queryParams = new URLSearchParams();
            if (typeof page === 'number') queryParams.append('page', String(page));
            if (typeof per_page === 'number') queryParams.append('per_page', String(per_page));
            if (typeof skip === 'number') queryParams.append('skip', String(skip));
            if (typeof limit === 'number') queryParams.append('limit', String(limit));
            queryParams.append('sort_by', sort_by);
            queryParams.append('sort_dir', sort_dir);
            
            // Додаємо фільтри, якщо вони вказані
            Object.entries(filters).forEach(([key, value]) => {
                if (value === undefined || value === null) return;
                if (Array.isArray(value)) {
                    // FastAPI List[int] — повторювані params: key=1&key=2
                    value.forEach((v: any) => queryParams.append(key, String(v)));
                } else {
                    queryParams.append(key, String(value));
                }
            });
            
            console.log("Fetching products from API:", `${API_URL}?${queryParams.toString()}`);
            
            // Add retry logic (max 3 retries)
            let retries = 0;
            const maxRetries = 3;
            
            while (retries < maxRetries) {
                try {
                    const response = await axios.get<ProductListResponse>(`${API_URL}?${queryParams.toString()}`, { signal });
                    console.log("Products fetched successfully:", response.data);
                    return response.data;
                } catch (error: any) {
                    // Запит СКАСОВАНО навмисно (новий фільтр/пошук перебив старий) —
                    // це не збій. Раніше цикл ретраїв бачив у скасуванні помилку і
                    // слав ще 3 запити з backoff'ом: швидка зміна фільтрів множила
                    // навантаження вчетверо й уповільнювала все інше.
                    if (error?.name === 'CanceledError' || error?.code === 'ERR_CANCELED') {
                        throw error;
                    }
                    retries++;
                    console.error(`Attempt ${retries}/${maxRetries} failed:`, error);

                    if (retries >= maxRetries) {
                        throw error;
                    }

                    // Wait before retrying (exponential backoff)
                    await new Promise(resolve => setTimeout(resolve, 500 * Math.pow(2, retries)));
                }
            }
            
            // If we reach here, all retries failed
            throw new Error("Failed to fetch products after multiple attempts");
        } catch (error) {
            console.error('Error fetching products:', error);
            throw error;
        }
    },
    
    /**
     * Отримати товар за ID
     */
    async getProduct(id: number): Promise<Product> {
        try {
            const response = await axios.get<Product>(`${API_URL}/${id}`);
            return response.data;
        } catch (error) {
            console.error(`Error fetching product ${id}:`, error);
            throw error;
        }
    },

    /** Легкий стан BMS→журнал для живого чіпа картки. */
    async getJournalSyncState(id: number): Promise<JournalSyncState> {
        const response = await axios.get<JournalSyncState>(`/api/journal-sync/product/${id}`);
        return response.data;
    },

    /** Точково підтягнути один товар з актуальної вкладки журналу, без видалень. */
    async syncProductFromJournal(id: number): Promise<ProductJournalPullResult> {
        const response = await axios.post<ProductJournalPullResult>(`${API_URL}/${id}/sync-from-journal`);
        return response.data;
    },

    /**
     * Отримати фото товару (з локальної папки/cloud — за productnumber).
     * Повертає список з url для відображення в галереї картки.
     */
    async getProductImages(id: number): Promise<{
        productnumber: string;
        count: number;
        // kind: 'official' — студійні (пріоритет для прев'ю), 'real' — мої фото, 'defect' — дефекти
        images: { filename: string; url: string; index: number; kind?: 'official' | 'real' | 'defect'; is_defect?: boolean }[];
    }> {
        try {
            const response = await axios.get(`${API_URL}/${id}/images`);
            return response.data;
        } catch (error) {
            console.error(`Error fetching product images ${id}:`, error);
            return { productnumber: '', count: 0, images: [] };
        }
    },
    
    /** Додати фото товару (конверт у WebP + R2 на бекенді).
     *  kind='official' → нумерація `_NN`; kind='real' → `_00N`. */
    async addProductPhotos(id: number, files: File[], kind: 'official' | 'real' | 'defect' = 'official'): Promise<{ added: number; category: string; kind: string; errors?: { file: string; reason: string }[] }> {
        const fd = new FormData();
        files.forEach((f) => fd.append('files', f));
        const res = await axios.post(`${API_URL}/${id}/photos`, fd, {
            params: { kind },
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return res.data;
    },

    /** Пакетне викачування: усі фото товару одним .zip.
     *  Повертає blob + ім'я архіву з Content-Disposition. */
    async downloadProductPhotosZip(
        id: number,
        kind: 'all' | 'official' | 'real' | 'defect' = 'all',
    ): Promise<{ blob: Blob; filename: string }> {
        const res = await axios.get(`${API_URL}/${id}/photos/download`, {
            params: { kind },
            responseType: 'blob',
        });
        const name = filenameFromDisposition(res.headers['content-disposition'] as string | undefined);
        return { blob: res.data as Blob, filename: name || `product-${id}-photos.zip` };
    },

    /** Перемістити всі фото між галереями (official/real/defect). */
    async movePhotosKind(id: number, from_kind: 'official' | 'real' | 'defect', to_kind: 'official' | 'real' | 'defect'): Promise<{ moved: string[]; from: string[] }> {
        const res = await axios.post(`${API_URL}/${id}/photos/move-kind`, null, {
            params: { from_kind, to_kind },
        });
        return res.data;
    },

    /** Перенести ОДНЕ фото в інший набір (official/real/defect). */
    async movePhotoOne(id: number, filename: string, to_kind: 'official' | 'real' | 'defect'): Promise<{ moved: string; from: string }> {
        const res = await axios.post(`${API_URL}/${id}/photos/move-one`, null, {
            params: { filename, to_kind },
        });
        return res.data;
    },

    /** Замінити вміст одного фото (та сама позиція, новий файл). */
    async replaceProductPhoto(id: number, filename: string, file: File): Promise<{ replaced: string }> {
        const fd = new FormData();
        fd.append('file', file);
        const res = await axios.put(`${API_URL}/${id}/photos/replace`, fd, {
            params: { filename },
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return res.data;
    },

    /** Повернути/віддзеркалити канонічне фото (та сама назва й R2-ключ). */
    async transformProductPhoto(
        id: number,
        filename: string,
        operation: 'rotate_left' | 'rotate_180' | 'rotate_right' | 'flip_horizontal',
    ): Promise<{ transformed: string; operation: string; width: number; height: number; version: string }> {
        const res = await axios.post(`${API_URL}/${id}/photos/transform`, { operation }, {
            params: { filename },
        });
        return res.data;
    },

    /** Перенумерувати фото (перше = головне) — official→`_01.._0N`, real→`_001.._00N`. */
    async reorderProductPhotos(id: number, order: string[], kind: 'official' | 'real' | 'defect' = 'official'): Promise<{ order: string[] }> {
        const res = await axios.put(`${API_URL}/${id}/photos/reorder`, { order }, { params: { kind } });
        return res.data;
    },

    /** Видалити одне фото (мірор + R2). */
    async deleteProductPhoto(id: number, filename: string): Promise<{ deleted: string }> {
        const res = await axios.delete(`${API_URL}/${id}/photos/${encodeURIComponent(filename)}`);
        return res.data;
    },

    /**
     * Створити новий товар
     */
    async createProduct(productData: Partial<Product>): Promise<Product> {
        try {
            const response = await axios.post<Product>(API_URL, productData);
            return response.data;
        } catch (error) {
            console.error('Error creating product:', error);
            throw error;
        }
    },
    
    /**
     * Оновити існуючий товар
     */
    async updateProduct(id: number, productData: Partial<Product>): Promise<Product> {
        try {
            const response = await axios.put<Product>(`${API_URL}/${id}`, productData);
            return response.data;
        } catch (error) {
            console.error(`Error updating product ${id}:`, error);
            throw error;
        }
    },
    
    /**
     * Зняти in-app лок з полів товару («скинути до аркуша»).
     * fields порожній/відсутній → розблокувати всі.
     */
    async unlockProductFields(id: number, fields?: string[]): Promise<{ success: boolean; remaining_locked: string[]; message: string }> {
        try {
            const response = await axios.patch(`${API_URL}/${id}/unlock`, { fields: fields ?? null });
            return response.data;
        } catch (error) {
            console.error(`Error unlocking product ${id}:`, error);
            throw error;
        }
    },

    /**
     * Видалити товар
     */
    async deleteProduct(id: number): Promise<{ success: boolean; message: string }> {
        try {
            const response = await axios.delete<{ success: boolean; message: string }>(`${API_URL}/${id}`);
            return response.data;
        } catch (error) {
            console.error(`Error deleting product ${id}:`, error);
            throw error;
        }
    },
    
    /**
     * Оновити видимість товару
     */
    async updateProductVisibility(id: number, isVisible: boolean): Promise<{ success: boolean; message: string; is_visible: boolean }> {
        try {
            const response = await axios.patch<{ success: boolean; message: string; is_visible: boolean }>(
                `${API_URL}/${id}/visibility`, 
                { is_visible: isVisible }
            );
            return response.data;
        } catch (error) {
            console.error(`Error updating visibility for product ${id}:`, error);
            throw error;
        }
    },

    /**
     * Публікація товару в публічний інтернет-каталог (Telegram Mini App).
     * ОКРЕМІ endpoint-и (catalog_listings) — не зачіпають збереження картки.
     * Ключ = productnumber → діє на всю картку/ростовку.
     */
    async getCatalogStatus(id: number): Promise<{ productnumber: string; is_published: boolean; is_featured: boolean; sale_price: number | null; is_on_sale: boolean }> {
        const response = await axios.get(`${API_URL}/${id}/catalog`);
        return response.data;
    },

    async setCatalogStatus(id: number, payload: { is_published: boolean; is_featured?: boolean; clear_lost?: boolean }): Promise<{ success: boolean; productnumber: string; is_published: boolean; is_featured: boolean }> {
        const response = await axios.patch(`${API_URL}/${id}/catalog`, payload);
        return response.data;
    },

    /**
     * Знижка на картку у публічному каталозі (акційна ціна ЛИШЕ для вітрини —
     * products.price НЕ чіпається). Ключ = productnumber → діє на всю ростовку.
     */
    async setCatalogDiscount(id: number, payload: { sale_price: number | null; is_on_sale: boolean }): Promise<{ success: boolean; productnumber: string; sale_price: number | null; is_on_sale: boolean }> {
        const response = await axios.patch(`${API_URL}/${id}/catalog/discount`, payload);
        return response.data;
    },
    
    /**
     * Масове оновлення товарів
     */
    async bulkUpdateProducts(productIds: number[], updateData: Partial<Product>): Promise<{ success: boolean; message: string; updated_count: number }> {
        try {
            const response = await axios.post<{ success: boolean; message: string; updated_count: number }>(
                `${API_URL}/bulk-update`, 
                {
                    product_ids: productIds,
                    update_data: updateData
                }
            );
            return response.data;
        } catch (error) {
            console.error('Error bulk updating products:', error);
            throw error;
        }
    },
    
    /**
     * Отримати доступні опції для фільтрів
     */
    async getFilters(): Promise<ProductFilters> {
        try {
            const response = await axios.get<ProductFilters>(`${API_URL}/filters`);
            return response.data;
        } catch (error) {
            console.error('Error fetching product filters:', error);
            throw error;
        }
    },

    /**
     * Динамічні фасети: EU-розміри ТА кольорові групи, наявні в поточному
     * відфільтрованому наборі. Приймає ті самі фільтри, що й getProducts
     * (свій фільтр кожен фасет ігнорує на бекенді).
     */
    async getAvailableFacets(
        params: Record<string, any> = {}, signal?: AbortSignal
    ): Promise<{ eu: string[]; colorGroups: { id: number; count: number }[] }> {
        try {
            const queryParams = new URLSearchParams();
            Object.entries(params).forEach(([key, value]) => {
                if (value === undefined || value === null) return;
                if (Array.isArray(value)) {
                    value.forEach((v: any) => queryParams.append(key, String(v)));
                } else {
                    queryParams.append(key, String(value));
                }
            });
            const response = await axios.get<{ eu: string[]; color_groups: { id: number; count: number }[] }>(
                `${API_URL}/available-facets?${queryParams.toString()}`, { signal }
            );
            return { eu: response.data?.eu || [], colorGroups: response.data?.color_groups || [] };
        } catch (error) {
            console.error('Error fetching available facets:', error);
            return { eu: [], colorGroups: [] };
        }
    }
};

export default productService;
