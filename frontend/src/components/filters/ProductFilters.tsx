import React, { useState } from 'react';
import styled from 'styled-components';
import type { ProductFilters, ProductFilter } from '../../types/product';

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

interface ProductFiltersPanelProps {
  filters: ProductFilters;
  selectedFilters: ProductFilter;
  onFilterChange: (filters: ProductFilter) => void;
}

const ProductFiltersPanel: React.FC<ProductFiltersPanelProps> = ({ filters, selectedFilters, onFilterChange }) => {
  const [priceMin, setPriceMin] = useState<string>(
    selectedFilters.min_price !== undefined ? selectedFilters.min_price.toString() : ''
  );
  const [priceMax, setPriceMax] = useState<string>(
    selectedFilters.max_price !== undefined ? selectedFilters.max_price.toString() : ''
  );

  // Обробник зміни чекбоксів брендів
  const handleBrandChange = (brandId: number, checked: boolean) => {
    let updatedBrands = Array.isArray(selectedFilters.brands) ? [...selectedFilters.brands] : [];
    if (checked) {
      if (!updatedBrands.includes(brandId)) updatedBrands.push(brandId);
    } else {
      updatedBrands = updatedBrands.filter(b => b !== brandId);
    }
    onFilterChange({ ...selectedFilters, brands: updatedBrands.length > 0 ? updatedBrands : undefined });
  };

  // Обробник зміни чекбоксів типів
  const handleTypeChange = (typeId: number, checked: boolean) => {
    let updatedTypes = Array.isArray(selectedFilters.types) ? [...selectedFilters.types] : [];
    if (checked) {
      if (!updatedTypes.includes(typeId)) updatedTypes.push(typeId);
    } else {
      updatedTypes = updatedTypes.filter(t => t !== typeId);
    }
    onFilterChange({ ...selectedFilters, types: updatedTypes.length > 0 ? updatedTypes : undefined });
  };

  // Обробник зміни чекбоксів кольорів
  const handleColorChange = (colorId: number, checked: boolean) => {
    let updatedColors = Array.isArray(selectedFilters.colors) ? [...selectedFilters.colors] : [];
    if (checked) {
      if (!updatedColors.includes(colorId)) updatedColors.push(colorId);
    } else {
      updatedColors = updatedColors.filter(c => c !== colorId);
    }
    onFilterChange({ ...selectedFilters, colors: updatedColors.length > 0 ? updatedColors : undefined });
  };

  // Обробник зміни чекбоксів країн
  const handleCountryChange = (countryId: number, checked: boolean) => {
    let updatedCountries = Array.isArray(selectedFilters.countries) ? [...selectedFilters.countries] : [];
    if (checked) {
      if (!updatedCountries.includes(countryId)) updatedCountries.push(countryId);
    } else {
      updatedCountries = updatedCountries.filter(c => c !== countryId);
    }
    onFilterChange({ ...selectedFilters, countries: updatedCountries.length > 0 ? updatedCountries : undefined });
  };

  // Обробник зміни діапазону цін
  const handlePriceRangeChange = () => {
    const min = priceMin !== '' ? parseFloat(priceMin) : undefined;
    const max = priceMax !== '' ? parseFloat(priceMax) : undefined;
    onFilterChange({ ...selectedFilters, min_price: min, max_price: max });
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
      {filters.brands && filters.brands.length > 0 && (
        <FilterSection>
          <FilterTitle>Brands</FilterTitle>
          {filters.brands.map(brand => (
            <CheckboxLabel key={brand.id}>
              <input
                type="checkbox"
                checked={Array.isArray(selectedFilters.brands) ? selectedFilters.brands.includes(brand.id) : false}
                onChange={(e) => handleBrandChange(brand.id, e.target.checked)}
              />
              {brand.name}
            </CheckboxLabel>
          ))}
        </FilterSection>
      )}
      {filters.types && filters.types.length > 0 && (
        <FilterSection>
          <FilterTitle>Types</FilterTitle>
          {filters.types.map(type => (
            <CheckboxLabel key={type.id}>
              <input
                type="checkbox"
                checked={Array.isArray(selectedFilters.types) ? selectedFilters.types.includes(type.id) : false}
                onChange={(e) => handleTypeChange(type.id, e.target.checked)}
              />
              {type.name}
            </CheckboxLabel>
          ))}
        </FilterSection>
      )}
      {filters.colors && filters.colors.length > 0 && (
        <FilterSection>
          <FilterTitle>Colors</FilterTitle>
          {filters.colors.map(color => (
            <CheckboxLabel key={color.id}>
              <input
                type="checkbox"
                checked={Array.isArray(selectedFilters.colors) ? selectedFilters.colors.includes(color.id) : false}
                onChange={(e) => handleColorChange(color.id, e.target.checked)}
              />
              {color.name}
            </CheckboxLabel>
          ))}
        </FilterSection>
      )}
      {filters.countries && filters.countries.length > 0 && (
        <FilterSection>
          <FilterTitle>Countries</FilterTitle>
          {filters.countries.map(country => (
            <CheckboxLabel key={country.id}>
              <input
                type="checkbox"
                checked={Array.isArray(selectedFilters.countries) ? selectedFilters.countries.includes(country.id) : false}
                onChange={(e) => handleCountryChange(country.id, e.target.checked)}
              />
              {country.name}
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

export default ProductFiltersPanel; 