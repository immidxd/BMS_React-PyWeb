import React from 'react';
import styled from 'styled-components';
import { useSearch } from '../App';

const TabContainer = styled.div`
  display: flex;
  flex-direction: column;
  height: calc(100% - 20px);
  padding-top: 10px;
`;

const MainContentArea = styled.div`
  width: 100%;
  height: 100%;
`;

const OrdersTable = styled.table`
  width: 100%;
  border-collapse: collapse;
  background-color: white;
`;

const TableHeader = styled.th`
  text-align: left;
  padding: 12px 15px;
  background-color: #f8f9fa;
  border-bottom: 2px solid #e9ecef;
`;

const TableRow = styled.tr`
  &:nth-child(even) {
    background-color: #f9f9f9;
  }
  
  &:hover {
    background-color: #f5f5f5;
  }
`;

const TableCell = styled.td`
  padding: 12px 15px;
  border-bottom: 1px solid #e9ecef;
`;

const Badge = styled.span<{ color: string }>`
  display: inline-block;
  padding: 4px 8px;
  border-radius: 4px;
  background-color: ${props => props.color};
  color: white;
  font-size: 12px;
  font-weight: bold;
`;

const OrdersTab: React.FC = () => {
  const { searchTerm } = useSearch();
  
  // Демонстраційні дані для замовлень
  const demoOrders = [
    { 
      id: 1, 
      customer: 'Іван Петренко', 
      date: '2023-04-15', 
      status: 'Нове', 
      statusColor: '#4a6da7',
      paymentStatus: 'Оплачено', 
      paymentMethod: 'Картка',
      shippingMethod: 'Нова Пошта',
      total: 4700 
    },
    { 
      id: 2, 
      customer: 'Олена Коваль', 
      date: '2023-04-12', 
      status: 'Відправлено', 
      statusColor: '#28a745',
      paymentStatus: 'Оплачено', 
      paymentMethod: 'Готівка',
      shippingMethod: 'Укрпошта',
      total: 2200 
    },
    { 
      id: 3, 
      customer: 'Микола Сидоренко', 
      date: '2023-04-10', 
      status: 'Доставлено', 
      statusColor: '#6c757d',
      paymentStatus: 'Оплачено', 
      paymentMethod: 'Готівка',
      shippingMethod: 'Самовивіз',
      total: 3500 
    },
    { 
      id: 4, 
      customer: 'Тетяна Шевченко', 
      date: '2023-04-08', 
      status: 'Скасовано', 
      statusColor: '#dc3545',
      paymentStatus: 'Повернуто', 
      paymentMethod: 'Картка',
      shippingMethod: 'Нова Пошта',
      total: 1800 
    },
  ];

  // Фільтрація замовлень за пошуковим запитом
  const filteredOrders = searchTerm
    ? demoOrders.filter(order => 
        order.customer.toLowerCase().includes(searchTerm.toLowerCase()) ||
        order.status.toLowerCase().includes(searchTerm.toLowerCase()) ||
        order.id.toString().includes(searchTerm)
      )
    : demoOrders;

  return (
    <TabContainer>
      <MainContentArea>
        <OrdersTable>
          <thead>
            <tr>
              <TableHeader>ID</TableHeader>
              <TableHeader>Клієнт</TableHeader>
              <TableHeader>Дата</TableHeader>
              <TableHeader>Статус</TableHeader>
              <TableHeader>Статус оплати</TableHeader>
              <TableHeader>Метод оплати</TableHeader>
              <TableHeader>Доставка</TableHeader>
              <TableHeader>Сума</TableHeader>
            </tr>
          </thead>
          <tbody>
            {filteredOrders.map(order => (
              <TableRow key={order.id}>
                <TableCell>#{order.id}</TableCell>
                <TableCell>{order.customer}</TableCell>
                <TableCell>{order.date}</TableCell>
                <TableCell>
                  <Badge color={order.statusColor}>{order.status}</Badge>
                </TableCell>
                <TableCell>{order.paymentStatus}</TableCell>
                <TableCell>{order.paymentMethod}</TableCell>
                <TableCell>{order.shippingMethod}</TableCell>
                <TableCell>{order.total} ₴</TableCell>
              </TableRow>
            ))}
          </tbody>
        </OrdersTable>
      </MainContentArea>
    </TabContainer>
  );
};

export default OrdersTab; 