import React from 'react';
import { BTN, BTN_GHOST, SWATCH, Toggle } from './ui';
import type { ImageLayer, Layer, StudioFont, TextLayer } from './types';

/**
 * Плаваюча смуга над обраним шаром.
 *
 * Тут живе те, що робиться постійно: вирівняти, збільшити кегль, зробити
 * жирним, змінити колір. Ходити за цим у бічну панель — саме та «незручність
 * на кожен клік», яка й перетворює редактор на форму налаштувань.
 */

export type AlignKind =
  | 'left' | 'centerX' | 'right'
  | 'top' | 'centerY' | 'bottom'
  | 'fitWidth';

type Props = {
  layer: Layer;
  fonts: StudioFont[];
  onPatch: (patch: Partial<TextLayer> & Partial<ImageLayer>) => void;
  onAlign: (kind: AlignKind) => void;
  onDuplicate: () => void;
  onRemove: () => void;
};

const ALIGN_BUTTONS: Array<{ kind: AlignKind; glyph: string; title: string }> = [
  { kind: 'left', glyph: '⇤', title: 'До лівого поля' },
  { kind: 'centerX', glyph: '⇔', title: 'По центру горизонталі' },
  { kind: 'right', glyph: '⇥', title: 'До правого поля' },
  { kind: 'top', glyph: '⤒', title: 'До верхнього поля' },
  { kind: 'centerY', glyph: '⇕', title: 'По центру вертикалі' },
  { kind: 'bottom', glyph: '⤓', title: 'До нижнього поля' },
  { kind: 'fitWidth', glyph: '↔', title: 'На всю ширину між полями' },
];

const LayerToolbar: React.FC<Props> = ({ layer, fonts, onPatch, onAlign, onDuplicate, onRemove }) => {
  const isText = layer.type === 'text';
  const text = layer as TextLayer;
  const families = Array.from(new Set(fonts.map(font => font.family)))
    .sort((a, b) => a.localeCompare(b, 'uk'));
  const heavier = (current: number) => (current >= 700 ? 400 : 700);

  return (
    <div className="flex flex-wrap items-center gap-1 rounded-xl border border-gray-200 bg-white/95 p-1.5 shadow-sm backdrop-blur dark:border-gray-700 dark:bg-gray-900/95">
      {ALIGN_BUTTONS.map(button => (
        <button key={button.kind} type="button" title={button.title}
          onClick={() => onAlign(button.kind)}
          className={`${BTN} w-8 border border-transparent px-0 text-center text-gray-500 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800`}>
          {button.glyph}
        </button>
      ))}

      <span className="mx-1 h-5 w-px bg-gray-200 dark:bg-gray-700" />

      {isText && (
        <>
          <select value={text.fontFamily}
            onChange={event => onPatch({ fontFamily: event.target.value })}
            className="max-w-[9rem] rounded-lg border border-gray-200 px-1.5 py-1 text-[11px] dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100">
            <option value="">Системний</option>
            {families.map(family => <option key={family} value={family}>{family}</option>)}
          </select>
          <div className="flex items-center rounded-lg border border-gray-200 dark:border-gray-600">
            <button type="button" title="Дрібніше"
              onClick={() => onPatch({ fontSize: Math.max(8, Math.round(text.fontSize * 0.92)) })}
              className="px-2 py-1 text-[11px] text-gray-500 dark:text-gray-300">−</button>
            <span className="w-10 text-center text-[11px] tabular-nums text-gray-600 dark:text-gray-300">
              {Math.round(text.fontSize)}
            </span>
            <button type="button" title="Більше"
              onClick={() => onPatch({ fontSize: Math.round(text.fontSize * 1.08) })}
              className="px-2 py-1 text-[11px] text-gray-500 dark:text-gray-300">+</button>
          </div>
          <Toggle active={text.fontWeight >= 700} title="Жирний"
            onClick={() => onPatch({ fontWeight: heavier(text.fontWeight) })}
            className="w-8 px-0 font-bold">Ж</Toggle>
          <Toggle active={text.fontStyle === 'italic'} title="Курсив"
            onClick={() => onPatch({ fontStyle: text.fontStyle === 'italic' ? 'normal' : 'italic' })}
            className="w-8 px-0 italic">К</Toggle>
          <Toggle active={text.decoration === 'line-through'} title="Закреслений"
            onClick={() => onPatch({
              decoration: text.decoration === 'line-through' ? 'none' : 'line-through',
            })}
            className="w-8 px-0 line-through">З</Toggle>
          <Toggle active={text.uppercase} title="ВЕЛИКІ літери"
            onClick={() => onPatch({ uppercase: !text.uppercase })}
            className="w-8 px-0">АБ</Toggle>
          {(['left', 'center', 'right'] as const).map(align => (
            <Toggle key={align} active={text.align === align} title={`Текст ${align}`}
              onClick={() => onPatch({ align })} className="w-8 px-0">
              {align === 'left' ? '≡' : align === 'center' ? '≣' : '≡'}
            </Toggle>
          ))}
          <input type="color" value={text.color} className={SWATCH} title="Колір тексту"
            onChange={event => onPatch({ color: event.target.value })} />
        </>
      )}

      {!isText && (
        <>
          <Toggle active={Boolean((layer as ImageLayer).flipX)} title="Віддзеркалити"
            onClick={() => onPatch({ flipX: !(layer as ImageLayer).flipX })}
            className="w-8 px-0">⇄</Toggle>
          <Toggle active={Boolean((layer as ImageLayer).flipY)} title="Перевернути"
            onClick={() => onPatch({ flipY: !(layer as ImageLayer).flipY })}
            className="w-8 px-0">⇅</Toggle>
        </>
      )}

      <span className="mx-1 h-5 w-px bg-gray-200 dark:bg-gray-700" />
      <button type="button" title="Дублювати (⌘D)" onClick={onDuplicate}
        className={`${BTN_GHOST} w-8 px-0 text-center`}>⧉</button>
      <button type="button" title="Видалити шар (Delete)" onClick={onRemove}
        className={`${BTN} w-8 px-0 text-center text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20`}>×</button>
    </div>
  );
};

export default LayerToolbar;
