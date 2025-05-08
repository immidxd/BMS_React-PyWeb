import React, { useState, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { AppThemeProvider, useTheme } from './contexts/ThemeContext';
import { FilterPanelProvider, useFilterPanel } from './contexts/FilterPanelContext';
import GlobalStyle from './styles/GlobalStyle'; // For potential global styles
import SearchBar from './components/common/SearchBar';
import './App.css';
import './index.css'; // Main Tailwind CSS import

// Lazy load pages
const ProductsPage = React.lazy(() => import('./pages/ProductsPage'));
const OrdersPage = React.lazy(() => import('./pages/OrdersPage'));
const ClientsPage = React.lazy(() => import('./pages/ClientsPage'));
const SuppliersPage = React.lazy(() => import('./pages/SuppliersPage'));
const DeliveriesPage = React.lazy(() => import('./pages/DeliveriesPage'));

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

type TabKey = 'products' | 'orders' | 'clients' | 'suppliers' | 'deliveries';

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
  { key: 'deliveries', label: 'Поставки', component: DeliveriesPage },
];

const AppContent: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabKey>(TABS[0].key);
  const ActivePageComponent = TABS.find(tab => tab.key === activeTab)?.component;

  const [currentSearchTerm, setCurrentSearchTerm] = useState('');
  const handleGlobalSearch = (term: string) => {
    console.log('Global search triggered:', term);
    setCurrentSearchTerm(term);
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
            <SearchBar onSearch={handleGlobalSearch} placeholder={`Пошук у розділі "${TABS.find(t=>t.key===activeTab)?.label}"...`} />
          </div>
          
          <div className="flex items-center space-x-2 flex-shrink-0">
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
    </div>
  );
};

const App: React.FC = () => {
  return (
    <Router basename={(process.env.PUBLIC_URL || '').replace(/^\/\//, '/')}> 
      <AppThemeProvider>
        <FilterPanelProvider>
          <GlobalStyle /> 
          <Routes>
            <Route path="/*" element={<AppContent />} /> 
          </Routes>
        </FilterPanelProvider>
      </AppThemeProvider>
    </Router>
  );
};

export default App;