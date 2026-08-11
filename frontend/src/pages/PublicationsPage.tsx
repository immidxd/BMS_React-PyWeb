import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  SendOutlined, WarningOutlined, MinusCircleOutlined, DisconnectOutlined,
  AppstoreOutlined, EditOutlined, DeleteOutlined, EyeOutlined,
} from '@ant-design/icons';
import MainLayout from '../layouts/MainLayout';
import Pagination from '../components/common/Pagination';
import ProductDetailsModal from '../components/products/ProductDetailsModal';
import TelegramPublishDialog, { type TelegramPreview, type TelegramPublishPayload } from '../components/products/TelegramPublishDialog';
import { confirmDialog, alertDialog, notify } from '../ui/feedback';
import LoadingSpinner from '../components/common/LoadingSpinner';

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
  id: number;
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
  sold_but_live_count: number;
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

type FilterMode = 'all' | 'published' | 'problematic' | 'unpublished' | 'unlinked';

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
  stats: PublicationStats | null;
}> = ({ filterMode, onFilterChange, stats }) => {
  const modes: ModeOption[] = [
    { key: 'published',   label: 'Опубліковані',        icon: <SendOutlined />,       tone: 'text-sky-500',     count: stats?.published_products },
    { key: 'problematic', label: 'Продані, але висять', icon: <WarningOutlined />,    tone: 'text-rose-500',    count: stats?.sold_but_live_count },
    { key: 'unpublished', label: 'Не опубліковані',     icon: <MinusCircleOutlined />, tone: 'text-gray-400' },
    { key: 'unlinked',    label: 'Незвʼязані пости',    icon: <DisconnectOutlined />, tone: 'text-amber-500',   count: stats?.unlinked_count },
    { key: 'all',         label: 'Всі товари',          icon: <AppstoreOutlined />,   tone: 'text-gray-400' },
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

      {stats && (
        <>
          <FilterSection title="Підсумки" defaultOpen>
            <div className="grid grid-cols-2 gap-1.5">
              {[
                { label: 'Усього постів', value: stats.total_posts, title: 'Усі активні пости, включно з копіями одного товару' },
                { label: 'Унік. товарів', value: stats.published_products, title: 'Різні привʼязані товари серед активних публікацій' },
                { label: 'У форумі', value: stats.forum_products, title: `${stats.forum_products} унікальних товарів · ${stats.forum_posts} постів із копіями по гілках` },
                { label: 'У каналі', value: stats.channel_products, title: `${stats.channel_products} унікальних товарів · ${stats.channel_posts} постів` },
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
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${c.chat_type === 'forum' ? 'bg-sky-400' : 'bg-emerald-400'}`} />
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
}> = ({ productId, onClose }) => {
  const [details, setDetails] = useState<PublicationDetail[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (productId === null) return;
    setLoading(true);
    fetch(`/api/publications/product/${productId}`)
      .then(r => r.json())
      .then(d => setDetails(d.publications || []))
      .finally(() => setLoading(false));
  }, [productId]);

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
                    d.tg_status === 'archived' ? 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300' :
                    'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300'
                  }`}>
                    {d.tg_status}
                  </span>
                </div>
                {d.message_text && (
                  <div className="text-xs text-gray-600 dark:text-gray-400 mt-1 line-clamp-3 whitespace-pre-wrap">
                    {d.message_text}
                  </div>
                )}
                {d.message_date && (
                  <div className="text-xs text-gray-400 mt-1">
                    {new Date(d.message_date).toLocaleString('uk-UA')}
                  </div>
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

const PublicationsPage: React.FC<PublicationsPageProps> = ({ currentSearchTerm }) => {
  const [items, setItems] = useState<PublicationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [filterMode, setFilterMode] = useState<FilterMode>('published');
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
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [expandedDetail, setExpandedDetail] = useState<any>(null);
  const [expandedLoading, setExpandedLoading] = useState(false);
  // Створення поста: прев'ю → діалог редагування → публікація живою.
  const [tgPreview, setTgPreview] = useState<TelegramPreview | null>(null);
  const [tgPreviewing, setTgPreviewing] = useState<number | null>(null);
  const [tgBusy, setTgBusy] = useState(false);

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
  useEffect(() => { fetchPromStatus(); fetchOlxStatus(); fetchMonobazarStatus(); }, [fetchPromStatus, fetchOlxStatus, fetchMonobazarStatus]);

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
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(perPage),
      });
      if (currentSearchTerm) params.set('search', currentSearchTerm);
      if (filterMode !== 'all') params.set('filter_mode', filterMode);
      // Режим «Продані, але висять» — окремий cleanup-сценарій; у ньому
      // «Тільки непродані» тимчасово не діє і відновлюється після виходу.
      params.set('only_unsold', String(onlyUnsold && filterMode !== 'problematic'));
      if (onlyRostovka) params.set('only_rostovka', 'true');

      const res = await fetch(`/api/publications/overview?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 1);
    } catch (e: any) {
      setError(e.message || 'Помилка завантаження');
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  }, [page, perPage, currentSearchTerm, filterMode, onlyUnsold, onlyRostovka]);

  useEffect(() => { fetchItems(); }, [fetchItems]);
  useEffect(() => { fetchStats(); }, [fetchStats]);

  const handleRefresh = () => {
    setIsRefreshing(true);
    fetchItems();
    fetchStats();
  };

  const handleResetFilters = () => {
    setPage(1);
    setFilterMode('published');
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
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    if (!(await confirmDialog(`Зняти з публікації ${ids.length} товарів? Кожен буде переслано у WORKSHOP і видалено з усіх каналів.`))) return;
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
      filterPanelContent={
        <PublicationsFilterPanel
          filterMode={filterMode}
          onFilterChange={(m) => { setFilterMode(m); setPage(1); }}
          stats={stats}
        />
      }
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
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
            {/* Усі інтеграції (Telegram/OLX/Prom) — за однією кнопкою, щоб тулбар був чистим */}
            <button
              onClick={() => setIntegrationsOpen(true)}
              className="px-3 py-1.5 text-sm bg-gray-800 hover:bg-gray-900 dark:bg-gray-200 dark:hover:bg-white text-white dark:text-gray-900 rounded transition-colors flex items-center gap-1.5"
              title="Синхронізація та керування каналами: Telegram, OLX, Prom"
            >
              ⚙ Інтеграції
              {promStatus?.token_expiring_soon && <span className="w-2 h-2 rounded-full bg-amber-400" title="Токен Prom спливає" />}
            </button>
          </div>
        </div>

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
              {filterMode === 'unpublished' ? 'Усі товари вже опубліковані' : 'Публікацій ще немає'}
            </div>
            <div className="text-xs">
              {filterMode === 'unpublished'
                ? 'Змініть фільтр зліва, щоб побачити інші товари.'
                : 'Відкрийте «Інтеграції» → «Синхронізувати все», щоб підтягнути пости з Telegram.'}
            </div>
          </div>
        ) : (
          <>
          {/* Bulk action bar */}
          {selectedIds.size > 0 && (
            <div className="mb-3 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg flex items-center justify-between">
              <span className="text-sm font-medium text-red-700 dark:text-red-300">
                Обрано: {selectedIds.size} товарів
              </span>
              <button
                onClick={handleBulkUnpublish}
                disabled={bulkUnpublishing}
                className="px-4 py-2 text-sm bg-red-600 hover:bg-red-700 text-white rounded transition-colors disabled:opacity-50"
              >
                {bulkUnpublishing ? 'Знімаю...' : `🗑 Зняти ${selectedIds.size} з публікації`}
              </button>
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
                  const isProblematic = item.status?.toLowerCase() === 'продано' && item.publication_count > 0;
                  const isUnlinked = item.is_unlinked === true;
                  const needsManualEdit = item.needs_manual_edit === true;
                  const isExpanded = expandedId === item.product_id && item.product_id !== null;
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
                        <span className={`font-medium ${item.publication_count > 0 ? 'text-blue-600 dark:text-blue-400' : 'text-gray-400'}`}>
                          {item.publication_count}
                        </span>
                      </td>
                      )}
                      {isPubColVisible('channels') && (
                      <td className="px-3 py-2 text-xs text-gray-600 dark:text-gray-400 truncate max-w-md" title={`${item.channels}\n${item.threads}`}>
                        {item.channels || '—'}
                        {item.threads && item.threads !== '—' && (
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
                            <button
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
                            </button>
                            {item.publication_count > 0 && (
                              <>
                                <button
                                  onClick={() => setDetailProductId(item.product_id as number)}
                                  title="Показати тексти постів"
                                  className="p-1 rounded-md text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
                                >
                                  <EyeOutlined style={{ fontSize: 13 }} />
                                </button>
                                <button
                                  onClick={() => handleUnpublish(item.product_id as number)}
                                  disabled={unpublishing === item.product_id}
                                  className="p-1 rounded-md text-gray-400 hover:text-red-600 dark:hover:text-red-400 transition-colors disabled:opacity-50"
                                  title="Переслати в WORKSHOP і видалити з усіх каналів"
                                >
                                  {unpublishing === item.product_id
                                    ? <span className="text-xs">…</span>
                                    : <DeleteOutlined style={{ fontSize: 13 }} />}
                                </button>
                              </>
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

      {/* Pagination */}
      <div className="fixed bottom-0 left-0 right-0 px-4 py-3 bg-white/95 dark:bg-gray-800/95 backdrop-blur border-t border-gray-200 dark:border-gray-700 z-30">
        <div className="w-full grid grid-cols-[1fr_auto_1fr] items-center gap-4 max-w-screen-2xl mx-auto px-2">
          <div className="justify-self-start flex items-center gap-6">
            <label className="inline-flex items-center text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={onlyUnsold && filterMode !== 'problematic'}
                disabled={filterMode === 'problematic'}
                onChange={(e) => { setOnlyUnsold(e.target.checked); setPage(1); }}
                title={filterMode === 'problematic' ? 'У цьому режимі треба бачити продані товари, які ще висять у Telegram' : undefined}
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

      <SyncModal
        open={syncOpen}
        onClose={() => setSyncOpen(false)}
        onSyncComplete={() => { fetchItems(); fetchStats(); }}
      />
      <DetailModal
        productId={detailProductId}
        onClose={() => setDetailProductId(null)}
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

      {/* Панель «Інтеграції» — керування каналами (Telegram / OLX / Prom) в одному місці */}
      {integrationsOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
             onClick={() => setIntegrationsOpen(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-xl max-w-4xl w-full max-h-[85vh] overflow-auto p-5"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-lg font-semibold">⚙ Інтеграції</h3>
              <button onClick={() => setIntegrationsOpen(false)} className="text-gray-400 hover:text-gray-700 text-xl">✕</button>
            </div>
            <p className="text-xs text-gray-500 mb-4">Синхронізація й так відбувається автоматично: Telegram/OLX — кожні 30 хв, Prom — кожні ~10 хв, а наявність на Prom оновлюється сама після кожного оновлення BMS. Ці кнопки — щоб оновити «зараз».</p>
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
