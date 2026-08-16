import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  SyncOutlined, ThunderboltOutlined, SendOutlined,
  MinusCircleOutlined, CheckCircleOutlined, DisconnectOutlined,
} from '@ant-design/icons';
import MainLayout from '../layouts/MainLayout';
import TelegramBatchPublishDialog, { type TelegramBatchRequest } from '../components/products/TelegramBatchPublishDialog';
import ViberBatchPublishDialog, { type ViberBatchRequest } from '../components/products/ViberBatchPublishDialog';
import InstagramBatchDraftDialog, { type InstagramBatchRequest } from '../components/products/InstagramBatchDraftDialog';
import FacebookBatchDraftDialog, { type FacebookBatchRequest } from '../components/products/FacebookBatchDraftDialog';
import { confirmDialog, notify } from '../ui/feedback';
import LoadingSpinner from '../components/common/LoadingSpinner';
import { taskManager } from '../services/taskManager';

/* ── Типи ──────────────────────────────────────────────────────────── */

type Channel = 'telegram' | 'instagram' | 'viber' | 'facebook';

interface Slot {
  id: number;
  source_id: string;
  title: string;
  channel: Channel;
  post_format: string | null;
  rubric: string | null;
  product_count: number;
  scheduled_at: string | null;
  plan_status: string;
  slot_state: 'new' | 'suggested' | 'confirmed' | 'published' | 'skipped';
  product_numbers: string[];
  product_ids: number[];
  suggested_numbers: string[];
  suggested_ids: number[];
  post_url: string | null;
  published_at: string | null;
}

interface PlanStatus {
  connected: boolean;
  reason?: string;
  message?: string;
  last_import: string | null;
}

const CHANNEL_LABEL: Record<Channel, string> = {
  telegram: 'Telegram',
  instagram: 'Instagram',
  facebook: 'Facebook',
  viber: 'Viber',
};

const CHANNEL_COLOR: Record<Channel, string> = {
  telegram: '#229ED9',
  instagram: '#C13584',
  facebook: '#1877F2',
  viber: '#7360F2',
};

const STATE_LABEL: Record<Slot['slot_state'], string> = {
  new: 'Порожній',
  suggested: 'Є пропозиція',
  confirmed: 'Готовий',
  published: 'Опубліковано',
  skipped: 'Пропущено',
};

/* ── Сторінка ──────────────────────────────────────────────────────── */

const ContentPlanPage: React.FC = () => {
  const [slots, setSlots] = useState<Slot[]>([]);
  const [status, setStatus] = useState<PlanStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [busySlot, setBusySlot] = useState<number | null>(null);
  const [channelFilter, setChannelFilter] = useState<'all' | Channel>('all');
  const [publishDialog, setPublishDialog] = useState<{ slot: Slot; productIds: number[] } | null>(null);
  const [publishBusy, setPublishBusy] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/content-plan/status');
      if (res.ok) setStatus(await res.json());
    } catch { /* офлайн-стан покаже банер */ }
  }, []);

  const loadSlots = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/content-plan/slots?channel=${channelFilter}&days_back=7&days_ahead=45`);
      const data = await res.json().catch(() => ({ slots: [] }));
      setSlots(data.slots || []);
    } catch {
      notify.error('Не вдалося завантажити контент-план');
    } finally {
      setLoading(false);
    }
  }, [channelFilter]);

  useEffect(() => { loadSlots(); }, [loadSlots]);
  useEffect(() => { loadStatus(); }, [loadStatus]);

  /** Ручна синхронізація. Вебхуки штовхають зміни самі, але кнопка потрібна
   *  для випадку, коли Obsidian був закритий під час правок. */
  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await fetch('/api/content-plan/sync?days_back=7&days_ahead=45', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        notify.warning({ message: 'Obsidian недоступний', description: data.detail || 'Відкрийте Obsidian і увімкніть HTTP API TaskNotes' });
        return;
      }
      notify.success(`Синхронізовано: ${data.imported} нових, ${data.updated} оновлено`);
      await Promise.all([loadSlots(), loadStatus()]);
    } finally {
      setSyncing(false);
    }
  };

  const handleSuggest = async (slot: Slot) => {
    setBusySlot(slot.id);
    try {
      const res = await fetch(`/api/content-plan/slots/${slot.id}/suggest`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        notify.error(data.detail || 'Не вдалося підібрати товари');
        return;
      }
      if (data.shortfall > 0) {
        notify.warning(`Знайдено лише ${data.suggested.length} із ${data.requested} — решта без фото або вже опублікована`);
      }
      await loadSlots();
    } finally {
      setBusySlot(null);
    }
  };

  /** Прийняти пропозицію як остаточний склад слота. */
  const handleConfirm = async (slot: Slot) => {
    setBusySlot(slot.id);
    try {
      const res = await fetch(`/api/content-plan/slots/${slot.id}/products`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_ids: slot.suggested_ids }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        notify.error(data.detail || 'Не вдалося зафіксувати склад слота');
        return;
      }
      await loadSlots();
    } finally {
      setBusySlot(null);
    }
  };

  const handleSkip = async (slot: Slot) => {
    if (!(await confirmDialog(`Пропустити слот «${slot.title}»?`))) return;
    await fetch(`/api/content-plan/slots/${slot.id}/skip`, { method: 'POST' });
    await loadSlots();
  };

  const openPublish = async (slot: Slot) => {
    const ids = slot.product_ids.length ? slot.product_ids : slot.suggested_ids;
    if (!ids.length) {
      notify.warning('Спершу підберіть товари для слота');
      return;
    }
    if (slot.channel === 'instagram') {
      const ok = await confirmDialog({
        title: 'Публікація в Instagram',
        body: 'Опублікований пост неможливо видалити через API. Продовжити?',
        kind: 'warning',
      });
      if (!ok) return;
    }
    if (slot.channel === 'facebook') {
      const ok = await confirmDialog({
        title: 'Публікація у Facebook',
        body: 'Опублікований допис BMS не прибирає — видаляти доведеться вручну у Сторінці. Продовжити?',
        kind: 'warning',
      });
      if (!ok) return;
    }
    setPublishDialog({ slot, productIds: ids });
  };

  /** Позначити слот виконаним і дописати результат назад у нотатку Obsidian. */
  const markPublished = async (slot: Slot, productIds: number[]) => {
    const res = await fetch(`/api/content-plan/slots/${slot.id}/mark-published`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_numbers: slot.product_numbers }),
    });
    const data = await res.json().catch(() => ({}));
    if (data.message) notify.info(data.message);
    await loadSlots();
  };

  const publishEndpoint: Record<Channel, string> = {
    telegram: '/api/publications/telegram/create-posts-batch',
    viber: '/api/publications/viber/create-posts-batch',
    instagram: '/api/publications/instagram/create-posts-batch',
    facebook: '/api/publications/facebook/create-posts-batch',
  };

  const handleBatchPublish = (request: TelegramBatchRequest | ViberBatchRequest | InstagramBatchRequest | FacebookBatchRequest) => {
    if (!publishDialog) return;
    const { slot, productIds } = publishDialog;
    setPublishBusy(true);
    taskManager.run(
      `Контент-план → ${CHANNEL_LABEL[slot.channel]}: ${request.items.length} постів`,
      async () => {
        const response = await fetch(publishEndpoint[slot.channel], {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(request),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          throw new Error(data.detail || data.error || 'Публікацію не виконано');
        }
        return data;
      },
      {
        onSuccess: () => { markPublished(slot, productIds); },
      },
    ).finally(() => {
      setPublishBusy(false);
      setPublishDialog(null);
    });
  };

  /* ── Групування за днями ─────────────────────────────────────────── */

  const grouped = useMemo(() => {
    const byDay = new Map<string, Slot[]>();
    slots.forEach(slot => {
      const day = slot.scheduled_at ? slot.scheduled_at.slice(0, 10) : 'без дати';
      if (!byDay.has(day)) byDay.set(day, []);
      byDay.get(day)!.push(slot);
    });
    return Array.from(byDay.entries());
  }, [slots]);

  const formatDay = (day: string) => {
    if (day === 'без дати') return day;
    const date = new Date(`${day}T00:00:00`);
    const today = new Date().toISOString().slice(0, 10);
    const label = date.toLocaleDateString('uk-UA', { weekday: 'long', day: 'numeric', month: 'long' });
    return day === today ? `Сьогодні · ${label}` : label;
  };

  const formatTime = (iso: string | null) =>
    iso ? new Date(iso).toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit' }) : '—';

  /* ── Рендер ──────────────────────────────────────────────────────── */

  const filterPanel = (
    <div style={{ padding: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 12 }}>Майданчик</div>
      {(['all', 'telegram', 'instagram', 'facebook', 'viber'] as const).map(value => (
        <label key={value} style={{ display: 'block', marginBottom: 8, cursor: 'pointer' }}>
          <input
            type="radio"
            checked={channelFilter === value}
            onChange={() => setChannelFilter(value)}
            style={{ marginRight: 8 }}
          />
          {value === 'all' ? 'Усі' : CHANNEL_LABEL[value]}
        </label>
      ))}
    </div>
  );

  return (
    <MainLayout
      filterPanelContent={filterPanel}
      onRefresh={loadSlots}
      isRefreshing={loading}
      onResetFilters={() => setChannelFilter('all')}
    >
      <div style={{ padding: '16px 24px' }}>
        {/* Стан зв'язку з Obsidian */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20,
          padding: '10px 14px', borderRadius: 8,
          background: status?.connected ? 'rgba(34,197,94,0.10)' : 'rgba(234,179,8,0.10)',
          border: `1px solid ${status?.connected ? 'rgba(34,197,94,0.35)' : 'rgba(234,179,8,0.35)'}`,
        }}>
          {status?.connected
            ? <CheckCircleOutlined style={{ color: '#22c55e' }} />
            : <DisconnectOutlined style={{ color: '#eab308' }} />}
          <span style={{ flex: 1 }}>
            {status?.connected
              ? 'Obsidian на зв\'язку — зміни в TaskNotes надходять автоматично'
              : (status?.message || 'Obsidian недоступний — показано останній імпортований план')}
            {status?.last_import && (
              <span style={{ opacity: 0.65, marginLeft: 8 }}>
                · останній імпорт {new Date(status.last_import).toLocaleString('uk-UA')}
              </span>
            )}
          </span>
          <button onClick={handleSync} disabled={syncing} style={btnStyle}>
            <SyncOutlined spin={syncing} /> Синхронізувати
          </button>
        </div>

        {loading && <LoadingSpinner />}

        {!loading && slots.length === 0 && (
          <div style={{ opacity: 0.7, padding: 40, textAlign: 'center' }}>
            Слотів немає. Заплануйте публікації в Obsidian (тека «Контент-план») і натисніть «Синхронізувати».
          </div>
        )}

        {grouped.map(([day, daySlots]) => (
          <div key={day} style={{ marginBottom: 28 }}>
            <div style={{
              fontWeight: 600, fontSize: 15, marginBottom: 10,
              textTransform: 'capitalize', opacity: 0.85,
            }}>
              {formatDay(day)}
            </div>

            {daySlots.map(slot => {
              const proposed = slot.product_numbers.length ? slot.product_numbers : slot.suggested_numbers;
              const isConfirmed = slot.product_ids.length > 0;
              const isDone = slot.slot_state === 'published';
              return (
                <div key={slot.id} style={{
                  display: 'flex', alignItems: 'flex-start', gap: 14,
                  padding: '12px 16px', marginBottom: 8, borderRadius: 8,
                  border: '1px solid rgba(128,128,128,0.25)',
                  opacity: slot.slot_state === 'skipped' ? 0.5 : 1,
                }}>
                  <div style={{ minWidth: 52, fontWeight: 600 }}>{formatTime(slot.scheduled_at)}</div>

                  <div style={{
                    padding: '2px 10px', borderRadius: 12, fontSize: 12, fontWeight: 600,
                    color: '#fff', background: CHANNEL_COLOR[slot.channel], whiteSpace: 'nowrap',
                  }}>
                    {CHANNEL_LABEL[slot.channel]}
                    {slot.post_format && slot.post_format !== 'post' ? ` · ${slot.post_format}` : ''}
                  </div>

                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 500 }}>{slot.title}</div>
                    <div style={{ fontSize: 13, opacity: 0.7, marginTop: 4 }}>
                      {STATE_LABEL[slot.slot_state]}
                      {' · '}
                      {proposed.length
                        ? `${proposed.length}/${slot.product_count}: ${proposed.join(', ')}`
                        : `потрібно ${slot.product_count} товарів`}
                    </div>
                    {slot.post_url && (
                      <a href={slot.post_url} target="_blank" rel="noreferrer"
                         style={{ fontSize: 13 }}>Переглянути пост</a>
                    )}
                  </div>

                  {!isDone && slot.slot_state !== 'skipped' && (
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button onClick={() => handleSuggest(slot)} disabled={busySlot === slot.id} style={btnStyle}>
                        <ThunderboltOutlined /> Підібрати
                      </button>
                      {!isConfirmed && slot.suggested_ids.length > 0 && (
                        <button onClick={() => handleConfirm(slot)} disabled={busySlot === slot.id} style={btnStyle}>
                          <CheckCircleOutlined /> Прийняти
                        </button>
                      )}
                      <button onClick={() => openPublish(slot)} style={{ ...btnStyle, fontWeight: 600 }}>
                        <SendOutlined /> Опублікувати
                      </button>
                      <button onClick={() => handleSkip(slot)} style={btnStyle} title="Пропустити слот">
                        <MinusCircleOutlined />
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {publishDialog?.slot.channel === 'telegram' && (
        <TelegramBatchPublishDialog
          productIds={publishDialog.productIds}
          busy={publishBusy}
          onCancel={() => setPublishDialog(null)}
          onPublish={handleBatchPublish}
        />
      )}
      {publishDialog?.slot.channel === 'viber' && (
        <ViberBatchPublishDialog
          productIds={publishDialog.productIds}
          busy={publishBusy}
          onCancel={() => setPublishDialog(null)}
          onPublish={handleBatchPublish}
        />
      )}
      {publishDialog?.slot.channel === 'instagram' && (
        <InstagramBatchDraftDialog
          productIds={publishDialog.productIds}
          busy={publishBusy}
          onCancel={() => setPublishDialog(null)}
          onPublish={handleBatchPublish}
        />
      )}
      {publishDialog?.slot.channel === 'facebook' && (
        <FacebookBatchDraftDialog
          productIds={publishDialog.productIds}
          busy={publishBusy}
          onCancel={() => setPublishDialog(null)}
          onPublish={handleBatchPublish}
        />
      )}
    </MainLayout>
  );
};

const btnStyle: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '6px 12px', borderRadius: 6, cursor: 'pointer',
  border: '1px solid rgba(128,128,128,0.35)', background: 'transparent',
  color: 'inherit', fontSize: 13, whiteSpace: 'nowrap',
};

export default ContentPlanPage;
