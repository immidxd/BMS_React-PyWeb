import React, { useState, useEffect, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import MainLayout from '../layouts/MainLayout';
import Pagination from '../components/common/Pagination';
import ClientDetailsModal from '../components/clients/ClientDetailsModal';

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

  const fetchClients = useCallback(async () => {
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
      const res = await fetch(`/api/clients?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setClients(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 1);
    } catch (e: any) {
      setError(e.message || 'Помилка завантаження');
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  }, [page, perPage, sortBy, sortDir, currentSearchTerm]);

  useEffect(() => { fetchClients(); }, [fetchClients]);

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

  const socials = (c: Client) => {
    const links = [
      c.telegram && { label: 'TG', val: c.telegram },
      c.instagram && { label: 'IG', val: c.instagram },
      c.facebook && { label: 'FB', val: c.facebook },
      c.viber && { label: 'Viber', val: c.viber },
      c.olx && { label: 'OLX', val: c.olx },
    ].filter(Boolean) as { label: string; val: string }[];
    if (!links.length) return <span className="text-gray-400 text-xs">—</span>;
    return (
      <div className="flex flex-wrap gap-1">
        {links.map(l => (
          <span key={l.label} className="inline-block px-1.5 py-0.5 bg-gray-100 dark:bg-gray-600 rounded text-xs text-gray-600 dark:text-gray-300" title={l.val}>
            {l.label}
          </span>
        ))}
      </div>
    );
  };

  const handleRefresh = () => { setIsRefreshing(true); fetchClients(); };
  const handleResetFilters = () => { setPage(1); setSortBy('last_name'); setSortDir('asc'); };

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
          {currentSearchTerm && (
            <span className="text-sm text-gray-500 dark:text-gray-400">Пошук: «{currentSearchTerm}»</span>
          )}
        </div>

        {/* Table */}
        {loading && clients.length === 0 ? (
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
                    <td colSpan={8} className="text-center py-12 text-gray-400">Клієнтів не знайдено</td>
                  </tr>
                ) : clients.map(c => (
                  <tr key={c.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors cursor-pointer" onDoubleClick={() => { setSelectedClientId(c.id); setModalOpen(true); }}>
                    <td className="px-3 py-2 text-gray-500 dark:text-gray-400 font-mono text-xs">{c.id}</td>
                    <td className="px-3 py-2 font-medium text-gray-900 dark:text-gray-100 max-w-[200px]">
                      <span className="block truncate">{c.full_name || '—'}</span>
                    </td>
                    <td className="px-3 py-2 text-gray-600 dark:text-gray-300 whitespace-nowrap font-mono text-xs">
                      {c.phone_number || '—'}
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
          <div className="max-w-screen-2xl mx-auto flex items-center justify-between gap-4">
            <span className="text-sm text-gray-500 dark:text-gray-400">
              Всього: <strong>{total}</strong> клієнтів
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

export default ClientsPage;