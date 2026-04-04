/**
 * Компонент для відображення превью результатів глобального пошуку
 * Показує результати з усіх категорій в компактному вигляді
 */

import React from 'react';
import { GlobalSearchResponse } from '../../services/searchService';

interface SearchResultsPreviewProps {
  results: GlobalSearchResponse;
  onNavigateToCategory?: (category: string, query: string) => void;
  onSelectItem?: (category: string, item: any) => void;
}

const SearchResultsPreview: React.FC<SearchResultsPreviewProps> = ({
  results,
  onNavigateToCategory,
  onSelectItem
}) => {
  const handleCategoryClick = (category: string) => {
    if (onNavigateToCategory) {
      onNavigateToCategory(category, results.query);
    }
  };

  const handleItemClick = (category: string, item: any) => {
    if (onSelectItem) {
      onSelectItem(category, item);
    }
  };

  const formatPrice = (price?: number) => {
    if (!price) return '';
    return new Intl.NumberFormat('uk-UA', {
      style: 'currency',
      currency: 'UAH',
      maximumFractionDigits: 0
    }).format(price);
  };

  const renderProductItem = (item: any) => (
    <div 
      key={item.id}
      className="flex items-center justify-between p-2 hover:bg-gray-50 dark:hover:bg-gray-700 rounded cursor-pointer"
      onClick={() => handleItemClick('products', item)}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm">{(item.productnumber || '').replace(/^#/, '')}</span>
          {item.model && (
            <span className="text-sm text-gray-600 dark:text-gray-400">{item.model}</span>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-500">
          {item.brand_name && <span>{item.brand_name}</span>}
          {item.type_name && <span>• {item.type_name}</span>}
          {item.color_name && <span>• {item.color_name}</span>}
        </div>
      </div>
      <div className="text-right">
        {item.price && (
          <div className="text-sm font-medium text-green-600 dark:text-green-400">
            {formatPrice(item.price)}
          </div>
        )}
        <div className="text-xs text-gray-500 dark:text-gray-500">
          К-сть: {item.quantity || 0}
        </div>
      </div>
    </div>
  );

  const renderClientItem = (item: any) => (
    <div 
      key={item.id}
      className="flex items-center justify-between p-2 hover:bg-gray-50 dark:hover:bg-gray-700 rounded cursor-pointer"
      onClick={() => handleItemClick('clients', item)}
    >
      <div className="flex-1 min-w-0">
        <div className="font-medium text-sm">{item.full_name}</div>
        <div className="text-xs text-gray-500 dark:text-gray-500">
          {item.phone_number && <span>{item.phone_number}</span>}
          {item.email && <span> • {item.email}</span>}
        </div>
      </div>
    </div>
  );

  const renderOrderItem = (item: any) => (
    <div 
      key={item.id}
      className="flex items-center justify-between p-2 hover:bg-gray-50 dark:hover:bg-gray-700 rounded cursor-pointer"
      onClick={() => handleItemClick('orders', item)}
    >
      <div className="flex-1 min-w-0">
        <div className="font-medium text-sm">#{item.order_number || item.id}</div>
        <div className="text-xs text-gray-500 dark:text-gray-500">
          {item.client_name && <span>{item.client_name}</span>}
          {item.status && <span> • {item.status}</span>}
        </div>
      </div>
      {item.total_amount && (
        <div className="text-sm font-medium text-green-600 dark:text-green-400">
          {formatPrice(item.total_amount)}
        </div>
      )}
    </div>
  );

  const categories = [
    {
      key: 'products',
      label: 'Товари',
      icon: '📦',
      data: results.results.products,
      renderItem: renderProductItem
    },
    {
      key: 'clients',
      label: 'Клієнти',
      icon: '👥',
      data: results.results.clients,
      renderItem: renderClientItem
    },
    {
      key: 'orders',
      label: 'Замовлення',
      icon: '📋',
      data: results.results.orders,
      renderItem: renderOrderItem
    },
    {
      key: 'suppliers',
      label: 'Постачальники',
      icon: '🚚',
      data: results.results.suppliers,
      renderItem: (item: any) => (
        <div key={item.id} className="p-2 text-sm text-gray-500">
          {item.name || `Постачальник #${item.id}`}
        </div>
      )
    },
    {
      key: 'deliveries',
      label: 'Поставки',
      icon: '📦',
      data: results.results.deliveries,
      renderItem: (item: any) => (
        <div key={item.id} className="p-2 text-sm text-gray-500">
          {item.delivery_name || `Поставка #${item.id}`}
        </div>
      )
    }
  ];

  const hasResults = categories.some(cat => cat.data && cat.data.total > 0);

  if (!hasResults) {
    return (
      <div className="text-center py-8 text-gray-500 dark:text-gray-400">
        <div className="text-lg mb-2">🔍</div>
        <div>Нічого не знайдено за запитом "{results.query}"</div>
        <div className="text-sm mt-1">Спробуйте інший пошуковий термін</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {categories.map(category => {
        if (!category.data || category.data.total === 0) return null;

        return (
          <div key={category.key} className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
            <div className="flex items-center justify-between p-3 border-b border-gray-200 dark:border-gray-700">
              <div className="flex items-center gap-2">
                <span className="text-lg">{category.icon}</span>
                <span className="font-semibold text-gray-900 dark:text-gray-100">
                  {category.label}
                </span>
                <span className="text-sm text-gray-500 dark:text-gray-500">
                  ({category.data.total})
                </span>
              </div>
              
              {category.data.total > category.data.items.length && (
                <button
                  onClick={() => handleCategoryClick(category.key)}
                  className="text-sm text-blue-600 dark:text-blue-400 hover:underline"
                >
                  Показати всі →
                </button>
              )}
            </div>
            
            <div className="divide-y divide-gray-100 dark:divide-gray-700">
              {category.data.items.map(item => category.renderItem(item))}
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default SearchResultsPreview;
