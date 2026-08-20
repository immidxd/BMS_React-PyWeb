/**
 * Універсальний сервіс для глобального пошуку
 * Підтримує пошук по всіх сутностях системи з інтелектуальними інсайтами
 */

import axios from 'axios';

// Типи для пошуку
export interface CategorySearchResult {
  items: any[];
  total: number;
}

export interface SearchInsights {
  products?: {
    total_found: number;
    brands_found: number;
    avg_price?: number;
  };
  orders?: {
    total_found: number;
    active_orders: number;
    total_value?: number;
  };
  clients?: {
    total_found: number;
    active_clients: number;
  };
  suppliers?: {
    total_found: number;
  };
  deliveries?: {
    total_found: number;
  };
}

export interface GlobalSearchResponse {
  query: string;
  scope?: string;
  results: {
    products?: CategorySearchResult;
    orders?: CategorySearchResult;
    clients?: CategorySearchResult;
    suppliers?: CategorySearchResult;
    deliveries?: CategorySearchResult;
  };
  insights: SearchInsights;
  timestamp: string;
}

export interface SearchOptions {
  scope?: 'products' | 'orders' | 'clients' | 'suppliers' | 'deliveries';
  limit?: number;
  include_insights?: boolean;
  signal?: AbortSignal;
}

/**
 * Клас для роботи з глобальним пошуком
 */
class SearchService {
  private readonly baseUrl = '/api/search';

  /**
   * Глобальний пошук по всіх категоріях
   */
  async globalSearch(
    query: string, 
    options: SearchOptions = {}
  ): Promise<GlobalSearchResponse> {
    try {
      const params = new URLSearchParams();
      params.append('q', query);
      
      if (options.scope) {
        params.append('scope', options.scope);
      }
      
      if (options.limit !== undefined) {
        params.append('limit', String(options.limit));
      }
      
      if (options.include_insights !== undefined) {
        params.append('include_insights', String(options.include_insights));
      }

      console.log(`[SearchService] Global search: "${query}"`, options);
      
      const response = await axios.get<GlobalSearchResponse>(
        `${this.baseUrl}/global?${params.toString()}`,
        { signal: options.signal },
      );
      
      console.log('[SearchService] Search results:', response.data);
      return response.data;
      
    } catch (error) {
      console.error('[SearchService] Global search error:', error);
      throw error;
    }
  }

  /**
   * Пошук тільки товарів з розширеними параметрами
   */
  async searchProducts(
    query: string, 
    additionalParams: Record<string, any> = {}
  ): Promise<CategorySearchResult> {
    try {
      const params = new URLSearchParams();
      params.append('search', query);
      
      // Додаємо додаткові параметри (фільтри, сортування тощо)
      Object.entries(additionalParams).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          params.append(key, String(value));
        }
      });

      console.log(`[SearchService] Products search: "${query}"`, additionalParams);
      
      const response = await axios.get(`/api/products?${params.toString()}`);
      
      return {
        items: response.data.items || [],
        total: response.data.total || 0
      };
      
    } catch (error) {
      console.error('[SearchService] Products search error:', error);
      throw error;
    }
  }

  /**
   * Пошук тільки клієнтів
   */
  async searchClients(query: string): Promise<CategorySearchResult> {
    try {
      const params = new URLSearchParams();
      params.append('search', query);

      console.log(`[SearchService] Clients search: "${query}"`);
      
      const response = await axios.get(`/api/clients?${params.toString()}`);
      
      return {
        items: response.data.items || [],
        total: response.data.total || 0
      };
      
    } catch (error) {
      console.error('[SearchService] Clients search error:', error);
      // Повертаємо порожній результат замість помилки для сумісності
      return { items: [], total: 0 };
    }
  }

  /**
   * Отримати підказки для автодоповнення (майбутня функція)
   */
  async getSuggestions(query: string, limit: number = 5): Promise<string[]> {
    try {
      // TODO: Реалізувати ендпоінт підказок на backend
      console.log(`[SearchService] Getting suggestions for: "${query}"`);
      
      // Тимчасово повертаємо порожній масив
      return [];
      
    } catch (error) {
      console.error('[SearchService] Suggestions error:', error);
      return [];
    }
  }

  /**
   * Отримати історію пошуків (майбутня функція)
   */
  async getSearchHistory(limit: number = 10): Promise<any[]> {
    try {
      // TODO: Реалізувати збереження історії пошуків
      console.log('[SearchService] Getting search history');
      
      // Тимчасово повертаємо порожній масив
      return [];
      
    } catch (error) {
      console.error('[SearchService] Search history error:', error);
      return [];
    }
  }

  /**
   * Очистити історію пошуків
   */
  async clearSearchHistory(): Promise<void> {
    try {
      // TODO: Реалізувати очищення історії
      console.log('[SearchService] Clearing search history');
      
    } catch (error) {
      console.error('[SearchService] Clear history error:', error);
    }
  }

  /**
   * Форматувати результати для відображення
   */
  formatSearchResults(results: GlobalSearchResponse): {
    categories: Array<{
      name: string;
      label: string;
      count: number;
      items: any[];
    }>;
    totalFound: number;
  } {
    const categories = [];
    let totalFound = 0;

    if (results.results.products) {
      categories.push({
        name: 'products',
        label: 'Товари',
        count: results.results.products.total,
        items: results.results.products.items
      });
      totalFound += results.results.products.total;
    }

    if (results.results.orders) {
      categories.push({
        name: 'orders',
        label: 'Замовлення',
        count: results.results.orders.total,
        items: results.results.orders.items
      });
      totalFound += results.results.orders.total;
    }

    if (results.results.clients) {
      categories.push({
        name: 'clients',
        label: 'Клієнти',
        count: results.results.clients.total,
        items: results.results.clients.items
      });
      totalFound += results.results.clients.total;
    }

    if (results.results.suppliers) {
      categories.push({
        name: 'suppliers',
        label: 'Постачальники',
        count: results.results.suppliers.total,
        items: results.results.suppliers.items
      });
      totalFound += results.results.suppliers.total;
    }

    if (results.results.deliveries) {
      categories.push({
        name: 'deliveries',
        label: 'Поставки',
        count: results.results.deliveries.total,
        items: results.results.deliveries.items
      });
      totalFound += results.results.deliveries.total;
    }

    return { categories, totalFound };
  }
}

// Експортуємо єдиний інстанс сервісу
export const searchService = new SearchService();
export default searchService;
