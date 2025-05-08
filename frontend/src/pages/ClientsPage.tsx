import React, { useState, useEffect } from 'react';
import MainLayout from '../layouts/MainLayout';

// Placeholder for actual filter components for Clients
const ClientsFilterPanelContent: React.FC = () => {
  return (
    <div>
      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Тип клієнта</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-20 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Client Type Filter</div>
      
      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Статус</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-20 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Client Status Filter</div>

      <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Загальна сума замовлень</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Total Order Value Slider</div>
      
      <p className="text-xs text-center text-gray-400 dark:text-gray-500">More client filters...</p>
    </div>
  );
};

interface ClientsPageProps {
  currentSearchTerm: string;
}

const ClientsPage: React.FC<ClientsPageProps> = ({ currentSearchTerm }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  useEffect(() => {
    if (currentSearchTerm !== undefined) {
        console.log('ClientsPage received search term:', currentSearchTerm);
        // TODO: Implement filtering
    }
  }, [currentSearchTerm]);

  const handleRefresh = () => {
    console.log('Refreshing clients...');
    setIsRefreshing(true);
    setTimeout(() => {
      setIsRefreshing(false);
      console.log('Clients refreshed!');
    }, 1500);
  };

  const handleResetFilters = () => {
    console.log('Resetting client filters...');
  };

  return (
    <MainLayout
      filterPanelContent={<ClientsFilterPanelContent />}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      <div className="p-4 bg-white dark:bg-gray-800 shadow-md rounded-lg">
        <div className="flex justify-between items-center mb-4">
            <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Клієнти</h1>
            {currentSearchTerm && <p className='text-sm text-gray-500 dark:text-gray-400'>Активний пошук: "{currentSearchTerm}"</p>}
        </div>
        
        <div className="table-placeholder min-h-[60vh] border border-dashed border-gray-300 dark:border-gray-600 rounded flex justify-center items-center">
          <p className="text-gray-500 dark:text-gray-400">Таблиця клієнтів буде тут</p>
        </div>
        {/* No specific checkboxes under table for clients in the initial request */}
      </div>
    </MainLayout>
  );
};

export default ClientsPage; 