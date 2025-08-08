import React, { useEffect, useState } from 'react';
import { fetchClient, type Client } from '../../services/referenceService';

interface Props {
  clientId: number | null;
  open: boolean;
  onClose: () => void;
}

const ClientDetailsModal: React.FC<Props> = ({ clientId, open, onClose }) => {
  const [loading, setLoading] = useState(false);
  const [client, setClient] = useState<Client | null>(null);

  useEffect(() => {
    if (!open || !clientId) return;
    setLoading(true);
    fetchClient(clientId)
      .then(setClient)
      .finally(() => setLoading(false));
  }, [open, clientId]);

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
          <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Клієнт {clientId}</h2>
          <button onClick={onClose} className="px-2 py-1 text-sm rounded border border-gray-300 hover:bg-gray-100 dark:border-gray-700 dark:hover:bg-gray-700">Закрити</button>
        </div>
        {loading ? (
          <div className="py-8 text-center text-gray-500">Завантаження...</div>
        ) : !client ? (
          <div className="py-8 text-center text-gray-500">Не знайдено</div>
        ) : (
          <div className="space-y-3">
            <Row label="ПІБ" value={client.full_name} />
            <Row label="Телефон" value={client.phone_number || '—'} />
            <Row label="Email" value={client.email || '—'} />
            <Row label="Адреса" value={(client as any).address || '—'} />
            <Row label="Нотатки" value={client.notes || '—'} />
          </div>
        )}
      </div>
    </div>
  );
};

export default ClientDetailsModal;


