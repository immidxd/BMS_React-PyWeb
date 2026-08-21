import React, { useCallback, useEffect, useRef, useState } from 'react';
import ProductDetailsModal from '../products/ProductDetailsModal';
import ProductNumberLink from '../products/ProductNumberLink';
import CollectionCollageDialog, { type CollectionPlatform } from '../products/CollectionCollageDialog';
import {
  statisticsService,
  type CatalogStatsResponse,
  type CatalogProductStat,
} from '../../services/statisticsService';
import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

/**
 * Аналітика інтернет-вітрини та автопідбірки Top-9 — один компонент на дві сторінки.
 *
 * «Статистика» показує цифри й топ товарів; «Публікації» — ті самі цифри як
 * контекст рішення плюс керування автопостингом. Розкладами й чернетками
 * звідусіль керує один бекенд, тож перенесення інтерфейсу нічого не вмикає
 * й не вимикає.
 */

const fmtNum = (n: number) => n.toLocaleString('uk-UA', { maximumFractionDigits: 0 });

const WEEKDAYS = ['Понеділок', 'Вівторок', 'Середа', 'Четвер', 'П’ятниця', 'Субота', 'Неділя'];
const platformLabel = (platform: CollectionPlatform) => platform === 'viber' ? 'Viber' : 'Facebook';
const formatDateTime = (value?: string | null) => value
  ? new Date(value).toLocaleString('uk-UA', { dateStyle: 'medium', timeStyle: 'short' })
  : '—';


const KpiCard: React.FC<{ label: string; value: string; sub?: string; color?: string }> = ({ label, value, sub, color }) => (
  <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4 flex flex-col">
    <span className="text-xs text-gray-500 dark:text-gray-400 font-medium uppercase tracking-wide">{label}</span>
    <span className={`text-2xl font-bold mt-1 ${color || 'text-gray-900 dark:text-white'}`}>{value}</span>
    {sub && <span className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{sub}</span>}
  </div>
);

const Section: React.FC<{ title: string; children: React.ReactNode; controls?: React.ReactNode }> = ({ title, children, controls }) => (
  <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5 mb-6">
    <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
      <h2 className="text-lg font-semibold text-gray-900 dark:text-white">{title}</h2>
      {controls}
    </div>
    {children}
  </div>
);

// ── Sub-tabs ─────────────────────────────────────────────────────────────────
// Everything that is not one of the four dedicated views stays on "Основна",

type CatalogSort = 'popular' | 'views' | 'favorites' | 'sales';
type AutoCollectionDraft = {
  id?: number;
  platform: CollectionPlatform;
  source?: 'scheduled' | 'manual';
  status?: 'awaiting_review' | 'approved' | 'rejected' | 'expired';
  scheduled_for?: string;
  product_ids: number[];
  product_numbers?: string[];
  selected: Array<{ productnumber: string; popularity_score: number }>;
  reserves: Array<{ productnumber: string; popularity_score: number }>;
  warnings: string[];
  policy: { count: number; period_days: number; cooldown_days: number };
  audit: {
    eligible_pool: number;
    cooldown_skipped: number;
    no_photo_skipped: number;
    selection_key: string;
    // Present on drafts built by the Cloudflare contour, absent on older rows.
    data_source?: 'live' | 'cloud_snapshot';
    snapshot_stale?: boolean;
    snapshot_age_hours?: number | null;
  };
};
type AutoCollectionConfig = {
  platform: CollectionPlatform;
  enabled: boolean;
  weekday: number;
  local_time: string;
  timezone: string;
  period_days: number;
  cooldown_days: number;
  item_count: number;
  enabled_at?: string | null;
  next_run_at?: string | null;
  last_generated_at?: string | null;
  last_error?: string | null;
  manual_review_required: true;
  automatic_publishing: false;
};
type AutoCollectionAutomation = {
  configs: AutoCollectionConfig[];
  drafts: AutoCollectionDraft[];
  pending_count: number;
  safety: { manual_review_required: true; automatic_publishing: false; media_uploads: false };
  cloud_sync?: {
    configured: boolean;
    autonomous: boolean;
    running: boolean;
    pending: boolean;
    last_success_at?: string | null;
    last_error?: string | null;
    last_error_at?: string | null;
    draft_only: true;
  };
};

type PanelProps = {
  /** Таблиця «Топ товарів» — лише у «Статистиці». */
  showTopProducts?: boolean;
  /** Керування автопідбіркою Top-9 — лише у «Публікаціях». */
  showAutomation?: boolean;
};

const CatalogAnalyticsPanel: React.FC<PanelProps> = ({
  showTopProducts = false, showAutomation = false,
}) => {
  const [cardProductId, setCardProductId] = useState<number | null>(null);
  // Гонка відповідей: користувач швидко перемикає період, і пізній запит не
  // має перезаписати результат новішого.
  const catalogRequestRef = useRef(0);
  const [catalogDays, setCatalogDays] = useState(30);
  const [catalogStats, setCatalogStats] = useState<CatalogStatsResponse | null>(null);
  const [catalogStatsLoading, setCatalogStatsLoading] = useState(false);
  const [catalogSort, setCatalogSort] = useState<CatalogSort>('popular');
  const [autoCollectionDraft, setAutoCollectionDraft] = useState<AutoCollectionDraft | null>(null);
  const [autoCollectionLoading, setAutoCollectionLoading] = useState<CollectionPlatform | null>(null);
  const [autoCollectionError, setAutoCollectionError] = useState<string | null>(null);
  const [autoAutomation, setAutoAutomation] = useState<AutoCollectionAutomation | null>(null);
  const [autoAutomationLoading, setAutoAutomationLoading] = useState(false);
  const [autoAutomationBusy, setAutoAutomationBusy] = useState<string | null>(null);
  const [autoAutomationMessage, setAutoAutomationMessage] = useState<string | null>(null);
  const [autoAutomationError, setAutoAutomationError] = useState<string | null>(null);
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

  const loadAutoAutomation = useCallback(async (quiet = false) => {
    if (!quiet) setAutoAutomationLoading(true);
    try {
      const response = await fetch('/api/publications/collections/automation?draft_limit=20');
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Не вдалося завантажити розклад чернеток');
      setAutoAutomation(result);
      setAutoAutomationError(null);
    } catch (error: any) {
      setAutoAutomationError(error.message || 'Не вдалося завантажити розклад чернеток');
    } finally {
      if (!quiet) setAutoAutomationLoading(false);
    }
  }, []);
  useEffect(() => { void loadAutoAutomation(); }, [loadAutoAutomation]);

  const editAutoConfig = (platform: CollectionPlatform, patch: Partial<AutoCollectionConfig>) => {
    setAutoAutomation(current => current ? {
      ...current,
      configs: current.configs.map(config => config.platform === platform ? { ...config, ...patch } : config),
    } : current);
    setAutoAutomationMessage(null);
  };

  const saveAutoConfig = async (config: AutoCollectionConfig) => {
    const busyKey = `save:${config.platform}`;
    setAutoAutomationBusy(busyKey);
    setAutoAutomationError(null);
    setAutoAutomationMessage(null);
    try {
      const response = await fetch(`/api/publications/collections/automation/${config.platform}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: config.enabled, weekday: config.weekday, local_time: config.local_time,
          timezone: config.timezone, period_days: config.period_days,
          cooldown_days: config.cooldown_days, item_count: 9,
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Не вдалося зберегти налаштування');
      setAutoAutomation(current => current ? {
        ...current,
        configs: current.configs.map(row => row.platform === config.platform ? result.config : row),
      } : current);
      setAutoAutomationMessage(`${platformLabel(config.platform)}: налаштування збережено.`);
    } catch (error: any) {
      setAutoAutomationError(error.message || 'Не вдалося зберегти налаштування');
    } finally {
      setAutoAutomationBusy(null);
    }
  };

  const createSavedAutoDraft = async (platform: CollectionPlatform) => {
    const busyKey = `draft:${platform}`;
    setAutoAutomationBusy(busyKey);
    setAutoAutomationError(null);
    setAutoAutomationMessage(null);
    try {
      const response = await fetch(`/api/publications/collections/automation/${platform}/drafts`, { method: 'POST' });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Не вдалося створити чернетку');
      await loadAutoAutomation(true);
      if (result.draft) setAutoCollectionDraft(result.draft);
      setAutoAutomationMessage(`${platformLabel(platform)}: чернетку створено й залишено на ручній перевірці.`);
    } catch (error: any) {
      setAutoAutomationError(error.message || 'Не вдалося створити чернетку');
    } finally {
      setAutoAutomationBusy(null);
    }
  };

  // Публікація незворотна, тому кнопка спрацьовує лише з другого натискання:
  // перше переводить її в стан підтвердження, і воно саме спадає через 6 секунд.
  const [autoApproveArmed, setAutoApproveArmed] = useState<number | null>(null);

  const approveSavedAutoDraft = async (draftId: number) => {
    if (autoApproveArmed !== draftId) {
      setAutoApproveArmed(draftId);
      window.setTimeout(
        () => setAutoApproveArmed(current => (current === draftId ? null : current)),
        6000,
      );
      return;
    }
    setAutoApproveArmed(null);
    const busyKey = `approve:${draftId}`;
    setAutoAutomationBusy(busyKey);
    setAutoAutomationError(null);
    try {
      const response = await fetch(`/api/publications/collections/automation/drafts/${draftId}/approve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Не вдалося відправити підбірку');
      await loadAutoAutomation(true);
      const notes: string[] = result.revalidation?.warnings || [];
      setAutoAutomationMessage([
        result.scheduled_at
          ? `Підбірку заплановано на ${formatDateTime(result.scheduled_at)}.`
          : 'Підбірку передано у захищену чергу — диспетчер відправить її найближчим циклом.',
        ...notes,
      ].join(' '));
    } catch (error: any) {
      setAutoAutomationError(error.message || 'Не вдалося відправити підбірку');
    } finally {
      setAutoAutomationBusy(null);
    }
  };

  const rejectSavedAutoDraft = async (draftId: number) => {
    const busyKey = `reject:${draftId}`;
    setAutoAutomationBusy(busyKey);
    setAutoAutomationError(null);
    try {
      const response = await fetch(`/api/publications/collections/automation/drafts/${draftId}/reject`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Не вдалося відхилити чернетку');
      await loadAutoAutomation(true);
      setAutoAutomationMessage('Чернетку відхилено. Її товари знову доступні для наступних відборів.');
    } catch (error: any) {
      setAutoAutomationError(error.message || 'Не вдалося відхилити чернетку');
    } finally {
      setAutoAutomationBusy(null);
    }
  };

  useEffect(() => { void loadCatalogStats(); }, [loadCatalogStats]);
  useEffect(() => { if (showAutomation) void loadAutoAutomation(); }, [showAutomation, loadAutoAutomation]);

  return (
    <>
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

                {!showAutomation && (
                  // Керування автопостингом живе в «Публікаціях»; звідси —
                  // тихий місток, а не дубль тих самих кнопок.
                  <button
                    type="button"
                    onClick={() => window.dispatchEvent(new CustomEvent(
                      'bms:switch-to-publications', { detail: { tab: 'catalog' } },
                    ))}
                    className="text-xs text-gray-500 underline decoration-dotted underline-offset-4 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
                  >
                    Автопідбірки й розклади — у «Публікаціях» →
                  </button>
                )}

                {showTopProducts && (
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
                )}

                {showAutomation && (<>
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

                  <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800/60">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Щотижневі Top‑9 чернетки</h3>
                        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-gray-500 dark:text-gray-400">
                          У заданий день BMS лише фіксує склад підбірки для ручної перевірки. JPEG не створюється,
                          файли нікуди не завантажуються, автоматична публікація структурно вимкнена.
                          Після першого ввімкнення минулі дати не надолужуються.
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {autoAutomation?.cloud_sync?.configured && (
                          <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${autoAutomation.cloud_sync.last_error
                            ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300'
                            : autoAutomation.cloud_sync.running || autoAutomation.cloud_sync.pending
                              ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                              : autoAutomation.cloud_sync.autonomous
                                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
                                : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'}`}>
                            {autoAutomation.cloud_sync.last_error
                              ? 'Хмарна синхронізація затримана'
                              : autoAutomation.cloud_sync.running || autoAutomation.cloud_sync.pending
                                ? 'Синхронізація з хмарою…'
                                : autoAutomation.cloud_sync.autonomous
                                  ? 'Хмарні чернетки 24/7'
                                  : 'Neon синхронізовано'}
                          </span>
                        )}
                        {autoAutomation?.pending_count ? (
                          <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[11px] font-semibold text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
                            Чекають перевірки: {autoAutomation.pending_count}
                          </span>
                        ) : null}
                      </div>
                    </div>

                    {autoAutomationLoading ? (
                      <div className="py-8 text-center text-xs text-gray-400">Завантаження налаштувань…</div>
                    ) : autoAutomation ? (
                      <>
                        <div className="mt-4 grid gap-3 xl:grid-cols-2">
                          {autoAutomation.configs.map(config => {
                            const accent = config.platform === 'viber' ? '#7360F2' : '#1877F2';
                            return (
                              <div key={config.platform} className="rounded-xl border border-gray-200 p-3 dark:border-gray-700">
                                <div className="flex items-center justify-between gap-3">
                                  <div className="flex items-center gap-2">
                                    <span className="flex h-7 w-7 items-center justify-center rounded-lg text-xs font-black text-white" style={{ backgroundColor: accent }}>
                                      {config.platform === 'viber' ? 'V' : 'f'}
                                    </span>
                                    <div>
                                      <div className="text-sm font-semibold text-gray-800 dark:text-gray-100">{platformLabel(config.platform)}</div>
                                      <div className="text-[10px] text-gray-400">Top‑9 · лише ручна перевірка</div>
                                    </div>
                                  </div>
                                  <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
                                    <input type="checkbox" checked={config.enabled}
                                      onChange={event => editAutoConfig(config.platform, { enabled: event.target.checked })} />
                                    {config.enabled ? 'Увімкнено' : 'Вимкнено'}
                                  </label>
                                </div>

                                <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                                  <label className="text-[10px] font-medium text-gray-500">День
                                    <select value={config.weekday}
                                      onChange={event => editAutoConfig(config.platform, { weekday: Number(event.target.value) })}
                                      className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200">
                                      {WEEKDAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}
                                    </select>
                                  </label>
                                  <label className="text-[10px] font-medium text-gray-500">Час Києва
                                    <input type="time" value={config.local_time}
                                      onChange={event => editAutoConfig(config.platform, { local_time: event.target.value })}
                                      className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200" />
                                  </label>
                                  <label className="text-[10px] font-medium text-gray-500">Рейтинг
                                    <select value={config.period_days}
                                      onChange={event => editAutoConfig(config.platform, { period_days: Number(event.target.value) })}
                                      className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200">
                                      <option value={7}>7 днів</option><option value={30}>30 днів</option>
                                      <option value={90}>90 днів</option><option value={0}>Увесь чистий період</option>
                                    </select>
                                  </label>
                                  <label className="text-[10px] font-medium text-gray-500">Без повтору
                                    <select value={config.cooldown_days}
                                      onChange={event => editAutoConfig(config.platform, { cooldown_days: Number(event.target.value) })}
                                      className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200">
                                      {[14, 21, 28, 35, 42].map(days => <option key={days} value={days}>{days} днів</option>)}
                                    </select>
                                  </label>
                                </div>

                                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 pt-3 dark:border-gray-700">
                                  <div className="text-[10px] text-gray-400">
                                    {config.enabled ? <>Наступна чернетка: <b className="text-gray-600 dark:text-gray-300">{formatDateTime(config.next_run_at)}</b></> : 'Розклад неактивний'}
                                  </div>
                                  <div className="flex flex-wrap gap-2">
                                    <button type="button" disabled={autoAutomationBusy !== null}
                                      onClick={() => void createSavedAutoDraft(config.platform)}
                                      className="rounded-lg border border-gray-200 px-2.5 py-1.5 text-[11px] font-medium text-gray-600 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300">
                                      {autoAutomationBusy === `draft:${config.platform}` ? 'Формую…' : 'Створити чернетку зараз'}
                                    </button>
                                    <button type="button" disabled={autoAutomationBusy !== null}
                                      onClick={() => void saveAutoConfig(config)}
                                      className="rounded-lg px-2.5 py-1.5 text-[11px] font-semibold text-white disabled:opacity-50"
                                      style={{ backgroundColor: accent }}>
                                      {autoAutomationBusy === `save:${config.platform}` ? 'Зберігаю…' : 'Зберегти'}
                                    </button>
                                  </div>
                                </div>
                                {config.last_error && (
                                  <div className="mt-2 rounded-lg bg-red-50 px-2.5 py-2 text-[10px] text-red-700 dark:bg-red-900/20 dark:text-red-300">
                                    Остання перевірка розкладу: {config.last_error}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>

                        {(autoAutomationMessage || autoAutomationError) && (
                          <div className={`mt-3 rounded-lg px-3 py-2 text-xs ${autoAutomationError
                            ? 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300'
                            : 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300'}`}>
                            {autoAutomationError || autoAutomationMessage}
                          </div>
                        )}

                        <div className="mt-4 border-t border-gray-100 pt-3 dark:border-gray-700">
                          <div className="mb-2 flex items-center justify-between gap-2">
                            <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-200">Журнал чернеток</h4>
                            <span className="text-[10px] text-gray-400">Жодна з них не є публікацією</span>
                          </div>
                          {autoAutomation.drafts.length ? (
                            <div className="space-y-2">
                              {autoAutomation.drafts.slice(0, 10).map(draft => {
                                const numbers = draft.product_numbers || draft.selected.map(row => row.productnumber);
                                const waiting = draft.status === 'awaiting_review';
                                return (
                                  <div key={draft.id || draft.audit.selection_key}
                                    className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-100 px-3 py-2 dark:border-gray-700">
                                    <div className="min-w-0">
                                      <div className="flex flex-wrap items-center gap-2 text-xs">
                                        <b>{platformLabel(draft.platform)}</b>
                                        <span className={`rounded-full px-2 py-0.5 text-[10px] ${waiting
                                          ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
                                          : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'}`}>
                                          {waiting ? 'Чекає ручної перевірки' : draft.status === 'rejected' ? 'Відхилено' : draft.status}
                                        </span>
                                        <span className="text-[10px] text-gray-400">{draft.source === 'scheduled' ? 'За розкладом' : 'Створено вручну'} · {formatDateTime(draft.scheduled_for)}</span>
                                      </div>
                                      <div className="mt-1 max-w-3xl truncate font-mono text-[10px] text-gray-500">{numbers.join(', ')}</div>
                                      {!!draft.warnings?.length && (
                                        <ul className="mt-1 max-w-3xl space-y-0.5">
                                          {draft.warnings.map((warning, index) => (
                                            <li key={index} className="text-[10px] leading-snug text-amber-700 dark:text-amber-300">
                                              ⚠ {warning}
                                            </li>
                                          ))}
                                        </ul>
                                      )}
                                    </div>
                                    <div className="flex gap-2">
                                      <button type="button" onClick={() => setAutoCollectionDraft(draft)}
                                        className="rounded-lg border border-gray-200 px-2.5 py-1.5 text-[11px] text-gray-600 dark:border-gray-600 dark:text-gray-300">
                                        Перевірити сітку
                                      </button>
                                      {waiting && draft.id && (
                                        <>
                                          <button type="button" disabled={autoAutomationBusy !== null}
                                            onClick={() => void approveSavedAutoDraft(draft.id!)}
                                            className={`rounded-lg px-2.5 py-1.5 text-[11px] font-medium disabled:opacity-50 ${
                                              autoApproveArmed === draft.id
                                                ? 'bg-red-600 text-white'
                                                : 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
                                            }`}>
                                            {autoAutomationBusy === `approve:${draft.id}`
                                              ? 'Відправляю…'
                                              : autoApproveArmed === draft.id
                                                ? 'Точно опублікувати?'
                                                : 'Опублікувати'}
                                          </button>
                                          <button type="button" disabled={autoAutomationBusy !== null}
                                            onClick={() => void rejectSavedAutoDraft(draft.id!)}
                                            className="rounded-lg border border-red-200 px-2.5 py-1.5 text-[11px] text-red-600 disabled:opacity-50 dark:border-red-800 dark:text-red-300">
                                            {autoAutomationBusy === `reject:${draft.id}` ? 'Відхиляю…' : 'Відхилити'}
                                          </button>
                                        </>
                                      )}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          ) : (
                            <div className="rounded-lg bg-gray-50 px-3 py-5 text-center text-xs text-gray-400 dark:bg-gray-800">Чернеток ще немає</div>
                          )}
                        </div>

                        <p className="mt-3 text-[10px] leading-relaxed text-gray-400">
                          {autoAutomation.cloud_sync?.autonomous
                            ? 'Розклад дублюється у захищеному Cloudflare-контурі й може створити чернетку навіть із вимкненою програмою. Після відкриття BMS вона автоматично з’явиться тут. Контур не має доступу до публікації, фото-сховища чи токенів соцмереж.'
                            : 'Налаштування, кандидати й чернетки вже синхронізуються з Neon. Автономний запуск 24/7 буде позначений тут окремо після підтвердженого розгортання Cloudflare Worker; до того часу BMS не прикидається, що працює у вимкненому стані.'}
                        </p>
                      </>
                    ) : autoAutomationError ? (
                      <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-900/20 dark:text-red-300">{autoAutomationError}</div>
                    ) : null}
                  </div>
                </>)}
              </div>
            ) : (
              <div className="h-40 flex items-center justify-center text-gray-400">Статистика вітрини тимчасово недоступна</div>
            )}
          </Section>
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
          ranked
          selectionNote={`${autoCollectionDraft.id ? 'Це збережена чернетка на ручній перевірці. ' : ''}Top‑9 сформовано за ${autoCollectionDraft.policy.period_days ? `${autoCollectionDraft.policy.period_days} днів` : 'весь чистий період'}; повтори заблоковані на ${autoCollectionDraft.policy.cooldown_days} днів. Резерв: ${autoCollectionDraft.reserves.length}.`}
          onCancel={() => setAutoCollectionDraft(null)}
          onPublish={() => undefined}
        />
      )}
    </>
  );
};

export default CatalogAnalyticsPanel;
