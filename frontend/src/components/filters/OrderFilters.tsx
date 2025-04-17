import React from 'react';
import styled from 'styled-components';

const FiltersContainer = styled.div`
  display: flex;
  flex-direction: column;
  gap: 15px;
  padding-right: 10px;
  max-height: 100%;
  overflow-y: auto;
`;

const FiltersTitle = styled.h3`
  margin-top: 0;
  margin-bottom: 15px;
  font-size: 18px;
  padding-bottom: 10px;
  border-bottom: 1px solid #ddd;
`;

const CheckboxContainer = styled.div`
  display: flex;
  align-items: center;
  margin-top: 10px;
  margin-bottom: 10px;
  gap: 8px;
`;

const FilterSection = styled.div`
  margin-bottom: 15px;
`;

const FilterTitle = styled.h4`
  margin-top: 0;
  margin-bottom: 10px;
`;

const CheckboxLabel = styled.label`
  display: flex;
  align-items: center;
  margin-bottom: 5px;
  cursor: pointer;
  
  input {
    margin-right: 8px;
  }
`;

const OrderFilters: React.FC = () => {
  // Спрощений компонент без API викликів
  
  return (
    <FiltersContainer>
      <FiltersTitle>Фільтри замовлень</FiltersTitle>
      
      <FilterSection>
        <FilterTitle>Статуси</FilterTitle>
        <CheckboxLabel>
          <input type="checkbox" /> Новий
        </CheckboxLabel>
        <CheckboxLabel>
          <input type="checkbox" /> В обробці
        </CheckboxLabel>
        <CheckboxLabel>
          <input type="checkbox" /> Відправлено
        </CheckboxLabel>
        <CheckboxLabel>
          <input type="checkbox" /> Доставлено
        </CheckboxLabel>
      </FilterSection>
      
      <FilterSection>
        <FilterTitle>Клієнти</FilterTitle>
        <CheckboxLabel>
          <input type="checkbox" /> Іван Петренко
        </CheckboxLabel>
        <CheckboxLabel>
          <input type="checkbox" /> Олена Коваль
        </CheckboxLabel>
        <CheckboxLabel>
          <input type="checkbox" /> Микола Сидоренко
        </CheckboxLabel>
      </FilterSection>
      
      <FilterSection>
        <FilterTitle>Спосіб доставки</FilterTitle>
        <CheckboxLabel>
          <input type="checkbox" /> Нова Пошта
        </CheckboxLabel>
        <CheckboxLabel>
          <input type="checkbox" /> Укрпошта
        </CheckboxLabel>
        <CheckboxLabel>
          <input type="checkbox" /> Самовивіз
        </CheckboxLabel>
      </FilterSection>

      <FilterSection>
        <FilterTitle>Спосіб оплати</FilterTitle>
        <CheckboxLabel>
          <input type="checkbox" /> Готівка
        </CheckboxLabel>
        <CheckboxLabel>
          <input type="checkbox" /> Картка
        </CheckboxLabel>
        <CheckboxLabel>
          <input type="checkbox" /> Накладений платіж
        </CheckboxLabel>
      </FilterSection>

      <CheckboxContainer>
        <input type="checkbox" id="pending-only" />
        <label htmlFor="pending-only">Тільки в обробці</label>
      </CheckboxContainer>
    </FiltersContainer>
  );
};

export default OrderFilters; 