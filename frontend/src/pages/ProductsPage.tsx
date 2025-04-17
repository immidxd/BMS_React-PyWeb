import React, { useState, useEffect } from 'react';
import { message, Breadcrumb, Spin, Alert } from 'antd';
import { HomeOutlined, ShoppingOutlined } from '@ant-design/icons';
import { 
    Product, 
    ProductListResponse, 
    ProductFilters, 
    ProductFiltersOptions,
    GetProductsParams 
} from '../types/Product';
import { productService } from '../services/productService';
import { API_ERROR_EVENT } from '../services/axiosConfig';
import ProductsTable from '../components/products/ProductsTable';
import ProductFilters from '../components/products/ProductFilters';
import styled from 'styled-components';

// Стилізовані компоненти
const PageContainer = styled.div`
    padding: 16px;
`;

const BreadcrumbContainer = styled.div`
    margin-bottom: 16px;
`;

const ErrorAlert = styled(Alert)`
    margin-bottom: 16px;
`;

// Мокові дані для випадку відсутності з'єднання з сервером
const mockProducts: Product[] = [
    {
        id: 1,
        productnumber: 'P001',
        model: 'Nike Футболка',
        price: 1200,
        oldprice: null,
        quantity: 10,
        description: 'Базова футболка Nike чорного кольору',
        typeid: 1,
        subtypeid: 3,
        brandid: 1,
        statusid: 1,
        is_visible: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString()
    },
    {
        id: 2,
        productnumber: 'P002',
        model: "Levi's Джинси",
        price: 2500,
        oldprice: null,
        quantity: 5,
        description: 'Класичні джинси Levi\'s синього кольору',
        typeid: 2,
        subtypeid: 5,
        brandid: 6,
        statusid: 1,
        is_visible: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString()
    },
    {
        id: 3,
        productnumber: 'P003',
        model: 'Adidas Куртка',
        price: 3500,
        oldprice: null,
        quantity: 3,
        description: 'Зимова куртка Adidas зеленого кольору',
        typeid: 3,
        subtypeid: 2,
        brandid: 2,
        statusid: 1,
        is_visible: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString()
    }
];

const initialProductsState: ProductListResponse = {
    items: [],
    total: 0,
    page: 1,
    size: 10,
    pages: 0
};

const initialFiltersOptions: ProductFiltersOptions = {
    types: [],
    subtypes: [],
    brands: [],
    genders: [],
    colors: [],
    statuses: [],
    conditions: [],
    min_price: 0,
    max_price: 0
};

const ProductsPage: React.FC = () => {
    // Стан для списку товарів
    const [products, setProducts] = useState<ProductListResponse>(initialProductsState);
    
    // Стан для фільтрів
    const [filters, setFilters] = useState<ProductFilters>({});
    
    // Стан для опцій фільтрів
    const [filterOptions, setFilterOptions] = useState<ProductFiltersOptions>(initialFiltersOptions);
    
    // Стан для поточної сторінки та розміру сторінки
    const [currentPage, setCurrentPage] = useState<number>(1);
    const [pageSize, setPageSize] = useState<number>(10);
    
    // Стан для індикатора завантаження
    const [loading, setLoading] = useState<boolean>(true);
    
    // Стан для тимчасової взаємодії
    const [visibilityLoading, setVisibilityLoading] = useState<Record<number, boolean>>({});
    
    // Стан для відображення помилки з'єднання
    const [connectionError, setConnectionError] = useState<string | null>(null);
    
    // Обробник помилки з'єднання
    useEffect(() => {
        const handleConnectionError = (event: CustomEvent) => {
            setConnectionError(event.detail.message);
            
            // Використовуємо мокові дані у випадку помилки
            setProducts({
                items: mockProducts,
                total: mockProducts.length,
                page: 1,
                size: mockProducts.length,
                pages: 1
            });
            setLoading(false);
        };
        
        window.addEventListener(API_ERROR_EVENT, handleConnectionError as EventListener);
        
        return () => {
            window.removeEventListener(API_ERROR_EVENT, handleConnectionError as EventListener);
        };
    }, []);
    
    // Завантажуємо товари при монтуванні компонента та зміні параметрів
    useEffect(() => {
        loadProducts();
    }, [currentPage, pageSize, filters]);
    
    // Завантажуємо опції фільтрів при монтуванні компонента
    useEffect(() => {
        loadFilterOptions();
    }, []);
    
    // Функція для завантаження товарів
    const loadProducts = async () => {
        setLoading(true);
        try {
            const params: GetProductsParams = {
                ...filters,
                skip: (currentPage - 1) * pageSize,
                limit: pageSize
            };
            
            const response = await productService.getProducts(params);
            setProducts(response);
            // Якщо успішно завантажили дані, очищаємо помилку
            setConnectionError(null);
        } catch (error) {
            console.error('Error loading products:', error);
            if (!connectionError) {
                message.error('Помилка при завантаженні товарів');
            }
        } finally {
            setLoading(false);
        }
    };
    
    // Функція для завантаження опцій фільтрів
    const loadFilterOptions = async () => {
        try {
            const options = await productService.getFilters();
            setFilterOptions(options);
        } catch (error) {
            console.error('Error loading filter options:', error);
            message.error('Помилка при завантаженні опцій фільтрів');
        }
    };
    
    // Обробник зміни фільтрів
    const handleFilterChange = (newFilters: ProductFilters) => {
        setFilters(newFilters);
        setCurrentPage(1); // Скидаємо на першу сторінку при зміні фільтрів
    };
    
    // Обробник зміни сторінки та розміру сторінки
    const handlePageChange = (page: number, size?: number) => {
        setCurrentPage(page);
        if (size) {
            setPageSize(size);
        }
    };
    
    // Обробник видалення товару
    const handleDeleteProduct = async (id: number) => {
        try {
            await productService.deleteProduct(id);
            message.success('Товар успішно видалено');
            loadProducts(); // Перезавантажуємо список товарів
        } catch (error) {
            console.error('Error deleting product:', error);
            message.error('Помилка при видаленні товару');
        }
    };
    
    // Обробник зміни видимості товару
    const handleVisibilityChange = async (id: number, isVisible: boolean) => {
        setVisibilityLoading(prev => ({ ...prev, [id]: true }));
        try {
            await productService.updateProductVisibility(id, isVisible);
            // Оновлюємо товар у списку без перезавантаження всього списку
            setProducts(prev => ({
                ...prev,
                items: prev.items.map(item => 
                    item.id === id ? { ...item, is_visible: isVisible } : item
                )
            }));
        } catch (error) {
            console.error('Error updating product visibility:', error);
            message.error('Помилка при зміні видимості товару');
        } finally {
            setVisibilityLoading(prev => ({ ...prev, [id]: false }));
        }
    };
    
    return (
        <PageContainer>
            <BreadcrumbContainer>
                <Breadcrumb items={[
                    { title: <HomeOutlined />, href: '/' },
                    { title: 'Товари', icon: <ShoppingOutlined /> }
                ]} />
            </BreadcrumbContainer>
            
            {connectionError && (
                <ErrorAlert
                    message={connectionError}
                    type="warning"
                    showIcon
                    closable
                    onClose={() => setConnectionError(null)}
                />
            )}
            
            <ProductFilters 
                filters={filters}
                filterOptions={filterOptions}
                onFilterChange={handleFilterChange}
                loading={loading}
            />
            
            <ProductsTable 
                products={products}
                loading={loading}
                onDelete={handleDeleteProduct}
                onPageChange={handlePageChange}
                onVisibilityChange={handleVisibilityChange}
            />
        </PageContainer>
    );
};

export default ProductsPage; 