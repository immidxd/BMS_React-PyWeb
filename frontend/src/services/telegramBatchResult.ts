interface TelegramFailure {
  thread_title?: string;
  channel?: string;
  error?: string;
}

interface TelegramBatchItem {
  productnumber?: string;
  status?: string;
  error?: string;
  result?: { failed?: TelegramFailure[] };
}

interface TelegramBatchResult {
  status?: string;
  counts?: Record<string, number>;
  results?: TelegramBatchItem[];
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
  return [summary, ...issues].filter(Boolean).join(' — ');
}
