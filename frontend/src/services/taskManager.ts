
import { notify } from '../ui/feedback';

/** Глобальний менеджер фонових задач.
 *
 *  Призначення: довгі операції (додавання товару, завантаження фото, синхронізація,
 *  редагування) виконуються в МОДУЛЬНОМУ scope — промайс живе поза React-компонентом,
 *  тож закриття вікна/перемикання вкладки НЕ перериває операцію. По завершенню —
 *  уніфіковане сповіщення (antd) + запис в історію (центр сповіщень `TaskCenter`).
 *
 *  Патерн виклику (fire-and-continue): компонент кидає `taskManager.run(...)`; навіть якщо
 *  компонент розмонтується — задача доробляється і сповіщає сама. Локальний UI (скид
 *  форми/рефреш) компонент робить через guard (mountedRef) або через подію bms:*.
 */

export type TaskStatus = 'running' | 'waiting' | 'success' | 'partial' | 'error';
export interface Task {
  id: string;
  label: string;
  status: TaskStatus;
  detail?: string;
  startedAt: number;
  endedAt?: number;
}

type Listener = () => void;

function errDetail(e: any): string {
  if (!e?.response) return "Немає зв'язку з програмою — спробуйте ще раз";
  const d = e.response?.data?.detail;
  if (typeof d === 'string' && d.trim()) return d;
  return e?.message || 'Помилка';
}

class TaskManager {
  private tasks: Task[] = [];
  private listeners = new Set<Listener>();
  private seq = 0;

  getTasks(): Task[] { return this.tasks; }
  runningCount(): number { return this.tasks.filter(t => t.status === 'running' || t.status === 'waiting').length; }
  errorCount(): number { return this.tasks.filter(t => t.status === 'error').length; }
  attentionCount(): number { return this.tasks.filter(t => t.status === 'error' || t.status === 'partial').length; }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  }
  private emit() { this.listeners.forEach(fn => { try { fn(); } catch { /* ignore */ } }); }

  clearFinished() {
    this.tasks = this.tasks.filter(t => t.status === 'running' || t.status === 'waiting');
    this.emit();
  }

  /** Стан процесу, який виконує backend незалежно від конкретного вікна. */
  setExternal(id: string, label: string, status: TaskStatus, detail?: string) {
    const existing = this.tasks.find(t => t.id === id);
    if (existing) {
      existing.label = label;
      existing.status = status;
      existing.detail = detail;
      existing.endedAt = status === 'running' || status === 'waiting' ? undefined : Date.now();
      this.tasks = [...this.tasks];
    } else {
      this.tasks = [{ id, label, status, detail, startedAt: Date.now() }, ...this.tasks].slice(0, 40);
    }
    this.emit();
  }

  remove(id: string) {
    const next = this.tasks.filter(t => t.id !== id);
    if (next.length !== this.tasks.length) {
      this.tasks = next;
      this.emit();
    }
  }

  /** Запустити задачу. Повертає той самий промайс (можна await з guard, або кинути «й забути»). */
  async run<T>(
    label: string,
    fn: () => Promise<T>,
    opts?: {
      successMsg?: string;
      errorMsg?: string;
      silentSuccess?: boolean;
      onSuccess?: (res: T) => void;
      /** Дозволяє пакетній операції завершитися без exception, але лишити в
       *  Центрі сповіщень чесний статус «частково» та деталізацію помилок. */
      resultStatus?: (res: T) => { status: 'success' | 'partial'; detail?: string };
    },
  ): Promise<T> {
    const id = `t${++this.seq}_${Date.now()}`;
    const task: Task = { id, label, status: 'running', startedAt: Date.now() };
    this.tasks = [task, ...this.tasks].slice(0, 40);
    this.emit();
    try {
      const res = await fn();
      const outcome = opts?.resultStatus?.(res);
      task.status = outcome?.status ?? 'success';
      task.detail = outcome?.detail;
      task.endedAt = Date.now();
      this.tasks = [...this.tasks];
      this.emit();
      if (!opts?.silentSuccess && task.status === 'partial') {
        notify.warning({
          message: 'Частково виконано',
          description: task.detail || opts?.successMsg || label,
          duration: 9,
        });
      } else if (!opts?.silentSuccess) {
        notify.success({
          message: '✓ Готово',
          description: opts?.successMsg || label,
          duration: 4,
        });
      }
      try { opts?.onSuccess?.(res); } catch { /* ignore */ }
      return res;
    } catch (e: any) {
      task.status = 'error';
      task.detail = errDetail(e);
      task.endedAt = Date.now();
      this.tasks = [...this.tasks];
      this.emit();
      notify.error({
        message: '✕ Не вдалося',
        description: opts?.errorMsg ? `${opts.errorMsg}: ${task.detail}` : `${label}: ${task.detail}`,
        duration: 9,
      });
      throw e;
    }
  }
}

export const taskManager = new TaskManager();

/** Дрібний хелпер для подій оновлення даних (картки слухають і рефрешаться, якщо відкриті). */
export function emitDeliveryChanged(deliveryId: number) {
  window.dispatchEvent(new CustomEvent('bms:delivery-changed', { detail: { deliveryId } }));
}
export function emitProductPhotosChanged(productId: number) {
  window.dispatchEvent(new CustomEvent('bms:product-photos-changed', { detail: { productId } }));
}
