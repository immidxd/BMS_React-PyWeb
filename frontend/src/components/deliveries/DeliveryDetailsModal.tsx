import React, { useEffect, useState } from 'react';

interface DeliveryDetails {
  id: number;
  deliveryname: string | null;
  description: string | null;
  created_at: string | null;
  deliverydate: string | null;
  supplier_id: number | null;
}

interface Props {
  deliveryId: number | null;
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

const DeliveryDetailsModal: React.FC<Props> = ({ deliveryId, open, onClose, onSaved }) => {
  const [loading, setLoading] = useState(false);
  const [delivery, setDelivery] = useState<DeliveryDetails | null>(null);
  const [saving, setSaving] = useState(false);
  const [edit, setEdit] = useState<Partial<DeliveryDetails>>({});

  useEffect(() => {
    if (!open || !deliveryId) return;
    setLoading(true);
    fetch(`/api/deliveries/${deliveryId}`)
      .then(r => r.json())
      .then((d) => { setDelivery(d); setEdit({ deliveryname: d.deliveryname, description: d.description, deliverydate: d.deliverydate, supplier_id: d.supplier_id }); })
      .finally(() => setLoading(false));
  }, [open, deliveryId]);

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
          <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Поставка {deliveryId}</h2>
          <div className="flex gap-2">
            <button disabled={saving} onClick={async () => {
              if (!deliveryId) return;
              setSaving(true);
              try {
                await fetch(`/api/deliveries/${deliveryId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(edit) });
                if (onSaved) onSaved();
              } finally { setSaving(false); }
            }} className="px-2 py-1 text-sm rounded border border-blue-500 text-blue-600 hover:bg-blue-50 disabled:opacity-60">Зберегти</button>
            <button onClick={onClose} className="px-2 py-1 text-sm rounded border border-gray-300 hover:bg-gray-100 dark:border-gray-700 dark:hover:bg-gray-700">Закрити</button>
          </div>
        </div>
        {loading ? (
          <div className="py-8 text-center text-gray-500">Завантаження...</div>
        ) : !delivery ? (
          <div className="py-8 text-center text-gray-500">Не знайдено</div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-3 items-center text-sm"><div className="text-gray-500">Назва</div><input className="col-span-2 border rounded px-2 py-1" value={edit.deliveryname ?? ''} onChange={(e) => setEdit({ ...edit, deliveryname: e.target.value || undefined })} /></div>
            <Row label="Дата створення" value={delivery.created_at || '—'} />
            <div className="grid grid-cols-3 gap-3 items-center text-sm"><div className="text-gray-500">Дата відправлення</div><input type="date" className="col-span-2 border rounded px-2 py-1" value={edit.deliverydate ?? ''} onChange={(e) => setEdit({ ...edit, deliverydate: e.target.value || undefined })} /></div>
            <div className="grid grid-cols-3 gap-3 items-center text-sm"><div className="text-gray-500">Постачальник (ID)</div><input type="number" className="col-span-2 border rounded px-2 py-1" value={edit.supplier_id ?? 0} onChange={(e) => setEdit({ ...edit, supplier_id: Number(e.target.value) })} /></div>
            <div className="grid grid-cols-3 gap-3 items-center text-sm"><div className="text-gray-500">Опис</div><input className="col-span-2 border rounded px-2 py-1" value={edit.description ?? ''} onChange={(e) => setEdit({ ...edit, description: e.target.value || undefined })} /></div>
          </div>
        )}
      </div>
    </div>
  );
};

export default DeliveryDetailsModal;


