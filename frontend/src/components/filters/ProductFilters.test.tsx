import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import ProductFiltersPanel from './ProductFilters';
import type { ProductFilters } from '../../types/product';


const filters: ProductFilters = {
  brands: [],
  types: [],
  subtypes: [],
  colors: [],
  countries: [],
  statuses: [],
  conditions: [],
  genders: [],
  shipments: [],
  price_range: { min_price: 0, max_price: 0 },
  size_ranges: { eu: [], ua: [], usa: [], uk: [], jp: [], cn: [] },
};


describe('ProductFilters publication platforms', () => {
  it('shows the official Viber icon and enables the positive filter', () => {
    const onFilterChange = jest.fn();
    render(
      <ProductFiltersPanel
        filters={filters}
        selectedFilters={{}}
        onFilterChange={onFilterChange}
      />,
    );

    const viber = screen.getByRole('button', { name: 'Viber' });
    expect(viber.querySelector('img')?.getAttribute('src')).toBe('/media-logos/viber-logo.png');
    fireEvent.click(viber);

    expect(onFilterChange).toHaveBeenCalledWith(expect.objectContaining({
      published_on: ['viber'],
      published_on_not: undefined,
    }));
  });

  it('supports the same right-click exclusion as the other platforms', () => {
    const onFilterChange = jest.fn();
    render(
      <ProductFiltersPanel
        filters={filters}
        selectedFilters={{}}
        onFilterChange={onFilterChange}
      />,
    );

    fireEvent.contextMenu(screen.getByRole('button', { name: 'Viber' }));

    expect(onFilterChange).toHaveBeenCalledWith(expect.objectContaining({
      published_on: undefined,
      published_on_not: ['viber'],
    }));
  });
});
