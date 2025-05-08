import React, { useState, useEffect } from 'react';
import MainLayout from '../layouts/MainLayout';

// Placeholder for actual filter components for Suppliers
const SuppliersFilterPanelContent: React.FC = () => {
  return (
    <div>
      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Країна</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-20 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Country Filter</div>
      
      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Тип товарів</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-20 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Product Type Filter</div>

      <div className="flex items-center mt-4">
        <input 
            type="checkbox" 
            id="active-contracts-suppliers"
            className="h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 dark:focus:ring-primary-400 dark:bg-gray-700 dark:border-gray-600"
        />
        <label htmlFor="active-contracts-suppliers" className="ml-2 text-sm text-gray-700 dark:text-gray-300">
            Активні договори
        </label>
      </div>
      
      <p className="text-xs text-center text-gray-400 dark:text-gray-500 mt-6">More supplier filters...</p>
    </div>
  );
};

interface SuppliersPageProps {
  currentSearchTerm: string;
}

const SuppliersPage: React.FC<SuppliersPageProps> = ({ currentSearchTerm }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  useEffect(() => {
    if (currentSearchTerm !== undefined) {
        console.log('SuppliersPage received search term:', currentSearchTerm);
        // TODO: Implement filtering
    }
  }, [currentSearchTerm]);

  const handleRefresh = () => {
    console.log('Refreshing suppliers...');
    setIsRefreshing(true);
    setTimeout(() => {
      setIsRefreshing(false);
      console.log('Suppliers refreshed!');
    }, 1500);
  };

  const handleResetFilters = () => {
    console.log('Resetting supplier filters...');
  };

  return (
    <MainLayout
      filterPanelContent={<SuppliersFilterPanelContent />}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      <div className="p-4 bg-white dark:bg-gray-800 shadow-md rounded-lg">
        <div className="flex justify-between items-center mb-4">
            <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Постачальники</h1>
            {currentSearchTerm && <p className='text-sm text-gray-500 dark:text-gray-400'>Активний пошук: "{currentSearchTerm}"</p>}
        </div>

        <div className="table-placeholder min-h-[60vh] border border-dashed border-gray-300 dark:border-gray-600 rounded flex justify-center items-center">
          <p className="text-gray-500 dark:text-gray-400">Таблиця постачальників буде тут</p>
        </div>
      </div>
    </MainLayout>
  );
};

export default SuppliersPage; 