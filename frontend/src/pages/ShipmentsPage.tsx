import React, { useState, useEffect } from 'react';
import MainLayout from '../layouts/MainLayout';
import ShipmentsTable from '../components/shipments/ShipmentsTable';

const ShipmentsFilterPanelContent: React.FC = () => {
  return (
    <div>
      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Постачальник</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-20 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Supplier Filter</div>

      <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Група</h3>
      <div className="p-2 border border-dashed rounded mb-4 h-20 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Group Filter</div>

      <p className="text-xs text-center text-gray-400 dark:text-gray-500 mt-6">More shipment filters...</p>
    </div>
  );
};

interface ShipmentsPageProps {
  currentSearchTerm: string;
}

const ShipmentsPage: React.FC<ShipmentsPageProps> = ({ currentSearchTerm }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  useEffect(() => {
    if (currentSearchTerm !== undefined) {
      console.log('ShipmentsPage received search term:', currentSearchTerm);
    }
  }, [currentSearchTerm]);

  const handleRefresh = () => {
    setIsRefreshing(true);
    setTimeout(() => {
      setIsRefreshing(false);
    }, 1500);
  };

  const handleResetFilters = () => {
    console.log('Resetting shipment filters...');
  };

  return (
    <MainLayout
      filterPanelContent={<ShipmentsFilterPanelContent />}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      <div className="p-4 bg-white dark:bg-gray-800 shadow-md rounded-lg">
        <div className="flex justify-between items-center mb-4">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Поставки</h1>
          {currentSearchTerm && <p className='text-sm text-gray-500 dark:text-gray-400'>Активний пошук: "{currentSearchTerm}"</p>}
        </div>

        <ShipmentsTable />
      </div>
    </MainLayout>
  );
};

export default ShipmentsPage;
