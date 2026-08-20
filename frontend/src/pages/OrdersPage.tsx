import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import MainLayout from '../layouts/MainLayout';
import Pagination from '../components/common/Pagination';
import ProductDetailsModal from '../components/products/ProductDetailsModal';
import ClientDetailsModal from '../components/clients/ClientDetailsModal';
import OrderDetailsModal from '../components/orders/OrderDetailsModal';
import BmsEmpty from '../components/common/BmsEmpty';
import { CopyOnClick, OrderStatusBadge, PaymentStatusBadge, UnknownIf } from '../components/common/displayHelpers';
import { DeliveryBadge } from '../components/common/DeliveryBadge';
import LoadingSpinner from '../components/common/LoadingSpinner';
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
  discount_type?: string | null;
  discount_value?: number | null;
  additional_operation?: string | null;
  additional_operation_value?: number | null;
  notes: string | null;
  has_queue: boolean;
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
  has_queue: boolean;
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

const SALES_CHANNELS = ['Ефір', 'Viber', 'Telegram', 'Instagram', 'TikTok', 'OLX', 'Prom', 'MONO', 'Каталог', 'Grailed', 'Shafa', 'Магазин'] as const;
type SalesChannel = typeof SALES_CHANNELS[number];

const CHANNEL_COLORS: Record<string, string> = {
  'Ефір':      'bg-sky-100 text-sky-700 border-sky-200',
  'Viber':     'bg-violet-100 text-violet-700 border-violet-200',
  'Telegram':  'bg-blue-100 text-blue-700 border-blue-200',
  'Instagram': 'bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200',
  'TikTok':    'bg-gray-900 text-white border-gray-700',
  'OLX':       'bg-teal-100 text-teal-700 border-teal-200',
  'Prom':      'bg-indigo-100 text-indigo-700 border-indigo-200',   // фірмовий фіолет Prom.ua
  'MONO':      'bg-gray-900 text-white border-black',
  'Каталог':   'bg-emerald-100 text-emerald-700 border-emerald-200',
  'Grailed':   'bg-gray-200 text-gray-800 border-gray-400',
  'Shafa':     'bg-black text-white border-black',
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
  only_problematic?: boolean;   // тільки підсвічені (проблемні) замовлення
  product_id?: number;          // показати лише замовлення з цим товаром
  product_label?: string;       // людинозрозуміла мітка товару для банера (не йде в API)
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
  if (order.total_amount === 0 && order.order_status_name !== 'Подарунок') issues.push('Сума замовлення = 0');
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

  if (!filterOpts) return <LoadingSpinner variant="section" text="Завантаження фільтрів…" />;

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
          {/* Проблемні — окрема кнопка з оранжевим акцентом */}
          <button
            type="button"
            onClick={() => onChange({ ...filters, only_problematic: filters.only_problematic ? undefined : true })}
            className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
              filters.only_problematic
                ? 'bg-orange-500 border-orange-500 text-white'
                : 'border-orange-300 text-orange-600 dark:border-orange-600 dark:text-orange-400 hover:border-orange-500 hover:bg-orange-50 dark:hover:bg-orange-900/20'
            }`}
            title="Замовлення з сумою 0, без статусу або з неприв'язаними товарами"
          >⚠ Тільки проблемні</button>
        </div>
      </FilterSection>

      {/* Статус замовлення (+ синтетичний «Невідомо» = порожній статус у аркуші, id=0 → IS NULL) */}
      <FilterSection title="Статус замовлення" badge={countActive('order_status_ids')} defaultOpen>
        <CheckList
          items={[...filterOpts.order_statuses, { id: 0, status_name: 'Невідомо' } as FilterOption]}
          selected={filters.order_status_ids || []}
          onToggle={toggleArr('order_status_ids')}
        />
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
  const [cardProductId, setCardProductId] = useState<number | null>(null);
  const [cardClientId, setCardClientId] = useState<number | null>(null);
  const [editOrderId, setEditOrderId] = useState<number | null>(null);
  const [filteredSum, setFilteredSum] = useState<number>(0);
  const [queueMarkersCount, setQueueMarkersCount] = useState<number>(0);
  const [filterOpts, setFilterOpts] = useState<FilterOptions | null>(null);
  const listAbortRef = useRef<AbortController | null>(null);
  const listRequestRef = useRef(0);
  // Дефолтний фільтр: останній тиждень. Діє доки користувач сам не змінить
  // фільтри (включно зі скиданням). Один раз на монтуванні.
  const [filters, setFilters] = useState<ActiveFilters>(() => ({
    date_from: dayjs().subtract(7, 'day').format('YYYY-MM-DD'),
    date_to: dayjs().format('YYYY-MM-DD'),
  }));

  /* ── Column visibility (right-click toggle, per-user persisted) ───────── */
  const ORDERS_COLUMNS_KEY = 'orders_table_columns_v1';
  const ordersColumnOrder: { id: string; title: string; optional: boolean }[] = [
    { id: 'id',           title: 'Номер',     optional: false },
    { id: 'order_date',   title: 'Дата',      optional: true },
    { id: 'client_name',  title: 'Клієнт',    optional: false },
    { id: 'items_count',  title: 'Товари',    optional: true },
    { id: 'total_amount', title: 'Сума',      optional: false },
    { id: 'status',       title: 'Статус',    optional: true },
    { id: 'payment',      title: 'Оплата',    optional: true },
    { id: 'delivery',     title: 'Доставка',  optional: true },
    { id: 'tracking',     title: 'Трекінг',   optional: true },
    { id: 'channel',      title: 'Канал',     optional: true },
    { id: 'notes',        title: 'Нотатки',   optional: true },
  ];
  const ordersDefaultVis: Record<string, boolean> = ordersColumnOrder.reduce(
    (acc, c) => { acc[c.id] = true; return acc; },
    {} as Record<string, boolean>
  );
  const [colVis, setColVis] = useState<Record<string, boolean>>(() => {
    try {
      const raw = localStorage.getItem(ORDERS_COLUMNS_KEY);
      if (!raw) return ordersDefaultVis;
      return { ...ordersDefaultVis, ...JSON.parse(raw) };
    } catch { return ordersDefaultVis; }
  });
  useEffect(() => {
    localStorage.setItem(ORDERS_COLUMNS_KEY, JSON.stringify(colVis));
  }, [colVis]);
  const colMenuRef = useRef<HTMLDivElement | null>(null);
  const [colMenuOpen, setColMenuOpen] = useState(false);
  const [colMenuPos, setColMenuPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!colMenuRef.current) return setColMenuOpen(false);
      if (!colMenuRef.current.contains(e.target as Node)) setColMenuOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);
  const handleColumnsContextMenu: React.MouseEventHandler<HTMLDivElement> = (e) => {
    e.preventDefault();
    setColMenuPos({ x: e.clientX, y: e.clientY });
    setColMenuOpen(true);
  };
  const visibleColCount = ordersColumnOrder.filter(c => colVis[c.id]).length;

  // Load filter options once
  useEffect(() => {
    fetch('/api/orders/filters').then(r => r.json()).then(setFilterOpts).catch(() => {});
  }, []);

  // Pending nav-фільтр з картки товару ("Показати в замовленнях").
  // Перекриває дефолтний тижневий фільтр: товар може бути проданий давно,
  // тож шукаємо по ВСІХ датах, лише за product_id.
  const PENDING_ORDERS_FILTER_KEY = 'bms_orders_pending_filter';
  const applyPendingFilter = useCallback(() => {
    try {
      const raw = localStorage.getItem(PENDING_ORDERS_FILTER_KEY);
      if (!raw) return;
      localStorage.removeItem(PENDING_ORDERS_FILTER_KEY);
      const parsed = JSON.parse(raw);
      if (parsed && parsed.product_id) {
        setFilters({
          product_id: Number(parsed.product_id),
          product_label: parsed.product_label || undefined,
          // явно без date_from/date_to — шукаємо по всій історії
        });
        setPage(1);
        setSortBy('order_date');
        setSortDir('desc');
      }
    } catch { /* ignore */ }
  }, []);
  // Один раз при монтуванні + щоразу коли вкладку знову активують
  // (custom event з App при перемиканні таба).
  useEffect(() => {
    applyPendingFilter();
    const onShow = () => applyPendingFilter();
    window.addEventListener('bms:orders-show-product', onShow);
    return () => window.removeEventListener('bms:orders-show-product', onShow);
  }, [applyPendingFilter]);

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
    if (filters.only_problematic) p.set('only_problematic', 'true');
    if (filters.product_id) p.set('product_id', String(filters.product_id));
    return p;
  }, [page, perPage, sortBy, sortDir, currentSearchTerm, filters]);

  const fetchOrders = useCallback(async () => {
    listAbortRef.current?.abort();
    const controller = new AbortController();
    listAbortRef.current = controller;
    const requestId = ++listRequestRef.current;
    setLoading(true); setError(null);
    try {
      const res = await fetch(`/api/orders?${buildParams()}`, { signal: controller.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (requestId !== listRequestRef.current) return;
      setOrders(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 1);
      setFilteredSum(data.filtered_sum || 0);
      setQueueMarkersCount(data.queue_markers_count || 0);
    } catch (e: any) {
      if (e?.name === 'AbortError') return;
      if (requestId === listRequestRef.current) setError(e.message || 'Помилка завантаження');
    } finally {
      if (requestId === listRequestRef.current) { setLoading(false); setIsRefreshing(false); }
    }
  }, [buildParams]);

  useEffect(() => { fetchOrders(); }, [fetchOrders]);
  useEffect(() => () => listAbortRef.current?.abort(), []);
  useEffect(() => { setPage(1); }, [currentSearchTerm]);

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
            {queueMarkersCount > 0 && (
              <span
                title="Службові мітки «на товар є черга» за вибраний період. Вони не є замовленнями."
                className="ml-2 inline-flex align-middle px-2 py-0.5 rounded-full border border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-300 text-[11px] font-semibold"
              >
                Черга · {queueMarkersCount}
              </span>
            )}
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

        {/* Активний фільтр по товару (з картки товару → "Показати в замовленнях") */}
        {filters.product_id && (
          <div className="flex items-center justify-between gap-3 mb-3 px-3 py-2 rounded-lg border border-blue-200 bg-blue-50 dark:bg-blue-900/20 dark:border-blue-800">
            <span className="text-sm text-blue-800 dark:text-blue-200">
              Показано замовлення з товаром{filters.product_label ? <> <b>{filters.product_label}</b></> : <> ID {filters.product_id}</>}
              <span className="text-blue-500 dark:text-blue-400"> · фільтр по даті знято</span>
            </span>
            <button
              type="button"
              onClick={() => handleResetFilters()}
              className="text-xs px-2 py-1 rounded border border-blue-300 text-blue-700 hover:bg-blue-100 dark:text-blue-300 dark:border-blue-700 dark:hover:bg-blue-900/40 whitespace-nowrap"
            >
              ✕ Скинути
            </button>
          </div>
        )}

        {/* Table */}
        {loading && orders.length === 0 ? (
          <LoadingSpinner variant="section" size="large" text="Завантаження замовлень…" />
        ) : error ? (
          <div className="flex justify-center items-center h-48 text-red-500">{error}</div>
        ) : (
          <div className="overflow-x-auto rounded border border-gray-200 dark:border-gray-700" onContextMenu={handleColumnsContextMenu}>
            <table className="w-full text-sm [&_th]:text-center [&_td]:text-center">
              <thead className="bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600">
                <tr>
                  {colVis.id && (
                    <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('id')}>
                      Номер<SortIcon field="id" />
                    </th>
                  )}
                  {colVis.order_date && (
                    <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('order_date')}>
                      Дата<SortIcon field="order_date" />
                    </th>
                  )}
                  {colVis.client_name && (
                    <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('client_name')}>
                      Клієнт<SortIcon field="client_name" />
                    </th>
                  )}
                  {colVis.items_count && (
                    <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Товари</th>
                  )}
                  {colVis.total_amount && (
                    <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('total_amount')}>
                      Сума<SortIcon field="total_amount" />
                    </th>
                  )}
                  {colVis.status &&    <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Статус</th>}
                  {colVis.payment &&   <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Оплата</th>}
                  {colVis.delivery &&  <th className="px-1 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 w-[64px]">Доставка</th>}
                  {colVis.tracking &&  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Трекінг</th>}
                  {colVis.channel &&   <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Канал</th>}
                  {colVis.notes &&     <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Нотатки</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {orders.length === 0 ? (
                  <tr>
                    <td colSpan={visibleColCount}><BmsEmpty label="Замовлень не знайдено" /></td>
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
                      {colVis.id && (
                        <td className="px-3 py-2 font-mono text-xs">
                          <CopyOnClick
                            value={order.id}
                            display={
                              <span className={hasConflict ? 'text-orange-600 dark:text-orange-400 font-bold' : 'text-gray-500 dark:text-gray-400'}>
                                {order.id}{hasConflict && ' ⚠'}
                              </span>
                            }
                          />
                        </td>
                      )}
                      {colVis.order_date && (
                        <td className="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{fmtDate(order.order_date)}</td>
                      )}
                      {colVis.client_name && (
                        <td className="px-3 py-2 font-medium max-w-[160px] truncate">
                          {order.client_name
                            ? (order.client_id
                                ? <button
                                    type="button"
                                    onClick={(e) => { e.stopPropagation(); setCardClientId(order.client_id); }}
                                    className="text-gray-900 dark:text-gray-100 hover:text-blue-600 dark:hover:text-blue-400 hover:underline transition-colors"
                                    title="Відкрити картку клієнта"
                                  ><UnknownIf value={order.client_name} label="Анонімний" /></button>
                                : <span className="text-gray-900 dark:text-gray-100"><UnknownIf value={order.client_name} label="Анонімний" /></span>)
                            : <span className="text-gray-400 dark:text-gray-500 italic">Анонімний</span>}
                        </td>
                      )}
                      {colVis.items_count && (
                        <td className="px-3 py-2 text-gray-500 dark:text-gray-400 text-center">
                          <span className="inline-flex items-center gap-1.5">
                            <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-gray-100 dark:bg-gray-600 text-xs font-semibold">
                              {order.order_items?.length || 0}
                            </span>
                            {order.has_queue && (
                              <span
                                title="На один із товарів цього замовлення є черга в цій вкладці"
                                className="inline-flex w-2 h-2 rounded-full bg-amber-500 ring-2 ring-amber-100 dark:ring-amber-900/50"
                              />
                            )}
                          </span>
                        </td>
                      )}
                      {colVis.total_amount && (
                        <td className="px-3 py-2 font-semibold whitespace-nowrap text-gray-900 dark:text-gray-100">
                          {order.total_amount != null
                            ? <CopyOnClick value={String(order.total_amount)} display={<>{fmtMoney(order.total_amount)}</>} />
                            : '—'}
                        </td>
                      )}
                      {colVis.status && (
                        <td className="px-3 py-2">
                          {order.order_status_name
                            ? <OrderStatusBadge name={order.order_status_name} />
                            : <span className="text-gray-400 text-xs">—</span>}
                        </td>
                      )}
                      {colVis.payment && (
                        <td className="px-3 py-2">
                          <PaymentStatusBadge name={order.payment_status || order.payment_status_name} />
                        </td>
                      )}
                      {colVis.delivery && (
                        <td className="px-1 py-2 w-[64px]">
                          <DeliveryBadge name={order.delivery_method_name} height={18} />
                        </td>
                      )}
                      {colVis.tracking && (
                        <td className="px-3 py-2 font-mono text-xs text-gray-500 dark:text-gray-400">
                          {order.tracking_number
                            ? <CopyOnClick value={order.tracking_number} groupDigits />
                            : '—'}
                        </td>
                      )}
                      {colVis.channel && (
                        <td className="px-3 py-2">
                          {(() => {
                            const ch = (order.sales_channel || 'Ефір') as SalesChannel;
                            const cls = CHANNEL_COLORS[ch] || 'bg-gray-100 text-gray-600 border-gray-200';
                            const isDeferred = (order.delivery_method_name || '').toLowerCase().includes('відкладен')
                              || (order.payment_status || order.payment_status_name || '').toLowerCase().includes('відкладен');
                            return (
                              <div className="flex flex-col gap-0.5 items-center">
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
                      )}
                      {colVis.notes && (
                        <td className="px-3 py-2 text-gray-500 dark:text-gray-400 text-xs max-w-[160px] truncate">
                          {order.notes || '—'}
                        </td>
                      )}
                    </tr>
                    {expandedId === order.id && order.order_items?.length > 0 && (
                      <tr className="bg-blue-50 dark:bg-blue-900/20">
                        <td colSpan={visibleColCount} className="px-6 py-3">
                          <div className="flex items-center justify-between mb-2">
                            <div className="text-xs font-semibold text-gray-500 dark:text-gray-400">Позиції замовлення:</div>
                            <button
                              onClick={(e) => { e.stopPropagation(); setEditOrderId(order.id); }}
                              className="inline-flex items-center gap-1.5 text-xs px-3 py-1 rounded-md border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200"
                              title="Редагувати статус / оплату / доставку / трекінг / нотатки / канал">
                              ✎ Редагувати замовлення
                            </button>
                          </div>
                          <table className="w-full text-xs [&_th]:text-center [&_td]:text-center">
                            <thead>
                              <tr className="text-gray-500 dark:text-gray-400">
                                <th className="text-left pr-4 pb-1">Номер товару</th>
                                <th className="text-left pr-4 pb-1">Назва</th>
                                <th className="text-left pr-4 pb-1">К-сть</th>
                                <th className="text-left pr-4 pb-1">Ціна</th>
                                <th className="text-left pb-1">Знижка / операція</th>
                              </tr>
                            </thead>
                            <tbody>
                              {order.order_items.map(item => {
                                const googleQ = (item.product_name || '').trim();
                                const googleUrl = googleQ ? `https://www.google.com/search?q=${encodeURIComponent(googleQ)}` : null;
                                // Формуємо бейдж знижки/операції
                                const badges: { label: string; cls: string; title?: string }[] = [];
                                if (item.discount_type && item.discount_value) {
                                  if (item.discount_type === 'Відсоток') {
                                    badges.push({
                                      label: `−${item.discount_value}%`,
                                      cls: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
                                      title: `Знижка ${item.discount_value}% від ціни товару`,
                                    });
                                  } else {
                                    badges.push({
                                      label: `−${fmtMoney(item.discount_value)}`,
                                      cls: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
                                      title: `Знижка ${fmtMoney(item.discount_value)} грн`,
                                    });
                                  }
                                }
                                if (item.additional_operation && item.additional_operation_value != null) {
                                  const op = item.additional_operation;
                                  const v = item.additional_operation_value;
                                  const sign = v >= 0 ? '+' : '';
                                  badges.push({
                                    label: `${op} ${sign}${fmtMoney(v)}`,
                                    cls: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
                                    title: `Додаткова операція: ${op} ${v}`,
                                  });
                                }
                                return (
                                <tr key={item.id} className="border-t border-blue-100 dark:border-blue-800">
                                  <td className="pr-4 py-1 font-mono">
                                    {item.product_id ? (
                                      <span
                                        className="cursor-pointer text-blue-600 hover:text-blue-800 hover:underline dark:text-blue-400 dark:hover:text-blue-300"
                                        title="Відкрити картку товару"
                                        onClick={(e) => { e.stopPropagation(); setCardProductId(item.product_id); }}
                                      >{item.product_number}</span>
                                    ) : (
                                      <span className="text-gray-500 dark:text-gray-400">{item.product_number}</span>
                                    )}
                                    {item.has_queue && (
                                      <span
                                        title="На цей товар є черга в цій вкладці"
                                        className="ml-2 inline-flex px-1.5 py-0.5 rounded-full border border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-300 text-[10px] font-semibold font-sans"
                                      >Черга</span>
                                    )}
                                  </td>
                                  <td className="pr-4 py-1">
                                    {googleUrl ? (
                                      <a href={googleUrl} target="_blank" rel="noopener noreferrer"
                                        className="cursor-pointer text-blue-600 hover:text-blue-800 hover:underline dark:text-blue-400 dark:hover:text-blue-300"
                                        title="Пошук в Google"
                                        onClick={(e) => e.stopPropagation()}
                                      >{item.product_name}</a>
                                    ) : (
                                      <span className="text-gray-700 dark:text-gray-300">{item.product_name}</span>
                                    )}
                                  </td>
                                  <td className="pr-4 py-1">{item.quantity}</td>
                                  <td className="pr-4 py-1 font-semibold">{fmtMoney(item.price)}</td>
                                  <td className="py-1">
                                    {badges.length === 0 ? (
                                      <span className="text-gray-300 dark:text-gray-600">—</span>
                                    ) : (
                                      <span className="inline-flex flex-wrap gap-1">
                                        {badges.map((b, i) => (
                                          <span key={i} title={b.title}
                                            className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold ${b.cls}`}>
                                            {b.label}
                                          </span>
                                        ))}
                                      </span>
                                    )}
                                  </td>
                                </tr>
                              );})}
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

        {/* Column-visibility floating menu (right-click on table) */}
        {colMenuOpen && (
          <div
            ref={colMenuRef}
            style={{ top: colMenuPos.y, left: colMenuPos.x }}
            className="fixed z-[10000] w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg p-2"
          >
            <div className="px-2 py-1 text-xs text-gray-500 dark:text-gray-400">Видимість колонок</div>
            <div className="max-h-80 overflow-auto pr-1">
              {ordersColumnOrder.map(c => (
                <label
                  key={c.id}
                  className="flex items-center justify-between px-2 py-1 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 rounded"
                >
                  <span className="text-gray-700 dark:text-gray-200">{c.title}</span>
                  <input
                    type="checkbox"
                    checked={!!colVis[c.id]}
                    onChange={(e) => setColVis(v => ({ ...v, [c.id]: e.target.checked }))}
                    disabled={!c.optional && colVis[c.id]}
                  />
                </label>
              ))}
            </div>
            <div className="mt-2 grid grid-cols-3 gap-2">
              <button
                className="px-2 py-1 text-xs rounded border border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
                onClick={() => setColVis(ordersColumnOrder.reduce((a, c) => { a[c.id] = true; return a; }, {} as Record<string, boolean>))}
              >Всі</button>
              <button
                className="px-2 py-1 text-xs rounded border border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
                onClick={() => setColVis(ordersColumnOrder.reduce((a, c) => { a[c.id] = !c.optional; return a; }, {} as Record<string, boolean>))}
              >Тільки обов'язкові</button>
              <button
                className="px-2 py-1 text-xs rounded border border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
                onClick={() => setColVis(ordersDefaultVis)}
              >За умовч.</button>
            </div>
          </div>
        )}

        {/* Footer pagination */}
        <div className="fixed bottom-0 left-0 right-0 px-4 py-3 bg-white/95 dark:bg-gray-800/95 backdrop-blur border-t border-gray-100 dark:border-gray-700 z-20">
          <div className="w-full grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] items-center gap-4 max-w-screen-2xl mx-auto px-2">
            <div className="order-2 md:order-none justify-self-start flex items-center gap-4 flex-wrap">
              <span className="text-sm text-gray-500 dark:text-gray-400">
                Всього: <strong>{total}</strong> замовлень
              </span>
              {filteredSum > 0 && (
                <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 text-sm font-semibold text-green-700 dark:text-green-300">
                  <span className="text-xs font-normal text-green-500 dark:text-green-400">сума</span>
                  {new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH', maximumFractionDigits: 0 }).format(filteredSum)}
                </span>
              )}
            </div>
            <div className="order-1 md:order-none justify-self-center flex justify-center">
              <Pagination
                currentPage={page}
                totalPages={pages}
                totalItems={total}
                itemsPerPage={perPage}
                onPageChange={setPage}
                onPerPageChange={(n) => { setPerPage(n); setPage(1); }}
              />
            </div>
            <span className="order-3 md:order-none justify-self-end text-xs text-gray-400">
              Стор. {page} з {pages}
            </span>
          </div>
        </div>
        <div className="h-20" />
      </div>
      <ProductDetailsModal
        productId={cardProductId}
        open={cardProductId !== null}
        onClose={() => setCardProductId(null)}
      />
      <ClientDetailsModal
        clientId={cardClientId}
        open={cardClientId !== null}
        onClose={() => setCardClientId(null)}
      />
      <OrderDetailsModal
        orderId={editOrderId}
        open={editOrderId !== null}
        filterOptions={filterOpts as any}
        onClose={() => setEditOrderId(null)}
        onSaved={() => { setEditOrderId(null); fetchOrders(); }}
      />
    </MainLayout>
  );
};

export default OrdersPage;
