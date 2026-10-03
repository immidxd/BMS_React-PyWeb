import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { notify } from '../../ui/feedback';

/**
 * Сповіщення про безкоштовний ліміт Neon (хмарна БД каталогу) — ЖОРСТКЕ ПРАВИЛО, CLAUDE.md.
 * Читає ЛОКАЛЬНИЙ /api/cloud-budget/status (кеш бекенду, хмару не будить) раз на 10 хв.
 *   warn (≥60%)    — жовтий, можна сховати до кінця сесії;
 *   economy (≥75%) — помаранчевий: синк каталогу рідше;
 *   stop (≥90%)    — червоний, не ховається: BMS більше не будить хмару автоматично;
 *   unknown        — сіра підказка налаштувати лічильник (ховається на 7 днів).
 * При підвищенні рівня — додатковий тост.
 */
interface BudgetStatus {
  configured?: boolean;
  level?: 'ok' | 'warn' | 'economy' | 'stop' | 'unknown';
  used_cu_hours?: number;
  free_cu_hours?: number;
  percent?: number;
  projected_cu_hours?: number;
  period_end?: string;
  error?: string;
}

const RANK: Record<string, number> = { unknown: 0, ok: 0, warn: 1, economy: 2, stop: 3 };
const LAST_LEVEL_KEY = 'bms-neon-last-level';
const HIDE_KEY = 'bms-neon-banner-hidden';      // sessionStorage: рівень, який сховали
const HIDE_UNKNOWN_KEY = 'bms-neon-unknown-hidden-until';

const STYLE: Record<string, { bg: string; title: string }> = {
  warn: { bg: '#b7791f', title: 'Neon: витрачено понад 60% безкоштовного ліміту' },
  economy: { bg: '#c05621', title: 'Neon: понад 75% ліміту — синк каталогу сповільнено' },
  stop: { bg: '#c53030', title: 'Neon: ліміт майже вичерпано — хмару більше не будимо' },
  unknown: { bg: '#4a5568', title: 'Лічильник Neon не налаштовано' },
};

const safe = <T,>(fn: () => T, fallback: T): T => { try { return fn(); } catch { return fallback; } };

const CloudBudgetBanner: React.FC = () => {
  const [st, setSt] = useState<BudgetStatus | null>(null);
  const [hidden, setHidden] = useState<string | null>(() => safe(() => sessionStorage.getItem(HIDE_KEY), null));
  const first = useRef(true);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      if (document.hidden) return;
      axios.get('/api/cloud-budget/status')
        .then((res) => {
          if (cancelled || !res?.data) return;
          const data = res.data as BudgetStatus;
          setSt(data);
          const lv = data.level || 'unknown';
          const prev = safe(() => localStorage.getItem(LAST_LEVEL_KEY), null) || 'ok';
          if ((RANK[lv] ?? 0) > (RANK[prev] ?? 0) && !first.current) {
            notify.warning({ message: STYLE[lv]?.title || 'Neon', description: `${data.percent ?? '?'}% з ${data.free_cu_hours ?? 100} CU-годин` });
          }
          first.current = false;
          safe(() => localStorage.setItem(LAST_LEVEL_KEY, lv), undefined);
        })
        .catch(() => { /* офлайн/старий бекенд — мовчимо */ });
    };
    load();
    const t = setInterval(load, 10 * 60 * 1000);
    document.addEventListener('visibilitychange', load);
    return () => { cancelled = true; clearInterval(t); document.removeEventListener('visibilitychange', load); };
  }, []);

  const lv = st?.level;
  if (!st || !lv || lv === 'ok') return null;
  if (lv === 'unknown' && Number(safe(() => localStorage.getItem(HIDE_UNKNOWN_KEY), null) || 0) > Date.now()) return null;
  if (lv !== 'stop' && hidden === lv) return null;

  const close = () => {
    if (lv === 'unknown') safe(() => localStorage.setItem(HIDE_UNKNOWN_KEY, String(Date.now() + 7 * 86400000)), undefined);
    else safe(() => sessionStorage.setItem(HIDE_KEY, lv), undefined);
    setHidden(lv);
  };
  const end = st.period_end ? new Date(st.period_end).toLocaleDateString('uk-UA') : null;

  return (
    <div
      role="status"
      style={{
        position: 'fixed', left: 12, bottom: 76, zIndex: 10000, maxWidth: 380,
        padding: '8px 12px', fontSize: 13, lineHeight: 1.4, borderRadius: 8,
        background: STYLE[lv].bg, color: '#fff', boxShadow: '0 4px 14px rgba(0,0,0,0.25)',
        display: 'flex', alignItems: 'flex-start', gap: 8,
      }}
    >
      <span style={{ flex: 1 }}>
        <b>{STYLE[lv].title}</b>
        <br />
        {lv === 'unknown'
          ? 'Задайте NEON_API_KEY і NEON_PROJECT_ID, щоб BMS стежив за безкоштовним лімітом хмарної БД.'
          : <>
              {st.used_cu_hours} з {st.free_cu_hours} CU-год ({st.percent}%)
              {st.projected_cu_hours != null ? `, прогноз на місяць ≈ ${st.projected_cu_hours}` : ''}
              {end ? `; оновлення ліміту ${end}` : ''}.
            </>}
        {st.error ? <span style={{ opacity: 0.85 }}>{` (останнє оновлення не вдалось)`}</span> : null}
      </span>
      {lv !== 'stop' && (
        <button
          onClick={close}
          aria-label="Сховати"
          style={{ background: 'transparent', border: 'none', color: '#fff', cursor: 'pointer', fontSize: 16, lineHeight: 1, padding: 0 }}
        >
          ×
        </button>
      )}
    </div>
  );
};

export default CloudBudgetBanner;
