import React, { useState, useEffect, useCallback, useRef } from 'react';
import MainLayout from '../layouts/MainLayout';
import ProductDetailsModal from '../components/products/ProductDetailsModal';
import ProductNumberLink from '../components/products/ProductNumberLink';
import CollectionCollageDialog, { type CollectionPlatform } from '../components/products/CollectionCollageDialog';
import {
  statisticsService,
  type SalesStatsResponse,
  type ShipmentsStatsResponse,
  type SuppliersStatsResponse,
  type SummaryStats,
  type SupplierTotalData,
  type DeliveriesListResponse,
  type DeliveryDetailStats,
  type SupplierDetailStats,
  type ClientsStatsResponse,
  type ProductsStatsResponse,
  type CatalogStatsResponse,
  type CatalogProductStat,
} from '../services/statisticsService';
import {
  BarChart, Bar, LineChart, Line, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ComposedChart,
} from 'recharts';

// ── Helpers ──────────────────────────────────────────────────────────────────
const fmtNum = (n: number) => n.toLocaleString('uk-UA', { maximumFractionDigits: 0 });
const fmtPrice = (n: number) => n.toLocaleString('uk-UA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtShort = (n: number) => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
};

const COLORS = {
  revenue: '#10b981',
  cost: '#f59e0b',
  advertising: '#ef4444',
  profit: '#6366f1',
  orders: '#3b82f6',
  items: '#8b5cf6',
  avgPrice: '#ec4899',
  sellRate: '#14b8a6',
  shipments: '#f97316',
};

type PeriodType = 'month' | 'quarter' | 'year';
type CatalogSort = 'popular' | 'views' | 'favorites' | 'sales';
type AutoCollectionDraft = {
  platform: CollectionPlatform;
  product_ids: number[];
  selected: Array<{ productnumber: string; popularity_score: number }>;
  reserves: Array<{ productnumber: string; popularity_score: number }>;
  warnings: string[];
  policy: { count: number; period_days: number; cooldown_days: number };
  audit: { eligible_pool: number; cooldown_skipped: number; no_photo_skipped: number; selection_key: string };
};

// ── Period Selector ──────────────────────────────────────────────────────────
const PeriodSelector: React.FC<{
  period: PeriodType;
  setPeriod: (p: PeriodType) => void;
  year: number | undefined;
  setYear: (y: number | undefined) => void;
  years: number[];
  showTotal?: boolean;
  periodValue?: string;
  setPeriodValue?: (p: string) => void;
}> = ({ period, setPeriod, year, setYear, years, showTotal, periodValue, setPeriodValue }) => {
  const periods: { key: string; label: string }[] = [
    ...(showTotal ? [{ key: 'total', label: 'Загалом' }] : []),
    { key: 'year', label: 'Роки' },
    { key: 'quarter', label: 'Квартали' },
    { key: 'month', label: 'Місяці' },
  ];
  const activePeriod = periodValue ?? period;
  const setActivePeriod = setPeriodValue ?? ((p: string) => setPeriod(p as PeriodType));

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <div className="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-0.5">
        {periods.map(p => (
          <button
            key={p.key}
            onClick={() => setActivePeriod(p.key)}
            className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
              activePeriod === p.key
                ? 'bg-white dark:bg-gray-600 text-gray-900 dark:text-white shadow-sm'
                : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>
      <select
        value={year ?? ''}
        onChange={e => setYear(e.target.value ? Number(e.target.value) : undefined)}
        className="text-xs border border-gray-200 dark:border-gray-600 rounded-md px-2 py-1 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200"
      >
        <option value="">Всі роки</option>
        {years.map(y => (
          <option key={y} value={y}>{y}</option>
        ))}
      </select>
    </div>
  );
};

// ── KPI Card ─────────────────────────────────────────────────────────────────
const KpiCard: React.FC<{ label: string; value: string; sub?: string; color?: string }> = ({ label, value, sub, color }) => (
  <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4 flex flex-col">
    <span className="text-xs text-gray-500 dark:text-gray-400 font-medium uppercase tracking-wide">{label}</span>
    <span className={`text-2xl font-bold mt-1 ${color || 'text-gray-900 dark:text-white'}`}>{value}</span>
    {sub && <span className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{sub}</span>}
  </div>
);

// ── Custom Tooltip ───────────────────────────────────────────────────────────
const CustomTooltip: React.FC<any> = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg p-3 text-xs">
      <p className="font-semibold text-gray-700 dark:text-gray-200 mb-1">{label}</p>
      {payload.map((p: any, i: number) => (
        <p key={i} style={{ color: p.color }} className="flex justify-between gap-4">
          <span>{p.name}:</span>
          <span className="font-medium">{fmtPrice(p.value)}</span>
        </p>
      ))}
    </div>
  );
};

// ── Section wrapper ──────────────────────────────────────────────────────────
const Section: React.FC<{ title: string; children: React.ReactNode; controls?: React.ReactNode }> = ({ title, children, controls }) => (
  <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5 mb-6">
    <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
      <h2 className="text-lg font-semibold text-gray-900 dark:text-white">{title}</h2>
      {controls}
    </div>
    {children}
  </div>
);

// ── Main Page ────────────────────────────────────────────────────────────────
interface StatisticsPageProps {
  currentSearchTerm: string;
}

const StatisticsPage: React.FC<StatisticsPageProps> = () => {
  const [years, setYears] = useState<number[]>([]);
  const [summary, setSummary] = useState<SummaryStats | null>(null);
  const [cardProductId, setCardProductId] = useState<number | null>(null);

  // Sales state
  const [salesPeriod, setSalesPeriod] = useState<PeriodType>('month');
  const [salesYear, setSalesYear] = useState<number | undefined>(undefined);
  const [salesData, setSalesData] = useState<SalesStatsResponse | null>(null);
  const [salesLoading, setSalesLoading] = useState(false);

  // Shipments state
  const [shipPeriod, setShipPeriod] = useState<PeriodType>('month');
  const [shipYear, setShipYear] = useState<number | undefined>(undefined);
  const [shipData, setShipData] = useState<ShipmentsStatsResponse | null>(null);
  const [shipLoading, setShipLoading] = useState(false);
  const [shipMetric, setShipMetric] = useState<'total_cost' | 'avg_price' | 'revenue' | 'sell_rate'>('total_cost');

  // Suppliers state
  const [supPeriod, setSupPeriod] = useState<string>('total');
  const [supYear, setSupYear] = useState<number | undefined>(undefined);
  const [supData, setSupData] = useState<SuppliersStatsResponse | null>(null);
  const [supLoading, setSupLoading] = useState(false);
  const [supMetric, setSupMetric] = useState<'total_cost' | 'avg_price'>('total_cost');

  // Deliveries list state
  const [delData, setDelData] = useState<DeliveriesListResponse | null>(null);
  const [delLoading, setDelLoading] = useState(false);
  const [delPage, setDelPage] = useState(1);
  const [delDetail, setDelDetail] = useState<DeliveryDetailStats | null>(null);
  const [delDetailLoading, setDelDetailLoading] = useState(false);
  const [delDetailId, setDelDetailId] = useState<number | null>(null);

  // Supplier detail state
  const [supDetailId, setSupDetailId] = useState<number | null>(null);
  const [supDetail, setSupDetail] = useState<SupplierDetailStats | null>(null);
  const [supDetailLoading, setSupDetailLoading] = useState(false);

  // Client statistics state
  const [clientStats, setClientStats] = useState<ClientsStatsResponse | null>(null);
  const [clientStatsLoading, setClientStatsLoading] = useState(false);

  // Products statistics state
  const [productStats, setProductStats] = useState<ProductsStatsResponse | null>(null);
  const [productStatsLoading, setProductStatsLoading] = useState(false);
  const [catalogDays, setCatalogDays] = useState(30);
  const [catalogStats, setCatalogStats] = useState<CatalogStatsResponse | null>(null);
  const [catalogStatsLoading, setCatalogStatsLoading] = useState(false);
  const [catalogSort, setCatalogSort] = useState<CatalogSort>('popular');
  const [autoCollectionDraft, setAutoCollectionDraft] = useState<AutoCollectionDraft | null>(null);
  const [autoCollectionLoading, setAutoCollectionLoading] = useState<CollectionPlatform | null>(null);
  const [autoCollectionError, setAutoCollectionError] = useState<string | null>(null);
  const salesRequestRef = useRef(0);
  const shipmentsRequestRef = useRef(0);
  const suppliersRequestRef = useRef(0);
  const deliveriesRequestRef = useRef(0);
  const catalogRequestRef = useRef(0);

  // Load years + summary
  useEffect(() => {
    statisticsService.getYears().then(r => setYears(r.years)).catch(console.error);
    statisticsService.getSummary().then(setSummary).catch(console.error);
  }, []);

  // Load sales data
  const loadSales = useCallback(async () => {
    const requestId = ++salesRequestRef.current;
    setSalesLoading(true);
    try {
      const res = await statisticsService.getSalesStats(salesPeriod, salesYear);
      if (requestId === salesRequestRef.current) setSalesData(res);
    } catch (e) { console.error(e); }
    finally { if (requestId === salesRequestRef.current) setSalesLoading(false); }
  }, [salesPeriod, salesYear]);
  useEffect(() => { loadSales(); }, [loadSales]);

  // Load shipments data
  const loadShipments = useCallback(async () => {
    const requestId = ++shipmentsRequestRef.current;
    setShipLoading(true);
    try {
      const res = await statisticsService.getShipmentsStats(shipPeriod, shipYear);
      if (requestId === shipmentsRequestRef.current) setShipData(res);
    } catch (e) { console.error(e); }
    finally { if (requestId === shipmentsRequestRef.current) setShipLoading(false); }
  }, [shipPeriod, shipYear]);
  useEffect(() => { loadShipments(); }, [loadShipments]);

  // Load suppliers data
  const loadSuppliers = useCallback(async () => {
    const requestId = ++suppliersRequestRef.current;
    setSupLoading(true);
    try {
      const res = await statisticsService.getSuppliersStats(supPeriod, supYear, 15);
      if (requestId === suppliersRequestRef.current) setSupData(res);
    } catch (e) { console.error(e); }
    finally { if (requestId === suppliersRequestRef.current) setSupLoading(false); }
  }, [supPeriod, supYear]);
  useEffect(() => { loadSuppliers(); }, [loadSuppliers]);

  // Load deliveries list
  const loadDeliveries = useCallback(async () => {
    const requestId = ++deliveriesRequestRef.current;
    setDelLoading(true);
    try {
      const res = await statisticsService.getDeliveriesList(delPage, 15);
      if (requestId === deliveriesRequestRef.current) setDelData(res);
    } catch (e) { console.error(e); }
    finally { if (requestId === deliveriesRequestRef.current) setDelLoading(false); }
  }, [delPage]);
  useEffect(() => { loadDeliveries(); }, [loadDeliveries]);

  // Load delivery detail
  useEffect(() => {
    if (delDetailId === null) { setDelDetail(null); return; }
    let cancelled = false;
    setDelDetailLoading(true);
    statisticsService.getDeliveryDetail(delDetailId)
      .then((data) => { if (!cancelled) setDelDetail(data); })
      .catch(console.error)
      .finally(() => { if (!cancelled) setDelDetailLoading(false); });
    return () => { cancelled = true; };
  }, [delDetailId]);

  // Load supplier detail
  useEffect(() => {
    if (supDetailId === null) { setSupDetail(null); return; }
    let cancelled = false;
    setSupDetailLoading(true);
    statisticsService.getSupplierDetail(supDetailId)
      .then((data) => { if (!cancelled) setSupDetail(data); })
      .catch(console.error)
      .finally(() => { if (!cancelled) setSupDetailLoading(false); });
    return () => { cancelled = true; };
  }, [supDetailId]);

  // Load client statistics
  const loadClientStats = useCallback(async () => {
    setClientStatsLoading(true);
    try {
      const res = await statisticsService.getClientsStats(15);
      setClientStats(res);
    } catch (e) { console.error(e); }
    finally { setClientStatsLoading(false); }
  }, []);
  useEffect(() => { loadClientStats(); }, [loadClientStats]);

  // Load product statistics
  const loadProductStats = useCallback(async () => {
    setProductStatsLoading(true);
    try {
      const res = await statisticsService.getProductsStats(15);
      setProductStats(res);
    } catch (e) { console.error(e); }
    finally { setProductStatsLoading(false); }
  }, []);
  useEffect(() => { loadProductStats(); }, [loadProductStats]);

  const loadCatalogStats = useCallback(async () => {
    const requestId = ++catalogRequestRef.current;
    setCatalogStatsLoading(true);
    try {
      const res = await statisticsService.getCatalogStats(catalogDays, 100);
      if (requestId === catalogRequestRef.current) setCatalogStats(res);
    } catch (e) { console.error(e); }
    finally { if (requestId === catalogRequestRef.current) setCatalogStatsLoading(false); }
  }, [catalogDays]);
  useEffect(() => { loadCatalogStats(); }, [loadCatalogStats]);

  // Перший запит може лише запустити фонове повернення даних із Neon. Короткий
  // повтор підхоплює свіжий зріз без блокування всієї сторінки статистики.
  useEffect(() => {
    if (!catalogStats?.sync.syncing) return;
    const timer = window.setInterval(loadCatalogStats, 2500);
    return () => window.clearInterval(timer);
  }, [catalogStats?.sync.syncing, loadCatalogStats]);

  const sortedCatalogProducts = React.useMemo(() => {
    if (!catalogStats) return [];
    const key: Record<CatalogSort, keyof CatalogProductStat> = {
      popular: 'popularity_score', views: 'views',
      favorites: 'active_favorites', sales: 'sold_count',
    };
    return [...catalogStats.top_products].sort((a, b) =>
      Number(b[key[catalogSort]]) - Number(a[key[catalogSort]])
      || b.views - a.views
      || a.productnumber.localeCompare(b.productnumber)
    );
  }, [catalogStats, catalogSort]);

  const openAutoCollectionDraft = async (platform: CollectionPlatform) => {
    if (autoCollectionLoading) return;
    setAutoCollectionLoading(platform);
    setAutoCollectionError(null);
    try {
      const params = new URLSearchParams({
        platform, count: '9', period_days: String(catalogDays), cooldown_days: '14',
      });
      const response = await fetch(`/api/publications/collections/auto-draft?${params}`);
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Не вдалося сформувати автоматичну чернетку');
      setAutoCollectionDraft(result);
    } catch (error: any) {
      setAutoCollectionError(error.message || 'Не вдалося сформувати автоматичну чернетку');
    } finally {
      setAutoCollectionLoading(null);
    }
  };

  const shipMetrics = [
    { key: 'total_cost', label: 'Вартість завозу' },
    { key: 'avg_price', label: 'Сер. ціна пари' },
    { key: 'revenue', label: 'Виторг від продажу' },
    { key: 'sell_rate', label: 'Ефективність продажу %' },
  ];

  const supMetrics = [
    { key: 'total_cost', label: 'Загальна вартість' },
    { key: 'avg_price', label: 'Середня ціна товару' },
  ];

  return (
    <MainLayout
      filterPanelContent={
        <div className="space-y-4">
          <h3 className="text-md font-semibold text-gray-700 dark:text-gray-200">Фільтри статистики</h3>
          <p className="text-xs text-gray-400 dark:text-gray-500">
            Використовуйте селектори періодів та років безпосередньо на графіках для налаштування відображення.
          </p>
        </div>
      }
      onRefresh={() => { loadSales(); loadShipments(); loadSuppliers(); loadDeliveries(); loadClientStats(); loadProductStats(); loadCatalogStats(); statisticsService.getSummary().then(setSummary); }}
      isRefreshing={salesLoading || shipLoading || supLoading || delLoading || clientStatsLoading || productStatsLoading || catalogStatsLoading}
      onResetFilters={() => {
        setSalesPeriod('month'); setSalesYear(undefined);
        setShipPeriod('month'); setShipYear(undefined);
        setSupPeriod('total'); setSupYear(undefined);
        setCatalogDays(30); setCatalogSort('popular');
      }}
    >
      <div className="space-y-6">
        {/* ── KPI Cards ─────────────────────────────────────────────── */}
        {summary && (
          <div>
            <div className="flex items-center gap-2 mb-3">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Загальні показники</h2>
              <span className="text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 px-2 py-0.5 rounded-full">
                За весь час ({years.length > 0 ? `${years[0]}–${years[years.length - 1]}` : '...'})
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
              <KpiCard
                label="Всього товарів"
                value={fmtNum(summary.total_products)}
                sub={`Продано повністю: ${fmtNum(summary.products_fully_sold)} • Частково: ${fmtNum(summary.products_partially_sold)} • Залишок: ${fmtNum(summary.products_unsold)}`}
              />
              <KpiCard
                label="Виторг (оплачено)"
                value={fmtPrice(summary.total_revenue)}
                sub={`${fmtNum(summary.paid_orders)} оплачених із ${fmtNum(summary.confirmed_orders)} підтверджених замовлень`}
                color="text-emerald-600"
              />
              <KpiCard
                label="Чистий прибуток"
                value={fmtPrice(summary.net_profit)}
                sub={`Виторг ${fmtShort(summary.total_revenue)} − Собівартість ${fmtShort(summary.total_purchase_cost)} − Доставка ${fmtShort(summary.total_delivery_cost)} − Реклама ${fmtShort(summary.total_advertising_cost)}`}
                color={summary.net_profit >= 0 ? 'text-indigo-600' : 'text-red-600'}
              />
              <KpiCard
                label="Реклама (ефір)"
                value={fmtPrice(summary.total_advertising_cost)}
                sub="Віднято від фінального чистого прибутку"
                color="text-red-600"
              />
              <KpiCard
                label="Потенц. виторг залишку"
                value={fmtPrice(summary.unsold_inventory_cost)}
                sub={`${fmtNum(summary.products_unsold + summary.products_partially_sold)} товарів зі стоком (за продажною)`}
                color="text-amber-600"
              />
            </div>
          </div>
        )}

        {/* ── Public catalog analytics ─────────────────────────────── */}
        <Section
          title="Інтернет-вітрина"
          controls={
            <div className="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-0.5">
              {[
                { days: 7, label: '7 днів' },
                { days: 30, label: '30 днів' },
                { days: 90, label: '90 днів' },
                { days: 0, label: 'Весь чистий період' },
              ].map(p => (
                <button key={p.days} onClick={() => setCatalogDays(p.days)}
                  className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${catalogDays === p.days
                    ? 'bg-white dark:bg-gray-600 text-gray-900 dark:text-white shadow-sm'
                    : 'text-gray-500 dark:text-gray-400 hover:text-gray-700'}`}>
                  {p.label}
                </button>
              ))}
            </div>
          }
        >
          {catalogStatsLoading && !catalogStats ? (
            <div className="h-48 flex items-center justify-center text-gray-400">Завантаження...</div>
          ) : catalogStats ? (
            <div className="space-y-6">
              <div className={`rounded-lg border px-3 py-2 text-xs ${catalogStats.sync.last_error
                ? 'border-amber-200 bg-amber-50 text-amber-800 dark:bg-amber-900/20 dark:border-amber-800 dark:text-amber-300'
                : 'border-blue-100 bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:border-blue-800 dark:text-blue-300'}`}>
                {catalogStats.sync.syncing
                  ? 'Оновлюю дані з вітрини… Поточний зріз уже можна переглядати.'
                  : catalogStats.tracking_started_at
                    ? `Чиста статистика рахується з ${new Date(catalogStats.tracking_started_at).toLocaleString('uk-UA')}. Остання синхронізація: ${catalogStats.sync.last_synced_at ? new Date(catalogStats.sync.last_synced_at).toLocaleString('uk-UA') : 'очікується'}.`
                    : 'Чистий підрахунок увімкнено. Перші реальні відвідування з’являться після оновлення вітрини.'}
                {catalogStats.legacy.total_views > 0 && (
                  <span className="ml-1">Старі {fmtNum(catalogStats.legacy.total_views)} технічних переглядів збережені для аудиту, але рейтинг не спотворюють.</span>
                )}
              </div>

              <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
                <KpiCard label="Відвідувачі" value={fmtNum(catalogStats.summary.visitors)} sub="унікальні" />
                <KpiCard label="Сесії" value={fmtNum(catalogStats.summary.sessions)} sub="відкриття вітрини" />
                <KpiCard label="Перегляди товарів" value={fmtNum(catalogStats.summary.product_views)} sub={`${fmtNum(catalogStats.summary.viewed_products)} товарів`} />
                <KpiCard label="Активні лайки" value={fmtNum(catalogStats.summary.active_favorites)} sub={`+${fmtNum(catalogStats.summary.favorite_adds)} за період`} color="text-rose-600" />
                <KpiCard label="Звернення" value={fmtNum(catalogStats.summary.contact_clicks)} sub={`${catalogStats.summary.contact_rate}% від переглядів`} color="text-blue-600" />
                <KpiCard label="Частка лайків" value={`${catalogStats.summary.like_rate}%`} sub="додавань / переглядів" color="text-indigo-600" />
              </div>

              {catalogStats.trend.length > 0 && (
                <div>
                  <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">Динаміка інтересу</h3>
                  <ResponsiveContainer width="100%" height={260}>
                    <ComposedChart data={catalogStats.trend} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={(v) => new Date(v).toLocaleDateString('uk-UA', { day: '2-digit', month: '2-digit' })} />
                      <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                      <Tooltip labelFormatter={(v) => new Date(v).toLocaleDateString('uk-UA')} />
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                      <Bar dataKey="views" name="Перегляди товарів" fill="#6366f1" radius={[3, 3, 0, 0]} />
                      <Line dataKey="visitors" name="Відвідувачі" stroke="#10b981" strokeWidth={2} dot={{ r: 2 }} />
                      <Line dataKey="favorite_adds" name="Нові лайки" stroke="#ec4899" strokeWidth={2} dot={{ r: 2 }} />
                      <Line dataKey="contact_clicks" name="Звернення" stroke="#f59e0b" strokeWidth={2} dot={{ r: 2 }} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              )}

              <div>
                <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
                  <div>
                    <h3 className="text-sm font-medium text-gray-700 dark:text-gray-200">Топ товарів</h3>
                    <p className="text-[11px] text-gray-400">Продані товари лишаються у статистиці, але позначаються непридатними для майбутнього автопосту.</p>
                  </div>
                  <div className="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-0.5">
                    {[
                      ['popular', 'Популярні'], ['views', 'Перегляди'],
                      ['favorites', 'Лайки'], ['sales', 'Продажі'],
                    ].map(([key, label]) => (
                      <button key={key} onClick={() => setCatalogSort(key as CatalogSort)}
                        className={`px-2.5 py-1 text-xs rounded-md ${catalogSort === key
                          ? 'bg-white dark:bg-gray-600 shadow-sm text-gray-900 dark:text-white'
                          : 'text-gray-500 dark:text-gray-400'}`}>
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
                {sortedCatalogProducts.length > 0 ? (
                  <div className="overflow-x-auto rounded-lg border border-gray-100 dark:border-gray-700">
                    <table className="w-full text-xs">
                      <thead className="bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-300">
                        <tr>
                          <th className="px-3 py-2 text-left">#</th>
                          <th className="px-3 py-2 text-left">Товар</th>
                          <th className="px-3 py-2 text-right">Перегляди</th>
                          <th className="px-3 py-2 text-right">Лайки</th>
                          <th className="px-3 py-2 text-right">Звернення</th>
                          <th className="px-3 py-2 text-right">Продано</th>
                          <th className="px-3 py-2 text-right">Залишок</th>
                          <th className="px-3 py-2 text-left">Автопідбірка</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedCatalogProducts.slice(0, 30).map((p, i) => (
                          <tr key={p.productnumber} className="border-t border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/60">
                            <td className="px-3 py-2 text-gray-400">{i + 1}</td>
                            <td className="px-3 py-2 min-w-[190px]">
                              <ProductNumberLink productNumber={p.productnumber} onOpen={setCardProductId} />
                              <div className="text-[10px] text-gray-400 truncate max-w-[240px]">{[p.brand, p.model, p.type].filter(Boolean).join(' · ') || 'Без назви'}</div>
                            </td>
                            <td className="px-3 py-2 text-right font-medium">{fmtNum(p.views)} <span className="text-[9px] text-gray-400">({fmtNum(p.unique_viewers)} ос.)</span></td>
                            <td className="px-3 py-2 text-right text-rose-600 font-medium">{fmtNum(p.active_favorites)}</td>
                            <td className="px-3 py-2 text-right">{fmtNum(p.contact_clicks)}</td>
                            <td className="px-3 py-2 text-right">{fmtNum(p.sold_count)}</td>
                            <td className="px-3 py-2 text-right">{fmtNum(p.available)}</td>
                            <td className="px-3 py-2">
                              <span className={`px-2 py-0.5 rounded-full text-[10px] ${p.eligible_for_autopost
                                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
                                : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'}`}>
                                {p.eligible_for_autopost ? 'Можна' : p.available <= 0 ? 'Продано' : !p.published ? 'Не у вітрині' : 'Не можна'}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="h-32 flex items-center justify-center text-gray-400 text-sm">Ще немає чистих даних за цей період</div>
                )}
              </div>

              <div className="rounded-xl border border-indigo-100 bg-indigo-50/60 p-4 dark:border-indigo-900 dark:bg-indigo-950/20">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-indigo-900 dark:text-indigo-200">Автоматична Top‑9 · перевірка відбору</h3>
                    <p className="mt-1 max-w-3xl text-xs leading-relaxed text-indigo-700/80 dark:text-indigo-300/80">
                      Рейтинг за обраний вище період, тільки опубліковані товари в наявності з фото.
                      Товар із будь-якої підбірки за останні 14 днів пропускається, наступний кандидат займає його місце.
                      На цьому етапі доступні лише точний preview та збереження JPEG — публікація вимкнена.
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button type="button" onClick={() => void openAutoCollectionDraft('viber')}
                      disabled={autoCollectionLoading !== null}
                      className="rounded-lg bg-[#7360F2] px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">
                      {autoCollectionLoading === 'viber' ? 'Формую…' : 'Preview Top‑9 для Viber'}
                    </button>
                    <button type="button" onClick={() => void openAutoCollectionDraft('facebook')}
                      disabled={autoCollectionLoading !== null}
                      className="rounded-lg bg-[#1877F2] px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">
                      {autoCollectionLoading === 'facebook' ? 'Формую…' : 'Preview Top‑9 для Facebook'}
                    </button>
                  </div>
                </div>
                {autoCollectionError && (
                  <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">
                    {autoCollectionError}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="h-40 flex items-center justify-center text-gray-400">Статистика вітрини тимчасово недоступна</div>
          )}
        </Section>

        {/* ── 1. Sales / Revenue ────────────────────────────────────── */}
        <Section
          title="Продажі / Виторг"
          controls={
            <PeriodSelector
              period={salesPeriod} setPeriod={setSalesPeriod}
              year={salesYear} setYear={setSalesYear}
              years={years}
            />
          }
        >
          {salesLoading ? (
            <div className="h-80 flex items-center justify-center text-gray-400">Завантаження...</div>
          ) : salesData && salesData.data.length > 0 ? (
            <div className="space-y-6">
              {/* Revenue + Cost + Profit bar chart */}
              <div>
                <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">Виторг / Витрати / Чистий прибуток</h3>
                <ResponsiveContainer width="100%" height={320}>
                  <ComposedChart data={salesData.data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={fmtShort} />
                    <Tooltip content={<CustomTooltip />} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar dataKey="revenue" name="Виторг" fill={COLORS.revenue} radius={[4, 4, 0, 0]} />
                    <Bar dataKey="cost" name="Собівартість" fill={COLORS.cost} radius={[4, 4, 0, 0]} />
                    <Bar dataKey="advertising_cost" name="Реклама (ефір)" fill={COLORS.advertising} radius={[4, 4, 0, 0]} />
                    <Line dataKey="profit" name="Чистий прибуток" stroke={COLORS.profit} strokeWidth={2} dot={{ r: 3 }} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              {/* Orders count + Items sold */}
              <div>
                <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">Кількість замовлень / Одиниць продано</h3>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={salesData.data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar dataKey="orders" name="Замовлень" fill={COLORS.orders} radius={[4, 4, 0, 0]} />
                    <Bar dataKey="items_sold" name="Одиниць продано" fill={COLORS.items} radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <div className="h-40 flex items-center justify-center text-gray-400 text-sm">Немає даних за обраний період</div>
          )}
        </Section>

        {/* ── 2. Shipments ──────────────────────────────────────────── */}
        <Section
          title="Завози товару"
          controls={
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-0.5">
                {shipMetrics.map(m => (
                  <button
                    key={m.key}
                    onClick={() => setShipMetric(m.key as any)}
                    className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                      shipMetric === m.key
                        ? 'bg-white dark:bg-gray-600 text-gray-900 dark:text-white shadow-sm'
                        : 'text-gray-500 dark:text-gray-400 hover:text-gray-700'
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              <PeriodSelector
                period={shipPeriod} setPeriod={setShipPeriod}
                year={shipYear} setYear={setShipYear}
                years={years}
              />
            </div>
          }
        >
          {shipLoading ? (
            <div className="h-80 flex items-center justify-center text-gray-400">Завантаження...</div>
          ) : shipData && shipData.data.length > 0 ? (
            <ResponsiveContainer width="100%" height={360}>
              <ComposedChart data={shipData.data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                <YAxis
                  tick={{ fontSize: 11 }}
                  tickFormatter={shipMetric === 'sell_rate' ? (v: number) => `${v}%` : fmtShort}
                />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {shipMetric === 'total_cost' && (
                  <>
                    <Bar dataKey="total_cost" name="Вартість завозу" fill={COLORS.cost} radius={[4, 4, 0, 0]} />
                    <Line dataKey="revenue" name="Виторг від продажу" stroke={COLORS.revenue} strokeWidth={2} dot={{ r: 3 }} />
                  </>
                )}
                {shipMetric === 'avg_price' && (
                  <Area dataKey="avg_price" name="Сер. ціна пари" fill={COLORS.avgPrice} fillOpacity={0.2} stroke={COLORS.avgPrice} strokeWidth={2} />
                )}
                {shipMetric === 'revenue' && (
                  <>
                    <Bar dataKey="revenue" name="Виторг" fill={COLORS.revenue} radius={[4, 4, 0, 0]} />
                    <Line dataKey="profit" name="Прибуток" stroke={COLORS.profit} strokeWidth={2} dot={{ r: 3 }} />
                  </>
                )}
                {shipMetric === 'sell_rate' && (
                  <Area dataKey="sell_rate" name="Ефективність продажу %" fill={COLORS.sellRate} fillOpacity={0.2} stroke={COLORS.sellRate} strokeWidth={2} />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-40 flex items-center justify-center text-gray-400 text-sm">Немає даних за обраний період</div>
          )}
        </Section>

        {/* ── 3. Suppliers ──────────────────────────────────────────── */}
        <Section
          title="Постачальники"
          controls={
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-0.5">
                {supMetrics.map(m => (
                  <button
                    key={m.key}
                    onClick={() => setSupMetric(m.key as any)}
                    className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                      supMetric === m.key
                        ? 'bg-white dark:bg-gray-600 text-gray-900 dark:text-white shadow-sm'
                        : 'text-gray-500 dark:text-gray-400 hover:text-gray-700'
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              <PeriodSelector
                period={supPeriod as PeriodType}
                setPeriod={(p) => setSupPeriod(p)}
                year={supYear} setYear={setSupYear}
                years={years}
                showTotal
                periodValue={supPeriod}
                setPeriodValue={setSupPeriod}
              />
            </div>
          }
        >
          {supLoading ? (
            <div className="h-80 flex items-center justify-center text-gray-400">Завантаження...</div>
          ) : supData && supData.data.length > 0 ? (
            supPeriod === 'total' ? (
              // Horizontal bar chart for overall supplier stats
              <ResponsiveContainer width="100%" height={Math.max(360, (supData.data as SupplierTotalData[]).length * 36)}>
                <BarChart
                  data={(supData.data as SupplierTotalData[]).map(d => ({
                    ...d,
                    displayName: d.name.length > 20 ? d.name.slice(0, 18) + '…' : d.name,
                  }))}
                  layout="vertical"
                  margin={{ top: 5, right: 30, bottom: 5, left: 120 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={fmtShort} />
                  <YAxis type="category" dataKey="displayName" tick={{ fontSize: 11 }} width={110} />
                  <Tooltip content={<CustomTooltip />} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  {supMetric === 'total_cost' && (
                    <>
                      <Bar dataKey="total_cost" name="Закупка" fill={COLORS.cost} radius={[0, 4, 4, 0]} />
                      <Bar dataKey="revenue" name="Виторг" fill={COLORS.revenue} radius={[0, 4, 4, 0]} />
                    </>
                  )}
                  {supMetric === 'avg_price' && (
                    <Bar dataKey="avg_price" name="Сер. ціна" fill={COLORS.avgPrice} radius={[0, 4, 4, 0]} />
                  )}
                </BarChart>
              </ResponsiveContainer>
            ) : (
              // Grouped time series for suppliers by period
              (() => {
                // Pivot data: period → { supplier1: cost, supplier2: cost, ... }
                const periodMap = supPeriod === 'total' ? {} :
                  (supData.data as any[]).reduce((acc: any, d: any) => {
                    if (!acc[d.period_label]) acc[d.period_label] = { period: d.period_label };
                    acc[d.period_label][d.supplier_name] = supMetric === 'avg_price' ? d.avg_price : d.total_cost;
                    return acc;
                  }, {} as Record<string, any>);
                const chartData = Object.values(periodMap) as any[];
                // Get top suppliers by sum
                const supTotals: Record<string, number> = {};
                (supData.data as any[]).forEach((d: any) => {
                  const val = supMetric === 'avg_price' ? d.avg_price : d.total_cost;
                  supTotals[d.supplier_name] = (supTotals[d.supplier_name] || 0) + val;
                });
                const topSuppliers = Object.entries(supTotals)
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 8)
                  .map(e => e[0]);
                const palette = ['#10b981', '#f59e0b', '#6366f1', '#3b82f6', '#ec4899', '#f97316', '#14b8a6', '#8b5cf6'];
                return (
                  <ResponsiveContainer width="100%" height={360}>
                    <BarChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={fmtShort} />
                      <Tooltip content={<CustomTooltip />} />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      {topSuppliers.map((name, i) => (
                        <Bar key={name} dataKey={name} name={name} fill={palette[i % palette.length]} stackId="a" />
                      ))}
                    </BarChart>
                  </ResponsiveContainer>
                );
              })()
            )
          ) : (
            <div className="h-40 flex items-center justify-center text-gray-400 text-sm">Немає даних за обраний період</div>
          )}
        </Section>

        {/* ── 4. Deliveries Table ─────────────────────────────────────── */}
        <Section title="Статистика по завозах">
          {delLoading ? (
            <div className="h-40 flex items-center justify-center text-gray-400">Завантаження...</div>
          ) : delData && delData.items.length > 0 ? (
            <div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm [&_th]:text-center [&_td]:text-center">
                  <thead className="bg-gray-50 dark:bg-gray-700 border-b">
                    <tr>
                      <th className="px-3 py-2 text-left font-semibold">Назва</th>
                      <th className="px-3 py-2 text-left font-semibold">Дата</th>
                      <th className="px-3 py-2 text-left font-semibold">Постачальник</th>
                      <th className="px-3 py-2 text-right font-semibold">Пар</th>
                      <th className="px-3 py-2 text-right font-semibold">Продано%</th>
                      <th className="px-3 py-2 text-right font-semibold" title="Закупівельна вартість + доставка (з Журналу)">Собівартість</th>
                      <th className="px-3 py-2 text-right font-semibold">Виторг</th>
                      <th className="px-3 py-2 text-right font-semibold">Прибуток</th>
                    </tr>
                  </thead>
                  <tbody>
                    {delData.items.map(d => (
                      <tr
                        key={d.id}
                        className={`border-b hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer ${delDetailId === d.id ? 'bg-blue-50 dark:bg-blue-900/20' : ''}`}
                        onClick={() => setDelDetailId(delDetailId === d.id ? null : d.id)}
                      >
                        <td className="px-3 py-2 text-blue-600 hover:underline">{d.deliveryname || `#${d.id}`}</td>
                        <td className="px-3 py-2 whitespace-nowrap">{d.deliverydate ? new Date(d.deliverydate).toLocaleDateString('uk-UA') : '—'}</td>
                        <td className="px-3 py-2">{d.supplier_name || '—'}</td>
                        <td className="px-3 py-2 text-right">{d.total_pairs}</td>
                        <td className="px-3 py-2 text-right">
                          <span className={`font-medium ${d.sell_rate >= 70 ? 'text-green-600' : d.sell_rate >= 40 ? 'text-yellow-600' : 'text-red-500'}`}>
                            {d.sell_rate}%
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right whitespace-nowrap text-amber-600" title={d.cost_estimated ? `Оцінка (закупівля в журналі не вказана): ~${fmtPrice(d.purchase_cost)} + Доставка: ${fmtPrice(d.delivery_cost)}` : `Закупівля: ${fmtPrice(d.purchase_cost)} + Доставка: ${fmtPrice(d.delivery_cost)}`}>
                          {d.purchase_cost > 0
                            ? `${d.cost_estimated ? '~' : ''}${fmtPrice(d.purchase_cost + d.delivery_cost)}`
                            : <span className="text-gray-300 text-xs">—</span>}
                        </td>
                        <td className="px-3 py-2 text-right whitespace-nowrap">{fmtPrice(d.revenue)}</td>
                        <td className={`px-3 py-2 text-right whitespace-nowrap font-medium ${d.profit >= 0 ? 'text-green-600' : 'text-red-500'}`}>{fmtPrice(d.profit)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              {delData.pages > 1 && (
                <div className="flex justify-center items-center gap-2 mt-3">
                  <button
                    disabled={delPage <= 1}
                    onClick={() => setDelPage(p => p - 1)}
                    className="px-2 py-1 text-xs border rounded disabled:opacity-30"
                  >Назад</button>
                  <span className="text-xs text-gray-500">Сторінка {delPage} з {delData.pages}</span>
                  <button
                    disabled={delPage >= delData.pages}
                    onClick={() => setDelPage(p => p + 1)}
                    className="px-2 py-1 text-xs border rounded disabled:opacity-30"
                  >Далі</button>
                </div>
              )}

              {/* Delivery detail panel */}
              {delDetailId !== null && (
                <div className="mt-4 p-4 bg-gray-50 dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600">
                  {delDetailLoading ? (
                    <div className="text-center text-gray-400 py-4">Завантаження деталей...</div>
                  ) : delDetail ? (
                    <div className="space-y-4">
                      <div className="flex items-center justify-between">
                        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
                          {delDetail.delivery.deliveryname || `Завоз #${delDetail.delivery.id}`}
                          {delDetail.delivery.supplier_name && <span className="text-gray-400 font-normal ml-2">({delDetail.delivery.supplier_name})</span>}
                        </h3>
                        <button onClick={() => setDelDetailId(null)} className="text-xs text-gray-400 hover:text-gray-600">Закрити</button>
                      </div>

                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <div className="text-center p-2 bg-white dark:bg-gray-800 rounded border">
                          <div className="text-lg font-bold text-gray-800 dark:text-white">{delDetail.total_pairs}</div>
                          <div className="text-[10px] text-gray-400">Всього пар</div>
                        </div>
                        <div className="text-center p-2 bg-white dark:bg-gray-800 rounded border">
                          <div className="text-lg font-bold text-green-600">{delDetail.sold_count} <span className="text-xs font-normal">({delDetail.sell_rate}%)</span></div>
                          <div className="text-[10px] text-gray-400">Продано</div>
                        </div>
                        <div className="text-center p-2 bg-white dark:bg-gray-800 rounded border">
                          <div className="text-lg font-bold text-amber-600">{fmtPrice(delDetail.cost_per_pair)}</div>
                          <div className="text-[10px] text-gray-400">Собівартість/пара</div>
                        </div>
                        <div className={`text-center p-2 bg-white dark:bg-gray-800 rounded border`}>
                          <div className={`text-lg font-bold ${delDetail.net_revenue >= 0 ? 'text-green-600' : 'text-red-500'}`}>{fmtPrice(delDetail.net_revenue)}</div>
                          <div className="text-[10px] text-gray-400">Чистий прибуток</div>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* Type distribution */}
                        {delDetail.type_distribution.length > 0 && (
                          <div>
                            <h4 className="text-xs font-semibold text-gray-500 mb-2">Розподіл типів</h4>
                            <div className="space-y-1">
                              {delDetail.type_distribution.map(t => {
                                const pct = delDetail.total_pairs > 0 ? Math.round(t.count / delDetail.total_pairs * 100) : 0;
                                return (
                                  <div key={t.type_name} className="flex items-center gap-2 text-xs">
                                    <span className="w-24 truncate text-gray-600 dark:text-gray-300">{t.type_name}</span>
                                    <div className="flex-1 bg-gray-200 dark:bg-gray-600 rounded-full h-2">
                                      <div className="bg-blue-500 h-2 rounded-full" style={{ width: `${pct}%` }} />
                                    </div>
                                    <span className="w-10 text-right text-gray-500">{t.count}</span>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}
                        {/* Size distribution */}
                        {delDetail.size_distribution.length > 0 && (
                          <div>
                            <h4 className="text-xs font-semibold text-gray-500 mb-2">Розподіл розмірів (EU)</h4>
                            <ResponsiveContainer width="100%" height={150}>
                              <BarChart data={delDetail.size_distribution} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                                <XAxis dataKey="size" tick={{ fontSize: 9 }} />
                                <YAxis tick={{ fontSize: 9 }} />
                                <Bar dataKey="count" fill="#6366f1" radius={[3, 3, 0, 0]} />
                              </BarChart>
                            </ResponsiveContainer>
                          </div>
                        )}
                      </div>
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          ) : (
            <div className="h-40 flex items-center justify-center text-gray-400 text-sm">Немає даних про завози</div>
          )}
        </Section>

        {/* ── 5. Client Statistics ────────────────────────────────────── */}
        <Section title="Статистика клієнтів">
          {clientStatsLoading ? (
            <div className="h-40 flex items-center justify-center text-gray-400">Завантаження...</div>
          ) : clientStats ? (
            <div className="space-y-6">
              {/* Top clients by revenue */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">Топ клієнтів за виторгом</h3>
                  <div className="space-y-1">
                    {clientStats.top_by_revenue.slice(0, 10).map((c, i) => (
                      <div key={c.id} className="flex items-center gap-2 text-xs py-1">
                        <span className="w-5 text-right text-gray-400 font-medium">{i + 1}.</span>
                        <span className="flex-1 truncate text-gray-700 dark:text-gray-300">{c.name}</span>
                        <span className="text-gray-400">{c.orders_count} зам.</span>
                        <span className="font-medium text-green-600 w-24 text-right">{fmtPrice(c.total_revenue)}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">Топ клієнтів за к-стю замовлень</h3>
                  <div className="space-y-1">
                    {clientStats.top_by_orders.slice(0, 10).map((c, i) => (
                      <div key={c.id} className="flex items-center gap-2 text-xs py-1">
                        <span className="w-5 text-right text-gray-400 font-medium">{i + 1}.</span>
                        <span className="flex-1 truncate text-gray-700 dark:text-gray-300">{c.name}</span>
                        <span className="font-bold text-blue-600">{c.orders_count}</span>
                        <span className="text-gray-400 w-24 text-right">{fmtPrice(c.total_revenue)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* New clients trend + Average check trend */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {clientStats.new_clients_trend.length > 0 && (
                  <div>
                    <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">Нові клієнти по місяцях</h3>
                    <ResponsiveContainer width="100%" height={200}>
                      <BarChart data={clientStats.new_clients_trend.slice(-12)} margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                        <XAxis dataKey="month" tick={{ fontSize: 10 }} />
                        <YAxis tick={{ fontSize: 10 }} />
                        <Tooltip />
                        <Bar dataKey="new_clients" name="Нових клієнтів" fill={COLORS.orders} radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
                {clientStats.avg_check_trend.length > 0 && (
                  <div>
                    <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">Середній чек (тренд)</h3>
                    <ResponsiveContainer width="100%" height={200}>
                      <AreaChart data={clientStats.avg_check_trend.slice(-12)} margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                        <XAxis dataKey="month" tick={{ fontSize: 10 }} />
                        <YAxis tick={{ fontSize: 10 }} tickFormatter={fmtShort} />
                        <Tooltip content={<CustomTooltip />} />
                        <Area dataKey="avg_check" name="Середній чек" fill={COLORS.revenue} fillOpacity={0.2} stroke={COLORS.revenue} strokeWidth={2} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>

              {/* Rating distribution */}
              {clientStats.rating_distribution.length > 0 && (
                <div>
                  <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">Розподіл рейтингів клієнтів</h3>
                  <div className="flex gap-4 flex-wrap">
                    {clientStats.rating_distribution.map(r => {
                      const colors: Record<string, string> = { excellent: 'bg-green-100 text-green-700', good: 'bg-blue-100 text-blue-700', average: 'bg-yellow-100 text-yellow-700', low: 'bg-red-100 text-red-700' };
                      const labels: Record<string, string> = { excellent: 'Відмінний (8+)', good: 'Хороший (6-8)', average: 'Середній (4-6)', low: 'Низький (<4)' };
                      return (
                        <div key={r.category} className={`flex items-center gap-2 px-3 py-2 rounded-lg ${colors[r.category] || 'bg-gray-100 text-gray-700'}`}>
                          <span className="text-xs font-medium">{labels[r.category] || r.category}</span>
                          <span className="text-lg font-bold">{r.count}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="h-40 flex items-center justify-center text-gray-400 text-sm">Немає даних</div>
          )}
        </Section>

        {/* ── 7. Product Statistics ─────────────────────────────── */}
        <Section title="Статистика по товарах">
          {productStatsLoading ? (
            <div className="h-40 flex items-center justify-center text-gray-400">Завантаження...</div>
          ) : productStats ? (
            <div className="space-y-5">
              {/* Inventory summary KPIs */}
              {productStats.inventory_summary && (
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                  {[
                    { label: 'Всього товарів', value: productStats.inventory_summary.total_products, color: 'text-blue-700' },
                    { label: 'Одиниць', value: productStats.inventory_summary.total_units, color: 'text-indigo-700' },
                    { label: 'Доступно', value: productStats.inventory_summary.fully_available, color: 'text-green-700' },
                    { label: 'Частково продано', value: productStats.inventory_summary.partially_sold, color: 'text-orange-700' },
                    { label: 'Продано повністю', value: productStats.inventory_summary.fully_sold, color: 'text-red-700' },
                    { label: 'Ростовок', value: productStats.inventory_summary.rostovkas, color: 'text-purple-700' },
                  ].map(kpi => (
                    <div key={kpi.label} className="bg-gray-50 dark:bg-gray-800 rounded-lg p-3 text-center">
                      <div className={`text-xl font-bold ${kpi.color}`}>{fmtNum(kpi.value)}</div>
                      <div className="text-[11px] text-gray-500 mt-0.5">{kpi.label}</div>
                    </div>
                  ))}
                </div>
              )}

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                {/* Top brands */}
                <div>
                  <h4 className="text-sm font-semibold text-gray-600 dark:text-gray-300 mb-2">Топ брендів (по виторгу)</h4>
                  <div className="space-y-1.5">
                    {productStats.top_brands.slice(0, 10).map((b, i) => {
                      const maxRev = productStats.top_brands[0]?.revenue || 1;
                      const pct = Math.round((b.revenue / maxRev) * 100);
                      return (
                        <div key={b.brand} className="flex items-center gap-2">
                          <span className="text-[10px] text-gray-400 w-4 text-right">{i + 1}</span>
                          <span className="text-xs font-medium text-gray-700 dark:text-gray-200 w-28 truncate">{b.brand}</span>
                          <div className="flex-1 bg-gray-100 dark:bg-gray-700 rounded-full h-4 relative">
                            <div className="h-4 bg-blue-400 rounded-full" style={{ width: `${pct}%` }} />
                            <span className="absolute inset-0 flex items-center justify-end pr-1.5 text-[9px] font-semibold text-gray-600 dark:text-gray-200">{fmtShort(b.revenue)}₴</span>
                          </div>
                          <span className="text-[10px] text-gray-400 w-8 text-right">{b.sold_count}шт</span>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Type distribution */}
                <div>
                  <h4 className="text-sm font-semibold text-gray-600 dark:text-gray-300 mb-2">Розподіл по типах товарів</h4>
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={productStats.type_distribution} layout="vertical" margin={{ left: 8, right: 40 }}>
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmtShort} />
                      <YAxis type="category" dataKey="type" tick={{ fontSize: 10 }} width={80} />
                      <Tooltip formatter={(v: any) => [`${fmtNum(Number(v))} шт`, 'Продано']} />
                      <Bar dataKey="sold_count" fill="#6366f1" radius={[0, 3, 3, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Channel distribution */}
              {productStats.channel_distribution.length > 0 && (
                <div>
                  <h4 className="text-sm font-semibold text-gray-600 dark:text-gray-300 mb-2">Канали продажу</h4>
                  <div className="flex flex-wrap gap-2">
                    {productStats.channel_distribution.map(ch => {
                      const colorMap: Record<string, string> = {
                        'Ефір': 'bg-sky-100 text-sky-700 border-sky-200',
                        'Telegram': 'bg-blue-100 text-blue-700 border-blue-200',
                        'OLX': 'bg-orange-100 text-orange-700 border-orange-200',
                        'Prom': 'bg-indigo-100 text-indigo-700 border-indigo-200',
                        'MONO': 'bg-black text-white border-black',
                        'Каталог': 'bg-emerald-100 text-emerald-700 border-emerald-200',
                        'Viber': 'bg-violet-100 text-violet-700 border-violet-200',
                        'Instagram': 'bg-pink-100 text-pink-700 border-pink-200',
                        'GRAILED': 'bg-gray-100 text-gray-700 border-gray-200',
                        'Магазин': 'bg-green-100 text-green-700 border-green-200',
                      };
                      return (
                        <div key={ch.channel} className={`flex flex-col gap-0.5 px-3 py-1.5 rounded-lg border text-xs font-medium ${colorMap[ch.channel] || 'bg-gray-100 text-gray-700 border-gray-200'}`}>
                          <div className="flex items-center gap-2">
                            <span>{ch.channel}</span>
                            <span className="font-bold">{fmtNum(ch.orders_count)}</span>
                            <span className="opacity-70">замовл.</span>
                          </div>
                          <div className="text-[10px] opacity-75">Виторг: {fmtShort(ch.revenue)}₴</div>
                          {ch.advertising_cost > 0 && (
                            <div className="text-[10px] leading-tight">
                              Реклама: −{fmtShort(ch.advertising_cost)}₴ · після реклами: {fmtShort(ch.net_revenue)}₴
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Top products */}
              <div>
                <h4 className="text-sm font-semibold text-gray-600 dark:text-gray-300 mb-2">Топ товарів по продажах</h4>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-xs [&_th]:text-center [&_td]:text-center">
                    <thead>
                      <tr className="bg-gray-50 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
                        <th className="px-2 py-1.5 text-left font-medium">#</th>
                        <th className="px-2 py-1.5 text-left font-medium">Номер</th>
                        <th className="px-2 py-1.5 text-left font-medium">Модель</th>
                        <th className="px-2 py-1.5 text-left font-medium">Бренд</th>
                        <th className="px-2 py-1.5 text-left font-medium">Тип</th>
                        <th className="px-2 py-1.5 text-right font-medium">Продано</th>
                        <th className="px-2 py-1.5 text-right font-medium">Виторг</th>
                      </tr>
                    </thead>
                    <tbody>
                      {productStats.top_products.map((p, i) => (
                        <tr key={p.productnumber} className={i % 2 === 0 ? 'bg-white dark:bg-gray-900' : 'bg-gray-50 dark:bg-gray-800'}>
                          <td className="px-2 py-1 text-gray-400">{i + 1}</td>
                          <td className="px-2 py-1 font-mono">
                            {p.productnumber ? (
                              <ProductNumberLink productNumber={p.productnumber} onOpen={setCardProductId} />
                            ) : '—'}
                          </td>
                          <td className="px-2 py-1 text-gray-700 dark:text-gray-300 max-w-[120px] truncate">{p.model || '—'}</td>
                          <td className="px-2 py-1 text-gray-600 dark:text-gray-400">{p.brand || '—'}</td>
                          <td className="px-2 py-1 text-gray-500">{p.type || '—'}</td>
                          <td className="px-2 py-1 text-right font-semibold text-indigo-700">{p.sold_count}</td>
                          <td className="px-2 py-1 text-right font-semibold text-green-700">{fmtShort(p.revenue)}₴</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          ) : (
            <div className="h-40 flex items-center justify-center text-gray-400 text-sm">Немає даних</div>
          )}
        </Section>
      </div>
      <ProductDetailsModal
        productId={cardProductId}
        open={cardProductId !== null}
        onClose={() => setCardProductId(null)}
      />
      {autoCollectionDraft && (
        <CollectionCollageDialog
          platform={autoCollectionDraft.platform}
          productIds={autoCollectionDraft.product_ids}
          previewOnly
          selectionNote={`Top‑9 сформовано за ${autoCollectionDraft.policy.period_days ? `${autoCollectionDraft.policy.period_days} днів` : 'весь чистий період'}; повтори заблоковані на ${autoCollectionDraft.policy.cooldown_days} днів. Резерв: ${autoCollectionDraft.reserves.length}.`}
          onCancel={() => setAutoCollectionDraft(null)}
          onPublish={() => undefined}
        />
      )}
    </MainLayout>
  );
};

export default StatisticsPage;
