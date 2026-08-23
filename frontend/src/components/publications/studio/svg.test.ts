/**
 * Рендер макета в SVG. Під тестом саме те, що ламається тихо: переноси рядків
 * (від них залежить висота блоку й прилипання), і присутність ефектів у
 * розмітці — бо помітно це аж у готовому кадрі, коли пост уже зібрано.
 *
 * Canvas у jsdom немає, а `wrapLines` без нього не міряє текст — підставляємо
 * передбачуване вимірювання: рівно 10 px на символ. Тоді «скільки слів влізе»
 * стає арифметикою, а не залежністю від шрифтів машини.
 */

import { adjustOverscan, buildSvg, textBlockHeight, wrapLines } from './svg';
import {
  CanvasFormat, DEFAULT_ADJUST, DEFAULT_EXTRUDE, DEFAULT_GRADIENT,
  DEFAULT_PHOTO_FILTER, DEFAULT_SCRIM, DEFAULT_SHADOW, DEFAULT_STROKE,
  DEFAULT_VIGNETTE, PostSpec, TextLayer, normalizeSpec,
} from './types';

beforeAll(() => {
  (HTMLCanvasElement.prototype as any).getContext = () => ({
    font: '',
    measureText: (value: string) => ({ width: value.length * 10 }),
  });
});

const FORMAT: CanvasFormat = { key: 'story', label: 'Сторіс', width: 1080, height: 1920 };

const textLayer = (patch: Partial<TextLayer> = {}): TextLayer => ({
  id: 't1', type: 'text', role: 'title', text: 'ЗАГОЛОВОК',
  x: 80, y: 200, width: 900, rotation: 0, opacity: 1,
  fontFamily: '', fontWeight: 700, fontStyle: 'normal', fontSize: 100,
  lineHeight: 1.1, letterSpacing: 0, align: 'left', color: '#4E2358',
  decoration: 'none', uppercase: false,
  fillType: 'solid',
  gradient: { ...DEFAULT_GRADIENT },
  shadow: { ...DEFAULT_SHADOW },
  stroke: { ...DEFAULT_STROKE },
  extrude: { ...DEFAULT_EXTRUDE },
  ...patch,
});

const specWith = (layer: TextLayer): PostSpec => ({
  version: 1, format: 'story',
  background: {
    type: 'color', color: '#FFFFFF', assetId: null, fit: 'cover', scale: 1,
    offsetX: 0, offsetY: 0, overlay: '#000000', overlayOpacity: 0,
    filter: { ...DEFAULT_PHOTO_FILTER },
    adjust: { ...DEFAULT_ADJUST },
    vignette: { ...DEFAULT_VIGNETTE },
    scrim: { ...DEFAULT_SCRIM },
  },
  layers: [layer],
});

const resources = {
  assetHref: () => null,
  assetSize: () => null,
  fonts: [],
};

const render = (layer: TextLayer) => buildSvg(specWith(layer), FORMAT, resources);

/* ── Переноси ───────────────────────────────────────────────────────────── */

test('текст переноситься по словах у ширину шару', () => {
  // Ширина 200 px = 20 символів; кожне слово по 5 символів (50 px).
  const lines = wrapLines(textLayer({ text: 'аааа бббб вввв гггг дддд', width: 200 }));
  expect(lines.length).toBeGreaterThan(1);
  lines.forEach(line => expect(line.length * 10).toBeLessThanOrEqual(200));
});

test('власний перенос рядка зберігається', () => {
  expect(wrapLines(textLayer({ text: 'перший\nдругий' }))).toEqual(['перший', 'другий']);
});

test('міжлітерний інтервал враховується у переносах', () => {
  // 11 символів = 110 px, у 120 вміщається одним рядком.
  const narrow = { text: 'ааааа ббббб', width: 120 };
  expect(wrapLines(textLayer(narrow)).length).toBe(1);
  // Розрідження додає 10×20 px — той самий рядок уже не влазить. Саме це й
  // ловить тест: canvas міжлітерний інтервал не міряє, ми додаємо його самі.
  expect(wrapLines(textLayer({ ...narrow, letterSpacing: 20 })).length).toBe(2);
});

test('висота блоку = рядки × кегль × міжрядковий', () => {
  const layer = textLayer({ text: 'один\nдва', fontSize: 100, lineHeight: 1.2 });
  expect(textBlockHeight(layer)).toBeCloseTo(2 * 100 * 1.2);
});

test('ВЕЛИКІ літери застосовуються до рендеру, а не лише на екрані', () => {
  expect(render(textLayer({ text: 'напис', uppercase: true }))).toContain('НАПИС');
});

/* ── Ефекти ─────────────────────────────────────────────────────────────── */

test('градієнтна заливка додає визначення і посилається на нього', () => {
  const svg = render(textLayer({ fillType: 'gradient' }));
  expect(svg).toContain('<linearGradient id="grad_t1"');
  expect(svg).toContain('fill="url(#grad_t1)"');
});

test('суцільна заливка не тягне зайвих визначень', () => {
  const svg = render(textLayer());
  expect(svg).not.toContain('linearGradient');
  expect(svg).not.toContain('feDropShadow');
});

test('тінь малюється фільтром із заданими зсувом і силою', () => {
  const svg = render(textLayer({
    shadow: { enabled: true, color: '#000000', dx: 10, dy: 16, blur: 24, opacity: 0.7 },
  }));
  expect(svg).toContain('<feDropShadow dx="10" dy="16"');
  // stdDeviation = половина розмиття: у SVG це радіус, а не діаметр.
  expect(svg).toContain('stdDeviation="12"');
  expect(svg).toContain('filter="url(#shadow_t1)"');
});

test('обведення малюється під заливкою, інакше шрифт «худне»', () => {
  const svg = render(textLayer({
    stroke: { enabled: true, color: '#FFFFFF', width: 14 },
  }));
  expect(svg).toContain('stroke-width="14"');
  expect(svg).toContain('paint-order="stroke"');
});

test('обʼєм додає стільки копій, скільки задано глибини', () => {
  const svg = render(textLayer({
    extrude: { enabled: true, depth: 6, angle: 45, color: '#0A84FF' },
  }));
  // 6 зміщених копій + основний текст.
  expect(svg.match(/<text /g)?.length).toBe(7);
});

test('вимкнені ефекти не лишають слідів у розмітці', () => {
  const svg = render(textLayer({
    stroke: { enabled: false, color: '#FFFFFF', width: 40 },
    extrude: { enabled: false, depth: 40, angle: 0, color: '#000000' },
  }));
  expect(svg).not.toContain('paint-order');
  expect(svg.match(/<text /g)?.length).toBe(1);
});

/* ── Сумісність зі старими постами ──────────────────────────────────────── */

test('макет без полів ефектів відкривається і рендериться', () => {
  // Саме такий spec лежить у чернетках, збережених до появи ефектів.
  const legacy = {
    version: 1, format: 'story',
    background: { type: 'color', color: '#EEE' },
    layers: [{
      id: 'old', type: 'text', role: 'title', text: 'Старий', x: 10, y: 20,
      width: 500, rotation: 0, opacity: 1, fontFamily: '', fontWeight: 400,
      fontStyle: 'normal', fontSize: 60, lineHeight: 1.2, letterSpacing: 0,
      align: 'left', color: '#000', decoration: 'none', uppercase: false,
    }],
  };
  const spec = normalizeSpec(legacy, 'story');
  expect(spec.layers[0]).toMatchObject({ fillType: 'solid' });
  expect((spec.layers[0] as TextLayer).shadow.enabled).toBe(false);
  expect(() => buildSvg(spec, FORMAT, resources)).not.toThrow();
});

/* ── Фото: геометрія, віньєтка, затемнення ──────────────────────────────── */

const withBackground = (patch: Record<string, unknown>): PostSpec => ({
  ...specWith(textLayer()),
  background: {
    type: 'asset', color: '#FFFFFF', assetId: 7, fit: 'cover', scale: 1,
    offsetX: 0, offsetY: 0, overlay: '#000000', overlayOpacity: 0,
    filter: { ...DEFAULT_PHOTO_FILTER },
    adjust: { ...DEFAULT_ADJUST },
    vignette: { ...DEFAULT_VIGNETTE },
    scrim: { ...DEFAULT_SCRIM },
    ...patch,
  } as PostSpec['background'],
});

const photoResources = {
  assetHref: () => 'data:image/webp;base64,AA',
  assetSize: () => ({ width: 2000, height: 1500 }),
  fonts: [],
};

const renderBackground = (patch: Record<string, unknown>) =>
  buildSvg(withBackground(patch), FORMAT, photoResources);

test('дзеркало й поворот лягають одним перетворенням', () => {
  const svg = renderBackground({
    adjust: { ...DEFAULT_ADJUST, flipX: true, rotate: -6 },
  });
  expect(svg).toContain('rotate(-6)');
  expect(svg).toContain('scale(-1 1)');
});

test('без правок геометрії зайвого transform немає', () => {
  expect(renderBackground({})).not.toContain('skewX');
});

test('нахил дає кадру запас, щоб не з\'явився порожній кут', () => {
  expect(adjustOverscan({ ...DEFAULT_ADJUST })).toBe(1);
  expect(adjustOverscan({ ...DEFAULT_ADJUST, rotate: 10 })).toBeGreaterThan(1);
  // Запас обмежений: інакше сильний нахил непомітно «з'їдав» би пів кадру.
  expect(adjustOverscan({ ...DEFAULT_ADJUST, rotate: 45, tiltX: 20, tiltY: 20 }))
    .toBeLessThanOrEqual(1.6);
});

test('віньєтка малюється радіальним градієнтом і лише коли увімкнена', () => {
  expect(renderBackground({})).not.toContain('bgVignette');
  const svg = renderBackground({
    vignette: { enabled: true, strength: 0.6, softness: 0.5, color: '#000000' },
  });
  expect(svg).toContain('<radialGradient id="bgVignette"');
  expect(svg).toContain('stop-opacity="0.6"');
});

test('затемнення знизу — лінійний градієнт із прозорим верхом', () => {
  const svg = renderBackground({
    scrim: { mode: 'bottom', color: '#000000', opacity: 0.6 },
  });
  expect(svg).toContain('<linearGradient id="bgScrim"');
  expect(svg).toContain('stop-opacity="0"');
  expect(svg).toContain('stop-opacity="0.6"');
});

test('вимкнене затемнення не лишає слідів', () => {
  expect(renderBackground({ scrim: { mode: 'none', color: '#000', opacity: 0.9 } }))
    .not.toContain('bgScrim');
});

test('дзеркалення фото-шару не перевертає його рамку обрізки', () => {
  const spec: PostSpec = {
    ...specWith(textLayer()),
    layers: [{
      id: 'img', type: 'image', assetId: 7, x: 100, y: 100, width: 400, height: 300,
      rotation: 0, opacity: 1, radius: 40,
      filter: { ...DEFAULT_PHOTO_FILTER }, flipX: true, flipY: false,
    }],
  };
  const svg = buildSvg(spec, FORMAT, photoResources);
  // Обрізка лишається на групі, дзеркало — на самому зображенні.
  expect(svg).toContain('<g clip-path="url(#clip_img)"');
  expect(svg).toContain('scale(-1 1)');
});
