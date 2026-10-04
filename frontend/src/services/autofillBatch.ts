/** Пакетне ШІ-розпізнавання: запуск, живий прогрес, скасування.
 *
 *  Сама робота живе на БЕКЕНДІ (фонова задача), а не в цьому вікні: один
 *  товар — до 90 секунд, тож завіз із двадцяти йде пів години. Закрита
 *  картка, перемкнута вкладка чи перезавантажена сторінка не мають його
 *  переривати — тому тут лише опитування стану.
 *
 *  Прогрес показує СПІЛЬНИЙ Task Center (`taskManager.setExternal`), а не
 *  окрема панель: своя система сповіщень поруч із наявною — це два місця,
 *  де треба шукати, що зараз відбувається.
 */
import { taskManager } from './taskManager';

export interface BatchJob {
  id: string;
  label: string;
  state: 'running' | 'waiting' | 'done' | 'cancelled' | 'error';
  total: number;
  done: number;
  proposed_products: number;
  proposed_fields: number;
  nothing: number;
  errors: number;
  skipped: number;
  current: string | null;
  stop_reason: string | null;
  cancel_requested?: boolean;
  /** Стікер на знімках із чужим номером — ціна/розмір із нього не взяті. */
  sticker_mismatch?: { product_id: number; number: string | null; sticker_number: string | null }[];
  results: { product_id: number; number: string | null; fields: number; ok: boolean; reason?: string; sticker_number?: string | null }[];
}

export interface StartResult {
  ok: boolean;
  job?: BatchJob;
  queued?: number;
  considered?: number;
  nothing_to_do?: boolean;
  reason?: string;
}

/** Один запис у Task Center на всі пакети: паралельних не буває (бекенд не
 *  дає), тож і рядків не має бути два. */
export const TASK_ID = 'autofill-batch';

const isFinal = (s: BatchJob['state']) => s === 'done' || s === 'cancelled' || s === 'error';

/** Людською мовою: що саме зараз відбувається. */
export function describe(job: BatchJob): string {
  if (isFinal(job.state)) {
    const parts: string[] = [];
    if (job.proposed_fields) parts.push(`знайдено ${job.proposed_fields} полів на ${job.proposed_products} товарах`);
    if (job.nothing) parts.push(`без знахідок: ${job.nothing}`);
    if (job.errors) parts.push(`не вдалося: ${job.errors}`);
    if (job.skipped) parts.push(`не дійшли черги: ${job.skipped}`);
    // Стікер із чужим номером — найчастіше фото не з тієї картки. Називаємо
    // обидва номери: людина має одразу бачити, куди дивитись.
    const mism = job.sticker_mismatch || [];
    if (mism.length) {
      parts.push(`стікер з іншим номером: ${mism
        .map((m) => `${m.number || '#' + m.product_id} (на стікері ${m.sticker_number || '?'})`)
        .join(', ')}`);
    }
    const tail = job.stop_reason ? ` — ${job.stop_reason}` : '';
    return (parts.join(' · ') || 'нічого не змінилось') + tail;
  }
  const head = `${job.done} з ${job.total}`;
  if (job.state === 'waiting') return `${head} · ${job.stop_reason || 'очікування'}`;
  return job.current ? `${head} · ${job.current}` : head;
}

function publish(job: BatchJob) {
  const status = job.state === 'waiting' ? 'waiting'
    : job.state === 'error' ? 'error'
    : !isFinal(job.state) ? 'running'
    : (job.errors > 0 || job.skipped > 0 || job.state === 'cancelled') ? 'partial'
    : 'success';
  const label = isFinal(job.state)
    ? (job.state === 'cancelled' ? `Розпізнавання скасовано: ${job.label}` : `Розпізнано: ${job.label}`)
    : `Розпізнавання ${job.label}`;
  taskManager.setExternal(TASK_ID, label, status, describe(job));
}

type Sub = (job: BatchJob) => void;
const subscribers = new Set<Sub>();
let pollTimer: number | undefined;
let lastJob: BatchJob | null = null;

/** Підписатись на прогрес (картка завозу оновлює свій список). */
export function subscribe(fn: Sub): () => void {
  subscribers.add(fn);
  if (lastJob) fn(lastJob);
  return () => { subscribers.delete(fn); };
}

function emit(job: BatchJob) {
  lastJob = job;
  publish(job);
  subscribers.forEach((fn) => { try { fn(job); } catch { /* підписник не валить поллер */ } });
}

async function fetchActive(): Promise<BatchJob | null> {
  const r = await fetch('/api/autofill/batch/active');
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const d = await r.json();
  return d.job || null;
}

/** Запустити опитування. Ідемпотентно: другий виклик нічого не дублює. */
export function watch(): void {
  if (pollTimer !== undefined) return;
  const tick = async () => {
    let delay = 15000;
    try {
      const job = await fetchActive();
      if (job) {
        emit(job);
        delay = 2000;
      } else if (lastJob && !isFinal(lastJob.state)) {
        // Задача зникла з «активних» — дочитуємо фінальний стан за id, щоб
        // підсумок не загубився разом із нею.
        const r = await fetch(`/api/autofill/batch/${lastJob.id}`);
        if (r.ok) emit(await r.json());
      }
    } catch {
      // Недоступність індикатора не створює фальшивої помилки задачі.
    } finally {
      pollTimer = window.setTimeout(tick, delay);
    }
  };
  void tick();
}

export async function start(body: {
  product_ids?: number[]; delivery_id?: number; label?: string;
  use_paid?: boolean; skip_with_proposals?: boolean;
}): Promise<StartResult> {
  const r = await fetch('/api/autofill/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`);
  if (d.job) emit(d.job);
  watch();
  return d;
}

export async function cancel(jobId: string): Promise<void> {
  await fetch(`/api/autofill/batch/${jobId}/cancel`, { method: 'POST' });
}

/** «Підтвердити все» над набором товарів — той самий шлях, що й у картці. */
export async function acceptForProducts(productIds: number[]): Promise<{ products: number; fields: number; errors: any[] }> {
  const r = await fetch('/api/proposals/accept-products', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product_ids: productIds }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`);
  return d;
}

/** Прийняти всі пропозиції ОДНОГО товару (кнопка в рядку таблиці). */
export async function acceptAllFor(productId: number): Promise<{ accepted: number }> {
  const r = await fetch(`/api/products/${productId}/proposals/accept-all`, { method: 'POST' });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`);
  return d;
}

export const currentJob = (): BatchJob | null => lastJob;

/** Спитати людину й запустити. Спільна для всіх трьох входів (картка завозу,
 *  меню «Дії» у «Товарах», рядок «Поставок») — інакше три діалоги розійшлися
 *  б у формулюваннях і в запобіжниках.
 *
 *  Діалог показує ЗАЛИШОК ДОБОВОЇ КВОТИ, бо це головне, що варто знати перед
 *  стартом: завіз на двадцять товарів може не влізти в денну межу, і краще
 *  дізнатись це до запуску, ніж на п'ятнадцятому товарі.
 */
export async function confirmAndStart(opts: {
  count: number;
  label: string;
  productIds?: number[];
  deliveryId?: number;
}): Promise<StartResult | null> {
  const { confirmDialog, notify } = await import('../ui/feedback');

  let quotaLine = '';
  try {
    const r = await fetch('/api/autofill/limits');
    if (r.ok) {
      const d = await r.json();
      const free = d?.free || {};
      const left = free.limit ? Math.max(0, free.limit - (free.used || 0)) : null;
      if (free.exhausted) {
        quotaLine = '\n\n⚠ Добова квота Google вже вичерпана — розпізнавання не почнеться.';
      } else if (left !== null) {
        quotaLine = `\n\nЗалишок добової квоти: приблизно ${left} запитів`
          + (left < opts.count ? ` — на всі ${opts.count} товарів її не вистачить, решта лишиться на завтра.` : '.');
      }
    }
  } catch { /* підказка про квоту не обов'язкова */ }

  const ok = await confirmDialog({
    title: `Розпізнати ${opts.count} ${plural(opts.count)}?`,
    body: 'ШІ перегляне живі знімки кожного товару й складе ПРОПОЗИЦІЇ — у картки нічого не запишеться, '
      + 'доки ви не підтвердите.\n\nТовари, у яких уже є нерозглянуті пропозиції, пропускаються.\n'
      + 'Один товар — до пів хвилини, тож пакет іде у фоні: вікно можна закрити.'
      + quotaLine,
    okText: 'Розпізнати', kind: 'confirm',
  });
  if (!ok) return null;

  try {
    const res = await start({
      product_ids: opts.productIds, delivery_id: opts.deliveryId, label: opts.label,
    });
    if (res.nothing_to_do) {
      notify.info({ message: 'Нема чого розпізнавати', description: res.reason, duration: 7 });
    } else {
      notify.success({
        message: `Розпізнавання почалось (${res.queued} із ${res.considered})`,
        description: 'Прогрес — у «Фонових процесах» унизу праворуч. Вікно можна закрити.',
        duration: 6,
      });
    }
    return res;
  } catch (e: any) {
    notify.error({ message: 'Не вдалося запустити', description: String(e?.message || e), duration: 9 });
    return null;
  }
}

function plural(n: number): string {
  const d = n % 10, h = n % 100;
  if (d === 1 && h !== 11) return 'товар';
  if (d >= 2 && d <= 4 && (h < 12 || h > 14)) return 'товари';
  return 'товарів';
}
