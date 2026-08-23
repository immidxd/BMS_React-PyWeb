import React from 'react';
import { BTN_GHOST, LABEL, Section, Slider, SWATCH, Toggle, deg, pct } from '../ui';
import {
  Background, DEFAULT_ADJUST, DEFAULT_PHOTO_FILTER, DEFAULT_SCRIM, DEFAULT_VIGNETTE,
} from '../types';

/**
 * Фон: кадр, світло й підкладка під текст.
 *
 * Порядок груп — це порядок роботи над кадром: спершу що показуємо, далі як
 * він стоїть, потім світло, і аж наприкінці — затемнення під заголовок.
 * Відкрита за замовчуванням лише перша: решта потрібна не щоразу, а панель на
 * двадцять повзунків одразу — це те, від чого ми пішли.
 */

type Props = {
  background: Background;
  onChange: (patch: Partial<Background>) => void;
  onPickPhoto: () => void;
};

const SCRIM_MODES: Array<{ key: Background['scrim']['mode']; label: string }> = [
  { key: 'none', label: 'Немає' },
  { key: 'top', label: 'Згори' },
  { key: 'bottom', label: 'Знизу' },
  { key: 'both', label: 'З обох' },
  { key: 'radial', label: 'У центрі' },
];

const BackgroundPanel: React.FC<Props> = ({ background, onChange, onPickPhoto }) => {
  const isPhoto = background.type === 'asset';
  const adjust = background.adjust || DEFAULT_ADJUST;
  const filter = background.filter || DEFAULT_PHOTO_FILTER;
  const vignette = background.vignette || DEFAULT_VIGNETTE;
  const scrim = background.scrim || DEFAULT_SCRIM;

  const patchAdjust = (patch: Partial<typeof adjust>) =>
    onChange({ adjust: { ...adjust, ...patch } });
  const patchFilter = (patch: Partial<typeof filter>) =>
    onChange({ filter: { ...filter, ...patch } });

  const touchedAdjust = adjust.rotate || adjust.tiltX || adjust.tiltY || adjust.flipX || adjust.flipY;
  const touchedLight = filter.brightness !== 1 || filter.contrast !== 1
    || filter.saturation !== 1 || filter.blur > 0;

  return (
    <div>
      <Section title="Що на фоні" defaultOpen>
        <div className="flex flex-wrap items-center gap-2">
          <Toggle active={!isPhoto} onClick={() => onChange({ type: 'color' })}>Колір</Toggle>
          <input type="color" value={background.color} className={SWATCH}
            onChange={event => onChange({ color: event.target.value })} />
          <Toggle active={isPhoto} onClick={() => onChange({ type: 'asset' })}>Фото</Toggle>
          <button type="button" className={BTN_GHOST} onClick={onPickPhoto}>
            {background.assetId ? 'Змінити фото' : 'Обрати з галереї'}
          </button>
        </div>
        {isPhoto && !background.assetId && (
          <p className="text-[10px] text-amber-600 dark:text-amber-400">
            Фото ще не обране — поки що видно колір.
          </p>
        )}
      </Section>

      {isPhoto && (
        <>
          <Section title="Кадр" defaultOpen
            hint="Тягніть фото просто на полотні, колесо миші — масштаб. Повзунки тут для точного доведення.">
            <Slider label="Масштаб" value={background.scale} min={0.5} max={4} step={0.01}
              format={pct} onReset={() => onChange({ scale: 1 })}
              onChange={value => onChange({ scale: value })} />
            <div className="grid grid-cols-2 gap-2">
              <Slider label="Зсув ↔" value={background.offsetX} min={-1200} max={1200} step={1}
                format={value => `${Math.round(value)}`} onReset={() => onChange({ offsetX: 0 })}
                onChange={value => onChange({ offsetX: value })} />
              <Slider label="Зсув ↕" value={background.offsetY} min={-1200} max={1200} step={1}
                format={value => `${Math.round(value)}`} onReset={() => onChange({ offsetY: 0 })}
                onChange={value => onChange({ offsetY: value })} />
            </div>
            <div className="flex flex-wrap gap-1.5">
              <Toggle active={background.fit === 'cover'} onClick={() => onChange({ fit: 'cover' })}>
                Заповнити
              </Toggle>
              <Toggle active={background.fit === 'contain'} onClick={() => onChange({ fit: 'contain' })}>
                Вмістити
              </Toggle>
              <button type="button" className={BTN_GHOST}
                onClick={() => onChange({ scale: 1, offsetX: 0, offsetY: 0 })}>
                Скинути кадр
              </button>
            </div>
          </Section>

          <Section title="Геометрія" badge={touchedAdjust ? 'змінено' : null}
            hint="Поворот виправляє завалений горизонт. Нахили — це скіс: на малих кутах вони випрямляють стіни й полиці, на великих помітно тягнуть кадр.">
            <div className="flex flex-wrap gap-1.5">
              <Toggle active={adjust.flipX} onClick={() => patchAdjust({ flipX: !adjust.flipX })}
                title="Віддзеркалити ліворуч-праворуч">⇄ Дзеркало</Toggle>
              <Toggle active={adjust.flipY} onClick={() => patchAdjust({ flipY: !adjust.flipY })}
                title="Перевернути згори-вниз">⇅ Переворот</Toggle>
              <button type="button" className={BTN_GHOST}
                onClick={() => onChange({ adjust: { ...DEFAULT_ADJUST } })}>
                Скинути
              </button>
            </div>
            <Slider label="Поворот (горизонт)" value={adjust.rotate} min={-45} max={45} step={0.1}
              format={deg} onReset={() => patchAdjust({ rotate: 0 })}
              onChange={value => patchAdjust({ rotate: value })} />
            <div className="grid grid-cols-2 gap-2">
              <Slider label="Нахил ↔" value={adjust.tiltX} min={-20} max={20} step={0.1}
                format={deg} onReset={() => patchAdjust({ tiltX: 0 })}
                onChange={value => patchAdjust({ tiltX: value })} />
              <Slider label="Нахил ↕" value={adjust.tiltY} min={-20} max={20} step={0.1}
                format={deg} onReset={() => patchAdjust({ tiltY: 0 })}
                onChange={value => patchAdjust({ tiltY: value })} />
            </div>
          </Section>

          <Section title="Світло" badge={touchedLight ? 'змінено' : null}>
            <div className="grid grid-cols-2 gap-2">
              <Slider label="Яскравість" value={filter.brightness} min={0.4} max={1.8}
                format={pct} onReset={() => patchFilter({ brightness: 1 })}
                onChange={value => patchFilter({ brightness: value })} />
              <Slider label="Контраст" value={filter.contrast} min={0.4} max={2}
                format={pct} onReset={() => patchFilter({ contrast: 1 })}
                onChange={value => patchFilter({ contrast: value })} />
              <Slider label="Насиченість" value={filter.saturation} min={0} max={2}
                format={pct} onReset={() => patchFilter({ saturation: 1 })}
                onChange={value => patchFilter({ saturation: value })} />
              <Slider label="Розмиття" value={filter.blur} min={0} max={24} step={0.5}
                format={value => `${value}`} onReset={() => patchFilter({ blur: 0 })}
                onChange={value => patchFilter({ blur: value })} />
            </div>
            <button type="button" className={BTN_GHOST}
              onClick={() => onChange({ filter: { ...DEFAULT_PHOTO_FILTER } })}>
              Скинути світло
            </button>
          </Section>
        </>
      )}

      <Section title="Затемнення під текст"
        badge={scrim.mode !== 'none' || background.overlayOpacity > 0 ? 'увімкнено' : null}
        hint="Градієнт кладеться поверх фото — заголовок читається навіть на строкатому кадрі. Світлий колір дає засвітлення замість затемнення.">
        <div className="flex flex-wrap items-center gap-1.5">
          {SCRIM_MODES.map(mode => (
            <Toggle key={mode.key} active={scrim.mode === mode.key}
              onClick={() => onChange({ scrim: { ...scrim, mode: mode.key } })}>
              {mode.label}
            </Toggle>
          ))}
          <input type="color" value={scrim.color} className={SWATCH}
            onChange={event => onChange({ scrim: { ...scrim, color: event.target.value } })} />
        </div>
        {scrim.mode !== 'none' && (
          <Slider label="Сила" value={scrim.opacity} min={0.05} max={1}
            format={pct} onReset={() => onChange({ scrim: { ...scrim, opacity: 0.45 } })}
            onChange={value => onChange({ scrim: { ...scrim, opacity: value } })} />
        )}
        <Slider label="Рівне затемнення всього кадру" value={background.overlayOpacity}
          min={0} max={0.85} format={pct} onReset={() => onChange({ overlayOpacity: 0 })}
          onChange={value => onChange({ overlayOpacity: value })} />
      </Section>

      <Section title="Віньєтка" badge={vignette.enabled ? 'увімкнено' : null}
        hint="Притемнення по краях збирає увагу в центр кадру. Світлий колір дає протилежний ефект — засвіт по краях.">
        <div className="flex flex-wrap items-center gap-2">
          <Toggle active={vignette.enabled}
            onClick={() => onChange({ vignette: { ...vignette, enabled: !vignette.enabled } })}>
            {vignette.enabled ? 'Увімкнено' : 'Вимкнено'}
          </Toggle>
          <input type="color" value={vignette.color} className={SWATCH}
            onChange={event => onChange({ vignette: { ...vignette, color: event.target.value } })} />
        </div>
        {vignette.enabled && (
          <div className="grid grid-cols-2 gap-2">
            <Slider label="Сила" value={vignette.strength} min={0.05} max={1}
              format={pct} onReset={() => onChange({ vignette: { ...vignette, strength: 0.45 } })}
              onChange={value => onChange({ vignette: { ...vignette, strength: value } })} />
            <Slider label="М'якість" value={vignette.softness} min={0.05} max={0.95}
              format={pct} onReset={() => onChange({ vignette: { ...vignette, softness: 0.55 } })}
              onChange={value => onChange({ vignette: { ...vignette, softness: value } })} />
          </div>
        )}
      </Section>

      {!isPhoto && (
        <p className={`${LABEL} mt-2 normal-case`}>
          Геометрія, світло й віньєтка з'являться, щойно фон стане фотографією.
        </p>
      )}
    </div>
  );
};

export default BackgroundPanel;
