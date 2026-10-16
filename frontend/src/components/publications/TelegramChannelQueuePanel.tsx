import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CheckCircleOutlined, ClockCircleOutlined, CloseOutlined, ReloadOutlined, SendOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useIsActivePage } from '../../contexts/ActivePageContext';
import { confirmDialog, notify } from '../../ui/feedback';
import ProductNumberText from '../common/ProductNumberText';
import {
  channelQueueApi, type ChannelQueueItem, type ChannelQueueResponse,
} from '../../services/telegramChannelQueue';
import {
  DEFAULT_DAY_TIMES, buildDaySlots, describeSlots, localDateTimeInput, parseDayTimes, tomorrowInput,
} from '../../utils/channelSlots';

/**
 * «Публікації → Розклад каналу»: що й коли вийде в канал BrandStore, що вийшло
 * і що потребує уваги.
 *
 * План живе в BMS; у розклад Telegram стоїть лише найближча частина (там 100
 * місць, кожне фото окремо). Після часу виходу BMS перевіряє канал і, якщо
 * Telegram пост не відправив, досилає сам — тому тут видно, ким доставлено.
 */

const REFRESH_MS = 60_000;
const INPUT = 'px-2.5 py-1.5 rounded-lg text-xs border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-gray-400/30';

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit' });
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('uk-UA', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const tomorrow = new Date(); tomorrow.setDate(today.getDate() + 1);
  const base = d.toLocaleDateString('uk-UA', { weekday: 'long', day: 'numeric', month: 'long' });
  if (d.toDateString() === today.toDateString()) return `Сьогодні · ${base}`;
  if (d.toDateString() === tomorrow.toDateString()) return `Завтра · ${base}`;
  return base.charAt(0).toUpperCase() + base.slice(1);
}

function title(item: ChannelQueueItem): string {
  return [item.brand, item.model].filter(Boolean).join(' ') || 'Товар';
}

const STATE_BADGE: Record<string, { label: string; className: string; hint: string }> = {
  queued: {
    label: 'У розкладі Telegram',
    className: 'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-900/20 dark:text-sky-300 dark:border-sky-800',
    hint: 'Вийде навіть якщо Mac спатиме; після виходу BMS перевірить канал.',
  },
  planned: {
    label: 'У плані BMS',
    className: 'bg-gray-50 text-gray-600 border-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:border-gray-700',
    hint: 'BMS поставить у розклад Telegram, щойно там звільниться місце (ліміт 100 повідомлень).',
  },
  cancelling: {
    label: 'Знімається',
    className: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-800',
    hint: 'BMS прибере пост із розкладу Telegram при наступній перевірці.',
  },
};

const SENT_VIA: Record<string, string> = {
  telegram: 'вийшов за розкладом',
  bms: 'Telegram не відправив — BMS дослав',
  manual: 'надіслано вручну',
};

const TelegramChannelQueuePanel: React.FC = () => {
  const isActivePage = useIsActivePage();
  const [data, setData] = useState<ChannelQueueResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedFailed, setSelectedFailed] = useState<Set<number>>(new Set());
  const [replanStart, setReplanStart] = useState(tomorrowInput());
  const [replanTimes, setReplanTimes] = useState(DEFAULT_DAY_TIMES);
  const [replanning, setReplanning] = useState(false);
  const requestRef = useRef(0);

  const load = useCallback(async (quiet = false) => {
    const requestId = ++requestRef.current;
    if (!quiet) setLoading(true);
    try {
      const next = await channelQueueApi.list();
      if (requestId !== requestRef.current) return;
      setData(next);
      setError(null);
    } catch (err: any) {
      if (requestId === requestRef.current) setError(err.message || 'Не вдалося завантажити розклад');
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Опитування — лише локальний бекенд, і лише коли сторінку видно.
  useEffect(() => {
    if (!isActivePage) return undefined;
    load(true);
    const timer = window.setInterval(() => {
      if (!document.hidden) load(true);
    }, REFRESH_MS);
    const onVisible = () => { if (!document.hidden) load(true); };
    const onChanged = () => load(true);
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('bms:telegram-status-refresh', onChanged);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('bms:telegram-status-refresh', onChanged);
    };
  }, [isActivePage, load]);

  const items = data?.items ?? [];
  const upcoming = useMemo(
    () => items.filter(i => ['planned', 'queued', 'cancelling'].includes(i.state))
      .sort((a, b) => (a.scheduled_at || '').localeCompare(b.scheduled_at || '')),
    [items],
  );
  const failed = useMemo(
    () => items.filter(i => i.state === 'failed' && i.available)
      .sort((a, b) => (a.scheduled_at || '').localeCompare(b.scheduled_at || '')),
    [items],
  );
  const history = useMemo(
    () => items.filter(i => i.state === 'sent' || i.state === 'cancelled' || (i.state === 'failed' && !i.available))
      .sort((a, b) => (b.sent_at || b.scheduled_at || '').localeCompare(a.sent_at || a.scheduled_at || '')),
    [items],
  );
  const groups = useMemo(() => {
    const map = new Map<string, ChannelQueueItem[]>();
    for (const item of upcoming) {
      const key = item.scheduled_at ? new Date(item.scheduled_at).toDateString() : '—';
      map.set(key, [...(map.get(key) || []), item]);
    }
    return Array.from(map.values());
  }, [upcoming]);

  const queuedCount = upcoming.filter(i => i.state === 'queued').length;
  const plannedCount = upcoming.filter(i => i.state === 'planned').length;
  const sentRecent = history.filter(i => i.state === 'sent');
  const rescued = sentRecent.filter(i => i.sent_via === 'bms').length;

  const replanSlots = useMemo(
    () => buildDaySlots(replanStart, parseDayTimes(replanTimes), selectedFailed.size),
    [replanStart, replanTimes, selectedFailed.size],
  );

  const act = async (id: number, action: () => Promise<unknown>, done: string) => {
    setBusyId(id);
    try {
      await action();
      notify.success({ message: done, duration: 4 });
      window.dispatchEvent(new CustomEvent('bms:telegram-status-refresh'));
    } catch (err: any) {
      notify.error({ message: err.message || 'Не вдалося', duration: 8 });
    } finally {
      setBusyId(null);
      load(true);
    }
  };

  const sync = async () => {
    setSyncing(true);
    try {
      await channelQueueApi.sync();
      notify.success({ message: 'Розклад каналу перевірено', duration: 4 });
    } catch (err: any) {
      notify.error({ message: err.message || 'Не вдалося перевірити', duration: 8 });
    } finally {
      setSyncing(false);
      load(true);
    }
  };

  const saveTime = (item: ChannelQueueItem) => {
    const at = new Date(editValue);
    if (Number.isNaN(at.getTime())) return;
    setEditingId(null);
    act(item.id, () => channelQueueApi.reschedule(item.id, at.toISOString()),
      `#${item.productnumber} — новий час ${fmtDateTime(at.toISOString())}`);
  };

  const replanSelected = async () => {
    const ids = failed.filter(i => selectedFailed.has(i.id)).map(i => i.id);
    if (!ids.length || replanSlots.length < ids.length) return;
    setReplanning(true);
    let ok = 0;
    const errors: string[] = [];
    for (let index = 0; index < ids.length; index += 1) {
      try {
        await channelQueueApi.reschedule(ids[index], replanSlots[index].toISOString(), true);
        ok += 1;
      } catch (err: any) {
        errors.push(err.message || 'помилка');
      }
    }
    try { await channelQueueApi.sync(); } catch { /* сторож доставить за 5 хв */ }
    setReplanning(false);
    setSelectedFailed(new Set());
    if (errors.length) notify.warning({ message: `Заплановано ${ok} з ${ids.length}`, description: errors.slice(0, 3).join('; '), duration: 10 });
    else notify.success({ message: `Заплановано знову: ${ok}`, description: describeSlots(replanSlots), duration: 6 });
    load(true);
  };

  const rowActions = (item: ChannelQueueItem) => (
    <div className="flex items-center gap-1 shrink-0">
      <button type="button" disabled={busyId === item.id}
              onClick={() => { setEditingId(item.id); setEditValue(item.scheduled_at ? localDateTimeInput(new Date(item.scheduled_at)) : ''); }}
              className="px-2 py-1 rounded-md text-[11px] text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800">
        Змінити час
      </button>
      <button type="button" disabled={busyId === item.id}
              onClick={async () => {
                if (!(await confirmDialog(`Надіслати #${item.productnumber} у канал просто зараз?`))) return;
                act(item.id, () => channelQueueApi.sendNow(item.id), `#${item.productnumber} надіслано в канал`);
              }}
              className="px-2 py-1 rounded-md text-[11px] text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800">
        <SendOutlined className="mr-1" />Зараз
      </button>
      {item.state !== 'failed' && (
        <button type="button" disabled={busyId === item.id}
                onClick={async () => {
                  if (!(await confirmDialog(`Скасувати пост #${item.productnumber} у канал? У форумі й гілках він лишиться.`))) return;
                  act(item.id, () => channelQueueApi.cancel(item.id), `#${item.productnumber} знято з розкладу`);
                }}
                className="p-1.5 rounded-md text-gray-400 hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20"
                aria-label={`Скасувати #${item.productnumber}`}>
          <CloseOutlined style={{ fontSize: 10 }} />
        </button>
      )}
    </div>
  );

  const timeEditor = (item: ChannelQueueItem) => editingId === item.id && (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <input type="datetime-local" value={editValue} onChange={e => setEditValue(e.target.value)}
             min={localDateTimeInput(new Date(Date.now() + 3 * 60_000))} className={INPUT} />
      <button type="button" onClick={() => saveTime(item)} className="bms-btn bms-btn-primary !py-1 !text-xs">Зберегти</button>
      <button type="button" onClick={() => setEditingId(null)} className="bms-btn bms-btn-secondary !py-1 !text-xs">Скасувати</button>
    </div>
  );

  if (loading && !data) {
    return <div className="py-16 text-center text-sm text-gray-400">Завантажую розклад каналу…</div>;
  }

  return (
    <div className="mt-4 space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Розклад каналу BrandStore</h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400 max-w-2xl">
            План живе в BMS. У розклад Telegram стає лише найближча частина (там 100 місць, кожне фото окремо),
            а після виходу BMS перевіряє канал і досилає те, що Telegram не відправив.
          </p>
        </div>
        <button type="button" onClick={sync} disabled={syncing} className="bms-btn bms-btn-secondary">
          <ReloadOutlined spin={syncing} /> {syncing ? 'Перевіряю…' : 'Перевірити зараз'}
        </button>
      </div>

      {error && (
        <div className="flex gap-2 px-3 py-2.5 rounded-xl text-xs text-rose-700 bg-rose-50 border border-rose-200 dark:text-rose-300 dark:bg-rose-900/20 dark:border-rose-800">
          <WarningOutlined className="mt-0.5" /> {error}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Заплановано" value={upcoming.length}
              hint={upcoming.length ? `у Telegram ${queuedCount} · чекають у BMS ${plannedCount}` : 'нічого не заплановано'} />
        <Stat label="Наступний пост" value={data?.next_at ? fmtDateTime(data.next_at) : '—'}
              hint={data?.last_at ? `останній — ${fmtDateTime(data.last_at)}` : ''} />
        <Stat label="Вийшло за 14 днів" value={sentRecent.length}
              hint={rescued ? `з них ${rescued} дослав BMS` : 'усі — за розкладом'} />
        <Stat label="Не вийшли" value={failed.length} tone={failed.length ? 'warn' : undefined}
              hint={failed.length ? 'потрібне рішення — нижче' : 'усе гаразд'} />
      </div>

      {failed.length > 0 && (
        <section className="rounded-2xl border border-amber-200 dark:border-amber-800 bg-amber-50/40 dark:bg-amber-900/10 overflow-hidden">
          <div className="px-4 py-3 flex flex-wrap items-center gap-2 border-b border-amber-200/70 dark:border-amber-800/60">
            <WarningOutlined className="text-amber-600" />
            <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">Не вийшли в канал — товар ще в продажу</span>
            <span className="text-xs text-gray-500">· {failed.length}</span>
            <button type="button" className="ml-auto text-xs text-gray-600 dark:text-gray-300 hover:underline"
                    onClick={() => setSelectedFailed(prev => prev.size === failed.length ? new Set() : new Set(failed.map(i => i.id)))}>
              {selectedFailed.size === failed.length ? 'Зняти всі' : 'Вибрати всі'}
            </button>
          </div>
          {selectedFailed.size > 0 && (
            <div className="px-4 py-3 flex flex-wrap items-end gap-3 bg-white/70 dark:bg-gray-900/40 border-b border-amber-200/70 dark:border-amber-800/60">
              <label className="text-xs text-gray-500">З дня
                <input type="date" value={replanStart} onChange={e => setReplanStart(e.target.value)} className={`${INPUT} mt-1 block`} />
              </label>
              <label className="text-xs text-gray-500">Щодня о
                <input value={replanTimes} onChange={e => setReplanTimes(e.target.value)} className={`${INPUT} mt-1 block w-56`} />
              </label>
              <div className="text-xs text-gray-500 pb-1.5">
                {replanSlots.length >= selectedFailed.size ? describeSlots(replanSlots) : 'Вкажи хоча б одну годину'}
              </div>
              <button type="button" onClick={replanSelected}
                      disabled={replanning || replanSlots.length < selectedFailed.size}
                      className="ml-auto bms-btn bms-btn-primary">
                <ClockCircleOutlined /> {replanning ? 'Планую…' : `Запланувати знову ${selectedFailed.size}`}
              </button>
            </div>
          )}
          <ul className="divide-y divide-amber-100 dark:divide-amber-900/40">
            {failed.map(item => (
              <li key={item.id} className="px-4 py-2.5">
                <div className="flex items-center gap-3">
                  <input type="checkbox" className="accent-gray-900" checked={selectedFailed.has(item.id)}
                         aria-label={`Вибрати #${item.productnumber}`}
                         onChange={e => setSelectedFailed(prev => {
                           const next = new Set(prev);
                           if (e.target.checked) next.add(item.id); else next.delete(item.id);
                           return next;
                         })} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-gray-800 dark:text-gray-100 truncate">
                      <ProductNumberText value={`#${item.productnumber}`} className="font-semibold" /> · {title(item)}
                      {item.price ? <span className="text-gray-400"> · {item.price} грн</span> : null}
                    </div>
                    <div className="text-[11px] text-amber-700 dark:text-amber-400 truncate">
                      мав вийти {fmtDateTime(item.original_scheduled_at || item.scheduled_at)} · {item.last_error || 'не вийшов'}
                    </div>
                  </div>
                  {rowActions(item)}
                </div>
                {timeEditor(item)}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-2xl border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="px-4 py-3 flex items-center gap-2 border-b border-gray-100 dark:border-gray-800">
          <ClockCircleOutlined className="text-gray-500" />
          <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">Заплановано</span>
          <span className="text-xs text-gray-400">· {upcoming.length}</span>
          {data && data.queued_photos > 0 && (
            <span className="ml-auto text-[11px] text-gray-400" title="Telegram тримає в розкладі до 100 повідомлень на канал, кожне фото — окремо">
              місць у розкладі Telegram: {data.queued_photos} / {data.queue_budget}
            </span>
          )}
        </div>
        {upcoming.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-gray-400">
            Нічого не заплановано. Пакетна публікація з «Товарів» або «Публікацій» розкладе пости по днях.
          </div>
        ) : groups.map(group => (
          <div key={group[0].scheduled_at || group[0].id}>
            <div className="px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400 bg-gray-50 dark:bg-gray-800/50">
              {group[0].scheduled_at ? dayLabel(group[0].scheduled_at) : '—'} · {group.length}
            </div>
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {group.map(item => {
                const badge = STATE_BADGE[item.state];
                return (
                  <li key={item.id} className="px-4 py-2.5">
                    <div className="flex items-center gap-3">
                      <span className="w-12 shrink-0 text-sm font-semibold tabular-nums text-gray-800 dark:text-gray-100">{fmtTime(item.scheduled_at)}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm text-gray-800 dark:text-gray-100 truncate">
                          <ProductNumberText value={`#${item.productnumber}`} className="font-semibold" /> · {title(item)}
                          {item.price ? <span className="text-gray-400"> · {item.price} грн</span> : null}
                          {item.silent ? <span className="text-gray-400"> · 🔕</span> : null}
                        </div>
                        {item.last_error && (
                          <div className="text-[11px] text-gray-500 truncate">{item.last_error}</div>
                        )}
                      </div>
                      {badge && (
                        <span title={badge.hint} className={`hidden md:inline-flex shrink-0 px-2 py-0.5 rounded-full border text-[10px] ${badge.className}`}>
                          {badge.label}
                        </span>
                      )}
                      {item.state !== 'cancelling' && rowActions(item)}
                    </div>
                    {timeEditor(item)}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </section>

      <section className="rounded-2xl border border-gray-200 dark:border-gray-700 overflow-hidden">
        <button type="button" onClick={() => setHistoryOpen(v => !v)}
                className="w-full px-4 py-3 flex items-center gap-2 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50">
          <CheckCircleOutlined className="text-gray-500" />
          <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">Історія за 14 днів</span>
          <span className="text-xs text-gray-400">· {history.length}</span>
          <span className="ml-auto text-gray-400">{historyOpen ? '−' : '+'}</span>
        </button>
        {historyOpen && (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800 border-t border-gray-100 dark:border-gray-800">
            {history.map(item => (
              <li key={item.id} className="px-4 py-2 flex items-center gap-3 text-sm">
                <span className="w-24 shrink-0 text-xs tabular-nums text-gray-500">{fmtDateTime(item.sent_at || item.scheduled_at)}</span>
                <span className="min-w-0 flex-1 truncate text-gray-700 dark:text-gray-200">
                  <ProductNumberText value={`#${item.productnumber}`} className="font-medium" /> · {title(item)}
                </span>
                <span className={`shrink-0 text-[11px] ${item.state === 'sent' ? (item.sent_via === 'bms' ? 'text-amber-600' : 'text-emerald-600') : 'text-gray-400'}`}>
                  {item.state === 'sent' ? (SENT_VIA[item.sent_via || 'telegram'] || 'вийшов') : (item.last_error || 'знято')}
                </span>
              </li>
            ))}
            {history.length === 0 && <li className="px-4 py-6 text-center text-xs text-gray-400">Порожньо</li>}
          </ul>
        )}
      </section>
    </div>
  );
};

const Stat: React.FC<{ label: string; value: React.ReactNode; hint?: string; tone?: 'warn' }> = ({ label, value, hint, tone }) => (
  <div className={`rounded-2xl border px-4 py-3 ${tone === 'warn' ? 'border-amber-200 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-900/10' : 'border-gray-200 dark:border-gray-700'}`}>
    <div className="text-[11px] uppercase tracking-wide text-gray-400">{label}</div>
    <div className="mt-1 text-lg font-semibold tabular-nums text-gray-900 dark:text-gray-50">{value}</div>
    {hint && <div className="mt-0.5 text-[11px] text-gray-500 truncate">{hint}</div>}
  </div>
);

export default TelegramChannelQueuePanel;
