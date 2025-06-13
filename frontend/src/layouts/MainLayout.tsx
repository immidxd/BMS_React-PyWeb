import React, { useState } from 'react';
import RefreshButton from '../components/common/RefreshButton';
import FilterPanel from '../components/common/FilterPanel';
import { useFilterPanel } from '../contexts/FilterPanelContext';
import { ParsingDialog } from '../components/ParsingDialog';
import { ParsingStatus } from '../components/ParsingStatus';

interface MainLayoutProps {
  children: React.ReactNode;
  filterPanelContent: React.ReactNode;
  onRefresh: () => void;
  isRefreshing?: boolean;
  onResetFilters: () => void;
}

const MainLayout: React.FC<MainLayoutProps> = ({
  children,
  filterPanelContent,
  onRefresh,
  isRefreshing = false,
  onResetFilters,
}) => {
  const { isFilterPanelOpen, openFilterPanel, closeFilterPanel } = useFilterPanel();
  const [parsingDialogOpen, setParsingDialogOpen] = useState(false);

  const handleRefreshClick = () => {
    setParsingDialogOpen(true);
  };

  const handleStartParsing = async (mode: string, params: any) => {
    try {
      const response = await fetch('/api/parsing/parsing/start', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ mode, params }),
      });
      
      if (!response.ok) {
        throw new Error('Failed to start parsing');
      }
    } catch (error) {
      console.error('Error starting parsing:', error);
    }
  };

  return (
    <div className="main-layout flex flex-col h-full">
      <div className="flex flex-row flex-grow gap-4">
        <div 
          className="hidden sm:block fixed left-0 top-0 bottom-0 w-4 z-30 cursor-pointer"
          onMouseEnter={openFilterPanel}
        >
          <div className="h-full w-px bg-gray-300 dark:bg-gray-600 opacity-50 hover:opacity-100 transition-opacity"></div>
        </div>

        <div className="content-area flex-grow w-full">
          {children}
        </div>
      </div>
      
      <div className="fixed bottom-6 right-6 z-20">
        <RefreshButton onClick={handleRefreshClick} isLoading={isRefreshing} />
      </div>

      <FilterPanel 
        isOpen={isFilterPanelOpen} 
        onClose={closeFilterPanel}
        onResetFilters={onResetFilters}
      >
        {filterPanelContent}
      </FilterPanel>

      {/* Діалог парсингу */}
      <ParsingDialog
        open={parsingDialogOpen}
        onClose={() => setParsingDialogOpen(false)}
        onStartParsing={handleStartParsing}
      />

      {/* Статус парсингу */}
      <ParsingStatus />
    </div>
  );
};

export default MainLayout; 