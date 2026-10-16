interface TelegramFailure {
  thread_title?: string;
  channel?: string;
  error?: string;
}

interface TelegramBatchItem {
  productnumber?: string;
  status?: string;
  error?: string;
  result?: { failed?: TelegramFailure[]; channel?: { queue_id?: number; scheduled_at?: string | null } | null };
}

interface TelegramBatchResult {
  status?: string;
  counts?: Record<string, number>;
  results?: TelegramBatchItem[];
  channel_queue?: { queued?: number; left_planned?: number; note?: string };
}

/** «Канал: 29 у плані (17 уже в розкладі Telegram)» — щоб було видно, що пости не загубились. */
export function formatChannelPlan(result: TelegramBatchResult): string {
  const planned = (result.results || []).filter(item => item.result?.channel?.queue_id).length;
  if (!planned) return '';
  const queued = Math.min(planned, result.channel_queue?.queued ?? 0);
  const tail = queued >= planned
    ? 'усі вже в розкладі Telegram'
    : `${queued} уже в розкладі Telegram, решту BMS поставить, щойно звільниться місце`;
  return `Канал: ${planned} заплановано — ${tail}. Стан — «Публікації → Розклад каналу»`;
}

function productLabel(value?: string): string {
  const clean = String(value || '').replace(/^#/, '');
  return clean ? `#${clean}` : 'товар без номера';
}

function affectedLabel(numbers: string[]): string {
  const visible = numbers.slice(0, 4).join(', ');
  const rest = numbers.length - 4;
  const lastTwo = numbers.length % 100;
  const last = numbers.length % 10;
  const noun = lastTwo >= 11 && lastTwo <= 14
    ? 'товарів'
    : last === 1
      ? 'товар'
      : last >= 2 && last <= 4
        ? 'товари'
        : 'товарів';
  return `${numbers.length} ${noun}: ${visible}${rest > 0 ? `, і ще ${rest}` : ''}`;
}

/** Компактний, але повний підсумок для Task Center без 25 повторів одного збою. */
export function formatTelegramBatchResult(result: TelegramBatchResult): string {
  const counts = result.counts || {};
  const summary = [
    counts.success ? `${counts.success} успішно` : '',
    counts.partial ? `${counts.partial} частково` : '',
    counts.error ? `${counts.error} з помилкою` : '',
    counts.skipped ? `${counts.skipped} не надсилали` : '',
  ].filter(Boolean).join(' · ');

  const grouped = new Map<string, string[]>();
  for (const item of result.results || []) {
    if (item.status === 'success') continue;
    const number = productLabel(item.productnumber);
    const failures = item.result?.failed || [];
    const problems = failures.length
      ? failures.map(failure => {
          const destination = failure.thread_title || failure.channel || 'Напрямок Telegram';
          return `${destination}: ${failure.error || 'не вдалося'}`;
        })
      : [item.error || item.status || 'не вдалося'];

    for (const problem of problems) {
      const numbers = grouped.get(problem) || [];
      if (!numbers.includes(number)) numbers.push(number);
      grouped.set(problem, numbers);
    }
  }

  const issues = Array.from(grouped, ([problem, numbers]) =>
    `${problem} (${affectedLabel(numbers)})`,
  );
  return [summary, ...issues, formatChannelPlan(result)].filter(Boolean).join(' — ');
}

/** Рядок про канал для одиночної публікації: коли вийде і чи зсунуто час. */
export function formatChannelNote(channel?: { scheduled_at?: string | null; sent?: boolean; shifted?: boolean } | null): string {
  if (!channel) return '';
  if (!channel.scheduled_at) return 'канал — переслано зараз';
  const when = new Date(channel.scheduled_at).toLocaleString('uk-UA', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  return `канал — ${when}${channel.shifted ? ' (зсунуто: ця хвилина вже зайнята іншим постом)' : ''}, під наглядом BMS`;
}
