import React, { useState, useEffect } from 'react';
import { 
    Card, 
    Form, 
    Input, 
    Select, 
    Button, 
    Checkbox, 
    Slider, 
    Row, 
    Col, 
    Divider, 
    Typography 
} from 'antd';
import { SearchOutlined, FilterOutlined, ReloadOutlined } from '@ant-design/icons';
import { ProductFilters, ProductFiltersOptions, FilterOption } from '../../types/Product';
import styled from 'styled-components';

const { Title } = Typography;
const { Option } = Select;

const FiltersContainer = styled.div`
    margin-bottom: 20px;
`;

const FilterGroup = styled.div`
    margin-bottom: 16px;
`;

const FilterTitle = styled.div`
    font-weight: bold;
    margin-bottom: 8px;
`;

const FilterRow = styled(Row)`
    margin-bottom: 16px;
`;

const PriceRangeContainer = styled.div`
    padding: 0 10px;
`;

interface ProductFiltersProps {
    filters: ProductFilters;
    filterOptions: ProductFiltersOptions;
    onFilterChange: (filters: ProductFilters) => void;
    loading?: boolean;
}

const ProductFilters: React.FC<ProductFiltersProps> = ({ 
    filters, 
    filterOptions, 
    onFilterChange, 
    loading = false 
}) => {
    const [form] = Form.useForm();
    const [priceRange, setPriceRange] = useState<[number, number]>([0, 0]);
    
    // Ініціалізуємо фільтри при зміні опцій
    useEffect(() => {
        if (filterOptions) {
            setPriceRange([filterOptions.min_price, filterOptions.max_price]);
            form.setFieldsValue({
                ...filters,
                price_range: [
                    filters.min_price ?? filterOptions.min_price,
                    filters.max_price ?? filterOptions.max_price
                ]
            });
        }
    }, [filterOptions, filters, form]);
    
    // Обробник зміни ціни
    const handlePriceChange = (value: [number, number]) => {
        setPriceRange(value);
    };
    
    // Обробник застосування фільтрів
    const handleApplyFilters = (values: any) => {
        const { price_range, ...rest } = values;
        
        // Формуємо об'єкт фільтрів
        const newFilters: ProductFilters = {
            ...rest,
            min_price: price_range ? price_range[0] : undefined,
            max_price: price_range ? price_range[1] : undefined,
        };
        
        // Передаємо фільтри наверх
        onFilterChange(newFilters);
    };
    
    // Обробник скидання фільтрів
    const handleResetFilters = () => {
        form.resetFields();
        setPriceRange([filterOptions.min_price, filterOptions.max_price]);
        onFilterChange({});
    };
    
    return (
        <FiltersContainer>
            <Card 
                title={
                    <div>
                        <FilterOutlined style={{ marginRight: 8 }} />
                        <span>Фільтри товарів</span>
                    </div>
                }
                extra={
                    <Button 
                        icon={<ReloadOutlined />} 
                        onClick={handleResetFilters}
                        disabled={loading}
                    >
                        Скинути
                    </Button>
                }
            >
                <Form
                    form={form}
                    layout="vertical"
                    onFinish={handleApplyFilters}
                    initialValues={{
                        ...filters,
                        price_range: [
                            filters.min_price ?? filterOptions.min_price,
                            filters.max_price ?? filterOptions.max_price
                        ]
                    }}
                >
                    <FilterRow gutter={16}>
                        <Col xs={24} sm={12} md={8} lg={6}>
                            <Form.Item name="search">
                                <Input 
                                    placeholder="Пошук товарів"
                                    prefix={<SearchOutlined />}
                                    allowClear
                                />
                            </Form.Item>
                        </Col>
                        
                        <Col xs={24} sm={12} md={8} lg={6}>
                            <Form.Item name="typeid" label="Тип">
                                <Select 
                                    placeholder="Всі типи" 
                                    allowClear
                                    loading={loading}
                                >
                                    {filterOptions.types.map((type: FilterOption) => (
                                        <Option key={type.id} value={type.id}>
                                            {type.name}
                                        </Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                        
                        <Col xs={24} sm={12} md={8} lg={6}>
                            <Form.Item name="subtypeid" label="Підтип">
                                <Select 
                                    placeholder="Всі підтипи" 
                                    allowClear
                                    loading={loading}
                                >
                                    {filterOptions.subtypes
                                        .filter((subtype: FilterOption) => 
                                            !form.getFieldValue('typeid') || 
                                            subtype.typeid === form.getFieldValue('typeid')
                                        )
                                        .map((subtype: FilterOption) => (
                                            <Option key={subtype.id} value={subtype.id}>
                                                {subtype.name}
                                            </Option>
                                        ))
                                    }
                                </Select>
                            </Form.Item>
                        </Col>
                        
                        <Col xs={24} sm={12} md={8} lg={6}>
                            <Form.Item name="brandid" label="Бренд">
                                <Select 
                                    placeholder="Всі бренди" 
                                    allowClear
                                    loading={loading}
                                    showSearch
                                    optionFilterProp="children"
                                >
                                    {filterOptions.brands.map((brand: FilterOption) => (
                                        <Option key={brand.id} value={brand.id}>
                                            {brand.name}
                                        </Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                    </FilterRow>
                    
                    <FilterRow gutter={16}>
                        <Col xs={24} sm={12} md={8} lg={6}>
                            <Form.Item name="genderid" label="Стать">
                                <Select 
                                    placeholder="Всі" 
                                    allowClear
                                    loading={loading}
                                >
                                    {filterOptions.genders.map((gender: FilterOption) => (
                                        <Option key={gender.id} value={gender.id}>
                                            {gender.name}
                                        </Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                        
                        <Col xs={24} sm={12} md={8} lg={6}>
                            <Form.Item name="colorid" label="Колір">
                                <Select 
                                    placeholder="Всі кольори" 
                                    allowClear
                                    loading={loading}
                                >
                                    {filterOptions.colors.map((color: FilterOption) => (
                                        <Option key={color.id} value={color.id}>
                                            {color.name}
                                        </Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                        
                        <Col xs={24} sm={12} md={8} lg={6}>
                            <Form.Item name="statusid" label="Статус">
                                <Select 
                                    placeholder="Всі статуси" 
                                    allowClear
                                    loading={loading}
                                >
                                    {filterOptions.statuses.map((status: FilterOption) => (
                                        <Option key={status.id} value={status.id}>
                                            {status.name}
                                        </Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                        
                        <Col xs={24} sm={12} md={8} lg={6}>
                            <Form.Item name="conditionid" label="Стан">
                                <Select 
                                    placeholder="Всі стани" 
                                    allowClear
                                    loading={loading}
                                >
                                    {filterOptions.conditions.map((condition: FilterOption) => (
                                        <Option key={condition.id} value={condition.id}>
                                            {condition.name}
                                        </Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                    </FilterRow>
                    
                    <FilterGroup>
                        <FilterTitle>Діапазон цін</FilterTitle>
                        <PriceRangeContainer>
                            <Form.Item name="price_range">
                                <Slider 
                                    range 
                                    min={filterOptions.min_price} 
                                    max={filterOptions.max_price}
                                    onChange={handlePriceChange}
                                    value={priceRange}
                                    disabled={loading}
                                    tipFormatter={(value) => `${value} ₴`}
                                />
                            </Form.Item>
                            <Row justify="space-between">
                                <Col>від {priceRange[0]} ₴</Col>
                                <Col>до {priceRange[1]} ₴</Col>
                            </Row>
                        </PriceRangeContainer>
                    </FilterGroup>
                    
                    <FilterRow>
                        <Col span={12}>
                            <Form.Item name="with_stock_only" valuePropName="checked">
                                <Checkbox disabled={loading}>
                                    Тільки в наявності
                                </Checkbox>
                            </Form.Item>
                        </Col>
                        <Col span={12}>
                            <Form.Item name="is_visible" valuePropName="checked">
                                <Checkbox disabled={loading}>
                                    Тільки видимі
                                </Checkbox>
                            </Form.Item>
                        </Col>
                    </FilterRow>
                    
                    <Form.Item>
                        <Button 
                            type="primary" 
                            htmlType="submit" 
                            icon={<FilterOutlined />}
                            loading={loading}
                        >
                            Застосувати фільтри
                        </Button>
                    </Form.Item>
                </Form>
            </Card>
        </FiltersContainer>
    );
};

export default ProductFilters; 