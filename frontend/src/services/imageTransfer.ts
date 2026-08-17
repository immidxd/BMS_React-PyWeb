/**
 * Викачування та копіювання зображень у буфер обміну.
 *
 * Фото віддає наш же бекенд (StaticFiles-маунт або Drive-проксі), тож усі URL —
 * same-origin: `fetch` без CORS працює напряму.
 *
 * ⚠️ ДВА РІЗНІ ШЛЯХИ ЗБЕРЕЖЕННЯ, і це не примха:
 *
 *  • **Десктоп-застосунок (PyWebView)** — файл записує БЕКЕНД у теку
 *    «Завантаження». У вбудованому вебв'ю атрибут `<a download>` не працює:
 *    клік не зберігає файл, а ПЕРЕХОДИТЬ на нього. Фото через це розгорталось
 *    на весь екран поверх застосунку й блокувало роботу, а .zip вебв'ю показати
 *    не може — тому «завантажити все» лише вдавало процес і зникало в нікуди.
 *    Бекенд у цьому режимі працює на тій самій машині, тож запис на диск — і є
 *    правильна відповідь. UI показує повний шлях, куди збережено.
 *
 *  • **Звичайний браузер** — штатне `<a download>`, як і було.
 *
 * Режим бекенд повідомляє полем `desktop` у /api/runtime-config (його ставить
 * лаунчер main.py). Не вгадуємо за user-agent: WKWebView прикидається Safari, а
 * властивість `a.download` у ньому ВИЗНАЧЕНА — просто не діє. Feature detection
 * тут дає хибнопозитив, тому питаємо бекенд.
 */

export interface SavedFile {
  /** Повний шлях на диску (лише десктоп-режим; у браузері null). */
  path: string | null;
  /** Підсумкове ім'я файлу. */
  filename: string;
}

// Режим питаємо ОДИН раз за сесію і кешуємо проміс — збереження не має чекати
// на повторний round-trip, а прапор у межах сесії не змінюється.
let _desktopProbe: Promise<boolean> | null = null;

export function isDesktopShell(): Promise<boolean> {
  if (!_desktopProbe) {
    _desktopProbe = fetch('/api/runtime-config')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => Boolean(d?.desktop))
      // Бекенд не відповів — вважаємо браузером: там `<a download>` принаймні
      // щось робить, тоді як зайвий запис на диск був би несподіванкою.
      .catch(() => false);
  }
  return _desktopProbe;
}

/** Зберегти blob як файл (у теку завантажень браузера). */
export function saveBlob(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Даємо браузеру перехопити клік перед відкликанням URL.
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}

async function postJson(url: string): Promise<any> {
  const res = await fetch(url, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
  return data;
}

/**
 * Зберегти ОДНЕ фото товару. Повертає, куди саме збережено, — щоб UI міг це
 * показати, а не лишати людину гадати, спрацювало чи ні.
 */
export async function saveProductPhoto(
  productId: number, filename: string, url: string,
): Promise<SavedFile> {
  if (await isDesktopShell()) {
    const d = await postJson(
      `/api/products/${productId}/photos/save-one?filename=${encodeURIComponent(filename)}`,
    );
    return { path: d.path ?? null, filename: d.filename || filename };
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  saveBlob(await res.blob(), filename);
  return { path: null, filename };
}

/** Зберегти ВСІ фото товару одним .zip. */
export async function saveProductPhotosZip(
  productId: number, kind: 'all' | 'official' | 'real' | 'defect' = 'all',
): Promise<SavedFile & { count: number }> {
  if (await isDesktopShell()) {
    const d = await postJson(`/api/products/${productId}/photos/save-zip?kind=${kind}`);
    return { path: d.path ?? null, filename: d.filename, count: d.count ?? 0 };
  }
  const res = await fetch(`/api/products/${productId}/photos/download?kind=${kind}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.detail || `HTTP ${res.status}`);
  }
  const name = filenameFromDisposition(res.headers.get('content-disposition'))
    || `product-${productId}-photos.zip`;
  const count = Number(res.headers.get('x-photo-count')) || 0;
  saveBlob(await res.blob(), name);
  return { path: null, filename: name, count };
}

/**
 * Зберегти сітку-підбірку як звичайний JPEG.
 *
 * Той самий renderer, що піде в публікацію, — збережений файл і опублікований
 * банер не можуть розійтися. Публікації при цьому не відбувається.
 */
export async function saveCollectionCollage(
  spec: Record<string, unknown>,
): Promise<SavedFile & { grid?: string }> {
  if (await isDesktopShell()) {
    const res = await fetch('/api/publications/collections/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(spec),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
    return { path: data.path ?? null, filename: data.filename, grid: data.grid };
  }
  const res = await fetch('/api/publications/collections/render', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.detail || `HTTP ${res.status}`);
  }
  const stamp = new Date().toISOString().slice(0, 16).replace('T', ' ').replace(':', '-');
  const platform = spec.platform === 'facebook' ? 'Facebook' : 'Viber';
  const name = `Підбірка ${platform} ${stamp}.jpeg`;
  saveBlob(await res.blob(), name);
  return { path: null, filename: name, grid: res.headers.get('X-BMS-Grid') || undefined };
}

/** Викачати одне фото за URL під його оригінальним іменем (шлях для браузера). */
export async function downloadImage(url: string, filename: string): Promise<void> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  saveBlob(await res.blob(), filename);
}

/** Перекодувати зображення в PNG — єдиний формат, який приймає буфер обміну. */
async function toPngBlob(blob: Blob): Promise<Blob> {
  if (blob.type === 'image/png') return blob;
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas 2d context unavailable');
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close?.();
  const png = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
  if (!png) throw new Error('PNG encode failed');
  return png;
}

async function fetchAsPng(url: string): Promise<Blob> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return toPngBlob(await res.blob());
}

/**
 * Скопіювати фото в буфер обміну.
 * Повертає 'image' — вставиться саме картинка; 'url' — фолбек (лише посилання),
 * коли браузер не дає писати зображення в буфер.
 */
export async function copyImageToClipboard(url: string): Promise<'image' | 'url'> {
  const clip = navigator.clipboard as any;
  if (clip?.write && typeof ClipboardItem !== 'undefined') {
    // Варіант з Promise всередині ClipboardItem зберігає user-gesture (Safari);
    // якщо браузер його не підтримує — дочитуємо blob і пишемо звичайним шляхом.
    try {
      await clip.write([new ClipboardItem({ 'image/png': fetchAsPng(url) })]);
      return 'image';
    } catch {
      try {
        await clip.write([new ClipboardItem({ 'image/png': await fetchAsPng(url) })]);
        return 'image';
      } catch {
        /* падаємо у фолбек нижче */
      }
    }
  }
  const absolute = new URL(url, window.location.origin).href;
  if (clip?.writeText) {
    await clip.writeText(absolute);
    return 'url';
  }
  throw new Error('clipboard unavailable');
}

/** Ім'я файлу з заголовка Content-Disposition (підтримує RFC 5987 `filename*`). */
export function filenameFromDisposition(disposition?: string | null): string | null {
  if (!disposition) return null;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (star) {
    try { return decodeURIComponent(star[1]); } catch { /* ignore */ }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1] : null;
}
