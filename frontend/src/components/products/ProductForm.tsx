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
import { Product } from '../../types/product';
import { useNavigate, useParams } from 'react-router-dom';
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
    onSubmit: (data: any) => Promise<void>;
    loading: boolean;
}

interface RouteParams {
    id?: string;
}

const ProductForm: React.FC<ProductFormProps> = ({ 
    mode, 
    product, 
    onSubmit, 
    loading 
}) => {
    const [form] = Form.useForm();
    const navigate = useNavigate();
    const { id } = useParams<{ id?: string }>();
    const [submitting, setSubmitting] = useState(false);
    
    // Оновлюємо форму при отриманні даних товару
    useEffect(() => {
        if (product) {
            form.setFieldsValue(product);
        }
    }, [product, form]);
    
    // Обробник подання форми
    const handleSubmit = async (values: any) => {
        setSubmitting(true);
        try {
            await onSubmit(values);
            message.success(`Товар успішно ${mode === 'create' ? 'створено' : 'оновлено'}`);
            navigate('/products');
        } catch (error) {
            console.error('Error submitting form:', error);
            message.error(`Помилка при ${mode === 'create' ? 'створенні' : 'оновленні'} товару`);
        } finally {
            setSubmitting(false);
        }
    };
    
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
                            onClick={() => navigate('/products')}
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
                                    disabled={loading || submitting}
                                >
                                    {/* Тут потрібно додати список типів */}
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
                                    disabled={loading || submitting}
                                >
                                    {/* Тут потрібно додати список підтипів */}
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
                                    {/* Тут потрібно додати список брендів */}
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
                                    {/* Тут потрібно додати список статей */}
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
                                    {/* Тут потрібно додати список кольорів */}
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
                                    {/* Тут потрібно додати список станів */}
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
                                    {/* Тут потрібно додати список статусів */}
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
                                    formatter={(value: number | string | undefined) => value ? `${value} ₴` : ''}
                                    parser={(value: string | undefined) => value ? Number(value.replace(/[^\d.]/g, '')) : 0}
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
                                    formatter={(value: number | string | undefined) => value ? `${value} ₴` : ''}
                                    parser={(value: string | undefined) => value ? Number(value.replace(/[^\d.]/g, '')) : 0}
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
                                onClick={() => navigate('/products')}
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