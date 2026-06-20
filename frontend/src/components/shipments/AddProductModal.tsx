import React, { useEffect, useState } from 'react';
import { fetchShipments, type Shipment } from '../../services/referenceService';
import QuickAddProductForm from './QuickAddProductForm';
import CreateDeliveryModal from './CreateDeliveryModal';

interface Props {
  open: boolean;
  onClose: () => void;
  onAdded?: () => void;   // після успішного додавання товару (для рефрешу списку товарів)
}

const inputCls =
  'w-full rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 ' +
  'px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-gray-400';

const AddProductModal: React.FC<Props> = ({ open, onClose, onAdded }) => {
  const [deliveries, setDeliveries] = useState<Shipment[]>([]);
  const [deliveryId, setDeliveryId] = useState<number | ''>('');
  const [showCreate, setShowCreate] = useState(false);

  const loadDeliveries = () =>
    fetchShipments(undefined, 1, 100, 'shipment_date', 'desc')
      .then(r => setDeliveries(r.items || []))
      .catch(() => {});

  useEffect(() => {
    if (!open) return;
    setDeliveryId('');
    loadDeliveries();
  }, [open]);

  if (!open) return null;

  const selected = deliveries.find(d => d.id === deliveryId);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" onMouseDown={onClose}>
      <div className="relative bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-4xl" onMouseDown={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-gray-100 dark:border-gray-800">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Додати товар</h2>
          <button onClick={onClose} aria-label="Закрити" className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-2xl leading-none">×</button>
        </div>

        <div className="px-6 py-5 space-y-4">
          {/* Крок 1 — у який завіз */}
          <div>
            <span className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">У який завіз</span>
            <div className="flex gap-2">
              <select
                value={deliveryId}
                onChange={e => setDeliveryId(e.target.value ? Number(e.target.value) : '')}
                className={inputCls}
              >
                <option value="">— Оберіть завіз —</option>
                {deliveries.map(d => (
                  <option key={d.id} value={d.id}>{d.sheet_name || `Завіз #${d.id}`}</option>
                ))}
              </select>
              <button
                onClick={() => setShowCreate(true)}
                className="whitespace-nowrap px-3 py-2 rounded-lg text-sm font-medium border border-gray-300 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                ＋ Створити
              </button>
            </div>
          </div>

          {/* Крок 2 — форма товару (коли завіз обрано) */}
          {selected ? (
            <div className="pt-2 border-t border-gray-100 dark:border-gray-800">
              <QuickAddProductForm deliveryId={selected.id} onSaved={() => onAdded && onAdded()} />
            </div>
          ) : (
            <p className="text-sm text-gray-400 pt-2">Спершу оберіть або створіть завіз, щоб додати товар.</p>
          )}
        </div>
      </div>

      <CreateDeliveryModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={async (d) => { await loadDeliveries(); setDeliveryId(d.id); }}
      />
    </div>
  );
};

export default AddProductModal;
