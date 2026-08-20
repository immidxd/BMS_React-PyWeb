import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import MainLayout from '../layouts/MainLayout';
import Pagination from '../components/common/Pagination';
import ClientDetailsModal from '../components/clients/ClientDetailsModal';
import { CopyOnClick } from '../components/common/displayHelpers';
import DuplicatesCarousel from '../components/clients/DuplicatesCarousel';
import { confirmDialog } from '../ui/feedback';
import LoadingSpinner from '../components/common/LoadingSpinner';

interface Client {
  id: number;
  full_name: string;
  first_name: string;
  last_name: string;
  phone_number: string | null;
  email: string | null;
  telegram: string | null;
  instagram: string | null;
  facebook: string | null;
  viber: string | null;
  olx: string | null;
  city_of_residence: string | null;
  order_count: number | null;
  total_order_amount: number | null;
  average_order_value: number | null;
}

interface ClientsPageProps {
  currentSearchTerm: string;
}

type SortField = 'id' | 'last_name' | 'first_name' | 'order_count' | 'total_order_amount';

function fmtMoney(n: number | null) {
  if (!n) return '—';
  return new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH', maximumFractionDigits: 0 }).format(n);
}

const ClientsFilterPanelContent: React.FC = () => (
  <div>
    <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Місто</h3>
    <div className="p-2 border border-dashed rounded mb-4 h-20 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">City Filter</div>
    <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Кількість замовлень</h3>
    <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Order Count Range</div>
    <h3 className="text-md font-semibold mb-3 text-gray-700 dark:text-gray-200">Сума замовлень</h3>
    <div className="p-2 border border-dashed rounded mb-4 h-16 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">Total Amount Range</div>
  </div>
);

const ClientsPage: React.FC<ClientsPageProps> = ({ currentSearchTerm }) => {
  const navigate = useNavigate();
  const location = useLocation();

  const [clients, setClients] = useState<Client[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(20);
  const [sortBy, setSortBy] = useState<SortField>('last_name');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [selectedClientId, setSelectedClientId] = useState<number | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  // Selection for merge
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [mergeTargetId, setMergeTargetId] = useState<number | null>(null);
  const [merging, setMerging] = useState(false);

  // Duplicates carousel
  const [carouselOpen, setCarouselOpen] = useState(false);
  const [dupCount, setDupCount] = useState<number | null>(null);
  const listAbortRef = useRef<AbortController | null>(null);
  const listRequestRef = useRef(0);

  const fetchDupCount = useCallback(async () => {
    try {
      const res = await fetch('/api/client-duplicates/groups?limit=500');
      if (!res.ok) return;
      const d = await res.json();
      setDupCount(d.total_clusters ?? 0);
    } catch { /* silent */ }
  }, []);
  useEffect(() => { fetchDupCount(); }, [fetchDupCount]);

  const fetchClients = useCallback(async () => {
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
        sort_by: sortBy,
        sort_dir: sortDir,
      });
      if (currentSearchTerm) params.set('search', currentSearchTerm);
      const res = await fetch(`/api/clients?${params}`, { signal: controller.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (requestId !== listRequestRef.current) return;
      setClients(data.items || []);
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
  }, [page, perPage, sortBy, sortDir, currentSearchTerm]);

  useEffect(() => { fetchClients(); }, [fetchClients]);
  useEffect(() => () => listAbortRef.current?.abort(), []);

  // Reset to first page whenever the search term changes — інакше попередня
  // позиція пагінації (напр. 21-ша сторінка) виходить за межі нового результату
  // і таблиця показує "Клієнтів не знайдено" попри ненульовий total.
  useEffect(() => { setPage(1); }, [currentSearchTerm]);

  useEffect(() => {
    const p = new URLSearchParams();
    p.set('page', String(page));
    p.set('per_page', String(perPage));
    p.set('sort_by', sortBy);
    p.set('sort_dir', sortDir);
    navigate({ pathname: location.pathname, search: p.toString() }, { replace: true });
  }, [page, perPage, sortBy, sortDir]);

  const handleSort = (field: SortField) => {
    if (sortBy === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortBy(field); setSortDir('asc'); }
    setPage(1);
  };

  const SortIcon = ({ field }: { field: SortField }) => (
    <span className="ml-1 text-gray-400 text-xs">
      {sortBy === field ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
    </span>
  );

  // Канонічні стилі бейджів соцмереж/майданчиків — НАСИЧЕНІ кольори з білим
  // текстом (бліді -100 відтінки читались як сірі, особливо blue-100).
  const SOCIAL_STYLES: Record<string, string> = {
    // ⚠️ НЕ використовувати bg-blue-500/600/700 — App.css перефарбовує їх у
    // брендовий чорний (--bms-primary) через !important. Тому точний hex Facebook.
    'FB':    'bg-[#4267B2] !text-white',   // Facebook — класичний (менш насичений) синій
    'VB':    'bg-violet-600 text-white',   // Viber — фіолетовий
    'TG':    'bg-sky-500 text-white',      // Telegram — блакитний
    'TT':    'bg-black text-white',        // TikTok — чорний
    'IG':    'bg-pink-600 text-white',     // Instagram — рожевий
    'OLX':   'bg-teal-600 text-white',     // OLX — бірюзовий
    'GD':    'bg-gray-500 text-white',     // Grailed — сірий
    'Shafa': 'bg-black text-white',        // Shafa — чорний
    'Prom':  'bg-indigo-600 text-white',   // Prom.ua — фірмовий фіолет
  };
  // Побудувати http(s)-посилання на профіль за значенням з БД.
  // Повертає null, якщо лінк сформувати неможливо (тоді мітка некліковна).
  const buildSocialHref = (label: string, raw: string): string | null => {
    const v = (raw || '').trim();
    if (!v) return null;
    if (/^https?:\/\//i.test(v)) return v;              // вже повний URL
    const handle = v.replace(/^@/, '').replace(/^\//, '');
    switch (label) {
      case 'FB':  return v.includes('facebook.com')  ? `https://${v}` : `https://facebook.com/${handle}`;
      case 'IG':  return v.includes('instagram.com') ? `https://${v}` : `https://instagram.com/${handle}`;
      case 'TG':  return v.includes('t.me')          ? `https://${v}` : `https://t.me/${handle}`;
      case 'OLX': return v.includes('.')             ? `https://${v.replace(/^www\./, '')}` : null;
      // Viber-значення зазвичай телефон, не веб-URL → не робимо клікабельним
      default:    return v.includes('.') ? `https://${v}` : null;
    }
  };
    // Валідність значення соцмережі: відсіюємо сміття (ціни «1250;», голі суми),
    // що потрапило в поле через помилку мапінгу при парсингу. Для веб-мереж
    // (OLX/FB/IG/TG) значення має містити літери або бути URL — НЕ голе число.
    // Для VB (Viber) телефон є валідним.
  const isValidSocial = (label: string, raw?: string | null): boolean => {
    const v = (raw || '').trim();
    if (!v) return false;
    if (label === 'VB') return true;                 // Viber — телефон ок
    if (/^[\d\s;,.+()-]+$/.test(v)) return false;    // голе число/ціна/телефон → не вебмережа
    return true;
  };
  const socials = (c: Client) => {
    const links = [
      isValidSocial('TG', c.telegram) && { label: 'TG', val: c.telegram! },
      isValidSocial('IG', c.instagram) && { label: 'IG', val: c.instagram! },
      isValidSocial('FB', c.facebook) && { label: 'FB', val: c.facebook! },
      // Viber у БД може бути як 'Viber' або 'VB' — у таблиці ЗАВЖДИ показуємо 'VB'
      isValidSocial('VB', c.viber) && { label: 'VB', val: c.viber! },
      isValidSocial('OLX', c.olx) && { label: 'OLX', val: c.olx! },
    ].filter(Boolean) as { label: string; val: string }[];
    if (!links.length) return <span className="text-gray-400 text-xs">—</span>;
    return (
      <div className="flex flex-wrap gap-1 justify-center">
        {links.map(l => {
          const cls = `inline-block px-1.5 py-0.5 rounded text-xs font-medium ${SOCIAL_STYLES[l.label] || 'bg-gray-100 text-gray-600 dark:bg-gray-600 dark:text-gray-300'}`;
          const href = buildSocialHref(l.label, l.val);
          if (href) {
            return (
              <a
                key={l.label}
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                title={`Відкрити: ${l.val}`}
                className={`${cls} no-underline hover:no-underline hover:opacity-80 transition-opacity cursor-pointer`}
              >
                {l.label}
              </a>
            );
          }
          return (
            <span key={l.label} className={cls} title={l.val}>
              {l.label}
            </span>
          );
        })}
      </div>
    );
  };

  const handleRefresh = () => { setIsRefreshing(true); fetchClients(); };
  const handleResetFilters = () => { setPage(1); setSortBy('last_name'); setSortDir('asc'); };

  // Selection helpers
  const toggleSelect = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    if (selectedIds.size === clients.length) setSelectedIds(new Set());
    else setSelectedIds(new Set(clients.map(c => c.id)));
  };
  const clearSelection = () => { setSelectedIds(new Set()); setMergeTargetId(null); };

  const selectedClients = clients.filter(c => selectedIds.has(c.id));

  // Auto-pick most "loaded" candidate as default master
  useEffect(() => {
    if (selectedClients.length === 0) { setMergeTargetId(null); return; }
    if (mergeTargetId && selectedClients.find(c => c.id === mergeTargetId)) return;
    const sorted = [...selectedClients].sort((a, b) =>
      (b.order_count ?? 0) - (a.order_count ?? 0) || (b.total_order_amount ?? 0) - (a.total_order_amount ?? 0)
    );
    setMergeTargetId(sorted[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIds]);

  const handleMerge = async () => {
    if (!mergeTargetId || selectedIds.size < 2) return;
    const sourceIds = Array.from(selectedIds).filter(id => id !== mergeTargetId);
    const target = clients.find(c => c.id === mergeTargetId);
    if (!(await confirmDialog(`Об'єднати ${sourceIds.length} клієнтів у "${target?.full_name || mergeTargetId}"? Дія незворотна.`))) return;
    setMerging(true);
    let okCount = 0;
    const errors: string[] = [];
    for (const sid of sourceIds) {
      try {
        const res = await fetch(`/api/clients/${sid}/merge`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_id: mergeTargetId }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          errors.push(`#${sid}: ${err.detail || res.status}`);
        } else {
          okCount++;
        }
      } catch (e: any) {
        errors.push(`#${sid}: ${e.message}`);
      }
    }
    setMerging(false);
    if (errors.length) alert(`Об'єднано: ${okCount}\nПомилки:\n${errors.join('\n')}`);
    clearSelection();
    fetchClients();
  };

  return (
    <MainLayout
      filterPanelContent={<ClientsFilterPanelContent />}
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      <div className="p-4 pb-24 bg-white dark:bg-gray-800 shadow-md rounded-lg w-full">
        {/* Header */}
        <div className="sticky top-0 z-20 bg-white/90 dark:bg-gray-800/90 backdrop-blur px-2 py-2 -mx-2 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center mb-3">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">
            Клієнти
            <span className="ml-2 text-base font-normal text-gray-400">({total})</span>
          </h1>
          <div className="flex items-center gap-3">
            {currentSearchTerm && (
              <span className="text-sm text-gray-500 dark:text-gray-400">Пошук: «{currentSearchTerm}»</span>
            )}
            {dupCount != null && dupCount > 0 && (
              <button
                onClick={() => setCarouselOpen(true)}
                className="px-3 py-1.5 text-sm rounded-lg border border-amber-300 bg-amber-50 hover:bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:border-amber-700 dark:text-amber-200 font-medium transition-colors"
                title="Переглянути групи можливих дублікатів"
              >
                ⚠️ Кандидати на мердж ({dupCount})
              </button>
            )}
          </div>
        </div>

        <DuplicatesCarousel
          open={carouselOpen}
          onClose={() => { setCarouselOpen(false); fetchDupCount(); fetchClients(); }}
          onMerged={() => { fetchDupCount(); fetchClients(); }}
        />

        {/* Merge bar — appears when ≥2 clients selected */}
        {selectedIds.size >= 2 && (
          <div className="flex flex-wrap items-center gap-3 px-4 py-2.5 mb-3 bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-700 rounded-lg">
            <span className="text-sm font-medium text-blue-700 dark:text-blue-300 whitespace-nowrap">
              Обрано: {selectedIds.size}
            </span>
            <span className="text-xs text-gray-600 dark:text-gray-300">Залишити (master):</span>
            <select
              value={mergeTargetId ?? ''}
              onChange={e => setMergeTargetId(Number(e.target.value))}
              className="px-2 py-1 border border-blue-300 dark:border-blue-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-800 dark:text-gray-200 max-w-[360px]"
            >
              {selectedClients.map(c => (
                <option key={c.id} value={c.id}>
                  #{c.id} · {c.full_name || '—'} · {c.order_count ?? 0} зам.
                </option>
              ))}
            </select>
            <button
              onClick={handleMerge}
              disabled={merging || !mergeTargetId}
              className="px-3 py-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-medium rounded"
            >
              {merging ? 'Обʼєдную…' : `🔀 Обʼєднати (${selectedIds.size - 1} → 1)`}
            </button>
            <button
              onClick={clearSelection}
              className="px-3 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-600 dark:hover:bg-gray-500 text-gray-700 dark:text-gray-200 text-sm rounded"
            >
              Скасувати
            </button>
          </div>
        )}

        {/* Table */}
        {loading && clients.length === 0 ? (
          <LoadingSpinner variant="section" size="large" text="Завантаження клієнтів…" />
        ) : error ? (
          <div className="flex justify-center items-center h-48 text-red-500">{error}</div>
        ) : (
          <div className="overflow-x-auto rounded border border-gray-200 dark:border-gray-700">
            <table className="w-full text-sm [&_th]:text-center [&_td]:text-center">
              <thead className="bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600">
                <tr>
                  <th className="px-2 py-3 text-center w-8">
                    <input
                      type="checkbox"
                      checked={clients.length > 0 && selectedIds.size === clients.length}
                      ref={el => { if (el) el.indeterminate = selectedIds.size > 0 && selectedIds.size < clients.length; }}
                      onChange={toggleSelectAll}
                      title="Виділити всіх на сторінці"
                    />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('id')}>
                    Номер<SortIcon field="id" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer" onClick={() => handleSort('last_name')}>
                    ПІБ<SortIcon field="last_name" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Телефон</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Email</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Соцмережі</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300">Місто</th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('order_count')}>
                    Замовлень<SortIcon field="order_count" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('total_order_amount')}>
                    Сума<SortIcon field="total_order_amount" />
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {clients.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="text-center py-12 text-gray-400">Клієнтів не знайдено</td>
                  </tr>
                ) : clients.map(c => (
                  <tr
                    key={c.id}
                    className={`hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors cursor-pointer select-none ${selectedIds.has(c.id) ? 'bg-blue-50/60 dark:bg-blue-900/20' : ''}`}
                    onDoubleClick={() => { setSelectedClientId(c.id); setModalOpen(true); }}
                  >
                    <td className="px-2 py-2 text-center" onClick={e => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selectedIds.has(c.id)}
                        onChange={() => toggleSelect(c.id)}
                      />
                    </td>
                    <td className="px-3 py-2 text-gray-500 dark:text-gray-400 font-mono text-xs">
                      <CopyOnClick value={c.id} />
                    </td>
                    <td className="px-3 py-2 font-medium text-gray-900 dark:text-gray-100 max-w-[200px]">
                      <span className="block truncate">{c.full_name || '—'}</span>
                    </td>
                    <td className="px-3 py-2 text-gray-600 dark:text-gray-300 whitespace-nowrap font-mono text-xs">
                      {c.phone_number ? <CopyOnClick value={c.phone_number} /> : '—'}
                    </td>
                    <td className="px-3 py-2 text-gray-600 dark:text-gray-300 text-xs max-w-[160px] truncate">
                      {c.email || '—'}
                    </td>
                    <td className="px-3 py-2">{socials(c)}</td>
                    <td className="px-3 py-2 text-gray-600 dark:text-gray-300 text-xs whitespace-nowrap">
                      {c.city_of_residence || '—'}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {(c.order_count ?? 0) > 0 ? (
                        <span className="inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 rounded-full bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 text-xs font-semibold">
                          {c.order_count}
                        </span>
                      ) : (
                        <span className="text-gray-400 text-xs">0</span>
                      )}
                    </td>
                    <td className="px-3 py-2 font-semibold whitespace-nowrap text-gray-900 dark:text-gray-100">
                      {fmtMoney(c.total_order_amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Client card modal */}
        <ClientDetailsModal
          clientId={selectedClientId}
          open={modalOpen}
          onClose={() => { setModalOpen(false); setSelectedClientId(null); }}
        />

        {/* Footer pagination */}
        <div className="fixed bottom-0 left-0 right-0 px-4 py-3 bg-white/95 dark:bg-gray-800/95 backdrop-blur border-t border-gray-100 dark:border-gray-700 z-20">
          <div className="w-full grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] items-center gap-4 max-w-screen-2xl mx-auto px-2">
            <span className="order-2 md:order-none justify-self-start text-sm text-gray-500 dark:text-gray-400">
              Всього: <strong>{total}</strong> клієнтів
            </span>
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
    </MainLayout>
  );
};

export default ClientsPage;
