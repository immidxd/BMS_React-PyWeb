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

import type {
  CanvasFormat, Gradient, ImageLayer, Layer, PhotoAdjust, PhotoFilter, PostSpec,
  Scrim, Shadow, StudioFont, TextLayer, Vignette,
} from './types';
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

/* ── Ефекти ─────────────────────────────────────────────────────────────── */

/** Фільтр обробки фото. Яскравість і контраст — одне перетворення:
 *  out = in·(b·c) + (0.5 − 0.5·c). Порожній рядок, якщо нічого не змінено —
 *  зайвий фільтр у SVG коштує пам'яті на кожному кадрі. */
const photoFilterDef = (id: string, filter?: PhotoFilter): string => {
  const f = { brightness: 1, contrast: 1, saturation: 1, blur: 0, ...(filter || {}) };
  const untouched = f.brightness === 1 && f.contrast === 1 && f.saturation === 1 && !f.blur;
  if (untouched) return '';
  const slope = round(f.brightness * f.contrast);
  const intercept = round(0.5 - 0.5 * f.contrast);
  const transfer = ['R', 'G', 'B'].map(channel => (
    `<feFunc${channel} type="linear" slope="${slope}" intercept="${intercept}"/>`
  )).join('');
  return (
    `<filter id="${id}" x="-15%" y="-15%" width="130%" height="130%"` +
    ` color-interpolation-filters="sRGB">` +
    `<feColorMatrix type="saturate" values="${round(f.saturation)}"/>` +
    `<feComponentTransfer>${transfer}</feComponentTransfer>` +
    (f.blur ? `<feGaussianBlur stdDeviation="${round(f.blur)}"/>` : '') +
    `</filter>`
  );
};

const shadowDef = (id: string, shadow: Shadow): string => (
  `<filter id="${id}" x="-40%" y="-40%" width="180%" height="180%"` +
  ` color-interpolation-filters="sRGB">` +
  `<feDropShadow dx="${round(shadow.dx)}" dy="${round(shadow.dy)}"` +
  ` stdDeviation="${round(shadow.blur / 2)}" flood-color="${escapeXml(shadow.color)}"` +
  ` flood-opacity="${round(shadow.opacity)}"/></filter>`
);

const gradientDef = (id: string, gradient: Gradient): string => {
  const rad = (gradient.angle * Math.PI) / 180;
  const dx = Math.cos(rad) / 2;
  const dy = Math.sin(rad) / 2;
  return (
    `<linearGradient id="${id}" x1="${round(0.5 - dx)}" y1="${round(0.5 - dy)}"` +
    ` x2="${round(0.5 + dx)}" y2="${round(0.5 + dy)}">` +
    `<stop offset="0" stop-color="${escapeXml(gradient.from)}"/>` +
    `<stop offset="1" stop-color="${escapeXml(gradient.to)}"/></linearGradient>`
  );
};

/** Геометрія кадру навколо його центру: дзеркало, поворот, нахил.
 *
 *  Порядок множників має значення: спершу дзеркалимо, потім нахиляємо, і аж
 *  тоді повертаємо. Інакше «завалений горизонт» після дзеркала виправлявся б
 *  у протилежний бік — рух мишею йшов би не туди, куди дивиться людина.
 */
const adjustTransform = (adjust: PhotoAdjust | undefined, cx: number, cy: number): string => {
  const a = { flipX: false, flipY: false, rotate: 0, tiltX: 0, tiltY: 0, ...(adjust || {}) };
  const parts: string[] = [];
  if (a.rotate) parts.push(`rotate(${round(a.rotate)})`);
  if (a.tiltX) parts.push(`skewX(${round(a.tiltX)})`);
  if (a.tiltY) parts.push(`skewY(${round(a.tiltY)})`);
  if (a.flipX || a.flipY) parts.push(`scale(${a.flipX ? -1 : 1} ${a.flipY ? -1 : 1})`);
  if (!parts.length) return '';
  return ` transform="translate(${round(cx)} ${round(cy)}) ${parts.join(' ')} translate(${round(-cx)} ${round(-cy)})"`;
};

/** Скільки «запасу» треба фото, щоб після нахилу й повороту в кадрі не
 *  з'явився порожній кут. Дешевше домалювати запас, ніж пояснювати людині,
 *  чому виправлення горизонту лишає білий трикутник. */
export const adjustOverscan = (adjust?: PhotoAdjust): number => {
  const a = { rotate: 0, tiltX: 0, tiltY: 0, ...(adjust || {}) };
  const magnitude = Math.abs(a.rotate) + Math.abs(a.tiltX) + Math.abs(a.tiltY);
  return magnitude ? 1 + Math.min(0.6, magnitude / 45) : 1;
};

const vignetteMarkup = (vignette: Vignette | undefined, format: CanvasFormat,
                        defs: string[], id: string): string => {
  if (!vignette?.enabled || vignette.strength <= 0) return '';
  // Прозорий центр → колір по краях. `softness` рухає початок затемнення:
  // менше значення = різкіше кільце.
  const start = Math.max(0.05, Math.min(0.95, 1 - vignette.softness));
  defs.push(
    `<radialGradient id="${id}" cx="0.5" cy="0.5" r="0.75">` +
    `<stop offset="${round(start)}" stop-color="${escapeXml(vignette.color)}" stop-opacity="0"/>` +
    `<stop offset="1" stop-color="${escapeXml(vignette.color)}" stop-opacity="${round(vignette.strength)}"/>` +
    `</radialGradient>`,
  );
  return `<rect x="0" y="0" width="${format.width}" height="${format.height}" fill="url(#${id})"/>`;
};

const scrimMarkup = (scrim: Scrim | undefined, format: CanvasFormat,
                     defs: string[], id: string): string => {
  if (!scrim || scrim.mode === 'none' || scrim.opacity <= 0) return '';
  const color = escapeXml(scrim.color);
  const alpha = round(scrim.opacity);
  if (scrim.mode === 'radial') {
    defs.push(
      `<radialGradient id="${id}" cx="0.5" cy="0.5" r="0.7">` +
      `<stop offset="0" stop-color="${color}" stop-opacity="${alpha}"/>` +
      `<stop offset="1" stop-color="${color}" stop-opacity="0"/></radialGradient>`,
    );
  } else {
    const stops = scrim.mode === 'top'
      ? `<stop offset="0" stop-color="${color}" stop-opacity="${alpha}"/>` +
        `<stop offset="0.55" stop-color="${color}" stop-opacity="0"/>`
      : scrim.mode === 'bottom'
        ? `<stop offset="0.45" stop-color="${color}" stop-opacity="0"/>` +
          `<stop offset="1" stop-color="${color}" stop-opacity="${alpha}"/>`
        : `<stop offset="0" stop-color="${color}" stop-opacity="${alpha}"/>` +
          `<stop offset="0.5" stop-color="${color}" stop-opacity="0"/>` +
          `<stop offset="1" stop-color="${color}" stop-opacity="${alpha}"/>`;
    defs.push(`<linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">${stops}</linearGradient>`);
  }
  return `<rect x="0" y="0" width="${format.width}" height="${format.height}" fill="url(#${id})"/>`;
};

/* ── Фон ────────────────────────────────────────────────────────────────── */

type Resources = {
  /** Адреса або data:-URI фото за його id. */
  assetHref: (assetId: number) => string | null;
  assetSize: (assetId: number) => { width: number; height: number } | null;
  fonts: StudioFont[];
  /** Вшиті шрифти (base64) — лише для експорту. */
  fontFaces?: string;
};

const backgroundMarkup = (
  spec: PostSpec, format: CanvasFormat, res: Resources, defs: string[],
): string => {
  const bg = spec.background;
  const base = `<rect x="0" y="0" width="${format.width}" height="${format.height}" fill="${escapeXml(bg.color)}"/>`;
  if (bg.type !== 'asset' || !bg.assetId) return base;

  const href = res.assetHref(bg.assetId);
  const size = res.assetSize(bg.assetId);
  if (!href || !size) return base;

  // Розкладка «як object-fit»: рахуємо самі, бо preserveAspectRatio не вміє
  // ані масштабу від людини, ані зсуву.
  const cover = Math.max(format.width / size.width, format.height / size.height);
  const contain = Math.min(format.width / size.width, format.height / size.height);
  // Запас під поворот і нахил: без нього виправлений горизонт лишає в куті
  // порожній трикутник.
  const overscan = adjustOverscan(bg.adjust);
  const scale = (bg.fit === 'cover' ? cover : contain) * (bg.scale || 1) * overscan;
  const width = size.width * scale;
  const height = size.height * scale;
  const x = (format.width - width) / 2 + (bg.offsetX || 0);
  const y = (format.height - height) / 2 + (bg.offsetY || 0);

  const filterDef = photoFilterDef('bgFilter', bg.filter);
  if (filterDef) defs.push(filterDef);
  const filterAttr = filterDef ? ' filter="url(#bgFilter)"' : '';
  const geometry = adjustTransform(bg.adjust, format.width / 2, format.height / 2);

  const overlay = bg.overlayOpacity > 0
    ? `<rect x="0" y="0" width="${format.width}" height="${format.height}" fill="${escapeXml(bg.overlay)}" opacity="${round(bg.overlayOpacity)}"/>`
    : '';
  // Порядок шарів фону: фото → рівномірне затемнення → градієнт під текст →
  // віньєтка. Віньєтка остання навмисно: вона має обрамляти вже готовий кадр,
  // а не ховатись під підкладкою заголовка.
  const scrim = scrimMarkup(bg.scrim, format, defs, 'bgScrim');
  const vignette = vignetteMarkup(bg.vignette, format, defs, 'bgVignette');
  // Фон завжди підрізаний полотном: із масштабом і зсувом фото свідомо більше
  // за кадр, і без обрізки воно вилазило б за межі SVG.
  return (
    `${base}<svg x="0" y="0" width="${format.width}" height="${format.height}"` +
    ` viewBox="0 0 ${format.width} ${format.height}" overflow="hidden">` +
    `<image href="${href}" x="${round(x)}" y="${round(y)}" width="${round(width)}"` +
    ` height="${round(height)}" preserveAspectRatio="none"${filterAttr}${geometry}/></svg>` +
    `${overlay}${scrim}${vignette}`
  );
};

/* ── Шари ───────────────────────────────────────────────────────────────── */

const transform = (layer: Layer, width: number, height: number): string => {
  if (!layer.rotation) return '';
  const cx = round(layer.x + width / 2);
  const cy = round(layer.y + height / 2);
  return ` transform="rotate(${round(layer.rotation)} ${cx} ${cy})"`;
};

const textMarkup = (layer: TextLayer, defs: string[]): string => {
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

  const common =
    `font-family="${escapeXml(fontStack(layer.fontFamily))}" font-size="${round(layer.fontSize)}"` +
    ` font-weight="${layer.fontWeight}" font-style="${layer.fontStyle}"` +
    ` letter-spacing="${round(layer.letterSpacing)}" text-anchor="${anchor}"`;

  const gradient = layer.fillType === 'gradient' ? layer.gradient : null;
  if (gradient) defs.push(gradientDef(`grad_${layer.id}`, gradient));
  const fill = gradient ? `url(#grad_${layer.id})` : escapeXml(layer.color);

  const stroke = layer.stroke?.enabled && layer.stroke.width > 0
    // paint-order: обведення малюється ПІД заливкою, інакше воно з'їдає
    // половину товщини літери й шрифт «худне».
    ? ` stroke="${escapeXml(layer.stroke.color)}" stroke-width="${round(layer.stroke.width)}"` +
      ` stroke-linejoin="round" paint-order="stroke"`
    : '';

  const decoration = layer.decoration !== 'none' ? ` text-decoration="${layer.decoration}"` : '';

  // «3D» — копії тексту, зміщені під кутом. Малюються ПЕРЕД основним, тому
  // читається як товща літери, а не як розмита тінь.
  let extruded = '';
  if (layer.extrude?.enabled && layer.extrude.depth > 0) {
    const rad = (layer.extrude.angle * Math.PI) / 180;
    const steps = Math.min(40, Math.round(layer.extrude.depth));
    for (let index = steps; index >= 1; index -= 1) {
      const dx = round(Math.cos(rad) * index);
      const dy = round(Math.sin(rad) * index);
      extruded += (
        `<g transform="translate(${dx} ${dy})">` +
        `<text ${common} fill="${escapeXml(layer.extrude.color)}">${tspans}</text></g>`
      );
    }
  }

  const main = `<text ${common} fill="${fill}"${stroke}${decoration}>${tspans}</text>`;

  const shadow = layer.shadow?.enabled ? layer.shadow : null;
  if (shadow) defs.push(shadowDef(`shadow_${layer.id}`, shadow));
  const filterAttr = shadow ? ` filter="url(#shadow_${layer.id})"` : '';

  return (
    `<g opacity="${round(layer.opacity)}"${filterAttr}` +
    `${transform(layer, layer.width, textBlockHeight(layer))}>${extruded}${main}</g>`
  );
};

const imageMarkup = (layer: ImageLayer, res: Resources, defs: string[]): string => {
  const href = res.assetHref(layer.assetId);
  if (!href) return '';
  const clip = layer.radius > 0 ? ` clip-path="url(#clip_${layer.id})"` : '';
  if (layer.radius > 0) {
    defs.push(
      `<clipPath id="clip_${layer.id}"><rect x="${round(layer.x)}" y="${round(layer.y)}"` +
      ` width="${round(layer.width)}" height="${round(layer.height)}" rx="${round(layer.radius)}"/></clipPath>`,
    );
  }
  const filterDef = photoFilterDef(`pf_${layer.id}`, layer.filter);
  if (filterDef) defs.push(filterDef);
  const filterAttr = filterDef ? ` filter="url(#pf_${layer.id})"` : '';
  // Дзеркало окремим обгортанням: обрізка (`clip-path`) задана в координатах
  // полотна, і якби відображення застосувалось разом із нею, разом із фото
  // перевернулася б і сама рамка.
  const mirrored = layer.flipX || layer.flipY;
  const image =
    `<image href="${href}" x="${round(layer.x)}" y="${round(layer.y)}"` +
    ` width="${round(layer.width)}" height="${round(layer.height)}"` +
    ` opacity="${round(layer.opacity)}" preserveAspectRatio="xMidYMid slice"` +
    `${filterAttr}${mirrored ? adjustTransform(
      { flipX: Boolean(layer.flipX), flipY: Boolean(layer.flipY), rotate: 0, tiltX: 0, tiltY: 0 },
      layer.x + layer.width / 2, layer.y + layer.height / 2) : ''}/>`;
  return `<g${clip}${transform(layer, layer.width, layer.height)}>${image}</g>`;
};

/* ── Складання документа ────────────────────────────────────────────────── */

export const buildSvg = (spec: PostSpec, format: CanvasFormat, res: Resources): string => {
  // Визначення (градієнти, фільтри, обрізки) збираються під час обходу шарів:
  // так ефект і його опис не можуть роз'їхатись.
  const defs: string[] = [];
  const background = backgroundMarkup(spec, format, res, defs);
  const body = spec.layers.map(layer => (
    layer.type === 'text' ? textMarkup(layer, defs) : imageMarkup(layer, res, defs)
  )).join('');
  const style = res.fontFaces ? `<style type="text/css">${res.fontFaces}</style>` : '';
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"` +
    ` width="${format.width}" height="${format.height}"` +
    ` viewBox="0 0 ${format.width} ${format.height}">` +
    `<defs>${style}${defs.join('')}</defs>${background}${body}</svg>`
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
