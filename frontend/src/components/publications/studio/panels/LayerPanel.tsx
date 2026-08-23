import React from 'react';
import { BTN_GHOST, FIELD, LABEL, Section, Slider, SWATCH, Toggle, deg, pct } from '../ui';
import {
  DEFAULT_EXTRUDE, DEFAULT_GRADIENT, DEFAULT_PHOTO_FILTER, DEFAULT_SHADOW,
  DEFAULT_STROKE, ImageLayer, Layer, StudioFont, TextLayer, TextRole,
  TEXT_ROLE_PRESETS,
} from '../types';

/**
 * Панель обраного шару.
 *
 * Найчастіші дії (текст, шрифт, кегль, колір) — одразу; ефекти — у згортках,
 * бо тінь із обведенням чіпають раз на десять постів, а заголовок правлять
 * щоразу. Кожен ефект показує в заголовку, чи він увімкнений: інакше
 * доводиться відкривати всі згортки, щоб зрозуміти, звідки в кадрі синя
 * облямівка.
 */

type Props = {
  layer: Layer;
  fonts: StudioFont[];
  canvasHeight: number;
  onPatch: (patch: Partial<TextLayer> & Partial<ImageLayer>) => void;
};

const LayerPanel: React.FC<Props> = ({ layer, fonts, canvasHeight, onPatch }) => {
  const families = Array.from(new Set(fonts.map(font => font.family)))
    .sort((a, b) => a.localeCompare(b, 'uk'));

  const weightsFor = (family: string): number[] => {
    const list = fonts.filter(font => font.family === family).map(font => font.weight);
    return list.length ? Array.from(new Set(list)).sort((a, b) => a - b) : [400, 700];
  };

  if (layer.type === 'image') {
    const filter = layer.filter || DEFAULT_PHOTO_FILTER;
    return (
      <div>
        <Section title="Фото-шар" defaultOpen>
          <div className="flex flex-wrap gap-1.5">
            <Toggle active={Boolean(layer.flipX)} onClick={() => onPatch({ flipX: !layer.flipX })}>
              ⇄ Дзеркало
            </Toggle>
            <Toggle active={Boolean(layer.flipY)} onClick={() => onPatch({ flipY: !layer.flipY })}>
              ⇅ Переворот
            </Toggle>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className={LABEL}>Ширина
              <input type="number" className={`${FIELD} mt-1`} value={Math.round(layer.width)}
                onChange={event => onPatch({ width: Number(event.target.value) })} />
            </label>
            <label className={LABEL}>Висота
              <input type="number" className={`${FIELD} mt-1`} value={Math.round(layer.height)}
                onChange={event => onPatch({ height: Number(event.target.value) })} />
            </label>
          </div>
          <Slider label="Заокруглення" value={layer.radius} min={0} max={240} step={1}
            format={value => `${Math.round(value)}`} onReset={() => onPatch({ radius: 0 })}
            onChange={value => onPatch({ radius: value })} />
          <div className="grid grid-cols-2 gap-2">
            <Slider label="Прозорість" value={layer.opacity} min={0.05} max={1}
              format={pct} onReset={() => onPatch({ opacity: 1 })}
              onChange={value => onPatch({ opacity: value })} />
            <Slider label="Поворот" value={layer.rotation} min={-180} max={180} step={0.5}
              format={deg} onReset={() => onPatch({ rotation: 0 })}
              onChange={value => onPatch({ rotation: value })} />
          </div>
        </Section>

        <Section title="Світло">
          <div className="grid grid-cols-2 gap-2">
            <Slider label="Яскравість" value={filter.brightness} min={0.4} max={1.8}
              format={pct} onReset={() => onPatch({ filter: { ...filter, brightness: 1 } })}
              onChange={value => onPatch({ filter: { ...filter, brightness: value } })} />
            <Slider label="Контраст" value={filter.contrast} min={0.4} max={2}
              format={pct} onReset={() => onPatch({ filter: { ...filter, contrast: 1 } })}
              onChange={value => onPatch({ filter: { ...filter, contrast: value } })} />
            <Slider label="Насиченість" value={filter.saturation} min={0} max={2}
              format={pct} onReset={() => onPatch({ filter: { ...filter, saturation: 1 } })}
              onChange={value => onPatch({ filter: { ...filter, saturation: value } })} />
            <Slider label="Розмиття" value={filter.blur} min={0} max={24} step={0.5}
              format={value => `${value}`} onReset={() => onPatch({ filter: { ...filter, blur: 0 } })}
              onChange={value => onPatch({ filter: { ...filter, blur: value } })} />
          </div>
        </Section>
      </div>
    );
  }

  const text = layer as TextLayer;
  const gradient = text.gradient || DEFAULT_GRADIENT;
  const shadow = text.shadow || DEFAULT_SHADOW;
  const stroke = text.stroke || DEFAULT_STROKE;
  const extrude = text.extrude || DEFAULT_EXTRUDE;

  return (
    <div>
      <Section title="Текст" defaultOpen>
        <textarea value={text.text} rows={3} className={FIELD}
          onChange={event => onPatch({ text: event.target.value })} />
        <div className="grid grid-cols-2 gap-2">
          <label className={LABEL}>Шаблон
            <select className={`${FIELD} mt-1`} value={text.role}
              onChange={event => {
                const role = event.target.value as TextRole;
                const preset = TEXT_ROLE_PRESETS[role];
                onPatch({
                  role,
                  fontWeight: preset.weight,
                  fontSize: Math.round(canvasHeight * preset.sizeRatio),
                  lineHeight: preset.lineHeight,
                  letterSpacing: preset.letterSpacing,
                  uppercase: preset.uppercase,
                });
              }}>
              {(Object.keys(TEXT_ROLE_PRESETS) as TextRole[]).map(role => (
                <option key={role} value={role}>{TEXT_ROLE_PRESETS[role].label}</option>
              ))}
            </select>
          </label>
          <label className={LABEL}>Шрифт
            <select className={`${FIELD} mt-1`} value={text.fontFamily}
              onChange={event => onPatch({ fontFamily: event.target.value })}>
              <option value="">Системний</option>
              {families.map(family => <option key={family} value={family}>{family}</option>)}
            </select>
          </label>
        </div>
        {!families.length && (
          <p className="text-[10px] leading-relaxed text-amber-600 dark:text-amber-400">
            Фірмових шрифтів ще немає. Вкладка «Шрифти» → «Додати з пристрою» покаже всі,
            що встановлені на цьому комп'ютері.
          </p>
        )}
        <div className="grid grid-cols-2 gap-2">
          <label className={LABEL}>Накреслення
            <select className={`${FIELD} mt-1`} value={text.fontWeight}
              onChange={event => onPatch({ fontWeight: Number(event.target.value) })}>
              {weightsFor(text.fontFamily).map(weight => (
                <option key={weight} value={weight}>{weight}</option>
              ))}
            </select>
          </label>
          <label className={LABEL}>Кегль
            <input type="number" className={`${FIELD} mt-1`} value={Math.round(text.fontSize)}
              onChange={event => onPatch({ fontSize: Number(event.target.value) || 12 })} />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Slider label="Міжрядковий" value={text.lineHeight} min={0.85} max={2}
            format={value => value.toFixed(2)} onReset={() => onPatch({ lineHeight: 1.2 })}
            onChange={value => onPatch({ lineHeight: value })} />
          <Slider label="Міжлітерний" value={text.letterSpacing} min={-5} max={20} step={0.1}
            format={value => value.toFixed(1)} onReset={() => onPatch({ letterSpacing: 0 })}
            onChange={value => onPatch({ letterSpacing: value })} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Slider label="Прозорість" value={text.opacity} min={0.05} max={1}
            format={pct} onReset={() => onPatch({ opacity: 1 })}
            onChange={value => onPatch({ opacity: value })} />
          <Slider label="Поворот" value={text.rotation} min={-180} max={180} step={0.5}
            format={deg} onReset={() => onPatch({ rotation: 0 })}
            onChange={value => onPatch({ rotation: value })} />
        </div>
      </Section>

      <Section title="Заливка" badge={text.fillType === 'gradient' ? 'градієнт' : null} defaultOpen>
        <div className="flex flex-wrap items-center gap-2">
          <Toggle active={text.fillType !== 'gradient'} onClick={() => onPatch({ fillType: 'solid' })}>
            Суцільна
          </Toggle>
          <input type="color" value={text.color} className={SWATCH}
            onChange={event => onPatch({ color: event.target.value })} />
          <Toggle active={text.fillType === 'gradient'} onClick={() => onPatch({ fillType: 'gradient' })}>
            Градієнт
          </Toggle>
          {text.fillType === 'gradient' && (
            <>
              <input type="color" value={gradient.from} className={SWATCH}
                onChange={event => onPatch({ gradient: { ...gradient, from: event.target.value } })} />
              <input type="color" value={gradient.to} className={SWATCH}
                onChange={event => onPatch({ gradient: { ...gradient, to: event.target.value } })} />
            </>
          )}
        </div>
        {text.fillType === 'gradient' && (
          <Slider label="Кут градієнта" value={gradient.angle} min={0} max={360} step={1}
            format={deg} onReset={() => onPatch({ gradient: { ...gradient, angle: 90 } })}
            onChange={value => onPatch({ gradient: { ...gradient, angle: value } })} />
        )}
      </Section>

      <Section title="Тінь" badge={shadow.enabled ? 'увімкнено' : null}>
        <div className="flex items-center gap-2">
          <Toggle active={shadow.enabled}
            onClick={() => onPatch({ shadow: { ...shadow, enabled: !shadow.enabled } })}>
            {shadow.enabled ? 'Увімкнено' : 'Вимкнено'}
          </Toggle>
          <input type="color" value={shadow.color} className={SWATCH}
            onChange={event => onPatch({ shadow: { ...shadow, color: event.target.value } })} />
        </div>
        {shadow.enabled && (
          <div className="grid grid-cols-2 gap-2">
            <Slider label="Зсув ↔" value={shadow.dx} min={-60} max={60} step={1}
              format={value => `${Math.round(value)}`}
              onChange={value => onPatch({ shadow: { ...shadow, dx: value } })} />
            <Slider label="Зсув ↕" value={shadow.dy} min={-60} max={60} step={1}
              format={value => `${Math.round(value)}`}
              onChange={value => onPatch({ shadow: { ...shadow, dy: value } })} />
            <Slider label="Розмиття" value={shadow.blur} min={0} max={80} step={1}
              format={value => `${Math.round(value)}`}
              onChange={value => onPatch({ shadow: { ...shadow, blur: value } })} />
            <Slider label="Сила" value={shadow.opacity} min={0.05} max={1}
              format={pct}
              onChange={value => onPatch({ shadow: { ...shadow, opacity: value } })} />
          </div>
        )}
      </Section>

      <Section title="Обведення" badge={stroke.enabled ? 'увімкнено' : null}>
        <div className="flex items-center gap-2">
          <Toggle active={stroke.enabled}
            onClick={() => onPatch({ stroke: { ...stroke, enabled: !stroke.enabled } })}>
            {stroke.enabled ? 'Увімкнено' : 'Вимкнено'}
          </Toggle>
          <input type="color" value={stroke.color} className={SWATCH}
            onChange={event => onPatch({ stroke: { ...stroke, color: event.target.value } })} />
        </div>
        {stroke.enabled && (
          <Slider label="Товщина" value={stroke.width} min={1} max={40} step={1}
            format={value => `${Math.round(value)}`}
            onChange={value => onPatch({ stroke: { ...stroke, width: value } })} />
        )}
      </Section>

      <Section title="Об'єм" badge={extrude.enabled ? 'увімкнено' : null}>
        <div className="flex items-center gap-2">
          <Toggle active={extrude.enabled}
            onClick={() => onPatch({ extrude: { ...extrude, enabled: !extrude.enabled } })}>
            {extrude.enabled ? 'Увімкнено' : 'Вимкнено'}
          </Toggle>
          <input type="color" value={extrude.color} className={SWATCH}
            onChange={event => onPatch({ extrude: { ...extrude, color: event.target.value } })} />
        </div>
        {extrude.enabled && (
          <div className="grid grid-cols-2 gap-2">
            <Slider label="Глибина" value={extrude.depth} min={1} max={40} step={1}
              format={value => `${Math.round(value)}`}
              onChange={value => onPatch({ extrude: { ...extrude, depth: value } })} />
            <Slider label="Напрям" value={extrude.angle} min={0} max={360} step={1}
              format={deg}
              onChange={value => onPatch({ extrude: { ...extrude, angle: value } })} />
          </div>
        )}
      </Section>

      <button type="button" className={`${BTN_GHOST} mt-2 w-full`}
        onClick={() => onPatch({
          fillType: 'solid',
          gradient: { ...DEFAULT_GRADIENT },
          shadow: { ...DEFAULT_SHADOW },
          stroke: { ...DEFAULT_STROKE },
          extrude: { ...DEFAULT_EXTRUDE },
        })}>
        Прибрати всі ефекти з шару
      </button>
    </div>
  );
};

export default LayerPanel;
