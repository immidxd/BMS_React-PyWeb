import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import Pagination from '../common/Pagination';
import axios from 'axios';
import DeliveryDetailsModal from './DeliveryDetailsModal';

type Delivery = {
  id: number;
  deliveryname: string | null;
  description: string | null;
  created_at: string | null;
  deliverydate: string | null;
  supplier_id: number | null;
  supplier_name?: string | null;
};

type DeliveryList = {
  items: Delivery[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
};

const DeliveriesTable: React.FC = () => {
  const [items, setItems] = useState<Delivery[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<number>(1);
  const [perPage, setPerPage] = useState<number>(20);
  const [total, setTotal] = useState<number>(0);
  const [search, setSearch] = useState<string>('');
  const [sortBy, setSortBy] = useState<'id' | 'created_at' | 'deliverydate' | 'deliveryname'>('created_at');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [detailsId, setDetailsId] = useState<number | null>(null);
  const [detailsOpen, setDetailsOpen] = useState<boolean>(false);
  const navigate = useNavigate();
  const location = useLocation();

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.append('page', String(page));
      params.append('per_page', String(perPage));
      if (search) params.append('search', search);
      params.append('sort_by', sortBy);
      params.append('sort_dir', sortDir);
      const { data } = await axios.get<DeliveryList>(`/api/deliveries?${params.toString()}`);
      setItems(data.items);
      setTotal(data.total);
    } catch (e) {
      setError('Помилка завантаження поставок');
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); }, [page, perPage, sortBy, sortDir]);

  // Parse URL on mount
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const pn = Number(params.get('page')) || 1;
    const ps = Number(params.get('per_page')) || 20;
    const sb = (params.get('sort_by') as typeof sortBy) || 'created_at';
    const sd = (params.get('sort_dir') as typeof sortDir) || 'desc';
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
      <DeliveryDetailsModal deliveryId={detailsId} open={detailsOpen} onClose={() => setDetailsOpen(false)} onSaved={() => { setDetailsOpen(false); loadData(); }} />
      {/* Локальне поле пошуку видалено — використовуємо глобальний пошук у заголовку */}

      <div className="overflow-x-auto rounded shadow border border-gray-200 bg-white">
        <table className="min-w-full text-sm [&_th]:text-center [&_td]:text-center">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-4 py-3 text-left font-semibold">ID</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('deliveryname'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Назва</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('created_at'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Дата створення</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('deliverydate'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Дата відправлення</th>
              <th className="px-4 py-3 text-left font-semibold">Постачальник</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={5} className="text-center py-8 text-gray-400">Завантаження...</td></tr>
            ) : error ? (
              <tr><td colSpan={5} className="text-center py-8 text-red-500">{error}</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={5} className="text-center py-8 text-gray-400">Поставок не знайдено</td></tr>
            ) : (
              items.map(d => (
                <tr key={d.id} className="border-b last:border-b-0 hover:bg-gray-50">
                  <td className="px-4 py-2 whitespace-nowrap">{d.id}</td>
                  <td className="px-4 py-2 whitespace-nowrap">
                    <button onClick={() => { setDetailsId(d.id); setDetailsOpen(true); }} className="underline text-blue-600 hover:text-blue-800">
                      {d.deliveryname || '—'}
                    </button>
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap">{d.created_at ? new Date(d.created_at).toLocaleDateString('uk-UA') : '—'}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{d.deliverydate ? new Date(d.deliverydate).toLocaleDateString('uk-UA') : '—'}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{d.supplier_name || d.supplier_id || '—'}</td>
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

export default DeliveriesTable;


