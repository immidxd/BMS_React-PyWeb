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
    Col,
    Pagination,
    Spin
} from 'antd';
import { 
    EditOutlined, 
    DeleteOutlined, 
    PlusOutlined, 
    EyeOutlined, 
    EyeInvisibleOutlined 
} from '@ant-design/icons';
import { Product, ProductListResponse } from '../../types/Product';
import { useHistory } from 'react-router-dom';
import { productService } from '../../services/productService';

// Типи даних 
interface Product {
  id: number;
  productnumber: string;
  clones?: string | null;
  typename: string | null;
  subtypename: string | null;
  brandname: string | null;
  model: string | null;
  marking?: string | null;
  gender?: string | null;
  color: string | null;
  description: string | null;
  country: string | null;
  manufacturer?: string | null;
  size: string | null;
  dimensions: string | null;
  price: number;
  oldprice: number | null;
  statusname: string | null;
  conditionname: string | null;
  additional_note?: string | null;
  import_status: string | null;
  quantity: number;
  image_url: string | null;
  is_visible: boolean;
  created_at: string;
  updated_at: string;
}

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
  products: ProductListResponse;
  loading: boolean;
  onDelete: (id: number) => Promise<void>;
  onPageChange: (page: number, pageSize?: number) => void;
  onVisibilityChange: (id: number, isVisible: boolean) => Promise<void>;
}

const ProductsTable: React.FC<ProductsTableProps> = ({ 
    products, 
    loading, 
    onDelete, 
    onPageChange, 
    onVisibilityChange 
}) => {
    const history = useHistory();
    const [visibilityLoading, setVisibilityLoading] = useState<Record<number, boolean>>({});
    
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
                <a onClick={() => history.push(`/products/${record.id}`)}>{text}</a>
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
                            onClick={() => history.push(`/products/${record.id}/edit`)}
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
    
    return (
        <TableContainer>
            <TableActions>
                <Col>
                    <Button 
                        type="primary" 
                        icon={<PlusOutlined />}
                        onClick={() => history.push('/products/create')}
                    >
                        Додати товар
                    </Button>
                </Col>
                <Col>
                    <Pagination
                        current={products.page}
                        total={products.total}
                        pageSize={products.size}
                        onChange={onPageChange}
                        showSizeChanger
                        showTotal={(total, range) => `${range[0]}-${range[1]} з ${total} товарів`}
                    />
                </Col>
            </TableActions>
            
            <Table
                columns={columns}
                dataSource={products.items}
                rowKey="id"
                pagination={false}
                loading={loading}
                scroll={{ x: 1300, y: 'calc(100vh - 300px)' }}
                size="middle"
                bordered
            />
        </TableContainer>
    );
};

export default ProductsTable; 