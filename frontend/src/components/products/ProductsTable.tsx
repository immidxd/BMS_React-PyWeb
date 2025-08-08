import React, { useState, useRef, useEffect } from 'react';
import styled from 'styled-components';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faEdit, faTrash, faEye, faEyeSlash, faCheck } from '@fortawesome/free-solid-svg-icons';
import { useTheme } from '../../contexts/ThemeContext';
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
    
    // Налаштування колонок для таблиці
    const columns = [
        {
            title: 'ID',
            dataIndex: 'id',
            key: 'id',
            width: 70,
            sorter: true,
        },
        {
            title: 'Номер',
            dataIndex: 'productnumber',
            key: 'productnumber',
            width: 120,
            render: (text: string, record: Product) => (
                <a onClick={() => { setDetailsId(record.id); setDetailsOpen(true); }}>{text}</a>
            ),
        },
        {
            title: 'Модель',
            dataIndex: 'model',
            key: 'model',
            width: 150,
        },
        {
            title: 'Бренд',
            dataIndex: 'brand_name',
            key: 'brand_name',
            width: 120,
        },
        {
            title: 'Тип',
            dataIndex: 'type_name',
            key: 'type_name',
            width: 120,
        },
        {
            title: 'Ціна',
            dataIndex: 'price',
            key: 'price',
            width: 150,
            render: (price: number, record: Product) => (
                <>
                    {price && <PriceText>{price.toFixed(2)} ₴</PriceText>}
                    {record.oldprice && <OldPriceText>{record.oldprice.toFixed(2)} ₴</OldPriceText>}
                </>
            ),
            sorter: true,
        },
        {
            title: 'К-сть',
            dataIndex: 'quantity',
            key: 'quantity',
            width: 80,
            align: 'center' as 'center',
            render: (quantity: number) => (
                <Tag color={quantity > 0 ? 'green' : 'red'}>
                    {quantity}
                </Tag>
            ),
            sorter: true,
        },
        {
            title: 'Статус',
            dataIndex: 'status_name',
            key: 'status_name',
            width: 120,
            render: (status: string) => (
                <Tag color={status === 'Продано' ? 'red' : status === 'В наявності' ? 'green' : 'blue'}>
                    {status || 'Не вказано'}
                </Tag>
            ),
        },
        {
            title: 'Видимість',
            dataIndex: 'is_visible',
            key: 'is_visible',
            width: 100,
            align: 'center' as 'center',
            render: (isVisible: boolean, record: Product) => (
                <Switch
                    checked={isVisible}
                    onChange={(checked) => handleVisibilityChange(record.id, checked)}
                    loading={visibilityLoading[record.id]}
                    checkedChildren={<EyeOutlined />}
                    unCheckedChildren={<EyeInvisibleOutlined />}
                />
            ),
        },
        {
            title: 'Дії',
            key: 'actions',
            width: 120,
            fixed: 'right' as 'right',
            render: (_: any, record: Product) => (
                <Space>
                    <Tooltip title="Редагувати">
                        <Button 
                            type="primary" 
                            icon={<EditOutlined />} 
                            size="small"
                            onClick={() => navigate(`/products/${record.id}/edit`)}
                        />
                    </Tooltip>
                    <Tooltip title="Видалити">
                        <Popconfirm
                            title="Ви впевнені, що хочете видалити цей товар?"
                            onConfirm={() => handleDelete(record.id)}
                            okText="Так"
                            cancelText="Ні"
                        >
                            <Button 
                                danger 
                                icon={<DeleteOutlined />}
                                size="small"
                            />
                        </Popconfirm>
                    </Tooltip>
                </Space>
            ),
        },
    ];
    
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
        <TableContainer className="max-h-[calc(100vh-220px)]">
            <ProductDetailsModal productId={detailsId} open={detailsOpen} onClose={() => setDetailsOpen(false)} onSaved={() => setDetailsOpen(false)} />
            
            <Table
                columns={columns}
                dataSource={products.items}
                rowKey="id"
                rowSelection={rowSelection}
                pagination={false}
                loading={loading}
                // y: заповнюємо висоту вікна мінус шапка сторінки
                scroll={{ y: 'calc(100vh - 260px)' }}
                tableLayout="auto"
                className="min-w-[1600px] xl:min-w-[1800px] 2xl:min-w-[2000px]"
                size="middle"
                bordered
                onChange={handleTableChange}
            />
        </TableContainer>
    );
};

export default ProductsTable; 