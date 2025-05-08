import React, { useState, useEffect } from 'react';
import MainLayout from '../layouts/MainLayout';

// Placeholder for actual filter components for Orders
const OrdersFilterPanelContent: React.FC = () => {
  return (
    <div>
      <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Рік</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Year Slider Area</div>

      <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Місяць</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Month Slider Area</div>

      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Статус відповіді</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-24 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Response Status Filter</div>
      
      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Статус оплати</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-24 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Payment Status Filter</div>

      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Доставка</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-24 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Delivery Method Filter</div>
      
      <button className="mt-4 w-full p-2 border border-primary-500 text-primary-500 rounded hover:bg-primary-50 dark:hover:bg-gray-700">
        Обрати дату (календар)
      </button>
    </div>
  );
};

interface OrdersPageProps {
  currentSearchTerm: string;
}

const OrdersPage: React.FC<OrdersPageProps> = ({ currentSearchTerm }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  useEffect(() => {
    if (currentSearchTerm !== undefined) {
      console.log('OrdersPage received search term:', currentSearchTerm);
      // TODO: Implement filtering
    }
  }, [currentSearchTerm]);

  const handleRefresh = () => {
    console.log('Refreshing orders...');
    setIsRefreshing(true);
    setTimeout(() => {
      setIsRefreshing(false);
      console.log('Orders refreshed!');
    }, 1500);
  };

  const handleResetFilters = () => {
    console.log('Resetting order filters...');
  };

  return (
    <MainLayout
      filterPanelContent={<OrdersFilterPanelContent />}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      <div className="p-4 bg-white dark:bg-gray-800 shadow-md rounded-lg">
        <div className="flex justify-between items-center mb-4">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Замовлення</h1>
          {currentSearchTerm && <p className='text-sm text-gray-500 dark:text-gray-400'>Активний пошук: "{currentSearchTerm}"</p>}
        </div>

        <div className="table-placeholder min-h-[60vh] border border-dashed border-gray-300 dark:border-gray-600 rounded flex justify-center items-center">
          <p className="text-gray-500 dark:text-gray-400">Таблиця замовлень буде тут</p>
        </div>

        <div className="mt-4 flex items-center space-x-6">
          <div className="flex items-center">
            <input 
              type="checkbox" 
              id="show-unpaid-orders"
              className="h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 dark:focus:ring-primary-400 dark:bg-gray-700 dark:border-gray-600"
            />
            <label htmlFor="show-unpaid-orders" className="ml-2 text-sm text-gray-700 dark:text-gray-300">
              Тільки неоплачені
            </label>
          </div>
          <div className="flex items-center">
            <input 
              type="checkbox" 
              id="show-unpacked-orders"
              className="h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 dark:focus:ring-primary-400 dark:bg-gray-700 dark:border-gray-600"
            />
            <label htmlFor="show-unpacked-orders" className="ml-2 text-sm text-gray-700 dark:text-gray-300">
              Неспаковані
            </label>
          </div>
        </div>
      </div>
    </MainLayout>
  );
};

export default OrdersPage; 