import React from 'react';


type TextControl = HTMLTextAreaElement | HTMLInputElement;
type Dialect = 'telegram' | 'viber';

interface Props {
  dialect: Dialect;
  targetRef: React.RefObject<TextControl>;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  accent?: 'sky' | 'violet';
}

const MARKERS: Record<Dialect, Record<'bold' | 'italic' | 'mono' | 'strike', [string, string]>> = {
  telegram: {
    bold: ['**', '**'],
    italic: ['__', '__'],
    mono: ['`', '`'],
    strike: ['~~', '~~'],
  },
  viber: {
    bold: ['*', '*'],
    italic: ['_', '_'],
    mono: ['```', '```'],
    strike: ['~', '~'],
  },
};

const TOOLS = [
  { key: 'bold' as const, label: 'Жирний', glyph: <b>B</b> },
  { key: 'italic' as const, label: 'Курсив', glyph: <i>I</i> },
  { key: 'mono' as const, label: 'Моноширинний', glyph: <span className="font-mono text-[11px]">&lt;/&gt;</span> },
  { key: 'strike' as const, label: 'Закреслений', glyph: <s>S</s> },
];

/** Форматує виділення синтаксисом саме тієї платформи, куди піде пост. */
const TextFormattingToolbar: React.FC<Props> = ({
  dialect, targetRef, value, onChange, disabled = false, accent = 'sky',
}) => {
  const apply = (key: keyof (typeof MARKERS)[Dialect]) => {
    const target = targetRef.current;
    if (!target || disabled) return;
    const start = target.selectionStart ?? value.length;
    const end = target.selectionEnd ?? start;
    const [open, close] = MARKERS[dialect][key];
    const selected = value.slice(start, end);
    const next = `${value.slice(0, start)}${open}${selected}${close}${value.slice(end)}`;
    onChange(next);
    window.requestAnimationFrame(() => {
      target.focus();
      const selectionStart = start + open.length;
      target.setSelectionRange(selectionStart, selectionStart + selected.length);
    });
  };

  const active = accent === 'violet'
    ? 'hover:border-violet-400 hover:bg-violet-50 hover:text-violet-700 dark:hover:bg-violet-900/25 dark:hover:text-violet-300'
    : 'hover:border-sky-400 hover:bg-sky-50 hover:text-sky-700 dark:hover:bg-sky-900/25 dark:hover:text-sky-300';

  return (
    <div className="flex items-center gap-1" role="toolbar" aria-label="Форматування тексту">
      {TOOLS.map(tool => (
        <button
          key={tool.key}
          type="button"
          onClick={() => apply(tool.key)}
          disabled={disabled}
          title={`${tool.label}: спочатку виділи текст`}
          aria-label={tool.label}
          className={`flex h-7 min-w-7 items-center justify-center rounded-md border border-gray-200 bg-white px-1.5 text-xs text-gray-600 transition disabled:opacity-40 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 ${active}`}
        >
          {tool.glyph}
        </button>
      ))}
    </div>
  );
};

export default TextFormattingToolbar;
