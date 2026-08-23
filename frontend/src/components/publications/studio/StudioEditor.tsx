import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from './api';
import { AssetPicker } from './StudioGallery';
import {
  buildFontFaces, buildSvg, collectAssetDataUrls, svgToPngBlob, textBlockHeight,
} from './svg';
import {
  CanvasFormat, CanvasFormatKey, DEFAULT_EXTRUDE, DEFAULT_GRADIENT,
  DEFAULT_PHOTO_FILTER, DEFAULT_SHADOW, DEFAULT_STROKE, ImageLayer, Layer,
  PhotoFilter, PlatformKey, PostSpec, PostTarget, StudioAsset, StudioConfig,
  StudioFont, StudioPost, TextLayer, TextRole, TEXT_ROLE_PRESETS, newId,
  normalizeSpec,
} from './types';
import { useIsActivePage } from '../../../contexts/ActivePageContext';

/**
 * Конструктор поста.
 *
 * Малює браузер, і саме той SVG, який людина бачить, стає растром — див.
 * `svg.ts`. Тому в редакторі немає «приблизного прев'ю»: розбіжність із
 * готовим кадром неможлива за побудовою.
 *
 * Типографіка задається РОЛЛЮ («Заголовок», «Основний текст»), а не набором
 * чисел. Числа теж доступні, але за замовчуванням людина обирає роль — так
 * різні пости лишаються однією крамницею, а не колекцією випадкових макетів.
 */

const CARD = 'rounded-xl border border-gray-200 dark:border-gray-700';
const BTN = 'rounded-lg px-3 py-1.5 text-xs font-medium transition-colors';
const BTN_MAIN = `${BTN} bg-[var(--bms-accent)] text-white hover:opacity-90 disabled:opacity-50`;
const BTN_GHOST = `${BTN} border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800`;
const FIELD = 'w-full rounded-lg border border-gray-200 px-2 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100';
const LABEL = 'text-[10px] font-medium uppercase tracking-wide text-gray-400';

const PUBLICATION_STATUS: Record<string, string> = {
  queued: 'у черзі', scheduled: 'заплановано', processing: 'відправляється',
  retrying: 'повтор', published: 'опубліковано', failed: 'помилка',
  cancelled: 'скасовано',
};

const fmtDateTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString('uk-UA', { dateStyle: 'short', timeStyle: 'short' }) : '';

const PLATFORM_HINT: Record<PlatformKey, string> = {
  telegram: 'канал і каталог',
  instagram: 'стрічка та Stories',
  facebook: 'обидві Сторінки',
  viber: 'канал, одна картинка',
};

/** Порожній макет під формат: фон + запрошення поставити заголовок. */
export const emptySpec = (format: CanvasFormatKey): PostSpec => ({
  version: 1,
  format,
  background: {
    type: 'color', color: '#F4F1F6', assetId: null, fit: 'cover',
    scale: 1, offsetX: 0, offsetY: 0, overlay: '#000000', overlayOpacity: 0,
    filter: { ...DEFAULT_PHOTO_FILTER },
  },
  layers: [],
});

const scaleLayer = (layer: Layer, kx: number, ky: number): Layer => {
  if (layer.type === 'text') {
    return {
      ...layer,
      x: layer.x * kx, y: layer.y * ky,
      width: layer.width * kx,
      fontSize: layer.fontSize * kx,
    };
  }
  return {
    ...layer,
    x: layer.x * kx, y: layer.y * ky,
    width: layer.width * kx, height: layer.height * ky,
  };
};

/** Повзунки обробки фото. Один компонент і для фону, і для фото-шару —
 *  обробка в обох випадках однакова, а два різні набори контролів на ту саму
 *  дію лише плутали б. */
const PhotoFilterControls: React.FC<{
  value: PhotoFilter;
  onChange: (patch: Partial<PhotoFilter>) => void;
}> = ({ value, onChange }) => (
  <div className="grid grid-cols-2 gap-2">
    <label className={LABEL}>Яскравість
      <input type="range" min={0.4} max={1.8} step={0.01} value={value.brightness}
        onChange={event => onChange({ brightness: Number(event.target.value) })} className="w-full" />
    </label>
    <label className={LABEL}>Контраст
      <input type="range" min={0.4} max={2} step={0.01} value={value.contrast}
        onChange={event => onChange({ contrast: Number(event.target.value) })} className="w-full" />
    </label>
    <label className={LABEL}>Насиченість (0 = ч/б)
      <input type="range" min={0} max={2} step={0.01} value={value.saturation}
        onChange={event => onChange({ saturation: Number(event.target.value) })} className="w-full" />
    </label>
    <label className={LABEL}>Розмиття
      <input type="range" min={0} max={24} step={0.5} value={value.blur}
        onChange={event => onChange({ blur: Number(event.target.value) })} className="w-full" />
    </label>
  </div>
);

type Props = {
  post: StudioPost;
  config: StudioConfig;
  fonts: StudioFont[];
  onSaved: (post: StudioPost) => void;
  onDeleted: (postId: number) => void;
  onClose: () => void;
};

const StudioEditor: React.FC<Props> = ({ post, config, fonts, onSaved, onDeleted, onClose }) => {
  const [title, setTitle] = useState(post.title);
  const [caption, setCaption] = useState(post.caption || '');
  // Старі чернетки не мають полів ефектів — домальовуємо їх при відкритті,
  // інакше редактор упав би на `layer.shadow.enabled`.
  const [spec, setSpec] = useState<PostSpec>(
    post.spec?.layers ? normalizeSpec(post.spec, post.base_format) : emptySpec(post.base_format),
  );
  const [targets, setTargets] = useState<PostTarget[]>(post.targets || []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [assets, setAssets] = useState<StudioAsset[]>([]);
  const [pickerFor, setPickerFor] = useState<'background' | 'layer' | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [stageWidth, setStageWidth] = useState(360);
  const [guides, setGuides] = useState<{ x: number | null; y: number | null }>({ x: null, y: null });
  const [readiness, setReadiness] = useState<api.PublishReadiness | null>(null);
  const [publications, setPublications] = useState<api.StudioPublication[]>([]);
  const [publishResult, setPublishResult] = useState<api.PublishResult | null>(null);
  const [publishAt, setPublishAt] = useState('');
  // Публікація йде в живі акаунти, тому кнопка спрацьовує лише з другого
  // натискання — той самий запобіжник, що й у черзі Stories.
  const [armed, setArmed] = useState(false);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const isActivePage = useIsActivePage();

  const stageRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{
    id: string; mode: 'move' | 'resize'; startX: number; startY: number;
    originX: number; originY: number; originW: number; originH: number;
    dragHeight: number;
  } | null>(null);

  const format: CanvasFormat = useMemo(
    () => config.formats.find(item => item.key === spec.format) || config.formats[0],
    [config.formats, spec.format],
  );

  useEffect(() => {
    void api.fetchAssets().then(result => setAssets(result.items)).catch(() => undefined);
    void api.fetchPublishStatus().then(setReadiness).catch(() => undefined);
  }, []);

  const loadPublications = useCallback(async () => {
    try {
      const result = await api.fetchPublications(post.id);
      setPublications(result.items);
    } catch { /* історія відправок — довідка, без неї редактор працює */ }
  }, [post.id]);

  useEffect(() => { void loadPublications(); }, [loadPublications]);

  // Полотно масштабується під наявну висоту, а не під ширину колонки: інакше
  // Сторіс 9:16 на широкому екрані вилазить за межі вікна.
  useEffect(() => {
    const fit = () => {
      const available = Math.max(320, window.innerHeight - 320);
      setStageWidth(Math.min(420, Math.round(available * (format.width / format.height))));
    };
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [format.width, format.height]);

  const scale = stageWidth / format.width;
  const stageHeight = Math.round(format.height * scale);

  /** Прилипання до країв, полів і центру полотна. Числа в макеті рідко бувають
   *  «на око» правильними — краще, щоб заголовок сам ставав рівно по центру,
   *  ніж щоб людина ловила його мишею. */
  const snapPosition = useCallback((x: number, y: number, width: number, height: number) => {
    const tolerance = 14;
    const margin = Math.round(format.width * 0.08);
    const xCandidates: Array<[number, number]> = [
      [0, 0], [margin, margin], [format.width / 2 - width / 2, format.width / 2],
      [format.width - width, format.width], [format.width - margin - width, format.width - margin],
    ];
    const yCandidates: Array<[number, number]> = [
      [0, 0], [margin, margin], [format.height / 2 - height / 2, format.height / 2],
      [format.height - height, format.height], [format.height - margin - height, format.height - margin],
    ];
    let guideX: number | null = null;
    let guideY: number | null = null;
    for (const [candidate, guide] of xCandidates) {
      if (Math.abs(x - candidate) <= tolerance) { x = Math.round(candidate); guideX = guide; break; }
    }
    for (const [candidate, guide] of yCandidates) {
      if (Math.abs(y - candidate) <= tolerance) { y = Math.round(candidate); guideY = guide; break; }
    }
    return { x, y, guideX, guideY };
  }, [format.width, format.height]);

  const assetById = useCallback(
    (id: number) => assets.find(asset => asset.id === id) || null, [assets],
  );

  const resources = useMemo(() => ({
    assetHref: (id: number) => assetById(id)?.src || null,
    assetSize: (id: number) => {
      const asset = assetById(id);
      return asset?.width && asset?.height ? { width: asset.width, height: asset.height } : null;
    },
    fonts,
  }), [assetById, fonts]);

  const svg = useMemo(() => buildSvg(spec, format, resources), [spec, format, resources]);

  const selected = spec.layers.find(layer => layer.id === selectedId) || null;

  const patchLayer = useCallback((id: string, patch: Partial<TextLayer> & Partial<ImageLayer>) => {
    setSpec(current => ({
      ...current,
      layers: current.layers.map(layer => (
        layer.id === id ? ({ ...layer, ...patch } as Layer) : layer
      )),
    }));
  }, []);

  /* ── Історія (Ctrl+Z) ──────────────────────────────────────────────── */

  // Знімок робиться із затримкою: інакше кожен рух повзунка кегля лишав би
  // окремий крок, і скасування довелось би тиснути двадцять разів поспіль.
  const historyRef = useRef<{ past: PostSpec[]; future: PostSpec[] }>({ past: [], future: [] });
  const snapshotRef = useRef<string>(JSON.stringify(spec));

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const current = JSON.stringify(spec);
      if (current === snapshotRef.current) return;
      const history = historyRef.current;
      history.past.push(JSON.parse(snapshotRef.current));
      if (history.past.length > 60) history.past.shift();
      history.future = [];
      snapshotRef.current = current;
      setCanUndo(true);
      setCanRedo(false);
    }, 400);
    return () => window.clearTimeout(timer);
  }, [spec]);

  const undo = useCallback(() => {
    const history = historyRef.current;
    const previous = history.past.pop();
    if (!previous) return;
    history.future.push(JSON.parse(snapshotRef.current));
    snapshotRef.current = JSON.stringify(previous);
    setSpec(previous);
    setCanUndo(history.past.length > 0);
    setCanRedo(true);
  }, []);

  const redo = useCallback(() => {
    const history = historyRef.current;
    const next = history.future.pop();
    if (!next) return;
    history.past.push(JSON.parse(snapshotRef.current));
    snapshotRef.current = JSON.stringify(next);
    setSpec(next);
    setCanUndo(true);
    setCanRedo(history.future.length > 0);
  }, []);

  /* ── Перетягування ─────────────────────────────────────────────────── */

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const dx = (event.clientX - drag.startX) / scale;
      const dy = (event.clientY - drag.startY) / scale;
      if (drag.mode === 'move') {
        const snapped = snapPosition(
          drag.originX + dx, drag.originY + dy, drag.originW, drag.dragHeight,
        );
        patchLayer(drag.id, { x: snapped.x, y: snapped.y });
        setGuides({ x: snapped.guideX, y: snapped.guideY });
      } else {
        patchLayer(drag.id, {
          width: Math.max(40, Math.round(drag.originW + dx)),
          ...(drag.originH ? { height: Math.max(40, Math.round(drag.originH + dy)) } : {}),
        });
      }
    };
    const up = () => { dragRef.current = null; setGuides({ x: null, y: null }); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
  }, [patchLayer, scale, snapPosition]);

  const startDrag = (event: React.PointerEvent, layer: Layer, mode: 'move' | 'resize') => {
    event.stopPropagation();
    setSelectedId(layer.id);
    dragRef.current = {
      id: layer.id, mode,
      startX: event.clientX, startY: event.clientY,
      originX: layer.x, originY: layer.y,
      originW: layer.width,
      originH: layer.type === 'image' ? layer.height : 0,
      // Висота потрібна прилипанню: текст «по центру» — це центр його блоку,
      // а не верхнього краю.
      dragHeight: layer.type === 'image' ? layer.height : textBlockHeight(layer),
    };
  };

  /* ── Додавання шарів ───────────────────────────────────────────────── */

  const addText = (role: TextRole) => {
    const preset = TEXT_ROLE_PRESETS[role];
    const bottom = spec.layers.reduce((acc, layer) => Math.max(
      acc, layer.type === 'text' ? layer.y + textBlockHeight(layer) : layer.y + layer.height,
    ), format.height * 0.12);
    const layer: TextLayer = {
      id: newId(), type: 'text', role,
      text: preset.label,
      x: Math.round(format.width * 0.08),
      y: Math.round(Math.min(bottom + format.height * 0.03, format.height * 0.82)),
      width: Math.round(format.width * 0.84),
      rotation: 0, opacity: 1,
      fontFamily: fonts[0]?.family || '',
      fontWeight: preset.weight,
      fontStyle: 'normal',
      fontSize: Math.round(format.height * preset.sizeRatio),
      lineHeight: preset.lineHeight,
      letterSpacing: preset.letterSpacing,
      align: 'left',
      color: spec.background.type === 'asset' ? '#FFFFFF' : '#4E2358',
      decoration: 'none',
      uppercase: preset.uppercase,
      fillType: 'solid',
      gradient: { ...DEFAULT_GRADIENT },
      shadow: { ...DEFAULT_SHADOW },
      stroke: { ...DEFAULT_STROKE },
      extrude: { ...DEFAULT_EXTRUDE },
    };
    setSpec(current => ({ ...current, layers: [...current.layers, layer] }));
    setSelectedId(layer.id);
  };

  const addImageLayer = (asset: StudioAsset) => {
    const width = Math.round(format.width * 0.4);
    const ratio = asset.width && asset.height ? asset.height / asset.width : 1;
    const layer: ImageLayer = {
      id: newId(), type: 'image', assetId: asset.id,
      x: Math.round(format.width * 0.3), y: Math.round(format.height * 0.3),
      width, height: Math.round(width * ratio),
      rotation: 0, opacity: 1, radius: 0,
      filter: { ...DEFAULT_PHOTO_FILTER },
    };
    setSpec(current => ({ ...current, layers: [...current.layers, layer] }));
    setSelectedId(layer.id);
  };

  const removeLayer = useCallback((id: string) => {
    setSpec(current => ({ ...current, layers: current.layers.filter(layer => layer.id !== id) }));
    setSelectedId(null);
  }, []);

  /** Зсув шару стрілками. Читає САМЕ поточний стан, а не знімок із замикання:
   *  два швидкі натискання потрапляють в один такт React, і обидва порахували б
   *  крок від того самого старого значення — шар зупинявся б, а стрілка іншої
   *  осі ще й повертала б попередню назад. */
  const nudgeLayer = useCallback((id: string, dx: number, dy: number) => {
    setSpec(current => ({
      ...current,
      layers: current.layers.map(layer => (
        layer.id === id ? ({ ...layer, x: layer.x + dx, y: layer.y + dy } as Layer) : layer
      )),
    }));
  }, []);

  const duplicateLayer = useCallback((id: string) => {
    setSpec(current => {
      const source = current.layers.find(layer => layer.id === id);
      if (!source) return current;
      // Копія зі зсувом: точно поверх оригіналу її не видно, і людина думає,
      // що дублювання не спрацювало.
      const copy = { ...source, id: newId(), x: source.x + 24, y: source.y + 24 } as Layer;
      return { ...current, layers: [...current.layers, copy] };
    });
  }, []);

  const moveLayer = (id: string, direction: -1 | 1) => {
    setSpec(current => {
      const index = current.layers.findIndex(layer => layer.id === id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= current.layers.length) return current;
      const layers = [...current.layers];
      [layers[index], layers[target]] = [layers[target], layers[index]];
      return { ...current, layers };
    });
  };

  // Клавіатура. isActivePage обов'язковий: сторінки залишаються змонтованими
  // при перемиканні верхніх вкладок, і без нього Ctrl+Z із «Товарів» скасовував
  // би правку в макеті, якого людина навіть не бачить.
  useEffect(() => {
    if (!isActivePage) return undefined;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        if (event.shiftKey) redo(); else undo();
        return;
      }
      if (!selectedId) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'd') {
        event.preventDefault();
        duplicateLayer(selectedId);
        return;
      }
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        removeLayer(selectedId);
        return;
      }
      const step = event.shiftKey ? 10 : 1;
      const nudge: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0], ArrowRight: [step, 0],
        ArrowUp: [0, -step], ArrowDown: [0, step],
      };
      const delta = nudge[event.key];
      if (!delta) return;
      event.preventDefault();
      nudgeLayer(selectedId, delta[0], delta[1]);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isActivePage, selectedId, nudgeLayer, removeLayer, duplicateLayer, undo, redo]);

  const changeFormat = (key: CanvasFormatKey) => {
    const next = config.formats.find(item => item.key === key);
    if (!next) return;
    // Координати абсолютні, тож при зміні полотна масштабуємо їх пропорційно —
    // інакше макет «поїде» за край і його доведеться складати заново.
    const kx = next.width / format.width;
    const ky = next.height / format.height;
    setSpec(current => ({
      ...current, format: key,
      layers: current.layers.map(layer => scaleLayer(layer, kx, ky)),
    }));
  };

  /* ── Мережі ────────────────────────────────────────────────────────── */

  const toggleTarget = (platform: PlatformKey) => {
    setTargets(current => {
      const existing = current.find(target => target.platform === platform);
      if (existing) return current.filter(target => target.platform !== platform);
      const info = config.platforms.find(item => item.key === platform);
      const preferred = info?.formats.includes(spec.format) ? spec.format : info?.formats[0];
      return [...current, {
        platform, format: (preferred || spec.format) as CanvasFormatKey,
        enabled: true, settings: {},
      }];
    });
  };

  const setTargetFormat = (platform: PlatformKey, value: CanvasFormatKey) => {
    setTargets(current => current.map(target => (
      target.platform === platform ? { ...target, format: value } : target
    )));
  };

  const setTargetSetting = (platform: PlatformKey, key: string, value: unknown) => {
    setTargets(current => current.map(target => (
      target.platform === platform
        ? { ...target, settings: { ...target.settings, [key]: value } }
        : target
    )));
  };

  /* ── Збереження ────────────────────────────────────────────────────── */

  /** Той самий макет, перерахований під інше полотно. Потрібно, коли одна
   *  мережа бере Сторіс, а друга — квадрат: макет один, кадрів кілька. */
  const specForFormat = useCallback((target: CanvasFormat): PostSpec => {
    if (target.key === spec.format) return spec;
    const kx = target.width / format.width;
    const ky = target.height / format.height;
    return {
      ...spec,
      format: target.key,
      layers: spec.layers.map(layer => scaleLayer(layer, kx, ky)),
    };
  }, [format.height, format.width, spec]);

  const renderPng = useCallback(async (target?: CanvasFormat): Promise<Blob> => {
    const canvas = target || format;
    const source = specForFormat(canvas);
    const [fontFaces, embedded] = await Promise.all([
      buildFontFaces(fonts.filter(font => (
        source.layers.some(layer => layer.type === 'text' && layer.fontFamily === font.family)
      ))),
      collectAssetDataUrls(source, id => assetById(id)?.src || null),
    ]);
    const exportSvg = buildSvg(source, canvas, {
      assetHref: id => embedded.get(id) || null,
      assetSize: resources.assetSize,
      fonts,
      fontFaces,
    });
    return svgToPngBlob(exportSvg, canvas);
  }, [assetById, fonts, format, resources.assetSize, specForFormat]);

  const save = async (withRender: boolean) => {
    setBusy(withRender ? 'render' : 'save'); setError(null); setMessage(null);
    try {
      let saved = await api.updatePost(post.id, {
        title, caption, spec, targets, base_format: spec.format,
      });
      if (withRender) {
        const blob = await renderPng();
        saved = await api.uploadRender(post.id, spec.format, blob);
      }
      onSaved(saved);
      setMessage(withRender ? 'Збережено, кадр зібрано.' : 'Чернетку збережено.');
    } catch (reason: any) {
      setError(reason.message || 'Не вдалося зберегти');
    } finally {
      setBusy(null);
    }
  };

  /* ── Публікація ────────────────────────────────────────────────────── */

  /** Зберегти, добудувати відсутні кадри й віддати пост мережам.
   *
   *  Кадри збираються ТУТ, а не на бекенді: рендерить браузер, і якщо Viber
   *  просить квадрат, а макет намальовано в Сторіс, кадр у квадраті має
   *  народитися з того самого документа — інакше мережа отримала б обрізок.
   */
  const publish = async (dryRun: boolean) => {
    setBusy(dryRun ? 'rehearse' : 'publish');
    setError(null); setMessage(null); setPublishResult(null);
    try {
      let saved = await api.updatePost(post.id, {
        title, caption, spec, targets, base_format: spec.format,
      });

      const needed = Array.from(new Set(
        targets.filter(target => target.enabled !== false).map(target => target.format),
      ));
      const missing = needed.filter(key => !saved.renders?.[key]);
      for (const key of missing) {
        const canvas = config.formats.find(item => item.key === key);
        if (!canvas) continue;
        const blob = await renderPng(canvas);
        // Прев'ю поста лишає за собою базовий формат — саме його людина бачить
        // у списку, і він не має підмінятись кадром для чужої мережі.
        saved = await api.uploadRender(post.id, key, blob);
      }
      onSaved(saved);

      const result = await api.publishPost(post.id, {
        dry_run: dryRun,
        publish_at: publishAt ? new Date(publishAt).toISOString() : null,
      });
      setPublishResult(result);
      if (!dryRun) {
        await loadPublications();
        try { onSaved(await api.fetchPost(post.id)); } catch { /* картка оновиться при виході */ }
      }
      setMessage(dryRun
        ? 'Репетиція пройшла: кадри зібрано, підписи й розклад придатні. Нічого не відправлено.'
        : (result.ok ? 'Пост передано мережам.' : 'Частину мереж не вдалося пройти — деталі нижче.'));
    } catch (reason: any) {
      setError(reason.message || 'Не вдалося опублікувати');
    } finally {
      setBusy(null);
      setArmed(false);
    }
  };

  const armPublish = () => {
    if (!armed) {
      setArmed(true);
      window.setTimeout(() => setArmed(false), 6000);
      return;
    }
    void publish(false);
  };

  const remove = async () => {
    if (!window.confirm(`Видалити пост «${title}»? Дію не скасувати.`)) return;
    try { await api.deletePost(post.id); onDeleted(post.id); }
    catch (reason: any) { setError(reason.message); }
  };

  /* ── Розмітка ──────────────────────────────────────────────────────── */

  const families = useMemo(() => {
    const unique = Array.from(new Set(fonts.map(font => font.family)));
    return unique.sort((a, b) => a.localeCompare(b, 'uk'));
  }, [fonts]);

  const weightsFor = (family: string): number[] => {
    const list = fonts.filter(font => font.family === family).map(font => font.weight);
    return list.length ? Array.from(new Set(list)).sort((a, b) => a - b) : [400, 700];
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <button type="button" onClick={onClose} className={BTN_GHOST}>← До постів</button>
          <input value={title} onChange={event => setTitle(event.target.value)}
            className="rounded-lg border border-transparent px-2 py-1 text-sm font-semibold text-gray-900 hover:border-gray-200 focus:border-gray-300 dark:text-gray-100 dark:hover:border-gray-600"
          />
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={undo} disabled={!canUndo} title="Скасувати (⌘Z)"
            className={`${BTN_GHOST} disabled:opacity-40`}>↶</button>
          <button type="button" onClick={redo} disabled={!canRedo} title="Повернути (⇧⌘Z)"
            className={`${BTN_GHOST} disabled:opacity-40`}>↷</button>
          <button type="button" onClick={() => void remove()} className={`${BTN} text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20`}>
            Видалити
          </button>
          <button type="button" disabled={busy !== null} onClick={() => void save(false)} className={BTN_GHOST}>
            {busy === 'save' ? 'Зберігаю…' : 'Зберегти чернетку'}
          </button>
          <button type="button" disabled={busy !== null} onClick={() => void save(true)} className={BTN_MAIN}>
            {busy === 'render' ? 'Збираю кадр…' : 'Зберегти й зібрати кадр'}
          </button>
        </div>
      </div>

      {(message || error) && (
        <div className={`rounded-lg px-3 py-2 text-xs ${error
          ? 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300'
          : 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300'}`}>
          {error || message}
        </div>
      )}

      <div className="flex flex-col gap-4 lg:flex-row">
        {/* ── Полотно ── */}
        <div className="flex flex-col items-center gap-2">
          <div
            ref={stageRef}
            onPointerDown={() => setSelectedId(null)}
            className="relative overflow-hidden rounded-xl border border-gray-200 shadow-sm dark:border-gray-700"
            style={{ width: stageWidth, height: stageHeight }}
          >
            <div className="pointer-events-none absolute inset-0 [&>svg]:h-full [&>svg]:w-full"
              dangerouslySetInnerHTML={{ __html: svg }} />
            {guides.x !== null && (
              <div className="pointer-events-none absolute top-0 h-full w-px bg-[var(--bms-accent)]"
                style={{ left: guides.x * scale }} />
            )}
            {guides.y !== null && (
              <div className="pointer-events-none absolute left-0 w-full border-t border-[var(--bms-accent)]"
                style={{ top: guides.y * scale }} />
            )}
            {spec.layers.map(layer => {
              const height = layer.type === 'text' ? textBlockHeight(layer) : layer.height;
              const isSelected = layer.id === selectedId;
              return (
                <div
                  key={layer.id}
                  onPointerDown={event => startDrag(event, layer, 'move')}
                  className={`absolute cursor-move ${isSelected
                    ? 'outline outline-1 outline-[var(--bms-accent)]'
                    : 'outline outline-1 outline-transparent hover:outline-gray-300'}`}
                  style={{
                    left: layer.x * scale, top: layer.y * scale,
                    width: layer.width * scale, height: Math.max(12, height * scale),
                    transform: layer.rotation ? `rotate(${layer.rotation}deg)` : undefined,
                  }}
                >
                  {isSelected && (
                    <span
                      onPointerDown={event => startDrag(event, layer, 'resize')}
                      className="absolute -bottom-1.5 -right-1.5 h-3 w-3 cursor-nwse-resize rounded-sm bg-[var(--bms-accent)]"
                    />
                  )}
                </div>
              );
            })}
          </div>
          <div className="text-[10px] text-gray-400">
            {format.label} · {format.width}×{format.height} px
          </div>
        </div>

        {/* ── Інспектор ── */}
        <div className="min-w-0 flex-1 space-y-3">
          <div className={`${CARD} p-3`}>
            <div className={LABEL}>Формат полотна</div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {config.formats.map(item => (
                <button key={item.key} type="button" onClick={() => changeFormat(item.key)}
                  className={`${BTN} ${spec.format === item.key
                    ? 'bg-[var(--bms-accent)] text-white'
                    : 'border border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          <div className={`${CARD} p-3`}>
            <div className={LABEL}>Фон</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-1.5 text-[11px] text-gray-600 dark:text-gray-300">
                <input type="radio" checked={spec.background.type === 'color'}
                  onChange={() => setSpec(current => ({
                    ...current, background: { ...current.background, type: 'color' },
                  }))} />
                Колір
              </label>
              <input type="color" value={spec.background.color}
                onChange={event => setSpec(current => ({
                  ...current, background: { ...current.background, color: event.target.value },
                }))}
                className="h-7 w-10 cursor-pointer rounded border border-gray-200 dark:border-gray-600" />
              <label className="flex items-center gap-1.5 text-[11px] text-gray-600 dark:text-gray-300">
                <input type="radio" checked={spec.background.type === 'asset'}
                  onChange={() => setSpec(current => ({
                    ...current, background: { ...current.background, type: 'asset' },
                  }))} />
                Фото
              </label>
              <button type="button" className={BTN_GHOST} onClick={() => setPickerFor('background')}>
                {spec.background.assetId ? 'Змінити фото' : 'Обрати з галереї'}
              </button>
            </div>

            {spec.background.type === 'asset' && (
              <div className="mt-3 grid grid-cols-2 gap-2">
                <label className={LABEL}>Масштаб
                  <input type="range" min={0.5} max={3} step={0.01} value={spec.background.scale}
                    onChange={event => setSpec(current => ({
                      ...current, background: { ...current.background, scale: Number(event.target.value) },
                    }))} className="w-full" />
                </label>
                <label className={LABEL}>Затемнення
                  <input type="range" min={0} max={0.8} step={0.01} value={spec.background.overlayOpacity}
                    onChange={event => setSpec(current => ({
                      ...current, background: { ...current.background, overlayOpacity: Number(event.target.value) },
                    }))} className="w-full" />
                </label>
                <label className={LABEL}>Зсув ↔
                  <input type="range" min={-format.width / 2} max={format.width / 2} step={1}
                    value={spec.background.offsetX}
                    onChange={event => setSpec(current => ({
                      ...current, background: { ...current.background, offsetX: Number(event.target.value) },
                    }))} className="w-full" />
                </label>
                <label className={LABEL}>Зсув ↕
                  <input type="range" min={-format.height / 2} max={format.height / 2} step={1}
                    value={spec.background.offsetY}
                    onChange={event => setSpec(current => ({
                      ...current, background: { ...current.background, offsetY: Number(event.target.value) },
                    }))} className="w-full" />
                </label>
              </div>
            )}

            {spec.background.type === 'asset' && (
              <div className="mt-3 border-t border-gray-100 pt-3 dark:border-gray-700">
                <div className={LABEL}>Обробка фону</div>
                <div className="mt-2">
                  <PhotoFilterControls
                    value={spec.background.filter}
                    onChange={patch => setSpec(current => ({
                      ...current,
                      background: { ...current.background, filter: { ...current.background.filter, ...patch } },
                    }))}
                  />
                </div>
              </div>
            )}
          </div>

          <div className={`${CARD} p-3`}>
            <div className="flex items-center justify-between">
              <div className={LABEL}>Шари</div>
              <button type="button" className={BTN_GHOST} onClick={() => setPickerFor('layer')}>
                + Фото
              </button>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(Object.keys(TEXT_ROLE_PRESETS) as TextRole[]).map(role => (
                <button key={role} type="button" onClick={() => addText(role)} className={BTN_GHOST}>
                  + {TEXT_ROLE_PRESETS[role].label}
                </button>
              ))}
            </div>
            <div className="mt-2 space-y-1">
              {[...spec.layers].reverse().map(layer => (
                <div key={layer.id}
                  onClick={() => setSelectedId(layer.id)}
                  className={`flex cursor-pointer items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-[11px] ${
                    layer.id === selectedId
                      ? 'bg-[var(--bms-accent)]/10 text-gray-900 dark:text-gray-100'
                      : 'bg-gray-50 text-gray-600 dark:bg-gray-800 dark:text-gray-300'}`}>
                  <span className="truncate">
                    {layer.type === 'text'
                      ? `${TEXT_ROLE_PRESETS[layer.role].label}: ${layer.text.slice(0, 28) || '—'}`
                      : `Фото #${layer.assetId}`}
                  </span>
                  <span className="flex shrink-0 items-center gap-1">
                    <button type="button" title="Вище" onClick={event => { event.stopPropagation(); moveLayer(layer.id, 1); }}>↑</button>
                    <button type="button" title="Нижче" onClick={event => { event.stopPropagation(); moveLayer(layer.id, -1); }}>↓</button>
                    <button type="button" title="Дублювати (⌘D)"
                      onClick={event => { event.stopPropagation(); duplicateLayer(layer.id); }}>⧉</button>
                    <button type="button" title="Прибрати" className="text-red-500"
                      onClick={event => { event.stopPropagation(); removeLayer(layer.id); }}>×</button>
                  </span>
                </div>
              ))}
              {!spec.layers.length && (
                <div className="px-2 py-3 text-[11px] text-gray-400">
                  Шарів ще немає. Почніть із заголовка.
                </div>
              )}
            </div>
          </div>

          {selected?.type === 'text' && (
            <div className={`${CARD} space-y-2 p-3`}>
              <div className={LABEL}>Текст</div>
              <textarea value={selected.text} rows={3} className={FIELD}
                onChange={event => patchLayer(selected.id, { text: event.target.value })} />

              <div className="grid grid-cols-2 gap-2">
                <label className={LABEL}>Шаблон
                  <select className={`${FIELD} mt-1`} value={selected.role}
                    onChange={event => {
                      const role = event.target.value as TextRole;
                      const preset = TEXT_ROLE_PRESETS[role];
                      patchLayer(selected.id, {
                        role,
                        fontWeight: preset.weight,
                        fontSize: Math.round(format.height * preset.sizeRatio),
                        lineHeight: preset.lineHeight,
                        letterSpacing: preset.letterSpacing,
                        uppercase: preset.uppercase,
                      });
                    }}>
                    {(Object.keys(TEXT_ROLE_PRESETS) as TextRole[]).map(role => (
                      <option key={role} value={role}>{TEXT_ROLE_PRESETS[role].label}</option>
                    ))}
                  </select>
                </label>
                <label className={LABEL}>Шрифт
                  <select className={`${FIELD} mt-1`} value={selected.fontFamily}
                    onChange={event => patchLayer(selected.id, { fontFamily: event.target.value })}>
                    <option value="">Системний</option>
                    {families.map(family => <option key={family} value={family}>{family}</option>)}
                  </select>
                </label>
                <label className={LABEL}>Накреслення
                  <select className={`${FIELD} mt-1`} value={selected.fontWeight}
                    onChange={event => patchLayer(selected.id, { fontWeight: Number(event.target.value) })}>
                    {weightsFor(selected.fontFamily).map(weight => (
                      <option key={weight} value={weight}>{weight}</option>
                    ))}
                  </select>
                </label>
                <label className={LABEL}>Кегль
                  <input type="number" className={`${FIELD} mt-1`} value={Math.round(selected.fontSize)}
                    onChange={event => patchLayer(selected.id, { fontSize: Number(event.target.value) || 12 })} />
                </label>
                <label className={LABEL}>Міжрядковий
                  <input type="range" min={0.9} max={2} step={0.01} value={selected.lineHeight}
                    onChange={event => patchLayer(selected.id, { lineHeight: Number(event.target.value) })}
                    className="w-full" />
                </label>
                <label className={LABEL}>Міжлітерний
                  <input type="range" min={-3} max={12} step={0.1} value={selected.letterSpacing}
                    onChange={event => patchLayer(selected.id, { letterSpacing: Number(event.target.value) })}
                    className="w-full" />
                </label>
              </div>

              <div className="flex flex-wrap items-center gap-1.5">
                {(['left', 'center', 'right'] as const).map(align => (
                  <button key={align} type="button" onClick={() => patchLayer(selected.id, { align })}
                    className={`${BTN} ${selected.align === align
                      ? 'bg-[var(--bms-accent)] text-white'
                      : 'border border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
                    {align === 'left' ? 'Ліворуч' : align === 'center' ? 'По центру' : 'Праворуч'}
                  </button>
                ))}
                <button type="button"
                  onClick={() => patchLayer(selected.id, {
                    fontStyle: selected.fontStyle === 'italic' ? 'normal' : 'italic',
                  })}
                  className={`${BTN} italic ${selected.fontStyle === 'italic'
                    ? 'bg-[var(--bms-accent)] text-white'
                    : 'border border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
                  Курсив
                </button>
                <button type="button"
                  onClick={() => patchLayer(selected.id, {
                    decoration: selected.decoration === 'line-through' ? 'none' : 'line-through',
                  })}
                  className={`${BTN} line-through ${selected.decoration === 'line-through'
                    ? 'bg-[var(--bms-accent)] text-white'
                    : 'border border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
                  Закреслений
                </button>
                <button type="button"
                  onClick={() => patchLayer(selected.id, { uppercase: !selected.uppercase })}
                  className={`${BTN} ${selected.uppercase
                    ? 'bg-[var(--bms-accent)] text-white'
                    : 'border border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
                  ВЕЛИКІ
                </button>
                <input type="color" value={selected.color}
                  onChange={event => patchLayer(selected.id, { color: event.target.value })}
                  className="h-7 w-10 cursor-pointer rounded border border-gray-200 dark:border-gray-600" />
              </div>

              <div className="grid grid-cols-3 gap-2">
                <label className={LABEL}>X
                  <input type="number" className={`${FIELD} mt-1`} value={Math.round(selected.x)}
                    onChange={event => patchLayer(selected.id, { x: Number(event.target.value) })} />
                </label>
                <label className={LABEL}>Y
                  <input type="number" className={`${FIELD} mt-1`} value={Math.round(selected.y)}
                    onChange={event => patchLayer(selected.id, { y: Number(event.target.value) })} />
                </label>
                <label className={LABEL}>Поворот
                  <input type="number" className={`${FIELD} mt-1`} value={Math.round(selected.rotation)}
                    onChange={event => patchLayer(selected.id, { rotation: Number(event.target.value) })} />
                </label>
              </div>

              {/* ── Ефекти ── */}
              <div className="space-y-3 border-t border-gray-100 pt-3 dark:border-gray-700">
                <div>
                  <div className={LABEL}>Заливка</div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    {(['solid', 'gradient'] as const).map(mode => (
                      <button key={mode} type="button"
                        onClick={() => patchLayer(selected.id, { fillType: mode })}
                        className={`${BTN} ${selected.fillType === mode
                          ? 'bg-[var(--bms-accent)] text-white'
                          : 'border border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
                        {mode === 'solid' ? 'Суцільна' : 'Градієнт'}
                      </button>
                    ))}
                    {selected.fillType === 'gradient' && (
                      <>
                        <input type="color" value={selected.gradient.from}
                          onChange={event => patchLayer(selected.id, {
                            gradient: { ...selected.gradient, from: event.target.value },
                          })}
                          className="h-7 w-10 cursor-pointer rounded border border-gray-200 dark:border-gray-600" />
                        <input type="color" value={selected.gradient.to}
                          onChange={event => patchLayer(selected.id, {
                            gradient: { ...selected.gradient, to: event.target.value },
                          })}
                          className="h-7 w-10 cursor-pointer rounded border border-gray-200 dark:border-gray-600" />
                        <label className={`${LABEL} flex-1`}>Кут
                          <input type="range" min={0} max={360} step={1} value={selected.gradient.angle}
                            onChange={event => patchLayer(selected.id, {
                              gradient: { ...selected.gradient, angle: Number(event.target.value) },
                            })} className="w-full" />
                        </label>
                      </>
                    )}
                  </div>
                </div>

                <div>
                  <label className="flex items-center gap-2 text-[11px] text-gray-600 dark:text-gray-300">
                    <input type="checkbox" checked={selected.shadow.enabled}
                      onChange={event => patchLayer(selected.id, {
                        shadow: { ...selected.shadow, enabled: event.target.checked },
                      })} />
                    Тінь
                  </label>
                  {selected.shadow.enabled && (
                    <div className="mt-1.5 grid grid-cols-2 gap-2">
                      <label className={LABEL}>Зсув ↔
                        <input type="range" min={-60} max={60} step={1} value={selected.shadow.dx}
                          onChange={event => patchLayer(selected.id, {
                            shadow: { ...selected.shadow, dx: Number(event.target.value) },
                          })} className="w-full" />
                      </label>
                      <label className={LABEL}>Зсув ↕
                        <input type="range" min={-60} max={60} step={1} value={selected.shadow.dy}
                          onChange={event => patchLayer(selected.id, {
                            shadow: { ...selected.shadow, dy: Number(event.target.value) },
                          })} className="w-full" />
                      </label>
                      <label className={LABEL}>Розмиття
                        <input type="range" min={0} max={80} step={1} value={selected.shadow.blur}
                          onChange={event => patchLayer(selected.id, {
                            shadow: { ...selected.shadow, blur: Number(event.target.value) },
                          })} className="w-full" />
                      </label>
                      <label className={LABEL}>Сила
                        <input type="range" min={0.05} max={1} step={0.01} value={selected.shadow.opacity}
                          onChange={event => patchLayer(selected.id, {
                            shadow: { ...selected.shadow, opacity: Number(event.target.value) },
                          })} className="w-full" />
                      </label>
                      <input type="color" value={selected.shadow.color}
                        onChange={event => patchLayer(selected.id, {
                          shadow: { ...selected.shadow, color: event.target.value },
                        })}
                        className="h-7 w-10 cursor-pointer rounded border border-gray-200 dark:border-gray-600" />
                    </div>
                  )}
                </div>

                <div>
                  <label className="flex items-center gap-2 text-[11px] text-gray-600 dark:text-gray-300">
                    <input type="checkbox" checked={selected.stroke.enabled}
                      onChange={event => patchLayer(selected.id, {
                        stroke: { ...selected.stroke, enabled: event.target.checked },
                      })} />
                    Обведення
                  </label>
                  {selected.stroke.enabled && (
                    <div className="mt-1.5 flex items-center gap-2">
                      <label className={`${LABEL} flex-1`}>Товщина
                        <input type="range" min={1} max={40} step={1} value={selected.stroke.width}
                          onChange={event => patchLayer(selected.id, {
                            stroke: { ...selected.stroke, width: Number(event.target.value) },
                          })} className="w-full" />
                      </label>
                      <input type="color" value={selected.stroke.color}
                        onChange={event => patchLayer(selected.id, {
                          stroke: { ...selected.stroke, color: event.target.value },
                        })}
                        className="h-7 w-10 cursor-pointer rounded border border-gray-200 dark:border-gray-600" />
                    </div>
                  )}
                </div>

                <div>
                  <label className="flex items-center gap-2 text-[11px] text-gray-600 dark:text-gray-300">
                    <input type="checkbox" checked={selected.extrude.enabled}
                      onChange={event => patchLayer(selected.id, {
                        extrude: { ...selected.extrude, enabled: event.target.checked },
                      })} />
                    Об'єм (3D)
                  </label>
                  {selected.extrude.enabled && (
                    <div className="mt-1.5 flex items-center gap-2">
                      <label className={`${LABEL} flex-1`}>Глибина
                        <input type="range" min={1} max={40} step={1} value={selected.extrude.depth}
                          onChange={event => patchLayer(selected.id, {
                            extrude: { ...selected.extrude, depth: Number(event.target.value) },
                          })} className="w-full" />
                      </label>
                      <label className={`${LABEL} flex-1`}>Напрям
                        <input type="range" min={0} max={360} step={1} value={selected.extrude.angle}
                          onChange={event => patchLayer(selected.id, {
                            extrude: { ...selected.extrude, angle: Number(event.target.value) },
                          })} className="w-full" />
                      </label>
                      <input type="color" value={selected.extrude.color}
                        onChange={event => patchLayer(selected.id, {
                          extrude: { ...selected.extrude, color: event.target.value },
                        })}
                        className="h-7 w-10 cursor-pointer rounded border border-gray-200 dark:border-gray-600" />
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {selected?.type === 'image' && (
            <div className={`${CARD} space-y-2 p-3`}>
              <div className={LABEL}>Фото-шар</div>
              <div className="grid grid-cols-2 gap-2">
                <label className={LABEL}>Ширина
                  <input type="number" className={`${FIELD} mt-1`} value={Math.round(selected.width)}
                    onChange={event => patchLayer(selected.id, { width: Number(event.target.value) })} />
                </label>
                <label className={LABEL}>Висота
                  <input type="number" className={`${FIELD} mt-1`} value={Math.round(selected.height)}
                    onChange={event => patchLayer(selected.id, { height: Number(event.target.value) })} />
                </label>
                <label className={LABEL}>Заокруглення
                  <input type="range" min={0} max={200} step={1} value={selected.radius}
                    onChange={event => patchLayer(selected.id, { radius: Number(event.target.value) })}
                    className="w-full" />
                </label>
                <label className={LABEL}>Прозорість
                  <input type="range" min={0.1} max={1} step={0.01} value={selected.opacity}
                    onChange={event => patchLayer(selected.id, { opacity: Number(event.target.value) })}
                    className="w-full" />
                </label>
              </div>
              <div className="border-t border-gray-100 pt-2 dark:border-gray-700">
                <div className={LABEL}>Обробка</div>
                <div className="mt-2">
                  <PhotoFilterControls
                    value={selected.filter}
                    onChange={patch => patchLayer(selected.id, {
                      filter: { ...selected.filter, ...patch },
                    })}
                  />
                </div>
              </div>
            </div>
          )}

          <div className={`${CARD} p-3`}>
            <div className={LABEL}>Куди публікувати</div>
            <div className="mt-2 space-y-2">
              {config.platforms.map(platform => {
                const target = targets.find(item => item.platform === platform.key);
                const ready = readiness?.platforms?.[platform.key];
                return (
                  <div key={platform.key} className="rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-800">
                    <div className="flex flex-wrap items-center gap-2">
                      <label className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-200">
                        <input type="checkbox" checked={Boolean(target)}
                          onChange={() => toggleTarget(platform.key)} />
                        {platform.label}
                      </label>
                      <span className="text-[10px] text-gray-400">{PLATFORM_HINT[platform.key]}</span>
                      {ready && !ready.ready && (
                        <span title={ready.detail || undefined}
                          className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
                          не підключено
                        </span>
                      )}
                      {target && (
                        <select className={`${FIELD} ml-auto w-40`} value={target.format}
                          onChange={event => setTargetFormat(platform.key, event.target.value as CanvasFormatKey)}>
                          {platform.formats.map(key => (
                            <option key={key} value={key}>
                              {config.formats.find(item => item.key === key)?.label || key}
                            </option>
                          ))}
                        </select>
                      )}
                    </div>
                    {target && platform.key === 'telegram' && (
                      <label className="mt-1.5 flex items-center gap-2 text-[11px] text-gray-500 dark:text-gray-400">
                        <input type="checkbox" checked={Boolean(target.settings.silent)}
                          onChange={event => setTargetSetting(platform.key, 'silent', event.target.checked)} />
                        🔕 Без звуку
                      </label>
                    )}
                    {target && platform.key === 'facebook' && Boolean(ready?.pages?.length) && (
                      <div className="mt-1.5 flex flex-wrap gap-3">
                        {(ready?.pages || []).map(page => {
                          const chosen = (target.settings.page_ids as string[] | undefined) || [];
                          // Порожній вибір = всі Сторінки: так само трактує це
                          // товарний контур, і два різні правила тут collision-ом
                          // не потрібні.
                          const checked = chosen.length === 0 || chosen.includes(page.id);
                          return (
                            <label key={page.id}
                              className="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400">
                              <input type="checkbox" checked={checked}
                                onChange={() => {
                                  const all = (ready?.pages || []).map(item => item.id);
                                  const current = chosen.length === 0 ? all : chosen;
                                  const next = current.includes(page.id)
                                    ? current.filter(id => id !== page.id)
                                    : [...current, page.id];
                                  setTargetSetting(platform.key, 'page_ids',
                                    next.length === all.length ? [] : next);
                                }} />
                              {page.name}
                            </label>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <p className="mt-2 text-[10px] leading-relaxed text-gray-400">
              Кожна мережа бере свій формат — кадр під неї збирається з того самого макета.
              Instagram, Facebook і Viber публікують через хмару й не потребують запущеної
              програми; Telegram відправляє сама BMS зі своєї сесії.
            </p>
          </div>

          <div className={`${CARD} p-3`}>
            <div className={LABEL}>Підпис до поста</div>
            <textarea value={caption} rows={3} className={`${FIELD} mt-2`}
              onChange={event => setCaption(event.target.value)}
              placeholder="Текст, який піде разом із картинкою" />
          </div>

          <div className={`${CARD} p-3`}>
            <div className={LABEL}>Відправлення</div>

            <div className="mt-2 flex flex-wrap items-end gap-2">
              <label className={LABEL}>Опублікувати о
                <input type="datetime-local" className={`${FIELD} mt-1`} value={publishAt}
                  onChange={event => setPublishAt(event.target.value)} />
              </label>
              <button type="button" className={BTN_GHOST} disabled={busy !== null}
                onClick={() => void publish(true)}>
                {busy === 'rehearse' ? 'Перевіряю…' : 'Репетиція'}
              </button>
              <button type="button" disabled={busy !== null || !targets.length}
                onClick={armPublish}
                className={`${BTN} ${armed
                  ? 'bg-red-600 text-white hover:bg-red-700'
                  : 'bg-[var(--bms-accent)] text-white hover:opacity-90'} disabled:opacity-50`}>
                {busy === 'publish'
                  ? 'Відправляю…'
                  : armed ? 'Точно публікуємо?' : (publishAt ? 'Запланувати' : 'Опублікувати')}
              </button>
            </div>

            <p className="mt-2 text-[10px] leading-relaxed text-gray-400">
              «Репетиція» проходить увесь шлях, окрім самої відправки: збирає кадри, перевіряє
              підписи й час. Публікація йде в живі акаунти, тому кнопка спрацьовує з другого
              натискання. Порожній час = публікувати одразу.
            </p>

            {publishResult && (
              <div className="mt-3 space-y-1">
                {publishResult.results.map((row, index) => (
                  <div key={`${row.platform}-${index}`}
                    className={`flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-[11px] ${
                      row.ok
                        ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300'
                        : 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300'}`}>
                    <span>
                      {row.platform}
                      {row.page ? ` · ${row.page}` : ''}
                      {row.format ? ` · ${config.formats.find(item => item.key === row.format)?.label || row.format}` : ''}
                    </span>
                    <span className="text-[10px] opacity-80">
                      {row.error
                        || (row.dry_run ? `кадр ${Math.round((row.image_bytes || 0) / 1024)} КБ` : '')
                        || (row.cached ? 'уже відправлено раніше' : row.status || '')}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {Boolean(publications.length) && (
              <div className="mt-3 border-t border-gray-100 pt-2 dark:border-gray-700">
                <div className="flex items-center justify-between">
                  <div className={LABEL}>Уже відправлено</div>
                  <button type="button" className="text-[10px] text-gray-400 hover:underline"
                    onClick={() => void api.syncPublications(post.id).then(loadPublications)}>
                    оновити стани
                  </button>
                </div>
                <div className="mt-1.5 space-y-1">
                  {publications.map(row => (
                    <div key={row.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-gray-50 px-2 py-1.5 text-[11px] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                      <span>
                        {row.platform}
                        {row.account_label ? ` · ${row.account_label}` : ''}
                      </span>
                      <span className="text-[10px] text-gray-400">
                        {PUBLICATION_STATUS[row.status] || row.status}
                        {row.scheduled_at ? ` · ${fmtDateTime(row.scheduled_at)}` : ''}
                        {row.error ? ` · ${row.error}` : ''}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <AssetPicker
        open={pickerFor !== null}
        onClose={() => setPickerFor(null)}
        onPick={asset => {
          setAssets(current => (current.some(item => item.id === asset.id) ? current : [asset, ...current]));
          if (pickerFor === 'background') {
            setSpec(current => ({
              ...current,
              background: { ...current.background, type: 'asset', assetId: asset.id },
            }));
          } else {
            addImageLayer(asset);
          }
        }}
      />
    </div>
  );
};

export default StudioEditor;
