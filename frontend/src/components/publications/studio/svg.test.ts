/**
 * Рендер макета в SVG. Під тестом саме те, що ламається тихо: переноси рядків
 * (від них залежить висота блоку й прилипання), і присутність ефектів у
 * розмітці — бо помітно це аж у готовому кадрі, коли пост уже зібрано.
 *
 * Canvas у jsdom немає, а `wrapLines` без нього не міряє текст — підставляємо
 * передбачуване вимірювання: рівно 10 px на символ. Тоді «скільки слів влізе»
 * стає арифметикою, а не залежністю від шрифтів машини.
 */

import { buildSvg, textBlockHeight, wrapLines } from './svg';
import {
  CanvasFormat, DEFAULT_EXTRUDE, DEFAULT_GRADIENT, DEFAULT_PHOTO_FILTER,
  DEFAULT_SHADOW, DEFAULT_STROKE, PostSpec, TextLayer, normalizeSpec,
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
