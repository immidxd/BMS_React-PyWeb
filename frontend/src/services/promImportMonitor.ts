export interface PromImportProgress {
  ok: boolean;
  done: boolean;
  retryable?: boolean;
  status?: string;
  source?: 'import_status' | 'product_list';
  import_id?: string | null;
  expected?: number;
  found?: number;
  presence?: Record<string, number>;
  details?: Record<string, unknown>;
  error?: string | null;
}

interface WaitForPromImportOptions {
  importId?: string | number | null;
  skus?: string[];
  timeoutMs?: number;
}

const INITIAL_DELAY_MS = 5_000;
const POLL_INTERVAL_MS = 12_000;
const DEFAULT_TIMEOUT_MS = 30 * 60_000;

const delay = (ms: number) => new Promise<void>(resolve => window.setTimeout(resolve, ms));

function taskError(detail: string): Error {
  const error: any = new Error(detail);
  error.response = { data: { detail } };
  return error;
}

async function readJson(response: Response): Promise<any> {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function terminalError(progress: PromImportProgress): Error {
  const status = String(progress.status || '').toUpperCase();
  const details = progress.details || {};
  const apiMessage = typeof details.message === 'string' ? details.message : '';
  if (status === 'PARTIAL') {
    return taskError(apiMessage || 'Prom завершив імпорт частково. Перевір помилки імпорту в кабінеті Prom.');
  }
  if (status === 'FATAL') {
    return taskError(apiMessage || 'Prom завершив імпорт із помилкою. Перевір журнал імпорту в кабінеті Prom.');
  }
  return taskError(progress.error || `Prom завершив імпорт зі статусом ${status || 'ERROR'}.`);
}

/**
 * Waits for a terminal Prom signal only. This monitor is deliberately read-only:
 * it never submits, retries, cancels, or edits an import.
 */
export async function waitForPromImport({
  importId,
  skus = [],
  timeoutMs = DEFAULT_TIMEOUT_MS,
}: WaitForPromImportOptions): Promise<PromImportProgress> {
  const uniqueSkus = Array.from(new Set(skus.map(String).map(s => s.trim()).filter(Boolean)));
  if ((importId === null || importId === undefined || importId === '') && uniqueSkus.length === 0) {
    throw taskError('Prom не повернув даних для відстеження імпорту.');
  }

  const deadline = Date.now() + timeoutMs;
  let lastProblem = '';
  await delay(Math.min(INITIAL_DELAY_MS, timeoutMs));

  while (Date.now() < deadline) {
    try {
      const response = await fetch('/api/publications/prom/import-progress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ import_id: importId ?? null, skus: uniqueSkus }),
      });
      const progress: PromImportProgress & { detail?: string } = await readJson(response);

      if (!response.ok) {
        const detail = progress.detail || progress.error || `HTTP ${response.status}`;
        if (response.status >= 400 && response.status < 500) throw taskError(detail);
        lastProblem = detail;
      } else if (progress.done) {
        if (String(progress.status || '').toUpperCase() === 'SUCCESS') return progress;
        throw terminalError(progress);
      } else if (!progress.ok && progress.retryable === false) {
        throw taskError(progress.error || 'Не вдалося перевірити статус імпорту Prom.');
      } else if (progress.error) {
        lastProblem = progress.error;
      }
    } catch (error: any) {
      if (error?.response) throw error;
      lastProblem = error?.message || 'тимчасовий збій зв’язку';
    }

    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await delay(Math.min(POLL_INTERVAL_MS, remaining));
  }

  const suffix = lastProblem ? ` Остання відповідь: ${lastProblem}` : '';
  throw taskError(`Prom не підтвердив завершення імпорту за 30 хвилин.${suffix}`);
}
