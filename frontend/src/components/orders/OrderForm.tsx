import React, { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import styled from 'styled-components';
import { format, parse } from 'date-fns';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faSave, faTrash, faPlus, faArrowLeft } from '@fortawesome/free-solid-svg-icons';
import { 
  OrderWithDetails, 
  OrderItem, 
  fetchOrder, 
  fetchOrderFilters, 
  createOrder, 
  updateOrder, 
  FilterOptions 
} from '../../services/orderService';
import productService from '../../services/productService';
import { Product } from '../../types/product';

// Styled components
const Container = styled.div`
  padding: 20px;
`;

const PageHeader = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
`;

const PageTitle = styled.h1`
  font-size: 24px;
  margin: 0;
`;

const BackButton = styled.button`
  background: none;
  border: none;
  color: #007bff;
  cursor: pointer;
  display: flex;
  align-items: center;
  font-size: 16px;
  
  svg {
    margin-right: 8px;
  }
  
  &:hover {
    text-decoration: underline;
  }
`;

const Form = styled.form`
  background-color: white;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  padding: 20px;
`;

const FormSection = styled.div`
  margin-bottom: 20px;
  
  h2 {
    font-size: 18px;
    margin-bottom: 15px;
    padding-bottom: 8px;
    border-bottom: 1px solid #e9ecef;
  }
`;

const FormRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  margin: 0 -10px;
`;

const FormGroup = styled.div<{ width?: string }>`
  flex: ${props => props.width || '1'};
  padding: 0 10px;
  margin-bottom: 15px;
  min-width: 200px;
  
  @media (max-width: 768px) {
    flex: 1 1 100%;
  }
`;

const Label = styled.label`
  display: block;
  margin-bottom: 5px;
  font-weight: 500;
`;

const Input = styled.input<{ error?: boolean }>`
  display: block;
  width: 100%;
  padding: 8px 12px;
  border: 1px solid ${props => props.error ? '#dc3545' : '#ced4da'};
  border-radius: 4px;
  font-size: 16px;
  
  &:focus {
    outline: none;
    border-color: ${props => props.error ? '#dc3545' : '#80bdff'};
    box-shadow: 0 0 0 0.2rem ${props => props.error ? 'rgba(220, 53, 69, 0.25)' : 'rgba(0, 123, 255, 0.25)'};
  }
`;

const Select = styled.select<{ error?: boolean }>`
  display: block;
  width: 100%;
  padding: 8px 12px;
  border: 1px solid ${props => props.error ? '#dc3545' : '#ced4da'};
  border-radius: 4px;
  font-size: 16px;
  appearance: auto;
  
  &:focus {
    outline: none;
    border-color: ${props => props.error ? '#dc3545' : '#80bdff'};
    box-shadow: 0 0 0 0.2rem ${props => props.error ? 'rgba(220, 53, 69, 0.25)' : 'rgba(0, 123, 255, 0.25)'};
  }
`;

const TextArea = styled.textarea<{ error?: boolean }>`
  display: block;
  width: 100%;
  padding: 8px 12px;
  border: 1px solid ${props => props.error ? '#dc3545' : '#ced4da'};
  border-radius: 4px;
  font-size: 16px;
  min-height: 100px;
  resize: vertical;
  
  &:focus {
    outline: none;
    border-color: ${props => props.error ? '#dc3545' : '#80bdff'};
    box-shadow: 0 0 0 0.2rem ${props => props.error ? 'rgba(220, 53, 69, 0.25)' : 'rgba(0, 123, 255, 0.25)'};
  }
`;

const ErrorText = styled.div`
  color: #dc3545;
  font-size: 12px;
  margin-top: 4px;
`;

const OrderItemCard = styled.div`
  border: 1px solid #e9ecef;
  border-radius: 4px;
  padding: 15px;
  margin-bottom: 15px;
  position: relative;
`;

const RemoveItemButton = styled.button`
  position: absolute;
  top: 10px;
  right: 10px;
  background: none;
  border: none;
  color: #dc3545;
  cursor: pointer;
  padding: 5px;
  
  &:hover {
    color: #bd2130;
  }
`;

const AddItemButton = styled.button`
  background-color: #28a745;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 8px 15px;
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  
  svg {
    margin-right: 8px;
  }
  
  &:hover {
    background-color: #218838;
  }
`;

const FormActions = styled.div`
  display: flex;
  justify-content: flex-end;
  margin-top: 20px;
  gap: 10px;
`;

const SubmitButton = styled.button`
  background-color: #007bff;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 10px 20px;
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  
  svg {
    margin-right: 8px;
  }
  
  &:hover {
    background-color: #0069d9;
  }
  
  &:disabled {
    background-color: #6c757d;
    cursor: not-allowed;
  }
`;

const CancelButton = styled.button`
  background-color: #6c757d;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 10px 20px;
  font-size: 16px;
  cursor: pointer;
  
  &:hover {
    background-color: #5a6268;
  }
`;

interface Errors {
  [key: string]: string;
}

const OrderForm: React.FC = () => {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const isEdit = id !== 'new' && !!id;
  
  // Form state
  const [order, setOrder] = useState<Partial<OrderWithDetails>>({
    client_id: undefined,
    order_date: format(new Date(), 'yyyy-MM-dd'),
    order_status_id: undefined,
    total_amount: 0,
    payment_method_id: undefined,
    payment_status_id: undefined,
    payment_status: '',
    delivery_method_id: undefined,
    delivery_address_id: undefined,
    delivery_status_id: undefined,
    tracking_number: '',
    notes: '',
    deferred_until: undefined,
    priority: 0,
    broadcast_id: undefined,
    order_items: []
  });
  
  // Data state
  const [filterOptions, setFilterOptions] = useState<FilterOptions>({
    order_statuses: [],
    payment_statuses: [],
    payment_methods: [],
    delivery_methods: [],
    delivery_statuses: [],
    clients: []
  });
  
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [errors, setErrors] = useState<Errors>({});
  
  // Load data on component mount
  useEffect(() => {
    const loadData = async () => {
      try {
        // Load dropdown options
        const options = await fetchOrderFilters();
        setFilterOptions(options);
        
        // Load products
        const productsResponse = await productService.getProducts();
        setProducts(productsResponse.items || []);
        
        // If editing, load order data
        if (isEdit && id) {
          const orderData = await fetchOrder(parseInt(id));
          
          // Format dates for input fields
          if (orderData.order_date) {
            orderData.order_date = format(new Date(orderData.order_date), 'yyyy-MM-dd');
          }
          
          if (orderData.deferred_until) {
            orderData.deferred_until = format(new Date(orderData.deferred_until), 'yyyy-MM-dd');
          }
          
          setOrder(orderData);
        }
      } catch (err) {
        console.error('Error loading form data:', err);
      } finally {
        setLoading(false);
      }
    };
    
    loadData();
  }, [isEdit, id]);
  
  // Handle change for general order data
  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value, type } = e.target;
    
    let processedValue: any = value;
    
    // Convert numeric values
    if (type === 'number') {
      processedValue = value === '' ? '' : Number(value);
    }
    
    // Clear error when field is updated
    if (errors[name]) {
      setErrors({ ...errors, [name]: '' });
    }
    
    setOrder({ ...order, [name]: processedValue });
  };
  
  // Handle change for order items
  const handleItemChange = (index: number, e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value, type } = e.target;
    
    if (!order.order_items) return;
    
    let processedValue: any = value;
    
    // Convert numeric values
    if (type === 'number') {
      processedValue = value === '' ? '' : Number(value);
    }
    
    // Clear error when field is updated
    if (errors[`order_items.${index}.${name}`]) {
      setErrors({ ...errors, [`order_items.${index}.${name}`]: '' });
    }
    
    const updatedItems = [...order.order_items];
    updatedItems[index] = { ...updatedItems[index], [name]: processedValue };
    
    // If product_id changed, set price from product
    if (name === 'product_id' && processedValue) {
      const productId = Number(processedValue);
      const product = products.find(p => p.id === productId);
      if (product && product.price !== undefined) {
        updatedItems[index].price = product.price;
      }
    }
    
    setOrder({ ...order, order_items: updatedItems });
    
    // Recalculate total when item changes
    recalculateTotal(updatedItems);
  };
  
  // Add new item
  const addItem = () => {
    if (!order.order_items) return;
    
    const newItem: OrderItem = {
      product_id: 0,
      quantity: 1,
      price: 0,
      discount_type: null,
      discount_value: null,
      additional_operation: null,
      additional_operation_value: null,
      notes: null
    };
    
    setOrder({ ...order, order_items: [...order.order_items, newItem] });
  };
  
  // Remove item
  const removeItem = (index: number) => {
    if (!order.order_items) return;
    
    const updatedItems = [...order.order_items];
    updatedItems.splice(index, 1);
    
    setOrder({ ...order, order_items: updatedItems });
    
    // Recalculate total after removing item
    recalculateTotal(updatedItems);
  };
  
  // Recalculate order total
  const recalculateTotal = (items: OrderItem[]) => {
    let total = 0;
    
    items.forEach(item => {
      let itemTotal = item.price * item.quantity;
      
      // Apply discount
      if (item.discount_type === 'Відсоток' && item.discount_value) {
        itemTotal = itemTotal * (1 - item.discount_value / 100);
      } else if (item.discount_type === 'Фіксована' && item.discount_value) {
        itemTotal = itemTotal - item.discount_value;
      }
      
      // Apply additional operation
      if (item.additional_operation_value) {
        itemTotal += item.additional_operation_value;
      }
      
      total += itemTotal;
    });
    
    setOrder(prevOrder => ({ ...prevOrder, total_amount: Math.max(0, total) }));
  };
  
  // Form validation
  const validateForm = () => {
    const newErrors: Errors = {};
    
    // Validate required fields
    if (!order.client_id) {
      newErrors.client_id = 'Виберіть клієнта';
    }
    
    if (!order.order_date) {
      newErrors.order_date = 'Виберіть дату замовлення';
    }
    
    // Validate order items
    if (!order.order_items || order.order_items.length === 0) {
      newErrors.order_items = 'Додайте хоча б один товар';
    } else {
      order.order_items.forEach((item, index) => {
        if (!item.product_id) {
          newErrors[`order_items.${index}.product_id`] = 'Виберіть товар';
        }
        
        if (!item.quantity || item.quantity <= 0) {
          newErrors[`order_items.${index}.quantity`] = 'Кількість повинна бути більше 0';
        }
        
        if (item.price < 0) {
          newErrors[`order_items.${index}.price`] = 'Ціна не може бути від\'ємною';
        }
      });
    }
    
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };
  
  // Handle form submission
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!validateForm()) {
      return;
    }
    
    setSaving(true);
    
    try {
      if (isEdit && id) {
        // Update existing order
        await updateOrder(parseInt(id), order);
      } else {
        // Create new order
        await createOrder(order as any);
      }
      
      // Navigate back to orders list
      navigate('/orders');
    } catch (err) {
      console.error('Error saving order:', err);
      alert('Помилка при збереженні замовлення');
    } finally {
      setSaving(false);
    }
  };
  
  // Navigate back
  const handleBack = () => {
    navigate('/orders');
  };
  
  // Loading state
  if (loading) {
    return <div>Завантаження даних...</div>;
  }
  
  return (
    <Container>
      <PageHeader>
        <PageTitle>{isEdit ? 'Редагування замовлення' : 'Нове замовлення'}</PageTitle>
        <BackButton onClick={handleBack}>
          <FontAwesomeIcon icon={faArrowLeft} />
          Назад до списку
        </BackButton>
      </PageHeader>
      
      <Form onSubmit={handleSubmit}>
        {/* Basic order information */}
        <FormSection>
          <h2>Основна інформація</h2>
          <FormRow>
            <FormGroup width="2">
              <Label htmlFor="client_id">Клієнт *</Label>
              <Select 
                id="client_id" 
                name="client_id" 
                value={order.client_id || ''} 
                onChange={handleChange}
                error={!!errors.client_id}
              >
                <option value="">- Виберіть клієнта -</option>
                {filterOptions.clients.map(client => (
                  <option key={client.id} value={client.id}>{client.name}</option>
                ))}
              </Select>
              {errors.client_id && <ErrorText>{errors.client_id}</ErrorText>}
            </FormGroup>
            
            <FormGroup>
              <Label htmlFor="order_date">Дата замовлення *</Label>
              <Input 
                type="date" 
                id="order_date" 
                name="order_date" 
                value={order.order_date || ''} 
                onChange={handleChange}
                error={!!errors.order_date}
              />
              {errors.order_date && <ErrorText>{errors.order_date}</ErrorText>}
            </FormGroup>
            
            <FormGroup>
              <Label htmlFor="order_status_id">Статус замовлення</Label>
              <Select 
                id="order_status_id" 
                name="order_status_id" 
                value={order.order_status_id || ''} 
                onChange={handleChange}
              >
                <option value="">- Не обрано -</option>
                {filterOptions.order_statuses.map(status => (
                  <option key={status.id} value={status.id}>{status.name}</option>
                ))}
              </Select>
            </FormGroup>
          </FormRow>
          
          <FormRow>
            <FormGroup>
              <Label htmlFor="payment_method_id">Метод оплати</Label>
              <Select 
                id="payment_method_id" 
                name="payment_method_id" 
                value={order.payment_method_id || ''} 
                onChange={handleChange}
              >
                <option value="">- Не обрано -</option>
                {filterOptions.payment_methods.map(method => (
                  <option key={method.id} value={method.id}>{method.name}</option>
                ))}
              </Select>
            </FormGroup>
            
            <FormGroup>
              <Label htmlFor="payment_status_id">Статус оплати</Label>
              <Select 
                id="payment_status_id" 
                name="payment_status_id" 
                value={order.payment_status_id || ''} 
                onChange={handleChange}
              >
                <option value="">- Не обрано -</option>
                {filterOptions.payment_statuses.map(status => (
                  <option key={status.id} value={status.id}>{status.name}</option>
                ))}
              </Select>
            </FormGroup>
            
            <FormGroup>
              <Label htmlFor="payment_status">Текстовий статус оплати</Label>
              <Input 
                type="text" 
                id="payment_status" 
                name="payment_status" 
                value={order.payment_status || ''} 
                onChange={handleChange}
                placeholder="Наприклад: Оплачено 50%, решта при отриманні"
              />
            </FormGroup>
          </FormRow>
          
          <FormRow>
            <FormGroup>
              <Label htmlFor="delivery_method_id">Метод доставки</Label>
              <Select 
                id="delivery_method_id" 
                name="delivery_method_id" 
                value={order.delivery_method_id || ''} 
                onChange={handleChange}
              >
                <option value="">- Не обрано -</option>
                {filterOptions.delivery_methods.map(method => (
                  <option key={method.id} value={method.id}>{method.name}</option>
                ))}
              </Select>
            </FormGroup>
            
            <FormGroup>
              <Label htmlFor="delivery_status_id">Статус доставки</Label>
              <Select 
                id="delivery_status_id" 
                name="delivery_status_id" 
                value={order.delivery_status_id || ''} 
                onChange={handleChange}
              >
                <option value="">- Не обрано -</option>
                {filterOptions.delivery_statuses.map(status => (
                  <option key={status.id} value={status.id}>{status.name}</option>
                ))}
              </Select>
            </FormGroup>
            
            <FormGroup>
              <Label htmlFor="tracking_number">Номер відстеження</Label>
              <Input 
                type="text" 
                id="tracking_number" 
                name="tracking_number" 
                value={order.tracking_number || ''} 
                onChange={handleChange}
                placeholder="Номер трекінгу посилки"
              />
            </FormGroup>
          </FormRow>
          
          <FormRow>
            <FormGroup>
              <Label htmlFor="deferred_until">Відкладено до</Label>
              <Input 
                type="date" 
                id="deferred_until" 
                name="deferred_until" 
                value={order.deferred_until || ''} 
                onChange={handleChange}
              />
            </FormGroup>
            
            <FormGroup>
              <Label htmlFor="priority">Пріоритет</Label>
              <Input 
                type="number" 
                id="priority" 
                name="priority" 
                value={order.priority || 0} 
                onChange={handleChange}
                min="0"
                max="10"
              />
            </FormGroup>
            
            <FormGroup>
              <Label htmlFor="total_amount">Загальна сума (розрахункова)</Label>
              <Input 
                type="number" 
                id="total_amount" 
                name="total_amount" 
                value={order.total_amount || 0} 
                readOnly 
              />
            </FormGroup>
          </FormRow>
          
          <FormRow>
            <FormGroup width="1">
              <Label htmlFor="notes">Примітки</Label>
              <TextArea 
                id="notes" 
                name="notes" 
                value={order.notes || ''} 
                onChange={handleChange}
                placeholder="Додаткова інформація про замовлення"
              />
            </FormGroup>
          </FormRow>
        </FormSection>
        
        {/* Order items */}
        <FormSection>
          <h2>Товари замовлення</h2>
          
          {errors.order_items && <ErrorText>{errors.order_items}</ErrorText>}
          
          {order.order_items && order.order_items.length > 0 ? (
            order.order_items.map((item, index) => (
              <OrderItemCard key={index}>
                <RemoveItemButton type="button" onClick={() => removeItem(index)}>
                  <FontAwesomeIcon icon={faTrash} />
                </RemoveItemButton>
                
                <FormRow>
                  <FormGroup width="2">
                    <Label htmlFor={`item-${index}-product`}>Товар *</Label>
                    <Select 
                      id={`item-${index}-product`}
                      name="product_id"
                      value={item.product_id || ''}
                      onChange={(e) => handleItemChange(index, e)}
                      error={!!errors[`order_items.${index}.product_id`]}
                    >
                      <option value="">- Виберіть товар -</option>
                      {products.map(product => (
                        <option key={product.id} value={product.id}>
                          {product.productnumber} - {product.model} {product.marking}
                        </option>
                      ))}
                    </Select>
                    {errors[`order_items.${index}.product_id`] && (
                      <ErrorText>{errors[`order_items.${index}.product_id`]}</ErrorText>
                    )}
                  </FormGroup>
                  
                  <FormGroup>
                    <Label htmlFor={`item-${index}-quantity`}>Кількість *</Label>
                    <Input 
                      type="number"
                      id={`item-${index}-quantity`}
                      name="quantity"
                      value={item.quantity || 1}
                      onChange={(e) => handleItemChange(index, e)}
                      min="1"
                      error={!!errors[`order_items.${index}.quantity`]}
                    />
                    {errors[`order_items.${index}.quantity`] && (
                      <ErrorText>{errors[`order_items.${index}.quantity`]}</ErrorText>
                    )}
                  </FormGroup>
                  
                  <FormGroup>
                    <Label htmlFor={`item-${index}-price`}>Ціна *</Label>
                    <Input 
                      type="number"
                      id={`item-${index}-price`}
                      name="price"
                      value={item.price || 0}
                      onChange={(e) => handleItemChange(index, e)}
                      min="0"
                      step="0.01"
                      error={!!errors[`order_items.${index}.price`]}
                    />
                    {errors[`order_items.${index}.price`] && (
                      <ErrorText>{errors[`order_items.${index}.price`]}</ErrorText>
                    )}
                  </FormGroup>
                </FormRow>
                
                <FormRow>
                  <FormGroup>
                    <Label htmlFor={`item-${index}-discount-type`}>Тип знижки</Label>
                    <Select
                      id={`item-${index}-discount-type`}
                      name="discount_type"
                      value={item.discount_type || ''}
                      onChange={(e) => handleItemChange(index, e)}
                    >
                      <option value="">Немає знижки</option>
                      <option value="Відсоток">Відсоток</option>
                      <option value="Фіксована">Фіксована</option>
                    </Select>
                  </FormGroup>
                  
                  <FormGroup>
                    <Label htmlFor={`item-${index}-discount-value`}>
                      {item.discount_type === 'Відсоток' ? 'Знижка (%)' : 'Знижка (грн)'}
                    </Label>
                    <Input 
                      type="number"
                      id={`item-${index}-discount-value`}
                      name="discount_value"
                      value={item.discount_value || ''}
                      onChange={(e) => handleItemChange(index, e)}
                      min="0"
                      step="0.01"
                      disabled={!item.discount_type}
                    />
                  </FormGroup>
                  
                  <FormGroup>
                    <Label htmlFor={`item-${index}-additional-operation`}>Додаткова операція</Label>
                    <Input 
                      type="text"
                      id={`item-${index}-additional-operation`}
                      name="additional_operation"
                      value={item.additional_operation || ''}
                      onChange={(e) => handleItemChange(index, e)}
                      placeholder="Наприклад: Доставка"
                    />
                  </FormGroup>
                  
                  <FormGroup>
                    <Label htmlFor={`item-${index}-additional-operation-value`}>Значення операції (грн)</Label>
                    <Input 
                      type="number"
                      id={`item-${index}-additional-operation-value`}
                      name="additional_operation_value"
                      value={item.additional_operation_value || ''}
                      onChange={(e) => handleItemChange(index, e)}
                      step="0.01"
                    />
                  </FormGroup>
                </FormRow>
              </OrderItemCard>
            ))
          ) : (
            <div>Додайте товари до замовлення</div>
          )}
          
          <AddItemButton type="button" onClick={addItem}>
            <FontAwesomeIcon icon={faPlus} />
            Додати товар
          </AddItemButton>
        </FormSection>
        
        <FormActions>
          <CancelButton type="button" onClick={handleBack}>
            Скасувати
          </CancelButton>
          
          <SubmitButton type="submit" disabled={saving}>
            <FontAwesomeIcon icon={faSave} />
            {saving ? 'Збереження...' : 'Зберегти'}
          </SubmitButton>
        </FormActions>
      </Form>
    </Container>
  );
};

export default OrderForm; 