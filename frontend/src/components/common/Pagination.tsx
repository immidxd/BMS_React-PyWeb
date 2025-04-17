import React from 'react';
import styled from 'styled-components';

const PaginationContainer = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 20px;
  padding: 10px 0;
`;

const PageInfo = styled.div`
  font-size: 14px;
`;

const ButtonGroup = styled.div`
  display: flex;
  gap: 5px;
`;

const Button = styled.button<{ isActive?: boolean }>`
  padding: 5px 10px;
  border: 1px solid #ccc;
  background-color: ${props => props.isActive ? '#007bff' : 'white'};
  color: ${props => props.isActive ? 'white' : 'black'};
  cursor: pointer;
  border-radius: 4px;
  
  &:hover {
    background-color: ${props => props.isActive ? '#007bff' : '#f0f0f0'};
  }
  
  &:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
`;

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  totalItems?: number;
  itemsPerPage?: number;
  onPageChange: (page: number) => void;
  onPerPageChange?: (perPage: number) => void;
}

const Pagination: React.FC<PaginationProps> = ({
  currentPage,
  totalPages,
  totalItems = 0,
  itemsPerPage = 20,
  onPageChange,
  onPerPageChange
}) => {
  // Розраховуємо діапазон відображених елементів
  const startItem = totalItems ? (currentPage - 1) * itemsPerPage + 1 : 0;
  const endItem = totalItems ? Math.min(currentPage * itemsPerPage, totalItems) : 0;
  
  // Створюємо масив кнопок сторінок
  const pageButtons = [];
  const maxButtons = 5; // Максимальна кількість кнопок сторінок
  
  let startPage = Math.max(1, currentPage - Math.floor(maxButtons / 2));
  let endPage = startPage + maxButtons - 1;
  
  if (endPage > totalPages) {
    endPage = totalPages;
    startPage = Math.max(1, endPage - maxButtons + 1);
  }
  
  for (let i = startPage; i <= endPage; i++) {
    pageButtons.push(
      <Button 
        key={i}
        isActive={i === currentPage}
        onClick={() => onPageChange(i)}
      >
        {i}
      </Button>
    );
  }
  
  return (
    <PaginationContainer>
      <PageInfo>
        Показано {startItem}-{endItem} з {totalItems} записів
      </PageInfo>
      
      <ButtonGroup>
        <Button 
          onClick={() => onPageChange(1)} 
          disabled={currentPage === 1}
        >
          ←←
        </Button>
        <Button 
          onClick={() => onPageChange(currentPage - 1)} 
          disabled={currentPage === 1}
        >
          ←
        </Button>
        
        {pageButtons}
        
        <Button 
          onClick={() => onPageChange(currentPage + 1)} 
          disabled={currentPage === totalPages || totalPages === 0}
        >
          →
        </Button>
        <Button 
          onClick={() => onPageChange(totalPages)} 
          disabled={currentPage === totalPages || totalPages === 0}
        >
          →→
        </Button>
      </ButtonGroup>
      
      {onPerPageChange && (
        <div>
          <select 
            value={itemsPerPage} 
            onChange={(e) => onPerPageChange(Number(e.target.value))}
            style={{ padding: '5px' }}
          >
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      )}
    </PaginationContainer>
  );
};

export default Pagination; 