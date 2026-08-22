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
