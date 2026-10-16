/**
 * Слоти контент-плану каналу: «з такої дати, о таких годинах щодня».
 *
 * Спільне для пакетного діалогу й «Розкладу каналу», щоб «по днях» в обох
 * місцях рахувалось однаково. Слоти в минулому (і ближчі за 3 хв) пропускаються.
 */

export const DEFAULT_DAY_TIMES = '09:00, 12:00, 15:00, 18:00';
const MIN_LEAD_MS = 3 * 60_000;

/** «9, 12:30; 18.00» → ['09:00', '12:30', '18:00'] — відсортовано й без повторів. */
export function parseDayTimes(raw: string): string[] {
  const out = new Set<string>();
  for (const part of raw.split(/[,;\s]+/)) {
    const m = part.trim().match(/^(\d{1,2})(?:[:.](\d{2}))?$/);
    if (!m) continue;
    const h = Number(m[1]);
    const min = Number(m[2] ?? '0');
    if (h > 23 || min > 59) continue;
    out.add(`${String(h).padStart(2, '0')}:${String(min).padStart(2, '0')}`);
  }
  return Array.from(out).sort();
}

/** `count` слотів починаючи з дня `startDate` (YYYY-MM-DD, локальний час). */
export function buildDaySlots(startDate: string, times: string[], count: number, now = Date.now()): Date[] {
  const slots: Date[] = [];
  if (!times.length || count <= 0) return slots;
  const [y, mo, d] = startDate.split('-').map(Number);
  if (!y || !mo || !d) return slots;
  const day = new Date(y, mo - 1, d);
  for (let guard = 0; slots.length < count && guard < 3660; guard += 1) {
    for (const time of times) {
      const [h, m] = time.split(':').map(Number);
      const at = new Date(day.getFullYear(), day.getMonth(), day.getDate(), h, m, 0, 0);
      if (at.getTime() >= now + MIN_LEAD_MS) slots.push(at);
      if (slots.length >= count) break;
    }
    day.setDate(day.getDate() + 1);
  }
  return slots;
}

export function localDateInput(value: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${value.getFullYear()}-${p(value.getMonth() + 1)}-${p(value.getDate())}`;
}

export function localDateTimeInput(value: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${localDateInput(value)}T${p(value.getHours())}:${p(value.getMinutes())}`;
}

/** Завтра — типовий старт плану: сьогоднішні слоти часто вже минули. */
export function tomorrowInput(now = new Date()): string {
  const t = new Date(now);
  t.setDate(t.getDate() + 1);
  return localDateInput(t);
}

/** «17.10–22.10 · 6 днів» для підказки під налаштуваннями. */
export function describeSlots(slots: Date[]): string {
  if (!slots.length) return '';
  const fmt = (d: Date) => d.toLocaleDateString('uk-UA', { day: '2-digit', month: '2-digit' });
  const days = new Set(slots.map(s => s.toDateString())).size;
  const first = slots[0];
  const last = slots[slots.length - 1];
  const range = fmt(first) === fmt(last) ? fmt(first) : `${fmt(first)}–${fmt(last)}`;
  const noun = days % 10 === 1 && days % 100 !== 11 ? 'день'
    : [2, 3, 4].includes(days % 10) && ![12, 13, 14].includes(days % 100) ? 'дні' : 'днів';
  return `${range} · ${days} ${noun}`;
}
