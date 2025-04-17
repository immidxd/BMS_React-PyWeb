import React, { createContext, useContext, useState, useEffect } from 'react';
import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';
import styled from 'styled-components';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faFilter, faSearch, faMoon, faSun, faBox } from '@fortawesome/free-solid-svg-icons';
import ProductsTab from './pages/ProductsTab';
import OrdersTab from './pages/OrdersTab';
import { ProductsPage } from './pages/ProductsPage';
import ProductFilters from './components/filters/ProductFilters';
import OrderFilters from './components/filters/OrderFilters';
import { useTheme } from './contexts/ThemeContext';
import { QueryClient, QueryClientProvider } from 'react-query';
import { ProductFiltersOptions, ProductFilters as ProductFiltersType } from './services/productService';
import MainLayout from './components/layout/MainLayout';
import './App.css';

const { Content } = Layout;

// Створюємо контекст пошуку
interface SearchContextType {
  searchTerm: string;
  setSearchTerm: React.Dispatch<React.SetStateAction<string>>;
}

const SearchContext = createContext<SearchContextType>({
  searchTerm: '',
  setSearchTerm: () => {},
});

export const useSearch = () => useContext(SearchContext);

// Створюємо контекст фільтрів
interface FilterContextType {
  sidebarOpen: boolean;
  setSidebarOpen: React.Dispatch<React.SetStateAction<boolean>>;
}

const FilterContext = createContext<FilterContextType>({
  sidebarOpen: true,
  setSidebarOpen: () => {},
});

export const useFilter = () => useContext(FilterContext);

// Стильові компоненти
const AppContainer = styled.div`
  display: flex;
  flex-direction: column;
  height: 100vh;
  background-color: #f5f5f5;
`;

const TopNav = styled.div`
  display: flex;
  background-color: #f5f5f5;
  border-bottom: none;
`;

const TabContainer = styled.div`
  display: flex;
  height: 28px;
`;

const Tab = styled.div<{ active: boolean }>`
  padding: 0 12px;
  height: 28px;
  display: flex;
  align-items: center;
  cursor: pointer;
  background-color: ${props => props.active ? 'white' : '#e8e8e8'};
  color: #333;
  font-weight: ${props => props.active ? 'bold' : 'normal'};
  border-right: 1px solid #eee;
  border-bottom: ${props => props.active ? 'none' : '1px solid #eee'};
  font-size: 12px;
  
  &:hover {
    background-color: ${props => props.active ? 'white' : '#e0e0e0'};
  }
`;

const Header = styled.header`
  display: flex;
  align-items: center;
  height: 70px;
  padding: 0 20px;
  background-color: white;
  color: #333;
  border-bottom: none;
  box-shadow: 0 1px 4px rgba(0,0,0,0.05);
`;

const Logo = styled.div`
  margin-right: 25px;
  display: flex;
  align-items: center;
`;

const ThemeToggle = styled.button`
  background: none;
  border: none;
  cursor: pointer;
  color: #555;
  font-size: 26px;
  padding: 8px;
  margin-left: 15px;
  
  &:hover {
    color: #333;
  }
`;

const LogoImage = styled.img`
  height: 48px;
`;

const SearchContainer = styled.div`
  position: relative;
  flex: 1;
  max-width: none;
`;

const SearchInput = styled.input`
  width: 100%;
  padding: 12px 20px 12px 30px;
  border-radius: 6px;
  border: 1px solid #eee;
  background-color: #f8f8f8;
  color: #333;
  transition: background-color 0.2s;
  font-size: 16px;

  &:focus {
    outline: none;
    background-color: #fff;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }
`;

const SearchIcon = styled.div`
  position: absolute;
  left: 15px;
  top: 50%;
  transform: translateY(-50%);
  color: #888;
  font-size: 16px;
`;

const MainContainer = styled.div`
  display: flex;
  flex: 1;
  overflow: hidden;
  position: relative;
  background-color: white;
`;

const FilterTab = styled.div<{ isOpen: boolean }>`
  position: absolute;
  left: ${props => props.isOpen ? '300px' : '0'};
  width: 16px;
  height: 42px;
  background-color: ${props => props.isOpen ? 'transparent' : '#e8e8e8'};
  border: ${props => props.isOpen ? 'none' : '1px solid #ddd'};
  border-left: none;
  border-radius: 0 3px 3px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: ${props => props.isOpen ? '5' : '100'};
  transition: left 0.3s ease, opacity 0.2s ease;
  font-size: 10px;
  box-shadow: ${props => props.isOpen ? 'none' : '1px 1px 3px rgba(0,0,0,0.1)'};
  opacity: ${props => props.isOpen ? '0' : '1'};
  pointer-events: ${props => props.isOpen ? 'none' : 'auto'};
  
  &:hover {
    background-color: ${props => props.isOpen ? 'transparent' : '#d5d5d5'};
  }
`;

const FilterIcon = styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  cursor: move;
`;

const Sidebar = styled.div<{ isOpen: boolean }>`
  position: absolute;
  left: ${props => props.isOpen ? '0' : '-300px'};
  width: 300px;
  height: 100%;
  background-color: white;
  overflow-y: auto;
  transition: left 0.3s ease;
  border-right: 1px solid #f0f0f0;
  padding: 20px;
  z-index: 10;
  box-shadow: ${props => props.isOpen ? '0 0 10px rgba(0,0,0,0.05)' : 'none'};
`;

const CloseButton = styled.button`
  position: absolute;
  top: 20px;
  right: 20px;
  background: none;
  border: none;
  font-size: 20px;
  color: #888;
  cursor: pointer;
  padding: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
  
  &:hover {
    color: #333;
  }
`;

const ContentArea = styled.main<{ sidebarOpen: boolean }>`
  margin-left: ${props => props.sidebarOpen ? '320px' : '0'};
  flex: 1;
  padding: 20px;
  overflow-y: auto;
  background-color: white;
  transition: margin-left 0.3s ease;
  position: relative;
  
  &:before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    width: 20px;
    height: 100%;
    background-color: white;
    z-index: 1;
    display: ${props => props.sidebarOpen ? 'block' : 'none'};
  }
`;

// Додаємо тимчасові дані для фільтрів, поки не підключені до API
const dummyFilters = {
  brands: ['Nike', 'Adidas', 'Puma'],
  types: ['Взуття', 'Одяг', 'Аксесуари'],
  colors: ['Чорний', 'Білий', 'Червоний'],
  countries: ['Китай', 'В\'єтнам', 'Індонезія'],
  statuses: ['В наявності', 'Продано', 'Резерв'],
  conditions: ['Нове', 'Б/у', 'Реставроване'],
  price_range: {
    min: 0,
    max: 10000
  },
  size_range: {
    min: '36',
    max: '46'
  }
};

// Компонент для App з іконками теми
const AppContent: React.FC = () => {
  const [activeTab, setActiveTab] = useState('products');
  const { sidebarOpen, setSidebarOpen } = useFilter();
  const { searchTerm, setSearchTerm } = useSearch();
  const { darkTheme, toggleTheme } = useTheme();
  const queryClient = new QueryClient();
  const [tabPosition, setTabPosition] = useState(50);
  const [dragInfo, setDragInfo] = useState<{
    isDragging: boolean;
    startY: number;
    startPosition: number;
  }>({
    isDragging: false,
    startY: 0,
    startPosition: 0
  });
  
  // Додаємо посилання на DOM-елементи
  const sidebarRef = React.useRef<HTMLDivElement>(null);
  
  // Функція для обробки кліків за межами меню
  const handleOutsideClick = React.useCallback((e: MouseEvent) => {
    if (
      sidebarOpen && 
      sidebarRef.current && 
      !sidebarRef.current.contains(e.target as Node)
    ) {
      setSidebarOpen(false);
    }
  }, [sidebarOpen, setSidebarOpen]);
  
  // Додаємо обробник кліків при відкритому меню
  React.useEffect(() => {
    if (sidebarOpen) {
      document.addEventListener('mousedown', handleOutsideClick);
    } else {
      document.removeEventListener('mousedown', handleOutsideClick);
    }
    
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick);
    };
  }, [sidebarOpen, handleOutsideClick]);
  
  // Початок перетягування
  const handleDragStart = (e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragInfo({
      isDragging: true,
      startY: e.clientY,
      startPosition: tabPosition
    });
  };
  
  // Кінець перетягування
  const handleDragEnd = () => {
    setDragInfo(prev => ({ ...prev, isDragging: false }));
  };
  
  // Перетягування
  const handleDrag = (e: MouseEvent) => {
    if (dragInfo.isDragging) {
      const deltaY = e.clientY - dragInfo.startY;
      const newPosition = Math.max(0, Math.min(100, dragInfo.startPosition + deltaY));
      setTabPosition(newPosition);
    }
  };
  
  useEffect(() => {
    if (dragInfo.isDragging) {
      window.addEventListener('mousemove', handleDrag);
      window.addEventListener('mouseup', handleDragEnd);
    }

    return () => {
      window.removeEventListener('mousemove', handleDrag);
      window.removeEventListener('mouseup', handleDragEnd);
    };
  }, [dragInfo.isDragging]);
  
  const [selectedFilters, setSelectedFilters] = useState<ProductFiltersType>({});
  const [filterOptions, setFilterOptions] = useState<ProductFiltersOptions>({
    brands: [],
    types: [],
    colors: [],
    countries: [],
    price_range: { min: 0, max: 1000 },
    size_range: { min: '0', max: '100' }
  });
  
  return (
    <AppContainer>
      <Header>
        <Logo>
          <LogoImage src="/logo.png" alt="Logo" />
        </Logo>
        <SearchContainer>
          <SearchIcon>
            <FontAwesomeIcon icon={faSearch} />
          </SearchIcon>
          <SearchInput
            type="text"
            placeholder="Search..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </SearchContainer>
        <ThemeToggle onClick={toggleTheme}>
          <FontAwesomeIcon icon={darkTheme ? faSun : faMoon} />
        </ThemeToggle>
      </Header>

      <MainContainer>
        <FilterTab
          isOpen={sidebarOpen}
          onMouseDown={handleDragStart}
          onMouseUp={handleDragEnd}
          onMouseMove={(e: React.MouseEvent<HTMLDivElement>) => {
            if (dragInfo.isDragging) {
              const deltaY = e.clientY - dragInfo.startY;
              const newPosition = Math.max(0, Math.min(100, dragInfo.startPosition + deltaY));
              setTabPosition(newPosition);
            }
          }}
        >
          <FilterIcon>
            <FontAwesomeIcon icon={faFilter} />
          </FilterIcon>
        </FilterTab>

        <Sidebar isOpen={sidebarOpen}>
          <CloseButton onClick={() => setSidebarOpen(false)}>×</CloseButton>
          {activeTab === 'products' && (
            <ProductFilters
              filters={filterOptions}
              selectedFilters={selectedFilters}
              onFilterChange={setSelectedFilters}
            />
          )}
          {activeTab === 'orders' && <OrderFilters />}
        </Sidebar>

        <TabContainer>
          <Tab
            active={activeTab === 'products'}
            onClick={() => setActiveTab('products')}
          >
            <FontAwesomeIcon icon={faBox} style={{ marginRight: '8px' }} />
            Products
          </Tab>
          <Tab
            active={activeTab === 'orders'}
            onClick={() => setActiveTab('orders')}
          >
            Orders
          </Tab>
        </TabContainer>

        <ContentArea sidebarOpen={sidebarOpen}>
          {activeTab === 'products' && <ProductsPage />}
          {activeTab === 'orders' && <OrdersTab />}
        </ContentArea>
      </MainContainer>
    </AppContainer>
  );
};

const App: React.FC = () => {
  return (
    <Router>
      <MainLayout>
        <Content className="site-content">
          <Routes>
            <Route path="/" element={<ProductsPage />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="*" element={<div>404 Not Found</div>} />
          </Routes>
        </Content>
      </MainLayout>
    </Router>
  );
};

export default App; 