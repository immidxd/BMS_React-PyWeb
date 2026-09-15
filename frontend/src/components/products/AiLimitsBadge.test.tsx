import { describeLimits, type AiLimits } from './AiLimitsBadge';

const base = (over: Partial<AiLimits['free']> = {}, paid: Partial<AiLimits['paid']> = {}, month: Partial<AiLimits['month']> = {}): AiLimits => ({
  month: { spent_usd: 0.15, cap_usd: 20, remaining_usd: 19.85, allowed: true, reason: null, ...month },
  free: {
    used: 5, limit: null, observed_max: 21, exhausted: false, denials: 0,
    last_ok_at: null, last_denial_at: null,
    window_start: '2026-09-15T07:00:00+00:00', resets_at: '2026-09-16T07:00:00+00:00', retry_after_s: 0,
    ...over,
  },
  paid: { available: false, credits_depleted: false, last_at: null, calls_this_month: 0, spent_usd: 0, ...paid },
  now: '2026-09-15T10:00:00+00:00',
});
const now = new Date('2026-09-15T10:00:00+00:00');

test('межа невідома → нижня оцінка з тильдою, а не вигадане число', () => {
  const t = describeLimits(base(), now);
  expect(t.short).toBe('ШІ: 5 з ~21 сьогодні · $0.15 з $20');
  expect(t.tone).toBe('ok');
  expect(t.long).toContain('Google каже її лише у відмові');
});

test('межа з відмови показується як число', () => {
  expect(describeLimits(base({ limit: 20 }), now).short).toBe('ШІ: 5 з 20 сьогодні · $0.15 з $20');
  expect(describeLimits(base({ limit: 20, used: 16 }), now).tone).toBe('warn');
});

test('вичерпано → коли знову, і чи є платний вихід', () => {
  const t = describeLimits(base({ exhausted: true, used: 21, last_denial_at: '2026-09-15T10:28:00+00:00' }), now);
  expect(t.short).toMatch(/^ШІ: квоту вичерпано · знову завтра о \d\d:\d\d$/);
  expect(t.tone).toBe('off');
  const paid = describeLimits(base({ exhausted: true }, { available: true }), now);
  expect(paid.short).toContain('платний ключ готовий');
  const depleted = describeLimits(base({ exhausted: true }, { available: true, credits_depleted: true }), now);
  expect(depleted.short).not.toContain('платний ключ готовий');
  expect(depleted.long).toContain('кредити вичерпано');
});

test('скидання сьогодні — «о», не «завтра о»', () => {
  const t = describeLimits(base({ exhausted: true, resets_at: '2026-09-15T20:00:00+00:00' }), now);
  expect(t.short).toMatch(/знову о \d\d:\d\d/);
});

test('хвилинна пауза і місячна стеля мають перевагу над добовим лічильником', () => {
  expect(describeLimits(base({ retry_after_s: 31 }), now).short).toBe('ШІ: пауза 31 с (хвилинна межа)');
  expect(describeLimits(base({}, {}, { allowed: false }), now).short).toBe('ШІ: місячну стелю $20 вичерпано');
});
