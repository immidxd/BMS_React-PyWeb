import React, { useEffect, useState, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  fetchSuppliers, mergeSuppliers, updateSupplier, deleteSupplier,
  fetchSupplierGroups, createSupplierGroup,
  fetchSupplierAliases, splitSupplier,
  type Supplier, type SupplierList, type SupplierGroup,
} from '../../services/referenceService';
import Pagination from '../common/Pagination';
import BmsEmpty from '../common/BmsEmpty';
import { confirmDialog } from '../../ui/feedback';

type SortCol = 'id' | 'name' | 'product_count' | 'shipments_count' | 'total_spent' | 'avg_price';

const fmt = (n: number) => n.toLocaleString('uk-UA', { maximumFractionDigits: 0 });
const fmtPrice = (n: number) => n.toLocaleString('uk-UA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const SuppliersTable: React.FC = () => {
  const [items, setItems] = useState<Supplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [perPage] = useState(100);
  const [total, setTotal] = useState(0);
  const [sortBy, setSortBy] = useState<SortCol>('name');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [mergeTarget, setMergeTarget] = useState<number | null>(null);
  const [mergeName, setMergeName] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [groups, setGroups] = useState<SupplierGroup[]>([]);
  const [editingGroupId, setEditingGroupId] = useState<number | null>(null);
  const [newGroupName, setNewGroupName] = useState('');
  const [showNewGroupInput, setShowNewGroupInput] = useState(false);
  const [expandedAliases, setExpandedAliases] = useState<number | null>(null);
  const [aliasesMap, setAliasesMap] = useState<Record<number, { id: number; alias_name: string; delivery_count: number }[]>>({});
  const [aliasesLoading, setAliasesLoading] = useState(false);
  const [splittingAlias, setSplittingAlias] = useState<number | null>(null);
  const navigate = useNavigate();
  const location = useLocation();

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data: SupplierList = await fetchSuppliers(undefined, page, perPage, sortBy as any, sortDir);
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

  const loadGroups = useCallback(async () => {
    try {
      const data = await fetchSupplierGroups();
      setGroups(data);
    } catch (e) {
      console.error('Failed to load supplier groups', e);
    }
  }, []);

  useEffect(() => { loadGroups(); }, [loadGroups]);

  useEffect(() => {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('sort_by', sortBy);
    params.set('sort_dir', sortDir);
    navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
  }, [page, sortBy, sortDir, navigate, location.pathname]);

  const totalPages = Math.max(1, Math.ceil(total / perPage));

  const toggleSort = (col: SortCol) => {
    if (sortBy === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortBy(col); setSortDir(col === 'name' ? 'asc' : 'desc'); }
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
      await mergeSuppliers(mergeTarget, sourceIds, mergeName || undefined);
      setSelected(new Set());
      setMergeTarget(null);
      setMergeName('');
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

  const handleGroupChange = async (supplierId: number, groupId: number | null) => {
    try {
      await updateSupplier(supplierId, { group_id: groupId });
      setEditingGroupId(null);
      await loadData();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Помилка збереження групи');
    }
  };

  const handleCreateGroup = async (supplierId: number) => {
    if (!newGroupName.trim()) return;
    try {
      const { id } = await createSupplierGroup({ name: newGroupName.trim() });
      await loadGroups();
      await handleGroupChange(supplierId, id);
      setNewGroupName('');
      setShowNewGroupInput(false);
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Помилка створення групи');
    }
  };

  const handleDelete = async (id: number) => {
    if (!(await confirmDialog('Видалити постачальника?'))) return;
    try {
      await deleteSupplier(id);
      await loadData();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Помилка видалення');
    }
  };

  const toggleAliases = async (supplierId: number) => {
    if (expandedAliases === supplierId) {
      setExpandedAliases(null);
      return;
    }
    setExpandedAliases(supplierId);
    if (!aliasesMap[supplierId]) {
      setAliasesLoading(true);
      try {
        const aliases = await fetchSupplierAliases(supplierId);
        setAliasesMap(prev => ({ ...prev, [supplierId]: aliases }));
      } catch (e) {
        console.error('Failed to load aliases', e);
      } finally {
        setAliasesLoading(false);
      }
    }
  };

  const handleSplit = async (aliasId: number, aliasName: string, supplierId: number) => {
    if (!(await confirmDialog(`Розділити "${aliasName}" назад у окремого постачальника?\nВідповідні поставки будуть переприсвоєні.`))) return;
    setSplittingAlias(aliasId);
    try {
      const result = await splitSupplier(aliasId);
      alert(`Створено постачальника "${result.alias_name}" (ID ${result.new_supplier_id}), переміщено ${result.moved_deliveries} поставок.`);
      // Remove alias from local state
      setAliasesMap(prev => ({
        ...prev,
        [supplierId]: (prev[supplierId] || []).filter(a => a.id !== aliasId),
      }));
      await loadData();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Помилка розділення');
    } finally {
      setSplittingAlias(null);
    }
  };

  const sortIcon = (col: SortCol) => {
    if (sortBy !== col) return '';
    return sortDir === 'asc' ? ' \u2191' : ' \u2193';
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
          <input
            type="text"
            placeholder="Нова назва (необов'язково)"
            value={mergeName}
            onChange={e => setMergeName(e.target.value)}
            className="text-sm border rounded px-2 py-1 w-48"
          />
          <button
            onClick={handleMerge}
            disabled={!mergeTarget}
            className="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-40"
          >
            Злити
          </button>
          <button
            onClick={() => { setSelected(new Set()); setMergeTarget(null); setMergeName(''); }}
            className="px-3 py-1 text-sm text-gray-600 hover:text-red-600"
          >
            Скасувати
          </button>
        </div>
      )}

      <div className="overflow-x-auto rounded shadow border border-gray-200 bg-white">
        <table className="min-w-full text-sm [&_th]:text-center [&_td]:text-center">
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
              <th className="px-3 py-3 text-left font-semibold cursor-pointer w-14" onClick={() => toggleSort('id')}>ID{sortIcon('id')}</th>
              <th className="px-3 py-3 text-left font-semibold cursor-pointer" onClick={() => toggleSort('name')}>Назва{sortIcon('name')}</th>
              <th className="px-3 py-3 text-left font-semibold w-36">Група</th>
              <th className="px-3 py-3 text-center font-semibold cursor-pointer w-20" onClick={() => toggleSort('product_count')}>Товарів{sortIcon('product_count')}</th>
              <th className="px-3 py-3 text-center font-semibold cursor-pointer w-24" onClick={() => toggleSort('shipments_count')}>Поставок{sortIcon('shipments_count')}</th>
              <th className="px-3 py-3 text-right font-semibold cursor-pointer w-28" onClick={() => toggleSort('total_spent')}>Витрачено{sortIcon('total_spent')}</th>
              <th className="px-3 py-3 text-right font-semibold cursor-pointer w-24" onClick={() => toggleSort('avg_price')}>Сер. ціна{sortIcon('avg_price')}</th>
              <th className="px-3 py-3 text-left font-semibold w-40">Бренди</th>
              <th className="px-3 py-3 text-center font-semibold w-20">Дії</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={10} className="text-center py-8 text-gray-400">Завантаження...</td></tr>
            ) : error ? (
              <tr><td colSpan={10} className="text-center py-8 text-red-500">{error}</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={10}><BmsEmpty label="Постачальників не знайдено" /></td></tr>
            ) : (
              items.map(s => (
                <React.Fragment key={s.id}>
                <tr className={`border-b last:border-b-0 hover:bg-gray-50 ${selected.has(s.id) ? 'bg-blue-50' : ''}`}>
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={selected.has(s.id)}
                      onChange={() => toggleSelect(s.id)}
                      className="w-3.5 h-3.5"
                    />
                  </td>
                  <td className="px-3 py-2 text-gray-400 text-xs">{s.id}</td>
                  <td className="px-3 py-2">
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
                  <td className="px-3 py-2 text-xs">
                    {editingGroupId === s.id ? (
                      <div className="flex flex-col gap-1 min-w-[130px]">
                        <select
                          autoFocus
                          className="border rounded px-1 py-0.5 text-xs w-full"
                          value={s.group_id ?? ''}
                          onChange={e => {
                            const val = e.target.value;
                            if (val === '__new__') {
                              setShowNewGroupInput(true);
                            } else {
                              handleGroupChange(s.id, val ? Number(val) : null);
                            }
                          }}
                          onBlur={() => { if (!showNewGroupInput) setEditingGroupId(null); }}
                        >
                          <option value="">— Без групи —</option>
                          {groups.map(g => (
                            <option key={g.id} value={g.id}>{g.name}</option>
                          ))}
                          <option value="__new__">+ Створити нову...</option>
                        </select>
                        {showNewGroupInput && (
                          <div className="flex gap-1">
                            <input
                              type="text"
                              value={newGroupName}
                              onChange={e => setNewGroupName(e.target.value)}
                              onKeyDown={e => {
                                if (e.key === 'Enter') handleCreateGroup(s.id);
                                if (e.key === 'Escape') { setShowNewGroupInput(false); setNewGroupName(''); setEditingGroupId(null); }
                              }}
                              placeholder="Назва групи"
                              className="border rounded px-1 py-0.5 text-xs flex-1"
                              autoFocus
                            />
                            <button onClick={() => handleCreateGroup(s.id)} className="text-green-600 text-xs font-bold">OK</button>
                            <button onClick={() => { setShowNewGroupInput(false); setNewGroupName(''); }} className="text-gray-400 text-xs">✕</button>
                          </div>
                        )}
                      </div>
                    ) : (
                      <span
                        onClick={() => { setEditingGroupId(s.id); setShowNewGroupInput(false); setNewGroupName(''); }}
                        className="cursor-pointer hover:underline text-gray-600"
                        title="Клікніть для зміни групи"
                      >
                        {s.group_name || <span className="text-gray-300">—</span>}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span className={`inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 rounded-full text-xs font-bold ${
                      s.product_count > 0 ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                    }`}>{fmt(s.product_count)}</span>
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span className={`text-xs ${s.shipments_count > 0 ? 'text-indigo-600 font-medium' : 'text-gray-400'}`}>
                      {s.shipments_count}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-xs tabular-nums">
                    {s.total_spent > 0 ? fmtPrice(s.total_spent) : '—'}
                  </td>
                  <td className="px-3 py-2 text-right text-xs tabular-nums">
                    {s.avg_price > 0 ? fmtPrice(s.avg_price) : '—'}
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-500 max-w-[160px] truncate" title={s.top_brands || ''}>
                    {s.top_brands || '—'}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <div className="flex gap-1 justify-center">
                      <button
                        onClick={() => toggleAliases(s.id)}
                        className={`text-xs ${expandedAliases === s.id ? 'text-orange-600' : 'text-gray-400 hover:text-orange-500'}`}
                        title="Показати злиті назви (аліаси)"
                      >{expandedAliases === s.id ? '▼' : '▶'}</button>
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
                {expandedAliases === s.id && (
                  <tr className="bg-orange-50/50">
                    <td colSpan={10} className="px-6 py-2">
                      {aliasesLoading ? (
                        <span className="text-xs text-gray-400">Завантаження...</span>
                      ) : !aliasesMap[s.id] || aliasesMap[s.id].length === 0 ? (
                        <span className="text-xs text-gray-400">Немає злитих назв (аліасів)</span>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          <span className="text-xs text-gray-500 mr-1 self-center">Злиті назви:</span>
                          {aliasesMap[s.id].map(a => (
                            <div key={a.id} className="inline-flex items-center gap-1.5 bg-white border border-orange-200 rounded-full px-3 py-1 text-xs">
                              <span className="font-medium text-orange-800">{a.alias_name}</span>
                              {a.delivery_count > 0 && (
                                <span className="text-gray-400">({a.delivery_count} пост.)</span>
                              )}
                              <button
                                onClick={() => handleSplit(a.id, a.alias_name, s.id)}
                                disabled={splittingAlias === a.id}
                                className="ml-1 text-orange-600 hover:text-orange-800 font-medium disabled:opacity-40"
                                title={`Розділити "${a.alias_name}" назад в окремого постачальника`}
                              >
                                {splittingAlias === a.id ? '...' : '↗ Розділити'}
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                )}
                </React.Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="grid grid-cols-[1fr_auto_1fr] items-center mt-4 gap-4">
        <span className="justify-self-start text-sm text-gray-500">Всього: {total} постачальників</span>
        <div className="justify-self-center flex justify-center">
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
        <span />
      </div>
    </div>
  );
};

export default SuppliersTable;
