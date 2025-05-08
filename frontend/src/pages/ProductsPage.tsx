import React, { useState, useEffect } from 'react';
import MainLayout from '../layouts/MainLayout';

// Placeholder for actual filter components for Products
const ProductsFilterPanelContent: React.FC = () => {
  return (
    <div>
      <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Ціна</h3>
      {/* Placeholder for Price Slider */}
      <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Price Slider Area</div>

      <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Заміри (СМ)</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Measurements Slider Area</div>

      <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Розмір</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Size Slider Area</div>

      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Бренд</h3>
      {/* Placeholder for Brand checkboxes with search */}
      <div className="p-2 border border-dashed rounded mb-4 h-24 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Brand Filter Area</div>
      
      {/* Add other filter sections (Вид, Модель, Стать, Країна, Постачальник, Стан) as placeholders */}
      <p className="text-xs text-center text-gray-400 dark:text-gray-500">More filters here...</p>
    </div>
  );
};

interface ProductsPageProps {
  currentSearchTerm: string; // Receive search term from App
}

const ProductsPage: React.FC<ProductsPageProps> = ({ currentSearchTerm }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Effect to react to global search changes if needed for filtering
  useEffect(() => {
    if (currentSearchTerm !== undefined) {
        console.log('ProductsPage received search term:', currentSearchTerm);
        // TODO: Implement actual filtering logic based on currentSearchTerm
    }
  }, [currentSearchTerm]);

  const handleRefresh = () => {
    console.log('Refreshing products...');
    setIsRefreshing(true);
    setTimeout(() => {
      setIsRefreshing(false);
      console.log('Products refreshed!');
    }, 1500);
  };

  const handleResetFilters = () => {
    console.log('Resetting product filters...');
    // Logic to reset all filter states for products
  };

  return (
    <MainLayout
      filterPanelContent={<ProductsFilterPanelContent />}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      {/* Main content for Products Page */}
      <div className="p-4 bg-white dark:bg-gray-800 shadow-md rounded-lg">
        <div className="flex justify-between items-center mb-4">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Товари</h1>
          {/* Display the search term received from props */}
          {currentSearchTerm && <p className='text-sm text-gray-500 dark:text-gray-400'>Активний пошук: "{currentSearchTerm}"</p>}
        </div>
        
        {/* Placeholder for ProductsTable */}
        <div className="table-placeholder min-h-[60vh] border border-dashed border-gray-300 dark:border-gray-600 rounded flex justify-center items-center">
          <p className="text-gray-500 dark:text-gray-400">Таблиця товарів буде тут</p>
        </div>

        <div className="mt-4 flex items-center">
          <input 
            type="checkbox" 
            id="show-unsold-products"
            className="h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 dark:focus:ring-primary-400 dark:bg-gray-700 dark:border-gray-600"
          />
          <label htmlFor="show-unsold-products" className="ml-2 text-sm text-gray-700 dark:text-gray-300">
            Показувати тільки непродані
          </label>
        </div>
      </div>
    </MainLayout>
  );
};

export default ProductsPage; 