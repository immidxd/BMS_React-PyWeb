/**
 * Макет → SVG → растр.
 *
 * Один-єдиний рендер на всю майстерню. Те, що людина бачить у редакторі, — це
 * той самий SVG-рядок, який потім лягає в PNG; різниця лише в тому, що для
 * експорту шрифти й фото вшиваються в документ як base64. Зроблено так
 * навмисно: окремий «фінальний» рендер (хоч на PIL, хоч на canvas) неминуче
 * розходився б із прев'ю в переносах рядків, кернінгу й тінях — і кожна така
 * дрібниця виглядає як «на прев'ю було не так».
 *
 * Чому байти, а не URL: SVG, який малюється в canvas, не має права тягнути
 * зовнішні файли — картинка просто не намалюється, а canvas стане «отруєним».
 * Тому все, що потрібне кадру, приходить усередині документа.
 */

import type { CanvasFormat, ImageLayer, Layer, PostSpec, StudioFont, TextLayer } from './types';
import { FALLBACK_FAMILY } from './types';

/* ── Дрібні утиліти ─────────────────────────────────────────────────────── */

const escapeXml = (value: string): string => value
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&apos;');

const round = (value: number): number => Math.round(value * 100) / 100;

/** Родина для CSS/canvas: фірмова + системний запас (кирилиця не має зникнути,
 *  якщо у шрифті раптом немає гліфа). */
export const fontStack = (family: string): string =>
  family ? `"${family}", ${FALLBACK_FAMILY}` : FALLBACK_FAMILY;

/* ── Вимірювання й переноси ─────────────────────────────────────────────── */

let measureCtx: CanvasRenderingContext2D | null = null;

const context = (): CanvasRenderingContext2D => {
  if (!measureCtx) {
    measureCtx = document.createElement('canvas').getContext('2d');
  }
  if (!measureCtx) throw new Error('Canvas недоступний — рендер неможливий');
  return measureCtx;
};

const applyFont = (ctx: CanvasRenderingContext2D, layer: TextLayer): void => {
  ctx.font = `${layer.fontStyle} ${layer.fontWeight} ${layer.fontSize}px ${fontStack(layer.fontFamily)}`;
};

const measure = (ctx: CanvasRenderingContext2D, value: string, layer: TextLayer): number => {
  const base = ctx.measureText(value).width;
  // Міжлітерний інтервал canvas не враховує — додаємо самі, інакше довгий
  // розріджений заголовок «влізе» в прев'ю й вилізе за край у растрі.
  const extra = Math.max(0, value.length - 1) * layer.letterSpacing;
  return base + extra;
};

export const layerText = (layer: TextLayer): string =>
  layer.uppercase ? layer.text.toLocaleUpperCase('uk-UA') : layer.text;

/** Розбиття на рядки: спершу власні переноси, далі — по словах у ширину шару. */
export const wrapLines = (layer: TextLayer): string[] => {
  const ctx = context();
  applyFont(ctx, layer);
  const result: string[] = [];
  for (const paragraph of layerText(layer).split('\n')) {
    if (!paragraph.trim()) { result.push(''); continue; }
    let line = '';
    for (const word of paragraph.split(/\s+/)) {
      const candidate = line ? `${line} ${word}` : word;
      if (line && measure(ctx, candidate, layer) > layer.width) {
        result.push(line);
        line = word;
      } else {
        line = candidate;
      }
    }
    result.push(line);
  }
  return result;
};

/** Висота текстового блоку в пікселях полотна — потрібна і рамці виділення,
 *  і перевірці «чи вліз текст у кадр». */
export const textBlockHeight = (layer: TextLayer): number =>
  wrapLines(layer).length * layer.fontSize * layer.lineHeight;

/* ── Фон ────────────────────────────────────────────────────────────────── */

type Resources = {
  /** Адреса або data:-URI фото за його id. */
  assetHref: (assetId: number) => string | null;
  assetSize: (assetId: number) => { width: number; height: number } | null;
  fonts: StudioFont[];
  /** Вшиті шрифти (base64) — лише для експорту. */
  fontFaces?: string;
};

const backgroundMarkup = (spec: PostSpec, format: CanvasFormat, res: Resources): string => {
  const { background: bg } = spec;
  const base = `<rect x="0" y="0" width="${format.width}" height="${format.height}" fill="${escapeXml(bg.color)}"/>`;
  if (bg.type !== 'asset' || !bg.assetId) return base;

  const href = res.assetHref(bg.assetId);
  const size = res.assetSize(bg.assetId);
  if (!href || !size) return base;

  // Розкладка «як object-fit»: рахуємо самі, бо preserveAspectRatio не вміє
  // ані масштабу від людини, ані зсуву.
  const cover = Math.max(format.width / size.width, format.height / size.height);
  const contain = Math.min(format.width / size.width, format.height / size.height);
  const scale = (bg.fit === 'cover' ? cover : contain) * (bg.scale || 1);
  const width = size.width * scale;
  const height = size.height * scale;
  const x = (format.width - width) / 2 + (bg.offsetX || 0);
  const y = (format.height - height) / 2 + (bg.offsetY || 0);

  const overlay = bg.overlayOpacity > 0
    ? `<rect x="0" y="0" width="${format.width}" height="${format.height}" fill="${escapeXml(bg.overlay)}" opacity="${round(bg.overlayOpacity)}"/>`
    : '';
  return `${base}<image href="${href}" x="${round(x)}" y="${round(y)}" width="${round(width)}" height="${round(height)}" preserveAspectRatio="none"/>${overlay}`;
};

/* ── Шари ───────────────────────────────────────────────────────────────── */

const transform = (layer: Layer, width: number, height: number): string => {
  if (!layer.rotation) return '';
  const cx = round(layer.x + width / 2);
  const cy = round(layer.y + height / 2);
  return ` transform="rotate(${round(layer.rotation)} ${cx} ${cy})"`;
};

const textMarkup = (layer: TextLayer): string => {
  const lines = wrapLines(layer);
  const step = layer.fontSize * layer.lineHeight;
  const anchor = layer.align === 'center' ? 'middle' : layer.align === 'right' ? 'end' : 'start';
  const anchorX = layer.align === 'center'
    ? layer.x + layer.width / 2
    : layer.align === 'right' ? layer.x + layer.width : layer.x;
  // Базова лінія першого рядка: ~78% кегля від верху рядкового боксу. Число
  // наближене, але ОДНЕ і для прев'ю, і для растру — тому кадр збігається.
  const firstBaseline = layer.y + layer.fontSize * 0.78;
  const tspans = lines.map((line, index) => (
    `<tspan x="${round(anchorX)}" y="${round(firstBaseline + index * step)}">${escapeXml(line || ' ')}</tspan>`
  )).join('');
  const decoration = layer.decoration !== 'none'
    ? ` text-decoration="${layer.decoration}"` : '';
  return (
    `<text font-family="${escapeXml(fontStack(layer.fontFamily))}" font-size="${round(layer.fontSize)}"` +
    ` font-weight="${layer.fontWeight}" font-style="${layer.fontStyle}"` +
    ` letter-spacing="${round(layer.letterSpacing)}" text-anchor="${anchor}"` +
    ` fill="${escapeXml(layer.color)}" opacity="${round(layer.opacity)}"${decoration}` +
    `${transform(layer, layer.width, textBlockHeight(layer))}>${tspans}</text>`
  );
};

const imageMarkup = (layer: ImageLayer, res: Resources, index: number): string => {
  const href = res.assetHref(layer.assetId);
  if (!href) return '';
  const clip = layer.radius > 0 ? ` clip-path="url(#clip${index})"` : '';
  const defs = layer.radius > 0
    ? `<clipPath id="clip${index}"><rect x="${round(layer.x)}" y="${round(layer.y)}" width="${round(layer.width)}" height="${round(layer.height)}" rx="${round(layer.radius)}"/></clipPath>`
    : '';
  return (
    `${defs}<image href="${href}" x="${round(layer.x)}" y="${round(layer.y)}"` +
    ` width="${round(layer.width)}" height="${round(layer.height)}"` +
    ` opacity="${round(layer.opacity)}" preserveAspectRatio="xMidYMid slice"${clip}` +
    `${transform(layer, layer.width, layer.height)}/>`
  );
};

/* ── Складання документа ────────────────────────────────────────────────── */

export const buildSvg = (spec: PostSpec, format: CanvasFormat, res: Resources): string => {
  const body = spec.layers.map((layer, index) => (
    layer.type === 'text' ? textMarkup(layer) : imageMarkup(layer, res, index)
  )).join('');
  const style = res.fontFaces ? `<defs><style type="text/css">${res.fontFaces}</style></defs>` : '';
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"` +
    ` width="${format.width}" height="${format.height}"` +
    ` viewBox="0 0 ${format.width} ${format.height}">` +
    `${style}${backgroundMarkup(spec, format, res)}${body}</svg>`
  );
};

/* ── Ресурси для експорту ───────────────────────────────────────────────── */

const dataUrlCache = new Map<string, string>();

const fetchDataUrl = async (url: string): Promise<string> => {
  const cached = dataUrlCache.get(url);
  if (cached) return cached;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Не вдалося прочитати ${url}`);
  const blob = await response.blob();
  const encoded = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('Файл не прочитано'));
    reader.readAsDataURL(blob);
  });
  dataUrlCache.set(url, encoded);
  return encoded;
};

/** `@font-face` з вшитими байтами — без них растр вийде системним шрифтом,
 *  і фірмовий макет перестане бути фірмовим. */
export const buildFontFaces = async (fonts: StudioFont[]): Promise<string> => {
  const chunks = await Promise.all(fonts.map(async font => {
    try {
      const encoded = await fetchDataUrl(font.src);
      return (
        `@font-face{font-family:"${font.family}";font-weight:${font.weight};` +
        `font-style:${font.style};src:url(${encoded});}`
      );
    } catch {
      return '';
    }
  }));
  return chunks.join('');
};

/** Усі фото, задіяні в макеті, як data:-URI. */
export const collectAssetDataUrls = async (
  spec: PostSpec,
  assetSrc: (assetId: number) => string | null,
): Promise<Map<number, string>> => {
  const ids = new Set<number>();
  if (spec.background.type === 'asset' && spec.background.assetId) {
    ids.add(spec.background.assetId);
  }
  spec.layers.forEach(layer => { if (layer.type === 'image') ids.add(layer.assetId); });

  const pairs = await Promise.all(Array.from(ids).map(async id => {
    const src = assetSrc(id);
    if (!src) return null;
    try { return [id, await fetchDataUrl(src)] as const; } catch { return null; }
  }));
  const map = new Map<number, string>();
  pairs.forEach(pair => { if (pair) map.set(pair[0], pair[1]); });
  return map;
};

/* ── Растр ──────────────────────────────────────────────────────────────── */

export const svgToPngBlob = (svg: string, format: CanvasFormat): Promise<Blob> =>
  new Promise((resolve, reject) => {
    const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      const canvas = document.createElement('canvas');
      canvas.width = format.width;
      canvas.height = format.height;
      const ctx = canvas.getContext('2d');
      if (!ctx) { reject(new Error('Canvas недоступний')); return; }
      ctx.drawImage(image, 0, 0, format.width, format.height);
      canvas.toBlob(
        result => result ? resolve(result) : reject(new Error('Растр не зібрався')),
        'image/png',
      );
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('Кадр не намалювався — перевірте фон і шрифти'));
    };
    image.src = url;
  });
