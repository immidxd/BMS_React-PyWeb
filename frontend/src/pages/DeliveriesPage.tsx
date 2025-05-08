import React, { useState, useEffect } from 'react';
import MainLayout from '../layouts/MainLayout';

// Placeholder for actual filter components for Deliveries
const DeliveriesFilterPanelContent: React.FC = () => {
  return (
    <div>
      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Постачальник</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-20 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Supplier Filter (Dropdown)</div>
      
      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Статус поставки</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-20 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Delivery Status Filter</div>

      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Дата відправлення</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-12 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Ship Date Range Picker</div>

      <p className="text-xs text-center text-gray-400 dark:text-gray-500 mt-6">More delivery filters...</p>
    </div>
  );
};

interface DeliveriesPageProps {
  currentSearchTerm: string;
}

const DeliveriesPage: React.FC<DeliveriesPageProps> = ({ currentSearchTerm }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  useEffect(() => {
    if (currentSearchTerm !== undefined) {
        console.log('DeliveriesPage received search term:', currentSearchTerm);
        // TODO: Implement filtering
    }
  }, [currentSearchTerm]);

  const handleRefresh = () => {
    console.log('Refreshing deliveries...');
    setIsRefreshing(true);
    setTimeout(() => {
      setIsRefreshing(false);
      console.log('Deliveries refreshed!');
    }, 1500);
  };

  const handleResetFilters = () => {
    console.log('Resetting delivery filters...');
  };

  return (
    <MainLayout
      filterPanelContent={<DeliveriesFilterPanelContent />}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      <div className="p-4 bg-white dark:bg-gray-800 shadow-md rounded-lg">
        <div className="flex justify-between items-center mb-4">
            <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Поставки</h1>
            {currentSearchTerm && <p className='text-sm text-gray-500 dark:text-gray-400'>Активний пошук: "{currentSearchTerm}"</p>}
        </div>
        
        <div className="table-placeholder min-h-[60vh] border border-dashed border-gray-300 dark:border-gray-600 rounded flex justify-center items-center">
          <p className="text-gray-500 dark:text-gray-400">Таблиця поставок буде тут</p>
        </div>
      </div>
    </MainLayout>
  );
};

export default DeliveriesPage; 