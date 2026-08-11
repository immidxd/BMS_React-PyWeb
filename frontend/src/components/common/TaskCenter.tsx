import React, { useEffect, useState, useSyncExternalStore } from 'react';
import { taskManager, type Task } from '../../services/taskManager';

/** Плаваючий центр фонових задач (правий нижній кут). Дзвоник із badge (активні/помилки),
 *  розкривна панель зі списком останніх задач (виконується/готово/помилка). Монтується ОДИН
 *  раз на рівні App — тож видимий на будь-якій сторінці й переживає навігацію. */

function useTasks(): Task[] {
  return useSyncExternalStore(
    (cb) => taskManager.subscribe(cb),
    () => taskManager.getTasks(),
  );
}

const ago = (t?: number) => {
  if (!t) return '';
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return `${s}с тому`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m}хв тому` : `${Math.round(m / 60)}год тому`;
};

const TaskCenter: React.FC = () => {
  const tasks = useTasks();
  const [open, setOpen] = useState(false);
  const [, force] = useState(0);

  const running = tasks.filter(t => t.status === 'running').length;
  const errors = tasks.filter(t => t.status === 'error').length;
  const partial = tasks.filter(t => t.status === 'partial').length;
  const hasAny = tasks.length > 0;

  // Оновлювати «N с тому» поки панель відкрита.
  useEffect(() => {
    if (!open) return;
    const id = setInterval(() => force(x => x + 1), 5000);
    return () => clearInterval(id);
  }, [open]);

  // Якщо нічого не відбувалось — не показуємо дзвоник зовсім (мінімалізм).
  if (!hasAny && !open) return null;

  const dot = (t: Task) =>
    t.status === 'running'
      ? <span className="w-3.5 h-3.5 border-2 border-gray-300 border-t-gray-600 rounded-full animate-spin shrink-0" />
      : t.status === 'success'
        ? <span className="text-green-600 shrink-0">✓</span>
        : t.status === 'partial'
          ? <span className="text-amber-500 shrink-0">!</span>
          : <span className="text-red-500 shrink-0">✕</span>;

  return (
    <div className="fixed bottom-4 right-4 z-[200] flex flex-col items-end gap-2 select-none">
      {open && (
        <div className="w-80 max-h-[60vh] overflow-auto rounded-xl bg-white dark:bg-gray-800 shadow-2xl border border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 dark:border-gray-700 sticky top-0 bg-white dark:bg-gray-800">
            <span className="text-[12px] font-medium text-gray-700 dark:text-gray-200">Фонові процеси</span>
            <div className="flex items-center gap-2">
              <button onClick={() => taskManager.clearFinished()}
                className="text-[11px] text-gray-400 hover:text-gray-700 dark:hover:text-gray-200">Очистити</button>
              <button onClick={() => setOpen(false)}
                className="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 text-lg leading-none">×</button>
            </div>
          </div>
          {tasks.length === 0 ? (
            <div className="px-3 py-6 text-center text-[12px] text-gray-400">Немає процесів</div>
          ) : (
            <ul className="py-1">
              {tasks.map(t => (
                <li key={t.id} className="px-3 py-2 flex items-start gap-2 text-[12px] border-b last:border-b-0 border-gray-50 dark:border-gray-700/50">
                  <span className="mt-0.5">{dot(t)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="text-gray-800 dark:text-gray-100 truncate">{t.label}</div>
                    {(t.status === 'error' || t.status === 'partial') && t.detail && (
                      <div className={`${t.status === 'partial' ? 'text-amber-600 dark:text-amber-400' : 'text-red-500'} text-[11px] break-words`}>{t.detail}</div>
                    )}
                    <div className="text-gray-400 text-[10px]">
                      {t.status === 'running' ? 'виконується…' : ago(t.endedAt)}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <button onClick={() => setOpen(o => !o)}
        title="Фонові процеси"
        className={`relative w-11 h-11 rounded-full shadow-lg border flex items-center justify-center transition-colors
          ${errors > 0 ? 'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-800'
            : partial > 0 ? 'bg-amber-50 dark:bg-amber-900/30 border-amber-200 dark:border-amber-800'
            : running > 0 ? 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700'
              : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700'}`}>
        {running > 0
          ? <span className="w-5 h-5 border-2 border-gray-300 border-t-gray-700 rounded-full animate-spin" />
          : <span className="text-lg">🔔</span>}
        {(running > 0 || errors > 0 || partial > 0) && (
          <span className={`absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-semibold text-white flex items-center justify-center
            ${errors > 0 ? 'bg-red-500' : partial > 0 ? 'bg-amber-500' : 'bg-gray-700'}`}>
            {running > 0 ? running : errors || partial}
          </span>
        )}
      </button>
    </div>
  );
};

export default TaskCenter;
