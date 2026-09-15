import React, { useCallback, useEffect, useState } from 'react';

// Ліміти ШІ одним рядком. Google залишок квоти не віддає — бекенд рахує його
// з власного обліку викликів (див. services/ai_quota.py), а межу дізнається
// лише з відмови 429. Тому «~» у тексті чесний: це нижня оцінка, не число.

export interface AiLimits {
  month: { spent_usd: number; cap_usd: number; remaining_usd: number; allowed: boolean; reason: string | null };
  free: {
    used: number; limit: number | null; observed_max: number; exhausted: boolean; denials: number;
    last_ok_at: string | null; last_denial_at: string | null;
    window_start: string; resets_at: string; retry_after_s: number;
  };
  paid: { available: boolean; credits_depleted: boolean; last_at: string | null; calls_this_month: number; spent_usd: number };
  now: string;
}

export const AI_LIMITS_CHANGED = 'bms:ai-limits-changed';
export const emitAiLimitsChanged = () => { try { window.dispatchEvent(new Event(AI_LIMITS_CHANGED)); } catch { /* SSR/тести */ } };

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit' });
const sameLocalDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

export interface LimitsText { short: string; long: string; tone: 'ok' | 'warn' | 'off' }

/** Чистий опис стану — окремо від React, щоб тестувати без DOM. */
export function describeLimits(l: AiLimits, now: Date = new Date()): LimitsText {
  const resets = new Date(l.free.resets_at);
  const when = `${sameLocalDay(resets, now) ? 'о' : 'завтра о'} ${hhmm(l.free.resets_at)}`;
  const limitText = l.free.limit != null ? String(l.free.limit)
    : (l.free.observed_max > 0 ? `~${l.free.observed_max}` : '?');
  const money = `$${l.month.spent_usd.toFixed(2)} з $${l.month.cap_usd.toFixed(0)} за місяць`;

  const lines: string[] = [];
  if (l.free.limit != null) lines.push(`Безкоштовна добова межа: ${l.free.limit} запитів (Google повідомив у відмові).`);
  else if (l.free.observed_max > 0) lines.push(`Безкоштовна добова межа невідома — Google каже її лише у відмові; найбільше за день проходило ${l.free.observed_max}.`);
  else lines.push('Безкоштовна добова межа невідома — Google каже її лише у відмові.');
  lines.push(`Сьогодні пройшло: ${l.free.used}. Квота скидається ${when} (північ за тихоокеанським часом).`);
  if (l.free.limit != null && l.free.used >= l.free.limit && !l.free.exhausted) {
    // Спостережено 15.09: після 20 відмов поодинокі запити знову проходять —
    // Google рахує день ковзним вікном, а не лічильником до півночі.
    lines.push('Межу вже досягнуто, але Google відпускає по одному запиту з паузами — наступний може пройти або отримати відмову.');
  }
  if (l.free.exhausted && l.free.last_denial_at) lines.push(`Остання відмова: ${hhmm(l.free.last_denial_at)}.`);
  lines.push(`Гроші: ${money}.`);
  if (l.paid.available) {
    lines.push(l.paid.credits_depleted
      ? 'Платний ключ: кредити вичерпано — поповнити на ai.studio/projects («BMS Paid»).'
      : `Платний ключ готовий${l.paid.calls_this_month ? ` (за місяць: ${l.paid.calls_this_month} викл., $${l.paid.spent_usd.toFixed(2)})` : ''}.`);
  } else {
    lines.push('Платний ключ не налаштовано (GEMINI_API_KEY_PAID).');
  }
  const long = lines.join('\n');

  if (!l.month.allowed) return { short: `ШІ: місячну стелю $${l.month.cap_usd.toFixed(0)} вичерпано`, long, tone: 'off' };
  if (l.free.retry_after_s > 0) return { short: `ШІ: пауза ${l.free.retry_after_s} с (хвилинна межа)`, long, tone: 'warn' };
  if (l.free.exhausted) {
    const paid = l.paid.available && !l.paid.credits_depleted ? ' · платний ключ готовий' : '';
    return { short: `ШІ: квоту вичерпано · знову ${when}${paid}`, long, tone: 'off' };
  }
  const nearCap = l.free.limit != null && l.free.used >= Math.ceil(l.free.limit * 0.8);
  return { short: `ШІ: ${l.free.used} з ${limitText} сьогодні · ${money.replace(' за місяць', '')}`, long, tone: nearCap ? 'warn' : 'ok' };
}

export function useAiLimits(): { limits: AiLimits | null; reload: () => void } {
  const [limits, setLimits] = useState<AiLimits | null>(null);
  const reload = useCallback(() => {
    fetch('/api/autofill/limits')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d && d.free) setLimits(d as AiLimits); })
      .catch(() => { /* бейдж — допоміжний; тиша краща за помилку */ });
  }, []);
  useEffect(() => {
    reload();
    // Картка після кожного запуску розпізнавання шле подію — бейдж на сторінці
    // списку (keep-alive, змонтована постійно) оновлюється без опитування.
    window.addEventListener(AI_LIMITS_CHANGED, reload);
    return () => window.removeEventListener(AI_LIMITS_CHANGED, reload);
  }, [reload]);
  return { limits, reload };
}

const TONE: Record<LimitsText['tone'], string> = {
  ok: 'text-gray-500 dark:text-gray-400',
  warn: 'text-amber-600 dark:text-amber-400',
  off: 'text-gray-400 dark:text-gray-500',
};

/** Вміст спливашки: перший рядок — стан, далі пояснення. Для місць, де
 *  окремий рядок бейджа займав би зайве місце (список товарів). */
export function AiLimitsTip({ prefix }: { prefix?: string }) {
  const { limits } = useAiLimits();
  if (!limits) return prefix ? <span>{prefix}</span> : null;
  const t = describeLimits(limits);
  return (
    <div className="whitespace-pre-line text-[12px] leading-snug max-w-[340px]">
      {prefix && <div className="mb-1">{prefix}</div>}
      <div className={`font-medium ${t.tone === 'off' ? 'opacity-80' : ''}`}>{t.short}</div>
      <div className="mt-1 opacity-80">{t.long}</div>
    </div>
  );
}

/** Тихий рядок стану. Клік — оновити. */
export default function AiLimitsBadge({ className = '' }: { className?: string }) {
  const { limits, reload } = useAiLimits();
  if (!limits) return null;
  const t = describeLimits(limits);
  return (
    <button
      type="button" onClick={reload} title={t.long}
      className={`inline-block text-[11px] whitespace-nowrap ${TONE[t.tone]} hover:underline decoration-dotted underline-offset-2 ${className}`}
    >{t.short}</button>
  );
}
