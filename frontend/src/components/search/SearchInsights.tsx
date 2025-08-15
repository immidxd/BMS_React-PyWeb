/**
 * Компонент для відображення інсайтів пошуку
 * Показує статистику та пов'язану інформацію
 */

import React from 'react';
import { SearchInsights as SearchInsightsType } from '../../services/searchService';

interface SearchInsightsProps {
  insights: SearchInsightsType;
  query: string;
  onNavigateToCategory?: (category: string, query: string) => void;
}

const SearchInsights: React.FC<SearchInsightsProps> = ({
  insights,
  query,
  onNavigateToCategory
}) => {
  const handleCategoryClick = (category: string) => {
    if (onNavigateToCategory) {
      onNavigateToCategory(category, query);
    }
  };

  const formatPrice = (price?: number) => {
    if (!price) return '—';
    return new Intl.NumberFormat('uk-UA', {
      style: 'currency',
      currency: 'UAH',
      maximumFractionDigits: 0
    }).format(price);
  };

  const formatNumber = (num: number) => {
    return new Intl.NumberFormat('uk-UA').format(num);
  };

  return (
    <div className="bg-blue-50 dark:bg-blue-900/20 rounded-lg p-4 mb-4">
      <h3 className="text-sm font-semibold text-blue-900 dark:text-blue-100 mb-3">
        💡 Пов'язана інформація
      </h3>
      
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {/* Товари */}
        {insights.products && insights.products.total_found > 0 && (
          <div 
            className="bg-white dark:bg-gray-800 rounded-md p-3 border border-blue-200 dark:border-blue-700 cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => handleCategoryClick('products')}
          >
            <div className="text-center">
              <div className="text-lg font-bold text-blue-600 dark:text-blue-400">
                📦 {formatNumber(insights.products.total_found)}
              </div>
              <div className="text-xs text-gray-600 dark:text-gray-400 mb-1">товарів</div>
              
              {insights.products.brands_found > 0 && (
                <div className="text-xs text-gray-500 dark:text-gray-500">
                  {insights.products.brands_found} брендів
                </div>
              )}
              
              {insights.products.avg_price && (
                <div className="text-xs text-green-600 dark:text-green-400 font-medium">
                  ~{formatPrice(insights.products.avg_price)}
                </div>
              )}
              
              <div className="text-xs text-blue-600 dark:text-blue-400 mt-1 hover:underline">
                [Перейти]
              </div>
            </div>
          </div>
        )}

        {/* Замовлення */}
        {insights.orders && insights.orders.total_found > 0 && (
          <div 
            className="bg-white dark:bg-gray-800 rounded-md p-3 border border-blue-200 dark:border-blue-700 cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => handleCategoryClick('orders')}
          >
            <div className="text-center">
              <div className="text-lg font-bold text-blue-600 dark:text-blue-400">
                📋 {formatNumber(insights.orders.total_found)}
              </div>
              <div className="text-xs text-gray-600 dark:text-gray-400 mb-1">замовлень</div>
              
              {insights.orders.active_orders > 0 && (
                <div className="text-xs text-orange-600 dark:text-orange-400">
                  {insights.orders.active_orders} активних
                </div>
              )}
              
              {insights.orders.total_value && (
                <div className="text-xs text-green-600 dark:text-green-400 font-medium">
                  {formatPrice(insights.orders.total_value)}
                </div>
              )}
              
              <div className="text-xs text-blue-600 dark:text-blue-400 mt-1 hover:underline">
                [Перейти]
              </div>
            </div>
          </div>
        )}

        {/* Клієнти */}
        {insights.clients && insights.clients.total_found > 0 && (
          <div 
            className="bg-white dark:bg-gray-800 rounded-md p-3 border border-blue-200 dark:border-blue-700 cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => handleCategoryClick('clients')}
          >
            <div className="text-center">
              <div className="text-lg font-bold text-blue-600 dark:text-blue-400">
                👥 {formatNumber(insights.clients.total_found)}
              </div>
              <div className="text-xs text-gray-600 dark:text-gray-400 mb-1">клієнтів</div>
              
              {insights.clients.active_clients > 0 && (
                <div className="text-xs text-green-600 dark:text-green-400">
                  {insights.clients.active_clients} активних
                </div>
              )}
              
              <div className="text-xs text-blue-600 dark:text-blue-400 mt-1 hover:underline">
                [Перейти]
              </div>
            </div>
          </div>
        )}

        {/* Постачальники */}
        {insights.suppliers && insights.suppliers.total_found > 0 && (
          <div 
            className="bg-white dark:bg-gray-800 rounded-md p-3 border border-blue-200 dark:border-blue-700 cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => handleCategoryClick('suppliers')}
          >
            <div className="text-center">
              <div className="text-lg font-bold text-blue-600 dark:text-blue-400">
                🚚 {formatNumber(insights.suppliers.total_found)}
              </div>
              <div className="text-xs text-gray-600 dark:text-gray-400 mb-1">постачальників</div>
              
              <div className="text-xs text-blue-600 dark:text-blue-400 mt-1 hover:underline">
                [Перейти]
              </div>
            </div>
          </div>
        )}

        {/* Поставки */}
        {insights.deliveries && insights.deliveries.total_found > 0 && (
          <div 
            className="bg-white dark:bg-gray-800 rounded-md p-3 border border-blue-200 dark:border-blue-700 cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => handleCategoryClick('deliveries')}
          >
            <div className="text-center">
              <div className="text-lg font-bold text-blue-600 dark:text-blue-400">
                📦 {formatNumber(insights.deliveries.total_found)}
              </div>
              <div className="text-xs text-gray-600 dark:text-gray-400 mb-1">поставок</div>
              
              <div className="text-xs text-blue-600 dark:text-blue-400 mt-1 hover:underline">
                [Перейти]
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Якщо немає інсайтів */}
      {!Object.values(insights).some(insight => insight && (insight as any).total_found > 0) && (
        <div className="text-center text-gray-500 dark:text-gray-400 py-2">
          <span className="text-sm">Немає додаткової інформації для відображення</span>
        </div>
      )}
    </div>
  );
};

export default SearchInsights;
