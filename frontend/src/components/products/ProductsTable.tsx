import React, { useState, useRef, useEffect } from 'react';
import styled from 'styled-components';
import { 
    Table, 
    Button, 
    Space, 
    Tag, 
    Popconfirm, 
    message, 
    Tooltip,
    Switch,
    Row,
    Col
} from 'antd';
import { 
    EditOutlined, 
    DeleteOutlined, 
    PlusOutlined, 
    EyeOutlined, 
    EyeInvisibleOutlined 
} from '@ant-design/icons';
import { Product } from '../../types/product';
import type { TableProps } from 'antd';
import ProductDetailsModal from './ProductDetailsModal';
import { useNavigate } from 'react-router-dom';
import { productService } from '../../services/productService';
// Pagination is rendered at page level

// Column configuration type
interface ColumnConfig {
  id: string;
  title: string;
  visible: boolean;
  optional: boolean;
  width?: string;
}

const TableContainer = styled.div`
    margin-top: 16px;
    position: relative;
`;

const TableActions = styled(Row)`
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
`;

const PriceText = styled.span`
    font-weight: bold;
`;

const OldPriceText = styled.span`
    text-decoration: line-through;
    color: #999;
    margin-left: 8px;
    font-size: 0.85em;
`;

interface ProductsTableProps {
  products: { items: Product[], total: number, page: number, per_page: number };
  loading: boolean;
  onDelete: (id: number) => Promise<void>;
  onPageChange: (page: number, pageSize?: number) => void;
  onVisibilityChange: (id: number, isVisible: boolean) => Promise<void>;
  onSortChange?: (sortBy: string, sortDir: 'asc' | 'desc') => void;
  selectedRowKeys: React.Key[];
  onSelectedRowKeysChange: (keys: React.Key[]) => void;
}

const ProductsTable: React.FC<ProductsTableProps> = ({ 
    products, 
    loading, 
    onDelete, 
    onPageChange, 
    onVisibilityChange,
    onSortChange,
    selectedRowKeys,
    onSelectedRowKeysChange,
}) => {
    const navigate = useNavigate();
    const [visibilityLoading, setVisibilityLoading] = useState<Record<number, boolean>>({});
    const [detailsId, setDetailsId] = useState<number | null>(null);
    const [detailsOpen, setDetailsOpen] = useState<boolean>(false);
    // Контекстне меню керування колонками
    const storageKey = 'products_table_columns_v2';
    const menuRef = useRef<HTMLDivElement | null>(null);
    const [menuOpen, setMenuOpen] = useState(false);
    const [menuPos, setMenuPos] = useState<{x:number;y:number}>({x:0,y:0});
    const columnOrder: { id: string; title: string; optional: boolean }[] = [
        // 0.1 (опціонально перед №1)
        { id: 'id', title: 'ID', optional: true },
        // 1
        { id: 'productnumber', title: 'Номер', optional: false },
        // 1.1
        { id: 'clonednumbers', title: 'Номера-клони', optional: true },
        // 2
        { id: 'type_name', title: 'Тип', optional: false },
        // 2.1
        { id: 'subtype_name', title: 'Підтип', optional: true },
        // 3
        { id: 'brand_name', title: 'Бренд', optional: false },
        // 3.1, 3.2, 3.3
        { id: 'model', title: 'Модель', optional: true },
        { id: 'marking', title: 'Маркування', optional: true },
        { id: 'year', title: 'Рік', optional: true },
        // 4
        { id: 'gender_name', title: 'Стать', optional: false },
        // 5
        { id: 'color_name', title: 'Колір', optional: false },
        // 5.1
        { id: 'description', title: 'Опис', optional: true },
        // 6
        { id: 'sizeeu', title: 'Розмір', optional: false },
        // 7
        { id: 'measurementscm', title: 'СМ', optional: false },
        // 8
        { id: 'price', title: 'Ціна', optional: false },
        // 8.1, 8.2
        { id: 'oldprice', title: 'Стара ціна', optional: true },
        { id: 'quantity', title: 'К-сть (заг.)', optional: true },
        { id: 'available_qty', title: 'В наявності', optional: false },
        // 9
        { id: 'status_name', title: 'Статус', optional: false },
        // 10
        { id: 'condition_name', title: 'Стан', optional: false },
        // 11, 12, 13
        { id: 'supplier_name', title: 'Постачальник', optional: true },
        { id: 'is_visible', title: 'Видимість', optional: true },
        { id: 'actions', title: 'Дії', optional: true },
    ];
    const defaultVisibility: Record<string, boolean> = columnOrder.reduce((acc, c) => {
        acc[c.id] = !c.optional; return acc;
    }, {} as Record<string, boolean>);
    const [visibleMap, setVisibleMap] = useState<Record<string, boolean>>(() => {
        try {
            const raw = localStorage.getItem(storageKey);
            if (!raw) return defaultVisibility;
            const parsed = JSON.parse(raw);
            return { ...defaultVisibility, ...parsed };
        } catch {
            return defaultVisibility;
        }
    });
    useEffect(() => { localStorage.setItem(storageKey, JSON.stringify(visibleMap)); }, [visibleMap]);
    useEffect(() => {
        const onDocClick = (e: MouseEvent) => {
            if (!menuRef.current) return setMenuOpen(false);
            if (!menuRef.current.contains(e.target as Node)) setMenuOpen(false);
        };
        document.addEventListener('mousedown', onDocClick);
        return () => document.removeEventListener('mousedown', onDocClick);
    }, []);
    const handleContextMenu: React.MouseEventHandler<HTMLDivElement> = (e) => {
        e.preventDefault();
        setMenuPos({ x: e.clientX, y: e.clientY });
        setMenuOpen(true);
    };
    
    // Обробник зміни видимості товару
    const handleVisibilityChange = async (id: number, isVisible: boolean) => {
        setVisibilityLoading(prev => ({ ...prev, [id]: true }));
        try {
            await onVisibilityChange(id, isVisible);
            message.success(`Видимість товару ${isVisible ? 'включена' : 'виключена'}`);
        } catch (error) {
            message.error('Помилка при зміні видимості товару');
        } finally {
            setVisibilityLoading(prev => ({ ...prev, [id]: false }));
        }
    };
    
    // Обробник видалення товару
    const handleDelete = async (id: number) => {
        try {
            await onDelete(id);
            message.success('Товар успішно видалено');
        } catch (error) {
            message.error('Помилка при видаленні товару');
        }
    };
    
    // Опис усіх можливих колонок уніфіковано, з рендерами
    const allColumns: Record<string, any> = {
        id: {
            title: 'ID', dataIndex: 'id', key: 'id', width: 45, sorter: true,
        },
        productnumber: {
            title: 'Номер', dataIndex: 'productnumber', key: 'productnumber', width: 80,
            render: (text: string, record: Product) => (
                <div className="flex flex-col gap-0.5">
                    <span className="text-xs text-left font-medium text-gray-800 dark:text-gray-200">{text}</span>
                    {record.is_rostovka && (
                        <Tooltip title={`Ростовка — набір розмірів (${record.quantity} од.)`}>
                            <span className="inline-flex items-center gap-0.5 px-1 py-0 rounded text-[9px] font-semibold bg-purple-100 text-purple-700 border border-purple-200 w-fit cursor-help">
                                ▤ Рост.
                            </span>
                        </Tooltip>
                    )}
                </div>
            ),
        },
        model: { title: 'Модель', dataIndex: 'model', key: 'model', width: 140 },
        brand_name: { title: 'Бренд', dataIndex: 'brand_name', key: 'brand_name', width: 110 },
        type_name: { title: 'Тип', dataIndex: 'type_name', key: 'type_name', width: 110 },
        subtype_name: { title: 'Підтип', dataIndex: 'subtype_name', key: 'subtype_name', width: 120 },
        gender_name: { title: 'Стать', dataIndex: 'gender_name', key: 'gender_name', width: 75 },
        color_name: { title: 'Колір', dataIndex: 'color_name', key: 'color_name', width: 65 },
        sizeeu: { title: 'Розмір (EU)', dataIndex: 'sizeeu', key: 'sizeeu', width: 70,
            render: (text: string, record: Product) => {
                const isRost = record.is_rostovka;
                if (!text) return <span className="text-gray-300 text-xs">—</span>;
                return (
                    <span className={`text-xs ${isRost ? 'text-purple-700 font-medium' : ''}`}>
                        {text}
                        {isRost && record.quantity > 1 && (
                            <span className="text-purple-400 ml-0.5">×{record.quantity}</span>
                        )}
                    </span>
                );
            }},
        measurementscm: { title: 'СМ', dataIndex: 'measurementscm', key: 'measurementscm', width: 60,
            render: (text: string) => <span className="text-xs">{text}</span> },
        price: { title: 'Ціна', dataIndex: 'price', key: 'price', width: 50, sorter: true,
            render: (price: number, record: Product) => (
                <span className="text-xs">
                    {price !== undefined && price !== null && <PriceText>{Number(price).toFixed(0)}₴</PriceText>}
                </span>
            )},
        oldprice: { title: 'Стара ціна', dataIndex: 'oldprice', key: 'oldprice', width: 90,
            render: (value: number) => value ? <OldPriceText>{Number(value).toFixed(0)} ₴</OldPriceText> : null },
        quantity: { title: 'К-сть (заг.)', dataIndex: 'quantity', key: 'quantity', width: 65, align: 'center' as const, sorter: true,
            render: (q: number) => <Tag color={q > 0 ? 'blue' : 'red'}>{q}</Tag> },
        available_qty: { title: 'В наявності', key: 'available_qty', width: 80, align: 'center' as const,
            render: (_: any, record: Product) => {
                const total = record.quantity ?? 0;
                const avail = record.available_qty ?? total;
                const sold  = record.sold_count  ?? 0;
                if (total === 0) return <Tag color="red">0</Tag>;
                if (sold === 0)  return <Tag color="green">{total}</Tag>;
                if (avail <= 0) return <Tag color="red">0 / {total}</Tag>;
                return (
                    <Tooltip title={`Продано: ${sold} / ${total}`}>
                        <Tag color="orange">{avail} / {total}</Tag>
                    </Tooltip>
                );
            } },
        status_name: { title: 'Статус', key: 'status_name', width: 110,
            render: (_: any, record: Product) => {
                const sold = record.sold_count ?? 0;
                const qty = record.quantity ?? 0;
                const staticStatus = record.status_name || '';
                let displayStatus = staticStatus;
                if (staticStatus === 'Подаровано') {
                    displayStatus = 'Подаровано';
                } else if (sold > 0 && sold >= qty && qty > 0) {
                    displayStatus = 'Продано';
                } else if (sold > 0 && sold < qty) {
                    displayStatus = `Продано ${sold}/${qty}`;
                }
                const colorMap: Record<string, string> = {
                    'Непродано':  'green',
                    'Продано':    'red',
                    'Подаровано': 'purple',
                };
                const color = displayStatus.startsWith('Продано') ? 'red' : (colorMap[displayStatus] || (displayStatus ? 'geekblue' : 'default'));
                return (
                    <Tag
                        color={color}
                        style={{ display: 'block', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', padding: '0 6px', fontSize: 12 }}
                    >
                        {displayStatus || 'Не вказано'}
                    </Tag>
                );
            } },
        condition_name: { title: 'Стан', dataIndex: 'condition_name', key: 'condition_name', width: 75 },
        clonednumbers: { title: 'Номера-клони', dataIndex: 'clonednumbers', key: 'clonednumbers', width: 160 },
        marking: { title: 'Маркування', dataIndex: 'marking', key: 'marking', width: 140 },
        year: { title: 'Рік', dataIndex: 'year', key: 'year', width: 50 },
        description: { title: 'Опис', dataIndex: 'description', key: 'description', width: 200, ellipsis: true },
        supplier_name: { title: 'Постачальник', dataIndex: 'supplier_name', key: 'supplier_name', width: 160 },
        is_visible: { title: 'Видимість', dataIndex: 'is_visible', key: 'is_visible', width: 85, align: 'center' as const,
            render: (isVisible: boolean, record: Product) => (
                <Switch
                    checked={isVisible}
                    onChange={(checked) => handleVisibilityChange(record.id, checked)}
                    loading={visibilityLoading[record.id]}
                    checkedChildren={<EyeOutlined />}
                    unCheckedChildren={<EyeInvisibleOutlined />}
                />
            ) },
        actions: { title: 'Дії', key: 'actions', width: 90, fixed: 'right' as const,
            render: (_: any, record: Product) => (
                <Space>
                    <Tooltip title="Редагувати">
                        <Button type="primary" icon={<EditOutlined />} size="small" onClick={() => navigate(`/products/${record.id}/edit`)} />
                    </Tooltip>
                    <Tooltip title="Видалити">
                        <Popconfirm title="Ви впевнені, що хочете видалити цей товар?" onConfirm={() => handleDelete(record.id)} okText="Так" cancelText="Ні">
                            <Button danger icon={<DeleteOutlined />} size="small" />
                        </Popconfirm>
                    </Tooltip>
                </Space>
            ) },
    };
    const columns = columnOrder
        .filter(c => visibleMap[c.id])
        .map(c => allColumns[c.id])
        .filter(Boolean);
    
    const handleTableChange: TableProps<Product>['onChange'] = (pagination, filters, sorter) => {
        // AntD can return sorter as object or array; handle both
        const s: any = Array.isArray(sorter) ? sorter[0] : sorter;
        if (s && s.field && s.order && onSortChange) {
            const sortBy = String(s.field);
            const sortDir = s.order === 'ascend' ? 'asc' : 'desc';
            onSortChange(sortBy, sortDir);
        }
    };

    const rowSelection = {
        selectedRowKeys,
        onChange: (keys: React.Key[]) => onSelectedRowKeysChange(keys),
    };

    return (
        <TableContainer className="max-h-[calc(100vh-220px)]" onContextMenu={handleContextMenu}>
            <ProductDetailsModal productId={detailsId} open={detailsOpen} onClose={() => setDetailsOpen(false)} />
            
            <div className="overflow-x-auto rounded-lg shadow-md border border-gray-200 bg-white dark:bg-gray-800 dark:border-gray-700">
                <Table
                    columns={columns}
                    dataSource={products.items}
                    rowKey="id"
                    rowSelection={rowSelection}
                    pagination={false}
                    loading={loading}
                    onRow={(record: Product) => {
                        const issues: string[] = [];
                        if (!record.productnumber || record.productnumber === '???' || record.productnumber.startsWith('__tmp_rename_') || record.productnumber.startsWith('???_')) issues.push('Товар не має номера');
                        if (!record.type_name) issues.push('Не вказано тип');
                        if (!record.price) issues.push('Ціна = 0 або не вказана');
                        if (!record.supplier_name) issues.push('Не вказано постачальника');
                        const sold = record.sold_count ?? 0;
                        const qty = record.quantity ?? 0;
                        if (sold > qty) issues.push(`Перепродано: ${sold} продано з ${qty} наявних`);
                        if ((record.pnum_dup_brands ?? 0) > 1) issues.push('Номер товару дублюється (різні бренди)');
                        const hasIssue = issues.length > 0;
                        const conflictTitle = issues.join(' • ');
                        return {
                            title: hasIssue ? `⚠ ${conflictTitle}` : undefined,
                            className: `cursor-pointer ${hasIssue
                                ? 'bg-orange-50 dark:bg-orange-900/20 border-l-4 border-orange-400 hover:bg-orange-100 dark:hover:bg-orange-900/40'
                                : 'hover:bg-gray-50 dark:hover:bg-gray-700/50'}`,
                            onDoubleClick: () => { setDetailsId(record.id); setDetailsOpen(true); },
                        };
                    }}
                    // y: заповнюємо висоту вікна мінус шапка сторінки
                    scroll={{ x: 'max-content', y: 'calc(100vh - 260px)' }}
                    tableLayout="fixed"
                    className="min-w-[1200px] xl:min-w-[1400px] 2xl:min-w-[1600px]"
                    size="small"
                    bordered={false}
                    onChange={handleTableChange}
                />
            </div>

            {/* Контекстне меню керування колонками */}
            {menuOpen && (
                <div
                  ref={menuRef}
                  style={{ top: menuPos.y, left: menuPos.x }}
                  className="fixed z-[10000] w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg p-2"
                >
                  <div className="px-2 py-1 text-xs text-gray-500">Видимість колонок</div>
                  <div className="max-h-80 overflow-auto pr-1">
                    {columnOrder.map(c => (
                      <label key={c.id} className="flex items-center justify-between px-2 py-1 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 rounded">
                        <span>{c.title}</span>
                        <input
                          type="checkbox"
                          checked={!!visibleMap[c.id]}
                          onChange={(e) => setVisibleMap(v => ({ ...v, [c.id]: e.target.checked }))}
                        />
                      </label>
                    ))}
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2">
                    <button className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-100" onClick={() => setVisibleMap(() => columnOrder.reduce((a, c) => (a[c.id] = true, a), {} as Record<string, boolean>))}>Всі</button>
                    <button className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-100" onClick={() => setVisibleMap(() => columnOrder.reduce((a, c) => (a[c.id] = false, a), {} as Record<string, boolean>))}>Приховати</button>
                    <button className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-100" onClick={() => setVisibleMap(defaultVisibility)}>За умовч.</button>
                  </div>
                </div>
            )}
        </TableContainer>
    );
};

export default ProductsTable; 