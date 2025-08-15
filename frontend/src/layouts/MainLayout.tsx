import React, { useEffect, useState } from 'react';
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
  const [currentJobId, setCurrentJobId] = useState<number | null>(null);
  const [isParsing, setIsParsing] = useState(false);
  const [hideButtonUntil, setHideButtonUntil] = useState<number | null>(null);
  const prevIsRunningRef = React.useRef<boolean>(false);

  const handleRefreshClick = () => {
    setParsingDialogOpen(true);
  };

  // legacy global banner visibility kept (optional)
  useEffect(() => {
    let isMounted = true;
    const poll = async () => {
      try {
        const res = await fetch('/api/parsing/status');
        const data = await res.json();
        if (!isMounted) return;
        const running = Boolean(data?.is_running);
        if (!running && prevIsRunningRef.current) setHideButtonUntil(Date.now() + 3000);
        prevIsRunningRef.current = running;
        setIsParsing(running);
      } catch {}
    };
    poll();
    const id = setInterval(poll, 1500);
    return () => { isMounted = false; clearInterval(id); };
  }, []);

  const handleStartParsing = async (mode: string, params: any) => {
    try {
      const res = await fetch(`/api/parsing/run?mode=${encodeURIComponent(mode)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params || {})
      });
      const data = await res.json();
      console.log('[MainLayout] run response:', data);
      if (!res.ok || !data?.jobId) throw new Error('run failed');
      setCurrentJobId(data.jobId);
      // Закриваємо меню вибору відразу після старту
      setParsingDialogOpen(false);
    } catch (e) {
      console.error('[MainLayout] quick parsing error:', e);
    }
  };

return (
  <div className="main-layout flex flex-col h-screen relative">{/* тягнемося на весь екран */}
      <div className="flex flex-row flex-grow gap-4 overflow-hidden px-2 sm:px-4">{/* трохи більше корисної ширини */}
        <div 
          className="hidden sm:block fixed left-0 top-0 bottom-0 w-4 z-30 cursor-pointer"
          onMouseEnter={openFilterPanel}
        >
          <div className="h-full w-px bg-gray-300 dark:bg-gray-600 opacity-50 hover:opacity-100 transition-opacity"></div>
        </div>

        <div className="content-area flex-grow w-full overflow-auto">{/* контент займає доступну висоту */}
          {children}
        </div>
      </div>
      
      {/* Плаваюча кнопка оновлення поверх усього UI (прихована під час активного парсингу) */}
      {!isParsing && !(hideButtonUntil && Date.now() < hideButtonUntil) && (
        <div className="fixed right-6 bottom-6 z-[9999] drop-shadow-lg">
          <RefreshButton onClick={handleRefreshClick} isLoading={isRefreshing} />
        </div>
      )}

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
        onClose={() => { setParsingDialogOpen(false); setCurrentJobId(null); }}
        onStartParsing={handleStartParsing}
      />

      {/* Статус парсингу */}
      <ParsingStatus jobId={currentJobId} />
    </div>
  );
};

export default MainLayout; 