import axios from 'axios';
import {
    Product,
    ProductFilters
} from '../types/product';

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

/**
 * Сервіс для роботи з API товарів
 */
export const productService = {
    /**
     * Отримати список товарів з пагінацією та фільтрацією
     */
    async getProducts(params: Record<string, any> = {}): Promise<ProductListResponse> {
        try {
            const { 
                skip = 0, 
                limit = 10, 
                sort_by = 'id', 
                sort_dir = 'desc',
                ...filters 
            } = params;
            
            // Будуємо URL з параметрами
            const queryParams = new URLSearchParams();
            queryParams.append('skip', skip.toString());
            queryParams.append('limit', limit.toString());
            queryParams.append('sort_by', sort_by);
            queryParams.append('sort_dir', sort_dir);
            
            // Додаємо фільтри, якщо вони вказані
            Object.entries(filters).forEach(([key, value]) => {
                if (value !== undefined && value !== null) {
                    queryParams.append(key, value.toString());
                }
            });
            
            console.log("Fetching products from API:", `${API_URL}?${queryParams.toString()}`);
            
            // Add retry logic (max 3 retries)
            let retries = 0;
            const maxRetries = 3;
            
            while (retries < maxRetries) {
                try {
                    const response = await axios.get<ProductListResponse>(`${API_URL}?${queryParams.toString()}`);
                    console.log("Products fetched successfully:", response.data);
                    return response.data;
                } catch (error) {
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
    }
};

export default productService; 