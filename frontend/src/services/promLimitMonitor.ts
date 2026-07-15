import { notify } from '../ui/feedback';

export interface PromLimitStatus {
  limit_active?: boolean;
  limit_retry_at?: string | null;
  limit_warning?: string | null;
}

const STATUS_URL = '/api/publications/prom/import-limit';
const NETWORK_RETRY_MS = 60_000;

let timer: number | null = null;
let watchedRetryAt = 0;

function clearTimer() {
  if (timer !== null) window.clearTimeout(timer);
  timer = null;
  watchedRetryAt = 0;
}

async function loadStatus(): Promise<PromLimitStatus | null> {
  const response = await fetch(STATUS_URL);
  if (!response.ok) return null;
  return response.json();
}

async function verifyEstimatedReset() {
  timer = null;
  try {
    const status = await loadStatus();
    if (!status) throw new Error('status unavailable');
    if (status.limit_active && status.limit_retry_at) {
      watchPromLimitStatus(status);
      return;
    }
    watchedRetryAt = 0;
    notify.info({
      message: 'Prom: час очікування минув',
      description: 'Орієнтовний двогодинний інтервал завершився. Можна повторити публікацію; остаточне рішення приймає Prom.',
      duration: 9,
    });
  } catch {
    timer = window.setTimeout(verifyEstimatedReset, NETWORK_RETRY_MS);
  }
}

/** Schedule one non-invasive reminder from the backend's conservative estimate. */
export function watchPromLimitStatus(status: PromLimitStatus) {
  if (!status.limit_active || !status.limit_retry_at) {
    clearTimer();
    return;
  }
  const retryAt = Date.parse(status.limit_retry_at);
  if (!Number.isFinite(retryAt)) return;
  if (timer !== null && watchedRetryAt === retryAt) return;
  clearTimer();
  watchedRetryAt = retryAt;
  timer = window.setTimeout(verifyEstimatedReset, Math.max(1_000, retryAt - Date.now()));
}

/** Re-read status after app startup or a rejected publish attempt. */
export async function refreshPromLimitWatch() {
  try {
    const status = await loadStatus();
    if (status) watchPromLimitStatus(status);
  } catch {
    // A later publish attempt will retry. Never interfere with publication itself.
  }
}

/** An accepted/completed import proves that the previous warning is stale. */
export function markPromImportAccepted() {
  clearTimer();
}
