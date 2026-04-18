import React, { useState, Suspense } from 'react';
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

// Logo Component that switches based on theme
const AppLogo: React.FC = () => {
    const { theme } = useTheme();
    const logoSrc = theme === 'dark' 
        ? '/assets/logo/logo_dark.png'
        : '/assets/logo/logo.png';
    
    return (
        <img 
            src={logoSrc} 
            alt="Логотип BMS"
            className="h-12 w-auto" // Increased height from h-10 to h-12
        />
    );
};

const ThemeSwitcherButton: React.FC = () => {
  const { theme, toggleTheme } = useTheme();
  return (
    <button
      onClick={toggleTheme}
      aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      className="p-2 rounded-md bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 transition-colors duration-150 focus:outline-none focus:ring-1 focus:ring-primary-500"
    >
      {theme === 'dark' ? '☀️' : '🌙'}
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
      className="p-2 rounded-md bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 transition-colors duration-150 focus:outline-none focus:ring-1 focus:ring-primary-500"
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

  const [currentSearchTerm, setCurrentSearchTerm] = useState<string>('');
  const [parsingDialogOpen, setParsingDialogOpen] = useState(false);
  
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
        toast.info(`JobId: ${data.jobId}`);
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
          toast.warn(`Fallback JobId: ${data2.jobId}`);
        } else {
          console.error('[App.tsx] No jobId in response!', data);
          toast.error('ПОМИЛКА: Немає jobId в відповіді!');
        }
      }
      
      setParsingDialogOpen(false);
    } catch (error) {
      console.error('Error starting parsing:', error);
      toast.error(`Помилка запуску парсингу: ${error instanceof Error ? error.message : String(error)}`);
    }
  };

  return (
    <div className="app-container min-h-screen flex flex-col bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100 transition-colors duration-300">
      <nav className="flex space-x-1 px-3 pt-2 bg-gray-100 dark:bg-gray-800">
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            aria-current={activeTab === tab.key ? 'page' : undefined}
            className={`px-3 py-1 text-sm font-medium border rounded-t-md transition-colors duration-150 focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary-500 
              ${
                activeTab === tab.key
                  ? 'border-gray-300 border-b-white dark:border-gray-600 dark:border-b-gray-800 bg-white dark:bg-gray-800 text-primary-700 dark:text-primary-300'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-100 hover:bg-gray-200 dark:hover:bg-gray-700'
              }
            `}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <header className="p-2 px-3 bg-white dark:bg-gray-800 shadow-sm sticky top-0 z-40 border-b border-gray-200 dark:border-gray-700">
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
              className="p-2 rounded-md bg-blue-100 hover:bg-blue-200 dark:bg-blue-900 dark:hover:bg-blue-800 text-blue-700 dark:text-blue-200 transition-colors duration-150 focus:outline-none focus:ring-1 focus:ring-blue-500 text-sm font-medium px-3"
            >
              ⚡ Парсинг
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
