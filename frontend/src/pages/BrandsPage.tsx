import React, { useState, useEffect, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import MainLayout from '../layouts/MainLayout';
import Pagination from '../components/common/Pagination';
import BmsEmpty from '../components/common/BmsEmpty';

/* ── Types ─────────────────────────────────────────────────────────── */

interface Brand {
  id: number;
  brandname: string;
  normalized_name: string | null;
  concern_id: number | null;
  concern_name: string | null;
  total_products: number;
  available_pairs: number;
}

interface BrandConcern {
  id: number;
  name: string;
  country: string | null;
  description: string | null;
  brand_count: number;
}

interface BrandsPageProps {
  currentSearchTerm: string;
}

type SortField = 'id' | 'brandname' | 'total_products' | 'available_pairs' | 'concern_name';

/* ── Filter Panel ──────────────────────────────────────────────────── */

const BrandsFilterPanel: React.FC<{
  concerns: BrandConcern[];
  selectedConcernId: number | null;
  onConcernChange: (id: number | null) => void;
  hasProducts: boolean | null;
  onHasProductsChange: (v: boolean | null) => void;
}> = ({ concerns, selectedConcernId, onConcernChange, hasProducts, onHasProductsChange }) => (
  <div>
    <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Консерн</h3>
    <select
      value={selectedConcernId ?? ''}
      onChange={e => onConcernChange(e.target.value ? Number(e.target.value) : null)}
      className="w-full mb-4 px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-800 dark:text-gray-200"
    >
      <option value="">Всі</option>
      {concerns.map(c => (
        <option key={c.id} value={c.id}>{c.name} ({c.brand_count})</option>
      ))}
    </select>

    <h3 className="text-md font-semibold mb-2 text-gray-700 dark:text-gray-200">Товари</h3>
    <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 mb-1 cursor-pointer">
      <input
        type="checkbox"
        checked={hasProducts === true}
        onChange={() => onHasProductsChange(hasProducts === true ? null : true)}
        className="rounded border-gray-300"
      />
      Тільки з товарами
    </label>
    <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 cursor-pointer">
      <input
        type="checkbox"
        checked={hasProducts === false}
        onChange={() => onHasProductsChange(hasProducts === false ? null : false)}
        className="rounded border-gray-300"
      />
      Тільки без товарів
    </label>
  </div>
);

/* ── MergeBar ──────────────────────────────────────────────────────── */

const MergeBar: React.FC<{
  selected: Brand[];
  onMerge: (targetId: number, newName: string | null) => void;
  onCancel: () => void;
}> = ({ selected, onMerge, onCancel }) => {
  const [targetId, setTargetId] = useState<number>(selected[0]?.id ?? 0);
  const [newName, setNewName] = useState('');

  useEffect(() => {
    if (selected.length > 0 && !selected.find(b => b.id === targetId)) {
      setTargetId(selected[0].id);
    }
  }, [selected, targetId]);

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-700 rounded-lg">
      <span className="text-sm font-medium text-blue-700 dark:text-blue-300 whitespace-nowrap">
        Обрано: {selected.length}
      </span>
      <select
        value={targetId}
        onChange={e => setTargetId(Number(e.target.value))}
        className="px-2 py-1 border border-blue-300 dark:border-blue-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-800 dark:text-gray-200"
      >
        {selected.map(b => (
          <option key={b.id} value={b.id}>{b.brandname} (#{b.id})</option>
        ))}
      </select>
      <input
        type="text"
        placeholder="Нова назва (опційно)"
        value={newName}
        onChange={e => setNewName(e.target.value)}
        className="px-2 py-1 border border-blue-300 dark:border-blue-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-800 dark:text-gray-200 w-48"
      />
      <button
        onClick={() => onMerge(targetId, newName.trim() || null)}
        className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded transition-colors"
      >
        Злити
      </button>
      <button
        onClick={onCancel}
        className="px-3 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-600 dark:hover:bg-gray-500 text-gray-700 dark:text-gray-200 text-sm rounded transition-colors"
      >
        Скасувати
      </button>
    </div>
  );
};

/* ── Main Page ─────────────────────────────────────────────────────── */

const BrandsPage: React.FC<BrandsPageProps> = ({ currentSearchTerm }) => {
  const navigate = useNavigate();
  const location = useLocation();

  const [brands, setBrands] = useState<Brand[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [sortBy, setSortBy] = useState<SortField>('brandname');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Selection for merge
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // Filters
  const [concerns, setConcerns] = useState<BrandConcern[]>([]);
  const [selectedConcernId, setSelectedConcernId] = useState<number | null>(null);
  const [hasProducts, setHasProducts] = useState<boolean | null>(null);

  // Inline edit
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState('');

  // Concern inline edit
  const [editingConcernBrandId, setEditingConcernBrandId] = useState<number | null>(null);
  const [newConcernName, setNewConcernName] = useState('');
  const [showNewConcernInput, setShowNewConcernInput] = useState(false);

  // Fetch concerns
  const fetchConcerns = useCallback(async () => {
    try {
      const res = await fetch('/api/brand-concerns');
      if (res.ok) setConcerns(await res.json());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchConcerns(); }, [fetchConcerns]);

  // Fetch brands
  const fetchBrands = useCallback(async () => {
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
      if (selectedConcernId) params.set('concern_id', String(selectedConcernId));
      if (hasProducts !== null) params.set('has_products', String(hasProducts));

      const res = await fetch(`/api/brands?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setBrands(data.items || []);
      setTotal(data.total || 0);
      setPages(data.pages || 1);
    } catch (e: any) {
      setError(e.message || 'Помилка завантаження');
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  }, [page, perPage, sortBy, sortDir, currentSearchTerm, selectedConcernId, hasProducts]);

  useEffect(() => { fetchBrands(); }, [fetchBrands]);

  // URL sync
  useEffect(() => {
    const p = new URLSearchParams();
    p.set('page', String(page));
    p.set('per_page', String(perPage));
    p.set('sort_by', sortBy);
    p.set('sort_dir', sortDir);
    navigate({ pathname: location.pathname, search: p.toString() }, { replace: true });
  }, [page, perPage, sortBy, sortDir]);

  // Sorting
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

  // Selection
  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selected.size === brands.length) setSelected(new Set());
    else setSelected(new Set(brands.map(b => b.id)));
  };

  // Merge
  const handleMerge = async (targetId: number, newName: string | null) => {
    const sourceIds = Array.from(selected).filter(id => id !== targetId);
    if (sourceIds.length === 0) return;
    if (!window.confirm(`Злити ${sourceIds.length} брендів у "${brands.find(b => b.id === targetId)?.brandname}"?`)) return;

    try {
      const res = await fetch('/api/brands/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_id: targetId, source_ids: sourceIds, new_name: newName }),
      });
      if (!res.ok) {
        const err = await res.json();
        alert(err.detail || 'Помилка злиття');
        return;
      }
      setSelected(new Set());
      fetchBrands();
    } catch (e: any) {
      alert(e.message);
    }
  };

  // Block
  const handleBlock = async (brand: Brand) => {
    if (!window.confirm(`Заблокувати "${brand.brandname}"? Товари цього бренду втратять прив'язку.`)) return;
    try {
      const res = await fetch(`/api/brands/${brand.id}/block`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json();
        alert(err.detail || 'Помилка');
        return;
      }
      fetchBrands();
    } catch (e: any) {
      alert(e.message);
    }
  };

  // Inline edit brandname
  const startEdit = (brand: Brand) => {
    setEditingId(brand.id);
    setEditValue(brand.brandname);
  };

  const saveEdit = async () => {
    if (!editingId || !editValue.trim()) { setEditingId(null); return; }
    try {
      await fetch(`/api/brands/${editingId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ brandname: editValue.trim() }),
      });
      setEditingId(null);
      fetchBrands();
    } catch { setEditingId(null); }
  };

  // Concern change
  const handleConcernChange = async (brandId: number, concernId: number | null) => {
    setEditingConcernBrandId(null);
    setShowNewConcernInput(false);
    try {
      await fetch(`/api/brands/${brandId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ concern_id: concernId === null ? 0 : concernId }),
      });
      fetchBrands();
    } catch { /* ignore */ }
  };

  const handleCreateConcern = async (brandId: number) => {
    if (!newConcernName.trim()) return;
    try {
      const res = await fetch('/api/brand-concerns', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newConcernName.trim() }),
      });
      if (res.ok) {
        const data = await res.json();
        await handleConcernChange(brandId, data.id);
        fetchConcerns();
        setNewConcernName('');
      }
    } catch { /* ignore */ }
  };

  const handleRefresh = () => { setIsRefreshing(true); fetchBrands(); fetchConcerns(); };
  const handleResetFilters = () => {
    setPage(1);
    setSortBy('brandname');
    setSortDir('asc');
    setSelectedConcernId(null);
    setHasProducts(null);
    setSelected(new Set());
  };

  const selectedBrands = brands.filter(b => selected.has(b.id));

  return (
    <MainLayout
      filterPanelContent={
        <BrandsFilterPanel
          concerns={concerns}
          selectedConcernId={selectedConcernId}
          onConcernChange={(id) => { setSelectedConcernId(id); setPage(1); }}
          hasProducts={hasProducts}
          onHasProductsChange={(v) => { setHasProducts(v); setPage(1); }}
        />
      }
      onRefresh={handleRefresh}
      isRefreshing={isRefreshing}
      onResetFilters={handleResetFilters}
    >
      <div className="p-4 pb-24 bg-white dark:bg-gray-800 shadow-md rounded-lg w-full">
        {/* Header */}
        <div className="sticky top-0 z-20 bg-white/90 dark:bg-gray-800/90 backdrop-blur px-2 py-2 -mx-2 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center mb-3">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">
            Бренди
            <span className="ml-2 text-base font-normal text-gray-400">({total})</span>
          </h1>
          {currentSearchTerm && (
            <span className="text-sm text-gray-500 dark:text-gray-400">Пошук: «{currentSearchTerm}»</span>
          )}
        </div>

        {/* Merge bar */}
        {selectedBrands.length >= 2 && (
          <div className="mb-3">
            <MergeBar
              selected={selectedBrands}
              onMerge={handleMerge}
              onCancel={() => setSelected(new Set())}
            />
          </div>
        )}

        {/* Table */}
        {loading && brands.length === 0 ? (
          <div className="flex justify-center items-center h-48 text-gray-400">Завантаження...</div>
        ) : error ? (
          <div className="flex justify-center items-center h-48 text-red-500">{error}</div>
        ) : (
          <div className="overflow-x-auto rounded border border-gray-200 dark:border-gray-700">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600">
                <tr>
                  <th className="px-2 py-3 w-8">
                    <input
                      type="checkbox"
                      checked={brands.length > 0 && selected.size === brands.length}
                      onChange={toggleSelectAll}
                      className="rounded border-gray-300"
                    />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('id')}>
                    №<SortIcon field="id" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer" onClick={() => handleSort('brandname')}>
                    Назва бренду<SortIcon field="brandname" />
                  </th>
                  <th className="px-3 py-3 text-left font-semibold text-gray-600 dark:text-gray-300 cursor-pointer" onClick={() => handleSort('concern_name')}>
                    Консерн<SortIcon field="concern_name" />
                  </th>
                  <th className="px-3 py-3 text-center font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('available_pairs')}>
                    Наявні<SortIcon field="available_pairs" />
                  </th>
                  <th className="px-3 py-3 text-center font-semibold text-gray-600 dark:text-gray-300 cursor-pointer whitespace-nowrap" onClick={() => handleSort('total_products')}>
                    Всього<SortIcon field="total_products" />
                  </th>
                  <th className="px-3 py-3 text-center font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">
                    Дії
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {brands.length === 0 ? (
                  <tr><td colSpan={7}><BmsEmpty label="Брендів не знайдено" /></td></tr>
                ) : brands.map(b => (
                  <tr
                    key={b.id}
                    className={`hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors ${selected.has(b.id) ? 'bg-blue-50 dark:bg-blue-900/20' : ''}`}
                  >
                    {/* Checkbox */}
                    <td className="px-2 py-2 text-center">
                      <input
                        type="checkbox"
                        checked={selected.has(b.id)}
                        onChange={() => toggleSelect(b.id)}
                        className="rounded border-gray-300"
                      />
                    </td>

                    {/* ID */}
                    <td className="px-3 py-2 text-gray-400 text-xs">{b.id}</td>

                    {/* Brand name (inline editable) */}
                    <td className="px-3 py-2">
                      {editingId === b.id ? (
                        <input
                          autoFocus
                          value={editValue}
                          onChange={e => setEditValue(e.target.value)}
                          onBlur={saveEdit}
                          onKeyDown={e => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') setEditingId(null); }}
                          className="px-1 py-0.5 border border-blue-400 rounded text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 w-full"
                        />
                      ) : (
                        <span
                          onDoubleClick={() => startEdit(b)}
                          className="text-gray-900 dark:text-gray-100 cursor-text font-medium"
                          title="Подвійний клік для редагування"
                        >
                          {b.brandname}
                        </span>
                      )}
                    </td>

                    {/* Concern (inline dropdown) */}
                    <td className="px-3 py-2">
                      {editingConcernBrandId === b.id ? (
                        <div className="flex flex-col gap-1">
                          <select
                            autoFocus
                            value={b.concern_id ?? ''}
                            onChange={e => {
                              const val = e.target.value;
                              if (val === '__new__') {
                                setShowNewConcernInput(true);
                              } else {
                                handleConcernChange(b.id, val ? Number(val) : null);
                              }
                            }}
                            onBlur={() => { if (!showNewConcernInput) setEditingConcernBrandId(null); }}
                            className="px-1 py-0.5 border border-blue-400 rounded text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
                          >
                            <option value="">— Без консерну —</option>
                            {concerns.map(c => (
                              <option key={c.id} value={c.id}>{c.name}{c.country ? ` (${c.country})` : ''}</option>
                            ))}
                            <option value="__new__">+ Створити новий...</option>
                          </select>
                          {showNewConcernInput && (
                            <div className="flex gap-1">
                              <input
                                autoFocus
                                value={newConcernName}
                                onChange={e => setNewConcernName(e.target.value)}
                                onKeyDown={e => { if (e.key === 'Enter') handleCreateConcern(b.id); if (e.key === 'Escape') { setShowNewConcernInput(false); setEditingConcernBrandId(null); } }}
                                placeholder="Назва консерну"
                                className="px-1 py-0.5 border border-blue-400 rounded text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 flex-1"
                              />
                              <button
                                onClick={() => handleCreateConcern(b.id)}
                                className="px-1.5 py-0.5 bg-blue-600 text-white text-xs rounded"
                              >OK</button>
                            </div>
                          )}
                        </div>
                      ) : (
                        <span
                          onClick={() => { setEditingConcernBrandId(b.id); setShowNewConcernInput(false); }}
                          className={`cursor-pointer text-sm ${b.concern_name ? 'text-blue-600 dark:text-blue-400 hover:underline' : 'text-gray-400 italic'}`}
                          title="Клік для зміни консерну"
                        >
                          {b.concern_name || '—'}
                        </span>
                      )}
                    </td>

                    {/* Available pairs */}
                    <td className="px-3 py-2 text-center">
                      <span className={`font-medium ${b.available_pairs > 0 ? 'text-green-600 dark:text-green-400' : 'text-gray-400'}`}>
                        {b.available_pairs}
                      </span>
                    </td>

                    {/* Total products */}
                    <td className="px-3 py-2 text-center text-gray-600 dark:text-gray-300">
                      {b.total_products}
                    </td>

                    {/* Actions */}
                    <td className="px-3 py-2 text-center">
                      <button
                        onClick={() => handleBlock(b)}
                        title="Заблокувати бренд (видалити як некоректний)"
                        className="text-red-400 hover:text-red-600 dark:hover:text-red-400 transition-colors text-xs"
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
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
    </MainLayout>
  );
};

export default BrandsPage;
