import React, { useEffect, useState } from 'react';
import axios from 'axios';

/**
 * Ненав'язливий індикатор «доступне оновлення» (Крок E1).
 * Читає /api/update-status один раз на старті. Показується ЛИШЕ коли в каналі
 * цієї машини є новіша версія. Поки що інформаційний — застосування (завантаження
 * + встановлення) робитиметься окремо (E2). Можна закрити (×), стан у sessionStorage.
 * Якщо ендпоінт вимкнений/недоступний — нічого не рендериться (поведінка як без нього).
 */
interface UpdateStatus {
  update_available: boolean;
  latest_version: string | null;
  current_version: string;
  notes: string | null;
}

const DISMISS_KEY = 'bms-update-dismissed';

const UpdateBanner: React.FC = () => {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [dismissed, setDismissed] = useState<boolean>(() => {
    try { return sessionStorage.getItem(DISMISS_KEY) === '1'; } catch { return false; }
  });

  useEffect(() => {
    let cancelled = false;
    axios.get('/api/update-status')
      .then((res) => { if (!cancelled && res?.data) setStatus(res.data as UpdateStatus); })
      .catch(() => { /* офлайн/вимкнено — тихо ігноруємо */ });
    return () => { cancelled = true; };
  }, []);

  if (dismissed || !status?.update_available) return null;

  const close = () => {
    setDismissed(true);
    try { sessionStorage.setItem(DISMISS_KEY, '1'); } catch { /* ignore */ }
  };

  return (
    <div
      style={{
        position: 'fixed',
        top: 8,
        right: 8,
        zIndex: 10000,
        maxWidth: 360,
        padding: '8px 12px',
        fontSize: 13,
        lineHeight: 1.4,
        borderRadius: 8,
        background: '#1f6feb',
        color: '#fff',
        boxShadow: '0 4px 14px rgba(0,0,0,0.25)',
        display: 'flex',
        alignItems: 'flex-start',
        gap: 8,
      }}
      role="status"
    >
      <span style={{ flex: 1 }}>
        Доступне оновлення <b>{status.latest_version}</b>
        {status.current_version ? ` (зараз ${status.current_version})` : ''}.
        {status.notes ? <span style={{ opacity: 0.9 }}>{` ${status.notes}`}</span> : null}
      </span>
      <button
        onClick={close}
        aria-label="Сховати"
        style={{
          background: 'transparent', border: 'none', color: '#fff',
          cursor: 'pointer', fontSize: 16, lineHeight: 1, padding: 0,
        }}
      >
        ×
      </button>
    </div>
  );
};

export default UpdateBanner;
