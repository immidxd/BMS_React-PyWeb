// Мініатюри фото товару.
//
// Бекенд віддає ОРИГІНАЛ за `/product-images/<шлях>?v=<версія>` (локально) або
// `/product-images-drive/<file_id>` (Drive). Оригінал важить ≈100 КБ локально і
// ≈800 КБ з Drive — а показуємо ми його в плитці 64 px або в прев'ю 264 px.
// Ці хелпери переписують URL на мініатюру потрібної ширини (`*-thumb`, WebP,
// диск-кеш на бекенді). `?v=` зберігається — заміна фото змінює URL, тож
// immutable-кеш браузера не тримає старе.
//
// Ширини мають збігатися з ALLOWED_WIDTHS у backend/services/product_thumbs.py:
// запит іншої ширини бекенд округлить угору, і кеш просто не влучить.

/** Дозволені ширини мініатюр (мають збігатися з бекендом). */
export type ThumbWidth = 96 | 320 | 640;

const LOCAL_PREFIX = '/product-images/';
const DRIVE_PREFIX = '/product-images-drive/';

/**
 * URL мініатюри для фото товару.
 * Не наш URL (зовнішній/data:) — повертаємо як є.
 */
export function thumbUrl(url: string | null | undefined, width: ThumbWidth): string {
  if (!url) return '';
  const [path, query] = url.split('?');
  let thumbPath: string;
  if (path.startsWith(LOCAL_PREFIX)) {
    thumbPath = `/product-images-thumb/${path.slice(LOCAL_PREFIX.length)}`;
  } else if (path.startsWith(DRIVE_PREFIX)) {
    thumbPath = `/product-images-drive-thumb/${path.slice(DRIVE_PREFIX.length)}`;
  } else {
    return url;
  }
  const params = new URLSearchParams(query || '');
  params.set('w', String(width));
  return `${thumbPath}?${params.toString()}`;
}

/**
 * Попередньо завантажити зображення у кеш браузера.
 * Використовується для сусідніх кадрів галереї й сусідніх карток — щоб перехід
 * ◀/▶ був миттєвим, а не «секунда очікування на кожному кроці».
 */
const _prefetched = new Set<string>();

export function prefetchImage(url: string | null | undefined): void {
  if (!url || _prefetched.has(url)) return;
  _prefetched.add(url);
  const img = new Image();
  img.decoding = 'async';
  img.src = url;
}

/** Скинути пам'ять префетчу (напр. після масової заміни фото). */
export function clearPrefetchMemory(): void {
  _prefetched.clear();
}
