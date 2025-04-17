import React, { useState, useEffect } from 'react';
import { 
    Form, 
    Input, 
    Button, 
    Select, 
    InputNumber, 
    Checkbox, 
    Row, 
    Col, 
    Divider, 
    Card, 
    message, 
    Spin, 
    Space 
} from 'antd';
import { SaveOutlined, CloseOutlined, ArrowLeftOutlined } from '@ant-design/icons';
import { 
    Product, 
    ProductCreate, 
    ProductUpdate, 
    ProductFiltersOptions 
} from '../../types/Product';
import { useHistory, useParams } from 'react-router-dom';
import { productService } from '../../services/productService';
import styled from 'styled-components';

const { TextArea } = Input;
const { Option } = Select;

// Стилізовані компоненти
const FormContainer = styled.div`
    padding: 16px;
`;

const SubmitButtons = styled.div`
    margin-top: 24px;
    display: flex;
    justify-content: flex-end;
`;

interface ProductFormProps {
    mode: 'create' | 'edit';
    product?: Product;
    filterOptions: ProductFiltersOptions;
    onSubmit: (data: ProductCreate | ProductUpdate) => Promise<void>;
    loading: boolean;
}

interface RouteParams {
    id?: string;
}

const ProductForm: React.FC<ProductFormProps> = ({ 
    mode, 
    product, 
    filterOptions, 
    onSubmit, 
    loading 
}) => {
    const [form] = Form.useForm();
    const history = useHistory();
    const { id } = useParams<RouteParams>();
    const [submitting, setSubmitting] = useState(false);
    const [selectedTypeId, setSelectedTypeId] = useState<number | undefined>(product?.typeid);
    
    // Оновлюємо форму при отриманні даних товару
    useEffect(() => {
        if (product) {
            form.setFieldsValue(product);
            setSelectedTypeId(product.typeid);
        }
    }, [product, form]);
    
    // Обробник подання форми
    const handleSubmit = async (values: any) => {
        setSubmitting(true);
        try {
            await onSubmit(values);
            message.success(`Товар успішно ${mode === 'create' ? 'створено' : 'оновлено'}`);
            history.push('/products');
        } catch (error) {
            console.error('Error submitting form:', error);
            message.error(`Помилка при ${mode === 'create' ? 'створенні' : 'оновленні'} товару`);
        } finally {
            setSubmitting(false);
        }
    };
    
    // Обробник зміни типу товару
    const handleTypeChange = (value: number) => {
        setSelectedTypeId(value);
        form.setFieldsValue({ subtypeid: undefined });
    };
    
    // Отримуємо підтипи, що відповідають вибраному типу
    const filteredSubtypes = !selectedTypeId 
        ? filterOptions.subtypes 
        : filterOptions.subtypes.filter(subtype => !subtype.typeid || subtype.typeid === selectedTypeId);
    
    // Відображаємо спінер під час завантаження
    if (mode === 'edit' && !product && loading) {
        return (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '50vh' }}>
                <Spin size="large" tip="Завантаження товару..." />
            </div>
        );
    }
    
    return (
        <FormContainer>
            <Card 
                title={
                    <Space>
                        <Button 
                            icon={<ArrowLeftOutlined />} 
                            onClick={() => history.push('/products')}
                            type="link"
                        />
                        {mode === 'create' ? 'Додати новий товар' : `Редагування товару ${product?.productnumber}`}
                    </Space>
                }
            >
                <Form
                    form={form}
                    layout="vertical"
                    initialValues={{ 
                        is_visible: true,
                        quantity: 1,
                        ...product 
                    }}
                    onFinish={handleSubmit}
                >
                    <Divider orientation="left">Основна інформація</Divider>
                    <Row gutter={16}>
                        <Col span={8}>
                            <Form.Item
                                name="productnumber"
                                label="Номер товару"
                                rules={[{ required: true, message: 'Номер товару обов\'язковий' }]}
                            >
                                <Input placeholder="Введіть унікальний номер товару" disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={8}>
                            <Form.Item
                                name="clonednumbers"
                                label="Номери клонів"
                            >
                                <Input placeholder="Номери через кому" disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={8}>
                            <Form.Item
                                name="quantity"
                                label="Кількість"
                            >
                                <InputNumber min={0} style={{ width: '100%' }} disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                    </Row>
                    
                    <Row gutter={16}>
                        <Col span={8}>
                            <Form.Item
                                name="typeid"
                                label="Тип"
                            >
                                <Select 
                                    placeholder="Виберіть тип" 
                                    allowClear 
                                    onChange={handleTypeChange}
                                    disabled={loading || submitting}
                                >
                                    {filterOptions.types.map(type => (
                                        <Option key={type.id} value={type.id}>{type.name}</Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                        <Col span={8}>
                            <Form.Item
                                name="subtypeid"
                                label="Підтип"
                            >
                                <Select 
                                    placeholder="Виберіть підтип" 
                                    allowClear
                                    disabled={loading || submitting || !selectedTypeId}
                                >
                                    {filteredSubtypes.map(subtype => (
                                        <Option key={subtype.id} value={subtype.id}>{subtype.name}</Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                        <Col span={8}>
                            <Form.Item
                                name="brandid"
                                label="Бренд"
                            >
                                <Select 
                                    placeholder="Виберіть бренд" 
                                    allowClear
                                    disabled={loading || submitting}
                                    showSearch
                                    optionFilterProp="children"
                                >
                                    {filterOptions.brands.map(brand => (
                                        <Option key={brand.id} value={brand.id}>{brand.name}</Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                    </Row>
                    
                    <Row gutter={16}>
                        <Col span={8}>
                            <Form.Item
                                name="model"
                                label="Модель"
                            >
                                <Input placeholder="Модель товару" disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={8}>
                            <Form.Item
                                name="marking"
                                label="Маркування"
                            >
                                <Input placeholder="Маркування товару" disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={8}>
                            <Form.Item
                                name="year"
                                label="Рік"
                            >
                                <InputNumber 
                                    style={{ width: '100%' }} 
                                    min={1900} 
                                    max={new Date().getFullYear() + 1}
                                    disabled={loading || submitting}
                                />
                            </Form.Item>
                        </Col>
                    </Row>
                    
                    <Row gutter={16}>
                        <Col span={12}>
                            <Form.Item
                                name="description"
                                label="Опис"
                            >
                                <TextArea rows={4} placeholder="Опис товару" disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={12}>
                            <Form.Item
                                name="extranote"
                                label="Додаткові примітки"
                            >
                                <TextArea rows={4} placeholder="Додаткова інформація" disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                    </Row>
                    
                    <Divider orientation="left">Характеристики</Divider>
                    <Row gutter={16}>
                        <Col span={6}>
                            <Form.Item
                                name="genderid"
                                label="Стать"
                            >
                                <Select 
                                    placeholder="Виберіть стать" 
                                    allowClear
                                    disabled={loading || submitting}
                                >
                                    {filterOptions.genders.map(gender => (
                                        <Option key={gender.id} value={gender.id}>{gender.name}</Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                        <Col span={6}>
                            <Form.Item
                                name="colorid"
                                label="Колір"
                            >
                                <Select 
                                    placeholder="Виберіть колір" 
                                    allowClear
                                    disabled={loading || submitting}
                                    showSearch
                                    optionFilterProp="children"
                                >
                                    {filterOptions.colors.map(color => (
                                        <Option key={color.id} value={color.id}>{color.name}</Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                        <Col span={6}>
                            <Form.Item
                                name="conditionid"
                                label="Стан"
                            >
                                <Select 
                                    placeholder="Виберіть стан" 
                                    allowClear
                                    disabled={loading || submitting}
                                >
                                    {filterOptions.conditions.map(condition => (
                                        <Option key={condition.id} value={condition.id}>{condition.name}</Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                        <Col span={6}>
                            <Form.Item
                                name="statusid"
                                label="Статус"
                            >
                                <Select 
                                    placeholder="Виберіть статус" 
                                    allowClear
                                    disabled={loading || submitting}
                                >
                                    {filterOptions.statuses.map(status => (
                                        <Option key={status.id} value={status.id}>{status.name}</Option>
                                    ))}
                                </Select>
                            </Form.Item>
                        </Col>
                    </Row>
                    
                    <Row gutter={16}>
                        <Col span={6}>
                            <Form.Item
                                name="ownercountryid"
                                label="Країна власника"
                            >
                                <Select 
                                    placeholder="Виберіть країну" 
                                    allowClear
                                    disabled={loading || submitting}
                                >
                                    {/* Тут потрібно додати список країн */}
                                </Select>
                            </Form.Item>
                        </Col>
                        <Col span={6}>
                            <Form.Item
                                name="manufacturercountryid"
                                label="Країна виробника"
                            >
                                <Select 
                                    placeholder="Виберіть країну" 
                                    allowClear
                                    disabled={loading || submitting}
                                >
                                    {/* Тут потрібно додати список країн */}
                                </Select>
                            </Form.Item>
                        </Col>
                        <Col span={6}>
                            <Form.Item
                                name="importid"
                                label="Імпорт"
                            >
                                <Select 
                                    placeholder="Виберіть імпорт" 
                                    allowClear
                                    disabled={loading || submitting}
                                >
                                    {/* Тут потрібно додати список імпортів */}
                                </Select>
                            </Form.Item>
                        </Col>
                        <Col span={6}>
                            <Form.Item
                                name="deliveryid"
                                label="Доставка"
                            >
                                <Select 
                                    placeholder="Виберіть метод доставки" 
                                    allowClear
                                    disabled={loading || submitting}
                                >
                                    {/* Тут потрібно додати список методів доставки */}
                                </Select>
                            </Form.Item>
                        </Col>
                    </Row>
                    
                    <Divider orientation="left">Ціна</Divider>
                    <Row gutter={16}>
                        <Col span={12}>
                            <Form.Item
                                name="price"
                                label="Ціна"
                            >
                                <InputNumber
                                    style={{ width: '100%' }}
                                    min={0}
                                    step={0.01}
                                    formatter={value => value ? `${value} ₴` : ''}
                                    parser={value => value ? value.replace(/[^\d.]/g, '') : ''}
                                    disabled={loading || submitting}
                                />
                            </Form.Item>
                        </Col>
                        <Col span={12}>
                            <Form.Item
                                name="oldprice"
                                label="Стара ціна"
                            >
                                <InputNumber
                                    style={{ width: '100%' }}
                                    min={0}
                                    step={0.01}
                                    formatter={value => value ? `${value} ₴` : ''}
                                    parser={value => value ? value.replace(/[^\d.]/g, '') : ''}
                                    disabled={loading || submitting}
                                />
                            </Form.Item>
                        </Col>
                    </Row>
                    
                    <Divider orientation="left">Розміри</Divider>
                    <Row gutter={16}>
                        <Col span={4}>
                            <Form.Item
                                name="sizeeu"
                                label="Розмір EU"
                            >
                                <Input disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={4}>
                            <Form.Item
                                name="sizeua"
                                label="Розмір UA"
                            >
                                <Input disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={4}>
                            <Form.Item
                                name="sizeusa"
                                label="Розмір USA"
                            >
                                <Input disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={4}>
                            <Form.Item
                                name="sizeuk"
                                label="Розмір UK"
                            >
                                <Input disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={4}>
                            <Form.Item
                                name="sizejp"
                                label="Розмір JP"
                            >
                                <Input disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={4}>
                            <Form.Item
                                name="sizecn"
                                label="Розмір CN"
                            >
                                <Input disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                    </Row>
                    
                    <Row gutter={16}>
                        <Col span={12}>
                            <Form.Item
                                name="measurementscm"
                                label="Вимірювання (см)"
                            >
                                <Input placeholder="Наприклад: 100x50x30" disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                        <Col span={12}>
                            <Form.Item
                                name="mainimage"
                                label="Посилання на зображення"
                            >
                                <Input placeholder="URL зображення" disabled={loading || submitting} />
                            </Form.Item>
                        </Col>
                    </Row>
                    
                    <Row>
                        <Col span={24}>
                            <Form.Item
                                name="is_visible"
                                valuePropName="checked"
                            >
                                <Checkbox disabled={loading || submitting}>Товар видимий</Checkbox>
                            </Form.Item>
                        </Col>
                    </Row>
                    
                    <SubmitButtons>
                        <Space>
                            <Button
                                onClick={() => history.push('/products')}
                                icon={<CloseOutlined />}
                                disabled={loading || submitting}
                            >
                                Скасувати
                            </Button>
                            <Button
                                type="primary"
                                htmlType="submit"
                                icon={<SaveOutlined />}
                                loading={submitting}
                                disabled={loading}
                            >
                                {mode === 'create' ? 'Створити товар' : 'Зберегти зміни'}
                            </Button>
                        </Space>
                    </SubmitButtons>
                </Form>
            </Card>
        </FormContainer>
    );
};

export default ProductForm; 