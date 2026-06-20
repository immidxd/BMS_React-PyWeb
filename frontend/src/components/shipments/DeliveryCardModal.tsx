import React, { useEffect, useState, useCallback } from 'react';
import { productService } from '../../services/productService';
import type { Product, ProductFilters } from '../../types/product';
import { deleteProductFromDelivery, syncDelivery, type Shipment } from '../../services/referenceService';
import QuickAddProductForm from './QuickAddProductForm';
import ProductDetailsModal from '../products/ProductDetailsModal';

// Рахований статус продажу (як у таблиці Товарів) — не сирий журнальний statusid.
const statusOf = (p: Product): { label: string; cls: string } => {
  const sold = p.sold_count || 0;
  const qty = p.quantity || 1;
  // Продано = order-based (sold_count покриває кількість) АБО журнал «Статус»=Продано
  if ((qty > 0 && sold >= qty) || p.status_name === 'Продано') return { label: 'Продано', cls: 'text-red-600' };
  if (p.is_reserved) return { label: 'Заброньовано', cls: 'text-amber-600' };
  if (p.status_name === 'Подаровано') return { label: 'Подаровано', cls: 'text-purple-600' };
  return { label: 'Непродано', cls: 'text-green-600' };
};

interface Props {
  shipment: Shipment | null;
  open: boolean;
  onClose: () => void;
}

const fmtDate = (d: string | null) => {
  if (!d) return '—';
  try { return new Date(d).toLocaleDateString('uk-UA'); } catch { return d; }
};
const fmtPrice = (n?: number | null) =>
  (n ?? 0).toLocaleString('uk-UA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const DeliveryCardModal: React.FC<Props> = ({ shipment, open, onClose }) => {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<ProductFilters | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [syncInfo, setSyncInfo] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);

  // Швидке завантаження товарів з БД (без re-sync) — для рефрешу після add/delete.
  const loadProducts = useCallback(() => {
    if (!shipment) return Promise.resolve();
    return productService
      .getProducts({ shipment_id: shipment.id, per_page: 200, sort_by: 'productnumber', sort_dir: 'asc' })
      .then(r => setProducts(r.items || []))
      .catch(() => setError('Помилка завантаження товарів завозу'));
  }, [shipment]);

  // Відкриття картки: спершу точкова синхронізація цієї вкладки з аркушем, тоді показ.
  const syncAndLoad = useCallback(async () => {
    if (!shipment) return;
    setLoading(true); setError(null); setSyncInfo(null);
    try {
      const r = await syncDelivery(shipment.id);
      if (r.deleted > 0) setSyncInfo(`Синхронізовано з журналом · прибрано ${r.deleted}`);
    } catch {
      setSyncInfo('⚠ Не вдалось звірити з журналом — показано дані з програми');
    }
    await loadProducts();
    setLoading(false);
  }, [shipment, loadProducts]);

  useEffect(() => {
    if (!open || !shipment) return;
    setShowForm(false); setProducts([]); setSyncInfo(null);
    syncAndLoad();
    productService.getFilters().then(setFilters).catch(() => {});
  }, [open, shipment, syncAndLoad]);

  if (!open || !shipment) return null;

  const removeProduct = async (p: Product) => {
    if (!window.confirm(`Видалити товар ${p.productnumber}?`)) return;
    try {
      await deleteProductFromDelivery(shipment.id, p.id);
      loadProducts();
    } catch (e: any) {
      window.alert(e?.response?.data?.detail || 'Не вдалося видалити товар');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" onMouseDown={onClose}>
      <div className="relative bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-5xl h-[88vh] flex flex-col" onMouseDown={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-gray-100 dark:border-gray-800">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{shipment.sheet_name || `Завіз #${shipment.id}`}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-500 dark:text-gray-400">
              <span>📅 {fmtDate(shipment.shipment_date)}</span>
              <span>🏷 {shipment.supplier_name || 'Без постачальника'}</span>
              <span>📦 {products.length} товарів</span>
              {shipment.total_cost > 0 && <span>💰 {fmtPrice(shipment.total_cost)}</span>}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => setShowForm(s => !s)} disabled={loading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-black text-white hover:bg-gray-800 disabled:opacity-50">
              <span className="text-base leading-none">＋</span> Додати товар
            </button>
            <button onClick={onClose} aria-label="Закрити" className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-2xl leading-none">×</button>
          </div>
        </div>

        {showForm && (
          <div className="px-6 py-4 border-b border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-800/40">
            <QuickAddProductForm deliveryId={shipment.id} onSaved={loadProducts} filters={filters} />
          </div>
        )}

        {/* Body */}
        <div className="flex-1 min-h-0 overflow-auto px-6 py-4">
          {loading && (
            <div className="py-24 flex flex-col items-center justify-center gap-3 text-gray-400">
              <div className="w-8 h-8 border-2 border-gray-300 border-t-gray-600 rounded-full animate-spin" />
              <span className="text-sm">Синхронізація з журналом…</span>
            </div>
          )}
          {!loading && (
            <>
              {syncInfo && (
                <div className="mb-3 text-xs text-gray-400">{syncInfo}</div>
              )}
              {error && <div className="py-16 text-center text-red-500">{error}</div>}
              {!error && products.length === 0 && (
                <div className="py-16 text-center text-gray-400">У цьому завозі ще немає товарів</div>
              )}
              {!error && products.length > 0 && (
                <table className="w-full text-sm">
                  <thead className="text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-gray-800">
                    <tr>
                      <th className="px-2 py-2 text-left font-semibold">Номер</th>
                      <th className="px-2 py-2 text-left font-semibold">Тип</th>
                      <th className="px-2 py-2 text-left font-semibold">Бренд</th>
                      <th className="px-2 py-2 text-left font-semibold">Модель</th>
                      <th className="px-2 py-2 text-center font-semibold">Розмір</th>
                      <th className="px-2 py-2 text-right font-semibold">Ціна</th>
                      <th className="px-2 py-2 text-center font-semibold">Статус</th>
                      <th className="px-2 py-2 w-10 text-center font-semibold"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {products.map(p => {
                      const st = statusOf(p);
                      return (
                      <tr key={p.id} onClick={() => setDetailId(p.id)}
                        className="border-b last:border-b-0 border-gray-50 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/40 cursor-pointer">
                        <td className="px-2 py-2 font-medium tabular-nums">{p.productnumber}</td>
                        <td className="px-2 py-2">{p.type_name || '—'}</td>
                        <td className="px-2 py-2">{p.brand_name || '—'}</td>
                        <td className="px-2 py-2 text-gray-600 dark:text-gray-300 max-w-[200px] truncate" title={p.model || ''}>{p.model || '—'}</td>
                        <td className="px-2 py-2 text-center tabular-nums">{p.sizeeu || p.size_letter || '—'}</td>
                        <td className="px-2 py-2 text-right tabular-nums">{p.price ? fmtPrice(p.price) : '—'}</td>
                        <td className={`px-2 py-2 text-center text-xs font-medium ${st.cls}`}>{st.label}</td>
                        <td className="px-2 py-2 text-center">
                          <button onClick={e => { e.stopPropagation(); removeProduct(p); }} title="Видалити товар"
                            className="text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded px-1.5 py-0.5">🗑</button>
                        </td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      </div>

      <div onMouseDown={e => e.stopPropagation()}>
        <ProductDetailsModal
          productId={detailId}
          open={!!detailId}
          onClose={() => { setDetailId(null); loadProducts(); }}
        />
      </div>
    </div>
  );
};

export default DeliveryCardModal;
