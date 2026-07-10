import React, { useEffect, useState } from 'react';
import { updateSupplier, type Supplier } from '../../services/referenceService';
import LoadingSpinner from '../common/LoadingSpinner';

interface Props {
  supplierId: number | null;
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

const SupplierDetailsModal: React.FC<Props> = ({ supplierId, open, onClose, onSaved }) => {
  const [loading, setLoading] = useState(false);
  const [supplier, setSupplier] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [editName, setEditName] = useState('');
  const [editNotes, setEditNotes] = useState('');

  useEffect(() => {
    if (!open || !supplierId) return;
    setLoading(true);
    fetch(`/api/suppliers/${supplierId}`)
      .then(r => r.json())
      .then((s) => {
        setSupplier(s);
        setEditName(s.name || '');
        setEditNotes(s.notes || '');
      })
      .finally(() => setLoading(false));
  }, [open, supplierId]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-label="Закрити" />
      <div className="relative bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-lg mx-4 p-5">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Постачальник #{supplierId}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>
        {loading ? (
          <LoadingSpinner variant="modal" text="Завантаження постачальника…" />
        ) : !supplier ? (
          <div className="py-8 text-center text-gray-500">Не знайдено</div>
        ) : (
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-500 mb-1">Назва</label>
              <input
                className="w-full border rounded px-3 py-2 text-sm"
                value={editName}
                onChange={e => setEditName(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">Нотатки</label>
              <textarea
                className="w-full border rounded px-3 py-2 text-sm"
                rows={3}
                value={editNotes}
                onChange={e => setEditNotes(e.target.value)}
              />
            </div>
            <div className="text-sm text-gray-500">
              Товарів: <span className="font-bold text-gray-700">{supplier.product_count ?? 0}</span>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button onClick={onClose} className="px-3 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-100">Закрити</button>
              <button
                disabled={saving}
                onClick={async () => {
                  if (!supplierId) return;
                  setSaving(true);
                  try {
                    await updateSupplier(supplierId, { name: editName.trim(), notes: editNotes.trim() || undefined });
                    if (onSaved) onSaved();
                  } catch (e: any) {
                    alert(e?.response?.data?.detail || 'Помилка збереження');
                  } finally { setSaving(false); }
                }}
                className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-60"
              >
                {saving ? 'Збереження...' : 'Зберегти'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default SupplierDetailsModal;
