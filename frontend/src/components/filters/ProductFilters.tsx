import React, { useState, useMemo, useCallback } from 'react';
import { Select } from 'antd';
import type { ProductFilters, ProductFilter, ColorGroup } from '../../types/product';

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
  // Закріплені вибрані значення — завжди видимі, навіть коли пошук їх відфільтрував.
  const selectedItems = useMemo(
    () => selected
      .map(id => items.find(i => i.id === id))
      .filter((x): x is { id: number; name: string } => !!x),
    [selected, items]
  );

  return (
    <div>
      {selectedItems.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {selectedItems.map(it => (
            <span
              key={it.id}
              className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-full text-[11px] font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 border border-blue-200 dark:border-blue-800 max-w-full"
              title={it.name}
            >
              <span className="truncate max-w-[150px]">{it.name}</span>
              <button
                type="button"
                onClick={() => onToggle(it.id, false)}
                className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full hover:bg-blue-200 dark:hover:bg-blue-800 text-blue-500 dark:text-blue-300"
                aria-label={`Прибрати «${it.name}»`}
              >✕</button>
            </span>
          ))}
        </div>
      )}
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

// Offset for subtype-only IDs to avoid collision with type IDs in combined list
const SUBTYPE_OFFSET = 1_000_000;

const ProductFiltersPanel: React.FC<ProductFiltersPanelProps> = ({ filters, selectedFilters, onFilterChange }) => {
  const [priceMin, setPriceMin] = useState<string>(
    selectedFilters.min_price !== undefined ? String(selectedFilters.min_price) : ''
  );
  const [priceMax, setPriceMax] = useState<string>(
    selectedFilters.max_price !== undefined ? String(selectedFilters.max_price) : ''
  );

  // ─── Combined type + subtype list (deduplicated by name) ───
  const typeSubtypeMap = useMemo(() => {
    const byName = new Map<string, { displayName: string; typeId?: number; subtypeId?: number }>();

    for (const t of (filters.types || [])) {
      const key = t.name.toLowerCase();
      const entry = byName.get(key) || { displayName: t.name };
      entry.typeId = t.id;
      byName.set(key, entry);
    }
    for (const s of (filters.subtypes || [])) {
      const key = s.name.toLowerCase();
      const entry = byName.get(key) || { displayName: s.name };
      entry.subtypeId = s.id;
      byName.set(key, entry);
    }

    const items: { id: number; name: string }[] = [];
    const idMap = new Map<number, { typeId?: number; subtypeId?: number }>();

    Array.from(byName.values()).forEach(entry => {
      const displayId = entry.typeId ?? (entry.subtypeId! + SUBTYPE_OFFSET);
      items.push({ id: displayId, name: entry.displayName });
      idMap.set(displayId, { typeId: entry.typeId, subtypeId: entry.subtypeId });
    });

    items.sort((a, b) => a.name.localeCompare(b.name, 'uk'));
    return { items, idMap };
  }, [filters.types, filters.subtypes]);

  const combinedTypeSelection = useMemo(() => {
    const selected: number[] = [];
    Array.from(typeSubtypeMap.idMap.entries()).forEach(([displayId, entry]) => {
      const tSel = entry.typeId != null && (selectedFilters.typeids || []).includes(entry.typeId);
      const sSel = entry.subtypeId != null && (selectedFilters.subtypeids || []).includes(entry.subtypeId);
      if (tSel || sSel) selected.push(displayId);
    });
    return selected;
  }, [selectedFilters.typeids, selectedFilters.subtypeids, typeSubtypeMap]);

  const toggleTypeOrSubtype = useCallback((displayId: number, checked: boolean) => {
    const entry = typeSubtypeMap.idMap.get(displayId);
    if (!entry) return;

    let tids = [...(selectedFilters.typeids || [])];
    let sids = [...(selectedFilters.subtypeids || [])];

    if (entry.typeId != null) {
      tids = checked ? [...tids.filter(x => x !== entry.typeId), entry.typeId!] : tids.filter(x => x !== entry.typeId);
    }
    if (entry.subtypeId != null) {
      sids = checked ? [...sids.filter(x => x !== entry.subtypeId), entry.subtypeId!] : sids.filter(x => x !== entry.subtypeId);
    }

    onFilterChange({
      ...selectedFilters,
      typeids: tids.length > 0 ? tids : undefined,
      subtypeids: sids.length > 0 ? sids : undefined,
    });
  }, [selectedFilters, onFilterChange, typeSubtypeMap]);

  const typeFilterBadge = combinedTypeSelection.length;
  // ────────────────────────────────────────────────────────────

  const toggle = (field: 'brandids' | 'genderids' | 'colorids' | 'conditionids' | 'statusids' | 'color_group_ids') =>
    (id: number, checked: boolean) => {
      const current: number[] = (selectedFilters as any)[field] || [];
      const updated = checked ? [...current.filter(x => x !== id), id] : current.filter(x => x !== id);
      onFilterChange({ ...selectedFilters, [field]: updated.length > 0 ? updated : undefined });
    };

  const toggleColorGroup = useCallback((groupId: number) => {
    const current = selectedFilters.color_group_ids || [];
    const isActive = current.includes(groupId);
    const updated = isActive ? current.filter(x => x !== groupId) : [...current, groupId];
    onFilterChange({
      ...selectedFilters,
      color_group_ids: updated.length > 0 ? updated : undefined,
      // Очищуємо colorids при виборі групи
      colorids: undefined,
    });
  }, [selectedFilters, onFilterChange]);

  const applyPrice = () => {
    onFilterChange({
      ...selectedFilters,
      min_price: priceMin !== '' ? parseFloat(priceMin) : undefined,
      max_price: priceMax !== '' ? parseFloat(priceMax) : undefined,
    });
  };

  const countActive = (field: string) => ((selectedFilters as any)[field] || []).length;

  const totalActive = [
    'brandids','genderids','colorids','color_group_ids','conditionids','statusids',
  ].reduce((acc, f) => acc + countActive(f), 0)
    + typeFilterBadge
    + (selectedFilters.min_price !== undefined || selectedFilters.max_price !== undefined ? 1 : 0)
    + (selectedFilters.sizeeu?.length || 0)
    + (selectedFilters.min_sizeeu !== undefined || selectedFilters.max_sizeeu !== undefined ? 1 : 0)
    + (selectedFilters.size_letter?.length || 0)
    + (selectedFilters.min_measurementscm !== undefined || selectedFilters.max_measurementscm !== undefined ? 1 : 0);

  const euSizes = useMemo(() => {
    const raw = filters.size_ranges?.eu || [];
    return Array.from(new Set(raw)).sort((a, b) => parseFloat(a) - parseFloat(b));
  }, [filters.size_ranges]);

  const letterSizes = useMemo(() => {
    // Backend повертає вже відсортовано (XS,S,M,L,XL,XXL,…); тут просто dedup на всяк
    const raw = (filters as any).size_letters || [];
    return Array.from(new Set(raw)) as string[];
  }, [filters]);

  return (
    <div className="flex flex-col gap-0 text-sm">

      {/* Active filter count badge */}
      {totalActive > 0 && (
        <div className="mb-2 flex items-center gap-2 px-1">
          <span className="text-xs text-gray-500 dark:text-gray-400">Активних фільтрів:</span>
          <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full bg-blue-500 text-white text-[11px] font-bold">{totalActive}</span>
        </div>
      )}

      {/* Тип (об'єднано: види + підвиди, дедупліковано по назві) */}
      {typeSubtypeMap.items.length > 0 && (
        <FilterSection title={SECTION_LABELS.types} badge={typeFilterBadge} defaultOpen>
          <MultiCheckList
            items={typeSubtypeMap.items}
            selected={combinedTypeSelection}
            onToggle={toggleTypeOrSubtype}
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

      {/* Колір — базові групи + пошук відтінків */}
      <FilterSection
        title={SECTION_LABELS.colors}
        badge={(selectedFilters.color_group_ids?.length || 0) + (selectedFilters.colorids?.length || 0)}
        defaultOpen
      >
        {/* Базові кольори — чіпи */}
        {filters.color_groups && filters.color_groups.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-2">
            {filters.color_groups.map((cg: ColorGroup) => {
              const isActive = (selectedFilters.color_group_ids || []).includes(cg.id);
              const isWhite = cg.hex?.toLowerCase() === '#ffffff';
              return (
                <button
                  key={cg.id}
                  type="button"
                  onClick={() => toggleColorGroup(cg.id)}
                  title={`${cg.name} (${cg.count})`}
                  className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium transition-all border ${
                    isActive
                      ? 'ring-2 ring-blue-400 border-blue-400 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300'
                      : 'border-gray-200 dark:border-gray-600 hover:border-gray-400 text-gray-600 dark:text-gray-300 hover:text-gray-800'
                  }`}
                >
                  <span
                    className={`inline-block w-3 h-3 rounded-full flex-shrink-0 ${isWhite ? 'border border-gray-300' : ''}`}
                    style={{ backgroundColor: cg.hex || '#ccc' }}
                  />
                  <span className="truncate max-w-[80px]">{cg.name}</span>
                </button>
              );
            })}
          </div>
        )}

        {/* Пошук конкретного відтінку */}
        {filters.colors?.length > 0 && (
          <div>
            <div className="text-[10px] text-gray-400 mb-1 uppercase tracking-wider">Або конкретний відтінок:</div>
            <Select
              mode="multiple"
              allowClear
              showSearch
              placeholder="Пошук відтінку..."
              value={selectedFilters.colorids || []}
              onChange={(values: number[]) => {
                onFilterChange({
                  ...selectedFilters,
                  colorids: values.length > 0 ? values : undefined,
                  // Очищуємо групи при виборі конкретного відтінку
                  color_group_ids: undefined,
                });
              }}
              options={(() => {
                const baseNames = new Set((filters.color_groups || []).map(cg => cg.name.toLowerCase()));
                const base: Array<{ label: string; value: number }> = [];
                const rest: Array<{ label: string; value: number }> = [];
                (filters.colors || []).forEach(c => {
                  const item = { label: c.name, value: c.id };
                  if (baseNames.has((c.name || '').toLowerCase())) base.push(item);
                  else rest.push(item);
                });
                return [...base, ...rest];
              })()}
              filterOption={(input, option) =>
                (option?.label as string)?.toLowerCase().includes(input.toLowerCase()) ?? false
              }
              maxTagCount={3}
              maxTagPlaceholder={(omitted) => `+${omitted.length}...`}
              size="small"
              className="w-full"
              style={{ fontSize: '12px' }}
            />
          </div>
        )}
      </FilterSection>

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

      {/* Поточний стан */}
      {filters.conditions?.length > 0 && (
        <FilterSection title="Поточний стан" badge={(selectedFilters as any).current_conditionids?.length || 0}>
          <MultiCheckList
            items={filters.conditions}
            selected={(selectedFilters as any).current_conditionids || []}
            onToggle={(id, checked) => {
              const cur = (selectedFilters as any).current_conditionids || [];
              const next = checked ? [...cur.filter((x: number) => x !== id), id] : cur.filter((x: number) => x !== id);
              onFilterChange({ ...selectedFilters, current_conditionids: next.length ? next : undefined } as any);
            }}
            maxVisible={10}
          />
        </FilterSection>
      )}

      {/* Стиль */}
      {filters.styles && filters.styles.length > 0 && (
        <FilterSection title="Стиль" badge={(selectedFilters as any).styleids?.length || 0}>
          <MultiCheckList
            items={filters.styles}
            selected={(selectedFilters as any).styleids || []}
            onToggle={(id, checked) => {
              const cur = (selectedFilters as any).styleids || [];
              const next = checked ? [...cur.filter((x: number) => x !== id), id] : cur.filter((x: number) => x !== id);
              onFilterChange({ ...selectedFilters, styleids: next.length ? next : undefined } as any);
            }}
            maxVisible={15}
          />
        </FilterSection>
      )}

      {/* Сезон */}
      {filters.seasons && filters.seasons.length > 0 && (
        <FilterSection title="Сезон" badge={(selectedFilters as any).seasons?.length || 0}>
          <MultiCheckList
            items={filters.seasons.map((s, i) => ({ id: i, name: s }))}
            selected={(((selectedFilters as any).seasons || []) as string[]).map(s => filters.seasons!.indexOf(s)).filter(i => i >= 0)}
            onToggle={(idx, checked) => {
              const all = (selectedFilters as any).seasons || [];
              const value = filters.seasons![idx];
              const next = checked ? Array.from(new Set([...all, value])) : all.filter((s: string) => s !== value);
              onFilterChange({ ...selectedFilters, seasons: next.length ? next : undefined } as any);
            }}
            maxVisible={10}
          />
        </FilterSection>
      )}

      {/* Ширина */}
      {filters.widths && filters.widths.length > 0 && (
        <FilterSection title="Ширина" badge={(selectedFilters as any).widths?.length || 0}>
          <MultiCheckList
            items={filters.widths.map((w, i) => ({ id: i, name: w }))}
            selected={(((selectedFilters as any).widths || []) as string[]).map(w => filters.widths!.indexOf(w)).filter(i => i >= 0)}
            onToggle={(idx, checked) => {
              const all = (selectedFilters as any).widths || [];
              const value = filters.widths![idx];
              const next = checked ? Array.from(new Set([...all, value])) : all.filter((w: string) => w !== value);
              onFilterChange({ ...selectedFilters, widths: next.length ? next : undefined } as any);
            }}
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
        <FilterSection
          title="Розмір (EU)"
          badge={(selectedFilters.sizeeu?.length || 0) + (selectedFilters.min_sizeeu !== undefined || selectedFilters.max_sizeeu !== undefined ? 1 : 0)}
        >
          <div className="space-y-2">
            {/* Діапазон розмірів */}
            <div>
              <div className="text-[10px] text-gray-400 mb-1 uppercase tracking-wider">Діапазон:</div>
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  step="0.5"
                  min="10"
                  max="60"
                  placeholder="Від"
                  value={selectedFilters.min_sizeeu ?? ''}
                  onChange={e => {
                    const v = e.target.value ? parseFloat(e.target.value) : undefined;
                    onFilterChange({ ...selectedFilters, min_sizeeu: v, sizeeu: undefined });
                  }}
                  className="w-full border border-gray-200 dark:border-gray-600 rounded px-2 py-1 text-xs bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:border-blue-400"
                />
                <span className="text-gray-400 text-xs flex-shrink-0">—</span>
                <input
                  type="number"
                  step="0.5"
                  min="10"
                  max="60"
                  placeholder="До"
                  value={selectedFilters.max_sizeeu ?? ''}
                  onChange={e => {
                    const v = e.target.value ? parseFloat(e.target.value) : undefined;
                    onFilterChange({ ...selectedFilters, max_sizeeu: v, sizeeu: undefined });
                  }}
                  className="w-full border border-gray-200 dark:border-gray-600 rounded px-2 py-1 text-xs bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:border-blue-400"
                />
                {(selectedFilters.min_sizeeu !== undefined || selectedFilters.max_sizeeu !== undefined) && (
                  <button
                    type="button"
                    onClick={() => onFilterChange({ ...selectedFilters, min_sizeeu: undefined, max_sizeeu: undefined })}
                    className="flex-shrink-0 text-gray-400 hover:text-red-500 transition-colors text-sm leading-none"
                    title="Скинути діапазон"
                  >×</button>
                )}
              </div>
            </div>

            {/* Або конкретні розміри */}
            {(selectedFilters.min_sizeeu === undefined && selectedFilters.max_sizeeu === undefined) && (
              <div>
                <div className="text-[10px] text-gray-400 mb-1 uppercase tracking-wider">Або конкретні розміри:</div>
                <Select
                  mode="multiple"
                  allowClear
                  showSearch
                  placeholder="Оберіть розміри..."
                  value={selectedFilters.sizeeu || []}
                  onChange={(values: string[]) => {
                    onFilterChange({ ...selectedFilters, sizeeu: values.length > 0 ? values : undefined });
                  }}
                  options={euSizes.map(size => ({ label: size, value: size }))}
                  style={{ width: '100%' }}
                  maxTagCount={4}
                  maxTagPlaceholder={(omitted) => `+${omitted.length}...`}
                  size="small"
                  filterOption={(input, option) =>
                    (option?.label as string)?.toLowerCase().includes(input.toLowerCase()) ?? false
                  }
                />
                {selectedFilters.sizeeu && selectedFilters.sizeeu.length > 0 && (
                  <button
                    type="button"
                    onClick={() => onFilterChange({ ...selectedFilters, sizeeu: undefined })}
                    className="mt-1 w-full py-0.5 text-[10px] text-gray-400 hover:text-red-500 hover:underline transition-colors"
                  >
                    Очистити
                  </button>
                )}
              </div>
            )}
          </div>
        </FilterSection>
      )}

      {/* Буквений розмір (XS / S / M / L / XL / XXL / ...) */}
      {letterSizes.length > 0 && (
        <FilterSection
          title="Буквений розмір"
          badge={selectedFilters.size_letter?.length || 0}
        >
          <div className="flex flex-wrap gap-1.5">
            {letterSizes.map(letter => {
              const isActive = selectedFilters.size_letter?.includes(letter) || false;
              return (
                <button
                  key={letter}
                  type="button"
                  onClick={() => {
                    const cur = selectedFilters.size_letter || [];
                    const next = isActive
                      ? cur.filter(x => x !== letter)
                      : [...cur, letter];
                    onFilterChange({
                      ...selectedFilters,
                      size_letter: next.length > 0 ? next : undefined,
                    });
                  }}
                  className={[
                    "min-w-[42px] px-2.5 py-1 text-xs rounded border transition-colors",
                    isActive
                      ? "bg-blue-500 text-white border-blue-500 dark:bg-blue-600 dark:border-blue-600"
                      : "bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 border-gray-200 dark:border-gray-600 hover:border-blue-400",
                  ].join(" ")}
                >
                  {letter}
                </button>
              );
            })}
            {selectedFilters.size_letter && selectedFilters.size_letter.length > 0 && (
              <button
                type="button"
                onClick={() => onFilterChange({ ...selectedFilters, size_letter: undefined })}
                className="ml-1 text-[10px] text-gray-400 hover:text-red-500 hover:underline transition-colors"
              >
                Очистити
              </button>
            )}
          </div>
        </FilterSection>
      )}

      {/* СМ (довжина стопи) */}
      <FilterSection
        title="Розмір в СМ"
        badge={selectedFilters.min_measurementscm !== undefined || selectedFilters.max_measurementscm !== undefined ? 1 : 0}
      >
        <div className="flex items-center gap-1.5">
          <input
            type="number"
            step="0.5"
            min="10"
            max="40"
            placeholder="Від"
            value={selectedFilters.min_measurementscm ?? ''}
            onChange={e => {
              const v = e.target.value ? parseFloat(e.target.value) : undefined;
              onFilterChange({ ...selectedFilters, min_measurementscm: v });
            }}
            className="w-full border border-gray-200 dark:border-gray-600 rounded px-2 py-1 text-xs bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:border-blue-400"
          />
          <span className="text-gray-400 text-xs flex-shrink-0">—</span>
          <input
            type="number"
            step="0.5"
            min="10"
            max="40"
            placeholder="До"
            value={selectedFilters.max_measurementscm ?? ''}
            onChange={e => {
              const v = e.target.value ? parseFloat(e.target.value) : undefined;
              onFilterChange({ ...selectedFilters, max_measurementscm: v });
            }}
            className="w-full border border-gray-200 dark:border-gray-600 rounded px-2 py-1 text-xs bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:border-blue-400"
          />
          {(selectedFilters.min_measurementscm !== undefined || selectedFilters.max_measurementscm !== undefined) && (
            <button
              type="button"
              onClick={() => onFilterChange({ ...selectedFilters, min_measurementscm: undefined, max_measurementscm: undefined })}
              className="flex-shrink-0 text-gray-400 hover:text-red-500 transition-colors text-sm leading-none"
              title="Скинути"
            >×</button>
          )}
        </div>
        <p className="text-[10px] text-gray-400 mt-1">довжина стопи/виробу в см</p>
      </FilterSection>

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