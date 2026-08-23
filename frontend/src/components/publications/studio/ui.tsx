import React, { useState } from 'react';

/**
 * Спільна мова інтерфейсу майстерні.
 *
 * Класи зібрані в одному місці не заради краси, а щоб панелі інструментів
 * виглядали однією програмою: коли кожна панель вигадує власні відступи й
 * кегль, редактор швидко перетворюється на клаптикову ковдру — саме те, від
 * чого ми тут ідемо.
 */

export const CARD = 'rounded-xl border border-gray-200 dark:border-gray-700';
export const BTN = 'rounded-lg px-3 py-1.5 text-xs font-medium transition-colors';
export const BTN_MAIN = `${BTN} bg-[var(--bms-accent)] text-white hover:opacity-90 disabled:opacity-50`;
export const BTN_GHOST = `${BTN} border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800`;
export const FIELD = 'w-full rounded-lg border border-gray-200 px-2 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100';
export const LABEL = 'text-[10px] font-medium uppercase tracking-wide text-gray-400';
export const SWATCH = 'h-7 w-9 shrink-0 cursor-pointer rounded border border-gray-200 dark:border-gray-600';

/** Кнопка-перемикач: увімкнений стан читається кольором, а не рамкою. */
export const Toggle: React.FC<{
  active: boolean;
  onClick: () => void;
  title?: string;
  className?: string;
  children: React.ReactNode;
}> = ({ active, onClick, title, className = '', children }) => (
  <button
    type="button" title={title} onClick={onClick}
    className={`${BTN} ${className} ${active
      ? 'bg-[var(--bms-accent)] text-white'
      : 'border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800'}`}
  >
    {children}
  </button>
);

/** Повзунок із підписом і поточним значенням.
 *
 *  Значення показуємо завжди: у попередній версії людина рухала повзунок
 *  «наосліп» і не могла ані повторити те саме число в іншому пості, ані
 *  зрозуміти, наскільки далеко зайшла. */
export const Slider: React.FC<{
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  format?: (value: number) => string;
  onChange: (value: number) => void;
  onReset?: () => void;
}> = ({ label, value, min, max, step = 0.01, format, onChange, onReset }) => (
  <label className="block">
    <span className="flex items-baseline justify-between">
      <span className={LABEL}>{label}</span>
      <button
        type="button"
        onClick={() => onReset?.()}
        title={onReset ? 'Повернути типове значення' : undefined}
        className={`text-[10px] tabular-nums ${onReset ? 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-200' : 'text-gray-400'}`}
      >
        {format ? format(value) : value}
      </button>
    </span>
    <input
      type="range" min={min} max={max} step={step} value={value}
      onChange={event => onChange(Number(event.target.value))}
      className="mt-0.5 w-full accent-[var(--bms-accent)]"
    />
  </label>
);

/** Згортка. Другорядне має бути під рукою, але не перед очима — інакше
 *  панель знову перетворюється на нескінченний список повзунків. */
export const Section: React.FC<{
  title: string;
  hint?: string;
  defaultOpen?: boolean;
  badge?: string | null;
  children: React.ReactNode;
}> = ({ title, hint, defaultOpen = false, badge, children }) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-gray-100 py-2 first:border-t-0 dark:border-gray-700">
      <button
        type="button" onClick={() => setOpen(current => !current)}
        className="flex w-full items-center justify-between gap-2 py-1 text-left"
      >
        <span className="flex items-center gap-2">
          <span className="text-[11px] font-semibold text-gray-700 dark:text-gray-200">{title}</span>
          {badge && (
            <span className="rounded-full bg-[var(--bms-accent)]/10 px-1.5 py-0.5 text-[9px] text-[var(--bms-accent)]">
              {badge}
            </span>
          )}
        </span>
        <span className="text-[10px] text-gray-400">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className="mt-2 space-y-2.5">
          {hint && <p className="text-[10px] leading-relaxed text-gray-400">{hint}</p>}
          {children}
        </div>
      )}
    </div>
  );
};

export const pct = (value: number): string => `${Math.round(value * 100)}%`;
export const deg = (value: number): string => `${Math.round(value)}°`;
export const px = (value: number): string => `${Math.round(value)} px`;
