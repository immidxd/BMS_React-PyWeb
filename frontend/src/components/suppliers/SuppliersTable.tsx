import React, { useEffect, useState, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchSuppliers, mergeSuppliers, updateSupplier, deleteSupplier, type Supplier, type SupplierList } from '../../services/referenceService';
import Pagination from '../common/Pagination';

const SuppliersTable: React.FC = () => {
  const [items, setItems] = useState<Supplier[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<number>(1);
  const [perPage] = useState<number>(100);
  const [total, setTotal] = useState<number>(0);
  const [sortBy, setSortBy] = useState<'id' | 'name' | 'product_count'>('name');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [mergeTarget, setMergeTarget] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const navigate = useNavigate();
  const location = useLocation();

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data: SupplierList = await fetchSuppliers(undefined, page, perPage, sortBy, sortDir);
      setItems(data.items);
      setTotal(data.total);
    } catch (e) {
      setError('Помилка завантаження постачальників');
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [page, perPage, sortBy, sortDir]);

  useEffect(() => { loadData(); }, [loadData]);

  // State -> URL sync
  useEffect(() => {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('sort_by', sortBy);
    params.set('sort_dir', sortDir);
    navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
  }, [page, sortBy, sortDir, navigate, location.pathname]);

  const totalPages = Math.max(1, Math.ceil(total / perPage));

  const toggleSort = (col: typeof sortBy) => {
    if (sortBy === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortBy(col); setSortDir('asc'); }
  };

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const handleMerge = async () => {
    if (!mergeTarget || selected.size < 2) return;
    const sourceIds = Array.from(selected).filter(id => id !== mergeTarget);
    if (sourceIds.length === 0) return;
    try {
      await mergeSuppliers(mergeTarget, sourceIds);
      setSelected(new Set());
      setMergeTarget(null);
      await loadData();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Помилка злиття');
    }
  };

  const handleSaveEdit = async (id: number) => {
    if (!editName.trim()) return;
    try {
      await updateSupplier(id, { name: editName.trim() });
      setEditingId(null);
      await loadData();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Помилка збереження');
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm('Видалити постачальника?')) return;
    try {
      await deleteSupplier(id);
      await loadData();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Помилка видалення');
    }
  };

  const sortIcon = (col: typeof sortBy) => {
    if (sortBy !== col) return '';
    return sortDir === 'asc' ? ' ↑' : ' ↓';
  };

  return (
    <div className="w-full">
      {/* Merge bar */}
      {selected.size >= 2 && (
        <div className="mb-3 p-3 bg-blue-50 border border-blue-200 rounded flex items-center gap-3 flex-wrap">
          <span className="text-sm font-medium text-blue-800">Обрано: {selected.size}</span>
          <span className="text-sm text-gray-600">Злити в:</span>
          <select
            className="text-sm border rounded px-2 py-1"
            value={mergeTarget ?? ''}
            onChange={e => setMergeTarget(Number(e.target.value) || null)}
          >
            <option value="">-- Оберіть цільового --</option>
            {items.filter(s => selected.has(s.id)).map(s => (
              <option key={s.id} value={s.id}>{s.name} (ID {s.id})</option>
            ))}
          </select>
          <button
            onClick={handleMerge}
            disabled={!mergeTarget}
            className="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-40"
          >
            Злити
          </button>
          <button
            onClick={() => { setSelected(new Set()); setMergeTarget(null); }}
            className="px-3 py-1 text-sm text-gray-600 hover:text-red-600"
          >
            Скасувати
          </button>
        </div>
      )}

      <div className="overflow-x-auto rounded shadow border border-gray-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-3 py-3 w-10">
                <input
                  type="checkbox"
                  checked={items.length > 0 && items.every(s => selected.has(s.id))}
                  onChange={e => {
                    if (e.target.checked) setSelected(new Set(items.map(s => s.id)));
                    else setSelected(new Set());
                  }}
                  className="w-3.5 h-3.5"
                />
              </th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer w-16" onClick={() => toggleSort('id')}>ID{sortIcon('id')}</th>
              <th className="px-4 py-3 text-left font-semibold cursor-pointer" onClick={() => toggleSort('name')}>Назва{sortIcon('name')}</th>
              <th className="px-4 py-3 text-left font-semibold">Нотатки</th>
              <th className="px-4 py-3 text-center font-semibold cursor-pointer w-28" onClick={() => toggleSort('product_count')}>Товарів{sortIcon('product_count')}</th>
              <th className="px-4 py-3 text-center font-semibold w-24">Дії</th>
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
                <tr key={s.id} className={`border-b last:border-b-0 hover:bg-gray-50 ${selected.has(s.id) ? 'bg-blue-50' : ''}`}>
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={selected.has(s.id)}
                      onChange={() => toggleSelect(s.id)}
                      className="w-3.5 h-3.5"
                    />
                  </td>
                  <td className="px-4 py-2 text-gray-500">{s.id}</td>
                  <td className="px-4 py-2">
                    {editingId === s.id ? (
                      <div className="flex gap-1">
                        <input
                          type="text"
                          value={editName}
                          onChange={e => setEditName(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') handleSaveEdit(s.id); if (e.key === 'Escape') setEditingId(null); }}
                          className="border rounded px-2 py-0.5 text-sm flex-1"
                          autoFocus
                        />
                        <button onClick={() => handleSaveEdit(s.id)} className="text-green-600 hover:text-green-800 text-xs font-bold">OK</button>
                        <button onClick={() => setEditingId(null)} className="text-gray-400 hover:text-gray-600 text-xs">X</button>
                      </div>
                    ) : (
                      <span className="font-medium">{s.name}</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-gray-500 max-w-[200px] truncate">{s.notes || '—'}</td>
                  <td className="px-4 py-2 text-center">
                    <span className={`inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 rounded-full text-xs font-bold ${
                      s.product_count > 0 ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                    }`}>
                      {s.product_count}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-center">
                    <div className="flex gap-1 justify-center">
                      <button
                        onClick={() => { setEditingId(s.id); setEditName(s.name); }}
                        className="text-blue-600 hover:text-blue-800 text-xs"
                        title="Редагувати"
                      >✏️</button>
                      {s.product_count === 0 && (
                        <button
                          onClick={() => handleDelete(s.id)}
                          className="text-red-500 hover:text-red-700 text-xs"
                          title="Видалити"
                        >🗑️</button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex justify-between items-center mt-4">
        <span className="text-sm text-gray-500">Всього: {total} постачальників</span>
        {totalPages > 1 && (
          <Pagination
            currentPage={page}
            totalPages={totalPages}
            totalItems={total}
            itemsPerPage={perPage}
            onPageChange={setPage}
          />
        )}
      </div>
    </div>
  );
};

export default SuppliersTable;
