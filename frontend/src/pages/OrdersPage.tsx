import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import MainLayout from '../layouts/MainLayout';
import Pagination from '../components/common/Pagination';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import 'dayjs/locale/uk';

dayjs.locale('uk');
const { RangePicker } = DatePicker;

/* ── Types ─────────────────────────────────────────────────────────────── */
interface OrderItem {
  id: number;
  product_id: number | null;
  product_number: string;
  product_name: string;
  quantity: number;
  price: number;
  notes: string | null;
}

interface Order {
  id: number;
  client_id: number | null;
  client_name: string | null;
  order_date: string;
  order_status_id: number | null;
  order_status_name: string | null;
  payment_status_id: number | null;
  payment_status: string | null;
  payment_status_name: string | null;
  delivery_method_id: number | null;
  delivery_method_name: string | null;
  tracking_number: string | null;
  total_amount: number;
  notes: string | null;
  priority: number;
  order_items: OrderItem[];
  sales_channel: string | null;
  deferred_until: string | null;
}

interface FilterOption { id: number; name?: string; status_name?: string; }
interface FilterOptions {
  order_statuses: FilterOption[];
  payment_statuses: FilterOption[];
  payment_methods: FilterOption[];
  delivery_methods: FilterOption[];
  delivery_statuses: FilterOption[];
  clients: { id: number; name: string }[];
}

const SALES_CHANNELS = ['Ефір', 'Viber', 'Telegram', 'Instagram', 'TikTok', 'OLX', 'Grailed', 'Shafa', 'Магазин'] as const;
type SalesChannel = typeof SALES_CHANNELS[number];

const CHANNEL_COLORS: Record<string, string> = {
  'Ефір':      'bg-sky-100 text-sky-700 border-sky-200',
  'Viber':     'bg-violet-100 text-violet-700 border-violet-200',
  'Telegram':  'bg-blue-100 text-blue-700 border-blue-200',
  'Instagram': 'bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200',
  'TikTok':    'bg-gray-900 text-white border-gray-700',
  'OLX':       'bg-teal-100 text-teal-700 border-teal-200',
  'Grailed':   'bg-gray-200 text-gray-800 border-gray-400',
  'Shafa':     'bg-gray-900 text-gray-100 border-gray-600',
  'Магазин':   'bg-green-100 text-green-700 border-green-200',
  'GRAILED':   'bg-gray-200 text-gray-800 border-gray-400',
};

interface ActiveFilters {
  order_status_ids?: number[];
  payment_status_ids?: number[];
  delivery_method_ids?: number[];
  has_tracking?: boolean;       // true = є трекінг, false = без трекінга (нестворені)
  amount_min?: number;
  amount_max?: number;
  date_from?: string;
  date_to?: string;
  sales_channels?: string[];
}

interface OrdersPageProps {
  currentSearchTerm: string;
}

type SortField = 'id' | 'order_date' | 'total_amount' | 'priority' | 'client_name';

/* ── Constants ─────────────────────────────────────────────────────────── */
const STATUS_COLORS: Record<string, string> = {
  'Нове': '#3b82f6',
  'В обробці': '#8b5cf6',
  'Доставляється': '#f59e0b',
  'Доставлено': '#10b981',
  'Скасовано': '#ef4444',
};

const PAYMENT_COLORS: Record<string, string> = {
  'Оплачено': '#10b981',
  'Частково оплачено': '#f59e0b',
  'Очікує оплати': '#6366f1',
  'Не оплачено': '#ef4444',
  'Відкладено': '#60a5fa',
};

const DELIVERY_COLORS: Record<string, string> = {
  'Нова пошта':  'bg-red-100 text-red-700 border-red-200',
  'НП':          'bg-red-100 text-red-700 border-red-200',
  'Укрпошта':    'bg-yellow-100 text-yellow-700 border-yellow-200',
  'УП':          'bg-yellow-100 text-yellow-700 border-yellow-200',
  'самовивіз':   'bg-blue-100 text-blue-700 border-blue-200',
  'Самовивіз':   'bg-blue-100 text-blue-700 border-blue-200',
  'Локально':    'bg-violet-100 text-violet-700 border-violet-200',
  'Магазин':     'bg-pink-100 text-pink-700 border-pink-200',
  'Відкладено':  'bg-sky-100 text-sky-700 border-sky-200',
};

/** Normalize short delivery method names to full canonical form */
const normalizeDelivery = (dm: string): string => {
  const up = dm.trim().toUpperCase();
  if (up === 'НП' || up === 'НОВА ПОШТА') return 'Нова пошта';
  if (up === 'УП' || up === 'УКРПОШТА') return 'Укрпошта';
  if (up === 'САМОВИВІЗ' || up === 'САМОВИВОЗ') return 'Самовивіз';
  return dm.charAt(0).toUpperCase() + dm.slice(1);
};

const SORT_OPTIONS: { value: SortField; label: string; dir: 'asc' | 'desc' }[] = [
  { value: 'order_date', label: 'Новіші спочатку', dir: 'desc' },
  { value: 'order_date', label: 'Старіші спочатку', dir: 'asc' },
  { value: 'total_amount', label: 'Від найбільшого', dir: 'desc' },
  { value: 'total_amount', label: 'Від найменшого', dir: 'asc' },
  { value: 'client_name', label: 'Клієнт А→Я', dir: 'asc' },
  { value: 'client_name', label: 'Клієнт Я→А', dir: 'desc' },
  { value: 'id', label: 'ID ↓', dir: 'desc' },
  { value: 'id', label: 'ID ↑', dir: 'asc' },
];

/* ── Helpers ───────────────────────────────────────────────────────────── */
function fmtDate(d: string | null) {
  if (!d) return '—';
  try { return new Date(d).toLocaleDateString('uk-UA'); } catch { return d; }
}
function fmtMoney(n: number) {
  return new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH', maximumFractionDigits: 0 }).format(n);
}
function getOrderConflicts(order: Order): string[] {
  const issues: string[] = [];
  const unresolved = order.order_items?.filter(i => i.product_id === null || i.product_id === undefined);
  if (unresolved?.length) {
    const nums = unresolved.map(i => i.product_number || i.notes || '?').join(', ');
    issues.push(`${unresolved.length} товар(ів) не знайдено в базі: ${nums}`);
  }
  if (!order.order_status_name) issues.push('Не вказано статус замовлення');
  if (order.total_amount === 0) issues.push('Сума замовлення = 0');
  return issues;
}
function optName(o: FilterOption): string { return o.name || o.status_name || ''; }

/* ── Filter Panel Helpers ──────────────────────────────────────────────── */
function FilterSection({ title, badge, children, defaultOpen = false }: {
  title: string; badge?: number; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-gray-100 dark:border-gray-700 pb-0">
      <button type="button" onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between py-2.5 text-sm font-semibold text-gray-700 dark:text-gray-200 hover:text-blue-600 dark:hover:text-blue-400 transition-colors">
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

function CheckList({ items, selected, onToggle }: {
  items: FilterOption[]; selected: number[]; onToggle: (id: number, checked: boolean) => void;
}) {
  return (
    <div className="space-y-0.5 max-h-48 overflow-y-auto pr-1">
      {items.map(item => (
        <label key={item.id} className="flex items-center gap-2 py-0.5 cursor-pointer group">
          <input type="checkbox" className="w-3.5 h-3.5 rounded border-gray-300 text-blue-500 focus:ring-blue-400 cursor-pointer"
            checked={selected.includes(item.id)} onChange={e => onToggle(item.id, e.target.checked)} />
          <span className={`text-xs truncate max-w-[180px] group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors ${
            selected.includes(item.id) ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-gray-600 dark:text-gray-300'
          }`}>{optName(item)}</span>
        </label>
      ))}
    </div>
  );
}

/* ── Filter Panel Component ────────────────────────────────────────────── */
const OrdersFilterPanel: React.FC<{
  filterOpts: FilterOptions | null;
  filters: ActiveFilters;
  onChange: (f: ActiveFilters) => void;
}> = ({ filterOpts, filters, onChange }) => {
  const [amtMin, setAmtMin] = useState(filters.amount_min !== undefined ? String(filters.amount_min) : '');
  const [amtMax, setAmtMax] = useState(filters.amount_max !== undefined ? String(filters.amount_max) : '');
  const [dateFrom, setDateFrom] = useState(filters.date_from || '');
  const [dateTo, setDateTo] = useState(filters.date_to || '');

  const toggleArr = (field: 'order_status_ids' | 'payment_status_ids' | 'delivery_method_ids') =>
    (id: number, checked: boolean) => {
      const current = filters[field] || [];
      const updated = checked ? [...current, id] : current.filter(x => x !== id);
      onChange({ ...filters, [field]: updated.length > 0 ? updated : undefined });
    };

  const countActive = (f: keyof ActiveFilters) => {
    const v = filters[f];
    return Array.isArray(v) ? v.length : (v !== undefined ? 1 : 0);
  };
  const totalActive = countActive('order_status_ids') + countActive('payment_status_ids') + countActive('delivery_method_ids')
    + (filters.has_tracking !== undefined ? 1 : 0) + (filters.amount_min !== undefined || filters.amount_max !== undefined ? 1 : 0)
    + (filters.date_from || filters.date_to ? 1 : 0) + countActive('sales_channels');

  const applyAmount = () => {
    onChange({
      ...filters,
      amount_min: amtMin ? parseFloat(amtMin) : undefined,
      amount_max: amtMax ? parseFloat(amtMax) : undefined,
    });
  };
  const applyDate = () => {
    onChange({ ...filters, date_from: dateFrom || undefined, date_to: dateTo || undefined });
  };

  if (!filterOpts) return <div className="flex items-center justify-center h-32 text-gray-400 text-sm">Завантаження фільтрів...</div>;

  return (
    <div className="flex flex-col gap-0 text-sm">
      {totalActive > 0 && (
        <div className="mb-2 flex items-center gap-2 px-1">
          <span className="text-xs text-gray-500 dark:text-gray-400">Активних фільтрів:</span>
          <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full bg-blue-500 text-white text-[11px] font-bold">{totalActive}</span>
        </div>
      )}

      {/* Швидкі фільтри */}
      <FilterSection title="Швидкі фільтри" defaultOpen>
        <div className="flex flex-wrap gap-1.5">
          {[
            { label: 'Оплачені', apply: () => onChange({ ...filters, payment_status_ids: [1] }), active: filters.payment_status_ids?.length === 1 && filters.payment_status_ids[0] === 1 },
            { label: 'Не оплачені', apply: () => onChange({ ...filters, payment_status_ids: [4] }), active: filters.payment_status_ids?.length === 1 && filters.payment_status_ids[0] === 4 },
            { label: 'Скасовані', apply: () => onChange({ ...filters, order_status_ids: [5] }), active: filters.order_status_ids?.length === 1 && filters.order_status_ids[0] === 5 },
            { label: 'Нестворені (без ТТН)', apply: () => onChange({ ...filters, has_tracking: filters.has_tracking === false ? undefined : false }), active: filters.has_tracking === false },
            { label: 'З трекінгом', apply: () => onChange({ ...filters, has_tracking: filters.has_tracking === true ? undefined : true }), active: filters.has_tracking === true },
          ].map(q => (
            <button key={q.label} type="button" onClick={q.apply}
              className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
                q.active
                  ? 'bg-blue-500 border-blue-500 text-white'
                  : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:border-blue-400 hover:text-blue-600'
              }`}
            >{q.label}</button>
          ))}
        </div>
      </FilterSection>

      {/* Статус замовлення */}
      <FilterSection title="Статус замовлення" badge={countActive('order_status_ids')} defaultOpen>
        <CheckList items={filterOpts.order_statuses} selected={filters.order_status_ids || []} onToggle={toggleArr('order_status_ids')} />
      </FilterSection>

      {/* Статус оплати */}
      <FilterSection title="Статус оплати" badge={countActive('payment_status_ids')} defaultOpen>
        <CheckList items={filterOpts.payment_statuses} selected={filters.payment_status_ids || []} onToggle={toggleArr('payment_status_ids')} />
      </FilterSection>

      {/* Спосіб доставки */}
      <FilterSection title="Доставка" badge={countActive('delivery_method_ids')}>
        <CheckList items={filterOpts.delivery_methods} selected={filters.delivery_method_ids || []} onToggle={toggleArr('delivery_method_ids')} />
      </FilterSection>

      {/* Канал продажу */}
      <FilterSection title="Канал продажу" badge={countActive('sales_channels')}>
        <div className="flex flex-wrap gap-1.5">
          {SALES_CHANNELS.map(ch => {
            const active = filters.sales_channels?.includes(ch) ?? false;
            const colorClass = CHANNEL_COLORS[ch as SalesChannel];
            return (
              <button key={ch} type="button"
                onClick={() => {
                  const current = filters.sales_channels || [];
                  const updated = active ? current.filter(x => x !== ch) : [...current, ch];
                  onChange({ ...filters, sales_channels: updated.length > 0 ? updated : undefined });
                }}
                className={`px-2 py-0.5 rounded-full text-xs font-medium border transition-all ${
                  active ? colorClass + ' ring-2 ring-offset-1 ring-current' : 'border-gray-200 text-gray-500 hover:border-gray-400 bg-white dark:bg-gray-700 dark:border-gray-600 dark:text-gray-300'
                }`}
              >
                {ch}
              </button>
            );
          })}
        </div>
        {filters.sales_channels && filters.sales_channels.length > 0 && (
          <button type="button" onClick={() => onChange({ ...filters, sales_channels: undefined })}
            className="mt-1.5 w-full py-0.5 text-xs text-gray-400 hover:text-red-500 transition-colors">✕ Скинути</button>
        )}
      </FilterSection>

      {/* Діапазон суми */}
      <FilterSection title="Сума замовлення" badge={filters.amount_min !== undefined || filters.amount_max !== undefined ? 1 : 0}>
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <label className="text-[10px] text-gray-400 uppercase tracking-wide">Від (₴)</label>
              <input type="number" value={amtMin} onChange={e => setAmtMin(e.target.value)}
                onBlur={applyAmount} onKeyDown={e => e.key === 'Enter' && applyAmount()} placeholder="0"
                className="w-full mt-0.5 px-2 py-1 text-xs border border-gray-200 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-400" />
            </div>
            <span className="text-gray-400 mt-4">—</span>
            <div className="flex-1">
              <label className="text-[10px] text-gray-400 uppercase tracking-wide">До (₴)</label>
              <input type="number" value={amtMax} onChange={e => setAmtMax(e.target.value)}
                onBlur={applyAmount} onKeyDown={e => e.key === 'Enter' && applyAmount()} placeholder="∞"
                className="w-full mt-0.5 px-2 py-1 text-xs border border-gray-200 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-400" />
            </div>
          </div>
          <button type="button" onClick={applyAmount}
            className="w-full py-1 text-xs font-medium rounded bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-700 hover:bg-blue-100 transition-colors">
            Застосувати
          </button>
          {(filters.amount_min !== undefined || filters.amount_max !== undefined) && (
            <button type="button" onClick={() => { setAmtMin(''); setAmtMax(''); onChange({ ...filters, amount_min: undefined, amount_max: undefined }); }}
              className="w-full py-0.5 text-xs text-gray-400 hover:text-red-500 transition-colors">✕ Скинути</button>
          )}
        </div>
      </FilterSection>

      {/* Діапазон дат — календарик */}
      <FilterSection title="Період" badge={filters.date_from || filters.date_to ? 1 : 0} defaultOpen>
        <div className="space-y-2">
          <RangePicker
            size="small"
            format="DD.MM.YYYY"
            placeholder={['Від', 'До']}
            value={[
              dateFrom ? dayjs(dateFrom) : null,
              dateTo ? dayjs(dateTo) : null,
            ]}
            onChange={(dates) => {
              const from = dates?.[0]?.format('YYYY-MM-DD') || '';
              const to = dates?.[1]?.format('YYYY-MM-DD') || '';
              setDateFrom(from);
              setDateTo(to);
              onChange({ ...filters, date_from: from || undefined, date_to: to || undefined });
            }}
            style={{ width: '100%' }}
            presets={[
              { label: 'Сьогодні', value: [dayjs(), dayjs()] },
              { label: 'Вчора', value: [dayjs().subtract(1, 'day'), dayjs().subtract(1, 'day')] },
              { label: 'Тиждень', value: [dayjs().subtract(7, 'day'), dayjs()] },
              { label: 'Місяць', value: [dayjs().subtract(1, 'month'), dayjs()] },
              { label: '3 місяці', value: [dayjs().subtract(3, 'month'), dayjs()] },
            ]}
          />
          {(filters.date_from || filters.date_to) && (
            <button type="button" onClick={() => { setDateFrom(''); setDateTo(''); onChange({ ...filters, date_from: undefined, date_to: undefined }); }}
              className="w-full py-0.5 text-xs text-gray-400 hover:text-red-500 transition-colors">✕ Скинути</button>
          )}
        </div>
      </FilterSection>
    </div>
  );
};

/* ── Main Page ─────────────────────────────────────────────────────────── */
const OrdersPage: React.FC<OrdersPageProps> = ({ currentSearchTerm }) => {
  const navigate = useNavigate();
  const location = useLocation();

  const [orders, setOrders] = useState<Order[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(20);
  const [sortBy, setSortBy] = useState<SortField>('order_date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [filterOpts, setFilterOpts] = useState<FilterOptions | null>(null);
  const [filters, setFilters] = useState<ActiveFilters>({});

  // Load filter options once
  useEffect(() => {
    fetch('/api/orders/filters').then(r => r.json()).then(setFilterOpts).catch(() => {});
  }, []);

  // Build query params from state
  const buildParams = useCallback(() => {
    const p = new URLSearchParams({
      page: String(page), per_page: String(perPage), sort_by: sortBy, sort_dir: sortDir,
    });
    if (currentSearchTerm) p.set('search', currentSearchTerm);
    if (filters.order_status_ids?.length) filters.order_status_ids.forEach(id => p.append('order_status_ids', String(id)));
    if (filters.payment_status_ids?.length) filters.payment_status_ids.forEach(id => p.append('payment_status_ids', String(id)));
    if (filters.delivery_method_ids?.length) filters.delivery_method_ids.forEach(id => p.append('delivery_method_ids', String(id)));
    if (filters.has_tracking !== undefined) p.set('has_tracking', String(filters.has_tracking));
    if (filters.amount_min !== undefined) p.set('amount_min', String(filters.amount_min));
    if (filters.amount_max !== undefined) p.set('amount_max', String(filters.amount_max));
    if (filters.date_from) p.set('date_from', filters.date_from);
    if (filters.date_to) p.set('date_to', filters.date_to);
    if (filters.sales_channels?.length) filters.sales_channels.forEach(ch => p.append('sales_channels', ch));
    return p;
  }, [page, perPage, sortBy, sortDir, currentSearchTerm, filters]);

  const fetchOrders = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const res = await fetch(`/api/orders?${buildParams()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setOrders(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 1);
    } catch (e: any) { setError(e.message || 'Помилка завантаження'); }
    finally { setLoading(false); setIsRefreshing(false); }
  }, [buildParams]);

  useEffect(() => { fetchOrders(); }, [fetchOrders]);

  // URL sync
  useEffect(() => {
    const p = new URLSearchParams();
    p.set('page', String(page)); p.set('per_page', String(perPage));
    p.set('sort_by', sortBy); p.set('sort_dir', sortDir);
    navigate({ pathname: location.pathname, search: p.toString() }, { replace: true });
  }, [page, perPage, sortBy, sortDir]);

  const handleSort = (field: SortField) => {
    if (sortBy === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortBy(field); setSortDir('desc'); }
    setPage(1);
  };
  const SortIcon = ({ field }: { field: SortField }) => (
    <span className="ml-1 text-gray-400 text-xs">
      {sortBy === field ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
    </span>
  );

  const handleRefresh = () => { setIsRefreshing(true); fetchOrders(); };
  const handleResetFilters = () => { setFilters({}); setPage(1); setSortBy('order_date'); setSortDir('desc'); };
  const handleFilterChange = (f: ActiveFilters) => { setFilters(f); setPage(1); };

  const sortLabel = useMemo(() => {
    const match = SORT_OPTIONS.find(o => o.value === sortBy && o.dir === sortDir);
    return match?.label || 'Сортування';
  }, [sortBy, sortDir]);

  return (
    <MainLayout
      filterPanelContent={<OrdersFilterPanel filterOpts={filterOpts} filters={filters} onChange={handleFilterChange} />}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      <div className="p-4 pb-24 bg-white dark:bg-gray-800 shadow-md rounded-lg w-full">
        {/* Header */}
        <div className="sticky top-0 z-20 bg-white/90 dark:bg-gray-800/90 backdrop-blur px-2 py-2 -mx-2 border-b border-gray-100 dark:border-gray-700 flex flex-wrap justify-between items-center mb-3 gap-2">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">
            Замовлення
            <span className="ml-2 text-base font-normal text-gray-400">({total})</span>
          </h1>
          <div className="flex items-center gap-3">
            {currentSearchTerm && (
              <span className="text-sm text-gray-500 dark:text-gray-400">Пошук: «{currentSearchTerm}»</span>
            )}
            {/* Date range picker */}
            <RangePicker
              size="small"
              format="DD.MM.YYYY"
              placeholder={['Від', 'До']}
              value={[
                filters.date_from ? dayjs(filters.date_from) : null,
                filters.date_to ? dayjs(filters.date_to) : null,
              ]}
              onChange={(dates) => {
                handleFilterChange({
                  ...filters,
                  date_from: dates?.[0]?.format('YYYY-MM-DD') || undefined,
                  date_to: dates?.[1]?.format('YYYY-MM-DD') || undefined,
                });
              }}
              presets={[
                { label: 'Сьогодні', value: [dayjs(), dayjs()] },
                { label: 'Вчора', value: [dayjs().subtract(1, 'day'), dayjs().subtract(1, 'day')] },
                { label: 'Тиждень', value: [dayjs().subtract(7, 'day'), dayjs()] },
                { label: 'Місяць', value: [dayjs().subtract(1, 'month'), dayjs()] },
                { label: '3 місяці', value: [dayjs().subtract(3, 'month'), dayjs()] },
              ]}
              style={{ minWidth: 220 }}
            />
            {/* Sort dropdown */}
            <select value={`${sortBy}|${sortDir}`}
              onChange={e => { const [f, d] = e.target.value.split('|'); setSortBy(f as SortField); setSortDir(d as 'asc' | 'desc'); setPage(1); }}
              className="text-xs border border-gray-200 dark:border-gray-600 rounded px-2 py-1.5 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-400">
              {SORT_OPTIONS.map((o, i) => (
                <option key={i} value={`${o.value}|${o.dir}`}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Table */}
        {loading && orders.length === 0 ? (
          <div className="flex justify-center items-center h-48 text-gray-400">Завантаження...</div>
        ) : error ? (
          <div className="flex justify-center items-center h-48 text-red-500">{error}</div>
        ) : (
          <div className="overflow-x-auto rounded border border-gray-200 dark:border-gray-700">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600">
                <tr>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('id')}>
                    №<SortIcon field="id" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('order_date')}>
                    Дата<SortIcon field="order_date" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('client_name')}>
                    Клієнт<SortIcon field="client_name" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Товари</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('total_amount')}>
                    Сума<SortIcon field="total_amount" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Статус</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Оплата</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Доставка</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Трекінг</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Канал</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Нотатки</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {orders.length === 0 ? (
                  <tr>
                    <td colSpan={11} className="text-center py-12 text-gray-400">Замовлень не знайдено</td>
                  </tr>
                ) : orders.map(order => {
                  const conflicts = getOrderConflicts(order);
                  const hasConflict = conflicts.length > 0;
                  const conflictTitle = conflicts.join(' • ');
                  return (
                  <React.Fragment key={order.id}>
                    <tr
                      className={`transition-colors cursor-pointer ${hasConflict
                        ? 'bg-orange-50 dark:bg-orange-900/20 border-l-4 border-orange-400 hover:bg-orange-100 dark:hover:bg-orange-900/40'
                        : 'hover:bg-gray-50 dark:hover:bg-gray-700/50'}`}
                      title={hasConflict ? `⚠ ${conflictTitle}` : undefined}
                      onClick={() => setExpandedId(expandedId === order.id ? null : order.id)}
                    >
                      <td className="px-3 py-2 font-mono text-xs">
                        <span className={hasConflict ? 'text-orange-600 dark:text-orange-400 font-bold' : 'text-gray-500 dark:text-gray-400'}>
                          {order.id}{hasConflict && ' ⚠'}
                        </span>
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{fmtDate(order.order_date)}</td>
                      <td className="px-3 py-2 font-medium max-w-[160px] truncate">
                        {order.client_name
                          ? <span className="text-gray-900 dark:text-gray-100">{order.client_name}</span>
                          : <span className="text-gray-400 dark:text-gray-500 italic">Анонімний</span>}
                      </td>
                      <td className="px-3 py-2 text-gray-500 dark:text-gray-400 text-center">
                        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-gray-100 dark:bg-gray-600 text-xs font-semibold">
                          {order.order_items?.length || 0}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-semibold whitespace-nowrap text-gray-900 dark:text-gray-100">{fmtMoney(order.total_amount)}</td>
                      <td className="px-3 py-2">
                        {order.order_status_name ? (
                          <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium text-white"
                            style={{ background: STATUS_COLORS[order.order_status_name] || '#6b7280' }}>
                            {order.order_status_name}
                          </span>
                        ) : <span className="text-gray-400 text-xs">—</span>}
                      </td>
                      <td className="px-3 py-2">
                        {(order.payment_status || order.payment_status_name) ? (
                          <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium text-white"
                            style={{ background: PAYMENT_COLORS[order.payment_status || order.payment_status_name || ''] || '#6b7280' }}>
                            {order.payment_status || order.payment_status_name}
                          </span>
                        ) : <span className="text-gray-400 text-xs">—</span>}
                      </td>
                      <td className="px-3 py-2">
                        {(() => {
                          const dm = order.delivery_method_name;
                          if (!dm) return <span className="text-gray-400 text-xs">—</span>;
                          const dmNorm = normalizeDelivery(dm);
                          const cls = DELIVERY_COLORS[dmNorm] || 'bg-gray-100 text-gray-600 border-gray-200';
                          return <span className={`inline-flex px-1.5 py-0 rounded text-[10px] font-semibold border ${cls}`}>{dmNorm}</span>;
                        })()}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-500 dark:text-gray-400">
                        {order.tracking_number || '—'}
                      </td>
                      <td className="px-3 py-2">
                        {(() => {
                          const ch = (order.sales_channel || 'Ефір') as SalesChannel;
                          const cls = CHANNEL_COLORS[ch] || 'bg-gray-100 text-gray-600 border-gray-200';
                          const isDeferred = (order.delivery_method_name || '').toLowerCase().includes('відкладен')
                            || (order.payment_status || order.payment_status_name || '').toLowerCase().includes('відкладен');
                          return (
                            <div className="flex flex-col gap-0.5">
                              <span className={`inline-flex px-1.5 py-0 rounded text-[10px] font-semibold border ${cls}`}>{ch}</span>
                              {isDeferred && (
                                <span className="inline-flex items-center gap-0.5 px-1 py-0 rounded text-[9px] font-medium bg-orange-100 text-orange-700 border border-orange-200">
                                  {order.deferred_until ? `до ${fmtDate(order.deferred_until)}` : 'Без терміну'}
                                </span>
                              )}
                            </div>
                          );
                        })()}
                      </td>
                      <td className="px-3 py-2 text-gray-500 dark:text-gray-400 text-xs max-w-[160px] truncate">
                        {order.notes || '—'}
                      </td>
                    </tr>
                    {expandedId === order.id && order.order_items?.length > 0 && (
                      <tr className="bg-blue-50 dark:bg-blue-900/20">
                        <td colSpan={11} className="px-6 py-3">
                          <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-2">Позиції замовлення:</div>
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="text-gray-500 dark:text-gray-400">
                                <th className="text-left pr-4 pb-1">Номер товару</th>
                                <th className="text-left pr-4 pb-1">Назва</th>
                                <th className="text-left pr-4 pb-1">К-сть</th>
                                <th className="text-left pb-1">Ціна</th>
                              </tr>
                            </thead>
                            <tbody>
                              {order.order_items.map(item => (
                                <tr key={item.id} className="border-t border-blue-100 dark:border-blue-800">
                                  <td className="pr-4 py-1 font-mono text-blue-600 dark:text-blue-400">{item.product_number}</td>
                                  <td className="pr-4 py-1 text-gray-700 dark:text-gray-300">{item.product_name}</td>
                                  <td className="pr-4 py-1">{item.quantity}</td>
                                  <td className="py-1 font-semibold">{fmtMoney(item.price)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Footer pagination */}
        <div className="fixed bottom-0 left-0 right-0 px-4 py-3 bg-white/95 dark:bg-gray-800/95 backdrop-blur border-t border-gray-100 dark:border-gray-700 z-20">
          <div className="max-w-screen-2xl mx-auto flex items-center justify-between gap-4">
            <span className="text-sm text-gray-500 dark:text-gray-400">
              Всього: <strong>{total}</strong> замовлень
            </span>
            <Pagination
              currentPage={page}
              totalPages={pages}
              totalItems={total}
              itemsPerPage={perPage}
              onPageChange={setPage}
              onPerPageChange={(n) => { setPerPage(n); setPage(1); }}
            />
            <span className="text-xs text-gray-400">
              Стор. {page} з {pages}
            </span>
          </div>
        </div>
        <div className="h-20" />
      </div>
    </MainLayout>
  );
};

export default OrdersPage;