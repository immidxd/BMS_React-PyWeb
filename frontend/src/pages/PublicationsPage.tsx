import CatalogAnalyticsPanel from '../components/publications/CatalogAnalyticsPanel';
import StoryAutomationPanel from '../components/publications/StoryAutomationPanel';
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  SendOutlined, WarningOutlined, MinusCircleOutlined, DisconnectOutlined,
  AppstoreOutlined, EditOutlined, DeleteOutlined, EyeOutlined,
} from '@ant-design/icons';
import MainLayout from '../layouts/MainLayout';
import Pagination from '../components/common/Pagination';
import ProductDetailsModal from '../components/products/ProductDetailsModal';
import TelegramPublishDialog, { type TelegramPreview, type TelegramPublishPayload } from '../components/products/TelegramPublishDialog';
import TelegramBatchPublishDialog, { type TelegramBatchRequest } from '../components/products/TelegramBatchPublishDialog';
import ViberPublishDialog, { type ViberPreview, type ViberPublishPayload } from '../components/products/ViberPublishDialog';
import ViberBatchPublishDialog, { type ViberBatchRequest } from '../components/products/ViberBatchPublishDialog';
import InstagramPublishDialog, { InstagramMark, type InstagramDraftPayload, type InstagramPreview } from '../components/products/InstagramPublishDialog';
import InstagramBatchDraftDialog, { type InstagramBatchRequest } from '../components/products/InstagramBatchDraftDialog';
import FacebookPublishDialog, { FacebookMark, type FacebookDraftPayload, type FacebookPreview } from '../components/products/FacebookPublishDialog';
import { confirmDialog, alertDialog, notify } from '../ui/feedback';
import LoadingSpinner from '../components/common/LoadingSpinner';
import { taskManager } from '../services/taskManager';

/* ── Types ─────────────────────────────────────────────────────────── */

interface PublicationItem {
  product_id: number | null;
  productnumber: string;
  model: string | null;
  price: number | null;
  status: string | null;
  publication_count: number;
  channels: string;
  threads: string;
  is_unlinked?: boolean;
  needs_manual_edit?: boolean;
  telegram_publication_count?: number;
  viber_publication_count?: number;
  viber_pending_count?: number;
  instagram_publication_count?: number;
  instagram_pending_count?: number;
  instagram_permalink?: string | null;
  facebook_publication_count?: number;
  facebook_pending_count?: number;
  facebook_permalink?: string | null;
  // Розширені поля для column-selector
  brand_name?: string | null;
  type_name?: string | null;
  subtype_name?: string | null;
  sizeeu?: string | null;
  marking?: string | null;
  year?: number | null;
}

/* ── Column definitions ─────────────────────────────────────────────── */
type PubColumnId =
  | 'productnumber' | 'brand_name' | 'type_name' | 'subtype_name'
  | 'model' | 'marking' | 'year' | 'sizeeu' | 'price'
  | 'status' | 'publication_count' | 'channels' | 'actions';

const PUB_COLUMN_ORDER: { id: PubColumnId; title: string; optional: boolean }[] = [
  { id: 'productnumber',     title: 'Номер',     optional: false },
  { id: 'brand_name',        title: 'Бренд',     optional: false },
  { id: 'type_name',         title: 'Вид',       optional: false },
  { id: 'subtype_name',      title: 'Підвид',    optional: true  },
  { id: 'model',             title: 'Модель',    optional: false },
  { id: 'marking',           title: 'Маркування',optional: true  },
  { id: 'year',              title: 'Рік',       optional: true  },
  { id: 'sizeeu',            title: 'Розмір',    optional: true  },
  { id: 'price',             title: 'Ціна',      optional: true  },
  { id: 'status',            title: 'Статус',    optional: false },
  { id: 'publication_count', title: 'Постів',    optional: false },
  { id: 'channels',          title: 'Канали / Гілки', optional: false },
  { id: 'actions',           title: 'Дії',       optional: false },
];
const PUB_COLUMNS_STORAGE_KEY = 'publications_table_columns_v1';

interface PublicationDetail {
  id: number | string;
  local_publication_id?: number;
  platform?: 'telegram' | 'viber' | 'instagram' | 'facebook';
  chat_id: number;
  chat_title: string;
  chat_type: string;
  thread_id: number | null;
  thread_title: string | null;
  message_id: number;
  message_text: string;
  message_date: string | null;
  is_master: boolean;
  tg_status: string;
  is_multi_size?: boolean;
  sizes_in_post?: string;
  collage_url?: string | null;
  permalink?: string | null;
  scheduled_at?: string | null;
  error?: string | null;
}

interface PublicationStats {
  total_chats: number;
  published_products: number;
  total_posts: number;
  channel_posts: number;
  forum_posts: number;
  archive_posts: number;
  channel_products: number;
  forum_products: number;
  viber_posts: number;
  viber_products: number;
  viber_pending: number;
  instagram_posts: number;
  instagram_products: number;
  instagram_pending: number;
  facebook_posts: number;
  facebook_products: number;
  facebook_pending: number;
  sold_but_live_count: number;
  sold_but_live_telegram_count: number;
  sold_but_live_viber_count: number;
  sold_but_live_instagram_count: number;
  sold_but_live_facebook_count: number;
  unlinked_count: number;
  channels: Array<{
    chat_title: string;
    chat_type: string;
    post_count: number;
    unique_products: number;
  }>;
}

interface PublicationsPageProps {
  currentSearchTerm: string;
}

type FilterMode = 'all' | 'published' | 'pending' | 'problematic' | 'unpublished' | 'unlinked';
type PublicationPlatform = 'all' | 'telegram' | 'viber' | 'instagram' | 'facebook';

const publicationStatusLabel = (status: string) => ({
  published: 'Опубліковано',
  queued: 'У черзі',
  scheduled: 'Заплановано',
  processing: 'Публікується',
  retrying: 'Повторна спроба',
  cancelled: 'Скасовано',
  archived: 'В архіві',
  removed_manual: 'Прибрано вручну',
  failed: 'Помилка',
  error: 'Помилка',
}[status] || status);

/* ── Filter Panel ──────────────────────────────────────────────────── */

/* Секція фільтрів — та сама розкладачка, що в «Товарах» (ProductFilters),
   щоб панель зліва читалась однаково на всіх вкладках. */
const FilterSection: React.FC<{
  title: string; badge?: number; defaultOpen?: boolean; children: React.ReactNode;
}> = ({ title, badge, defaultOpen = false, children }) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-gray-100 dark:border-gray-700">
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
};

/* Режими перегляду. Раніше це були радіокнопки з емодзі-стікерами (📢 ⚠️ ○ 🔴)
   — єдине місце в програмі, де фільтр виглядав так. Тепер це рядки-стани з
   вектор-іконками й лічильником, у тон решті вкладок. */
type ModeOption = { key: FilterMode; label: string; icon: React.ReactNode; tone: string; count?: number };

const PublicationsFilterPanel: React.FC<{
  filterMode: FilterMode;
  onFilterChange: (m: FilterMode) => void;
  platform: PublicationPlatform;
  onPlatformChange: (platform: PublicationPlatform) => void;
  stats: PublicationStats | null;
}> = ({ filterMode, onFilterChange, platform, onPlatformChange, stats }) => {
  const modes: ModeOption[] = [
    { key: 'published',   label: 'Опубліковані',        icon: <SendOutlined />,       tone: 'text-sky-500',     count: stats?.published_products },
    { key: 'pending',     label: 'У черзі / заплановані', icon: <AppstoreOutlined />, tone: 'text-violet-500', count: (stats?.viber_pending || 0) + (stats?.instagram_pending || 0) + (stats?.facebook_pending || 0) },
    { key: 'problematic', label: 'Продані, але висять', icon: <WarningOutlined />,    tone: 'text-rose-500',    count:
      (stats?.sold_but_live_telegram_count || 0) + (stats?.sold_but_live_viber_count || 0)
      + (stats?.sold_but_live_instagram_count || 0) + (stats?.sold_but_live_facebook_count || 0) },
    { key: 'unpublished', label: 'Не опубліковані',     icon: <MinusCircleOutlined />, tone: 'text-gray-400' },
    { key: 'unlinked',    label: 'Незвʼязані пости',    icon: <DisconnectOutlined />, tone: 'text-amber-500',   count: stats?.unlinked_count },
    { key: 'all',         label: 'Всі товари',          icon: <AppstoreOutlined />,   tone: 'text-gray-400' },
  ];

  const platforms: Array<{ key: PublicationPlatform; label: string; short: string; count?: number }> = filterMode === 'problematic'
    ? [
        { key: 'telegram', label: 'Telegram', short: 'TG', count: stats?.sold_but_live_telegram_count },
        { key: 'instagram', label: 'Instagram', short: 'IG', count: stats?.sold_but_live_instagram_count },
        { key: 'facebook', label: 'Facebook', short: 'FB', count: stats?.sold_but_live_facebook_count },
        { key: 'viber', label: 'Viber', short: 'V', count: stats?.sold_but_live_viber_count },
      ]
    : filterMode === 'unlinked'
      ? [{ key: 'telegram', label: 'Telegram', short: 'TG', count: stats?.unlinked_count }]
      : filterMode === 'pending'
        ? [
            { key: 'all', label: 'Усі', short: 'Усі', count: (stats?.viber_pending || 0) + (stats?.instagram_pending || 0) + (stats?.facebook_pending || 0) },
            { key: 'instagram', label: 'Instagram', short: 'IG', count: stats?.instagram_pending },
            { key: 'facebook', label: 'Facebook', short: 'FB', count: stats?.facebook_pending },
            { key: 'viber', label: 'Viber', short: 'V', count: stats?.viber_pending },
          ]
        : filterMode === 'all'
          ? [{ key: 'all', label: 'Усі майданчики', short: 'Усі' }]
          : [
            { key: 'all', label: 'Усі майданчики', short: 'Усі' },
            { key: 'telegram', label: 'Telegram', short: 'TG' },
            { key: 'instagram', label: 'Instagram', short: 'IG' },
            { key: 'facebook', label: 'Facebook', short: 'FB' },
            { key: 'viber', label: 'Viber', short: 'V' },
            ];

  return (
    <div>
      <FilterSection title="Стан публікації" defaultOpen>
        <div className="space-y-1">
          {modes.map(opt => {
            const active = filterMode === opt.key;
            return (
              <button
                key={opt.key}
                type="button"
                onClick={() => onFilterChange(opt.key)}
                className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg border text-left transition-colors ${
                  active
                    ? 'bg-gray-100 dark:bg-gray-600/40 border-gray-800 dark:border-gray-300 ring-1 ring-gray-300'
                    : 'bg-white dark:bg-gray-700 border-gray-200 dark:border-gray-600 hover:border-gray-400'
                }`}
              >
                <span className={`shrink-0 ${active ? opt.tone : 'text-gray-400 dark:text-gray-500'}`}>{opt.icon}</span>
                <span className={`flex-1 min-w-0 truncate text-xs ${
                  active ? 'font-semibold text-gray-900 dark:text-gray-100' : 'text-gray-600 dark:text-gray-300'
                }`}>{opt.label}</span>
                {opt.count != null && opt.count > 0 && (
                  <span className={`shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                    opt.key === 'problematic' ? 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300'
                    : opt.key === 'unlinked' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                    : 'bg-gray-200 text-gray-600 dark:bg-gray-600 dark:text-gray-200'
                  }`}>{opt.count}</span>
                )}
              </button>
            );
          })}
        </div>
      </FilterSection>

      {filterMode !== 'all' && <FilterSection title="Майданчик" defaultOpen>
        <div className="grid grid-cols-2 gap-1.5">
          {platforms.map(opt => {
            const active = platform === opt.key;
            return (
              <button key={opt.key} type="button" onClick={() => onPlatformChange(opt.key)}
                title={opt.label}
                className={`flex min-w-0 items-center gap-1.5 rounded-lg border px-2 py-2 text-left text-xs transition-colors ${active
                  ? 'border-gray-800 bg-gray-100 font-semibold text-gray-900 ring-1 ring-gray-300 dark:border-gray-300 dark:bg-gray-600/40 dark:text-gray-100'
                  : 'border-gray-200 bg-white text-gray-600 hover:border-gray-400 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-300'}`}>
                <span className="font-bold">{opt.short}</span>
                <span className="min-w-0 flex-1 truncate">{opt.label}</span>
                {opt.count != null && opt.count > 0 && <span className="rounded-full bg-rose-100 px-1.5 py-0.5 text-[10px] font-bold text-rose-700 dark:bg-rose-900/40 dark:text-rose-300">{opt.count}</span>}
              </button>
            );
          })}
        </div>
        {filterMode === 'problematic' && (
          <p className="mt-2 text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
            {platform === 'telegram'
              ? 'BMS може зняти ці пости автоматично.'
              : platform === 'instagram'
                ? 'Видаліть пости в Instagram і підтвердьте це галочкою в BMS.'
                : platform === 'facebook'
                  ? 'Видаліть допис у Сторінці Facebook і підтвердьте це галочкою в BMS.'
                  : 'Приберіть пост у Viber вручну й підтвердьте це в BMS.'}
          </p>
        )}
      </FilterSection>}

      {stats && (
        <>
          <FilterSection title="Підсумки" defaultOpen>
            <div className="grid grid-cols-2 gap-1.5">
              {[
                { label: 'Усього постів', value: stats.total_posts, title: 'Усі активні пости, включно з копіями одного товару' },
                { label: 'Унік. товарів', value: stats.published_products, title: 'Різні привʼязані товари серед активних публікацій' },
                { label: 'У форумі', value: stats.forum_products, title: `${stats.forum_products} унікальних товарів · ${stats.forum_posts} постів із копіями по гілках` },
                { label: 'У каналі', value: stats.channel_products, title: `${stats.channel_products} унікальних товарів · ${stats.channel_posts} постів` },
                { label: 'У Viber', value: stats.viber_products, title: `${stats.viber_products} унікальних товарів · ${stats.viber_posts} живих постів` },
                { label: 'Viber у черзі', value: stats.viber_pending, title: 'Заплановані пости й незавершені повторні спроби Viber' },
                { label: 'В Instagram', value: stats.instagram_products, title: `${stats.instagram_products} унікальних товарів · ${stats.instagram_posts} опублікованих матеріалів` },
                { label: 'Instagram у черзі', value: stats.instagram_pending, title: 'Заплановані пости, Stories, Reels і повторні спроби Instagram' },
                { label: 'У Facebook', value: stats.facebook_products, title: `${stats.facebook_products} унікальних товарів · ${stats.facebook_posts} опублікованих матеріалів` },
                { label: 'Facebook у черзі', value: stats.facebook_pending, title: 'Заплановані пости, Stories, Reels і повторні спроби Facebook' },
              ].map(s => (
                <div key={s.label} title={s.title} className="px-2 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-700/50 border border-gray-100 dark:border-gray-700">
                  <div className="text-[10px] uppercase tracking-wide text-gray-400 dark:text-gray-500">{s.label}</div>
                  <div className="text-sm font-semibold text-gray-800 dark:text-gray-100">{s.value}</div>
                </div>
              ))}
            </div>
          </FilterSection>

          {stats.channels.length > 0 && (
            <FilterSection title="Канали та гілки" badge={stats.channels.length}>
              <div className="space-y-1">
                {stats.channels.map((c, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${c.chat_type === 'forum' ? 'bg-sky-400' : c.chat_type === 'viber' ? 'bg-violet-500' : 'bg-emerald-400'}`} />
                    <span className="truncate flex-1" title={c.chat_title}>{c.chat_title}</span>
                    <span className="font-medium whitespace-nowrap text-gray-400">{c.post_count}</span>
                  </div>
                ))}
              </div>
            </FilterSection>
          )}
        </>
      )}
    </div>
  );
};

/* ── Sync Modal ────────────────────────────────────────────────────── */

const SyncModal: React.FC<{
  open: boolean;
  onClose: () => void;
  onSyncComplete: () => void;
}> = ({ open, onClose, onSyncComplete }) => {
  const [chatUsername, setChatUsername] = useState('');
  const [chatType, setChatType] = useState<'channel' | 'forum' | 'archive'>('forum');
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const runSync = async () => {
    if (!chatUsername.trim()) {
      setError('Введіть username каналу/форуму');
      return;
    }
    setSyncing(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch('/api/publications/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_username: chatUsername.trim(), chat_type: chatType }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || `HTTP ${res.status}`);
      } else {
        setResult(data);
        onSyncComplete();
      }
    } catch (e: any) {
      setError(e.message || 'Помилка синхронізації');
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-md w-full p-6">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">
          Синхронізувати з Telegram
        </h2>
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Username (без @) або ID
            </label>
            <input
              value={chatUsername}
              onChange={e => setChatUsername(e.target.value)}
              placeholder="brandstore_catalog"
              disabled={syncing}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Тип
            </label>
            <select
              value={chatType}
              onChange={e => setChatType(e.target.value as any)}
              disabled={syncing}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100"
            >
              <option value="channel">Канал (BrandStore)</option>
              <option value="forum">Форум (КАТАЛОГ ТОВАРУ)</option>
              <option value="archive">Архів (WORKSHOP)</option>
            </select>
          </div>

          <div className="text-xs text-gray-500 dark:text-gray-400 bg-blue-50 dark:bg-blue-900/20 p-2 rounded">
            🔒 Read-only операція. Програма тільки сканує пости і зберігає метадані.
            Нічого не редагує і не видаляє в Telegram.
          </div>

          {error && (
            <div className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 p-2 rounded">
              {error}
            </div>
          )}

          {result && (
            <div className="text-sm text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-900/20 p-2 rounded">
              ✅ Сканування завершено!<br/>
              Постів просканировано: <strong>{result.posts_scanned}</strong><br/>
              З товарами: <strong>{result.posts_with_products}</strong><br/>
              Збережено: <strong>{result.new_posts_saved}</strong>
            </div>
          )}
        </div>

        <div className="flex gap-2 justify-end mt-5">
          <button
            onClick={onClose}
            disabled={syncing}
            className="px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors"
          >
            Закрити
          </button>
          <button
            onClick={runSync}
            disabled={syncing}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors disabled:opacity-50"
          >
            {syncing ? 'Сканую...' : 'Запустити'}
          </button>
        </div>
      </div>
    </div>
  );
};

/* ── Detail Modal ──────────────────────────────────────────────────── */

const DetailModal: React.FC<{
  productId: number | null;
  onClose: () => void;
  onChanged?: () => void;
}> = ({ productId, onClose, onChanged }) => {
  const [details, setDetails] = useState<PublicationDetail[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [managingId, setManagingId] = useState<number | null>(null);
  const [rescheduleInputs, setRescheduleInputs] = useState<Record<number, string>>({});

  useEffect(() => {
    if (productId === null) return;
    setLoading(true);
    fetch(`/api/publications/product/${productId}`)
      .then(r => r.json())
      .then(d => setDetails(d.publications || []))
      .finally(() => setLoading(false));
  }, [productId, refreshKey]);

  // Черга Instagram і Facebook керується однаково — різниться лише майданчик
  // у шляху й у текстах. Тримати дві копії цієї логіки означало б, що одна з
  // них рано чи пізно відстане.
  const manageQueued = async (detail: PublicationDetail, action: 'cancel' | 'reschedule') => {
    const publicationId = detail.local_publication_id;
    const platform = detail.platform === 'facebook' ? 'facebook' : 'instagram';
    const label = platform === 'facebook' ? 'Facebook' : 'Instagram';
    if (!publicationId || managingId !== null) return;
    let body: Record<string, string> | undefined;
    if (action === 'reschedule') {
      const localValue = rescheduleInputs[publicationId];
      if (!localValue) {
        notify.warning({ message: 'Оберіть нову дату й час', duration: 5 });
        return;
      }
      const parsed = new Date(localValue);
      if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
        notify.warning({ message: 'Час має бути в майбутньому', duration: 5 });
        return;
      }
      body = { publish_at: parsed.toISOString() };
    }
    const confirmed = await confirmDialog(
      action === 'cancel'
        ? `Скасувати цю заплановану ${label}-публікацію? Медіа у ${label} ще не буде створено.`
        : `Перенести ${label}-публікацію на ${new Date(body!.publish_at).toLocaleString('uk-UA')}?`,
    );
    if (!confirmed) return;
    setManagingId(publicationId);
    try {
      const response = await fetch(`/api/publications/${platform}/publications/${publicationId}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Операція не виконана');
      notify.success({ message: action === 'cancel' ? `${label}-публікацію скасовано` : `Час ${label}-публікації змінено`, duration: 6 });
      setRefreshKey(value => value + 1);
    } catch (reason: any) {
      notify.error({ message: label, description: reason.message || 'Не вдалося оновити чергу', duration: 8 });
    } finally {
      setManagingId(null);
    }
  };

  const restoreManualCleanup = async (detail: PublicationDetail) => {
    const manualPlatforms = ['instagram', 'facebook', 'viber'];
    if (!productId || !manualPlatforms.includes(detail.platform || '') || managingId !== null) return;
    const approved = await confirmDialog('Повернути цю публікацію до активних? Вона знову враховуватиметься у фільтрах BMS.');
    if (!approved) return;
    setManagingId(typeof detail.local_publication_id === 'number' ? detail.local_publication_id : productId);
    try {
      const response = await fetch(`/api/publications/manual-cleanup/${detail.platform}/${productId}/restore`, { method: 'POST' });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.detail || 'Не вдалося відновити стан');
      notify.success({ message: 'Публікацію повернуто до активних у BMS', duration: 6 });
      setRefreshKey(value => value + 1);
      onChanged?.();
    } catch (error: any) {
      notify.error(error.message || 'Не вдалося відновити стан');
    } finally {
      setManagingId(null);
    }
  };

  if (productId === null) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-2xl w-full p-6 max-h-[80vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">
          Публікації товару #{productId}
        </h2>
        {loading ? (
          <div className="text-center py-8 text-gray-400">Завантаження...</div>
        ) : details.length === 0 ? (
          <div className="text-center py-8 text-gray-400">Публікацій не знайдено</div>
        ) : (
          <div className="space-y-2">
            {details.map(d => (
              <div key={d.id} className="border border-gray-200 dark:border-gray-700 rounded p-3">
                <div className="flex justify-between items-start mb-1">
                  <div>
                    <div className="font-semibold text-sm text-gray-900 dark:text-gray-100">
                      {d.chat_title}
                      {d.platform === 'viber' && <span className="ml-2 rounded bg-violet-100 px-1.5 py-0.5 text-xs text-violet-700 dark:bg-violet-900/30 dark:text-violet-300">VIBER</span>}
                      {d.platform === 'instagram' && <span className="ml-2 rounded bg-pink-100 px-1.5 py-0.5 text-xs text-pink-700 dark:bg-pink-900/30 dark:text-pink-300">INSTAGRAM</span>}
                      {d.platform === 'facebook' && <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">FACEBOOK</span>}
                      {d.is_master && <span className="ml-2 text-xs px-1.5 py-0.5 bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-300 rounded">ГОЛОВНА</span>}
                      {d.is_multi_size && <span className="ml-2 text-xs px-1.5 py-0.5 bg-purple-100 dark:bg-purple-900/30 text-purple-800 dark:text-purple-300 rounded">MULTI-SIZE</span>}
                    </div>
                    {d.thread_title && (
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        🗂 {d.thread_title}
                      </div>
                    )}
                    {d.is_multi_size && d.sizes_in_post && (
                      <div className="text-xs text-purple-600 dark:text-purple-400 mt-0.5">
                        Розміри в пості: {(() => {
                          try { return JSON.parse(d.sizes_in_post).join(', '); }
                          catch { return d.sizes_in_post; }
                        })()}
                      </div>
                    )}
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded ${
                    d.tg_status === 'published' ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300' :
                    ['scheduled', 'queued', 'processing', 'retrying'].includes(d.tg_status) ? 'bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300' :
                    ['archived', 'removed_manual'].includes(d.tg_status) ? 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300' :
                    'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300'
                  }`}>
                    {publicationStatusLabel(d.tg_status)}
                  </span>
                </div>
                {d.message_text && (
                  <div className="text-xs text-gray-600 dark:text-gray-400 mt-1 line-clamp-3 whitespace-pre-wrap">
                    {d.message_text}
                  </div>
                )}
                {d.collage_url && (/\.(mp4|mov)(\?|$)/i.test(d.collage_url)
                  ? <video src={d.collage_url} muted controls className="mt-2 h-32 w-24 rounded-lg border border-gray-200 object-cover dark:border-gray-700" />
                  : <img src={d.collage_url} alt={d.platform === 'instagram' ? 'Instagram-медіа' : d.platform === 'facebook' ? 'Facebook-медіа' : 'Viber-колаж'} className="mt-2 h-24 w-24 rounded-lg border border-gray-200 object-cover dark:border-gray-700" />)}
                {d.error && <div className="mt-1 text-xs text-rose-500">{d.error}</div>}
                {d.message_date && (
                  <div className="text-xs text-gray-400 mt-1">
                    {new Date(d.message_date).toLocaleString('uk-UA')}
                  </div>
                )}
                {d.permalink && <a href={d.permalink} target="_blank" rel="noreferrer" className={`mt-2 inline-block text-xs font-medium hover:underline ${d.platform === 'facebook' ? 'text-[#1877F2]' : 'text-pink-600'}`}>{d.platform === 'facebook' ? 'Відкрити у Facebook ↗' : 'Відкрити в Instagram ↗'}</a>}
                {(d.platform === 'instagram' || d.platform === 'facebook') && d.local_publication_id && ['scheduled', 'queued', 'retrying'].includes(d.tg_status) && (
                  <div className="mt-3 flex flex-wrap items-end gap-2 rounded-lg bg-pink-50 p-2 dark:bg-pink-950/20">
                    <label className="min-w-[210px] flex-1 text-[11px] font-medium text-gray-600 dark:text-gray-300">Новий час
                      <input type="datetime-local"
                        value={rescheduleInputs[d.local_publication_id] || ''}
                        onChange={event => setRescheduleInputs(current => ({ ...current, [d.local_publication_id!]: event.target.value }))}
                        className="mt-1 w-full rounded border border-pink-200 bg-white px-2 py-1.5 text-xs dark:border-pink-900 dark:bg-gray-800" />
                    </label>
                    <button type="button" onClick={() => manageQueued(d, 'reschedule')} disabled={managingId !== null}
                      className="rounded bg-pink-600 px-2.5 py-1.5 text-xs font-medium text-white disabled:opacity-50">Перенести</button>
                    <button type="button" onClick={() => manageQueued(d, 'cancel')} disabled={managingId !== null}
                      className="rounded border border-rose-300 px-2.5 py-1.5 text-xs font-medium text-rose-600 disabled:opacity-50">Скасувати</button>
                  </div>
                )}
                {(d.platform === 'instagram' || d.platform === 'facebook' || d.platform === 'viber') && d.tg_status === 'removed_manual' && (
                  <button type="button" onClick={() => restoreManualCleanup(d)} disabled={managingId !== null}
                    className="mt-2 rounded border border-gray-300 px-2.5 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700">
                    Повернути до активних
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="flex justify-end mt-4">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded transition-colors"
          >
            Закрити
          </button>
        </div>
      </div>
    </div>
  );
};

/* ── Main Page ─────────────────────────────────────────────────────── */

// Підвкладки «Публікацій». Автопостинг живе тут, а не в «Статистиці»: там
// лишились самі цифри, бо це різні заняття — дивитись і публікувати.
const PUB_TABS = [
  { key: 'list', label: 'Усі публікації' },
  { key: 'catalog', label: 'Інтернет-вітрина' },
  { key: 'stories', label: 'Сторіс' },
] as const;

type PubTab = typeof PUB_TABS[number]['key'];

const PublicationsPage: React.FC<PublicationsPageProps> = ({ currentSearchTerm }) => {
  const [pubTab, setPubTab] = useState<PubTab>('list');

  // Крос-вкладкова навігація зі «Статистики»: той самий патерн, що
  // «Товар → Перейти до замовлення» — App перемикає верхню вкладку й
  // підштовхує вже змонтовану сторінку відкрити потрібний розділ.
  useEffect(() => {
    const onOpenTab = (event: Event) => {
      const target = (event as CustomEvent).detail?.tab;
      if (PUB_TABS.some(tab => tab.key === target)) setPubTab(target as PubTab);
    };
    window.addEventListener('bms:publications-open-tab', onOpenTab);
    return () => window.removeEventListener('bms:publications-open-tab', onOpenTab);
  }, []);

  const [items, setItems] = useState<PublicationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const listAbortRef = useRef<AbortController | null>(null);
  const listRequestRef = useRef(0);
  const [filterMode, setFilterMode] = useState<FilterMode>('published');
  const [platform, setPlatform] = useState<PublicationPlatform>('all');
  // Базовий безпечний режим — не пропонувати публікацію товару без залишку.
  // Значення й вигляд відповідають однойменним перемикачам у «Товарах».
  const [onlyUnsold, setOnlyUnsold] = useState(true);
  const [onlyRostovka, setOnlyRostovka] = useState(false);
  const [stats, setStats] = useState<PublicationStats | null>(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const [detailProductId, setDetailProductId] = useState<number | null>(null);
  const [cardProductId, setCardProductId] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [unpublishing, setUnpublishing] = useState<number | null>(null);
  const [bulkUnpublishing, setBulkUnpublishing] = useState(false);
  const [manualCleanupBusy, setManualCleanupBusy] = useState<number | null>(null);
  const [bulkManualCleanupBusy, setBulkManualCleanupBusy] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [expandedDetail, setExpandedDetail] = useState<any>(null);
  const [expandedLoading, setExpandedLoading] = useState(false);
  // Створення поста: прев'ю → діалог редагування → публікація живою.
  const [tgPreview, setTgPreview] = useState<TelegramPreview | null>(null);
  const [tgBatchIds, setTgBatchIds] = useState<number[] | null>(null);
  const [tgPreviewing, setTgPreviewing] = useState<number | null>(null);
  const [tgBusy, setTgBusy] = useState(false);
  const [viberPreview, setViberPreview] = useState<ViberPreview | null>(null);
  const [viberPreviewing, setViberPreviewing] = useState<number | null>(null);
  const [viberBatchIds, setViberBatchIds] = useState<number[] | null>(null);
  const [viberBusy, setViberBusy] = useState(false);
  const [instagramPreview, setInstagramPreview] = useState<InstagramPreview | null>(null);
  const [instagramPreviewing, setInstagramPreviewing] = useState<number | null>(null);
  const [facebookPreview, setFacebookPreview] = useState<FacebookPreview | null>(null);
  const [facebookPreviewing, setFacebookPreviewing] = useState<number | null>(null);
  const [facebookBusy, setFacebookBusy] = useState(false);
  const [instagramBatchIds, setInstagramBatchIds] = useState<number[] | null>(null);
  const [instagramBusy, setInstagramBusy] = useState(false);

  // ── Column visibility (right-click menu) ─────────────────────────
  const colMenuRef = useRef<HTMLDivElement | null>(null);
  const [colMenuOpen, setColMenuOpen] = useState(false);
  const [colMenuPos, setColMenuPos] = useState<{x:number;y:number}>({x:0,y:0});
  // Стан для кнопки "Просканувати ВСІ зараз"
  const [syncingAll, setSyncingAll] = useState(false);
  const [syncAllMsg, setSyncAllMsg] = useState<string | null>(null);
  // Панель «Інтеграції» (Telegram/OLX/Prom) — усе керування каналами в одному місці.
  const [integrationsOpen, setIntegrationsOpen] = useState(false);
  const [olxStatus, setOlxStatus] = useState<any | null>(null);
  const [viberStatus, setViberStatus] = useState<any | null>(null);
  const [instagramStatus, setInstagramStatus] = useState<any | null>(null);
  const [viberSyncing, setViberSyncing] = useState(false);
  const [instagramSyncing, setInstagramSyncing] = useState(false);
  const [facebookStatus, setFacebookStatus] = useState<any | null>(null);
  const [facebookSyncing, setFacebookSyncing] = useState(false);
  // Prom-інтеграція: статус (термін токена), панель замовлень-дзеркала.
  const [promStatus, setPromStatus] = useState<any | null>(null);
  const [promOrders, setPromOrders] = useState<any[] | null>(null);
  // monoБазар: лише READ-верифікація (публічний API) — постинг заблоковано.
  const [monobazarStatus, setMonobazarStatus] = useState<any | null>(null);

  const fetchPromStatus = React.useCallback(async () => {
    try { const r = await fetch('/api/publications/prom/status'); if (r.ok) setPromStatus(await r.json()); }
    catch { /* нехай тихо */ }
  }, []);
  const fetchOlxStatus = React.useCallback(async () => {
    try { const r = await fetch('/api/publications/olx/status'); if (r.ok) setOlxStatus(await r.json()); }
    catch { /* нехай тихо */ }
  }, []);
  const fetchMonobazarStatus = React.useCallback(async () => {
    try { const r = await fetch('/api/publications/monobazar/status'); if (r.ok) setMonobazarStatus(await r.json()); }
    catch { /* нехай тихо */ }
  }, []);
  const fetchViberStatus = React.useCallback(async () => {
    try { const r = await fetch('/api/publications/viber/status'); if (r.ok) setViberStatus(await r.json()); }
    catch { /* редактор лишається доступним; стан повторимо при відкритті */ }
  }, []);
  const fetchInstagramStatus = React.useCallback(async () => {
    try { const r = await fetch('/api/publications/instagram/status'); if (r.ok) setInstagramStatus(await r.json()); }
    catch { /* dry-run редактор лишається доступним */ }
  }, []);
  const fetchFacebookStatus = React.useCallback(async () => {
    try { const r = await fetch('/api/publications/facebook/status'); if (r.ok) setFacebookStatus(await r.json()); }
    catch { /* dry-run редактор лишається доступним */ }
  }, []);
  const handleFacebookConnect = async () => {
    try {
      const response = await fetch('/api/publications/facebook/oauth/start', { method: 'POST' });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.authorization_url) throw new Error(result.detail || result.error || 'OAuth ще не налаштовано');
      window.open(result.authorization_url, '_blank', 'noopener,noreferrer');
      notify.info({ message: 'Відкрито підключення Facebook', description: 'Після підтвердження Meta поверніться в BMS і натисніть «Перевірити стан».', duration: 10 });
    } catch (reason: any) {
      notify.error({ message: 'Facebook OAuth', description: reason.message || 'Не вдалося почати підключення', duration: 9 });
    }
  };
  const handleInstagramConnect = async () => {
    try {
      const response = await fetch('/api/publications/instagram/oauth/start', { method: 'POST' });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.authorization_url) throw new Error(result.detail || result.error || 'OAuth ще не налаштовано');
      window.open(result.authorization_url, '_blank', 'noopener,noreferrer');
      notify.info({ message: 'Відкрито підключення Instagram', description: 'Після підтвердження Meta поверніться в BMS і натисніть «Перевірити стан».', duration: 10 });
    } catch (reason: any) {
      notify.error({ message: 'Instagram OAuth', description: reason.message || 'Не вдалося почати підключення', duration: 9 });
    }
  };
  useEffect(() => { fetchPromStatus(); fetchOlxStatus(); fetchMonobazarStatus(); fetchViberStatus(); fetchInstagramStatus(); fetchFacebookStatus(); }, [fetchPromStatus, fetchOlxStatus, fetchMonobazarStatus, fetchViberStatus, fetchInstagramStatus, fetchFacebookStatus]);
  useEffect(() => {
    const refresh = () => { void fetchViberStatus(); };
    window.addEventListener('bms:viber-status-refresh', refresh);
    return () => window.removeEventListener('bms:viber-status-refresh', refresh);
  }, [fetchViberStatus]);

  const handleViberStatusSync = () => {
    if (viberSyncing || !viberStatus?.configured) {
      void fetchViberStatus();
      return;
    }
    setViberSyncing(true);
    taskManager.run(
      'Оновлення станів Viber-публікацій',
      async () => {
        const response = await fetch('/api/publications/viber/sync-status', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Не вдалося звірити Viber-чергу');
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => result.errors?.length
          ? { status: 'partial', detail: `${result.updated || 0} оновлено · ${result.errors.length} помилок` }
          : { status: 'success', detail: `${result.updated || 0} станів оновлено` },
        onSuccess: (result: any) => {
          notify.success({ message: 'Стани Viber оновлено', description: `Перевірено ${result.checked || 0} записів`, duration: 5 });
          fetchItems();
          fetchStats();
        },
      },
    ).catch(() => undefined).finally(() => setViberSyncing(false));
  };

  const handleInstagramStatusSync = () => {
    if (instagramSyncing || !instagramStatus?.oauth_connected) {
      void fetchInstagramStatus();
      return;
    }
    setInstagramSyncing(true);
    taskManager.run(
      'Оновлення станів Instagram-публікацій',
      async () => {
        const response = await fetch('/api/publications/instagram/sync-status', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Не вдалося звірити Instagram-чергу');
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => result.errors?.length
          ? { status: 'partial', detail: `${result.updated || 0} оновлено · ${result.errors.length} помилок` }
          : { status: 'success', detail: `${result.updated || 0} станів оновлено` },
        onSuccess: (result: any) => {
          notify.success({ message: 'Стани Instagram оновлено', description: `Перевірено ${result.checked || 0} записів`, duration: 5 });
          fetchItems();
          fetchStats();
          fetchInstagramStatus();
        },
      },
    ).catch(() => undefined).finally(() => setInstagramSyncing(false));
  };

  const handleFacebookStatusSync = () => {
    if (facebookSyncing || !facebookStatus?.oauth_connected) {
      void fetchFacebookStatus();
      return;
    }
    setFacebookSyncing(true);
    taskManager.run(
      'Оновлення станів Facebook-публікацій',
      async () => {
        const response = await fetch('/api/publications/facebook/sync-status', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Не вдалося звірити Facebook-чергу');
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => result.errors?.length
          ? { status: 'partial', detail: `${result.updated || 0} оновлено · ${result.errors.length} помилок` }
          : { status: 'success', detail: `${result.updated || 0} станів оновлено` },
        onSuccess: (result: any) => {
          notify.success({ message: 'Стани Facebook оновлено', description: `Перевірено ${result.checked || 0} записів`, duration: 5 });
          fetchItems();
          fetchStats();
          fetchFacebookStatus();
        },
      },
    ).catch(() => undefined).finally(() => setFacebookSyncing(false));
  };

  // monoБазар: синхронізувати вітрину продавця (публічний API, без токенів).
  const handleMonobazarSync = async () => {
    if (syncingAll) return;
    setSyncingAll(true); setSyncAllMsg(null);
    try {
      const r = await fetch('/api/publications/monobazar/sync', { method: 'POST' });
      const d = await r.json();
      if (!r.ok) { setSyncAllMsg(`❌ monoБазар: ${d.detail || r.status}`); return; }
      setSyncAllMsg(`✅ monoБазар: ${d.total || 0} оголошень (${d.confident || 0} підтверджено, ${d.ambiguous || 0} неоднозначних, ${d.unmatched || 0} без збігу).`);
      fetchMonobazarStatus();
    } catch (e: any) {
      setSyncAllMsg(`❌ monoБазар: ${e.message || 'Помилка'}`);
    } finally { setSyncingAll(false); }
  };

  // Prom: синхронізувати товари + замовлення (дзеркала).
  const handlePromSync = async () => {
    if (syncingAll) return;
    setSyncingAll(true); setSyncAllMsg(null);
    try {
      const rp = await fetch('/api/publications/sync-prom-products', { method: 'POST' });
      const dp = await rp.json();
      if (!rp.ok) { setSyncAllMsg(`❌ Prom товари: ${dp.detail || rp.status}`); return; }
      const ro = await fetch('/api/publications/sync-prom-orders', { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
      const dord = await ro.json();
      setSyncAllMsg(`✅ Prom: ${dp.total || 0} товарів (${dp.linked || 0} злінковано), ${dord.total || 0} замовлень.`);
      fetchItems(); fetchPromStatus();
      setTimeout(() => setSyncAllMsg(null), 6000);
    } catch (e: any) { setSyncAllMsg(`❌ Prom: ${e.message || 'Помилка'}`); }
    finally { setSyncingAll(false); }
  };

  // Prom: оновити наявність (ЗАПИС у живі оголошення). Спершу dry-run → підтвердження.
  const handlePromPushAvailability = async () => {
    if (syncingAll) return;
    try {
      const dr = await fetch('/api/publications/prom/push-availability', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ dry_run: true }) });
      const dd = await dr.json();
      if (!dr.ok) { setSyncAllMsg(`❌ Prom: ${dd.detail || dr.status}`); return; }
      const n = dd.would_change || 0;
      if (n === 0) { setSyncAllMsg(`✅ Prom: наявність уже синхронна (перевірено ${dd.checked || 0}).`); setTimeout(() => setSyncAllMsg(null), 5000); return; }
      const sample = (dd.sample || []).map((c: any) => `  ${c.sku}: → ${c.to === 'available' ? 'в наявності' : 'немає'}`).join('\n');
      if (!(await confirmDialog(`Оновити наявність на Prom для ${n} товар(ів)?\nЦе ЗАПИС у твої живі оголошення.\n\n${sample}${n > (dd.sample||[]).length ? '\n  …' : ''}`))) return;
      setSyncingAll(true);
      const r = await fetch('/api/publications/prom/push-availability', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ dry_run: false }) });
      const d = await r.json();
      setSyncAllMsg(r.ok ? `✅ Prom: наявність оновлено для ${d.changed || 0} товар(ів).` : `❌ Prom: ${d.detail || r.status}`);
      fetchItems(); fetchPromStatus();
      setTimeout(() => setSyncAllMsg(null), 6000);
    } catch (e: any) { setSyncAllMsg(`❌ Prom: ${e.message || 'Помилка'}`); }
    finally { setSyncingAll(false); }
  };

  const openPromOrders = async () => {
    try {
      const r = await fetch('/api/publications/prom/orders?limit=200');
      const d = await r.json();
      setPromOrders(d.orders || []);
    } catch { setPromOrders([]); }
  };
  const defaultPubVisibility: Record<PubColumnId, boolean> = PUB_COLUMN_ORDER.reduce(
    (acc, c) => { acc[c.id] = !c.optional; return acc; },
    {} as Record<PubColumnId, boolean>,
  );
  const [pubColumnsVisible, setPubColumnsVisible] = useState<Record<PubColumnId, boolean>>(() => {
    try {
      const raw = localStorage.getItem(PUB_COLUMNS_STORAGE_KEY);
      if (!raw) return defaultPubVisibility;
      const parsed = JSON.parse(raw);
      return { ...defaultPubVisibility, ...parsed };
    } catch {
      return defaultPubVisibility;
    }
  });
  useEffect(() => {
    localStorage.setItem(PUB_COLUMNS_STORAGE_KEY, JSON.stringify(pubColumnsVisible));
  }, [pubColumnsVisible]);
  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!colMenuRef.current) return setColMenuOpen(false);
      if (!colMenuRef.current.contains(e.target as Node)) setColMenuOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);
  const handlePubContextMenu: React.MouseEventHandler<HTMLDivElement> = (e) => {
    e.preventDefault();
    setColMenuPos({ x: e.clientX, y: e.clientY });
    setColMenuOpen(true);
  };
  const isPubColVisible = (id: PubColumnId) => !!pubColumnsVisible[id];

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch('/api/publications/stats');
      if (res.ok) setStats(await res.json());
    } catch { /* ignore */ }
  }, []);

  const fetchItems = useCallback(async () => {
    listAbortRef.current?.abort();
    const controller = new AbortController();
    listAbortRef.current = controller;
    const requestId = ++listRequestRef.current;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(perPage),
      });
      if (currentSearchTerm) params.set('search', currentSearchTerm);
      if (filterMode !== 'all') params.set('filter_mode', filterMode);
      params.set('platform', platform);
      // Режим «Продані, але висять» — окремий cleanup-сценарій; у ньому
      // «Тільки непродані» тимчасово не діє і відновлюється після виходу.
      params.set('only_unsold', String(onlyUnsold && filterMode !== 'problematic'));
      if (onlyRostovka) params.set('only_rostovka', 'true');

      const res = await fetch(`/api/publications/overview?${params}`, { signal: controller.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (requestId !== listRequestRef.current) return;
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 1);
    } catch (e: any) {
      if (e?.name === 'AbortError') return;
      if (requestId === listRequestRef.current) setError(e.message || 'Помилка завантаження');
    } finally {
      if (requestId === listRequestRef.current) {
        setLoading(false);
        setIsRefreshing(false);
      }
    }
  }, [page, perPage, currentSearchTerm, filterMode, platform, onlyUnsold, onlyRostovka]);

  useEffect(() => { fetchItems(); }, [fetchItems]);
  useEffect(() => () => listAbortRef.current?.abort(), []);
  useEffect(() => { setPage(1); }, [currentSearchTerm]);
  useEffect(() => { fetchStats(); }, [fetchStats]);

  const handleRefresh = () => {
    setIsRefreshing(true);
    fetchItems();
    fetchStats();
  };

  const handleResetFilters = () => {
    setPage(1);
    setFilterMode('published');
    setPlatform('all');
    // Як у «Товарах»: базовий «Тільки непродані» не скидаємо.
    setOnlyRostovka(false);
  };

  const handleRelink = async () => {
    if (!(await confirmDialog('Спробувати зв\'язати непов\'язані пости з товарами по їх номерах?'))) return;
    try {
      const res = await fetch('/api/publications/relink', { method: 'POST' });
      const data = await res.json();
      notify.success(`Пов'язано: ${data.rows_affected} постів`);
      fetchItems();
      fetchStats();
    } catch (e: any) {
      notify.error(e.message);
    }
  };

  // ── Force full sync (всі відомі канали) ─────────────────────────────
  const handleSyncAll = async () => {
    if (syncingAll) return;
    setSyncingAll(true);
    setSyncAllMsg(null);
    try {
      const res = await fetch('/api/publications/sync-all', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        setSyncAllMsg(`❌ ${data.detail || `HTTP ${res.status}`}`);
      } else {
        const t = data.totals || {};
        setSyncAllMsg(
          `✅ Сканування завершено: ${t.posts_scanned || 0} постів проглянуто, ` +
          `${t.new_posts_saved || 0} нових збережено, ${data.auto_relinked || 0} перепов'язано.`
        );
        fetchItems();
        fetchStats();
        // Сховаємо банер через 5 сек
        setTimeout(() => setSyncAllMsg(null), 5000);
      }
    } catch (e: any) {
      setSyncAllMsg(`❌ ${e.message || 'Помилка'}`);
    } finally {
      setSyncingAll(false);
    }
  };

  // OLX: одноразова авторизація (OAuth) — відкриває сторінку OLX у новому вікні.
  const handleOlxConnect = async () => {
    try {
      const res = await fetch('/api/publications/olx/oauth/start');
      const data = await res.json();
      if (!res.ok) {
        setSyncAllMsg(`❌ OLX: ${data.detail || `HTTP ${res.status}`}`);
        return;
      }
      window.open(data.authorize_url, '_blank', 'noopener');
      setSyncAllMsg('🔗 Відкрито сторінку авторизації OLX. Після підтвердження повернись і натисни «Синхронізувати OLX».');
    } catch (e: any) {
      setSyncAllMsg(`❌ OLX: ${e.message || 'Помилка'}`);
    }
  };

  // OLX: синхронізувати оголошення + перепов'язати до товарів.
  const handleOlxSync = async () => {
    if (syncingAll) return;
    setSyncingAll(true);
    setSyncAllMsg(null);
    try {
      const res = await fetch('/api/publications/sync-olx', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        setSyncAllMsg(`❌ OLX: ${data.detail || `HTTP ${res.status}`}`);
      } else {
        setSyncAllMsg(
          `✅ OLX: ${data.total || 0} оголошень, ${data.created || 0} нових, ` +
          `${data.linked || 0} прив'язано, ${data.auto_relinked || 0} перепов'язано.`
        );
        fetchItems();
        fetchStats();
        setTimeout(() => setSyncAllMsg(null), 6000);
      }
    } catch (e: any) {
      setSyncAllMsg(`❌ OLX: ${e.message || 'Помилка'}`);
    } finally {
      setSyncingAll(false);
    }
  };

  // ── Створення поста ────────────────────────────────────────────────────
  // Прев'ю нічого не створює в Telegram — можна натискати без наслідків.
  const openPublishDialog = async (productId: number) => {
    if (tgPreviewing !== null) return;
    setTgPreviewing(productId);
    try {
      const res = await fetch('/api/publications/telegram/preview-post', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId }),
      });
      const d = await res.json();
      if (!res.ok) { notify.error(`Не вдалося зібрати пост: ${d.detail || res.status}`); return; }
      setTgPreview(d);
    } catch (e: any) {
      notify.error(e.message || 'Помилка звʼязку');
    } finally {
      setTgPreviewing(null);
    }
  };

  const handlePublish = async (payload: TelegramPublishPayload) => {
    if (!tgPreview) return;
    setTgBusy(true);
    try {
      const res = await fetch('/api/publications/telegram/create-post', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: tgPreview.product_id, ...payload }),
      });
      const d = await res.json();
      if (!res.ok) { notify.error(`Публікація не вдалася: ${d.detail || res.status}`); return; }

      // Репетиція нічого не змінює в каталозі — і список перечитувати нема сенсу.
      if (d.test_mode) {
        setTgPreview(null);
        await alertDialog({
          title: `Тестовий пост #${d.productnumber} надіслано`,
          body: `Перевір «${d.archive_title}» у Telegram: ${d.image_count} фото.\n`
              + 'У каталог і канал нічого не пішло, товар не позначений опублікованим.',
        });
        return;
      }

      const lines = [`Оригінал у «${tgPreview.root_topic.thread_title}»`];
      if (d.threads_posted?.length) {
        lines.push(`Копії в гілки: ${d.threads_posted.map((t: any) => t.thread_title).join(', ')}`);
      }
      if (d.channel) {
        lines.push(d.channel.scheduled_at
          ? `Канал: заплановано на ${new Date(d.channel.scheduled_at).toLocaleString('uk-UA', { dateStyle: 'short', timeStyle: 'short' })}`
          : 'Канал: переслано зараз');
      }
      if (d.failed?.length) {
        lines.push(`⚠️ Не вдалося: ${d.failed.map((f: any) => f.thread_title || f.channel || '?').join(', ')}`);
      }
      setTgPreview(null);
      // Довгий підсумок краще діалогом: тост зникає раніше, ніж його дочитають.
      await alertDialog({ title: `Опубліковано #${d.productnumber}`, body: lines.join('\n') });
      fetchItems();
      fetchStats();
    } catch (e: any) {
      notify.error(e.message || 'Помилка звʼязку');
    } finally {
      setTgBusy(false);
    }
  };

  const handleTelegramBatchPublish = (request: TelegramBatchRequest) => {
    const count = request.items.length;
    setTgBusy(true);
    taskManager.run(
      `Пакетна Telegram-публікація: ${count} постів`,
      async () => {
        const response = await fetch('/api/publications/telegram/create-posts-batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(request),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          const error: any = new Error(data.detail || data.error || 'Пакетна Telegram-публікація не вдалася');
          error.response = { data: { detail: data.detail || data.error } };
          throw error;
        }
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => {
          const counts = result.counts || {};
          const summary = [
            counts.success ? `${counts.success} успішно` : '',
            counts.partial ? `${counts.partial} частково` : '',
            counts.error ? `${counts.error} з помилкою` : '',
            counts.skipped ? `${counts.skipped} не надсилали` : '',
          ].filter(Boolean).join(' · ');
          const issues = (result.results || [])
            .filter((item: any) => item.status !== 'success')
            .map((item: any) => {
              const failed = item.result?.failed
                ?.map((failure: any) => failure.thread_title || failure.channel || failure.error)
                .join(', ');
              return `#${item.productnumber}: ${item.error || failed || item.status}`;
            })
            .join(' · ');
          return {
            status: result.status === 'success' ? 'success' : 'partial',
            detail: [summary, issues].filter(Boolean).join(' — '),
          };
        },
        onSuccess: (result: any) => {
          const counts = result.counts || {};
          const detail = `${counts.success || 0} успішно${counts.partial ? ` · ${counts.partial} частково` : ''}${counts.error ? ` · ${counts.error} з помилкою` : ''}${counts.skipped ? ` · ${counts.skipped} не надсилали` : ''}`;
          if (result.status === 'success') {
            notify.success({ message: 'Пакет Telegram опубліковано', description: detail, duration: 7 });
          } else {
            notify.warning({ message: 'Пакет Telegram виконано частково', description: `${detail}. Подробиці збережено у Сповіщеннях.`, duration: 11 });
          }
          setTgBatchIds(null);
          setSelectedIds(new Set());
          fetchItems();
          fetchStats();
        },
      },
    ).catch(() => undefined).finally(() => {
      setTgBusy(false);
      window.dispatchEvent(new CustomEvent('bms:telegram-status-refresh'));
    });
  };

  const openViberDialog = async (productId: number) => {
    if (viberPreviewing !== null) return;
    setViberPreviewing(productId);
    try {
      const response = await fetch('/api/publications/viber/preview-post', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Не вдалося зібрати Viber-пост');
      setViberPreview(data);
      setViberStatus(data.connection || null);
    } catch (reason: any) {
      notify.error({ message: `Viber: ${reason.message || 'Помилка зв’язку'}`, duration: 8 });
    } finally {
      setViberPreviewing(null);
    }
  };

  const handleViberPublish = (payload: ViberPublishPayload) => {
    if (!viberPreview) return;
    const productId = viberPreview.product_id;
    const productNumber = viberPreview.productnumber;
    setViberBusy(true);
    taskManager.run(
      `Viber-публікація #${productNumber}`,
      async () => {
        const response = await fetch('/api/publications/viber/create-post', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_id: productId, ...payload }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          const error: any = new Error(data.detail || data.error || 'Viber-публікація не вдалася');
          error.response = { data: { detail: data.detail || data.error } };
          throw error;
        }
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => ({ status: 'success', detail: result.status === 'scheduled' && result.scheduled_at ? `Заплановано на ${new Date(result.scheduled_at).toLocaleString('uk-UA')}` : 'Прийнято у захищену чергу' }),
        onSuccess: (result: any) => {
          notify.success({ message: result.status === 'scheduled' ? `#${productNumber} заплановано у Viber` : `#${productNumber} передано у Viber`, duration: 7 });
          setViberPreview(null);
          fetchItems();
          fetchStats();
        },
      },
    ).catch(() => undefined).finally(() => {
      setViberBusy(false);
      window.dispatchEvent(new CustomEvent('bms:viber-status-refresh'));
    });
  };

  const handleViberBatchPublish = (request: ViberBatchRequest) => {
    setViberBusy(true);
    taskManager.run(
      `Пакетна Viber-публікація: ${request.items.length} постів`,
      async () => {
        const response = await fetch('/api/publications/viber/create-posts-batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(request),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          const error: any = new Error(data.detail || data.error || 'Пакетна Viber-публікація не вдалася');
          error.response = { data: { detail: data.detail || data.error } };
          throw error;
        }
        return data;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => {
          const counts = result.counts || {};
          const details = (result.results || []).filter((item: any) => item.error).map((item: any) => `#${item.productnumber}: ${item.error}`).join(' · ');
          return { status: result.status === 'success' ? 'success' : 'partial', detail: [`${counts.success || 0} прийнято`, counts.error ? `${counts.error} з помилкою` : '', details].filter(Boolean).join(' · ') };
        },
        onSuccess: (result: any) => {
          const counts = result.counts || {};
          if (result.status === 'success') notify.success({ message: 'Пакет Viber прийнято', description: `${counts.success || 0} постів у черзі`, duration: 7 });
          else notify.warning({ message: 'Пакет Viber виконано частково', description: `${counts.success || 0} прийнято · ${counts.error || 0} з помилкою. Деталі — у Сповіщеннях.`, duration: 11 });
          setViberBatchIds(null);
          setSelectedIds(new Set());
          fetchItems();
          fetchStats();
        },
      },
    ).catch(() => undefined).finally(() => {
      setViberBusy(false);
      window.dispatchEvent(new CustomEvent('bms:viber-status-refresh'));
    });
  };

  const openFacebookDialog = async (productId: number) => {
    if (facebookPreviewing !== null) return;
    setFacebookPreviewing(productId);
    try {
      const response = await fetch('/api/publications/facebook/preview-post', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Не вдалося зібрати Facebook-чернетку');
      setFacebookPreview(data);
      setFacebookStatus(data.connection || facebookStatus);
    } catch (reason: any) {
      notify.error({ message: `Facebook: ${reason.message || 'Помилка зв’язку'}`, duration: 8 });
    } finally {
      setFacebookPreviewing(null);
    }
  };

  const handleFacebookPublish = async (payload: FacebookDraftPayload) => {
    if (!facebookPreview || facebookBusy) return;
    const pages = (facebookPreview.connection.pages || [])
      .filter(page => payload.page_ids.includes(page.id))
      .map(page => page.name);
    const approved = await confirmDialog({
      title: payload.publish_at ? 'Запланувати Facebook-публікацію?' : 'Опублікувати у Facebook зараз?',
      body: `#${facebookPreview.productnumber} · ${payload.publish_type === 'feed' ? 'пост/альбом' : payload.publish_type === 'story' ? 'Story' : 'Reel'}\nСторінки: ${pages.join(', ') || facebookPreview.connection.account}${payload.publish_at ? `\nЧас: ${new Date(payload.publish_at).toLocaleString('uk-UA')}` : ''}`,
      okText: payload.publish_at ? 'Запланувати' : 'Опублікувати', kind: 'warning',
    });
    if (!approved) return;
    setFacebookBusy(true);
    taskManager.run(
      `Facebook #${facebookPreview.productnumber}`,
      async () => {
        const response = await fetch('/api/publications/facebook/create-post', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Facebook-публікація не вдалася');
        return result;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => ({
          status: 'success',
          detail: result.scheduled_at
            ? `Заплановано: ${new Date(result.scheduled_at).toLocaleString('uk-UA')}`
            : `Передано у чергу · Сторінок: ${(result.pages || []).length}`,
        }),
        onSuccess: () => {
          notify.success({ message: 'Facebook-публікацію передано в чергу', duration: 7 });
          setFacebookPreview(null);
          fetchItems();
          fetchStats();
        },
      },
    ).catch(() => undefined).finally(() => setFacebookBusy(false));
  };

  const openInstagramDialog = async (productId: number) => {
    if (instagramPreviewing !== null) return;
    setInstagramPreviewing(productId);
    try {
      const response = await fetch('/api/publications/instagram/preview-post', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Не вдалося зібрати Instagram-чернетку');
      setInstagramPreview(data);
      setInstagramStatus(data.connection || instagramStatus);
    } catch (reason: any) {
      notify.error({ message: `Instagram: ${reason.message || 'Помилка зв’язку'}`, duration: 8 });
    } finally {
      setInstagramPreviewing(null);
    }
  };

  const handleInstagramPublish = async (payload: InstagramDraftPayload) => {
    if (!instagramPreview || instagramBusy) return;
    const approved = await confirmDialog({
      title: payload.publish_at ? 'Запланувати Instagram-публікацію?' : 'Опублікувати в Instagram зараз?',
      body: `#${instagramPreview.productnumber} · ${payload.publish_type === 'feed' ? 'пост/карусель' : payload.publish_type === 'story' ? 'Story' : 'Reel'}\nАкаунт: ${instagramPreview.connection.account}${payload.publish_at ? `\nЧас: ${new Date(payload.publish_at).toLocaleString('uk-UA')}` : ''}`,
      okText: payload.publish_at ? 'Запланувати' : 'Опублікувати', kind: 'warning',
    });
    if (!approved) return;
    setInstagramBusy(true);
    taskManager.run(
      `Instagram #${instagramPreview.productnumber}`,
      async () => {
        const response = await fetch('/api/publications/instagram/create-post', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Instagram-публікація не вдалася');
        return result;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => ({ status: 'success', detail: result.scheduled_at ? `Заплановано: ${new Date(result.scheduled_at).toLocaleString('uk-UA')}` : 'Передано у захищену чергу' }),
        onSuccess: (result: any) => {
          notify.success({ message: result.scheduled_at ? 'Instagram-публікацію заплановано' : 'Instagram-публікацію передано в чергу', duration: 7 });
          setInstagramPreview(null); setSelectedIds(new Set()); fetchItems(); fetchStats();
        },
      },
    ).catch(() => undefined).finally(() => setInstagramBusy(false));
  };

  const handleInstagramBatchPublish = async (request: InstagramBatchRequest) => {
    if (instagramBusy) return;
    const approved = await confirmDialog({
      title: 'Передати пакет в Instagram?',
      body: `${request.items.length} окремих публікацій буде передано у захищену хмарну чергу.`,
      okText: 'Передати пакет', kind: 'warning',
    });
    if (!approved) return;
    setInstagramBusy(true);
    taskManager.run(
      `Пакет Instagram: ${request.items.length} публікацій`,
      async () => {
        const response = await fetch('/api/publications/instagram/create-posts-batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Instagram-пакет не виконано');
        return result;
      },
      {
        silentSuccess: true,
        resultStatus: (result: any) => ({ status: result.status === 'success' ? 'success' : 'partial', detail: `${result.counts?.success || 0} прийнято · ${result.counts?.error || 0} помилок` }),
        onSuccess: (result: any) => {
          const counts = result.counts || {};
          if (result.status === 'success') notify.success({ message: 'Instagram-пакет прийнято', description: `${counts.success || 0} публікацій`, duration: 7 });
          else notify.warning({ message: 'Instagram-пакет прийнято частково', description: `${counts.success || 0} прийнято · ${counts.error || 0} помилок`, duration: 10 });
          setInstagramBatchIds(null); setSelectedIds(new Set()); fetchItems(); fetchStats();
        },
      },
    ).catch(() => undefined).finally(() => setInstagramBusy(false));
  };

  // Гілки форуму зчитуються з Telegram і кешуються локально — інакше діалог
  // не знає, куди взагалі можна публікувати.
  const handleRefreshThreads = async () => {
    if (syncingAll) return;
    setSyncingAll(true); setSyncAllMsg(null);
    try {
      const res = await fetch('/api/publications/telegram/refresh-threads', { method: 'POST' });
      const d = await res.json();
      setSyncAllMsg(res.ok
        ? `✅ Гілки форуму оновлено: ${d.threads}.`
        : `❌ Гілки: ${d.detail || res.status}`);
      setTimeout(() => setSyncAllMsg(null), 6000);
    } catch (e: any) {
      setSyncAllMsg(`❌ Гілки: ${e.message || 'Помилка'}`);
    } finally { setSyncingAll(false); }
  };

  const handleUnpublish = async (productId: number) => {
    if (!(await confirmDialog('Зняти з публікації? Пост буде переслано у WORKSHOP (архів) і видалено з усіх каналів/гілок.'))) return;
    setUnpublishing(productId);
    try {
      const res = await fetch(`/api/publications/unpublish/${productId}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        notify.error(`Помилка: ${data.detail || res.status}`);
      } else {
        const failCount = data.failed?.length || 0;
        const parts = [];
        if (data.deleted > 0) parts.push(`Видалено: ${data.deleted} постів`);
        if (data.forwarded > 0) parts.push(`Переслано в WORKSHOP: ${data.forwarded}`);
        if (data.edited > 0) parts.push(`Відредаговано (ростовки): ${data.edited}`);
        if (data.skipped > 0) parts.push(`Пропущено (є ще в наявності): ${data.skipped}`);
        if (failCount > 0) {
          parts.push(`⚠️ Помилок: ${failCount}`);
          const shown = (data.failed as any[]).slice(0, 3).map(f =>
            `  • ${f.chat || '?'} #${f.msg_id ?? '?'} — ${f.action || 'process'}: ${(f.error || '').slice(0, 120)}`
          );
          parts.push(...shown);
          if (failCount > shown.length) parts.push(`  …і ще ${failCount - shown.length}`);
        }
        await alertDialog({ title: 'Знято з публікації', body: parts.join('\n') || 'Готово' });
        fetchItems();
        fetchStats();
      }
    } catch (e: any) {
      notify.error(e.message);
    } finally {
      setUnpublishing(null);
    }
  };

  const handleBulkUnpublish = async () => {
    const ids = Array.from(selectedIds).filter(id => {
      const item = items.find(row => row.product_id === id);
      return !!item && (item.telegram_publication_count ?? item.publication_count) > 0;
    });
    if (ids.length === 0) {
      notify.warning({ message: 'Серед вибраного немає Telegram-постів для зняття', duration: 6 });
      return;
    }
    if (!(await confirmDialog(`Зняти з Telegram ${ids.length} товарів? Кожен Telegram-пост буде переслано у WORKSHOP і видалено з Telegram-каналів.`))) return;
    setBulkUnpublishing(true);
    try {
      const res = await fetch('/api/publications/unpublish-bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_ids: ids }),
      });
      const data = await res.json();
      if (!res.ok) {
        notify.error(`Помилка: ${data.detail || res.status}`);
      } else {
        const lines = [
          `Оброблено: ${data.products_processed} товарів`,
          `Видалено: ${data.total_deleted} постів`,
        ];
        if (typeof data.total_forwarded === 'number' && data.total_forwarded > 0)
          lines.push(`Переслано в WORKSHOP: ${data.total_forwarded}`);
        if (typeof data.total_edited === 'number' && data.total_edited > 0)
          lines.push(`Відредаговано: ${data.total_edited}`);
        if (typeof data.total_skipped === 'number' && data.total_skipped > 0)
          lines.push(`Пропущено: ${data.total_skipped}`);
        if (data.total_failed > 0) {
          lines.push(`⚠️ Помилок: ${data.total_failed}`);
          const fails = (data.details || [])
            .flatMap((r: any) => (r.failed || []).map((f: any) => ({ ...f, product_id: r.product_id })))
            .slice(0, 5);
          for (const f of fails) {
            lines.push(`  • prod ${f.product_id} / ${f.chat || '?'} #${f.msg_id ?? '?'} — ${f.action || 'process'}: ${(f.error || '').slice(0, 100)}`);
          }
        }
        await alertDialog({ title: 'Масове зняття завершено', body: lines.join('\n') });
        setSelectedIds(new Set());
        fetchItems();
        fetchStats();
      }
    } catch (e: any) {
      notify.error(e.message);
    } finally {
      setBulkUnpublishing(false);
    }
  };

  const MANUAL_CLEANUP_LABELS: Record<string, string> = {
    instagram: 'Instagram', facebook: 'Facebook', viber: 'Viber',
  };

  const handleManualCleanup = async (productId: number, targetPlatform: 'instagram' | 'facebook' | 'viber') => {
    const platformLabel = MANUAL_CLEANUP_LABELS[targetPlatform] || targetPlatform;
    const approved = await confirmDialog(
      `Підтвердити, що всі публікації цього проданого товару вже прибрані з ${platformLabel}? BMS збереже їх в історії, але більше не показуватиме як активні.`,
    );
    if (!approved) return;
    setManualCleanupBusy(productId);
    try {
      const response = await fetch(`/api/publications/manual-cleanup/${targetPlatform}/${productId}/confirm`, { method: 'POST' });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.detail || 'Не вдалося підтвердити прибирання');
      notify.success({ message: `Прибрано з обліку ${platformLabel}`, description: `${result.changed} публікацій збережено в історії`, duration: 6 });
      setSelectedIds(current => { const next = new Set(current); next.delete(productId); return next; });
      fetchItems();
      fetchStats();
    } catch (error: any) {
      notify.error(error.message || 'Не вдалося оновити стан публікації');
    } finally {
      setManualCleanupBusy(null);
    }
  };

  const handleBulkManualCleanup = async () => {
    if (!MANUAL_CLEANUP_LABELS[platform]) return;
    const ids = Array.from(selectedIds);
    if (!ids.length) return;
    const platformLabel = MANUAL_CLEANUP_LABELS[platform];
    const approved = await confirmDialog(
      `Підтвердити ручне прибирання ${ids.length} товарів із ${platformLabel}? Робіть це лише після фактичного видалення постів на майданчику.`,
    );
    if (!approved) return;
    setBulkManualCleanupBusy(true);
    let success = 0;
    const errors: string[] = [];
    for (const productId of ids) {
      try {
        const response = await fetch(`/api/publications/manual-cleanup/${platform}/${productId}/confirm`, { method: 'POST' });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) throw new Error(result.detail || `#${productId}: помилка`);
        success += 1;
      } catch (error: any) {
        errors.push(error.message || `#${productId}: помилка`);
      }
    }
    setBulkManualCleanupBusy(false);
    setSelectedIds(new Set());
    fetchItems();
    fetchStats();
    if (errors.length) notify.warning({ message: 'Оброблено частково', description: `${success} підтверджено · ${errors.length} помилок`, duration: 9 });
    else notify.success({ message: `Публікації ${platformLabel} підтверджено прибраними`, description: `${success} товарів`, duration: 7 });
  };

  const handleRowClick = async (productId: number | null) => {
    if (!productId) return;
    if (expandedId === productId) {
      setExpandedId(null);
      setExpandedDetail(null);
      return;
    }
    setExpandedId(productId);
    setExpandedLoading(true);
    try {
      const res = await fetch(`/api/publications/product-detail/${productId}`);
      if (res.ok) setExpandedDetail(await res.json());
    } catch { /* ignore */ }
    finally { setExpandedLoading(false); }
  };

  const toggleSelect = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    const selectableIds = items.filter(i => i.product_id !== null && !i.is_unlinked).map(i => i.product_id as number);
    if (selectedIds.size === selectableIds.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(selectableIds));
    }
  };

  return (
    <MainLayout
      filterPanelContent={pubTab !== 'list' ? (
        // Фільтри звужують СПИСОК публікацій і ні на що інше не впливають.
        // Показувати їх над вітриною чи сторіс — значить пропонувати дію, яка
        // мовчки перебудує сусідню підвкладку.
        <div className="space-y-3">
          <h3 className="text-md font-semibold text-gray-700 dark:text-gray-200">Фільтри пошуку</h3>
          <p className="text-xs leading-relaxed text-gray-400 dark:text-gray-500">
            Фільтри стосуються лише вкладки «Усі публікації». Тут вони нічого не змінюють —
            вітрина й сторіс мають власні критерії.
          </p>
          <button
            type="button"
            onClick={() => setPubTab('list')}
            className="text-xs text-gray-500 underline decoration-dotted underline-offset-4 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
          >
            Перейти до списку публікацій →
          </button>
        </div>
      ) : (
        <PublicationsFilterPanel
          filterMode={filterMode}
          onFilterChange={(m) => {
            setFilterMode(m);
            setPlatform(m === 'problematic' || m === 'unlinked' ? 'telegram' : 'all');
            setSelectedIds(new Set());
            setPage(1);
          }}
          platform={platform}
          onPlatformChange={(nextPlatform) => { setPlatform(nextPlatform); setSelectedIds(new Set()); setPage(1); }}
          stats={stats}
        />
      )}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      // Скидання теж належить списку: поза ним кнопка не має нишком
      // перебудовувати вибірку, якої зараз не видно.
      onResetFilters={pubTab === 'list' ? handleResetFilters : () => undefined}
    >
      <div className="p-4 pb-24 bg-white dark:bg-gray-800 shadow-md rounded-lg w-full">
        <div className="sticky top-0 z-20 bg-white/90 dark:bg-gray-800/90 backdrop-blur px-2 py-2 -mx-2 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center mb-3">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">
            Публікації
            <span className="ml-2 text-base font-normal text-gray-400">({total})</span>
          </h1>
          <div className="flex items-center gap-2">
            {currentSearchTerm && (
              <span className="text-sm text-gray-500 dark:text-gray-400">Пошук: «{currentSearchTerm}»</span>
            )}
            {/* Усі інтеграції — за однією кнопкою, щоб тулбар був чистим */}
            <button
              onClick={() => setIntegrationsOpen(true)}
              className="px-3 py-1.5 text-sm bg-gray-800 hover:bg-gray-900 dark:bg-gray-200 dark:hover:bg-white text-white dark:text-gray-900 rounded transition-colors flex items-center gap-1.5"
              title="Синхронізація та керування каналами: Telegram, Instagram, Viber, OLX, Prom"
            >
              ⚙ Інтеграції
              {promStatus?.token_expiring_soon && <span className="w-2 h-2 rounded-full bg-amber-400" title="Токен Prom спливає" />}
            </button>
          </div>
        </div>

        {/* ── Підвкладки ────────────────────────────────────────────── */}
        <div className="bms-subtabs" role="tablist" aria-label="Розділи публікацій">
          {PUB_TABS.map(tab => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={pubTab === tab.key}
              onClick={() => setPubTab(tab.key)}
              className={`bms-subtab ${pubTab === tab.key ? 'is-active' : ''}`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {pubTab === 'list' && (<>

        {/* Банер: токен Prom спливає скоро → нагадати замінити */}
        {promStatus?.token_expiring_soon && (
          <div className="mb-3 p-2 text-sm bg-amber-50 dark:bg-amber-900/20 border border-amber-300 dark:border-amber-700 rounded text-amber-800 dark:text-amber-300">
            ⚠️ Термін API-токена Prom спливає через {promStatus.token_days_left} дн.
            (до {promStatus.token_expires_at?.slice(0, 10)}). Створи новий у кабінеті Prom і онови токен.
          </div>
        )}

        {syncAllMsg && (
          <div className="mb-3 p-2 text-sm bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-700 rounded">
            {syncAllMsg}
          </div>
        )}

        {loading && items.length === 0 ? (
          <LoadingSpinner variant="section" size="large" text="Завантаження публікацій…" />
        ) : error ? (
          /* Помилка мусить мати вихід: фоновий джоб інколи віддає 500 на
             частку секунди, і без кнопки вкладка лишалась би мертвою до
             перезавантаження вікна. */
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <WarningOutlined style={{ fontSize: 28 }} className="text-rose-400" />
            <div className="text-sm text-gray-600 dark:text-gray-300">Не вдалося завантажити список</div>
            <div className="text-xs text-gray-400">{error}</div>
            <button
              onClick={handleRefresh}
              className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
            >
              Спробувати ще раз
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-2 text-gray-400">
            <div className="text-sm text-gray-600 dark:text-gray-300">
              {filterMode === 'unpublished' ? 'Усі товари вже опубліковані'
                : filterMode === 'problematic' ? 'Проданих публікацій для прибирання немає'
                : filterMode === 'pending' ? 'Черга порожня'
                : 'Публікацій ще немає'}
            </div>
            <div className="text-xs">
              {filterMode === 'unpublished'
                ? 'Змініть фільтр зліва, щоб побачити інші товари.'
                : filterMode === 'problematic'
                  ? 'На обраному майданчику все прибрано.'
                  : 'Відкрийте «Інтеграції» → «Синхронізувати все», щоб оновити стани.'}
            </div>
          </div>
        ) : (
          <>
          {/* Bulk action bar */}
          {selectedIds.size > 0 && (
            <div className="mb-3 flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-900/40">
              <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
                Обрано: {selectedIds.size} товарів
              </span>
              <div className="flex flex-wrap items-center justify-end gap-2">
                {filterMode !== 'problematic' && <button type="button" onClick={() => setTgBatchIds(Array.from(selectedIds))} disabled={tgBusy}
                        className="flex items-center gap-1.5 rounded bg-[#229ED9] px-4 py-2 text-sm font-medium text-white transition hover:brightness-110 disabled:opacity-50">
                  <SendOutlined /> Опублікувати у Telegram
                </button>}
                {filterMode !== 'problematic' && <button type="button" onClick={() => setViberBatchIds(Array.from(selectedIds))} disabled={viberBusy}
                        className="flex items-center gap-1.5 rounded bg-[#7360F2] px-4 py-2 text-sm font-medium text-white transition hover:brightness-110 disabled:opacity-50">
                  <span className="font-black">V</span> Опублікувати у Viber
                </button>}
                {filterMode !== 'problematic' && <button type="button" onClick={() => setInstagramBatchIds(Array.from(selectedIds))}
                        className="flex items-center gap-1.5 rounded bg-gradient-to-r from-[#833AB4] to-[#E1306C] px-4 py-2 text-sm font-medium text-white transition hover:brightness-110">
                  <InstagramMark className="h-4 w-4 text-[10px]" /> Підготувати для Instagram
                </button>}
                {(filterMode !== 'problematic' || platform === 'telegram') && <button
                  onClick={handleBulkUnpublish}
                  disabled={bulkUnpublishing || !items.some(item => item.product_id !== null && selectedIds.has(item.product_id) && (item.telegram_publication_count ?? item.publication_count) > 0)}
                  title="Стосується лише Telegram: Viber Channels API не має безпечної дії видалення поста"
                  className="rounded bg-red-600 px-4 py-2 text-sm text-white transition-colors hover:bg-red-700 disabled:opacity-50"
                >
                  {bulkUnpublishing ? 'Знімаю...' : `🗑 Зняти з Telegram`}
                </button>}
                {filterMode === 'problematic' && !!MANUAL_CLEANUP_LABELS[platform] && (
                  <button type="button" onClick={handleBulkManualCleanup} disabled={bulkManualCleanupBusy}
                    className="inline-flex items-center gap-1.5 rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-700 disabled:opacity-50"
                    title="Лише підтверджує фактичне ручне видалення; BMS не видаляє пост на майданчику">
                    {bulkManualCleanupBusy ? 'Підтверджую…' : '✓ Позначити прибраними'}
                  </button>
                )}
              </div>
            </div>
          )}

          <div className="overflow-x-auto rounded border border-gray-200 dark:border-gray-700" onContextMenu={handlePubContextMenu}>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600">
                <tr>
                  <th className="px-2 py-3 text-center w-8">
                    <input type="checkbox" checked={selectedIds.size > 0 && selectedIds.size === items.filter(i => i.product_id !== null && !i.is_unlinked).length} onChange={toggleSelectAll} className="rounded" />
                  </th>
                  {isPubColVisible('productnumber')      && <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Номер</th>}
                  {isPubColVisible('brand_name')         && <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Бренд</th>}
                  {isPubColVisible('type_name')          && <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Вид</th>}
                  {isPubColVisible('subtype_name')       && <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Підвид</th>}
                  {isPubColVisible('model')              && <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Модель</th>}
                  {isPubColVisible('marking')            && <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Маркування</th>}
                  {isPubColVisible('year')               && <th className="px-3 py-3 text-center font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Рік</th>}
                  {isPubColVisible('sizeeu')             && <th className="px-3 py-3 text-center font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Розмір</th>}
                  {isPubColVisible('price')              && <th className="px-3 py-3 text-right font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Ціна</th>}
                  {isPubColVisible('status')             && <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Статус</th>}
                  {isPubColVisible('publication_count')  && <th className="px-3 py-3 text-center font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Постів</th>}
                  {isPubColVisible('channels')           && <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Канали / Гілки</th>}
                  {isPubColVisible('actions')            && <th className="px-3 py-3 text-center font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Дії</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {items.map((item, idx) => {
                  const isProblematic = filterMode === 'problematic';
                  const isUnlinked = item.is_unlinked === true;
                  const needsManualEdit = item.needs_manual_edit === true;
                  const isExpanded = expandedId === item.product_id && item.product_id !== null;
                  const visiblePublicationCount = platform === 'telegram'
                    ? (item.telegram_publication_count ?? item.publication_count)
                    : platform === 'instagram'
                      ? (filterMode === 'pending' ? (item.instagram_pending_count || 0) : (item.instagram_publication_count || 0))
                      : platform === 'viber'
                        ? (filterMode === 'pending' ? (item.viber_pending_count || 0) : (item.viber_publication_count || 0))
                        : item.publication_count;
                  const platformSummary = platform === 'telegram'
                    ? `Telegram · ${visiblePublicationCount}`
                    : platform === 'instagram'
                      ? `Instagram · ${visiblePublicationCount}${item.instagram_pending_count ? ` · у черзі ${item.instagram_pending_count}` : ''}`
                      : platform === 'viber'
                        ? `Viber · ${visiblePublicationCount}${item.viber_pending_count ? ` · у черзі ${item.viber_pending_count}` : ''}`
                        : null;
                  return (
                    <React.Fragment key={isUnlinked ? `unlinked-${idx}` : item.product_id}>
                    <tr
                      className={`hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors cursor-pointer ${
                        isExpanded ? 'bg-blue-50 dark:bg-blue-900/20' :
                        needsManualEdit ? 'bg-yellow-50 dark:bg-yellow-900/20' :
                        isUnlinked ? 'bg-orange-50 dark:bg-orange-900/20' :
                        isProblematic ? 'bg-red-50 dark:bg-red-900/20' : ''
                      }`}
                      onClick={() => handleRowClick(item.product_id)}
                    >
                      <td className="px-2 py-2 text-center w-8" onClick={e => e.stopPropagation()}>
                        {!isUnlinked && item.product_id !== null ? (
                          <input type="checkbox" checked={selectedIds.has(item.product_id as number)} onChange={() => toggleSelect(item.product_id as number)} className="rounded" />
                        ) : <span className="text-gray-300">—</span>}
                      </td>
                      {isPubColVisible('productnumber') && (
                      <td className="px-3 py-2 font-mono text-xs text-gray-900 dark:text-gray-100 whitespace-nowrap">
                        {item.product_id ? (
                          <span
                            className="cursor-pointer text-blue-600 hover:text-blue-800 hover:underline dark:text-blue-400 dark:hover:text-blue-300"
                            title="Відкрити картку товару"
                            onClick={(e) => { e.stopPropagation(); setCardProductId(item.product_id as number); }}
                          >
                            {item.productnumber}
                          </span>
                        ) : item.productnumber}
                      </td>
                      )}
                      {isPubColVisible('brand_name') && (
                      <td className="px-3 py-2 text-gray-800 dark:text-gray-200 whitespace-nowrap">
                        {item.brand_name || <span className="text-gray-400">—</span>}
                      </td>
                      )}
                      {isPubColVisible('type_name') && (
                      <td className="px-3 py-2 text-gray-800 dark:text-gray-200 whitespace-nowrap">
                        {item.type_name || <span className="text-gray-400">—</span>}
                      </td>
                      )}
                      {isPubColVisible('subtype_name') && (
                      <td className="px-3 py-2 text-gray-700 dark:text-gray-300 whitespace-nowrap">
                        {item.subtype_name || <span className="text-gray-400">—</span>}
                      </td>
                      )}
                      {isPubColVisible('model') && (
                      <td className="px-3 py-2 truncate max-w-xs">
                        {item.model ? (() => {
                          const q = (item.model || '').trim();
                          return q ? (
                            <a
                              href={`https://www.google.com/search?q=${encodeURIComponent(q)}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="cursor-pointer text-blue-600 hover:text-blue-800 hover:underline dark:text-blue-400 dark:hover:text-blue-300"
                              title="Пошук в Google"
                              onClick={(e) => e.stopPropagation()}
                            >{item.model}</a>
                          ) : <span className="text-gray-700 dark:text-gray-300">{item.model}</span>;
                        })() : <span className="text-gray-700 dark:text-gray-300">—</span>}
                      </td>
                      )}
                      {isPubColVisible('marking') && (
                      <td className="px-3 py-2 text-xs text-gray-700 dark:text-gray-300 whitespace-nowrap">
                        {item.marking || <span className="text-gray-400">—</span>}
                      </td>
                      )}
                      {isPubColVisible('year') && (
                      <td className="px-3 py-2 text-center text-gray-700 dark:text-gray-300">
                        {item.year || <span className="text-gray-400">—</span>}
                      </td>
                      )}
                      {isPubColVisible('sizeeu') && (
                      <td className="px-3 py-2 text-center text-gray-700 dark:text-gray-300 whitespace-nowrap">
                        {item.sizeeu || <span className="text-gray-400">—</span>}
                      </td>
                      )}
                      {isPubColVisible('price') && (
                      <td className="px-3 py-2 text-right text-gray-800 dark:text-gray-200 whitespace-nowrap font-medium">
                        {item.price != null ? `${item.price}₴` : <span className="text-gray-400">—</span>}
                      </td>
                      )}
                      {isPubColVisible('status') && (
                      <td className="px-3 py-2">
                        {/* Стан рядка — пігулка з вектор-іконкою, а не емодзі-стікер:
                            той самий словник форм, що в решті таблиць програми. */}
                        <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-md border ${
                          needsManualEdit
                            ? 'bg-amber-50 dark:bg-amber-900/25 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-800'
                            : isUnlinked
                            ? 'bg-amber-50 dark:bg-amber-900/25 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-800'
                            : isProblematic
                            ? 'bg-rose-50 dark:bg-rose-900/25 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-800'
                            : 'bg-gray-50 dark:bg-gray-700/60 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-600'
                        }`}>
                          {needsManualEdit ? <><EditOutlined style={{ fontSize: 10 }} />Правити вручну</>
                            : isUnlinked ? <><DisconnectOutlined style={{ fontSize: 10 }} />Незвʼязаний</>
                            : isProblematic ? <><WarningOutlined style={{ fontSize: 10 }} />Продано</>
                            : (item.status || '—')}
                        </span>
                        {needsManualEdit && item.product_id && (
                          <button
                            className="ml-2 text-xs px-2 py-0.5 rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 hover:bg-green-200 dark:hover:bg-green-800/40 transition-colors"
                            title="Натисніть коли виправите пост вручну в Telegram"
                            onClick={async (e) => {
                              e.stopPropagation();
                              try {
                                await fetch(`/api/publications/clear-manual-edit/${item.product_id}`, { method: 'POST' });
                                fetchItems();
                              } catch {}
                            }}
                          >
                            Виправлено
                          </button>
                        )}
                      </td>
                      )}
                      {isPubColVisible('publication_count') && (
                      <td className="px-3 py-2 text-center">
                        <span className={`font-medium ${visiblePublicationCount > 0 ? 'text-blue-600 dark:text-blue-400' : 'text-gray-400'}`}>
                          {visiblePublicationCount}
                        </span>
                      </td>
                      )}
                      {isPubColVisible('channels') && (
                      <td className="px-3 py-2 text-xs text-gray-600 dark:text-gray-400 truncate max-w-md" title={`${item.channels}\n${item.threads}`}>
                        {platformSummary || item.channels || '—'}
                        {!platformSummary && item.threads && item.threads !== '—' && (
                          <span className="text-gray-400 ml-1">/ {item.threads}</span>
                        )}
                      </td>
                      )}
                      {isPubColVisible('actions') && (
                      <td className="px-3 py-2 text-center whitespace-nowrap" onClick={e => e.stopPropagation()}>
                        {!isUnlinked && item.product_id !== null && (
                          <div className="flex items-center justify-center gap-1">
                            {/* Опублікувати — головна дія для товару без постів;
                                для вже опублікованого лишається доступною
                                (наприклад, після зняття з продажу), але тьмяна. */}
                            {filterMode !== 'problematic' && <button
                              onClick={() => openPublishDialog(item.product_id as number)}
                              disabled={tgPreviewing === item.product_id}
                              title={item.publication_count > 0
                                ? 'Створити ще один пост (старі лишаться)'
                                : 'Створити пост у Telegram'}
                              className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium border transition-colors disabled:opacity-50 ${
                                item.publication_count > 0
                                  ? 'border-gray-200 dark:border-gray-600 text-gray-400 hover:text-sky-600 hover:border-sky-300'
                                  : 'border-sky-300 dark:border-sky-700 text-sky-600 dark:text-sky-400 bg-sky-50/60 dark:bg-sky-900/20 hover:bg-sky-100 dark:hover:bg-sky-900/40'
                              }`}
                            >
                              <SendOutlined style={{ fontSize: 11 }} />
                              {tgPreviewing === item.product_id ? '…' : 'Опублікувати'}
                            </button>}
                            {filterMode !== 'problematic' && <button
                              onClick={() => openViberDialog(item.product_id as number)}
                              disabled={viberPreviewing === item.product_id}
                              title="Створити колаж і пост у Viber"
                              className="inline-flex items-center gap-1 rounded-md border border-violet-300 bg-violet-50/60 px-2 py-1 text-xs font-medium text-violet-600 transition-colors hover:bg-violet-100 disabled:opacity-50 dark:border-violet-700 dark:bg-violet-900/20 dark:text-violet-300 dark:hover:bg-violet-900/40"
                            >
                              <span className="font-black">V</span>{viberPreviewing === item.product_id ? '…' : 'Viber'}
                            </button>}
                            {filterMode !== 'problematic' && <button
                              onClick={() => openFacebookDialog(item.product_id as number)}
                              disabled={facebookPreviewing === item.product_id}
                              title="Підготувати й перевірити Facebook-чернетку без надсилання"
                              className="inline-flex items-center gap-1 rounded-md border border-blue-300 bg-blue-50/60 px-2 py-1 text-xs font-medium text-[#1877F2] transition-colors hover:bg-blue-100 disabled:opacity-50 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-300 dark:hover:bg-blue-900/40"
                            >
                              <FacebookMark className="h-4 w-4 text-[9px]" />{facebookPreviewing === item.product_id ? '…' : 'Facebook'}
                            </button>}
                            {filterMode !== 'problematic' && <button
                              onClick={() => openInstagramDialog(item.product_id as number)}
                              disabled={instagramPreviewing === item.product_id}
                              title="Підготувати й перевірити Instagram-чернетку без надсилання"
                              className="inline-flex items-center gap-1 rounded-md border border-pink-300 bg-pink-50/60 px-2 py-1 text-xs font-medium text-pink-600 transition-colors hover:bg-pink-100 disabled:opacity-50 dark:border-pink-800 dark:bg-pink-900/20 dark:text-pink-300 dark:hover:bg-pink-900/40"
                            >
                              <InstagramMark className="h-4 w-4 text-[9px]" />{instagramPreviewing === item.product_id ? '…' : 'Instagram'}
                            </button>}
                            {item.publication_count > 0 && (
                              <>
                                <button
                                  onClick={() => setDetailProductId(item.product_id as number)}
                                  title="Показати тексти постів"
                                  className="p-1 rounded-md text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
                                >
                                  <EyeOutlined style={{ fontSize: 13 }} />
                                </button>
                                {(item.telegram_publication_count ?? item.publication_count) > 0 && (filterMode !== 'problematic' || platform === 'telegram') && (
                                  <button
                                    onClick={() => handleUnpublish(item.product_id as number)}
                                    disabled={unpublishing === item.product_id}
                                    className="p-1 rounded-md text-gray-400 hover:text-red-600 dark:hover:text-red-400 transition-colors disabled:opacity-50"
                                    title="Переслати Telegram-пост у WORKSHOP і видалити з Telegram-каналів"
                                  >
                                    {unpublishing === item.product_id
                                      ? <span className="text-xs">…</span>
                                      : <DeleteOutlined style={{ fontSize: 13 }} />}
                                  </button>
                                )}
                              </>
                            )}
                            {filterMode === 'problematic' && platform === 'instagram' && (
                              <>
                                {item.instagram_permalink && (
                                  <a href={item.instagram_permalink} target="_blank" rel="noreferrer"
                                    className="inline-flex items-center gap-1 rounded-md border border-pink-300 bg-pink-50 px-2 py-1 text-xs font-medium text-pink-600 hover:bg-pink-100 dark:border-pink-800 dark:bg-pink-950/20 dark:text-pink-300"
                                    title="Відкрити опублікований пост в Instagram">
                                    <InstagramMark className="h-4 w-4 text-[9px]" /> Відкрити пост ↗
                                  </a>
                                )}
                                <button type="button" onClick={() => handleManualCleanup(item.product_id as number, 'instagram')}
                                  disabled={manualCleanupBusy === item.product_id}
                                  className="inline-flex items-center gap-1 rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-300"
                                  title="Натисніть тільки після фактичного видалення поста в Instagram">
                                  {manualCleanupBusy === item.product_id ? '…' : '✓ Видалено вручну'}
                                </button>
                              </>
                            )}
                            {filterMode === 'problematic' && platform === 'facebook' && (
                              <>
                                {item.facebook_permalink && (
                                  <a href={item.facebook_permalink} target="_blank" rel="noreferrer"
                                    className="inline-flex items-center gap-1 rounded-md border border-blue-300 bg-blue-50 px-2 py-1 text-xs font-medium text-[#1877F2] hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-950/20 dark:text-blue-300"
                                    title="Відкрити опублікований допис у Facebook">
                                    <FacebookMark className="h-4 w-4 text-[9px]" /> Відкрити допис ↗
                                  </a>
                                )}
                                <button type="button" onClick={() => handleManualCleanup(item.product_id as number, 'facebook')}
                                  disabled={manualCleanupBusy === item.product_id}
                                  className="inline-flex items-center gap-1 rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-300"
                                  title="Натисніть тільки після фактичного видалення допису у Facebook">
                                  {manualCleanupBusy === item.product_id ? '…' : '✓ Видалено вручну'}
                                </button>
                              </>
                            )}
                            {filterMode === 'problematic' && platform === 'viber' && (
                              <button type="button" onClick={() => handleManualCleanup(item.product_id as number, 'viber')}
                                disabled={manualCleanupBusy === item.product_id}
                                className="inline-flex items-center gap-1 rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-300"
                                title="Натисніть тільки після фактичного прибирання поста у Viber">
                                {manualCleanupBusy === item.product_id ? '…' : '✓ Прибрано вручну'}
                              </button>
                            )}
                          </div>
                        )}
                        {isUnlinked && (
                          <span className="text-xs text-amber-500 dark:text-amber-400" title="Немає відповідника в базі товарів">
                            —
                          </span>
                        )}
                      </td>
                      )}
                    </tr>
                    {/* ── Expanded detail row ── */}
                    {isExpanded && (
                      <tr className="bg-blue-50/60 dark:bg-blue-900/10">
                        <td colSpan={1 + PUB_COLUMN_ORDER.filter(c => pubColumnsVisible[c.id]).length} className="px-6 py-3">
                          {expandedLoading ? (
                            <div className="text-xs text-gray-400 py-2">Завантаження...</div>
                          ) : expandedDetail ? (
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                              {/* Sizes table */}
                              <div>
                                <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-2">
                                  Розміри ({expandedDetail.productnumber}):
                                </div>
                                <table className="w-full text-xs">
                                  <thead>
                                    <tr className="text-gray-500 dark:text-gray-400">
                                      <th className="text-left pr-3 pb-1">Розмір</th>
                                      <th className="text-left pr-3 pb-1">Статус</th>
                                      <th className="text-left pb-1">Ціна</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {expandedDetail.sizes?.map((sz: any, i: number) => (
                                      <tr key={i} className={sz.status === 'Продано' ? 'text-red-600 dark:text-red-400' : 'text-gray-700 dark:text-gray-300'}>
                                        <td className="pr-3 py-0.5 font-mono">{sz.size || '—'}</td>
                                        <td className="pr-3 py-0.5">
                                          <span className={`px-1.5 py-0.5 rounded ${
                                            sz.status === 'Продано' ? 'bg-red-100 dark:bg-red-900/30 font-semibold' : 'bg-green-100 dark:bg-green-900/30'
                                          }`}>{sz.status}</span>
                                        </td>
                                        <td className="py-0.5">{sz.price ? `${sz.price} грн` : '—'}</td>
                                      </tr>
                                    ))}
                                    {(!expandedDetail.sizes || expandedDetail.sizes.length === 0) && (
                                      <tr><td colSpan={3} className="text-gray-400 py-1">Немає даних</td></tr>
                                    )}
                                  </tbody>
                                </table>
                              </div>
                              {/* Buyers */}
                              <div>
                                <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-2">
                                  Покупці:
                                </div>
                                {expandedDetail.buyers?.length > 0 ? (
                                  <table className="w-full text-xs">
                                    <thead>
                                      <tr className="text-gray-500 dark:text-gray-400">
                                        <th className="text-left pr-3 pb-1">Розмір</th>
                                        <th className="text-left pr-3 pb-1">Клієнт</th>
                                        <th className="text-left pr-3 pb-1">Замовлення</th>
                                        <th className="text-left pb-1">Дата</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {expandedDetail.buyers.map((b: any, i: number) => (
                                        <tr key={i} className="text-gray-700 dark:text-gray-300">
                                          <td className="pr-3 py-0.5 font-mono">{b.size || '—'}</td>
                                          <td className="pr-3 py-0.5">{b.client_name}</td>
                                          <td className="pr-3 py-0.5">#{b.order_id} ({b.order_status})</td>
                                          <td className="py-0.5">{b.order_date ? new Date(b.order_date).toLocaleDateString('uk-UA') : '—'}</td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                ) : (
                                  <div className="text-xs text-gray-400">Немає замовлень</div>
                                )}
                              </div>
                            </div>
                          ) : (
                            <div className="text-xs text-gray-400 py-2">Помилка завантаження</div>
                          )}
                        </td>
                      </tr>
                    )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          </>
        )}
        </>)}

        {pubTab === 'catalog' && <CatalogAnalyticsPanel showAutomation />}
        {pubTab === 'stories' && <StoryAutomationPanel />}

      </div>

      {/* Контекстне меню керування колонками */}
      {colMenuOpen && (
        <div
          ref={colMenuRef}
          style={{ top: colMenuPos.y, left: colMenuPos.x }}
          className="fixed z-[10000] w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg p-2"
        >
          <div className="px-2 py-1 text-xs text-gray-500">Видимість колонок</div>
          <div className="max-h-80 overflow-auto pr-1">
            {PUB_COLUMN_ORDER.map(c => (
              <label key={c.id} className="flex items-center justify-between px-2 py-1 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 rounded">
                <span>{c.title}</span>
                <input
                  type="checkbox"
                  checked={!!pubColumnsVisible[c.id]}
                  onChange={(e) => setPubColumnsVisible(v => ({ ...v, [c.id]: e.target.checked }))}
                />
              </label>
            ))}
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2">
            <button className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-100 dark:border-gray-600 dark:hover:bg-gray-700"
              onClick={() => setPubColumnsVisible(PUB_COLUMN_ORDER.reduce((a, c) => (a[c.id] = true, a), {} as Record<PubColumnId, boolean>))}
            >Всі</button>
            <button className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-100 dark:border-gray-600 dark:hover:bg-gray-700"
              onClick={() => setPubColumnsVisible(PUB_COLUMN_ORDER.reduce((a, c) => (a[c.id] = false, a), {} as Record<PubColumnId, boolean>))}
            >Приховати</button>
            <button className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-100 dark:border-gray-600 dark:hover:bg-gray-700"
              onClick={() => setPubColumnsVisible(defaultPubVisibility)}
            >За умовч.</button>
          </div>
        </div>
      )}

      {/* Pagination — лише для списку: на вкладках вітрини й сторіс гортати нічого */}
      {pubTab === 'list' && (
      <div className="fixed bottom-0 left-0 right-0 px-4 py-3 bg-white/95 dark:bg-gray-800/95 backdrop-blur border-t border-gray-200 dark:border-gray-700 z-30">
        <div className="w-full grid grid-cols-[1fr_auto_1fr] items-center gap-4 max-w-screen-2xl mx-auto px-2">
          <div className="justify-self-start flex items-center gap-6">
            <label className="inline-flex items-center text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={onlyUnsold && filterMode !== 'problematic'}
                disabled={filterMode === 'problematic'}
                onChange={(e) => { setOnlyUnsold(e.target.checked); setPage(1); }}
                title={filterMode === 'problematic' ? 'У цьому режимі треба бачити продані товари, які ще висять на обраному майданчику' : undefined}
                className="h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 dark:focus:ring-primary-400 dark:bg-gray-700 dark:border-gray-600 disabled:opacity-50"
              />
              <span className="ml-2">Тільки непродані</span>
            </label>
            <label className="inline-flex items-center text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={onlyRostovka}
                onChange={(e) => { setOnlyRostovka(e.target.checked); setPage(1); }}
                className="h-4 w-4 text-blue-500 border-gray-300 rounded focus:ring-blue-400 dark:focus:ring-blue-400 dark:bg-gray-700 dark:border-gray-600"
              />
              <span className="ml-2">Тільки ростовки</span>
            </label>
          </div>
          <div className="justify-self-center flex justify-center">
            <Pagination
              currentPage={page}
              totalPages={pages}
              totalItems={total}
              itemsPerPage={perPage}
              onPageChange={setPage}
              onPerPageChange={(n) => { setPerPage(n); setPage(1); }}
            />
          </div>
          <span />
        </div>
      </div>
      )}

      <SyncModal
        open={syncOpen}
        onClose={() => setSyncOpen(false)}
        onSyncComplete={() => { fetchItems(); fetchStats(); }}
      />
      <DetailModal
        productId={detailProductId}
        onClose={() => setDetailProductId(null)}
        onChanged={() => { fetchItems(); fetchStats(); }}
      />
      <ProductDetailsModal
        productId={cardProductId}
        open={cardProductId !== null}
        onClose={() => setCardProductId(null)}
      />

      {/* Створення поста: редагування тексту й вибір гілок перед відправкою */}
      {tgPreview && (
        <TelegramPublishDialog
          data={tgPreview}
          busy={tgBusy}
          onPreviewChange={setTgPreview}
          onCancel={() => { if (!tgBusy) setTgPreview(null); }}
          onConfirm={handlePublish}
        />
      )}
      {tgBatchIds && (
        <TelegramBatchPublishDialog
          productIds={tgBatchIds}
          busy={tgBusy}
          onCancel={() => { if (!tgBusy) setTgBatchIds(null); }}
          onPublish={handleTelegramBatchPublish}
        />
      )}
      {viberPreview && (
        <ViberPublishDialog
          data={viberPreview}
          busy={viberBusy}
          onPreviewChange={setViberPreview}
          onCancel={() => { if (!viberBusy) setViberPreview(null); }}
          onConfirm={handleViberPublish}
        />
      )}
      {viberBatchIds && (
        <ViberBatchPublishDialog
          productIds={viberBatchIds}
          busy={viberBusy}
          onCancel={() => { if (!viberBusy) setViberBatchIds(null); }}
          onPublish={handleViberBatchPublish}
        />
      )}
      {instagramPreview && (
        <InstagramPublishDialog
          data={instagramPreview}
          busy={instagramBusy}
          onCancel={() => { if (!instagramBusy) setInstagramPreview(null); }}
          onConfirm={handleInstagramPublish}
        />
      )}
      {facebookPreview && (
        <FacebookPublishDialog
          data={facebookPreview}
          busy={facebookBusy}
          onCancel={() => { if (!facebookBusy) setFacebookPreview(null); }}
          onConfirm={handleFacebookPublish}
        />
      )}
      {instagramBatchIds && (
        <InstagramBatchDraftDialog
          productIds={instagramBatchIds}
          busy={instagramBusy}
          onCancel={() => { if (!instagramBusy) setInstagramBatchIds(null); }}
          onPublish={handleInstagramBatchPublish}
        />
      )}

      {/* Панель «Інтеграції» — керування каналами в одному місці */}
      {integrationsOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
             onClick={() => setIntegrationsOpen(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-xl max-w-4xl w-full max-h-[85vh] overflow-auto p-5"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-lg font-semibold">⚙ Інтеграції</h3>
              <button onClick={() => setIntegrationsOpen(false)} className="text-gray-400 hover:text-gray-700 text-xl">✕</button>
            </div>
            <p className="text-xs text-gray-500 mb-4">Синхронізація й так відбувається автоматично: Telegram/OLX — кожні 30 хв, Prom — кожні ~10 хв. Viber та Instagram працюють через окремі захищені хмарні диспетчери з чергами й розкладом.</p>
            {syncAllMsg && (
              <div className="mb-4 p-2 text-sm bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-700 rounded">{syncAllMsg}</div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {/* Telegram */}
              <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-3 flex flex-col gap-2">
                <div className="flex items-center gap-2 font-medium">
                  <img src="/media-logos/telegram-logo.png" alt="" className="h-5" /> Telegram
                </div>
                <span className="text-xs text-green-600 dark:text-green-400">● активно (авто кожні 30 хв)</span>
                <button onClick={handleSyncAll} disabled={syncingAll}
                  className="mt-1 px-3 py-1.5 text-sm bg-green-600 hover:bg-green-700 disabled:opacity-60 text-white rounded">
                  {syncingAll ? '⏳ Скануємо…' : '🔄 Синхронізувати все'}
                </button>
                <button onClick={() => { setIntegrationsOpen(false); setSyncOpen(true); }}
                  className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded">📡 Один канал</button>
                <button onClick={handleRefreshThreads} disabled={syncingAll}
                  title="Перечитати список тематичних гілок форуму — саме з нього діалог публікації пропонує, куди класти товар"
                  className="px-3 py-1.5 text-xs bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded disabled:opacity-60">🗂 Оновити гілки форуму</button>
                <button onClick={handleRelink}
                  className="px-3 py-1.5 text-xs bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded">🔗 Перепов'язати</button>
              </div>
              {/* Viber Channel */}
              <div className="flex flex-col gap-2 rounded-lg border border-gray-200 p-3 dark:border-gray-700">
                <div className="flex items-center gap-2 font-medium">
                  <span className="inline-flex h-5 w-5 items-center justify-center rounded bg-[#7360F2] text-[10px] font-black text-white">V</span> Viber Channel
                </div>
                <span className={`text-xs ${viberStatus?.configured ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}`}>
                  {viberStatus?.configured ? '● захищене підключення активне' : '◐ редактор готовий · відправлення ще не підключене'}
                </span>
                <span className="text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
                  Один пост = колаж 1080×1080 із 1–5 фото + підпис. Пакети й розклад підтримуються.
                </span>
                {!viberStatus?.configured && !!viberStatus?.missing?.length && (
                  <span className="text-[10px] leading-relaxed text-gray-400">Очікує: {viberStatus.missing.join(', ')}</span>
                )}
                <button type="button" onClick={handleViberStatusSync} disabled={viberSyncing}
                        className="mt-auto rounded bg-violet-50 px-3 py-1.5 text-xs text-violet-700 hover:bg-violet-100 dark:bg-violet-900/25 dark:text-violet-300 dark:hover:bg-violet-900/40">
                  {viberSyncing ? 'Оновлюю…' : viberStatus?.configured ? 'Оновити стани публікацій' : 'Перевірити стан'}
                </button>
              </div>
              {/* Instagram Platform */}
              <div className="flex flex-col gap-2 rounded-lg border border-gray-200 p-3 dark:border-gray-700">
                <div className="flex items-center gap-2 font-medium">
                  <InstagramMark className="h-5 w-5 text-xs" /> Instagram
                </div>
                <span className={`text-xs ${instagramStatus?.live_publish_available ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}`}>
                  {instagramStatus?.live_publish_available ? '● OAuth, Worker і розклад активні' : instagramStatus?.oauth_connected ? '◐ OAuth підключено · живий режим Worker вимкнено' : '◐ редактор і renderer готові · очікується OAuth'}
                </span>
                <span className="text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
                  Feed, каруселі до {instagramStatus?.limits?.carousel_media || 10} фото, Stories та Reels зі слайдів. Доступні crop/zoom, власний текст, пакетна перевірка й розклад.
                </span>
                <span className="text-[11px] leading-relaxed text-amber-600 dark:text-amber-400">
                  {instagramStatus?.live_publish_available
                    ? `Публікації йдуть у захищену щохвилинну чергу для @${String(instagramStatus?.account || 'brandxstoreua').replace(/^@/, '')}.`
                    : `Жодна чернетка не може випадково піти в @${String(instagramStatus?.account || 'brandxstoreua').replace(/^@/, '')}.`}
                </span>
                <div className="mt-auto flex gap-2">
                  {!instagramStatus?.oauth_connected && <button type="button" onClick={handleInstagramConnect}
                          className="flex-1 rounded bg-pink-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-pink-700">Підключити OAuth</button>}
                  <button type="button" onClick={handleInstagramStatusSync} disabled={instagramSyncing}
                          className="flex-1 rounded bg-pink-50 px-3 py-1.5 text-xs text-pink-700 hover:bg-pink-100 dark:bg-pink-900/25 dark:text-pink-300 dark:hover:bg-pink-900/40">
                    {instagramSyncing ? 'Оновлюю…' : instagramStatus?.oauth_connected ? 'Оновити стани публікацій' : 'Перевірити стан'}
                  </button>
                </div>
              </div>
              {/* Facebook Page */}
              <div className="flex flex-col gap-2 rounded-lg border border-gray-200 p-3 dark:border-gray-700">
                <div className="flex items-center gap-2 font-medium">
                  <FacebookMark className="h-5 w-5 text-xs" /> Facebook
                </div>
                <span className={`text-xs ${facebookStatus?.live_publish_available ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}`}>
                  {facebookStatus?.live_publish_available ? '● OAuth, Worker і розклад активні' : facebookStatus?.oauth_connected ? '◐ OAuth підключено · живий режим Worker вимкнено' : '◐ редактор і renderer готові · очікується OAuth'}
                </span>
                <span className="text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
                  Пости, альбоми до {facebookStatus?.limits?.album_media || 10} фото, Stories та Reels зі слайдів. Той самий renderer і той самий текст, що й в Instagram.
                </span>
                <span className="text-[11px] leading-relaxed text-amber-600 dark:text-amber-400">
                  {facebookStatus?.live_publish_available
                    ? `Публікації йдуть у захищену щохвилинну чергу для Сторінки «${facebookStatus?.account || 'Facebook'}».`
                    : `Жодна чернетка не може випадково піти у Сторінку «${facebookStatus?.account || 'Facebook'}».`}
                </span>
                <div className="mt-auto flex gap-2">
                  {!facebookStatus?.oauth_connected && <button type="button" onClick={handleFacebookConnect}
                          className="flex-1 rounded bg-[#1877F2] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#0B5FCC]">Підключити OAuth</button>}
                  <button type="button" onClick={handleFacebookStatusSync} disabled={facebookSyncing}
                          className="flex-1 rounded bg-blue-50 px-3 py-1.5 text-xs text-blue-700 hover:bg-blue-100 dark:bg-blue-900/25 dark:text-blue-300 dark:hover:bg-blue-900/40">
                    {facebookSyncing ? 'Оновлюю…' : facebookStatus?.oauth_connected ? 'Оновити стани публікацій' : 'Перевірити стан'}
                  </button>
                </div>
              </div>
              {/* OLX */}
              <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-3 flex flex-col gap-2">
                <div className="flex items-center gap-2 font-medium">
                  <img src="/media-logos/olx-mark-emerald.png" alt="" className="h-5" /> OLX
                </div>
                <span className={`text-xs ${olxStatus?.authorized ? 'text-green-600 dark:text-green-400' : 'text-gray-400'}`}>
                  {olxStatus?.authorized ? `● підключено (${olxStatus.advert_count ?? 0} оголошень)` : '○ не підключено'}
                </span>
                {!olxStatus?.authorized && (
                  <button onClick={handleOlxConnect}
                    className="mt-1 px-3 py-1.5 text-xs bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded">🔌 Підключити (OAuth)</button>
                )}
                <button onClick={handleOlxSync} disabled={syncingAll}
                  className="mt-1 px-3 py-1.5 text-sm text-white rounded disabled:opacity-60" style={{ backgroundColor: '#002F34' }}>
                  {syncingAll ? '⏳ OLX…' : '🔄 Синхронізувати'}
                </button>
              </div>
              {/* Prom */}
              <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-3 flex flex-col gap-2">
                <div className="flex items-center gap-2 font-medium">
                  <img src="/media-logos/prom-logo.png" alt="" className="h-5 rounded" /> Prom
                </div>
                <span className={`text-xs ${promStatus?.token_expiring_soon ? 'text-amber-600 dark:text-amber-400' : promStatus?.configured ? 'text-green-600 dark:text-green-400' : 'text-gray-400'}`}>
                  {promStatus?.configured
                    ? `● токен ✓${promStatus.token_days_left != null ? ` (${promStatus.token_days_left} дн)` : ''} · ${promStatus.product_count ?? 0} тов., ${promStatus.order_count ?? 0} замовл.`
                    : '○ токен не задано'}
                </span>
                {promStatus?.token_expiring_soon && (
                  <span className="text-xs text-amber-600 dark:text-amber-400">⚠️ Токен спливає — створи новий у кабінеті Prom</span>
                )}
                <button onClick={handlePromSync} disabled={syncingAll}
                  className="mt-1 px-3 py-1.5 text-sm text-white rounded disabled:opacity-60" style={{ backgroundColor: '#5B2D8E' }}>
                  {syncingAll ? '⏳ Prom…' : '🔄 Синхронізувати'}
                </button>
                <button onClick={handlePromPushAvailability} disabled={syncingAll}
                  className="px-3 py-1.5 text-xs bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded disabled:opacity-60">📦 Оновити наявність</button>
                <button onClick={() => { setIntegrationsOpen(false); openPromOrders(); }}
                  className="px-3 py-1.5 text-xs bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded">
                  🧾 Замовлення{promStatus?.order_count ? ` (${promStatus.order_count})` : ''}
                </button>
              </div>
              {/* monoБазар: лише READ-верифікація (публічний API вітрини) — постинг
                  заблокований (немає партнерського доступу), тому кнопки «Опублікувати»
                  тут немає — тільки перегляд реального стану на вітрині monobazar. */}
              <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-3 flex flex-col gap-2">
                <div className="flex items-center gap-2 font-medium">
                  <span className="inline-flex items-center justify-center h-5 w-5 rounded bg-black text-white text-[10px] font-black">m</span>
                  monoБазар
                </div>
                <span className={`text-xs ${monobazarStatus?.seller_username ? 'text-green-600 dark:text-green-400' : 'text-gray-400'}`}>
                  {monobazarStatus?.seller_username
                    ? `● ${monobazarStatus.seller_username} · ${monobazarStatus.tracked ?? 0} оголошень`
                    : '○ username не задано'}
                </span>
                {!!monobazarStatus?.tracked && (
                  <span className="text-[11px] text-gray-500 dark:text-gray-400">
                    {monobazarStatus.confident} підтверджено · {monobazarStatus.ambiguous} неоднозначних · {monobazarStatus.unmatched} без збігу
                  </span>
                )}
                <span className="text-[11px] text-amber-600 dark:text-amber-400">
                  ⚠️ Лише перегляд: створення оголошень поки заблоковано (немає партнерського API)
                </span>
                <button onClick={handleMonobazarSync} disabled={syncingAll}
                  className="mt-1 px-3 py-1.5 text-sm text-white rounded disabled:opacity-60 bg-black hover:bg-gray-800">
                  {syncingAll ? '⏳ monoБазар…' : '🔄 Синхронізувати'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Дзеркало замовлень Prom (окреме від журналу — лише огляд) */}
      {promOrders !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
             onClick={() => setPromOrders(null)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-xl max-w-3xl w-full max-h-[80vh] overflow-auto p-4"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-lg font-semibold flex items-center gap-2">
                <img src="/media-logos/prom-logo.png" alt="Prom" className="h-5 rounded" /> Замовлення Prom
                <span className="text-sm text-gray-400">({promOrders.length})</span>
              </h3>
              <button onClick={() => setPromOrders(null)} className="text-gray-400 hover:text-gray-700 text-xl">✕</button>
            </div>
            <p className="text-xs text-gray-500 mb-3">Окреме дзеркало Prom — у Google-журнал не вливається. Товари злінковані до BMS за номером (sku).</p>
            {promOrders.length === 0 ? (
              <div className="text-center text-gray-400 py-8">Замовлень Prom немає. Натисни «Синхронізувати Prom».</div>
            ) : (
              <div className="space-y-2">
                {promOrders.map((o: any) => (
                  <div key={o.prom_id} className="border border-gray-200 dark:border-gray-700 rounded p-2 text-sm">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                      <span className="font-medium">#{o.prom_id} · {o.status} · <span className="text-gray-500">{o.source}</span></span>
                      <span className="text-gray-500">{o.date_created ? String(o.date_created).slice(0, 16).replace('T', ' ') : ''}</span>
                      <span className="font-semibold">{o.price_text || (o.price_num != null ? `${o.price_num} грн` : '')}</span>
                    </div>
                    <div className="text-gray-500 text-xs mt-0.5">{o.client_name || '—'} · {o.phone || ''} · злінковано {o.linked_count}/{(o.products || []).length}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {(o.products || []).map((pr: any, i: number) => (
                        <span key={i} className={`px-1.5 py-0.5 rounded text-xs border ${pr.product_id ? 'bg-indigo-50 border-indigo-200 text-indigo-700 dark:bg-indigo-900/20 dark:text-indigo-300' : 'bg-gray-50 border-gray-200 text-gray-500'}`}
                            title={pr.name || ''}>
                          {pr.sku || '?'}{pr.quantity ? ` ×${pr.quantity}` : ''}{pr.product_id ? ' ✓' : ''}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </MainLayout>
  );
};

export default PublicationsPage;
