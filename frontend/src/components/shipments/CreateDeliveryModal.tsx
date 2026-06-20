import React, { useEffect, useMemo, useState } from 'react';
import { createDelivery, fetchSuppliers, type Supplier } from '../../services/referenceService';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (d: { id: number; deliveryname: string }) => void;
}

const todayISO = () => new Date().toISOString().slice(0, 10);
const fmtDM = (iso: string) => {
  const [y, m, d] = iso.split('-');
  return y && m && d ? `${d}.${m}.${y}` : iso;
};

const inputCls =
  'w-full rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 ' +
  'px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-gray-400';
const labelCls = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1';

const CreateDeliveryModal: React.FC<Props> = ({ open, onClose, onCreated }) => {
  const [date, setDate] = useState(todayISO());
  const [supplier, setSupplier] = useState('');
  const [purchase, setPurchase] = useState('');
  const [delivery, setDelivery] = useState('');
  const [nameOverride, setNameOverride] = useState('');
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setDate(todayISO());
    setSupplier(''); setPurchase(''); setDelivery(''); setNameOverride(''); setError(null);
    fetchSuppliers(undefined, 1, 500).then(r => setSuppliers(r.items || [])).catch(() => {});
  }, [open]);

  const previewName = useMemo(() => {
    if (nameOverride.trim()) return nameOverride.trim();
    const d = fmtDM(date);
    return supplier.trim() ? `${d}(${supplier.trim()})` : d;
  }, [date, supplier, nameOverride]);

  if (!open) return null;

  const submit = async () => {
    setError(null);
    if (!date) { setError('Вкажіть дату завозу'); return; }
    setSubmitting(true);
    try {
      const res = await createDelivery({
        deliverydate: date,
        supplier_name: supplier.trim() || undefined,
        purchase_cost: purchase ? Number(purchase) : 0,
        delivery_cost: delivery ? Number(delivery) : 0,
        name_override: nameOverride.trim() || undefined,
      });
      onCreated({ id: res.id, deliveryname: res.deliveryname });
      onClose();
    } catch (e: any) {
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail;
      if (status === 403) setError('Створення вимкнено на бекенді (PARSER_ADD_PRODUCT=0)');
      else if (status === 409) setError(detail || 'Такий завіз уже існує');
      else setError(detail || 'Не вдалося створити завіз');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onMouseDown={onClose}
    >
      <div
        className="relative bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-md"
        onMouseDown={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-gray-100 dark:border-gray-800">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Новий завіз</h2>
          <button
            onClick={onClose}
            aria-label="Закрити"
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-2xl leading-none"
          >×</button>
        </div>

        <div className="px-6 py-5 space-y-4">
          <label className="block">
            <span className={labelCls}>Дата завозу</span>
            <input type="date" value={date} onChange={e => setDate(e.target.value)} className={inputCls} />
          </label>

          <label className="block">
            <span className={labelCls}>Постачальник</span>
            <input
              list="create-delivery-suppliers"
              value={supplier}
              onChange={e => setSupplier(e.target.value)}
              placeholder="Оберіть або введіть нового"
              className={inputCls}
            />
            <datalist id="create-delivery-suppliers">
              {suppliers.map(s => <option key={s.id} value={s.name} />)}
            </datalist>
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className={labelCls}>Сума (закупівля)</span>
              <input type="number" min="0" step="0.01" value={purchase}
                onChange={e => setPurchase(e.target.value)} placeholder="0" className={inputCls} />
            </label>
            <label className="block">
              <span className={labelCls}>Сума доставки</span>
              <input type="number" min="0" step="0.01" value={delivery}
                onChange={e => setDelivery(e.target.value)} placeholder="0" className={inputCls} />
            </label>
          </div>

          <label className="block">
            <span className={labelCls}>
              Назва вкладки <span className="text-gray-400 font-normal">(необов'язково)</span>
            </span>
            <input value={nameOverride} onChange={e => setNameOverride(e.target.value)}
              placeholder={previewName} className={inputCls} />
            <span className="block text-xs text-gray-400 mt-1">
              Вкладка у журналі: <b className="text-gray-600 dark:text-gray-300">{previewName}</b>
            </span>
          </label>

          {error && (
            <div className="text-sm text-red-600 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">{error}</div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-6 py-4 border-t border-gray-100 dark:border-gray-800">
          <button onClick={onClose} disabled={submitting}
            className="px-4 py-2 rounded-lg text-sm font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50">
            Скасувати
          </button>
          <button onClick={submit} disabled={submitting}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-black text-white hover:bg-gray-800 disabled:opacity-50">
            {submitting ? 'Створення…' : 'Створити завіз'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default CreateDeliveryModal;
