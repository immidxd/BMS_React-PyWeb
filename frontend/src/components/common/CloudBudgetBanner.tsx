import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { confirmDialog, notify } from '../../ui/feedback';

/**
 * Сповіщення про бюджет Neon (хмарна БД каталогу) — ЖОРСТКЕ ПРАВИЛО, CLAUDE.md.
 * Читає ЛОКАЛЬНИЙ /api/cloud-budget/status (кеш бекенду, хмару не будить) раз на 10 хв.
 *   warn (≥60%)    — жовтий, можна сховати до кінця сесії;
 *   economy (≥75%) — помаранчевий: синк каталогу рідше;
 *   stop (≥90%)    — червоний, не ховається: хмарну БД ВИМКНЕНО (каталог недоступний),
 *                    поки власник не натисне «Дозволити ще $1» (або до нового місяця);
 *   unknown        — сіра підказка налаштувати лічильник (ховається на 7 днів).
 * При підвищенні рівня — додатковий тост.
 */
interface BudgetStatus {
  configured?: boolean;
  plan?: 'launch' | 'free';
  level?: 'ok' | 'warn' | 'economy' | 'stop' | 'unknown';
  used_cu_hours?: number;
  free_cu_hours?: number;
  cost_usd?: number;
  budget_usd?: number;
  projected_usd?: number;
  percent?: number;
  projected_cu_hours?: number;
  period_end?: string;
  capped?: boolean;
  error?: string;
}

const RANK: Record<string, number> = { unknown: 0, ok: 0, warn: 1, economy: 2, stop: 3 };
const LAST_LEVEL_KEY = 'bms-neon-last-level';
const HIDE_KEY = 'bms-neon-banner-hidden';      // sessionStorage: рівень, який сховали
const HIDE_UNKNOWN_KEY = 'bms-neon-unknown-hidden-until';
const PERMIT_STEP_USD = 1;

const STYLE: Record<string, { bg: string; title: string }> = {
  warn: { bg: '#b7791f', title: 'Neon: витрачено понад 60% бюджету' },
  economy: { bg: '#c05621', title: 'Neon: понад 75% бюджету — синк каталогу сповільнено' },
  stop: { bg: '#c53030', title: 'Neon: бюджет вичерпано' },
  unknown: { bg: '#4a5568', title: 'Лічильник Neon не налаштовано' },
};

const safe = <T,>(fn: () => T, fallback: T): T => { try { return fn(); } catch { return fallback; } };

const usage = (s: BudgetStatus): string => (s.plan === 'launch' && s.budget_usd != null
  ? `$${s.cost_usd?.toFixed(2)} з $${s.budget_usd.toFixed(2)} (${s.percent}%)${s.projected_usd != null ? `, прогноз на місяць ≈ $${s.projected_usd.toFixed(2)}` : ''}`
  : `${s.used_cu_hours} з ${s.free_cu_hours} CU-год (${s.percent}%)${s.projected_cu_hours != null ? `, прогноз ≈ ${s.projected_cu_hours}` : ''}`);

const CloudBudgetBanner: React.FC = () => {
  const [st, setSt] = useState<BudgetStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [hidden, setHidden] = useState<string | null>(() => safe(() => sessionStorage.getItem(HIDE_KEY), null));
  const first = useRef(true);

  const apply = useCallback((data: BudgetStatus) => {
    setSt(data);
    const lv = data.level || 'unknown';
    const prev = safe(() => localStorage.getItem(LAST_LEVEL_KEY), null) || 'ok';
    if ((RANK[lv] ?? 0) > (RANK[prev] ?? 0) && !first.current) {
      notify.warning({ message: STYLE[lv]?.title || 'Neon', description: usage(data) });
    }
    first.current = false;
    safe(() => localStorage.setItem(LAST_LEVEL_KEY, lv), undefined);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      if (document.hidden) return;
      axios.get('/api/cloud-budget/status')
        .then((res) => { if (!cancelled && res?.data) apply(res.data as BudgetStatus); })
        .catch(() => { /* офлайн/старий бекенд — мовчимо */ });
    };
    load();
    const t = setInterval(load, 10 * 60 * 1000);
    document.addEventListener('visibilitychange', load);
    return () => { cancelled = true; clearInterval(t); document.removeEventListener('visibilitychange', load); };
  }, [apply]);

  const lv = st?.level;
  if (!st || !lv || lv === 'ok') return null;
  if (lv === 'unknown' && Number(safe(() => localStorage.getItem(HIDE_UNKNOWN_KEY), null) || 0) > Date.now()) return null;
  if (lv !== 'stop' && hidden === lv) return null;

  const close = () => {
    if (lv === 'unknown') safe(() => localStorage.setItem(HIDE_UNKNOWN_KEY, String(Date.now() + 7 * 86400000)), undefined);
    else safe(() => sessionStorage.setItem(HIDE_KEY, lv), undefined);
    setHidden(lv);
  };
  const permit = async () => {
    const ok = await confirmDialog({
      title: `Дозволити ще $${PERMIT_STEP_USD} на Neon?`,
      body: `Бюджет цього місяця зросте до $${((st.budget_usd ?? 3) + PERMIT_STEP_USD).toFixed(2)}.\nХмарну БД буде увімкнено, і каталог знову запрацює.`,
      okText: 'Дозволити', kind: 'warning',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await axios.post('/api/cloud-budget/permit', { extra_usd: PERMIT_STEP_USD });
      apply(res.data as BudgetStatus);
      notify.success('Дозвіл збережено — бюджет збільшено');
    } catch (e: any) {
      notify.error({ message: 'Не вдалося застосувати дозвіл', description: e?.response?.data?.detail || String(e) });
    } finally {
      setBusy(false);
    }
  };
  const end = st.period_end ? new Date(st.period_end).toLocaleDateString('uk-UA') : null;
  const btn: React.CSSProperties = { background: 'transparent', border: 'none', color: '#fff', cursor: 'pointer', fontSize: 16, lineHeight: 1, padding: 0 };

  return (
    <div
      role="status"
      style={{
        position: 'fixed', left: 12, bottom: 76, zIndex: 10000, maxWidth: 400,
        padding: '8px 12px', fontSize: 13, lineHeight: 1.4, borderRadius: 8,
        background: STYLE[lv].bg, color: '#fff', boxShadow: '0 4px 14px rgba(0,0,0,0.25)',
        display: 'flex', alignItems: 'flex-start', gap: 8,
      }}
    >
      <span style={{ flex: 1 }}>
        <b>{STYLE[lv].title}</b>
        <br />
        {lv === 'unknown'
          ? 'Задайте NEON_API_KEY і NEON_PROJECT_ID, щоб BMS стежив за витратами хмарної БД.'
          : <>{usage(st)}{end ? `; новий місяць ${end}` : ''}.</>}
        {lv === 'stop' && (
          <>
            <br />
            {st.capped
              ? 'Хмарну БД вимкнено — каталог недоступний, поки ви не дозволите більше.'
              : 'BMS більше не будить хмару автоматично.'}
            <br />
            <button
              onClick={permit}
              disabled={busy}
              style={{ marginTop: 6, padding: '3px 10px', borderRadius: 6, border: '1px solid #fff', background: 'rgba(255,255,255,0.15)', color: '#fff', cursor: busy ? 'wait' : 'pointer', fontSize: 12 }}
            >
              {busy ? 'Застосовую…' : `Дозволити ще $${PERMIT_STEP_USD}`}
            </button>
          </>
        )}
        {st.error ? <span style={{ opacity: 0.85 }}>{` (останнє оновлення не вдалось)`}</span> : null}
      </span>
      {lv !== 'stop' && <button onClick={close} aria-label="Сховати" style={btn}>×</button>}
    </div>
  );
};

export default CloudBudgetBanner;
