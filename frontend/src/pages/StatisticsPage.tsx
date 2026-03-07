import React, { useState, useEffect, useCallback } from 'react';
import MainLayout from '../layouts/MainLayout';
import {
  statisticsService,
  type SalesStatsResponse,
  type ShipmentsStatsResponse,
  type SuppliersStatsResponse,
  type SummaryStats,
  type SupplierTotalData,
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
  profit: '#6366f1',
  orders: '#3b82f6',
  items: '#8b5cf6',
  avgPrice: '#ec4899',
  sellRate: '#14b8a6',
  shipments: '#f97316',
};

type PeriodType = 'month' | 'quarter' | 'year';

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

  // Load years + summary
  useEffect(() => {
    statisticsService.getYears().then(r => setYears(r.years)).catch(console.error);
    statisticsService.getSummary().then(setSummary).catch(console.error);
  }, []);

  // Load sales data
  const loadSales = useCallback(async () => {
    setSalesLoading(true);
    try {
      const res = await statisticsService.getSalesStats(salesPeriod, salesYear);
      setSalesData(res);
    } catch (e) { console.error(e); }
    finally { setSalesLoading(false); }
  }, [salesPeriod, salesYear]);
  useEffect(() => { loadSales(); }, [loadSales]);

  // Load shipments data
  const loadShipments = useCallback(async () => {
    setShipLoading(true);
    try {
      const res = await statisticsService.getShipmentsStats(shipPeriod, shipYear);
      setShipData(res);
    } catch (e) { console.error(e); }
    finally { setShipLoading(false); }
  }, [shipPeriod, shipYear]);
  useEffect(() => { loadShipments(); }, [loadShipments]);

  // Load suppliers data
  const loadSuppliers = useCallback(async () => {
    setSupLoading(true);
    try {
      const res = await statisticsService.getSuppliersStats(supPeriod, supYear, 15);
      setSupData(res);
    } catch (e) { console.error(e); }
    finally { setSupLoading(false); }
  }, [supPeriod, supYear]);
  useEffect(() => { loadSuppliers(); }, [loadSuppliers]);

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
      onRefresh={() => { loadSales(); loadShipments(); loadSuppliers(); statisticsService.getSummary().then(setSummary); }}
      isRefreshing={salesLoading || shipLoading || supLoading}
      onResetFilters={() => {
        setSalesPeriod('month'); setSalesYear(undefined);
        setShipPeriod('month'); setShipYear(undefined);
        setSupPeriod('total'); setSupYear(undefined);
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
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiCard label="Всього товарів" value={fmtNum(summary.total_products)} sub={`Продано: ${fmtNum(summary.products_sold)} | Залишок: ${fmtNum(summary.total_products - summary.products_sold)}`} />
              <KpiCard label="Загальний виторг" value={fmtPrice(summary.total_revenue)} sub={`${fmtNum(summary.total_orders)} замовлень за весь час`} color="text-emerald-600" />
              <KpiCard
                label="Чистий прибуток"
                value={fmtPrice(summary.total_revenue - summary.total_purchase_cost)}
                sub={`Виторг ${fmtShort(summary.total_revenue)} − Собівартість проданого ${fmtShort(summary.total_purchase_cost)}`}
                color="text-indigo-600"
              />
              <KpiCard
                label="Вартість залишку"
                value={fmtPrice(summary.total_inventory_cost - summary.total_purchase_cost)}
                sub={`${fmtNum(summary.total_products - summary.products_sold)} непроданих товарів`}
                color="text-amber-600"
              />
            </div>
          </div>
        )}

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
                <h3 className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">Виторг / Собівартість / Прибуток</h3>
                <ResponsiveContainer width="100%" height={320}>
                  <ComposedChart data={salesData.data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={fmtShort} />
                    <Tooltip content={<CustomTooltip />} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar dataKey="revenue" name="Виторг" fill={COLORS.revenue} radius={[4, 4, 0, 0]} />
                    <Bar dataKey="cost" name="Собівартість" fill={COLORS.cost} radius={[4, 4, 0, 0]} />
                    <Line dataKey="profit" name="Прибуток" stroke={COLORS.profit} strokeWidth={2} dot={{ r: 3 }} />
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
      </div>
    </MainLayout>
  );
};

export default StatisticsPage;
