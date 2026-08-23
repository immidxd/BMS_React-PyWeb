/**
 * Майстерня публікацій — спільні типи.
 *
 * `PostSpec` — єдине джерело правди про макет. Його малює редактор, його ж
 * перетворює на SVG (а далі на растр) `svg.ts`. Другого опису макета ніде
 * немає навмисно: два описи одного кадру завжди розходяться.
 */

export type CanvasFormatKey = 'story' | 'square' | 'portrait' | 'landscape';

export type CanvasFormat = {
  key: CanvasFormatKey;
  label: string;
  width: number;
  height: number;
};

export type PlatformKey = 'telegram' | 'instagram' | 'facebook' | 'viber';

export type PlatformInfo = {
  key: PlatformKey;
  label: string;
  formats: CanvasFormatKey[];
};

export type StudioConfig = { formats: CanvasFormat[]; platforms: PlatformInfo[] };

export type StudioAsset = {
  id: number;
  filename: string;
  title: string | null;
  width: number | null;
  height: number | null;
  bytes: number;
  has_alpha: boolean;
  collection_id: number | null;
  tags: string[];
  sort_order: number;
  created_at: string;
  src: string;
  thumb_src: string;
};

export type StudioFont = {
  id: number;
  family: string;
  weight: number;
  style: 'normal' | 'italic';
  label: string | null;
  format: string;
  filename: string;
  bytes: number;
  has_cyrillic: boolean;
  src: string;
};

export type StudioCollection = {
  id: number;
  kind: 'media' | 'post';
  name: string;
  sort_order: number;
};

/* ── Ефекти ───────────────────────────────────────────────────────────────
 * Усе, що нижче, малює SVG рідними засобами — градієнт заливкою, тінь
 * фільтром, обведення `paint-order`, «3D» шаром зміщених копій. Тому ефект,
 * побачений у редакторі, потрапляє в растр буквально тим самим кодом.
 */

export type Gradient = { from: string; to: string; angle: number };

export type Shadow = {
  enabled: boolean;
  color: string;
  dx: number;
  dy: number;
  blur: number;
  opacity: number;
};

export type Stroke = { enabled: boolean; color: string; width: number };

/** Об'ємні літери: копії тексту, зміщені під кутом углиб кадру. */
export type Extrude = { enabled: boolean; depth: number; angle: number; color: string };

/** Обробка фото. Насиченість 0 = чорно-біле, тому окремого «ч/б» немає. */
export type PhotoFilter = {
  brightness: number;
  contrast: number;
  saturation: number;
  blur: number;
};

export const DEFAULT_SHADOW: Shadow = {
  enabled: false, color: '#000000', dx: 0, dy: 8, blur: 12, opacity: 0.35,
};
export const DEFAULT_STROKE: Stroke = { enabled: false, color: '#FFFFFF', width: 6 };
export const DEFAULT_EXTRUDE: Extrude = {
  enabled: false, depth: 12, angle: 45, color: '#B790BF',
};
export const DEFAULT_GRADIENT: Gradient = { from: '#4E2358', to: '#B790BF', angle: 90 };
export const DEFAULT_PHOTO_FILTER: PhotoFilter = {
  brightness: 1, contrast: 1, saturation: 1, blur: 0,
};

/** Роль тексту в макеті. Саме вона, а не «розмір 72», тримає єдиний стиль:
 *  людина обирає «Заголовок», а типографіку підставляє шаблон. */
export type TextRole = 'title' | 'subtitle' | 'body' | 'caption';

export type TextLayer = {
  id: string;
  type: 'text';
  role: TextRole;
  text: string;
  /** Координати й розміри — у пікселях полотна (1080×1920 тощо), не екрана. */
  x: number;
  y: number;
  width: number;
  rotation: number;
  opacity: number;
  fontFamily: string;
  fontWeight: number;
  fontStyle: 'normal' | 'italic';
  fontSize: number;
  lineHeight: number;
  letterSpacing: number;
  align: 'left' | 'center' | 'right';
  color: string;
  decoration: 'none' | 'underline' | 'line-through';
  uppercase: boolean;
  fillType: 'solid' | 'gradient';
  gradient: Gradient;
  shadow: Shadow;
  stroke: Stroke;
  extrude: Extrude;
};

export type ImageLayer = {
  id: string;
  type: 'image';
  assetId: number;
  x: number;
  y: number;
  width: number;
  height: number;
  rotation: number;
  opacity: number;
  radius: number;
  filter: PhotoFilter;
};

export type Layer = TextLayer | ImageLayer;

export type Background = {
  /** `color` — суцільний колір; `asset` — фото з галереї. */
  type: 'color' | 'asset';
  color: string;
  assetId: number | null;
  fit: 'cover' | 'contain';
  scale: number;
  offsetX: number;
  offsetY: number;
  /** Затемнення/освітлення поверх фото — щоб текст читався на будь-якому кадрі. */
  overlay: string;
  overlayOpacity: number;
  filter: PhotoFilter;
};

export type PostSpec = {
  version: 1;
  format: CanvasFormatKey;
  background: Background;
  layers: Layer[];
};

export type PostTarget = {
  platform: PlatformKey;
  format: CanvasFormatKey;
  enabled: boolean;
  settings: Record<string, unknown>;
};

export type StudioPost = {
  id: number;
  title: string;
  status: 'draft' | 'ready' | 'scheduled' | 'published' | 'archived';
  base_format: CanvasFormatKey;
  caption: string;
  spec?: PostSpec;
  targets: PostTarget[];
  renders: Record<string, { url: string; width: number; height: number; rendered_at?: string }>;
  collection_id: number | null;
  scheduled_at: string | null;
  published_at: string | null;
  updated_at: string;
  preview_src: string | null;
};

/** Типографічні шаблони. Кегль задано часткою висоти полотна, тому «Заголовок»
 *  лишається заголовком і в Сторіс 1080×1920, і в квадраті 1080×1080. */
export const TEXT_ROLE_PRESETS: Record<TextRole, {
  label: string;
  sizeRatio: number;
  weight: number;
  lineHeight: number;
  letterSpacing: number;
  uppercase: boolean;
}> = {
  title:    { label: 'ЗАГОЛОВОК',      sizeRatio: 0.072, weight: 700, lineHeight: 1.08, letterSpacing: -0.5, uppercase: true },
  subtitle: { label: 'Підзаголовок',   sizeRatio: 0.044, weight: 600, lineHeight: 1.16, letterSpacing: 0,    uppercase: false },
  body:     { label: 'Основний текст', sizeRatio: 0.030, weight: 400, lineHeight: 1.36, letterSpacing: 0,    uppercase: false },
  caption:  { label: 'Дрібний підпис', sizeRatio: 0.021, weight: 400, lineHeight: 1.30, letterSpacing: 1.2,  uppercase: true },
};

/** Резервна родина, доки не залито жодного фірмового шрифту. */
export const FALLBACK_FAMILY = 'Avenir Next, Helvetica Neue, Arial, sans-serif';

export const newId = (): string =>
  `l${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;

/* ── Сумісність зі старими постами ─────────────────────────────────────────
 * Макети, збережені до появи ефектів, не мають цих полів. Домальовуємо
 * значення за замовчуванням при читанні — інакше редактор упав би на
 * `layer.shadow.enabled` вже на першому відкритті старої чернетки.
 */

export const normalizeLayer = (layer: any): Layer => {
  if (layer?.type === 'image') {
    return {
      ...layer,
      filter: { ...DEFAULT_PHOTO_FILTER, ...(layer.filter || {}) },
    } as ImageLayer;
  }
  return {
    ...layer,
    fillType: layer?.fillType === 'gradient' ? 'gradient' : 'solid',
    gradient: { ...DEFAULT_GRADIENT, ...(layer?.gradient || {}) },
    shadow: { ...DEFAULT_SHADOW, ...(layer?.shadow || {}) },
    stroke: { ...DEFAULT_STROKE, ...(layer?.stroke || {}) },
    extrude: { ...DEFAULT_EXTRUDE, ...(layer?.extrude || {}) },
  } as TextLayer;
};

export const normalizeSpec = (spec: any, fallbackFormat: CanvasFormatKey): PostSpec => ({
  version: 1,
  format: spec?.format || fallbackFormat,
  background: {
    type: spec?.background?.type === 'asset' ? 'asset' : 'color',
    color: spec?.background?.color || '#F4F1F6',
    assetId: spec?.background?.assetId ?? null,
    fit: spec?.background?.fit === 'contain' ? 'contain' : 'cover',
    scale: spec?.background?.scale ?? 1,
    offsetX: spec?.background?.offsetX ?? 0,
    offsetY: spec?.background?.offsetY ?? 0,
    overlay: spec?.background?.overlay || '#000000',
    overlayOpacity: spec?.background?.overlayOpacity ?? 0,
    filter: { ...DEFAULT_PHOTO_FILTER, ...(spec?.background?.filter || {}) },
  },
  layers: Array.isArray(spec?.layers) ? spec.layers.map(normalizeLayer) : [],
});
