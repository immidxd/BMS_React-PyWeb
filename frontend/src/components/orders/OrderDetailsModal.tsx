import React, { useEffect, useState } from 'react';
import { fetchOrder, type OrderWithDetails, updateOrder, updateOrderItemPrice, addOrderItem, removeOrderItem, type FilterOptions } from '../../services/orderService';
import ProductDetailsModal from '../products/ProductDetailsModal';

interface Props {
  orderId: number | null;
  open: boolean;
  onClose: () => void;
  filterOptions?: FilterOptions;
  onSaved?: () => void;
}

const OrderDetailsModal: React.FC<Props> = ({ orderId, open, onClose, filterOptions, onSaved }) => {
  const [loading, setLoading] = useState(false);
  const [order, setOrder] = useState<OrderWithDetails | null>(null);
  const [saving, setSaving] = useState(false);
  const [edit, setEdit] = useState<Partial<OrderWithDetails>>({});
  const [cardProductId, setCardProductId] = useState<number | null>(null);
  const [editingItemId, setEditingItemId] = useState<number | null>(null);
  const [itemPriceDraft, setItemPriceDraft] = useState('');
  const [savingItem, setSavingItem] = useState(false);
  const [addPnum, setAddPnum] = useState('');
  const [addPrice, setAddPrice] = useState('');
  const [itemBusy, setItemBusy] = useState(false);

  const removeItem = async (itemId: number) => {
    if (!orderId) return;
    if (!window.confirm('Прибрати цю позицію із замовлення? (Запишеться в аркуш)')) return;
    setItemBusy(true);
    try { setOrder(await removeOrderItem(orderId, itemId)); }
    catch (e) { console.error('remove item failed', e); }
    finally { setItemBusy(false); }
  };

  const addItem = async () => {
    if (!orderId) return;
    const pn = addPnum.trim(); const pr = Number(addPrice);
    if (!pn || isNaN(pr) || pr < 0) return;
    setItemBusy(true);
    try {
      setOrder(await addOrderItem(orderId, pn, pr));
      setAddPnum(''); setAddPrice('');
    } catch (e: any) {
      console.error('add item failed', e);
      alert(e?.response?.data?.detail || 'Не вдалося додати позицію');
    } finally { setItemBusy(false); }
  };

  const saveItemPrice = async (itemId: number) => {
    if (!orderId) return;
    const v = Number(itemPriceDraft);
    if (isNaN(v) || v < 0) return;
    setSavingItem(true);
    try {
      const updated = await updateOrderItemPrice(orderId, itemId, v);
      setOrder(updated);          // refresh modal in place (sum recalculated)
      setEditingItemId(null);
    } catch (e) { console.error('Failed to save item price', e); }
    finally { setSavingItem(false); }
  };

  useEffect(() => {
    if (!open || !orderId) return;
    setLoading(true);
    fetchOrder(orderId)
      .then((o) => { setOrder(o); setEdit({
        order_status_id: o.order_status_id || undefined,
        payment_status_id: o.payment_status_id || undefined,
        payment_method_id: o.payment_method_id || undefined,
        delivery_method_id: o.delivery_method_id || undefined,
        delivery_status_id: o.delivery_status_id || undefined,
        tracking_number: o.tracking_number || undefined,
        notes: o.notes || undefined,
        sales_channel: (o as any).sales_channel || undefined,
      }); })
      .finally(() => setLoading(false));
  }, [open, orderId]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-label="Закрити" />
      <div className="relative bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-3xl mx-4 p-4">
        <div className="flex justify-between items-center mb-3">
          <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Деталі замовлення {orderId}</h2>
          <div className="flex gap-2">
            <button disabled={saving} onClick={async () => {
              if (!orderId) return;
              setSaving(true);
              try {
                await updateOrder(orderId, {
                  order_status_id: edit.order_status_id ?? null,
                  payment_status_id: edit.payment_status_id ?? null,
                  payment_method_id: edit.payment_method_id ?? null,
                  delivery_method_id: edit.delivery_method_id ?? null,
                  delivery_status_id: edit.delivery_status_id ?? null,
                  tracking_number: edit.tracking_number ?? null,
                  notes: edit.notes ?? null,
                  sales_channel: (edit as any).sales_channel ?? null,
                });
                if (onSaved) onSaved();
              } finally { setSaving(false); }
            }} className="px-2 py-1 text-sm rounded border border-blue-500 text-blue-600 hover:bg-blue-50 disabled:opacity-60">Зберегти</button>
            <button onClick={onClose} className="px-2 py-1 text-sm rounded border border-gray-300 hover:bg-gray-100 dark:border-gray-700 dark:hover:bg-gray-700">Закрити</button>
          </div>
        </div>
        {loading ? (
          <div className="py-8 text-center text-gray-500">Завантаження...</div>
        ) : !order ? (
          <div className="py-8 text-center text-gray-500">Не знайдено</div>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div><span className="text-gray-500">Клієнт:</span> <span className="font-medium">{order.client_name}</span></div>
              <div><span className="text-gray-500">Сума:</span> <span className="font-medium">{new Intl.NumberFormat('uk-UA',{style:'currency',currency:'UAH'}).format(order.total_amount)}</span></div>
              <div><span className="text-gray-500">Дата:</span> <span className="font-medium">{order.order_date}</span></div>
              <div>
                <span className="text-gray-500">Статус:</span>
                <select value={edit.order_status_id ?? ''} onChange={(e) => setEdit({ ...edit, order_status_id: e.target.value ? Number(e.target.value) : undefined })} className="ml-2 border rounded px-2 py-1">
                  <option value="">—</option>
                  {filterOptions?.order_statuses.map(os => (
                    <option key={os.id} value={os.id}>{os.name || (os as any).status_name}</option>
                  ))}
                </select>
              </div>
              <div>
                <span className="text-gray-500">Оплата:</span>
                <select value={edit.payment_status_id ?? ''} onChange={(e) => setEdit({ ...edit, payment_status_id: e.target.value ? Number(e.target.value) : undefined })} className="ml-2 border rounded px-2 py-1">
                  <option value="">—</option>
                  {filterOptions?.payment_statuses.map(ps => (
                    <option key={ps.id} value={ps.id}>{ps.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <span className="text-gray-500">Метод оплати:</span>
                <select value={edit.payment_method_id ?? ''} onChange={(e) => setEdit({ ...edit, payment_method_id: e.target.value ? Number(e.target.value) : undefined })} className="ml-2 border rounded px-2 py-1">
                  <option value="">—</option>
                  {filterOptions?.payment_methods.map(pm => (
                    <option key={pm.id} value={pm.id}>{pm.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <span className="text-gray-500">Доставка:</span>
                <select value={edit.delivery_method_id ?? ''} onChange={(e) => setEdit({ ...edit, delivery_method_id: e.target.value ? Number(e.target.value) : undefined })} className="ml-2 border rounded px-2 py-1">
                  <option value="">—</option>
                  {filterOptions?.delivery_methods.map(dm => (
                    <option key={dm.id} value={dm.id}>{dm.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <span className="text-gray-500">Статус доставки:</span>
                <select value={edit.delivery_status_id ?? ''} onChange={(e) => setEdit({ ...edit, delivery_status_id: e.target.value ? Number(e.target.value) : undefined })} className="ml-2 border rounded px-2 py-1">
                  <option value="">—</option>
                  {filterOptions?.delivery_statuses.map(ds => (
                    <option key={ds.id} value={ds.id}>{ds.name}</option>
                  ))}
                </select>
              </div>
              <div className="col-span-2 flex items-center gap-2">
                <span className="text-gray-500">Трекінг:</span>
                <input value={edit.tracking_number ?? ''} onChange={(e) => setEdit({ ...edit, tracking_number: e.target.value || undefined })} className="border rounded px-2 py-1 flex-1" />
              </div>
              <div className="col-span-2 flex items-center gap-2">
                <span className="text-gray-500">Примітки:</span>
                <input value={edit.notes ?? ''} onChange={(e) => setEdit({ ...edit, notes: e.target.value || undefined })} className="border rounded px-2 py-1 flex-1" />
              </div>
              <div className="flex items-center gap-2">
                <span className="text-gray-500">Канал:</span>
                <input value={(edit as any).sales_channel ?? ''} onChange={(e) => setEdit({ ...edit, sales_channel: e.target.value || undefined } as any)} className="border rounded px-2 py-1 flex-1" placeholder="Ефір / Telegram / ..." />
              </div>
            </div>
            <div className="overflow-x-auto border rounded">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 dark:bg-gray-700">
                  <tr>
                    <th className="px-3 py-2 text-left">№ товару</th>
                    <th className="px-3 py-2 text-left">Назва</th>
                    <th className="px-3 py-2 text-right">К-сть</th>
                    <th className="px-3 py-2 text-right">Ціна</th>
                    <th className="px-3 py-2 text-center w-10"></th>
                  </tr>
                </thead>
                <tbody>
                  {order.order_items?.map((it) => (
                    <tr key={it.id} className="border-t">
                      <td className="px-3 py-2">
                        {it.product_id ? (
                          <span
                            className="cursor-pointer text-blue-600 hover:text-blue-800 hover:underline dark:text-blue-400 dark:hover:text-blue-300"
                            title="Відкрити картку товару"
                            onClick={(e) => { e.stopPropagation(); setCardProductId(it.product_id); }}
                          >
                            {it.product_number || it.product_id}
                          </span>
                        ) : (it.product_number || '—')}
                      </td>
                      <td className="px-3 py-2">{it.product_name || '—'}</td>
                      <td className="px-3 py-2 text-right">{it.quantity}</td>
                      <td className="px-3 py-2 text-right">
                        {editingItemId === it.id ? (
                          <span className="inline-flex items-center gap-1 justify-end">
                            <input autoFocus type="number" value={itemPriceDraft}
                              onChange={(e) => setItemPriceDraft(e.target.value)}
                              onKeyDown={(e) => { if (e.key === 'Enter') saveItemPrice(it.id!); if (e.key === 'Escape') setEditingItemId(null); }}
                              className="w-24 px-2 py-0.5 text-right border rounded dark:bg-gray-800 dark:border-gray-600" />
                            <button onClick={() => saveItemPrice(it.id!)} disabled={savingItem} className="text-green-600 hover:text-green-700 px-1" title="Зберегти">✓</button>
                            <button onClick={() => setEditingItemId(null)} className="text-gray-400 hover:text-gray-600 px-1" title="Скасувати">✕</button>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 justify-end group">
                            {new Intl.NumberFormat('uk-UA',{style:'currency',currency:'UAH'}).format(it.price)}
                            <button onClick={() => { setEditingItemId(it.id!); setItemPriceDraft(String(it.price ?? '')); }}
                              className="opacity-40 group-hover:opacity-100 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 text-xs transition-opacity"
                              title="Редагувати ціну позиції">✎</button>
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <button onClick={() => removeItem(it.id!)} disabled={itemBusy}
                          className="text-gray-400 hover:text-red-600 disabled:opacity-40" title="Прибрати позицію">✕</button>
                      </td>
                    </tr>
                  ))}
                  <tr className="border-t bg-gray-50/60 dark:bg-gray-700/30">
                    <td className="px-3 py-2" colSpan={2}>
                      <input value={addPnum} onChange={(e) => setAddPnum(e.target.value)} placeholder="№ товару (напр. Ф4046)"
                        className="w-full px-2 py-1 border rounded dark:bg-gray-800 dark:border-gray-600"
                        onKeyDown={(e) => { if (e.key === 'Enter') addItem(); }} />
                    </td>
                    <td className="px-3 py-2 text-right text-gray-400">+1</td>
                    <td className="px-3 py-2 text-right">
                      <input type="number" value={addPrice} onChange={(e) => setAddPrice(e.target.value)} placeholder="ціна"
                        className="w-24 px-2 py-1 text-right border rounded dark:bg-gray-800 dark:border-gray-600"
                        onKeyDown={(e) => { if (e.key === 'Enter') addItem(); }} />
                    </td>
                    <td className="px-3 py-2 text-center">
                      <button onClick={addItem} disabled={itemBusy || !addPnum.trim() || !addPrice}
                        className="text-green-600 hover:text-green-700 disabled:opacity-40 font-bold" title="Додати позицію">＋</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
      <ProductDetailsModal
        productId={cardProductId}
        open={cardProductId !== null}
        onClose={() => setCardProductId(null)}
      />
    </div>
  );
};

export default OrderDetailsModal;


