import React, { useState, useMemo } from 'react';
import type { ProductFilters, ProductFilter } from '../../types/product';

interface ProductFiltersPanelProps {
  filters: ProductFilters;
  selectedFilters: ProductFilter;
  onFilterChange: (filters: ProductFilter) => void;
}

type SectionKey = 'types' | 'brands' | 'genders' | 'colors' | 'conditions' | 'statuses' | 'price';

const SECTION_LABELS: Record<SectionKey, string> = {
  types: 'Тип товару',
  brands: 'Бренд',
  genders: 'Стать',
  colors: 'Колір',
  conditions: 'Стан',
  statuses: 'Статус',
  price: 'Діапазон цін',
};

function SearchInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <div className="relative mb-2">
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder || 'Пошук...'}
        className="w-full pl-7 pr-2 py-1 text-xs border border-gray-200 dark:border-gray-600 rounded bg-gray-50 dark:bg-gray-700 text-gray-700 dark:text-gray-200 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
      />
      <svg className="absolute left-2 top-1.5 w-3.5 h-3.5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
      </svg>
    </div>
  );
}

function FilterSection({
  title, badge, children, defaultOpen = false,
}: { title: string; badge?: number; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-gray-100 dark:border-gray-700 pb-0">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between py-2.5 text-sm font-semibold text-gray-700 dark:text-gray-200 hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
      >
        <span>{title}</span>
        <span className="flex items-center gap-1.5">
          {badge && badge > 0 ? (
            <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-blue-500 text-white text-[10px] font-bold">{badge}</span>
          ) : null}
          <svg className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </span>
      </button>
      {open && <div className="pb-3">{children}</div>}
    </div>
  );
}

function MultiCheckList({
  items, selected, onToggle, maxVisible = 7,
}: {
  items: { id: number; name: string }[];
  selected: number[];
  onToggle: (id: number, checked: boolean) => void;
  maxVisible?: number;
}) {
  const [search, setSearch] = useState('');
  const [showAll, setShowAll] = useState(false);
  const filtered = useMemo(
    () => items.filter(i => i.name.toLowerCase().includes(search.toLowerCase())),
    [items, search]
  );
  const visible = showAll ? filtered : filtered.slice(0, maxVisible);

  return (
    <div>
      {items.length > 6 && <SearchInput value={search} onChange={setSearch} />}
      <div className="space-y-0.5 max-h-48 overflow-y-auto pr-1">
        {visible.map(item => (
          <label key={item.id} className="flex items-center gap-2 py-0.5 cursor-pointer group">
            <input
              type="checkbox"
              className="w-3.5 h-3.5 rounded border-gray-300 text-blue-500 focus:ring-blue-400 cursor-pointer"
              checked={selected.includes(item.id)}
              onChange={e => onToggle(item.id, e.target.checked)}
            />
            <span className={`text-xs truncate max-w-[180px] group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors ${
              selected.includes(item.id) ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-gray-600 dark:text-gray-300'
            }`}>{item.name}</span>
          </label>
        ))}
        {filtered.length === 0 && (
          <p className="text-xs text-gray-400 py-2 text-center">Нічого не знайдено</p>
        )}
      </div>
      {filtered.length > maxVisible && (
        <button
          type="button"
          onClick={() => setShowAll(s => !s)}
          className="mt-1.5 text-xs text-blue-500 hover:underline"
        >
          {showAll ? 'Згорнути' : `Ще ${filtered.length - maxVisible}...`}
        </button>
      )}
    </div>
  );
}

const ProductFiltersPanel: React.FC<ProductFiltersPanelProps> = ({ filters, selectedFilters, onFilterChange }) => {
  const [priceMin, setPriceMin] = useState<string>(
    selectedFilters.min_price !== undefined ? String(selectedFilters.min_price) : ''
  );
  const [priceMax, setPriceMax] = useState<string>(
    selectedFilters.max_price !== undefined ? String(selectedFilters.max_price) : ''
  );

  const toggle = (field: 'typeids' | 'brandids' | 'genderids' | 'colorids' | 'conditionids' | 'statusids') =>
    (id: number, checked: boolean) => {
      const current: number[] = (selectedFilters as any)[field] || [];
      const updated = checked ? [...current.filter(x => x !== id), id] : current.filter(x => x !== id);
      onFilterChange({ ...selectedFilters, [field]: updated.length > 0 ? updated : undefined });
    };

  const applyPrice = () => {
    onFilterChange({
      ...selectedFilters,
      min_price: priceMin !== '' ? parseFloat(priceMin) : undefined,
      max_price: priceMax !== '' ? parseFloat(priceMax) : undefined,
    });
  };

  const countActive = (field: string) => ((selectedFilters as any)[field] || []).length;

  const totalActive = [
    'typeids','brandids','genderids','colorids','conditionids','statusids',
  ].reduce((acc, f) => acc + countActive(f), 0)
    + (selectedFilters.min_price !== undefined || selectedFilters.max_price !== undefined ? 1 : 0)
    + (selectedFilters.sizeeu?.length || 0);

  const euSizes = useMemo(() => {
    const raw = filters.size_ranges?.eu || [];
    return Array.from(new Set(raw)).sort((a, b) => parseFloat(a) - parseFloat(b));
  }, [filters.size_ranges]);

  return (
    <div className="flex flex-col gap-0 text-sm">

      {/* Active filter count badge */}
      {totalActive > 0 && (
        <div className="mb-2 flex items-center gap-2 px-1">
          <span className="text-xs text-gray-500 dark:text-gray-400">Активних фільтрів:</span>
          <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full bg-blue-500 text-white text-[11px] font-bold">{totalActive}</span>
        </div>
      )}

      {/* Тип */}
      {filters.types?.length > 0 && (
        <FilterSection title={SECTION_LABELS.types} badge={countActive('typeids')} defaultOpen>
          <MultiCheckList
            items={filters.types}
            selected={(selectedFilters as any).typeids || []}
            onToggle={toggle('typeids')}
          />
        </FilterSection>
      )}

      {/* Бренд */}
      {filters.brands?.length > 0 && (
        <FilterSection title={SECTION_LABELS.brands} badge={countActive('brandids')} defaultOpen>
          <MultiCheckList
            items={filters.brands}
            selected={(selectedFilters as any).brandids || []}
            onToggle={toggle('brandids')}
          />
        </FilterSection>
      )}

      {/* Стать */}
      {filters.genders?.length > 0 && (
        <FilterSection title={SECTION_LABELS.genders} badge={countActive('genderids')} defaultOpen>
          <MultiCheckList
            items={filters.genders}
            selected={(selectedFilters as any).genderids || []}
            onToggle={toggle('genderids')}
            maxVisible={10}
          />
        </FilterSection>
      )}

      {/* Колір */}
      {filters.colors?.length > 0 && (
        <FilterSection title={SECTION_LABELS.colors} badge={countActive('colorids')}>
          <MultiCheckList
            items={filters.colors}
            selected={(selectedFilters as any).colorids || []}
            onToggle={toggle('colorids')}
          />
        </FilterSection>
      )}

      {/* Стан */}
      {filters.conditions?.length > 0 && (
        <FilterSection title={SECTION_LABELS.conditions} badge={countActive('conditionids')} defaultOpen>
          <MultiCheckList
            items={filters.conditions}
            selected={(selectedFilters as any).conditionids || []}
            onToggle={toggle('conditionids')}
            maxVisible={10}
          />
        </FilterSection>
      )}

      {/* Статус */}
      {filters.statuses?.length > 0 && (
        <FilterSection title={SECTION_LABELS.statuses} badge={countActive('statusids')}>
          <MultiCheckList
            items={filters.statuses}
            selected={(selectedFilters as any).statusids || []}
            onToggle={toggle('statusids')}
            maxVisible={10}
          />
        </FilterSection>
      )}

      {/* Розмір EU */}
      {euSizes.length > 0 && (
        <FilterSection title="Розмір (EU)" badge={selectedFilters.sizeeu?.length || 0}>
          <div className="flex flex-wrap gap-1">
            {euSizes.map(size => {
              const selected = (selectedFilters.sizeeu || []).includes(size);
              return (
                <button
                  key={size}
                  type="button"
                  onClick={() => {
                    const current = selectedFilters.sizeeu || [];
                    const updated = selected ? current.filter(s => s !== size) : [...current, size];
                    onFilterChange({ ...selectedFilters, sizeeu: updated.length > 0 ? updated : undefined });
                  }}
                  className={`px-2 py-0.5 rounded text-xs border transition-colors ${
                    selected
                      ? 'bg-blue-500 border-blue-500 text-white font-semibold'
                      : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:border-blue-400 hover:text-blue-600'
                  }`}
                >
                  {size}
                </button>
              );
            })}
          </div>
        </FilterSection>
      )}

      {/* Ціна */}
      <FilterSection
        title={SECTION_LABELS.price}
        badge={selectedFilters.min_price !== undefined || selectedFilters.max_price !== undefined ? 1 : 0}
        defaultOpen
      >
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <label className="text-[10px] text-gray-400 uppercase tracking-wide">Від (₴)</label>
              <input
                type="number"
                value={priceMin}
                onChange={e => setPriceMin(e.target.value)}
                onBlur={applyPrice}
                onKeyDown={e => e.key === 'Enter' && applyPrice()}
                placeholder={filters.price_range?.min_price !== undefined ? String(Math.floor(filters.price_range.min_price)) : '0'}
                className="w-full mt-0.5 px-2 py-1 text-xs border border-gray-200 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
            </div>
            <span className="text-gray-400 mt-4">—</span>
            <div className="flex-1">
              <label className="text-[10px] text-gray-400 uppercase tracking-wide">До (₴)</label>
              <input
                type="number"
                value={priceMax}
                onChange={e => setPriceMax(e.target.value)}
                onBlur={applyPrice}
                onKeyDown={e => e.key === 'Enter' && applyPrice()}
                placeholder={filters.price_range?.max_price !== undefined ? String(Math.ceil(filters.price_range.max_price)) : '∞'}
                className="w-full mt-0.5 px-2 py-1 text-xs border border-gray-200 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
            </div>
          </div>
          <button
            type="button"
            onClick={applyPrice}
            className="w-full py-1 text-xs font-medium rounded bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-700 hover:bg-blue-100 transition-colors"
          >
            Застосувати ціну
          </button>
          {(selectedFilters.min_price !== undefined || selectedFilters.max_price !== undefined) && (
            <button
              type="button"
              onClick={() => { setPriceMin(''); setPriceMax(''); onFilterChange({ ...selectedFilters, min_price: undefined, max_price: undefined }); }}
              className="w-full py-0.5 text-xs text-gray-400 hover:text-red-500 transition-colors"
            >
              ✕ Скинути ціну
            </button>
          )}
        </div>
      </FilterSection>

    </div>
  );
};

export default ProductFiltersPanel;