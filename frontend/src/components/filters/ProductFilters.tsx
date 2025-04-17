import React, { useState } from 'react';
import styled from 'styled-components';
import { ProductFiltersOptions } from '../../services/productService';
import type { ProductFilters } from '../../services/productService';

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

const RangeContainer = styled.div`
  display: flex;
  flex-direction: column;
  gap: 10px;
`;

const RangeInputs = styled.div`
  display: flex;
  gap: 10px;
  align-items: center;
`;

const RangeInput = styled.input`
  width: 100px;
  padding: 5px;
  border: 1px solid #ddd;
  border-radius: 4px;
`;

interface ProductFiltersProps {
  filters: ProductFiltersOptions;
  selectedFilters: ProductFilters;
  onFilterChange: (filters: ProductFilters) => void;
}

const ProductFilters: React.FC<ProductFiltersProps> = ({ filters, selectedFilters, onFilterChange }) => {
  const [priceMin, setPriceMin] = useState<string>(
    selectedFilters.price_min !== undefined ? selectedFilters.price_min.toString() : ''
  );
  const [priceMax, setPriceMax] = useState<string>(
    selectedFilters.price_max !== undefined ? selectedFilters.price_max.toString() : ''
  );

  // Обробник зміни чекбоксів брендів
  const handleBrandChange = (brand: string, checked: boolean) => {
    const updatedBrands = [...(selectedFilters.brands || [])];
    
    if (checked) {
      if (!updatedBrands.includes(brand)) {
        updatedBrands.push(brand);
      }
    } else {
      const index = updatedBrands.indexOf(brand);
      if (index !== -1) {
        updatedBrands.splice(index, 1);
      }
    }
    
    onFilterChange({ ...selectedFilters, brands: updatedBrands.length > 0 ? updatedBrands : undefined });
  };

  // Обробник зміни чекбоксів типів
  const handleTypeChange = (type: string, checked: boolean) => {
    const updatedTypes = [...(selectedFilters.types || [])];
    
    if (checked) {
      if (!updatedTypes.includes(type)) {
        updatedTypes.push(type);
      }
    } else {
      const index = updatedTypes.indexOf(type);
      if (index !== -1) {
        updatedTypes.splice(index, 1);
      }
    }
    
    onFilterChange({ ...selectedFilters, types: updatedTypes.length > 0 ? updatedTypes : undefined });
  };

  // Обробник зміни чекбоксів кольорів
  const handleColorChange = (color: string, checked: boolean) => {
    const updatedColors = [...(selectedFilters.colors || [])];
    
    if (checked) {
      if (!updatedColors.includes(color)) {
        updatedColors.push(color);
      }
    } else {
      const index = updatedColors.indexOf(color);
      if (index !== -1) {
        updatedColors.splice(index, 1);
      }
    }
    
    onFilterChange({ ...selectedFilters, colors: updatedColors.length > 0 ? updatedColors : undefined });
  };

  // Обробник зміни чекбоксів країн
  const handleCountryChange = (country: string, checked: boolean) => {
    const updatedCountries = [...(selectedFilters.countries || [])];
    
    if (checked) {
      if (!updatedCountries.includes(country)) {
        updatedCountries.push(country);
      }
    } else {
      const index = updatedCountries.indexOf(country);
      if (index !== -1) {
        updatedCountries.splice(index, 1);
      }
    }
    
    onFilterChange({ ...selectedFilters, countries: updatedCountries.length > 0 ? updatedCountries : undefined });
  };

  // Обробник зміни діапазону цін
  const handlePriceRangeChange = () => {
    const min = priceMin !== '' ? parseFloat(priceMin) : undefined;
    const max = priceMax !== '' ? parseFloat(priceMax) : undefined;
    
    onFilterChange({
      ...selectedFilters,
      price_min: min,
      price_max: max
    });
  };

  // Обробник зміни чекбоксу "Тільки непродані"
  const handleUnsoldChange = (checked: boolean) => {
    onFilterChange({ ...selectedFilters, only_unsold: checked });
  };

  // Обробник зміни чекбоксу "Тільки видимі"
  const handleVisibleChange = (checked: boolean) => {
    onFilterChange({ ...selectedFilters, visible_only: checked });
  };

  return (
    <FiltersContainer>
      <FiltersTitle>Product Filters</FiltersTitle>
      
      {filters.brands.length > 0 && (
        <FilterSection>
          <FilterTitle>Brands</FilterTitle>
          {filters.brands.map(brand => (
            <CheckboxLabel key={brand}>
              <input 
                type="checkbox" 
                checked={selectedFilters.brands?.includes(brand) || false}
                onChange={(e) => handleBrandChange(brand, e.target.checked)}
              /> 
              {brand}
            </CheckboxLabel>
          ))}
        </FilterSection>
      )}
      
      {filters.types.length > 0 && (
        <FilterSection>
          <FilterTitle>Types</FilterTitle>
          {filters.types.map(type => (
            <CheckboxLabel key={type}>
              <input 
                type="checkbox" 
                checked={selectedFilters.types?.includes(type) || false}
                onChange={(e) => handleTypeChange(type, e.target.checked)}
              /> 
              {type}
            </CheckboxLabel>
          ))}
        </FilterSection>
      )}
      
      {filters.colors.length > 0 && (
        <FilterSection>
          <FilterTitle>Colors</FilterTitle>
          {filters.colors.map(color => (
            <CheckboxLabel key={color}>
              <input 
                type="checkbox" 
                checked={selectedFilters.colors?.includes(color) || false}
                onChange={(e) => handleColorChange(color, e.target.checked)}
              /> 
              {color}
            </CheckboxLabel>
          ))}
        </FilterSection>
      )}
      
      {filters.countries.length > 0 && (
        <FilterSection>
          <FilterTitle>Countries</FilterTitle>
          {filters.countries.map(country => (
            <CheckboxLabel key={country}>
              <input 
                type="checkbox" 
                checked={selectedFilters.countries?.includes(country) || false}
                onChange={(e) => handleCountryChange(country, e.target.checked)}
              /> 
              {country}
            </CheckboxLabel>
          ))}
        </FilterSection>
      )}
      
      <FilterSection>
        <FilterTitle>Price Range</FilterTitle>
        <RangeContainer>
          <RangeInputs>
            <RangeInput
              type="number"
              value={priceMin}
              onChange={(e) => setPriceMin(e.target.value)}
              placeholder="Min"
            />
            <RangeInput
              type="number"
              value={priceMax}
              onChange={(e) => setPriceMax(e.target.value)}
              placeholder="Max"
            />
          </RangeInputs>
          <button onClick={handlePriceRangeChange}>Apply</button>
        </RangeContainer>
      </FilterSection>
      
      <FilterSection>
        <CheckboxContainer>
          <input
            type="checkbox"
            checked={selectedFilters.only_unsold || false}
            onChange={(e) => handleUnsoldChange(e.target.checked)}
          />
          <span>Only Unsold</span>
        </CheckboxContainer>
        
        <CheckboxContainer>
          <input
            type="checkbox"
            checked={selectedFilters.visible_only || false}
            onChange={(e) => handleVisibleChange(e.target.checked)}
          />
          <span>Visible Only</span>
        </CheckboxContainer>
      </FilterSection>
    </FiltersContainer>
  );
};

export default ProductFilters; 