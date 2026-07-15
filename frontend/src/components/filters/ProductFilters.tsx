import React, { useState, useMemo, useCallback } from 'react';
import { Select } from 'antd';
import type { ProductFilters, ProductFilter, ColorGroup } from '../../types/product';

interface ProductFiltersPanelProps {
  filters: ProductFilters;
  selectedFilters: ProductFilter;
  onFilterChange: (filters: ProductFilter) => void;
  // Динамічний фасет: EU-розміри, наявні в поточному відфільтрованому наборі.
  // Якщо передано — сітка розмірів адаптується під поточний стан; інакше
  // (undefined/null) береться глобальний список з filters.size_ranges.eu.
  availableEuSizes?: string[] | null;
  // Динамічний фасет кольорів: кольорові групи (id+count), наявні зараз.
  availableColorGroups?: { id: number; count: number }[] | null;
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

// Класичні символи статі (Venus ♀ / Mars ♂ / поєднаний ⚥) — лаконічні,
// інтуїтивно зрозумілі, без тексту (назва в tooltip/aria-label).
function GenderGlyph({ kind }: { kind: 'female' | 'male' | 'unisex' }) {
  const common = {
    viewBox: '0 0 24 24', width: 20, height: 20, fill: 'none',
    stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const, 'aria-hidden': true, focusable: 'false' as const,
  };
  if (kind === 'female') {
    return (
      <svg {...common}>
        <circle cx="12" cy="8" r="5" />
        <line x1="12" y1="13" x2="12" y2="21" />
        <line x1="9" y1="18" x2="15" y2="18" />
      </svg>
    );
  }
  if (kind === 'male') {
    return (
      <svg {...common}>
        <circle cx="10" cy="14" r="5" />
        <line x1="13.8" y1="10.2" x2="19" y2="5" />
        <polyline points="14.5 5 19 5 19 9.5" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <circle cx="11" cy="13" r="4" />
      <line x1="11" y1="17" x2="11" y2="21.5" />
      <line x1="8.5" y1="19.2" x2="13.5" y2="19.2" />
      <line x1="13.9" y1="10.1" x2="18" y2="6" />
      <polyline points="14.7 6 18 6 18 9.3" />
    </svg>
  );
}

function genderKind(name: string): 'female' | 'male' | 'unisex' {
  const n = (name || '').toLowerCase();
  if (n.startsWith('жін')) return 'female';
  if (n.startsWith('чол')) return 'male';
  return 'unisex';
}

// Активний чіп статі — легкий тематичний відтінок: рожевий (жін), синій (чол),
// сірий (унісекс). bg-blue-500/600/700 НЕ використовуємо (App.css перефарбовує
// їх у чорний); тут лише bg-blue-50/border-blue-500/ring — вони безпечні.
const GENDER_ACTIVE_TINT: Record<'female' | 'male' | 'unisex', string> = {
  female: 'bg-pink-50 dark:bg-pink-900/30 text-pink-600 dark:text-pink-300 border-pink-400 ring-1 ring-pink-300',
  // ⚠️ Arbitrary hex, НЕ blue-* — App.css перефарбовує іменовані blue-класи
  // (bg-blue-50/text-blue-600/dark:bg-blue-900/30) у брендовий чорний → синій
  // «гас» у сірий. Hex обходить це. Див. feedback_tailwind_blue_override.
  male:   'bg-[#EFF6FF] dark:bg-[#1E3A8A4D] text-[#2563EB] dark:text-[#93C5FD] border-[#60A5FA] ring-1 ring-[#93C5FD]',
  unisex: 'bg-gray-100 dark:bg-gray-600/40 text-gray-700 dark:text-gray-200 border-gray-400 ring-1 ring-gray-300',
};

// Майданчики публікації — фільтр «де опубліковано». Чіпи = ІКОНКИ (без тексту),
// стиль як у статі/кольору. Розширюваний: додати Instagram = +1 рядок тут +
// файл іконки в /media-logos/ + (бек) гілка в published_on. key збігається з
// бек-значеннями ('telegram'|'olx'). icon — файл у public/media-logos/.
// «Каталог» не має файлу-лого (це наш публічний інтернет-каталог / TG Mini App) —
// малюємо той самий storefront-гліф, що маркер «У каталозі» в таблиці (emerald),
// щоб фільтр і рядок читались як одне. svg має пріоритет над icon.
const CatalogFilterGlyph: React.FC = () => (
  <svg viewBox="0 0 24 24" width={18} height={18} fill="currentColor" aria-hidden focusable="false">
    <path d="M21.9 8.89l-1.05-4.37c-.22-.9-1-1.52-1.91-1.52H5.05c-.9 0-1.69.63-1.9 1.52L2.1 8.89c-.24 1.02-.02 2.06.62 2.88.08.11.19.19.28.29V19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2v-6.94c.09-.09.2-.18.28-.28.64-.82.87-1.87.62-2.89zm-2.99-3.9l1.05 4.37c.1.42.01.84-.25 1.17-.14.18-.44.47-.94.47-.61 0-1.14-.49-1.21-1.14L16.98 5l1.93-.01zM13 5h1.96l.54 4.52c.05.39-.07.78-.33 1.07-.22.26-.54.41-.95.41-.67 0-1.22-.59-1.22-1.31V5zM8.49 9.52L9.04 5H11v4.69c0 .72-.55 1.31-1.29 1.31-.34 0-.65-.15-.89-.41-.25-.29-.37-.68-.33-1.07zm-4.45-.16L5.05 5h1.97l-.58 4.86c-.08.65-.6 1.14-1.21 1.14-.49 0-.8-.29-.93-.47-.27-.32-.36-.75-.25-1.17zM5 19v-6.03c.08.01.15.03.23.03.87 0 1.66-.36 2.24-.95.6.6 1.4.95 2.31.95.87 0 1.65-.36 2.23-.93.59.57 1.39.93 2.29.93.84 0 1.64-.35 2.24-.95.58.59 1.37.95 2.24.95.08 0 .15-.02.23-.03V19H5z"/>
  </svg>
);

type PublicationPlatform = { key: string; label: string; icon?: string; svg?: React.ReactNode };
const PUBLICATION_PLATFORMS: PublicationPlatform[] = [
  { key: 'telegram', label: 'Telegram', icon: '/media-logos/telegram-logo.png' },
  { key: 'olx',      label: 'OLX',      icon: '/media-logos/olx-mark-emerald.png' },
  { key: 'prom',     label: 'Prom',     icon: '/media-logos/prom-logo.png' },
  { key: 'catalog',  label: 'Каталог',  svg: <CatalogFilterGlyph /> },
  // { key: 'instagram', label: 'Instagram', icon: '/media-logos/instagram.png' },
];

// Чіп майданчика: іконка (повний колір коли активний, приглушена коли ні),
// з текстовим фолбеком якщо файл іконки ще не доданий.
function PlatformChip({ platform, active, onClick }: {
  platform: PublicationPlatform; active: boolean; onClick: () => void;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  // «Каталог» рендериться SVG-гліфом (лого-файлу нема): emerald коли активний,
  // приглушено-сірий коли ні — тим же прийомом, що й лого-майданчики (grayscale).
  const renderContent = () => {
    if (platform.svg) {
      return (
        <span className={active ? 'text-emerald-500' : 'text-gray-400 dark:text-gray-500'}
              style={{ display: 'inline-flex' }}>
          {platform.svg}
        </span>
      );
    }
    if (!imgFailed && platform.icon) {
      return (
        <img
          src={platform.icon}
          alt={platform.label}
          onError={() => setImgFailed(true)}
          style={{ height: 18, width: 'auto' }}
          className={active ? '' : 'opacity-40 grayscale'}
        />
      );
    }
    return (
      <span className={`text-xs font-medium ${active ? 'text-gray-900 dark:text-gray-100' : 'text-gray-500 dark:text-gray-400'}`}>
        {platform.label}
      </span>
    );
  };
  return (
    <button
      type="button"
      title={platform.label}
      aria-label={platform.label}
      aria-pressed={active}
      onClick={onClick}
      className={[
        "flex-1 min-w-[52px] flex items-center justify-center py-2 rounded-md border transition-all",
        active
          ? "bg-gray-100 dark:bg-gray-600/40 border-gray-800 dark:border-gray-300 ring-1 ring-gray-300"
          : "bg-white dark:bg-gray-700 border-gray-200 dark:border-gray-600 hover:border-gray-400",
      ].join(" ")}
    >
      {renderContent()}
    </button>
  );
}

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

const ProductFiltersPanel: React.FC<ProductFiltersPanelProps> = ({ filters, selectedFilters, onFilterChange, availableEuSizes, availableColorGroups }) => {
  const [priceMin, setPriceMin] = useState<string>(
    selectedFilters.min_price !== undefined ? String(selectedFilters.min_price) : ''
  );
  const [priceMax, setPriceMax] = useState<string>(
    selectedFilters.max_price !== undefined ? String(selectedFilters.max_price) : ''
  );
  // «Показати більше» для сітки EU-розмірів і кольорів
  const [euShowAll, setEuShowAll] = useState(false);
  const [colorShowAll, setColorShowAll] = useState(false);

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

  // Цілі EU-розміри для сітки. Дробові (39.5, 39.6…) не показуємо окремими
  // комірками — кожна дробова частина «приписана» до цілого за BMS-конвентом:
  //   .3 (⅓) / .5 (½) → належать до нижнього цілого;
  //   .6 (⅔) / .7      → належать і до нижнього, і до верхнього (spill вгору).
  // Бекенд за обраним цілим N матчить усі товари з розміром у (N-0.5, N+1).
  const euSizes = useMemo(() => {
    // Динамічний фасет (availableEuSizes) має пріоритет — сітка показує лише
    // розміри, реально наявні в поточному наборі. Фолбек — глобальний список.
    const raw = (availableEuSizes != null ? availableEuSizes : filters.size_ranges?.eu) || [];
    const wholes = new Set<number>();
    for (const s of raw) {
      const x = parseFloat(s);
      if (!isFinite(x)) continue;
      const lower = Math.floor(x);
      wholes.add(lower);
      if (x - lower > 0.5) wholes.add(lower + 1); // .6/.7 «піднімаються» до наступного
    }
    // Вже вибрані розміри лишаємо в сітці завжди — навіть якщо фасет звузив набір
    // (інакше не було б як зняти вибір). Вони цілі (вибираються лише з сітки).
    for (const s of (selectedFilters.sizeeu || [])) {
      const x = parseFloat(s);
      if (isFinite(x)) wholes.add(Math.floor(x));
    }
    // Розміри < 14 не є взуттєвими EU (EU стартує ~16 навіть для немовлят).
    // Значення 6–13 — це чужі системи (US/UK), що потрапили в колонку EU помилково.
    // Ховаємо їх із сітки, щоб не плутати. Реальний фікс — деривація EU зі СМ при парсингу.
    const MIN_PLAUSIBLE_EU = 14;
    return Array.from(wholes)
      .filter(n => n >= MIN_PLAUSIBLE_EU)
      .sort((a, b) => a - b)
      .map(String);
  }, [filters.size_ranges, availableEuSizes, selectedFilters.sizeeu]);

  const letterSizes = useMemo(() => {
    // Backend повертає вже відсортовано (XS,S,M,L,XL,XXL,…); тут просто dedup на всяк
    const raw = (filters as any).size_letters || [];
    return Array.from(new Set(raw)) as string[];
  }, [filters]);

  // Динамічні кольорові чіпи: показуємо лише групи, наявні в поточному наборі
  // (availableColorGroups), із живим лічильником; вибрані лишаємо завжди. Без
  // фасета (null) — глобальний список filters.color_groups з власним count.
  const colorGroupsToShow = useMemo(() => {
    const all = filters.color_groups || [];
    if (availableColorGroups == null) return all;
    const countById = new Map(availableColorGroups.map(g => [g.id, g.count]));
    const selected = new Set(selectedFilters.color_group_ids || []);
    return all
      .filter(cg => countById.has(cg.id) || selected.has(cg.id))
      .map(cg => ({ ...cg, count: countById.get(cg.id) ?? 0 }));
  }, [filters.color_groups, availableColorGroups, selectedFilters.color_group_ids]);

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
        <FilterSection title={SECTION_LABELS.types} badge={typeFilterBadge}>
          <MultiCheckList
            items={typeSubtypeMap.items}
            selected={combinedTypeSelection}
            onToggle={toggleTypeOrSubtype}
          />
        </FilterSection>
      )}

      {/* Бренд */}
      {filters.brands?.length > 0 && (
        <FilterSection title={SECTION_LABELS.brands} badge={countActive('brandids')}>
          <MultiCheckList
            items={filters.brands}
            selected={(selectedFilters as any).brandids || []}
            onToggle={toggle('brandids')}
          />
        </FilterSection>
      )}

      {/* ─────────── Розміри (одразу після Бренду) ─────────── */}

      {/* Розмір (EU) — сітка комірок */}
      {euSizes.length > 0 && (() => {
        const EU_VISIBLE = 12;
        const rangeActive = selectedFilters.min_sizeeu !== undefined || selectedFilters.max_sizeeu !== undefined;
        const selectedEu = selectedFilters.sizeeu || [];
        const visibleSizes = euShowAll ? euSizes : euSizes.slice(0, EU_VISIBLE);
        return (
          <FilterSection
            title="Розмір (EU)"
            badge={selectedEu.length + (rangeActive ? 1 : 0)}
            defaultOpen
          >
            <div className="grid grid-cols-4 gap-1.5">
              {visibleSizes.map(size => {
                const isActive = selectedEu.includes(size);
                return (
                  <button
                    key={size}
                    type="button"
                    onClick={() => {
                      const next = isActive ? selectedEu.filter(s => s !== size) : [...selectedEu, size];
                      onFilterChange({
                        ...selectedFilters,
                        sizeeu: next.length ? next : undefined,
                        min_sizeeu: undefined,
                        max_sizeeu: undefined,
                      });
                    }}
                    className={[
                      "py-1.5 text-xs rounded-md border text-center transition-colors",
                      isActive
                        ? "bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300 border-blue-500 ring-1 ring-blue-400 font-medium"
                        : "bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 border-gray-200 dark:border-gray-600 hover:border-blue-400 hover:text-blue-600 dark:hover:text-blue-400",
                    ].join(" ")}
                  >{size}</button>
                );
              })}
            </div>

            {euSizes.length > EU_VISIBLE && (
              <button
                type="button"
                onClick={() => setEuShowAll(s => !s)}
                className="mt-2 w-full flex items-center justify-center gap-1 text-xs font-medium text-blue-500 hover:text-blue-600 transition-colors"
              >
                {euShowAll ? 'Згорнути' : `Показати більше (${euSizes.length - EU_VISIBLE})`}
                <svg className={`w-3.5 h-3.5 transition-transform ${euShowAll ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
            )}

            {selectedEu.length > 0 && (
              <button
                type="button"
                onClick={() => onFilterChange({ ...selectedFilters, sizeeu: undefined })}
                className="mt-1 w-full py-0.5 text-[10px] text-gray-400 hover:text-red-500 hover:underline transition-colors"
              >
                Очистити вибрані
              </button>
            )}

            {/* Або діапазон */}
            <div className="mt-3 pt-2 border-t border-gray-100 dark:border-gray-700">
              <div className="text-[10px] text-gray-400 mb-1 uppercase tracking-wider">Або діапазон:</div>
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
                {rangeActive && (
                  <button
                    type="button"
                    onClick={() => onFilterChange({ ...selectedFilters, min_sizeeu: undefined, max_sizeeu: undefined })}
                    className="flex-shrink-0 text-gray-400 hover:text-red-500 transition-colors text-sm leading-none"
                    title="Скинути діапазон"
                  >×</button>
                )}
              </div>
            </div>
          </FilterSection>
        );
      })()}

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

      {/* Розмір в СМ (довжина стопи) */}
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

      {/* ──────────────────────────────────────────────────── */}

      {/* Стать — лаконічні чіпи з піктограмами (без тексту) */}
      {filters.genders?.length > 0 && (
        <FilterSection title={SECTION_LABELS.genders} badge={countActive('genderids')} defaultOpen>
          <div className="flex gap-1.5">
            {filters.genders.map(gender => {
              const isActive = ((selectedFilters as any).genderids || []).includes(gender.id);
              const kind = genderKind(gender.name);
              return (
                <button
                  key={gender.id}
                  type="button"
                  title={gender.name}
                  aria-label={gender.name}
                  aria-pressed={isActive}
                  onClick={() => toggle('genderids')(gender.id, !isActive)}
                  className={[
                    "flex-1 min-w-[52px] flex items-center justify-center py-2 rounded-md border transition-colors",
                    isActive
                      ? GENDER_ACTIVE_TINT[kind]
                      : "bg-white dark:bg-gray-700 text-gray-500 dark:text-gray-300 border-gray-200 dark:border-gray-600 hover:border-gray-400 hover:text-gray-700 dark:hover:text-gray-200",
                  ].join(" ")}
                >
                  <GenderGlyph kind={kind} />
                </button>
              );
            })}
          </div>
        </FilterSection>
      )}

      {/* Колір — базові групи + пошук відтінків */}
      <FilterSection
        title={SECTION_LABELS.colors}
        badge={(selectedFilters.color_group_ids?.length || 0) + (selectedFilters.colorids?.length || 0)}
        defaultOpen
      >
        {/* Базові кольори — динамічні квадратні чіпи-зразки з лічильником.
            Не більше 2 рядків (10 шт.); решта — за «Показати більше». Вибрані
            понад ліміт завжди лишаються видимими (щоб було як зняти). */}
        {colorGroupsToShow.length > 0 && (() => {
          const COLOR_VISIBLE = 10; // 2 ряди × 5 колонок
          const selectedSet = new Set(selectedFilters.color_group_ids || []);
          const base = colorShowAll ? colorGroupsToShow : colorGroupsToShow.slice(0, COLOR_VISIBLE);
          const extraSelected = colorShowAll
            ? []
            : colorGroupsToShow.slice(COLOR_VISIBLE).filter(cg => selectedSet.has(cg.id));
          const visible = [...base, ...extraSelected];
          const hiddenCount = colorGroupsToShow.length - base.length;
          return (
            <div className="mb-2">
              <div className="grid grid-cols-5 gap-2">
                {visible.map((cg: ColorGroup) => {
                  const isActive = selectedSet.has(cg.id);
                  const isWhite = cg.hex?.toLowerCase() === '#ffffff';
                  return (
                    <div key={cg.id} className="flex flex-col items-center gap-1">
                      <button
                        type="button"
                        onClick={() => toggleColorGroup(cg.id)}
                        title={`${cg.name} (${cg.count})`}
                        aria-label={cg.name}
                        aria-pressed={isActive}
                        style={{ backgroundColor: cg.hex || '#ccc' }}
                        className={[
                          "w-11 h-11 rounded-lg transition-all",
                          isActive
                            ? "ring-2 ring-offset-2 ring-blue-500 ring-offset-white dark:ring-offset-gray-800"
                            : "ring-1 ring-black/10 hover:ring-black/30 dark:ring-white/15 dark:hover:ring-white/40",
                          isWhite ? "border border-gray-300" : "",
                        ].join(" ")}
                      />
                      <span className={`text-[11px] tabular-nums ${isActive ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-gray-500 dark:text-gray-400'}`}>
                        {cg.count}×
                      </span>
                    </div>
                  );
                })}
              </div>
              {hiddenCount > 0 && (
                <button
                  type="button"
                  onClick={() => setColorShowAll(s => !s)}
                  className="mt-2 w-full flex items-center justify-center gap-1 text-xs font-medium text-blue-500 hover:text-blue-600 transition-colors"
                >
                  {colorShowAll ? 'Згорнути' : `Показати більше (${hiddenCount})`}
                  <svg className={`w-3.5 h-3.5 transition-transform ${colorShowAll ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
              )}
            </div>
          );
        })()}

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

      {/* Публікації — де опубліковано товар (іконки майданчиків, multi-select OR) */}
      <FilterSection
        title="Публікації"
        badge={((selectedFilters as any).published_on?.length) || 0}
        defaultOpen
      >
        <div className="flex gap-1.5">
          {PUBLICATION_PLATFORMS.map(pl => {
            const active = (((selectedFilters as any).published_on || []) as string[]).includes(pl.key);
            return (
              <PlatformChip
                key={pl.key}
                platform={pl}
                active={active}
                onClick={() => {
                  const cur = (((selectedFilters as any).published_on || []) as string[]);
                  const next = active ? cur.filter(k => k !== pl.key) : [...cur, pl.key];
                  onFilterChange({ ...selectedFilters, published_on: next.length ? next : undefined } as any);
                }}
              />
            );
          })}
        </div>
      </FilterSection>

      {/* Стан */}
      {filters.conditions?.length > 0 && (
        <FilterSection title={SECTION_LABELS.conditions} badge={countActive('conditionids')}>
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
          {/* «Пошкоджений» — це СТАН, а не статус (аномалія в таблиці statuses);
              ховаємо з опцій фільтра. Дані не чіпаємо. */}
          <MultiCheckList
            items={filters.statuses.filter(s => (s.name || '').trim() !== 'Пошкоджений')}
            selected={(selectedFilters as any).statusids || []}
            onToggle={toggle('statusids')}
            maxVisible={10}
          />
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