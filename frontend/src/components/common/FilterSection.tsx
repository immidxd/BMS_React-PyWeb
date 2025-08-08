import React from 'react';
import styled from 'styled-components';

const FilterContainer = styled.div`
  margin-bottom: 15px;
`;

const FilterLabel = styled.label`
  display: block;
  margin-bottom: 8px;
  font-weight: 500;
`;

const CheckboxContainer = styled.div`
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 200px;
  overflow-y: auto;
`;

const CheckboxItem = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
`;

const SearchInput = styled.input`
  width: 100%;
  padding: 8px;
  margin-bottom: 10px;
  border: 1px solid #ddd;
  border-radius: 4px;
`;

interface FilterItem {
  id: string;
  label: string;
}

interface FilterSectionProps {
  title: string;
  items: FilterItem[];
  selectedItems: string[];
  onChange: (selected: string[]) => void;
  searchable?: boolean;
}

const FilterSection: React.FC<FilterSectionProps> = ({
  title,
  items,
  selectedItems,
  onChange,
  searchable = false
}) => {
  const [searchTerm, setSearchTerm] = React.useState('');
  
  const filteredItems = searchable && searchTerm
    ? items.filter(item => item.label.toLowerCase().includes(searchTerm.toLowerCase()))
    : items;
  
  const handleCheckboxChange = (itemId: string) => {
    if (selectedItems.includes(itemId)) {
      onChange(selectedItems.filter(id => id !== itemId));
    } else {
      onChange([...selectedItems, itemId]);
    }
  };
  
  return (
    <FilterContainer>
      <FilterLabel>{title}</FilterLabel>
      
      {searchable && (
        <SearchInput
          type="text"
          placeholder="Пошук..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      )}
      
      <CheckboxContainer>
        {filteredItems.map(item => (
          <CheckboxItem key={item.id}>
            <input
              type="checkbox"
              id={`filter-${title}-${item.id}`}
              checked={selectedItems.includes(item.id)}
              onChange={() => handleCheckboxChange(item.id)}
            />
            <label htmlFor={`filter-${title}-${item.id}`}>{item.label}</label>
          </CheckboxItem>
        ))}
        
        {filteredItems.length === 0 && (
          <div>Немає доступних елементів</div>
        )}
      </CheckboxContainer>
    </FilterContainer>
  );
};

export default FilterSection; 