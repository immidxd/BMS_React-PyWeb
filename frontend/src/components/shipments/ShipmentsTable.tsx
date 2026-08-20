import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  fetchShipments, fetchShipment, groupShipments, ungroupShipments, updateShipment,
  fetchShipmentGroups,
  type Shipment, type ShipmentList, type ShipmentGroup,
} from '../../services/referenceService';
import Pagination from '../common/Pagination';
import BmsEmpty from '../common/BmsEmpty';
import DeliveryCardModal from './DeliveryCardModal';

type SortCol = 'id' | 'shipment_date' | 'supplier_name' | 'items_count' | 'total_cost' | 'created_at';

const fmtDate = (d: string | null) => {
  if (!d) return '—';
  try {
    const dt = new Date(d);
    return dt.toLocaleDateString('uk-UA');
  } catch { return d; }
};
const fmtPrice = (n: number) => n.toLocaleString('uk-UA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt = (n: number) => n.toLocaleString('uk-UA', { maximumFractionDigits: 0 });

interface ShipmentsTableProps {
  reloadSignal?: number;
  searchTerm?: string;
  onLoadComplete?: () => void;
}

const ShipmentsTable: React.FC<ShipmentsTableProps> = ({ reloadSignal, searchTerm = '', onLoadComplete }) => {
  const [items, setItems] = useState<Shipment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [perPage] = useState(50);
  const [total, setTotal] = useState(0);
  const [sortBy, setSortBy] = useState<SortCol>('shipment_date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [groupName, setGroupName] = useState('');
  const [groups, setGroups] = useState<ShipmentGroup[]>([]);
  const [existingGroupId, setExistingGroupId] = useState<number | null>(null);
  const [cardShipment, setCardShipment] = useState<Shipment | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const listAbortRef = useRef<AbortController | null>(null);
  const listRequestRef = useRef(0);

  // Крос-таб «Поставка» з картки товару: deliveryid чекає в localStorage
  // (bms:pendingDeliveryCard). Читаємо при монтуванні І на подію-поштовх
  // (bms:deliveries-open-card) — щоб працювало і коли вкладка вже відкрита.
  useEffect(() => {
    const openPending = () => {
      const raw = localStorage.getItem('bms:pendingDeliveryCard');
      if (!raw) return;
      localStorage.removeItem('bms:pendingDeliveryCard');
      const id = Number(raw);
      if (!Number.isFinite(id) || id <= 0) return;
      fetchShipment(id).then(setCardShipment).catch((e) => console.error('open delivery card', e));
    };
    openPending();
    window.addEventListener('bms:deliveries-open-card', openPending);
    return () => window.removeEventListener('bms:deliveries-open-card', openPending);
  }, []);

  const loadData = useCallback(async () => {
    listAbortRef.current?.abort();
    const controller = new AbortController();
    listAbortRef.current = controller;
    const requestId = ++listRequestRef.current;
    setLoading(true);
    setError(null);
    try {
      const data: ShipmentList = await fetchShipments(
        searchTerm.trim() || undefined, page, perPage, sortBy, sortDir, undefined, undefined, controller.signal,
      );
      if (requestId !== listRequestRef.current) return;
      setItems(data.items);
      setTotal(data.total);
    } catch (e: any) {
      if (e?.name === 'CanceledError' || e?.code === 'ERR_CANCELED') return;
      if (requestId === listRequestRef.current) setError('Помилка завантаження поставок');
      console.error(e);
    } finally {
      if (requestId === listRequestRef.current) {
        setLoading(false);
        onLoadComplete?.();
      }
    }
  }, [page, perPage, sortBy, sortDir, searchTerm, onLoadComplete]);

  const loadGroups = useCallback(async () => {
    try {
      const g = await fetchShipmentGroups();
      setGroups(g);
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => { loadData(); }, [loadData, reloadSignal]);
  useEffect(() => { setPage(1); }, [searchTerm]);
  useEffect(() => () => listAbortRef.current?.abort(), []);
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
    else { setSortBy(col); setSortDir(col === 'supplier_name' ? 'asc' : 'desc'); }
  };

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const handleGroup = async () => {
    if (selected.size < 2) return;
    const ids = Array.from(selected);
    try {
      if (existingGroupId) {
        await groupShipments(ids, existingGroupId);
      } else {
        await groupShipments(ids, undefined, groupName || undefined);
      }
      setSelected(new Set());
      setGroupName('');
      setExistingGroupId(null);
      await loadData();
      await loadGroups();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Помилка групування');
    }
  };

  const handleUngroup = async (ids: number[]) => {
    try {
      await ungroupShipments(ids);
      await loadData();
      await loadGroups();
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Помилка розгрупування');
    }
  };

  const sortIcon = (col: SortCol) => {
    if (sortBy !== col) return '';
    return sortDir === 'asc' ? ' \u2191' : ' \u2193';
  };

  return (
    <div className="w-full">
      {/* Group bar */}
      {selected.size >= 2 && (
        <div className="mb-3 p-3 bg-indigo-50 border border-indigo-200 rounded flex items-center gap-3 flex-wrap">
          <span className="text-sm font-medium text-indigo-800">Обрано: {selected.size} поставок</span>
          <select
            className="text-sm border rounded px-2 py-1"
            value={existingGroupId ?? ''}
            onChange={e => setExistingGroupId(Number(e.target.value) || null)}
          >
            <option value="">Нова група</option>
            {groups.map(g => (
              <option key={g.id} value={g.id}>{g.name} ({g.shipments_count} пост.)</option>
            ))}
          </select>
          {!existingGroupId && (
            <input
              type="text"
              placeholder="Назва групи"
              value={groupName}
              onChange={e => setGroupName(e.target.value)}
              className="text-sm border rounded px-2 py-1 w-48"
            />
          )}
          <button
            onClick={handleGroup}
            className="px-3 py-1 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700"
          >
            Згрупувати
          </button>
          <button
            onClick={() => { setSelected(new Set()); setGroupName(''); setExistingGroupId(null); }}
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
              <th className="px-3 py-3 text-left font-semibold cursor-pointer w-28" onClick={() => toggleSort('shipment_date')}>Дата{sortIcon('shipment_date')}</th>
              <th className="px-3 py-3 text-left font-semibold cursor-pointer" onClick={() => toggleSort('supplier_name')}>Постачальник{sortIcon('supplier_name')}</th>
              <th className="px-3 py-3 text-left font-semibold">Аркуш</th>
              <th className="px-3 py-3 text-center font-semibold cursor-pointer w-20" onClick={() => toggleSort('items_count')}>Товарів{sortIcon('items_count')}</th>
              <th className="px-3 py-3 text-right font-semibold cursor-pointer w-28" onClick={() => toggleSort('total_cost')}>Вартість{sortIcon('total_cost')}</th>
              <th className="px-3 py-3 text-left font-semibold w-36">Група</th>
              <th className="px-3 py-3 text-center font-semibold w-16">Дії</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={9} className="text-center py-8 text-gray-400">Завантаження...</td></tr>
            ) : error ? (
              <tr><td colSpan={9} className="text-center py-8 text-red-500">{error}</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={9}><BmsEmpty label="Поставок не знайдено" /></td></tr>
            ) : (
              items.map(sh => (
                <tr
                  key={sh.id}
                  onClick={() => setCardShipment(sh)}
                  className={`border-b last:border-b-0 hover:bg-gray-50 cursor-pointer ${selected.has(sh.id) ? 'bg-indigo-50' : ''}`}
                >
                  <td className="px-3 py-2" onClick={e => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selected.has(sh.id)}
                      onChange={() => toggleSelect(sh.id)}
                      className="w-3.5 h-3.5"
                    />
                  </td>
                  <td className="px-3 py-2 text-gray-400 text-xs">{sh.id}</td>
                  <td className="px-3 py-2 text-xs tabular-nums">{fmtDate(sh.shipment_date)}</td>
                  <td className="px-3 py-2 font-medium">{sh.supplier_name || '—'}</td>
                  <td className="px-3 py-2 text-xs max-w-[180px] truncate text-indigo-600 dark:text-indigo-400" title={sh.sheet_name || ''}>
                    {sh.sheet_name || '—'}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span className={`inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 rounded-full text-xs font-bold ${
                      sh.items_count > 0 ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                    }`}>{fmt(sh.items_count)}</span>
                  </td>
                  <td className="px-3 py-2 text-right text-xs tabular-nums">
                    {sh.total_cost > 0 ? fmtPrice(sh.total_cost) : '—'}
                  </td>
                  <td className="px-3 py-2">
                    {sh.group_name ? (
                      <span className="inline-flex items-center gap-1">
                        <span className="text-xs bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded-full">{sh.group_name}</span>
                        <button
                          onClick={e => { e.stopPropagation(); handleUngroup([sh.id]); }}
                          className="text-gray-400 hover:text-red-500 text-xs"
                          title="Видалити з групи"
                        >✕</button>
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {sh.notes && (
                      <span className="text-xs text-gray-400" title={sh.notes}>📝</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="grid grid-cols-[1fr_auto_1fr] items-center mt-4 gap-4">
        <span className="justify-self-start text-sm text-gray-500">Всього: {total} поставок</span>
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

      <DeliveryCardModal
        shipment={cardShipment}
        open={!!cardShipment}
        onClose={() => setCardShipment(null)}
      />
    </div>
  );
};

export default ShipmentsTable;
