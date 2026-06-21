import React, { useMemo, useRef, useState } from 'react';

/** Розумне поле з автодоповненням (як пошук Google): підстрокове співпадіння з
 *  ранжуванням (спочатку префікс, далі входження), підсвітка збігу, навігація
 *  клавіатурою (↑↓ Enter Esc), вибір кліком. Зберігає стиль звичайного інпута. */

interface Props {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
  className?: string;
  /** Викликається при ВИБОРІ зі списку (Enter/клік) — для побічних ефектів (напр. тип). */
  onPick?: (v: string) => void;
  listCap?: number;
}

function rankFilter(options: string[], q: string, cap: number): string[] {
  const ql = (q || '').trim().toLowerCase();
  if (!ql) return options.slice(0, cap);
  const starts: string[] = [];
  const incl: string[] = [];
  for (const o of options) {
    const ol = o.toLowerCase();
    if (ol === ql) continue;                 // вже точно введено — не пропонуємо
    const i = ol.indexOf(ql);
    if (i === 0) starts.push(o);
    else if (i > 0) incl.push(o);
  }
  return [...starts, ...incl].slice(0, cap);
}

/** Підсвітити збіг (case-insensitive) у варіанті. */
function highlight(option: string, q: string): React.ReactNode {
  const ql = (q || '').trim().toLowerCase();
  if (!ql) return option;
  const i = option.toLowerCase().indexOf(ql);
  if (i < 0) return option;
  return (
    <>
      {option.slice(0, i)}
      <b className="font-semibold text-gray-900 dark:text-white">{option.slice(i, i + ql.length)}</b>
      {option.slice(i + ql.length)}
    </>
  );
}

const AutoCompleteInput: React.FC<Props> = ({ value, onChange, options, placeholder, className, onPick, listCap = 10 }) => {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(-1);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const filtered = useMemo(() => rankFilter(options, value, listCap), [options, value, listCap]);

  const choose = (v: string) => {
    onChange(v);
    onPick?.(v);
    setOpen(false);
    setHi(-1);
  };

  return (
    <div className="relative">
      <input
        value={value}
        onChange={(e) => { onChange(e.target.value); setOpen(true); setHi(-1); }}
        onFocus={() => { if (blurTimer.current) clearTimeout(blurTimer.current); setOpen(true); }}
        onBlur={() => { blurTimer.current = setTimeout(() => setOpen(false), 120); }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (!open) { setOpen(true); return; }
            setHi((h) => Math.min(h + 1, filtered.length - 1));
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setHi((h) => Math.max(h - 1, 0));
          } else if (e.key === 'Enter') {
            if (open && hi >= 0 && filtered[hi]) { e.preventDefault(); choose(filtered[hi]); }
          } else if (e.key === 'Escape') {
            setOpen(false); setHi(-1);
          }
        }}
        placeholder={placeholder}
        className={className}
        autoCapitalize="none" autoCorrect="off" spellCheck={false}
        autoComplete="off"
      />
      {open && filtered.length > 0 && (
        <ul className="absolute z-50 left-0 right-0 mt-1 max-h-56 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg py-1 text-sm">
          {filtered.map((o, i) => (
            <li
              key={o}
              onMouseDown={(e) => { e.preventDefault(); choose(o); }}
              onMouseEnter={() => setHi(i)}
              className={`px-2.5 py-1.5 cursor-pointer text-gray-700 dark:text-gray-200 ${i === hi ? 'bg-gray-100 dark:bg-gray-700' : ''}`}
            >
              {highlight(o, value)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default AutoCompleteInput;
