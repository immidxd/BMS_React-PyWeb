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
const BTN_GHOST = `${BTN} border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800`;

const WEIGHT_LABEL: Record<number, string> = {
  100: 'Thin', 200: 'ExtraLight', 300: 'Light', 400: 'Regular', 500: 'Medium',
  600: 'SemiBold', 700: 'Bold', 800: 'ExtraBold', 900: 'Black',
};

const SAMPLE = 'Їжак ґанок — 1234';

const StudioFonts: React.FC<{ fonts: StudioFont[]; onChanged: () => void }> = ({ fonts, onChanged }) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [deviceOpen, setDeviceOpen] = useState(false);
  const [device, setDevice] = useState<api.SystemFamily[]>([]);
  const [deviceBusy, setDeviceBusy] = useState(false);
  const [search, setSearch] = useState('');
  const [cyrillicOnly, setCyrillicOnly] = useState(true);
  const [importing, setImporting] = useState<string | null>(null);

  const loadDevice = async (refresh = false) => {
    setDeviceBusy(true); setError(null);
    try {
      const result = await api.fetchSystemFonts(refresh);
      setDevice(result.families);
      setDeviceOpen(true);
    } catch (reason: any) {
      setError(reason.message || 'Не вдалося прочитати шрифти пристрою');
    } finally {
      setDeviceBusy(false);
    }
  };

  const importFamily = async (family: api.SystemFamily, tokens: string[]) => {
    setImporting(family.family); setError(null); setMessage(null);
    try {
      const result = await api.importSystemFonts(tokens);
      setMessage(`${family.family}: додано накреслень — ${result.added}`
        + (result.errors.length ? `, не вдалося — ${result.errors.length}` : ''));
      onChanged();
    } catch (reason: any) {
      setError(reason.message || 'Не вдалося додати шрифт');
    } finally {
      setImporting(null);
    }
  };

  const visibleDevice = device
    .filter(family => (!cyrillicOnly || family.has_cyrillic))
    .filter(family => !search.trim()
      || family.family.toLowerCase().includes(search.trim().toLowerCase()))
    .slice(0, 120);

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
        <div className="flex items-center gap-2">
          <button type="button" className={BTN_GHOST} disabled={deviceBusy}
            onClick={() => (deviceOpen ? setDeviceOpen(false) : void loadDevice())}>
            {deviceBusy ? 'Читаю пристрій…' : deviceOpen ? 'Сховати шрифти пристрою' : 'Додати з пристрою'}
          </button>
          <label className={`${BTN_MAIN} cursor-pointer`}>
            {busy ? 'Заливаю…' : 'Залити файли'}
            <input type="file" accept=".ttf,.otf,.woff,.woff2" multiple hidden
              onChange={event => void upload(event.target.files)} />
          </label>
        </div>
      </div>

      {(message || error) && (
        <div className={`rounded-lg px-3 py-2 text-xs ${error
          ? 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300'
          : 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300'}`}>
          {error || message}
        </div>
      )}

      {deviceOpen && (
        <div className="rounded-xl border border-gray-200 p-4 dark:border-gray-700">
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-sm font-semibold text-gray-800 dark:text-gray-100">
              Шрифти цього комп'ютера
            </div>
            <span className="text-[10px] text-gray-400">знайдено родин: {device.length}</span>
            <button type="button" className="text-[10px] text-gray-400 hover:underline"
              onClick={() => void loadDevice(true)}>
              перечитати
            </button>
            <input value={search} onChange={event => setSearch(event.target.value)}
              placeholder="Пошук за назвою"
              className="ml-auto w-44 rounded-lg border border-gray-200 px-2 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-800" />
            <label className="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400">
              <input type="checkbox" checked={cyrillicOnly}
                onChange={event => setCyrillicOnly(event.target.checked)} />
              лише з кирилицею
            </label>
          </div>

          <p className="mt-2 text-[10px] leading-relaxed text-gray-400">
            Обраний шрифт копіюється в майстерню (у хмару), а не береться з диска — інакше макет
            не зібрався б на іншому комп'ютері. Родини з колекцій .ttc розбираються на окремі
            накреслення автоматично.
          </p>

          <div className="mt-3 max-h-80 space-y-1.5 overflow-auto pr-1">
            {visibleDevice.map(family => {
              const already = fonts.some(font => font.family === family.family);
              return (
                <div key={family.family}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-800">
                  <div className="min-w-0">
                    <div className="truncate text-xs text-gray-800 dark:text-gray-100">
                      {family.family}
                      {family.source === 'user' && (
                        <span className="ml-2 rounded bg-[var(--bms-accent)]/10 px-1.5 py-0.5 text-[9px] text-[var(--bms-accent)]">
                          ваш шрифт
                        </span>
                      )}
                      {already && (
                        <span className="ml-2 text-[9px] text-gray-400">уже в майстерні</span>
                      )}
                    </div>
                    <div className="mt-0.5 truncate text-[10px] text-gray-400">
                      {family.faces.map(face => `${face.weight_label}${face.italic ? ' Italic' : ''}`).join(' · ')}
                      {!family.has_cyrillic && ' · без кирилиці'}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <button type="button" className={BTN_GHOST}
                      disabled={importing === family.family}
                      onClick={() => void importFamily(family, family.faces.map(face => face.token))}>
                      {importing === family.family ? 'Додаю…' : `Додати всі (${family.faces.length})`}
                    </button>
                    {family.faces.length > 1 && (
                      <select className="rounded-lg border border-gray-200 px-2 py-1.5 text-[11px] dark:border-gray-600 dark:bg-gray-800"
                        value=""
                        onChange={event => {
                          if (!event.target.value) return;
                          void importFamily(family, [event.target.value]);
                          event.target.value = '';
                        }}>
                        <option value="">одне накреслення…</option>
                        {family.faces.map(face => (
                          <option key={face.token} value={face.token}>
                            {face.weight_label}{face.italic ? ' Italic' : ''}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                </div>
              );
            })}
            {!visibleDevice.length && (
              <div className="py-6 text-center text-[11px] text-gray-400">
                Нічого не знайдено. Зніміть галочку «лише з кирилицею» або змініть пошук.
              </div>
            )}
          </div>
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
