import React from 'react';
// Removed unused context import to potentially resolve linter issue
// import { useFilterPanel } from '../contexts/FilterPanelContext'; 

interface FilterPanelProps {
  isOpen: boolean;
  onClose: () => void; // Keep onClose prop as MainLayout passes context close function
  onResetFilters: () => void;
  children: React.ReactNode; // Specific filters will be passed as children
  title?: string;
}

const FilterPanel: React.FC<FilterPanelProps> = ({
  isOpen,
  onClose, // Received from MainLayout (which got it from context)
  onResetFilters,
  children,
  title = "Фільтри пошуку"
}) => {
  // Can also get controls directly from context if needed here
  // const { closeFilterPanel } = useFilterPanel(); 
  
  // Using the passed onClose function (which comes from context via MainLayout)
  // for both the X button and the mouse leave event.

  return (
    <>
      {/* Overlay for when the panel is open */}
      {isOpen && (
        <div 
          className="fixed inset-0 bg-black bg-opacity-50 z-30 transition-opacity duration-300 ease-in-out md:hidden"
          onClick={onClose} // Use onClose from props
          aria-hidden="true"
        ></div>
      )}

      {/* Filter Panel */}
      <div
        className={`fixed top-0 left-0 h-full w-72 sm:w-80 bg-white dark:bg-gray-800 shadow-xl z-40
                   transform transition-transform duration-300 ease-in-out 
                   ${isOpen ? 'translate-x-0' : '-translate-x-full'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="filter-panel-title"
        onMouseLeave={onClose} // Use onClose from props
      >
        <div className="flex flex-col h-full">
          {/* Header */}
          <div className="flex justify-between items-center p-4 border-b border-gray-200 dark:border-gray-700">
            <h2 id="filter-panel-title" className="text-lg font-semibold text-gray-800 dark:text-gray-100">
              {title}
            </h2>
            <button 
              onClick={onClose} // Use onClose from props
              aria-label="Закрити панель фільтрів"
              className="p-1 rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              {/* Close Icon (X) */}
              <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Filter Content Area */}
          <div className="flex-grow p-4 overflow-y-auto space-y-6">
            {children}
          </div>

          {/* Footer / Reset Button */}
          <div className="p-4 border-t border-gray-200 dark:border-gray-700">
            <button
              onClick={onResetFilters}
              className="w-full px-4 py-2 bg-red-500 hover:bg-red-600 dark:bg-red-600 dark:hover:bg-red-700 text-white rounded-lg font-medium transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 dark:focus:ring-offset-gray-800"
            >
              Скинути фільтри пошуку
            </button>
          </div>
        </div>
      </div>
    </>
  );
};

export default FilterPanel; 