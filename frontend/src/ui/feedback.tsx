/**
 * ЄДИНА система зворотного звʼязку BMS — тости, підтвердження, попередження.
 *
 * Замінює зоопарк: antd notification (bottomRight/topRight упереміш), antd message,
 * системні window.confirm / window.alert. Один стиль (мінімалістичний, як картки
 * програми), одна позиція (згори праворуч), світла/темна тема.
 *
 *   notify.success('Збережено')                    — тост
 *   notify.error({ message: 'X', description: 'Y' })
 *   await confirmDialog('Видалити товар?')          — true/false
 *   await confirmDialog({ title, body, danger: true, okText: 'Видалити' })
 *   await alertDialog('Повідомлення…')              — інформаційне вікно (замість alert)
 */
import React, { useEffect, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import { notification } from 'antd';
import type { ArgsProps } from 'antd/es/notification/interface';
import {
  CloseOutlined, QuestionCircleOutlined, ExclamationCircleOutlined,
  DeleteOutlined, InfoCircleOutlined,
} from '@ant-design/icons';

/* ── Тости ─────────────────────────────────────────────────────────────────── */

// Єдине місце розташування і вигляд (клас .bms-toast стилізується в index.css).
notification.config({ placement: 'topRight', top: 64, maxCount: 4 });

type ToastArg = string | ArgsProps;
const DUR: Record<string, number> = { success: 2.5, info: 3, warning: 4, error: 5 };

const toArgs = (kind: keyof typeof DUR, a: ToastArg): ArgsProps => {
  const base: ArgsProps = typeof a === 'string' ? { message: a } : { ...a };
  return { duration: DUR[kind], ...base, placement: 'topRight', className: `bms-toast bms-toast-${kind} ${base.className || ''}` };
};

export const notify = {
  success: (a: ToastArg) => notification.success(toArgs('success', a)),
  error:   (a: ToastArg) => notification.error(toArgs('error', a)),
  warning: (a: ToastArg) => notification.warning(toArgs('warning', a)),
  info:    (a: ToastArg) => notification.info(toArgs('info', a)),
};

/* ── Діалоги (замість window.confirm / window.alert) ───────────────────────── */

export interface DialogOpts {
  title?: string;
  body?: React.ReactNode;      // рядки з \n перенесуться (whitespace-pre-line)
  okText?: string;
  cancelText?: string;
  danger?: boolean;            // червона кнопка (видалення/безповоротні дії)
  kind?: 'confirm' | 'alert' | 'delete' | 'warning';
}

const ICONS: Record<string, { icon: React.ReactNode; cls: string }> = {
  confirm: { icon: <QuestionCircleOutlined style={{ fontSize: 17 }} />, cls: 'bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900' },
  delete:  { icon: <DeleteOutlined style={{ fontSize: 16 }} />,          cls: 'bg-red-600 text-white' },
  warning: { icon: <ExclamationCircleOutlined style={{ fontSize: 17 }} />, cls: 'bg-amber-500 text-white' },
  alert:   { icon: <InfoCircleOutlined style={{ fontSize: 17 }} />,      cls: 'bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900' },
};

const DialogCard: React.FC<{ opts: DialogOpts; alertOnly: boolean; onDone: (ok: boolean) => void }> = ({ opts, alertOnly, onDone }) => {
  const kind = opts.kind || (opts.danger ? 'delete' : alertOnly ? 'alert' : 'confirm');
  const ic = ICONS[kind] || ICONS.confirm;
  const okRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    okRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onDone(false); }
      if (e.key === 'Enter')  { e.stopPropagation(); onDone(true); }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onDone]);

  return (
    // .bms-dialog-host — маркер для гардів Escape у модалках позаду
    <div className="bms-dialog-host fixed inset-0 z-[200] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={() => onDone(false)} />
      <div className="relative w-full max-w-md rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-2xl overflow-hidden bms-fade-in">
        <div className="flex items-start gap-3 px-5 pt-4.5 pb-1" style={{ paddingTop: 18 }}>
          <span className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${ic.cls}`}>{ic.icon}</span>
          <div className="min-w-0 flex-1 pt-1">
            <div className="text-[15px] font-semibold text-gray-900 dark:text-gray-50 leading-snug">
              {opts.title || (alertOnly ? 'Повідомлення' : 'Підтвердження')}
            </div>
          </div>
          <button onClick={() => onDone(false)} className="p-1.5 -mr-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors" aria-label="Закрити">
            <CloseOutlined className="text-xs" />
          </button>
        </div>
        {opts.body != null && String(opts.body) !== '' && (
          <div className="px-5 pb-1 pl-[68px] -mt-0.5 text-sm text-gray-500 dark:text-gray-400 leading-relaxed whitespace-pre-line break-words max-h-[50vh] overflow-y-auto">
            {opts.body}
          </div>
        )}
        <div className="flex items-center justify-end gap-2 px-5 py-3.5 mt-2 border-t border-gray-100 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-800/30">
          {!alertOnly && (
            <button
              onClick={() => onDone(false)}
              className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 hover:text-gray-900 hover:bg-gray-50 dark:text-gray-300 dark:hover:text-gray-100 dark:hover:bg-gray-800 transition-colors duration-150"
            >
              {opts.cancelText || 'Скасувати'}
            </button>
          )}
          <button
            ref={okRef}
            onClick={() => onDone(true)}
            className={`px-4 py-2 rounded-lg text-sm font-semibold transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-offset-1 dark:focus:ring-offset-gray-900 ${
              kind === 'delete'
                ? 'bg-red-600 hover:bg-red-700 text-white focus:ring-red-400'
                : 'bg-gray-900 hover:bg-gray-700 text-white dark:bg-gray-100 dark:hover:bg-white dark:text-gray-900 focus:ring-gray-400'
            }`}
          >
            {opts.okText || (alertOnly ? 'Зрозуміло' : 'Так')}
          </button>
        </div>
      </div>
    </div>
  );
};

function openDialog(opts: DialogOpts, alertOnly: boolean): Promise<boolean> {
  return new Promise((resolve) => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);
    let settled = false;
    const done = (ok: boolean) => {
      if (settled) return;
      settled = true;
      // unmount поза поточним рендером React
      setTimeout(() => { root.unmount(); host.remove(); }, 0);
      resolve(ok);
    };
    root.render(<DialogCard opts={opts} alertOnly={alertOnly} onDone={done} />);
  });
}

/** Підтвердження (замість window.confirm). Приймає рядок або опції. */
export function confirmDialog(a: string | DialogOpts): Promise<boolean> {
  const opts = typeof a === 'string' ? splitMessage(a) : a;
  return openDialog(opts, false);
}

/** Інформаційне вікно (замість window.alert). Приймає рядок або опції. */
export function alertDialog(a: string | DialogOpts): Promise<boolean> {
  const opts = typeof a === 'string' ? splitMessage(a) : a;
  return openDialog(opts, true);
}

// Рядок «Заголовок?\n\nДеталі…» → title + body (перший рядок стає заголовком,
// якщо він короткий; інакше все — у body).
function splitMessage(s: string): DialogOpts {
  const nl = s.indexOf('\n');
  const first = nl === -1 ? s : s.slice(0, nl);
  if (first.length <= 80) {
    const rest = nl === -1 ? '' : s.slice(nl).replace(/^\n+/, '');
    return { title: first.trim(), body: rest || undefined };
  }
  return { body: s };
}
