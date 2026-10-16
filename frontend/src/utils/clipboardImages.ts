/**
 * Фото з буфера обміну (подія `paste`): скриншот, «Скопіювати зображення» в
 * браузері чи Telegram, файли, скопійовані у Finder/Провіднику.
 *
 * Лише розбір — що робити з фото, вирішує місце вставки (картка товару).
 */

const IMAGE_EXT = /\.(jpe?g|png|webp|gif|heic|heif|tiff?|bmp|avif)$/i;

const MIME_EXT: Record<string, string> = {
  'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif',
  'image/tiff': 'tif', 'image/heic': 'heic', 'image/heif': 'heif', 'image/bmp': 'bmp',
  'image/avif': 'avif',
};

export interface ClipboardPhotos {
  /** Зображення, готові до заливки (порядок — як у буфері). */
  images: File[];
  /** Непорожній text/plain: у полі вводу вставка тексту має пріоритет. */
  hasText: boolean;
  /** Файли, що не є фото (PDF тощо) — щоб пояснити, чому нічого не сталося. */
  otherFiles: number;
  /** Фрагмент сторінки з <img>, але без самого зображення (скопійовано посилання/виділення). */
  htmlImage: boolean;
}

// Порожній (0 байт) — не фото: буває, коли програма-джерело не віддала дані.
const isImage = (f: File) => f.size > 0 && (f.type.startsWith('image/') || IMAGE_EXT.test(f.name || ''));

/** У безіменного зображення (буває у вебвʼю) — ім'я з розширенням за типом:
 *  бекенд бере з імені лише суфікс тимчасового файлу, а помилку показує за ним. */
const withName = (f: File, i: number): File => {
  if (f.name) return f;
  const ext = MIME_EXT[f.type] || 'png';
  return new File([f], `з-буфера-${i + 1}.${ext}`, { type: f.type, lastModified: f.lastModified });
};

export function readClipboardPhotos(dt: DataTransfer | null | undefined): ClipboardPhotos {
  const empty: ClipboardPhotos = { images: [], hasText: false, otherFiles: 0, htmlImage: false };
  if (!dt) return empty;
  // `files` — основне джерело. `items` — запасне: той самий знімок там теж є,
  // але getAsFile() щоразу дає НОВИЙ об'єкт, тож брати обидва = дублікати.
  let files = Array.from(dt.files || []);
  if (files.length === 0) {
    files = Array.from(dt.items || [])
      .filter((it) => it.kind === 'file')
      .map((it) => it.getAsFile())
      .filter((f): f is File => !!f);
  }
  const images = files.filter(isImage).map(withName);
  let text = '';
  let html = '';
  try { text = dt.getData('text/plain') || ''; } catch { /* тип недоступний */ }
  try { html = dt.getData('text/html') || ''; } catch { /* тип недоступний */ }
  return {
    images,
    hasText: text.trim().length > 0,
    otherFiles: files.length - images.length,
    htmlImage: images.length === 0 && /<img\b/i.test(html),
  };
}

/** Поле, куди вставка тексту йде сама собою (input/textarea/contenteditable). */
export function isEditableTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || typeof el.tagName !== 'string') return false;
  const tag = el.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = ((el as HTMLInputElement).type || 'text').toLowerCase();
    return !['button', 'checkbox', 'radio', 'submit', 'reset', 'file', 'image', 'range', 'color'].includes(type);
  }
  return !!el.isContentEditable;
}

/** «⌘V» на Mac, «Ctrl+V» деінде (Windows-збірка BMS). */
export function pasteShortcutLabel(): string {
  const p = (typeof navigator !== 'undefined' && (navigator.platform || navigator.userAgent)) || '';
  return /Mac|iPhone|iPad/i.test(p) ? '⌘V' : 'Ctrl+V';
}
