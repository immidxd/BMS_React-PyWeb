import React, { useCallback, useEffect, useMemo, useState } from 'react';
import * as api from './api';
import type { StudioFont } from './types';

/**
 * Фірмові шрифти.
 *
 * Кожне накреслення — окремий файл і окремий рядок: Bold і Regular однієї
 * родини це два різні шрифти. Синтетичного «жирного» майстерня не робить
 * свідомо — браузер малює його розтягуванням контуру, і фірмовий шрифт від
 * цього виглядає дешево.
 *
 * Кирилиця перевіряється при заливці: макет українською у шрифті без кирилиці
 * мовчки перетворюється на порожні прямокутники, і помітно це вже в готовому
 * пості.
 */

const BTN = 'rounded-lg px-3 py-1.5 text-xs font-medium transition-colors';
const BTN_MAIN = `${BTN} bg-[var(--bms-accent)] text-white hover:opacity-90`;

const WEIGHT_LABEL: Record<number, string> = {
  100: 'Thin', 200: 'ExtraLight', 300: 'Light', 400: 'Regular', 500: 'Medium',
  600: 'SemiBold', 700: 'Bold', 800: 'ExtraBold', 900: 'Black',
};

const SAMPLE = 'Їжак ґанок — 1234';

const StudioFonts: React.FC<{ fonts: StudioFont[]; onChanged: () => void }> = ({ fonts, onChanged }) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await api.uploadFonts(Array.from(files));
      const noCyrillic = result.items.filter(font => !font.has_cyrillic);
      setMessage(
        `Додано накреслень: ${result.added}` +
        (result.errors.length ? ` · не вдалося: ${result.errors.length}` : '') +
        (noCyrillic.length
          ? ` · без кирилиці: ${noCyrillic.map(font => font.family).join(', ')}`
          : ''),
      );
      onChanged();
    } catch (reason: any) {
      setError(reason.message || 'Не вдалося залити шрифт');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (font: StudioFont) => {
    if (!window.confirm(`Прибрати ${font.family} ${WEIGHT_LABEL[font.weight] || font.weight}?`)) return;
    try { await api.deleteFont(font.id); onChanged(); }
    catch (reason: any) { setError(reason.message); }
  };

  const families = useMemo(() => {
    const map = new Map<string, StudioFont[]>();
    fonts.forEach(font => {
      const list = map.get(font.family) || [];
      list.push(font);
      map.set(font.family, list);
    });
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0], 'uk'));
  }, [fonts]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Фірмові шрифти</h3>
          <p className="mt-1 max-w-3xl text-xs text-gray-500 dark:text-gray-400">
            Приймаються .ttf, .otf, .woff, .woff2. Родину й накреслення програма читає із самого файлу —
            перейменовувати нічого не треба. Заливайте всі накреслення, які плануєте вживати:
            «жирний» береться з файлу Bold, а не домальовується.
          </p>
        </div>
        <label className={`${BTN_MAIN} cursor-pointer`}>
          {busy ? 'Заливаю…' : 'Залити шрифти'}
          <input type="file" accept=".ttf,.otf,.woff,.woff2" multiple hidden
            onChange={event => void upload(event.target.files)} />
        </label>
      </div>

      {(message || error) && (
        <div className={`rounded-lg px-3 py-2 text-xs ${error
          ? 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300'
          : 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300'}`}>
          {error || message}
        </div>
      )}

      {!families.length && (
        <div className="rounded-xl border border-dashed border-gray-300 p-8 text-center text-xs text-gray-400 dark:border-gray-600">
          Жодного фірмового шрифта ще немає — макети малюються системним.
          Залийте свої, і вони одразу зʼявляться у виборі шрифту в редакторі.
        </div>
      )}

      {families.map(([family, items]) => (
        <div key={family} className="rounded-xl border border-gray-200 p-4 dark:border-gray-700">
          <div className="flex items-baseline justify-between gap-3">
            <div className="text-sm font-semibold text-gray-800 dark:text-gray-100">{family}</div>
            <div className="text-[10px] text-gray-400">{items.length} накреслень</div>
          </div>
          <div className="mt-3 space-y-2">
            {items.sort((a, b) => a.weight - b.weight).map(font => (
              <div key={font.id} className="flex items-center justify-between gap-3 rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-800">
                <div className="min-w-0">
                  <div
                    className="truncate text-lg leading-tight text-gray-900 dark:text-gray-100"
                    style={{ fontFamily: `"${font.family}"`, fontWeight: font.weight, fontStyle: font.style }}
                  >
                    {SAMPLE}
                  </div>
                  <div className="mt-0.5 text-[10px] text-gray-400">
                    {WEIGHT_LABEL[font.weight] || font.weight}
                    {font.style === 'italic' ? ' Italic' : ''} · {font.format.toUpperCase()} ·{' '}
                    {Math.round(font.bytes / 1024)} КБ
                    {!font.has_cyrillic && (
                      <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
                        без кирилиці
                      </span>
                    )}
                  </div>
                </div>
                <button type="button" onClick={() => void remove(font)}
                  className="shrink-0 rounded-lg border border-gray-200 px-2 py-1 text-[11px] text-gray-500 hover:bg-white dark:border-gray-600 dark:hover:bg-gray-700">
                  Прибрати
                </button>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
};

export default StudioFonts;
