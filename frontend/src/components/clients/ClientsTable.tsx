import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchClients, type Client, type ClientList } from '../../services/referenceService';
import Pagination from '../common/Pagination';
import ClientDetailsModal from './ClientDetailsModal';
import BmsEmpty from '../common/BmsEmpty';

const ClientsTable: React.FC = () => {
  const [clients, setClients] = useState<Client[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<number>(1);
  const [perPage, setPerPage] = useState<number>(20);
  const [total, setTotal] = useState<number>(0);
  const [search, setSearch] = useState<string>('');
  const [sortBy, setSortBy] = useState<'id' | 'last_name' | 'first_name' | 'order_count' | 'total_order_amount' | 'confirmed_orders' | 'cancelled_count' | 'rating'>('last_name');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [detailsId, setDetailsId] = useState<number | null>(null);
  const [detailsOpen, setDetailsOpen] = useState<boolean>(false);
  const navigate = useNavigate();
  const location = useLocation();

  const loadClients = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.append('page', String(page));
      params.append('per_page', String(perPage));
      if (search) params.append('search', search);
      params.append('sort_by', sortBy);
      params.append('sort_dir', sortDir);
      const res = await fetch(`/api/clients?${params.toString()}`);
      const data: ClientList = await res.json();
      setClients(data.items);
      setTotal((data as any).total || 0);
    } catch (e) {
      setError('Помилка завантаження клієнтів');
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadClients(); }, [page, perPage, sortBy, sortDir]);

  // Parse URL on mount
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const pn = Number(params.get('page')) || 1;
    const ps = Number(params.get('per_page')) || 20;
    const sb = (params.get('sort_by') as typeof sortBy) || 'last_name';
    const sd = (params.get('sort_dir') as typeof sortDir) || 'asc';
    const q = params.get('search') || '';
    setPage(pn); setPerPage(ps); setSortBy(sb); setSortDir(sd); setSearch(q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // State -> URL sync
  useEffect(() => {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('per_page', String(perPage));
    params.set('sort_by', sortBy);
    params.set('sort_dir', sortDir);
    if (search) params.set('search', search);
    navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
  }, [page, perPage, sortBy, sortDir, search, navigate, location.pathname]);

  const totalPages = Math.max(1, Math.ceil(total / perPage));

  return (
    <div className="w-full">
      <ClientDetailsModal clientId={detailsId} open={detailsOpen} onClose={() => setDetailsOpen(false)} />
      {/* Локальне поле пошуку видалено — використовуємо глобальний пошук у заголовку */}

      <div className="overflow-x-auto rounded shadow border border-gray-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-3 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('id'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>ID</th>
              <th className="px-3 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('last_name'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>ПІБ</th>
              <th className="px-3 py-3 text-left font-semibold">Телефон</th>
              <th className="px-3 py-3 text-left font-semibold">Місто</th>
              <th className="px-3 py-3 text-center font-semibold cursor-pointer" onClick={() => { setSortBy('confirmed_orders'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }} title="Підтверджені замовлення">Замовл.</th>
              <th className="px-3 py-3 text-center font-semibold cursor-pointer" onClick={() => { setSortBy('cancelled_count'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }} title="Відміни">Відміни</th>
              <th className="px-3 py-3 text-center font-semibold" title="Ігнорування">Ігнор.</th>
              <th className="px-3 py-3 text-center font-semibold" title="Повернення / Обмін">Пов./Обм.</th>
              <th className="px-3 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('total_order_amount'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Сума</th>
              <th className="px-3 py-3 text-center font-semibold cursor-pointer" onClick={() => { setSortBy('rating'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Рейтинг</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={10} className="text-center py-8 text-gray-400">Завантаження...</td>
              </tr>
            ) : error ? (
              <tr>
                <td colSpan={10} className="text-center py-8 text-red-500">{error}</td>
              </tr>
            ) : clients.length === 0 ? (
              <tr>
                <td colSpan={10}><BmsEmpty label="Клієнтів не знайдено" /></td>
              </tr>
            ) : (
              clients.map((c) => {
                const rating = c.rating ?? 5;
                const ratingColor = rating >= 7 ? 'text-green-600 bg-green-50' : rating >= 4 ? 'text-yellow-600 bg-yellow-50' : 'text-red-600 bg-red-50';
                return (
                  <tr key={c.id} className="border-b last:border-b-0 hover:bg-gray-50">
                    <td className="px-3 py-2 whitespace-nowrap">{c.id}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="flex items-center gap-1.5">
                        <button onClick={() => { setDetailsId(c.id); setDetailsOpen(true); }} className="underline text-blue-600 hover:text-blue-800">
                          {c.full_name}
                        </button>
                        {c.has_deferred && (
                          <span title="Має відкладені замовлення" className="inline-flex items-center justify-center w-4 h-4 text-orange-500">
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">{c.phone_number || '—'}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{c.city_of_residence || '—'}</td>
                    <td className="px-3 py-2 text-center">
                      <span className="font-medium text-green-700">{c.confirmed_orders || 0}</span>
                    </td>
                    <td className="px-3 py-2 text-center">
                      {c.cancelled_count > 0 ? <span className="font-medium text-red-600">{c.cancelled_count}</span> : <span className="text-gray-300">0</span>}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {c.ignored_count > 0 ? <span className="font-medium text-yellow-600">{c.ignored_count}</span> : <span className="text-gray-300">0</span>}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {c.return_exchange_count > 0 ? <span className="font-medium text-orange-600">{c.return_exchange_count}</span> : <span className="text-gray-300">0</span>}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">{new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH' }).format(c.total_order_amount || 0)}</td>
                    <td className="px-3 py-2 text-center">
                      <span className={`inline-flex items-center justify-center min-w-[32px] px-1.5 py-0.5 rounded text-xs font-bold ${ratingColor}`}>
                        {rating.toFixed(1)}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="flex justify-center items-center mt-6 mb-2">
        <Pagination
          currentPage={page}
          totalPages={totalPages}
          totalItems={total}
          itemsPerPage={perPage}
          onPageChange={setPage}
          onPerPageChange={setPerPage}
        />
      </div>
    </div>
  );
};

export default ClientsTable;


