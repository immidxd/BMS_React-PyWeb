import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchSuppliers, type Supplier, type SupplierList } from '../../services/referenceService';
import Pagination from '../common/Pagination';
import SupplierDetailsModal from './SupplierDetailsModal';

const SuppliersTable: React.FC = () => {
  const [items, setItems] = useState<Supplier[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<number>(1);
  const [perPage, setPerPage] = useState<number>(20);
  const [total, setTotal] = useState<number>(0);
  const [search, setSearch] = useState<string>('');
  const [sortBy, setSortBy] = useState<'id' | 'company_name' | 'priority'>('priority');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [detailsId, setDetailsId] = useState<number | null>(null);
  const [detailsOpen, setDetailsOpen] = useState<boolean>(false);
  const navigate = useNavigate();
  const location = useLocation();

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data: SupplierList = await fetchSuppliers(search || undefined, page, perPage, sortBy, sortDir);
      setItems(data.items);
      setTotal(data.total);
    } catch (e) {
      setError('Помилка завантаження постачальників');
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
    const sb = (params.get('sort_by') as typeof sortBy) || 'priority';
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
      <SupplierDetailsModal supplierId={detailsId} open={detailsOpen} onClose={() => setDetailsOpen(false)} onSaved={() => { setDetailsOpen(false); loadData(); }} />
      {/* Локальне поле пошуку видалено — використовуємо глобальний пошук у заголовку */}

      <div className="overflow-x-auto rounded shadow border border-gray-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('id'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>ID</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('company_name'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Компанія</th>
              <th className="px-4 py-3 text-left font-semibold">Контакт</th>
              <th className="px-4 py-3 text-left font-semibold">Місто</th>
              <th className="px-4 py-3 text-left font-semibold">Статус</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => { setSortBy('priority'); setSortDir(sortDir === 'asc' ? 'desc' : 'asc'); }}>Пріоритет</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="text-center py-8 text-gray-400">Завантаження...</td></tr>
            ) : error ? (
              <tr><td colSpan={6} className="text-center py-8 text-red-500">{error}</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={6} className="text-center py-8 text-gray-400">Постачальників не знайдено</td></tr>
            ) : (
              items.map(s => (
                <tr key={s.id} className="border-b last:border-b-0 hover:bg-gray-50">
                  <td className="px-4 py-2 whitespace-nowrap">{s.id}</td>
                  <td className="px-4 py-2 whitespace-nowrap">
                    <button onClick={() => { setDetailsId(s.id); setDetailsOpen(true); }} className="underline text-blue-600 hover:text-blue-800">
                      {s.company_name || '—'}
                    </button>
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap">{s.contact_person || '—'}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{s.city_location || '—'}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{s.status || '—'}</td>
                  <td className="px-4 py-2 whitespace-nowrap">{s.priority}</td>
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

export default SuppliersTable;


