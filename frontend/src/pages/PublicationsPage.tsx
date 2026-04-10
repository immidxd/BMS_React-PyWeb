import React, { useState, useEffect, useCallback } from 'react';
import MainLayout from '../layouts/MainLayout';
import Pagination from '../components/common/Pagination';

/* ── Types ─────────────────────────────────────────────────────────── */

interface PublicationItem {
  product_id: number;
  productnumber: string;
  model: string | null;
  price: number | null;
  status: string | null;
  publication_count: number;
  channels: string;
  threads: string;
}

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
  sold_but_live_count: number;
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

type FilterMode = 'all' | 'published' | 'problematic' | 'unpublished';

/* ── Filter Panel ──────────────────────────────────────────────────── */

const PublicationsFilterPanel: React.FC<{
  filterMode: FilterMode;
  onFilterChange: (m: FilterMode) => void;
  stats: PublicationStats | null;
}> = ({ filterMode, onFilterChange, stats }) => (
  <div>
    <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Фільтр</h3>
    <div className="space-y-1.5 mb-4">
      {([
        { key: 'published', label: '📢 Опубліковані' },
        { key: 'problematic', label: '⚠️ Продані, але висять' },
        { key: 'unpublished', label: '○ Не опубліковані' },
        { key: 'all', label: 'Всі товари' },
      ] as { key: FilterMode; label: string }[]).map(opt => (
        <label key={opt.key} className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 cursor-pointer">
          <input
            type="radio"
            checked={filterMode === opt.key}
            onChange={() => onFilterChange(opt.key)}
            className="rounded border-gray-300"
          />
          {opt.label}
        </label>
      ))}
    </div>

    {stats && (
      <>
        <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Статистика</h3>
        <div className="text-xs text-gray-600 dark:text-gray-300 space-y-1 mb-4">
          <div className="flex justify-between">
            <span>Каналів:</span><span className="font-medium">{stats.total_chats}</span>
          </div>
          <div className="flex justify-between">
            <span>Опубліковано товарів:</span><span className="font-medium">{stats.published_products}</span>
          </div>
          <div className="flex justify-between">
            <span>Всього постів:</span><span className="font-medium">{stats.total_posts}</span>
          </div>
          {stats.sold_but_live_count > 0 && (
            <div className="flex justify-between text-red-600 dark:text-red-400 font-semibold">
              <span>⚠️ Сирітських:</span><span>{stats.sold_but_live_count}</span>
            </div>
          )}
        </div>

        {stats.channels.length > 0 && (
          <>
            <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Канали</h3>
            <div className="space-y-1">
              {stats.channels.map((c, i) => (
                <div key={i} className="text-xs text-gray-600 dark:text-gray-300 flex justify-between">
                  <span className="truncate flex-1 mr-1" title={c.chat_title}>{c.chat_title}</span>
                  <span className="font-medium whitespace-nowrap">{c.post_count}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </>
    )}
  </div>
);

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
  const [stats, setStats] = useState<PublicationStats | null>(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const [detailProductId, setDetailProductId] = useState<number | null>(null);

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
  }, [page, perPage, currentSearchTerm, filterMode]);

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
  };

  const handleRelink = async () => {
    if (!window.confirm('Спробувати зв\'язати непов\'язані пости з товарами по їх номерах?')) return;
    try {
      const res = await fetch('/api/publications/relink', { method: 'POST' });
      const data = await res.json();
      alert(`Пов\'язано: ${data.rows_affected} постів`);
      fetchItems();
      fetchStats();
    } catch (e: any) {
      alert(e.message);
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
            <button
              onClick={handleRelink}
              className="px-3 py-1.5 text-xs bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded transition-colors"
              title="Зв'язати непов'язані пости з товарами по номеру"
            >
              🔗 Перепов'язати
            </button>
            <button
              onClick={() => setSyncOpen(true)}
              className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
            >
              📡 Синхронізувати
            </button>
          </div>
        </div>

        {loading && items.length === 0 ? (
          <div className="flex justify-center items-center h-48 text-gray-400">Завантаження...</div>
        ) : error ? (
          <div className="flex justify-center items-center h-48 text-red-500">{error}</div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-gray-400">
            <div className="text-lg mb-2">Публікацій ще немає</div>
            <div className="text-sm">Натисніть "Синхронізувати" щоб завантажити пости з Telegram</div>
          </div>
        ) : (
          <div className="overflow-x-auto rounded border border-gray-200 dark:border-gray-700">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600">
                <tr>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">№ товару</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Модель</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Статус</th>
                  <th className="px-3 py-3 text-center font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Постів</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Канали / Гілки</th>
                  <th className="px-3 py-3 text-center font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Дії</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {items.map(item => {
                  // Exact match: 'Продано' only (NOT 'Непродано' which also contains 'продано')
                  const isProblematic = item.status?.toLowerCase() === 'продано' && item.publication_count > 0;
                  return (
                    <tr
                      key={item.product_id}
                      className={`hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors ${
                        isProblematic ? 'bg-red-50 dark:bg-red-900/20' : ''
                      }`}
                    >
                      <td className="px-3 py-2 font-mono text-xs text-gray-900 dark:text-gray-100 whitespace-nowrap">
                        {item.productnumber}
                      </td>
                      <td className="px-3 py-2 text-gray-700 dark:text-gray-300 truncate max-w-xs">
                        {item.model || '—'}
                      </td>
                      <td className="px-3 py-2">
                        <span className={`text-xs px-2 py-0.5 rounded ${
                          isProblematic
                            ? 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 font-semibold'
                            : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300'
                        }`}>
                          {isProblematic && '⚠️ '}{item.status || '—'}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-center">
                        <span className={`font-medium ${item.publication_count > 0 ? 'text-blue-600 dark:text-blue-400' : 'text-gray-400'}`}>
                          {item.publication_count}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-600 dark:text-gray-400 truncate max-w-md" title={`${item.channels}\n${item.threads}`}>
                        {item.channels || '—'}
                        {item.threads && item.threads !== '—' && (
                          <span className="text-gray-400 ml-1">/ {item.threads}</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-center">
                        {item.publication_count > 0 && (
                          <button
                            onClick={() => setDetailProductId(item.product_id)}
                            className="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 text-xs"
                          >
                            Деталі
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pagination */}
      <div className="fixed bottom-0 left-0 right-0 px-4 py-3 bg-white/95 dark:bg-gray-800/95 backdrop-blur border-t border-gray-200 dark:border-gray-700 z-30">
        <div className="flex items-center justify-between gap-4">
          <Pagination
            currentPage={page}
            totalPages={pages}
            totalItems={total}
            itemsPerPage={perPage}
            onPageChange={setPage}
            onPerPageChange={(n) => { setPerPage(n); setPage(1); }}
          />
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
    </MainLayout>
  );
};

export default PublicationsPage;
