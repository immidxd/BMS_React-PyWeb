import React, { useEffect, useState } from 'react';
import { type Supplier } from '../../services/referenceService';

interface Props {
  supplierId: number | null;
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

const SupplierDetailsModal: React.FC<Props> = ({ supplierId, open, onClose, onSaved }) => {
  const [loading, setLoading] = useState(false);
  const [supplier, setSupplier] = useState<Supplier | null>(null);
  const [saving, setSaving] = useState(false);
  const [edit, setEdit] = useState<Partial<Supplier>>({});

  useEffect(() => {
    if (!open || !supplierId) return;
    setLoading(true);
    fetch(`/api/suppliers/${supplierId}`)
      .then(r => r.json())
      .then((s) => { setSupplier(s); setEdit({
        company_name: s.company_name,
        contact_person: s.contact_person,
        city_location: s.city_location,
        status: s.status,
        priority: s.priority,
      }); })
      .finally(() => setLoading(false));
  }, [open, supplierId]);

  if (!open) return null;

  const Row: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) => (
    <div className="grid grid-cols-3 gap-3 text-sm">
      <div className="text-gray-500">{label}</div>
      <div className="col-span-2 font-medium break-words">{value ?? '—'}</div>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-label="Закрити" />
      <div className="relative bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-2xl mx-4 p-4">
        <div className="flex justify-between items-center mb-3">
          <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Постачальник {supplierId}</h2>
          <div className="flex gap-2">
            <button disabled={saving} onClick={async () => {
              if (!supplierId) return;
              setSaving(true);
              try {
                await fetch(`/api/suppliers/${supplierId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(edit) });
                if (onSaved) onSaved();
              } finally { setSaving(false); }
            }} className="px-2 py-1 text-sm rounded border border-blue-500 text-blue-600 hover:bg-blue-50 disabled:opacity-60">Зберегти</button>
            <button onClick={onClose} className="px-2 py-1 text-sm rounded border border-gray-300 hover:bg-gray-100 dark:border-gray-700 dark:hover:bg-gray-700">Закрити</button>
          </div>
        </div>
        {loading ? (
          <div className="py-8 text-center text-gray-500">Завантаження...</div>
        ) : !supplier ? (
          <div className="py-8 text-center text-gray-500">Не знайдено</div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-3 items-center text-sm"><div className="text-gray-500">Компанія</div><input className="col-span-2 border rounded px-2 py-1" value={edit.company_name ?? ''} onChange={(e) => setEdit({ ...edit, company_name: e.target.value || undefined })} /></div>
            <div className="grid grid-cols-3 gap-3 items-center text-sm"><div className="text-gray-500">Контакт</div><input className="col-span-2 border rounded px-2 py-1" value={edit.contact_person ?? ''} onChange={(e) => setEdit({ ...edit, contact_person: e.target.value || undefined })} /></div>
            <div className="grid grid-cols-3 gap-3 items-center text-sm"><div className="text-gray-500">Місто</div><input className="col-span-2 border rounded px-2 py-1" value={edit.city_location ?? ''} onChange={(e) => setEdit({ ...edit, city_location: e.target.value || undefined })} /></div>
            <div className="grid grid-cols-3 gap-3 items-center text-sm"><div className="text-gray-500">Статус</div><input className="col-span-2 border rounded px-2 py-1" value={edit.status ?? ''} onChange={(e) => setEdit({ ...edit, status: e.target.value || undefined })} /></div>
            <div className="grid grid-cols-3 gap-3 items-center text-sm"><div className="text-gray-500">Пріоритет</div><input type="number" className="col-span-2 border rounded px-2 py-1" value={edit.priority ?? 0} onChange={(e) => setEdit({ ...edit, priority: Number(e.target.value) })} /></div>
          </div>
        )}
      </div>
    </div>
  );
};

export default SupplierDetailsModal;


