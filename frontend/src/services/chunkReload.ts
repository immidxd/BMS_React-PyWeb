/**
 * Відновлення після перезбірки фронтенду.
 *
 * Проблема: кожна збірка дає чанкам нові хеші (`main.70c5a967.js`), старі файли
 * зникають. Вкладка, відкрита ДО деплою, тримає стару `index.html` і при переході
 * на ліниво завантажену сторінку просить чанк, якого вже нема → 404 → сторінка
 * висить на спінері «Завантаження сторінки…».
 *
 * Рішення (три рівні, щоб вічного спінера не було ніколи):
 *   1. `lazyWithRetry` — одна повторна спроба імпорту (мережевий збій);
 *   2. якщо не вийшло — ОДНЕ перезавантаження (`index.html` віддається no-cache,
 *      тож вкладка підхоплює свіжі хеші);
 *   3. якщо й після перезавантаження не працює — помилка показується користувачу
 *      (див. PageBoundary), а не ховається за спінером.
 */
import React from 'react';

const RELOAD_KEY = 'bms-chunk-reload-at';
/** Захист від циклу: не частіше одного авто-перезавантаження на 60 с. */
const RELOAD_COOLDOWN_MS = 60_000;

const CHUNK_ERROR_RE = /(loading|fetch(ing)?)\s+(css\s+)?chunk|chunkloaderror|dynamically imported module|importing a module script failed/i;

/** Чи схожа помилка на «чанк не завантажився» (а не звичайний баг у коді). */
export function isChunkLoadError(err: unknown): boolean {
  if (!err) return false;
  const name = (err as any)?.name || '';
  const msg = (err as any)?.message || String(err);
  return name === 'ChunkLoadError' || CHUNK_ERROR_RE.test(`${name} ${msg}`);
}

/**
 * Перезавантажити вкладку через застарілу збірку. Повертає `false`, якщо
 * перезавантаження щойно вже було — тоді проблема не в хешах і треба показати
 * помилку користувачу, а не крутити reload по колу.
 */
export function reloadForNewBuild(reason: string): boolean {
  let last = 0;
  try { last = Number(sessionStorage.getItem(RELOAD_KEY) || 0); } catch { /* приватний режим */ }
  if (Date.now() - last < RELOAD_COOLDOWN_MS) {
    console.warn(`[chunk] пропускаю перезавантаження (${reason}) — щойно вже перезавантажувались`);
    return false;
  }
  try { sessionStorage.setItem(RELOAD_KEY, String(Date.now())); } catch { /* ignore */ }
  console.warn(`[chunk] застаріла збірка (${reason}) — перезавантажую сторінку`);
  window.location.reload();
  return true;
}

/** `React.lazy` з повтором і аварійним перезавантаженням. */
export function lazyWithRetry<T extends React.ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
): React.LazyExoticComponent<T> {
  return React.lazy(async () => {
    try {
      return await factory();
    } catch (err) {
      if (!isChunkLoadError(err)) throw err;   // справжня помилка модуля — не ховаємо
      await new Promise((r) => setTimeout(r, 400));
      try {
        return await factory();                // друга спроба: мережевий збій
      } catch (err2) {
        reloadForNewBuild('lazy-import');      // 404 після деплою → свіжа index.html
        throw err2;
      }
    }
  });
}

/**
 * Глобальний перехоплювач для чанків, що вантажаться поза `React.lazy`
 * (теги <script>/<link> зі старої index.html, відкладені імпорти бібліотек).
 */
export function installChunkErrorRecovery(): void {
  window.addEventListener('unhandledrejection', (e) => {
    if (isChunkLoadError(e.reason)) reloadForNewBuild('unhandled-rejection');
  });

  window.addEventListener('error', (e) => {
    const el = e.target as HTMLElement | null;
    // Помилка завантаження ресурсу (не JS-виняток): у події немає `error`,
    // натомість target — тег скрипта/стилю. Реагуємо лише на власні бандли.
    if (el && el !== (window as any) && (el.tagName === 'SCRIPT' || el.tagName === 'LINK')) {
      const src = (el as HTMLScriptElement).src || (el as HTMLLinkElement).href || '';
      if (src.includes('/static/')) reloadForNewBuild(`asset ${src.split('/').pop()}`);
      return;
    }
    if (isChunkLoadError((e as ErrorEvent).error || (e as ErrorEvent).message)) {
      reloadForNewBuild('window-error');
    }
  }, true);
}
