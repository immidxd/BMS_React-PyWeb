import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchClients, type Client, type ClientList } from '../../services/referenceService';
import Pagination from '../common/Pagination';
import ClientDetailsModal from './ClientDetailsModal';

const ClientsTable: React.FC = () => {
  const [clients, setClients] = useState<Client[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<number>(1);
  const [perPage, setPerPage] = useState<number>(20);
  const [total, setTotal] = useState<number>(0);
  const [search, setSearch] = useState<string>('');
  const [sortBy, setSortBy] = useState<'id' | 'last_name' | 'first_name' | 'order_count' | 'total_order_amount'>('last_name');
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
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('id'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>ID</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('last_name'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>ПІБ</th>
              <th className="px-4 py-3 text-left font-semibold">Телефон</th>
              <th className="px-4 py-3 text-left font-semibold">Email</th>
              <th className="px-4 py-3 text-left font-semibold">Місто</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('order_count'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Замовлень</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('total_order_amount'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Сума</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="text-center py-8 text-gray-400">Завантаження...</td>
              </tr>
            ) : error ? (
              <tr>
                <td colSpan={7} className="text-center py-8 text-red-500">{error}</td>
              </tr>
            ) : clients.length === 0 ? (
              <tr>
                <td colSpan={7} className="text-center py-8 text-gray-400">Клієнтів не знайдено</td>
              </tr>
            ) : (
              clients.map((c) => (
                <tr key={c.id} className="border-b last:border-b-0 hover:bg-gray-50">
                  <td className="px-4 py-2 whitespace-nowrap">{c.id}</td>
                  <td className="px-4 py-2 whitespace-nowrap">
                    <button onClick={() => { setDetailsId(c.id); setDetailsOpen(true); }} className="underline text-blue-600 hover:text-blue-800">
                      {c.full_name}
                    </button>
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap">{c.phone_number || '—'}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{c.email || '—'}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{(c as any).city_of_residence || '—'}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{(c as any).order_count ?? 0}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH' }).format((c as any).total_order_amount || 0)}</td>
                </tr>
              ))
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


