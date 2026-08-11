import React, { useEffect, useRef, useState } from 'react';
import {
  CheckOutlined, CloseOutlined, ClockCircleOutlined, DragOutlined,
  LoadingOutlined, ReloadOutlined, SendOutlined, SwapOutlined, WarningOutlined,
} from '@ant-design/icons';
import SmartImage from '../common/SmartImage';
import { productService } from '../../services/productService';
import { emitProductPhotosChanged, taskManager } from '../../services/taskManager';

export interface ViberCollageFrame {
  image_idx: number;
  zoom: number;
  x: number;
  y: number;
}

export interface ViberCollageSpec {
  version: number;
  width: number;
  height: number;
  image_idx: number[];
  layout: 'auto' | 'hero' | 'grid';
  background: 'white' | 'soft' | 'warm' | 'dark';
  gap: number;
  column_split: number;
  left_split: number;
  right_top: number;
  right_middle: number;
  frames: ViberCollageFrame[];
}

export interface ViberPreview {
  ok: boolean;
  product_id: number;
  productnumber: string;
  brand: string | null;
  model: string | null;
  type: string | null;
  condition: string | null;
  condition_name: string | null;
  condition_confirmation_required: boolean;
  caption: string;
  caption_len: number;
  caption_limit: number;
  sizes: { product_id: number; size: string; measurementscm: string; available: number }[];
  image_count: number;
  image_kind: 'official' | 'real' | 'none';
  image_urls: string[];
  image_names: string[];
  default_image_idx: number[];
  collage: ViberCollageSpec;
  layouts: { key: ViberCollageSpec['layout']; label: string }[];
  backgrounds: { key: ViberCollageSpec['background']; label: string }[];
  channel: { title: string };
  connection: {
    configured: boolean;
    live_publish_available: boolean;
    schedule_available: boolean;
    missing: string[];
    collage: { width: number; height: number; max_bytes: number; max_photos: number };
  };
  already_published: number;
  pending_publications: number;
  batch_max_products: number;
  default_publish_at: string;
  warnings: string[];
}

export interface ViberPublishPayload {
  caption: string;
  collage: ViberCollageSpec;
  publish_at: string | null;
  idempotency_key: string;
  force?: boolean;
  condition_confirmed?: boolean;
  dry_run?: boolean;
}

interface Props {
  data: ViberPreview;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (payload: ViberPublishPayload) => void;
  mode?: 'publish' | 'draft';
  initialPayload?: ViberPublishPayload;
  /** Канонічне редагування фото оновлює картку і в пакетному прев'ю. */
  onPreviewChange?: (preview: ViberPreview) => void;
}

const VIBER_PURPLE = '#7360F2';
const INPUT = 'w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm text-gray-800 outline-none transition focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100';
const LABEL = 'text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500';
const BACKGROUNDS: Record<ViberCollageSpec['background'], string> = {
  white: '#ffffff', soft: '#f4f6f8', warm: '#f8f5f0', dark: '#181b20',
};
const VIBER_GRID_DEFAULTS = {
  gap: 4,
  column_split: 0.63,
  left_split: 0.505,
  right_top: 0.347,
  right_middle: 0.307,
};
const FRAME_ZOOM_MIN = 0.5;
const FRAME_ZOOM_MAX = 3;

function uuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `viber-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function withImageVersion(url: string, version: string): string {
  const [path, query = ''] = url.split('?');
  const params = new URLSearchParams(query);
  params.set('v', version);
  return `${path}?${params.toString()}`;
}

function asLocal(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function normalizeFrames(spec: ViberCollageSpec, imageIdx: number[]): ViberCollageSpec {
  const byIndex = new Map(spec.frames.map(frame => [frame.image_idx, frame]));
  return {
    ...spec,
    image_idx: imageIdx,
    frames: imageIdx.map(index => byIndex.get(index) ?? { image_idx: index, zoom: 1, x: 0, y: 0 }),
  };
}

function initialSpec(data: ViberPreview, payload?: ViberPublishPayload): ViberCollageSpec {
  const source = payload?.collage ?? data.collage;
  const selected = (source.image_idx?.length ? source.image_idx : data.default_image_idx).slice(0, 5);
  return normalizeFrames({
    ...source,
    version: 1,
    width: 1080,
    height: 1080,
    layout: source.layout || 'auto',
    background: source.background || 'white',
    gap: source.gap ?? VIBER_GRID_DEFAULTS.gap,
    column_split: source.column_split ?? VIBER_GRID_DEFAULTS.column_split,
    left_split: source.left_split ?? VIBER_GRID_DEFAULTS.left_split,
    right_top: source.right_top ?? VIBER_GRID_DEFAULTS.right_top,
    right_middle: source.right_middle ?? VIBER_GRID_DEFAULTS.right_middle,
  }, selected);
}

type FrameControlKey = 'zoom' | 'x' | 'y';
type ViberGridKey = 'column_split' | 'left_split' | 'right_top' | 'right_middle';

function clampFrameValue(key: FrameControlKey, value: number): number {
  return Math.max(
    key === 'zoom' ? FRAME_ZOOM_MIN : -1,
    Math.min(key === 'zoom' ? FRAME_ZOOM_MAX : 1, value),
  );
}

export const ViberConditionPublishConfirmation: React.FC<{
  items: { productnumber: string; conditionName: string; title?: string }[];
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}> = ({ items, busy = false, onCancel, onConfirm }) => {
  if (!items.length) return null;
  return (
    <div className="fixed inset-0 z-[150] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={busy ? undefined : onCancel} />
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-amber-200 bg-white shadow-2xl dark:border-amber-800 dark:bg-gray-900">
        <div className="flex gap-3 px-5 pt-5">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"><WarningOutlined /></span>
          <div>
            <div className="font-semibold text-gray-900 dark:text-gray-50">Перевір стан перед Viber-публікацією</div>
            <p className="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">Цей стан буде прямо вказаний у пості й його побачать підписники каналу.</p>
          </div>
        </div>
        <div className="mx-5 mt-4 max-h-52 space-y-2 overflow-y-auto">
          {items.map(item => (
            <div key={`${item.productnumber}-${item.conditionName}`} className="rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2.5 dark:border-amber-800 dark:bg-amber-900/20">
              <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">#{item.productnumber} · {item.conditionName}</div>
              {item.title && <div className="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">{item.title}</div>}
            </div>
          ))}
        </div>
        <div className="mt-5 flex justify-end gap-2 border-t border-gray-100 px-5 py-3.5 dark:border-gray-800">
          <button type="button" onClick={onCancel} disabled={busy} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300">Повернутися</button>
          <button type="button" onClick={onConfirm} disabled={busy} className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">Так, опублікувати</button>
        </div>
      </div>
    </div>
  );
};

export const ViberLivePublishConfirmation: React.FC<{
  count: number;
  channelTitle: string;
  publishAt?: string | null;
  scheduledCount?: number;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}> = ({ count, channelTitle, publishAt, scheduledCount = publishAt ? 1 : 0, busy = false, onCancel, onConfirm }) => {
  const scheduled = publishAt ? new Date(publishAt) : null;
  const validSchedule = scheduled && !Number.isNaN(scheduled.getTime()) ? scheduled : null;
  return (
    <div className="fixed inset-0 z-[155] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={busy ? undefined : onCancel} />
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-violet-200 bg-white shadow-2xl dark:border-violet-800 dark:bg-gray-900">
        <div className="flex gap-3 px-5 pt-5">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-violet-100 font-black text-violet-700 dark:bg-violet-900/40 dark:text-violet-300">V</span>
          <div>
            <div className="font-semibold text-gray-900 dark:text-gray-50">Підтвердити реальну Viber-публікацію</div>
            <p className="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
              {validSchedule
                ? `${count} ${count === 1 ? 'пост буде' : 'пости будуть'} поставлено в розклад на ${validSchedule.toLocaleString('uk-UA')}.`
                : scheduledCount > 0
                  ? `${scheduledCount} із ${count} постів підуть за індивідуальним розкладом${scheduledCount < count ? `, ще ${count - scheduledCount} — одразу` : ''}.`
                  : `${count} ${count === 1 ? 'пост піде' : 'пости підуть'} у канал одразу після підтвердження.`}
            </p>
          </div>
        </div>
        <div className="mx-5 mt-4 rounded-xl border border-violet-200 bg-violet-50/70 px-3 py-2.5 text-sm font-medium text-gray-800 dark:border-violet-800 dark:bg-violet-900/20 dark:text-gray-100">
          Канал: «{channelTitle}»
        </div>
        <div className="mx-5 mt-3 text-[11px] leading-relaxed text-gray-400">
          Це не тест. Після відправлення Channels Post API не дає BMS безпечної команди видалити пост.
        </div>
        <div className="mt-5 flex justify-end gap-2 border-t border-gray-100 px-5 py-3.5 dark:border-gray-800">
          <button type="button" onClick={onCancel} disabled={busy} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300">Повернутися</button>
          <button type="button" onClick={onConfirm} disabled={busy} className="rounded-lg bg-[#7360F2] px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
            {validSchedule || scheduledCount > 0 ? 'Так, підтвердити розклад' : 'Так, опублікувати зараз'}
          </button>
        </div>
      </div>
    </div>
  );
};

const ViberPublishDialog: React.FC<Props> = ({
  data, busy, onCancel, onConfirm, mode = 'publish', initialPayload, onPreviewChange,
}) => {
  const draftMode = mode === 'draft';
  const [caption, setCaption] = useState(initialPayload?.caption ?? data.caption);
  const [collage, setCollage] = useState<ViberCollageSpec>(() => initialSpec(data, initialPayload));
  const [activeImage, setActiveImage] = useState<number>(() => initialSpec(data, initialPayload).image_idx[0] ?? 0);
  const [editTargets, setEditTargets] = useState<number[]>(() => {
    const first = initialSpec(data, initialPayload).image_idx[0];
    return first === undefined ? [] : [first];
  });
  const [groupAdjust, setGroupAdjust] = useState<Record<FrameControlKey, number>>({ zoom: 0, x: 0, y: 0 });
  const [timing, setTiming] = useState<'now' | 'custom'>(() => initialPayload?.publish_at ? 'custom' : 'now');
  const [customAt, setCustomAt] = useState(asLocal(initialPayload?.publish_at ?? data.default_publish_at));
  const [force, setForce] = useState(initialPayload?.force ?? false);
  const [idempotencyKey] = useState(initialPayload?.idempotency_key ?? uuid());
  const [renderUrl, setRenderUrl] = useState<string | null>(null);
  const [renderBytes, setRenderBytes] = useState<number | null>(null);
  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [conditionConfirmOpen, setConditionConfirmOpen] = useState(false);
  const [liveConfirmOpen, setLiveConfirmOpen] = useState(false);
  const [conditionApproved, setConditionApproved] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<string | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [imageUrls, setImageUrls] = useState<string[]>(data.image_urls);
  const [mirroringImage, setMirroringImage] = useState<number | null>(null);
  const [photoRevision, setPhotoRevision] = useState(0);
  const renderUrlRef = useRef<string | null>(null);
  const renderSequence = useRef(0);
  const groupBaseRef = useRef<Map<number, ViberCollageFrame>>(new Map());
  const thumbnailDragRef = useRef(false);

  useEffect(() => () => {
    if (renderUrlRef.current) URL.revokeObjectURL(renderUrlRef.current);
  }, []);

  useEffect(() => {
    if (!collage.image_idx.length) return;
    const sequence = ++renderSequence.current;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setRendering(true);
      setRenderError(null);
      fetch('/api/publications/viber/render-collage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: data.product_id, collage }),
        signal: controller.signal,
      })
        .then(async response => {
          if (!response.ok) {
            const detail = await response.json().catch(() => ({}));
            throw new Error(detail.detail || 'Не вдалося зібрати колаж');
          }
          const blob = await response.blob();
          return { blob, bytes: Number(response.headers.get('X-BMS-Image-Bytes')) || blob.size };
        })
        .then(({ blob, bytes }) => {
          if (sequence !== renderSequence.current) return;
          const url = URL.createObjectURL(blob);
          if (renderUrlRef.current) URL.revokeObjectURL(renderUrlRef.current);
          renderUrlRef.current = url;
          setRenderUrl(url);
          setRenderBytes(bytes);
        })
        .catch(error => {
          if (error?.name !== 'AbortError' && sequence === renderSequence.current) {
            setRenderError(error?.message || 'Не вдалося зібрати колаж');
          }
        })
        .finally(() => { if (sequence === renderSequence.current) setRendering(false); });
    }, 260);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [collage, data.product_id, photoRevision]);

  const primaryEditImage = editTargets.includes(activeImage) ? activeImage : editTargets[0];
  const activeFrame = collage.frames.find(frame => frame.image_idx === primaryEditImage) ?? collage.frames[0];
  const editingMany = editTargets.length > 1;
  const captionProblem = !caption.trim()
    ? 'Підпис порожній'
    : caption.length > data.caption_limit ? `Перевищено ліміт на ${caption.length - data.caption_limit} символів` : null;
  const scheduleDate = customAt ? new Date(customAt) : null;
  const scheduleProblem = timing === 'custom' && (
    !scheduleDate || Number.isNaN(scheduleDate.getTime())
      ? 'Вкажи коректний час'
      : scheduleDate.getTime() < Date.now() + 2 * 60_000
        ? 'Не раніше ніж через 2 хвилини'
        : scheduleDate.getTime() > Date.now() + 365 * 24 * 60 * 60_000
          ? 'Не далі ніж на 365 днів'
          : null
  );
  const blockedRepeat = !draftMode && (data.already_published > 0 || data.pending_publications > 0) && !force;
  const dialogBusy = busy || mirroringImage !== null;
  const cannotPublish = dialogBusy || rendering || !collage.image_idx.length || !!captionProblem
    || !!scheduleProblem || blockedRepeat || (!draftMode && !data.connection.live_publish_available);

  const updateSelected = (next: number[]) => {
    const unique = Array.from(new Set(next)).slice(0, 5);
    if (!unique.length) return;
    setCollage(current => normalizeFrames(current, unique));
    if (!unique.includes(activeImage)) setActiveImage(unique[0]);
    setEditTargets(current => {
      const kept = current.filter(index => unique.includes(index));
      return kept.length ? kept : [unique[0]];
    });
    setGroupAdjust({ zoom: 0, x: 0, y: 0 });
  };

  const toggleImage = (index: number) => {
    if (collage.image_idx.includes(index)) {
      if (collage.image_idx.length > 1) updateSelected(collage.image_idx.filter(value => value !== index));
    } else if (collage.image_idx.length < 5) {
      updateSelected([...collage.image_idx, index]);
      setActiveImage(index);
      setEditTargets([index]);
    }
  };

  const moveSelected = (from: number, to: number) => {
    if (from === to || from < 0 || to < 0 || from >= collage.image_idx.length || to >= collage.image_idx.length) return;
    const next = [...collage.image_idx];
    const [value] = next.splice(from, 1);
    next.splice(to, 0, value);
    updateSelected(next);
  };

  const finishThumbnailDrag = () => {
    setDragIndex(null);
    window.setTimeout(() => { thumbnailDragRef.current = false; }, 100);
  };

  const updateFrame = (patch: Partial<ViberCollageFrame>) => {
    if (!activeFrame) return;
    setCollage(current => ({
      ...current,
      frames: current.frames.map(frame => frame.image_idx === activeFrame.image_idx ? { ...frame, ...patch } : frame),
    }));
  };

  const toggleEditTarget = (index: number) => {
    setEditTargets(current => {
      const next = current.includes(index)
        ? (current.length > 1 ? current.filter(value => value !== index) : current)
        : [...current, index];
      const ordered = collage.image_idx.filter(value => next.includes(value));
      groupBaseRef.current = new Map(
        collage.frames.filter(frame => ordered.includes(frame.image_idx)).map(frame => [frame.image_idx, { ...frame }]),
      );
      setActiveImage(ordered.includes(index) ? index : ordered[0]);
      return ordered;
    });
    setGroupAdjust({ zoom: 0, x: 0, y: 0 });
  };

  const selectAllFrames = () => {
    setEditTargets([...collage.image_idx]);
    groupBaseRef.current = new Map(
      collage.frames.map(frame => [frame.image_idx, { ...frame }]),
    );
    setGroupAdjust({ zoom: 0, x: 0, y: 0 });
  };

  const updateFramesTogether = (key: FrameControlKey, nextValue: number) => {
    setGroupAdjust(current => ({ ...current, [key]: nextValue }));
    const targets = new Set(editTargets);
    setCollage(current => ({
      ...current,
      frames: current.frames.map(frame => {
        if (!targets.has(frame.image_idx)) return frame;
        const base = groupBaseRef.current.get(frame.image_idx) ?? frame;
        return { ...frame, [key]: clampFrameValue(key, base[key] + nextValue) };
      }),
    }));
  };

  const resetEditedFrames = () => {
    const targets = new Set(editTargets);
    setCollage(current => ({
      ...current,
      frames: current.frames.map(frame => targets.has(frame.image_idx)
        ? { ...frame, zoom: 1, x: 0, y: 0 }
        : frame),
    }));
    groupBaseRef.current = new Map(
      editTargets.map(index => [index, { image_idx: index, zoom: 1, x: 0, y: 0 }]),
    );
    setGroupAdjust({ zoom: 0, x: 0, y: 0 });
  };

  const updateViberGrid = (key: ViberGridKey, value: number) => {
    setCollage(current => {
      const next = { ...current, [key]: value };
      if (key === 'right_top' || key === 'right_middle') {
        const sum = next.right_top + next.right_middle;
        if (sum > 0.82) {
          const otherKey = key === 'right_top' ? 'right_middle' : 'right_top';
          next[otherKey] = Math.max(0.18, 0.82 - value);
        }
      }
      return next;
    });
  };

  const resetViberGrid = () => {
    setCollage(current => ({ ...current, ...VIBER_GRID_DEFAULTS }));
  };

  const mirrorPhoto = async (index: number) => {
    const filename = data.image_names[index];
    if (!filename || dialogBusy) return;
    setMirroringImage(index);
    setCheckResult(null);
    setCheckError(null);
    try {
      const result = await taskManager.run(
        `Віддзеркалення фото #${data.productnumber}`,
        () => productService.transformProductPhoto(data.product_id, filename, 'flip_horizontal'),
        {
          successMsg: 'Фото віддзеркалено та синхронізовано з Cloudflare.',
          errorMsg: 'Фото не віддзеркалено',
        },
      );
      const version = result.version || Date.now().toString(36);
      const nextUrls = imageUrls.map((url, imageIndex) => (
        imageIndex === index ? withImageVersion(url, version) : url
      ));
      setImageUrls(nextUrls);
      setPhotoRevision(current => current + 1);
      onPreviewChange?.({ ...data, image_urls: nextUrls });
      emitProductPhotosChanged(data.product_id);
    } catch {
      // Task Center уже показав точну помилку й залишив канонічне фото без змін.
    } finally {
      setMirroringImage(null);
    }
  };

  const payload = (conditionConfirmed = false): ViberPublishPayload => ({
    caption: caption.trim(),
    collage,
    publish_at: timing === 'custom' && scheduleDate && !Number.isNaN(scheduleDate.getTime()) ? scheduleDate.toISOString() : null,
    idempotency_key: idempotencyKey,
    force: force || undefined,
    condition_confirmed: conditionConfirmed || undefined,
  });

  const submit = () => {
    if (cannotPublish) return;
    if (!draftMode && data.condition_confirmation_required) {
      setConditionConfirmOpen(true);
      return;
    }
    if (!draftMode) {
      setConditionApproved(false);
      setLiveConfirmOpen(true);
      return;
    }
    onConfirm(payload());
  };

  const runSafeCheck = async () => {
    if (checking || dialogBusy || rendering || !collage.image_idx.length || captionProblem || scheduleProblem) return;
    setChecking(true);
    setCheckResult(null);
    setCheckError(null);
    try {
      const response = await fetch('/api/publications/viber/create-post', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: data.product_id, ...payload(), dry_run: true }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Перевірка не вдалася');
      setCheckResult(`Перевірено: колаж ${Math.ceil(Number(result.image_bytes || 0) / 1024)} КБ, підпис і розклад коректні. У Viber нічого не надіслано.`);
    } catch (error: any) {
      setCheckError(error?.message || 'Перевірка не вдалася');
    } finally {
      setChecking(false);
    }
  };

  const previewTitle = [data.brand, data.model, data.type].filter(Boolean).join(' ');
  const zoomControlLabel = editingMany
    ? `Масштаб разом · ${groupAdjust.zoom === 0 ? 'без змін' : `${groupAdjust.zoom > 0 ? '+' : ''}${Math.round(groupAdjust.zoom * 100)}%`}`
    : `Масштаб · ${Math.round((activeFrame?.zoom ?? 1) * 100)}%`;

  return (
    <div className="bms-dialog-host fixed inset-0 z-[110] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={dialogBusy ? undefined : onCancel} />
      <div className="relative flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900">
        <header className="flex items-center gap-3 border-b border-gray-100 px-5 py-4 dark:border-gray-800">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-lg font-black text-white" style={{ background: VIBER_PURPLE }}>V</span>
          <div className="min-w-0 flex-1">
            <div className="font-semibold text-gray-900 dark:text-gray-50">{draftMode ? 'Редагування Viber-картки' : 'Публікація у Viber'}</div>
            <div className="mt-0.5 truncate text-xs text-gray-400">#{data.productnumber} · один колаж і підпис у «{data.channel.title}»</div>
          </div>
          <button type="button" onClick={dialogBusy ? undefined : onCancel} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-800" disabled={dialogBusy} aria-label="Закрити"><CloseOutlined /></button>
        </header>

        <div className="grid flex-1 grid-cols-1 gap-0 overflow-y-auto lg:grid-cols-[1.1fr_0.9fr] lg:overflow-hidden">
          <section className="space-y-4 p-5 lg:overflow-y-auto">
            <div>
              <div className="flex items-center justify-between gap-2">
                <span className={LABEL}>Фото для колажу · {collage.image_idx.length} з {Math.min(5, data.image_count)}</span>
                <span className="text-[11px] text-gray-400">порядок = розташування</span>
              </div>
              <div className="mt-2 flex gap-1.5 overflow-x-auto pb-1">
                {imageUrls.map((url, index) => {
                  const order = collage.image_idx.indexOf(index);
                  const selected = order >= 0;
                  return (
                    <div key={data.image_names[index] || index} className="group/viber-photo relative h-16 w-16 shrink-0">
                      <button type="button" draggable={selected && !dialogBusy}
                              disabled={dialogBusy}
                              onDragStart={() => { thumbnailDragRef.current = true; setDragIndex(order); }}
                              onDragEnd={finishThumbnailDrag}
                              onDragOver={event => { if (selected) event.preventDefault(); }}
                              onDrop={() => { if (selected && dragIndex !== null) moveSelected(dragIndex, order); finishThumbnailDrag(); }}
                              onClick={() => { if (!thumbnailDragRef.current) toggleImage(index); }}
                              aria-label={selected ? `Прибрати фото ${order + 1} з колажу` : `Додати фото ${index + 1} до колажу`}
                              className={`relative h-full w-full overflow-hidden rounded-lg border-2 bg-gray-100 transition-all disabled:cursor-wait ${selected ? 'border-violet-500' : 'border-transparent opacity-40 hover:opacity-70'}`}
                              title={selected ? 'Клік — прибрати; перетягни для зміни порядку' : 'Клік — додати до колажу'}>
                        <SmartImage src={url} thumb={320} thumbOnly className="h-full w-full object-cover" />
                        {selected && <span className="absolute left-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-violet-500 px-1 text-[10px] font-bold text-white shadow">{order + 1}</span>}
                        {selected && <span className="absolute bottom-1 left-1 inline-flex h-5 w-5 items-center justify-center rounded-full bg-gray-950/75 text-white opacity-80 shadow backdrop-blur-sm" title="Перетягни для зміни порядку"><DragOutlined style={{ fontSize: 10 }} /></span>}
                      </button>
                      <button type="button"
                              disabled={dialogBusy || !data.image_names[index]}
                              onClick={event => { event.stopPropagation(); void mirrorPhoto(index); }}
                              aria-label={`Віддзеркалити фото ${index + 1}`}
                              title="Віддзеркалити горизонтально та зберегти в BMS і Cloudflare"
                              className="absolute bottom-1 right-1 z-10 inline-flex h-5 w-5 items-center justify-center rounded-full bg-gray-950/75 text-white opacity-80 shadow backdrop-blur-sm transition hover:bg-violet-600 hover:opacity-100 active:scale-95 disabled:cursor-wait disabled:opacity-50">
                        {mirroringImage === index
                          ? <LoadingOutlined spin style={{ fontSize: 10 }} />
                          : <SwapOutlined style={{ fontSize: 10 }} />}
                      </button>
                    </div>
                  );
                })}
              </div>
              <p className="mt-1.5 text-[11px] leading-relaxed text-gray-400">Клік — додати чи прибрати; цифра показує порядок, перетягування його змінює. <SwapOutlined className="mx-0.5" /> віддзеркалює оригінал у BMS і Cloudflare.</p>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_auto]">
              <div>
                <span className={LABEL}>Композиція</span>
                <div className="mt-2 grid grid-cols-3 gap-2">
                  {data.layouts.map(item => (
                    <button key={item.key} type="button" onClick={() => setCollage(current => ({ ...current, layout: item.key }))}
                            className={`rounded-lg border px-2 py-2 text-xs font-medium ${collage.layout === item.key ? 'border-violet-500 bg-violet-50 text-violet-700 dark:bg-violet-900/25 dark:text-violet-300' : 'border-gray-200 text-gray-500 dark:border-gray-700'}`}>{item.label}</button>
                  ))}
                </div>
              </div>
              <div>
                <span className={LABEL}>Тло</span>
                <div className="mt-2 flex gap-2">
                  {data.backgrounds.map(item => (
                    <button key={item.key} type="button" onClick={() => setCollage(current => ({ ...current, background: item.key }))}
                            title={item.label} aria-label={`Тло: ${item.label}`}
                            className={`h-9 w-9 rounded-lg border ${collage.background === item.key ? 'border-violet-500 ring-2 ring-violet-500/25' : 'border-gray-200 dark:border-gray-700'}`}
                            style={{ background: BACKGROUNDS[item.key] }} />
                  ))}
                </div>
              </div>
            </div>

            {collage.image_idx.length === 5 && collage.layout !== 'grid' && (
              <div className="rounded-xl border border-violet-200 bg-violet-50/45 p-3 dark:border-violet-800 dark:bg-violet-900/10">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-xs font-semibold text-gray-700 dark:text-gray-200">Сітка як у попередніх Viber-постах</div>
                    <div className="mt-0.5 text-[11px] text-gray-400">Два великі фото ліворуч · три менші праворуч</div>
                  </div>
                  <button type="button" onClick={resetViberGrid} className="shrink-0 text-[11px] text-violet-600 hover:underline"><ReloadOutlined className="mr-1" />Стандарт Viber</button>
                </div>
                <div className="mt-3 grid gap-x-4 gap-y-3 sm:grid-cols-2">
                  {([
                    ['Ширина лівої колонки', 'column_split', 0.50, 0.78, `${Math.round(collage.column_split * 100)}%`],
                    ['Поділ двох великих фото', 'left_split', 0.28, 0.72, `${Math.round(collage.left_split * 100)}%`],
                    ['Верхнє фото праворуч', 'right_top', 0.18, 0.55, `${Math.round(collage.right_top * 100)}%`],
                    ['Середнє фото праворуч', 'right_middle', 0.18, 0.55, `${Math.round(collage.right_middle * 100)}%`],
                  ] as const).map(([label, key, min, max, valueLabel]) => (
                    <label key={key} className="text-[11px] text-gray-500">
                      <span className="flex justify-between gap-2"><span>{label}</span><b className="font-semibold text-gray-600 dark:text-gray-300">{valueLabel}</b></span>
                      <input type="range" min={min} max={max} step={0.01} value={collage[key]}
                             onChange={event => updateViberGrid(key, Number(event.target.value))}
                             className="mt-1 block w-full accent-violet-600" />
                    </label>
                  ))}
                  <label className="text-[11px] text-gray-500 sm:col-span-2">
                    <span className="flex justify-between gap-2"><span>Проміжок між фото</span><b className="font-semibold text-gray-600 dark:text-gray-300">{collage.gap} px</b></span>
                    <input type="range" min={0} max={24} step={1} value={collage.gap}
                           onChange={event => setCollage(current => ({ ...current, gap: Number(event.target.value) }))}
                           className="mt-1 block w-full accent-violet-600" />
                  </label>
                </div>
              </div>
            )}

            {activeFrame && editTargets.length > 0 && (
              <div className="rounded-xl border border-gray-200 bg-gray-50/60 p-3 dark:border-gray-700 dark:bg-gray-800/40">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-semibold text-gray-700 dark:text-gray-200">Редагувати</span>
                  <div className="flex flex-wrap gap-1">
                    {collage.image_idx.map((index, order) => (
                      <button key={index} type="button" onClick={() => toggleEditTarget(index)}
                              className={`h-7 min-w-7 rounded-md border px-2 text-[11px] font-semibold transition ${editTargets.includes(index) ? 'border-violet-500 bg-violet-50 text-violet-700 dark:bg-violet-900/25 dark:text-violet-300' : 'border-gray-200 bg-white text-gray-400 dark:border-gray-700 dark:bg-gray-900'}`}>
                        {order + 1}
                      </button>
                    ))}
                    <button type="button" onClick={selectAllFrames}
                            className={`h-7 rounded-md border px-2 text-[11px] font-semibold transition ${editTargets.length === collage.image_idx.length ? 'border-violet-500 bg-violet-50 text-violet-700 dark:bg-violet-900/25 dark:text-violet-300' : 'border-gray-200 bg-white text-gray-500 dark:border-gray-700 dark:bg-gray-900'}`}>
                      Усі {collage.image_idx.length}
                    </button>
                  </div>
                  <button type="button" onClick={resetEditedFrames} className="ml-auto text-[11px] text-violet-600 hover:underline"><ReloadOutlined className="mr-1" />Скинути {editingMany ? 'вибрані' : ''}</button>
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-gray-400">
                  {editingMany
                    ? `Вибрано ${editTargets.length} фото. Спільні повзунки додають однакову корекцію, не стираючи індивідуальні налаштування.`
                    : `Налаштовується фото ${collage.image_idx.indexOf(activeFrame.image_idx) + 1}. Натисни номери або «Усі», щоб рухати кілька кадрів разом.`}
                </p>
                <div className="mt-3 grid gap-3 sm:grid-cols-3">
                  {([
                    [zoomControlLabel, 'zoom', editingMany ? FRAME_ZOOM_MIN - 1 : FRAME_ZOOM_MIN, editingMany ? FRAME_ZOOM_MAX - 1 : FRAME_ZOOM_MAX, 0.05],
                    [editingMany ? 'Разом ліворуч / праворуч' : 'Ліворуч / праворуч', 'x', -1, 1, 0.05],
                    [editingMany ? 'Разом вгору / вниз' : 'Вгору / вниз', 'y', -1, 1, 0.05],
                  ] as const).map(([label, key, min, max, step]) => (
                    <label key={key} className="text-[11px] text-gray-500">{label}
                      <input type="range" min={min} max={max} step={step} value={editingMany ? groupAdjust[key] : activeFrame[key]}
                             aria-label={key === 'zoom' ? (editingMany ? 'Масштаб вибраних фото' : 'Масштаб фото') : undefined}
                             onChange={event => editingMany
                               ? updateFramesTogether(key, Number(event.target.value))
                               : updateFrame({ [key]: Number(event.target.value) })}
                             className="mt-1 block w-full accent-violet-600" />
                    </label>
                  ))}
                </div>
              </div>
            )}

            <div>
              <div className="flex items-center justify-between">
                <span className={LABEL}>Підпис</span>
                <span className={`text-[11px] ${captionProblem ? 'font-semibold text-rose-500' : 'text-gray-400'}`}>{caption.length} / {data.caption_limit}</span>
              </div>
              <textarea rows={9} value={caption} onChange={event => setCaption(event.target.value)} className={`${INPUT} mt-2 resize-y font-sans leading-relaxed`} />
              {captionProblem && <div className="mt-1 text-[11px] text-rose-500">{captionProblem}</div>}
            </div>
          </section>

          <aside className="space-y-4 border-t border-gray-100 bg-gray-50/60 p-5 dark:border-gray-800 dark:bg-gray-950/20 lg:overflow-y-auto lg:border-l lg:border-t-0">
            <div>
              <div className="flex items-center justify-between">
                <span className={LABEL}>Як побачать підписники</span>
                <span className="text-[11px] text-gray-400">JPEG · {renderBytes ? `${Math.ceil(renderBytes / 1024)} КБ` : 'прев’ю'}</span>
              </div>
              <div className="relative mt-2 aspect-square overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800">
                {renderUrl ? <img src={renderUrl} alt="Viber-колаж" className="h-full w-full object-contain" /> : collage.image_idx[0] !== undefined ? <SmartImage src={imageUrls[collage.image_idx[0]]} thumb={640} thumbOnly className="h-full w-full object-contain" /> : null}
                {rendering && <div className="absolute inset-0 flex items-center justify-center bg-white/70 text-violet-600 backdrop-blur-[1px] dark:bg-gray-900/70"><LoadingOutlined className="mr-2" /> Оновлюю</div>}
              </div>
              {renderError && <div className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600 dark:bg-rose-900/20 dark:text-rose-300">{renderError}</div>}
              <div className="mt-2 rounded-xl border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-900">
                <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">{previewTitle || `Товар #${data.productnumber}`}</div>
                <div className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-gray-600 dark:text-gray-300">{caption || 'Підпис порожній'}</div>
              </div>
            </div>

            <div className="rounded-xl border border-violet-200 bg-violet-50/70 p-3 dark:border-violet-800 dark:bg-violet-900/15">
              <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 dark:text-gray-100"><span className="flex h-6 w-6 items-center justify-center rounded-md text-[11px] font-black text-white" style={{ background: VIBER_PURPLE }}>V</span>{data.channel.title}</div>
              <div className="mt-1 text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">Viber Channel API надсилає один колаж із підписом. Окремого режиму «без звуку» в цьому API немає.</div>
            </div>

            <div>
              <span className={LABEL}>Час публікації</span>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <button type="button" onClick={() => setTiming('now')} className={`rounded-lg border px-3 py-2 text-xs font-medium ${timing === 'now' ? 'border-violet-500 bg-violet-50 text-violet-700 dark:bg-violet-900/25 dark:text-violet-300' : 'border-gray-200 text-gray-500 dark:border-gray-700'}`}><SendOutlined className="mr-1" />Зараз</button>
                <button type="button" onClick={() => setTiming('custom')} className={`rounded-lg border px-3 py-2 text-xs font-medium ${timing === 'custom' ? 'border-violet-500 bg-violet-50 text-violet-700 dark:bg-violet-900/25 dark:text-violet-300' : 'border-gray-200 text-gray-500 dark:border-gray-700'}`}><ClockCircleOutlined className="mr-1" />Запланувати</button>
              </div>
              {timing === 'custom' && (
                <div className="mt-2">
                  <input type="datetime-local" value={customAt} onChange={event => setCustomAt(event.target.value)}
                         min={asLocal(new Date(Date.now() + 2 * 60_000).toISOString())}
                         max={asLocal(new Date(Date.now() + 365 * 24 * 60 * 60_000).toISOString())}
                         className={INPUT} />
                  {scheduleProblem && <div className="mt-1 text-[11px] text-rose-500">{scheduleProblem}</div>}
                </div>
              )}
            </div>

            {(data.already_published > 0 || data.pending_publications > 0) && (
              <label className="flex cursor-pointer gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
                <input type="checkbox" checked={force} onChange={event => setForce(event.target.checked)} className="mt-0.5 accent-amber-600" />
                <span><b>{data.already_published > 0 ? `Уже є ${data.already_published} опублікованих постів.` : `Уже є ${data.pending_publications} постів у черзі або розкладі.`}</b><br />Створити ще одну версію свідомо.</span>
              </label>
            )}

            {data.warnings.length > 0 && (
              <div className="space-y-1.5">
                {data.warnings.map((warning, index) => <div key={`${warning}-${index}`} className="flex gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-700 dark:bg-amber-900/20 dark:text-amber-300"><WarningOutlined className="mt-0.5" />{warning}</div>)}
              </div>
            )}
            {checkResult && (
              <div className="flex gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-xs leading-relaxed text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300">
                <CheckOutlined className="mt-0.5" />{checkResult}
              </div>
            )}
            {checkError && (
              <div className="flex gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-relaxed text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">
                <WarningOutlined className="mt-0.5" />{checkError}
              </div>
            )}
            {!draftMode && !data.connection.live_publish_available && (
              <div className="rounded-xl border border-gray-200 bg-white p-3 text-xs leading-relaxed text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400">
                Редактор і безпечна перевірка вже працюють. Жива кнопка стане доступною після захищеного підключення Viber у Cloudflare; секрет у BMS не зберігатиметься.
              </div>
            )}
          </aside>
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-gray-100 bg-white px-5 py-3.5 dark:border-gray-800 dark:bg-gray-900">
          <span className="text-xs text-gray-400">{draftMode ? 'Зміни збережуться в картці пакета' : timing === 'custom' ? 'Після підтвердження пост виконає хмарний розклад' : 'Після підтвердження це буде реальний пост у каналі'}</span>
          <div className="flex gap-2">
            {!draftMode && (
              <button type="button" onClick={runSafeCheck}
                      disabled={checking || dialogBusy || rendering || !collage.image_idx.length || !!captionProblem || !!scheduleProblem}
                      className="rounded-lg border border-violet-200 px-3 py-2 text-sm font-medium text-violet-700 disabled:opacity-45 dark:border-violet-800 dark:text-violet-300">
                {checking ? <><LoadingOutlined className="mr-1.5" />Перевіряю…</> : 'Перевірити без надсилання'}
              </button>
            )}
            <button type="button" onClick={onCancel} disabled={dialogBusy} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300">Скасувати</button>
            <button type="button" onClick={submit} disabled={cannotPublish}
                    className="flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-45" style={{ background: VIBER_PURPLE }}>
              {dialogBusy ? <LoadingOutlined /> : draftMode ? <CheckOutlined /> : <SendOutlined />}
              {mirroringImage !== null ? 'Зберігаю фото…' : busy ? 'Виконую…' : draftMode ? 'Зберегти картку' : timing === 'custom' ? 'Запланувати в канал' : 'Опублікувати в канал'}
            </button>
          </div>
        </footer>
      </div>
      {conditionConfirmOpen && (
        <ViberConditionPublishConfirmation
          items={[{ productnumber: data.productnumber, conditionName: data.condition_name || data.condition || 'Вживаний', title: previewTitle }]}
          busy={busy}
          onCancel={() => setConditionConfirmOpen(false)}
          onConfirm={() => {
            setConditionConfirmOpen(false);
            setConditionApproved(true);
            setLiveConfirmOpen(true);
          }}
        />
      )}
      {liveConfirmOpen && (
        <ViberLivePublishConfirmation
          count={1}
          channelTitle={data.channel.title}
          publishAt={payload(conditionApproved).publish_at}
          busy={busy}
          onCancel={() => setLiveConfirmOpen(false)}
          onConfirm={() => {
            setLiveConfirmOpen(false);
            onConfirm(payload(conditionApproved));
          }}
        />
      )}
    </div>
  );
};

export default ViberPublishDialog;
