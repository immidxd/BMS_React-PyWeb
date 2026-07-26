/**
 * Викачування та копіювання зображень у буфер обміну.
 *
 * Фото віддає наш же бекенд (StaticFiles-маунт або Drive-проксі), тож усі URL —
 * same-origin: `fetch` без CORS і `<a download>` працюють напряму.
 */

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

/** Викачати одне фото за URL під його оригінальним іменем. */
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
