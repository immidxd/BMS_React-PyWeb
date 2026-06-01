import React, { useState, useEffect, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { AppThemeProvider, useTheme } from './contexts/ThemeContext';
import { FilterPanelProvider, useFilterPanel } from './contexts/FilterPanelContext';
import GlobalStyle from './styles/GlobalStyle'; // For potential global styles
import SearchBar from './components/common/SearchBar';
import { ParsingDialog } from './components/ParsingDialog';
import { ParsingStatus } from './components/ParsingStatus';
import { ToastContainer, toast } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';
import './App.css';
import './index.css'; // Main Tailwind CSS import

// Lazy load pages
const ProductsPage = React.lazy(() => import('./pages/ProductsPage'));
const OrdersPage = React.lazy(() => import('./pages/OrdersPage'));
const ClientsPage = React.lazy(() => import('./pages/ClientsPage'));
const SuppliersPage = React.lazy(() => import('./pages/SuppliersPage'));
const ShipmentsPage = React.lazy(() => import('./pages/ShipmentsPage'));
const StatisticsPage = React.lazy(() => import('./pages/StatisticsPage'));
const BrandsPage = React.lazy(() => import('./pages/BrandsPage'));
const PublicationsPage = React.lazy(() => import('./pages/PublicationsPage'));
const WarehousePage = React.lazy(() => import('./pages/WarehousePage'));

// Logo — мінімалістичний BMS-знак (3 риски + текст), у дусі дизайн-системи
const AppLogo: React.FC = () => {
    const { theme } = useTheme();
    const dark = theme === 'dark';
    const stroke = dark ? '#e4e4e4' : '#111';
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }} aria-label="Логотип BMS">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <div style={{ height: 3, width: 22, borderRadius: 1, background: stroke }} />
                <div style={{ height: 3, width: 16, borderRadius: 1, background: stroke }} />
                <div style={{ height: 3, width: 10, borderRadius: 1, background: stroke }} />
            </div>
            <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: '0.3px', color: stroke }}>BMS</div>
        </div>
    );
};

const ThemeSwitcherButton: React.FC = () => {
  const { theme, toggleTheme } = useTheme();
  return (
    <button
      onClick={toggleTheme}
      aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      className="bms-icon-btn"
    >
      {theme === 'dark' ? '☀' : '☾'}
    </button>
  );
};

const FilterToggleButton: React.FC = () => {
  const { toggleFilterPanel, isFilterPanelOpen } = useFilterPanel();
  return (
    <button
      onClick={toggleFilterPanel}
      aria-label="Відкрити/закрити фільтри"
      aria-expanded={isFilterPanelOpen}
      className="bms-icon-btn"
    >
      <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
        <path fillRule="evenodd" d="M3 3a1 1 0 011-1h12a1 1 0 011 1v3a1 1 0 01-.293.707L12 12.414V17a1 1 0 01-1.447.894l-2-1A1 1 0 018 16.002V12.414L3.293 6.707A1 1 0 013 6V3z" clipRule="evenodd" />
      </svg>
    </button>
  );
};

type TabKey = 'products' | 'orders' | 'clients' | 'suppliers' | 'deliveries' | 'brands' | 'publications' | 'warehouse' | 'statistics';

interface TabConfig {
  key: TabKey;
  label: string;
  component: React.LazyExoticComponent<React.ComponentType<any>>;
}

const TABS: TabConfig[] = [
  { key: 'products', label: 'Товари', component: ProductsPage },
  { key: 'orders', label: 'Замовлення', component: OrdersPage },
  { key: 'clients', label: 'Клієнти', component: ClientsPage },
  { key: 'suppliers', label: 'Постачальники', component: SuppliersPage },
  { key: 'deliveries', label: 'Поставки', component: ShipmentsPage },
  { key: 'brands', label: 'Бренди', component: BrandsPage },
  { key: 'publications', label: 'Публікації', component: PublicationsPage },
  { key: 'warehouse', label: 'Склад', component: WarehousePage },
  { key: 'statistics', label: 'Статистика', component: StatisticsPage },
];

const AppContent: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabKey>(TABS[0].key);
  const ActivePageComponent = TABS.find(tab => tab.key === activeTab)?.component;
  const { toggleFilterPanel } = useFilterPanel();

  const [currentSearchTerm, setCurrentSearchTerm] = useState<string>('');
  const [parsingDialogOpen, setParsingDialogOpen] = useState(false);

  // ── Гарячі клавіші (кросплатформно: ⌘ на macOS, Ctrl на Windows/Linux) ─────
  //   ⌘/Ctrl + 1..9 — перемкнути вкладку
  //   ⌘/Ctrl + B     — показати/сховати панель фільтрів
  //   ⌘/Ctrl + K     — фокус у рядок пошуку
  //   ⌘/Ctrl + ←/→   — попередня/наступна вкладка
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod || e.altKey) return;

      // Цифри 1..9 → конкретна вкладка
      if (e.key >= '1' && e.key <= '9') {
        const idx = parseInt(e.key, 10) - 1;
        if (idx < TABS.length) {
          e.preventDefault();
          setActiveTab(TABS[idx].key);
        }
        return;
      }
      const k = e.key.toLowerCase();
      if (k === 'b') {                      // фільтри
        e.preventDefault();
        toggleFilterPanel();
      } else if (k === 'k') {               // пошук
        e.preventDefault();
        const el = document.querySelector(
          'input[placeholder^="Пошук"], input[type="search"]'
        ) as HTMLInputElement | null;
        el?.focus();
        el?.select();
      } else if (e.key === 'ArrowRight') {  // наступна вкладка
        e.preventDefault();
        setActiveTab(prev => {
          const i = TABS.findIndex(t => t.key === prev);
          return TABS[(i + 1) % TABS.length].key;
        });
      } else if (e.key === 'ArrowLeft') {   // попередня вкладка
        e.preventDefault();
        setActiveTab(prev => {
          const i = TABS.findIndex(t => t.key === prev);
          return TABS[(i - 1 + TABS.length) % TABS.length].key;
        });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggleFilterPanel]);

  // Крос-вкладкова навігація: картка товару → "Показати в замовленнях".
  // ProductsTable кладе фільтр у localStorage і шле подію; ми перемикаємо таб,
  // OrdersPage читає pending-фільтр при монтуванні (+ повторна подія для надійності).
  useEffect(() => {
    const onSwitchToOrders = () => {
      setActiveTab('orders');
      // дати OrdersPage змонтуватись, потім підштовхнути перечитати фільтр
      setTimeout(() => window.dispatchEvent(new CustomEvent('bms:orders-show-product')), 50);
    };
    window.addEventListener('bms:switch-to-orders', onSwitchToOrders);
    return () => window.removeEventListener('bms:switch-to-orders', onSwitchToOrders);
  }, []);
  
  const handleGlobalSearch = (term: string) => {
    console.log('Global search triggered:', term);
    setCurrentSearchTerm(term);
  };

  const [currentJobId, setCurrentJobId] = useState<number | null>(null);

  const handleStartParsing = async (mode: string, params: any) => {
    try {
      // ПРИМІТКА: показуємо прогрес одразу, поки чекаємо відповідь API (jobId=-1 як тимчасовий),
      // щоб уникнути відсутності прогрес-вікна до приходу реального jobId
      if (!currentJobId) setCurrentJobId(-1 as any);
      const response = await fetch(`/api/parsing/run?mode=${encodeURIComponent(mode)}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ params: params || {} }),
      });
      
      if (!response.ok) {
        throw new Error('Failed to start parsing');
      }
      
      const data = await response.json();
      console.log('[App.tsx] Parsing started:', data);
      console.log('[App.tsx] API Response:', JSON.stringify(data));
      
      // Встановлюємо jobId для відображення прогресу (не скидаємо при закритті діалогу)
      if (data.jobId) {
        setCurrentJobId(data.jobId);
        console.log('[App.tsx] JobId set:', data.jobId);
        toast.success('Парсинг запущено');
      } else {
        // Fallback: створюємо job без запуску
        const res2 = await fetch(`/api/parsing/test?mode=${encodeURIComponent(mode)}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(params || {}),
        });
        const data2 = await res2.json();
        if (res2.ok && data2.jobId) {
          setCurrentJobId(data2.jobId);
          console.log('[App.tsx] Fallback JobId set:', data2.jobId);
        } else {
          console.error('[App.tsx] No jobId in response!', data);
          toast.error('Не вдалося запустити парсинг');
        }
      }
      
      setParsingDialogOpen(false);
    } catch (error) {
      console.error('Error starting parsing:', error);
      toast.error(`Помилка запуску парсингу: ${error instanceof Error ? error.message : String(error)}`);
    }
  };

  return (
    <div className="bms-root app-container min-h-screen flex flex-col">
      <nav className="bms-tabs">
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            aria-current={activeTab === tab.key ? 'page' : undefined}
            className={`bms-tab ${activeTab === tab.key ? 'is-active' : ''}`}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <header className="bms-header">
        <div className="flex items-center space-x-4 w-full">
          <div className="flex-shrink-0">
             <AppLogo />
          </div>

          <div className="flex-grow min-w-0">
            <SearchBar
              onSearch={handleGlobalSearch}
              placeholder={`Пошук у розділі "${TABS.find(t=>t.key===activeTab)?.label}"...`}
              showGlobalResults={true}
              currentScope={activeTab}
            />
          </div>

          <div className="flex items-center space-x-2 flex-shrink-0">
            <button
              onClick={() => setParsingDialogOpen(true)}
              aria-label="Запустити парсинг"
              title="Парсинг Google Sheets"
              className="bms-btn bms-btn-secondary"
            >
              <span className="bms-btn-badge">⚡</span>
              Парсинг
            </button>
            <FilterToggleButton />
            <ThemeSwitcherButton />
          </div>
        </div>
      </header>

      <main className="flex-grow p-4 container mx-auto w-full">
        <Suspense 
          fallback={
            <div className="flex justify-center items-center h-64">
              <p className="text-lg text-gray-500 dark:text-gray-400">Завантаження сторінки...</p>
            </div>
          }
        >
          {ActivePageComponent && <ActivePageComponent currentSearchTerm={currentSearchTerm} />}
        </Suspense>
      </main>

      {/* Діалог парсингу */}
      <ParsingDialog
        open={parsingDialogOpen}
        onClose={() => setParsingDialogOpen(false)}
        onStartParsing={handleStartParsing}
      />

      {/* Статус парсингу */}
      <ParsingStatus jobId={currentJobId} onComplete={() => setCurrentJobId(null)} />
    </div>
  );
};

const App: React.FC = () => {
  return (
    <Router basename={(process.env.PUBLIC_URL || '').replace(/^\/\//, '/')}> 
      <AppThemeProvider>
        <FilterPanelProvider>
          <GlobalStyle /> 
          <ToastContainer position="top-right" newestOnTop theme="dark" />
          <Routes>
            <Route path="/*" element={<AppContent />} /> 
          </Routes>
        </FilterPanelProvider>
      </AppThemeProvider>
    </Router>
  );
};

export default App;
