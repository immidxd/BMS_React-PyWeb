import React, { useEffect, useState } from 'react';
import RefreshButton from '../components/common/RefreshButton';
import FilterPanel from '../components/common/FilterPanel';
import { useFilterPanel } from '../contexts/FilterPanelContext';
import { ParsingDialog } from '../components/ParsingDialog';
import { ParsingStatus } from '../components/ParsingStatus';
import { toast } from 'react-toastify';

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
      // ПРИМІТКА: показуємо віджет одразу у стані "з’єднання..." через тимчасовий jobId=-1,
      // щоб уникнути ситуації, коли прогрес-вікно не з’являється до приходу реального jobId з API
      if (!currentJobId) setCurrentJobId(-1 as any);
      const res = await fetch(`/api/parsing/run?mode=${encodeURIComponent(mode)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ params: params || {} })
      });
      const data = await res.json();
      console.log('[MainLayout] test response:', data);
      toast.success('Парсинг запущено');
      
      if (!res.ok || !data?.jobId) throw new Error('run failed');
      setCurrentJobId(data.jobId);
      toast.info(`JobId: ${data.jobId}`);
      // Закриваємо меню вибору відразу після старту
      setParsingDialogOpen(false);
    } catch (e) {
      console.error('[MainLayout] run parsing error:', e);
      // Fallback: створюємо job без запуску, щоб показати прогрес-віджет і діагностувати
      try {
        const res2 = await fetch(`/api/parsing/test?mode=${encodeURIComponent(mode)}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params || {})
        });
        const data2 = await res2.json();
        if (res2.ok && data2?.jobId) {
          console.log('[MainLayout] fallback test jobId:', data2.jobId);
          setCurrentJobId(data2.jobId);
          setParsingDialogOpen(false);
          toast.warn(`Fallback jobId: ${data2.jobId}`);
          return;
        }
        throw new Error('fallback test failed');
      } catch (e2) {
        console.error('[MainLayout] fallback error:', e2);
        toast.error(`Помилка: ${(e as Error).message}`);
      }
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
      {!isParsing && !currentJobId && !(hideButtonUntil && Date.now() < hideButtonUntil) && (
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
        onClose={() => { setParsingDialogOpen(false); }}
        onStartParsing={handleStartParsing}
      />

      {/* Статус парсингу */}
      <ParsingStatus jobId={currentJobId} />
    </div>
  );
};

export default MainLayout; 
