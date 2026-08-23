import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from './api';
import CanvasStage from './CanvasStage';
import LayerToolbar, { AlignKind } from './LayerToolbar';
import BackgroundPanel from './panels/BackgroundPanel';
import LayerPanel from './panels/LayerPanel';
import PublishPanel from './panels/PublishPanel';
import { AssetPicker } from './StudioGallery';
import { buildFontFaces, buildSvg, collectAssetDataUrls, svgToPngBlob, textBlockHeight } from './svg';
import { BTN, BTN_GHOST, BTN_MAIN, CARD, LABEL, Toggle } from './ui';
import {
  Background, CanvasFormat, CanvasFormatKey, DEFAULT_ADJUST, DEFAULT_EXTRUDE,
  DEFAULT_GRADIENT, DEFAULT_PHOTO_FILTER, DEFAULT_SCRIM, DEFAULT_SHADOW,
  DEFAULT_STROKE, DEFAULT_VIGNETTE, ImageLayer, Layer, PlatformKey, PostSpec,
  PostTarget, StudioAsset, StudioConfig, StudioFont, StudioPost, TextLayer,
  TextRole, TEXT_ROLE_PRESETS, newId, normalizeSpec,
} from './types';
import { useIsActivePage } from '../../../contexts/ActivePageContext';

/**
 * Конструктор поста.
 *
 * Малює браузер, і саме той SVG, який людина бачить, стає растром — див.
 * `svg.ts`. Тому «приблизного прев'ю» тут немає за побудовою.
 *
 * Будова екрана: ліворуч полотно з плаваючою смугою над обраним шаром,
 * праворуч — ОДИН інструмент за раз. Це свідома заміна попередньої суцільної
 * колонки: коли всі двадцять груп налаштувань відкриті одночасно, знайти
 * потрібну важче, ніж її не мати.
 */

const MARGIN_RATIO = 0.08;

/** Порожній макет під формат. */
export const emptySpec = (format: CanvasFormatKey): PostSpec => ({
  version: 1,
  format,
  background: {
    type: 'color', color: '#F4F1F6', assetId: null, fit: 'cover',
    scale: 1, offsetX: 0, offsetY: 0, overlay: '#000000', overlayOpacity: 0,
    filter: { ...DEFAULT_PHOTO_FILTER },
    adjust: { ...DEFAULT_ADJUST },
    vignette: { ...DEFAULT_VIGNETTE },
    scrim: { ...DEFAULT_SCRIM },
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

type Tool = 'canvas' | 'background' | 'layer' | 'layers' | 'publish';

const TOOLS: Array<{ key: Tool; label: string }> = [
  { key: 'canvas', label: 'Полотно' },
  { key: 'background', label: 'Фон' },
  { key: 'layer', label: 'Шар' },
  { key: 'layers', label: 'Шари' },
  { key: 'publish', label: 'Публікація' },
];

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
  // Старі чернетки не мають полів ефектів і геометрії — домальовуємо їх при
  // відкритті, інакше редактор упав би на `background.adjust.flipX`.
  const [spec, setSpec] = useState<PostSpec>(
    post.spec?.layers ? normalizeSpec(post.spec, post.base_format) : emptySpec(post.base_format),
  );
  const [targets, setTargets] = useState<PostTarget[]>(post.targets || []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tool, setTool] = useState<Tool>('canvas');
  const [assets, setAssets] = useState<StudioAsset[]>([]);
  const [pickerFor, setPickerFor] = useState<'background' | 'layer' | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [stageWidth, setStageWidth] = useState(360);
  const [showGrid, setShowGrid] = useState(false);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const [readiness, setReadiness] = useState<api.PublishReadiness | null>(null);
  const [publications, setPublications] = useState<api.StudioPublication[]>([]);
  const [publishResult, setPublishResult] = useState<api.PublishResult | null>(null);
  const [publishAt, setPublishAt] = useState('');
  // Публікація йде в живі акаунти, тому кнопка спрацьовує лише з другого
  // натискання — той самий запобіжник, що й у черзі Stories.
  const [armed, setArmed] = useState(false);
  const isActivePage = useIsActivePage();

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
      const available = Math.max(320, window.innerHeight - 300);
      setStageWidth(Math.min(460, Math.round(available * (format.width / format.height))));
    };
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
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

  const patchBackground = useCallback((patch: Partial<Background>) => {
    setSpec(current => ({ ...current, background: { ...current.background, ...patch } }));
  }, []);

  /* ── Історія (⌘Z) ──────────────────────────────────────────────────── */

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

  /* ── Шари ──────────────────────────────────────────────────────────── */

  const selectLayer = useCallback((id: string | null) => {
    setSelectedId(id);
    // Обрали шар — інструмент сам стає тим, який зараз потрібен. Це те, чого
    // бракувало: раніше по кожен дріб'язок доводилось шукати місце в панелі.
    if (id) setTool('layer');
  }, []);

  const addText = (role: TextRole) => {
    const preset = TEXT_ROLE_PRESETS[role];
    const bottom = spec.layers.reduce((acc, layer) => Math.max(
      acc, layer.type === 'text' ? layer.y + textBlockHeight(layer) : layer.y + layer.height,
    ), format.height * 0.12);
    const layer: TextLayer = {
      id: newId(), type: 'text', role,
      text: preset.label,
      x: Math.round(format.width * MARGIN_RATIO),
      y: Math.round(Math.min(bottom + format.height * 0.03, format.height * 0.82)),
      width: Math.round(format.width * (1 - MARGIN_RATIO * 2)),
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
    selectLayer(layer.id);
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
      flipX: false, flipY: false,
    };
    setSpec(current => ({ ...current, layers: [...current.layers, layer] }));
    selectLayer(layer.id);
  };

  const removeLayer = useCallback((id: string) => {
    setSpec(current => ({ ...current, layers: current.layers.filter(layer => layer.id !== id) }));
    setSelectedId(null);
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

  /** Зсув шару стрілками. Читає САМЕ поточний стан, а не знімок із замикання:
   *  два швидкі натискання потрапляють в один такт React, і обидва порахували б
   *  крок від того самого старого значення. */
  const nudgeLayer = useCallback((id: string, dx: number, dy: number) => {
    setSpec(current => ({
      ...current,
      layers: current.layers.map(layer => (
        layer.id === id ? ({ ...layer, x: layer.x + dx, y: layer.y + dy } as Layer) : layer
      )),
    }));
  }, []);

  /** Вирівнювання одним рухом — замість ловіння центру мишею. */
  const alignLayer = useCallback((kind: AlignKind) => {
    if (!selectedId) return;
    setSpec(current => {
      const margin = Math.round(format.width * MARGIN_RATIO);
      return {
        ...current,
        layers: current.layers.map(layer => {
          if (layer.id !== selectedId) return layer;
          const height = layer.type === 'text' ? textBlockHeight(layer) : layer.height;
          switch (kind) {
            case 'left': return { ...layer, x: margin };
            case 'centerX': return { ...layer, x: Math.round((format.width - layer.width) / 2) };
            case 'right': return { ...layer, x: Math.round(format.width - margin - layer.width) };
            case 'top': return { ...layer, y: margin };
            case 'centerY': return { ...layer, y: Math.round((format.height - height) / 2) };
            case 'bottom': return { ...layer, y: Math.round(format.height - margin - height) };
            case 'fitWidth': return { ...layer, x: margin, width: format.width - margin * 2 };
            default: return layer;
          }
        }),
      };
    });
  }, [format.height, format.width, selectedId]);

  // Клавіатура. isActivePage обов'язковий: сторінки залишаються змонтованими
  // при перемиканні верхніх вкладок, і без нього ⌘Z із «Товарів» скасовував би
  // правку в макеті, якого людина навіть не бачить.
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

  /* ── Рендер і збереження ───────────────────────────────────────────── */

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

  /** Зберегти, добудувати відсутні кадри й віддати пост мережам. */
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
        try { onSaved(await api.fetchPost(post.id)); } catch { /* оновиться при виході */ }
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

  const enabledTargets = targets.filter(target => target.enabled !== false);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <button type="button" onClick={onClose} className={BTN_GHOST}>← До постів</button>
          <input value={title} onChange={event => setTitle(event.target.value)}
            className="rounded-lg border border-transparent px-2 py-1 text-sm font-semibold text-gray-900 hover:border-gray-200 focus:border-gray-300 dark:text-gray-100 dark:hover:border-gray-600"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <button type="button" onClick={undo} disabled={!canUndo} title="Скасувати (⌘Z)"
            className={`${BTN_GHOST} w-9 px-0 text-center`}>↶</button>
          <button type="button" onClick={redo} disabled={!canRedo} title="Повернути (⇧⌘Z)"
            className={`${BTN_GHOST} w-9 px-0 text-center`}>↷</button>
          <Toggle active={showGrid} onClick={() => setShowGrid(current => !current)}
            title="Сітка третин і поля" className="w-9 px-0 text-center">#</Toggle>
          <span className="mx-1 h-5 w-px bg-gray-200 dark:bg-gray-700" />
          <button type="button" onClick={() => void remove()}
            className={`${BTN} text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20`}>
            Видалити
          </button>
          <button type="button" disabled={busy !== null} onClick={() => void save(false)}
            className={BTN_GHOST}>
            {busy === 'save' ? 'Зберігаю…' : 'Зберегти чернетку'}
          </button>
          <button type="button" disabled={busy !== null} onClick={() => void save(true)}
            className={BTN_MAIN}>
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
        <div className="flex flex-col items-center gap-2">
          <div className="min-h-[2.5rem] w-full" style={{ maxWidth: stageWidth }}>
            {selected && (
              <LayerToolbar
                layer={selected}
                fonts={fonts}
                onPatch={patch => patchLayer(selected.id, patch)}
                onAlign={alignLayer}
                onDuplicate={() => duplicateLayer(selected.id)}
                onRemove={() => removeLayer(selected.id)}
              />
            )}
          </div>

          <CanvasStage
            spec={spec}
            format={format}
            svg={svg}
            width={stageWidth}
            selectedId={selectedId}
            backgroundMode={tool === 'background' || tool === 'canvas'}
            showGrid={showGrid}
            onSelect={selectLayer}
            onPatchLayer={patchLayer}
            onPatchBackground={patchBackground}
          />

          <div className="flex flex-wrap justify-center gap-1.5" style={{ maxWidth: stageWidth }}>
            {(Object.keys(TEXT_ROLE_PRESETS) as TextRole[]).map(role => (
              <button key={role} type="button" onClick={() => addText(role)} className={BTN_GHOST}>
                + {TEXT_ROLE_PRESETS[role].label}
              </button>
            ))}
            <button type="button" className={BTN_GHOST} onClick={() => setPickerFor('layer')}>
              + Фото
            </button>
          </div>
        </div>

        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {TOOLS.map(item => (
              <Toggle key={item.key} active={tool === item.key}
                onClick={() => setTool(item.key)}>
                {item.label}
                {item.key === 'publish' && enabledTargets.length
                  ? ` · ${enabledTargets.length}` : ''}
              </Toggle>
            ))}
          </div>

          <div className={`${CARD} px-3 py-2`}>
            {tool === 'canvas' && (
              <div className="space-y-3 py-1">
                <div>
                  <div className={LABEL}>Формат полотна</div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {config.formats.map(item => (
                      <Toggle key={item.key} active={spec.format === item.key}
                        onClick={() => changeFormat(item.key)}>
                        {item.label}
                      </Toggle>
                    ))}
                  </div>
                  <p className="mt-2 text-[10px] leading-relaxed text-gray-400">
                    Зміна формату не ламає макет: шари перераховуються пропорційно.
                    Кожна мережа може взяти власний формат — див. «Публікація».
                  </p>
                </div>
                <div className="border-t border-gray-100 pt-3 dark:border-gray-700">
                  <div className={LABEL}>Підказки</div>
                  <ul className="mt-1.5 space-y-1 text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
                    <li>Фон тягнеться мишею просто на полотні, колесо — масштаб.</li>
                    <li>Шар прилипає до країв, полів і центру; напрямні показують, до чого саме.</li>
                    <li>Стрілки рухають шар на 1 px, ⇧+стрілки — на 10.</li>
                    <li>⌘Z — скасувати, ⇧⌘Z — повернути, ⌘D — дублювати, Delete — прибрати.</li>
                  </ul>
                </div>
              </div>
            )}

            {tool === 'background' && (
              <BackgroundPanel
                background={spec.background}
                onChange={patchBackground}
                onPickPhoto={() => setPickerFor('background')}
              />
            )}

            {tool === 'layer' && (
              selected ? (
                <LayerPanel
                  layer={selected}
                  fonts={fonts}
                  canvasHeight={format.height}
                  onPatch={patch => patchLayer(selected.id, patch)}
                />
              ) : (
                <p className="py-6 text-center text-[11px] text-gray-400">
                  Оберіть шар на полотні або у вкладці «Шари».
                </p>
              )
            )}

            {tool === 'layers' && (
              <div className="space-y-1 py-1">
                {!spec.layers.length && (
                  <p className="py-6 text-center text-[11px] text-gray-400">
                    Шарів ще немає. Почніть із заголовка — кнопки під полотном.
                  </p>
                )}
                {[...spec.layers].reverse().map(layer => (
                  <div key={layer.id}
                    onClick={() => selectLayer(layer.id)}
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
                      <button type="button" title="Вище"
                        onClick={event => { event.stopPropagation(); moveLayer(layer.id, 1); }}>↑</button>
                      <button type="button" title="Нижче"
                        onClick={event => { event.stopPropagation(); moveLayer(layer.id, -1); }}>↓</button>
                      <button type="button" title="Дублювати (⌘D)"
                        onClick={event => { event.stopPropagation(); duplicateLayer(layer.id); }}>⧉</button>
                      <button type="button" title="Прибрати" className="text-red-500"
                        onClick={event => { event.stopPropagation(); removeLayer(layer.id); }}>×</button>
                    </span>
                  </div>
                ))}
              </div>
            )}

            {tool === 'publish' && (
              <PublishPanel
                config={config}
                targets={targets}
                readiness={readiness}
                caption={caption}
                publishAt={publishAt}
                busy={busy}
                armed={armed}
                publishResult={publishResult}
                publications={publications}
                onToggleTarget={toggleTarget}
                onTargetFormat={setTargetFormat}
                onTargetSetting={setTargetSetting}
                onCaption={setCaption}
                onPublishAt={setPublishAt}
                onRehearse={() => void publish(true)}
                onPublish={armPublish}
                onSync={() => void api.syncPublications(post.id).then(loadPublications)}
              />
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
            patchBackground({ type: 'asset', assetId: asset.id });
            setTool('background');
          } else {
            addImageLayer(asset);
          }
        }}
      />
    </div>
  );
};

export default StudioEditor;
