import React, { useEffect, useMemo, useState } from 'react';
import {
  CheckCircleOutlined, ClockCircleOutlined, CloseOutlined, LeftOutlined, LoadingOutlined,
  RightOutlined, SafetyCertificateOutlined, SendOutlined, WarningOutlined,
} from '@ant-design/icons';
import SmartImage from '../common/SmartImage';

export interface InstagramFeedPreset {
  label: string;
  width: number;
  height: number;
}

export interface InstagramPreview {
  ok: boolean;
  mode: 'draft_ready' | 'production';
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
  story_text: string;
  story_text_limit: number;
  image_count: number;
  image_kind: 'official' | 'real' | 'none';
  image_urls: string[];
  image_names: string[];
  default_image_idx: number[];
  carousel_limit: number;
  batch_max_products: number;
  default_feed_preset: string;
  feed_presets: Record<string, InstagramFeedPreset>;
  feed_zoom_defaults?: Record<string, number[]>;
  feed_edge_adjusted?: Record<string, boolean[]>;
  story_preset: InstagramFeedPreset;
  publish_types: Record<'feed' | 'story' | 'reel', { label: string; max_media: number }>;
  default_publish_at: string;
  connection: {
    configured: boolean;
    mode: 'draft_ready' | 'production';
    account: string;
    live_publish_available: boolean;
    schedule_available: boolean;
    oauth_connected?: boolean;
    missing: string[];
    note: string;
  };
  warnings: string[];
}

export interface InstagramDraftPayload {
  product_id: number;
  caption: string;
  story_text: string;
  image_idx: number[];
  feed_preset: string;
  publish_type: 'feed' | 'story' | 'reel';
  background: 'white' | 'soft' | 'dark';
  frames: { image_idx: number; zoom: number; x: number; y: number }[];
  publish_at: string | null;
  collaborators: string[];
  alt_text: string;
  share_to_feed: boolean;
  is_ai_generated: boolean;
  idempotency_key: string;
  condition_confirmed?: boolean;
}

interface Props {
  data: InstagramPreview;
  busy?: boolean;
  onCancel: () => void;
  onConfirm?: (payload: InstagramDraftPayload) => void;
}

export const InstagramMark: React.FC<{ className?: string }> = ({ className = '' }) => (
  <span
    aria-hidden="true"
    className={`inline-flex items-center justify-center rounded-md bg-gradient-to-br from-[#833AB4] via-[#E1306C] to-[#FCAF45] font-black text-white ${className}`}
  >
    ◎
  </span>
);

export function instagramDefaultZoom(
  preview: InstagramPreview,
  publishType: InstagramDraftPayload['publish_type'],
  imageIndex: number,
  feedPreset = preview.default_feed_preset,
): number {
  if (publishType !== 'feed') return 0.6;
  return preview.feed_zoom_defaults?.[feedPreset]?.[imageIndex] ?? 0.9;
}

export function instagramDraftFromPreview(preview: InstagramPreview): InstagramDraftPayload {
  return {
    product_id: preview.product_id,
    caption: preview.caption,
    image_idx: preview.default_image_idx.slice(0, preview.carousel_limit),
    feed_preset: preview.default_feed_preset,
    publish_type: 'feed',
    background: 'white',
    frames: preview.default_image_idx.slice(0, preview.carousel_limit).map(image_idx => ({
      image_idx,
      zoom: instagramDefaultZoom(preview, 'feed', image_idx),
      x: 0,
      y: 0,
    })),
    story_text: preview.story_text || '',
    publish_at: null,
    collaborators: [],
    alt_text: '',
    share_to_feed: true,
    is_ai_generated: false,
    idempotency_key: typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `instagram-${Date.now()}-${Math.random()}`,
  };
}

function asLocal(iso: string | null | undefined): string {
  if (!iso) return '';
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return '';
  const pad = (number: number) => String(number).padStart(2, '0');
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`;
}

const InstagramPublishDialog: React.FC<Props> = ({ data, busy = false, onCancel, onConfirm }) => {
  const [draft, setDraft] = useState<InstagramDraftPayload>(() => instagramDraftFromPreview(data));
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<any | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [focusedImage, setFocusedImage] = useState<number | null>(null);

  useEffect(() => {
    setDraft(instagramDraftFromPreview(data));
    setValidation(null);
  }, [data]);

  const preset = draft.publish_type === 'feed'
    ? (data.feed_presets[draft.feed_preset] || data.feed_presets[data.default_feed_preset])
    : data.story_preset;
  const captionTooLong = draft.caption.length > data.caption_limit;
  const storyTextLimit = data.story_text_limit || 320;
  const storyTextTooLong = draft.story_text.length > storyTextLimit;
  const captionRequired = draft.publish_type !== 'story';
  const liveReady = data.connection.live_publish_available && data.connection.oauth_connected !== false;
  const maxMedia = data.publish_types?.[draft.publish_type]?.max_media || (draft.publish_type === 'story' ? 1 : data.carousel_limit);
  const selectedOrder = useMemo(
    () => new Map(draft.image_idx.map((index, order) => [index, order + 1])),
    [draft.image_idx],
  );

  const toggleImage = (index: number) => {
    setValidation(null);
    setDraft(current => {
      if (current.image_idx.includes(index)) {
        return { ...current, image_idx: current.image_idx.filter(value => value !== index), frames: current.frames.filter(frame => frame.image_idx !== index) };
      }
      if (current.image_idx.length >= maxMedia) return current;
      const defaultZoom = instagramDefaultZoom(data, current.publish_type, index, current.feed_preset);
      return { ...current, image_idx: [...current.image_idx, index], frames: [...current.frames, { image_idx: index, zoom: defaultZoom, x: 0, y: 0 }] };
    });
  };

  const setPublishType = (publish_type: InstagramDraftPayload['publish_type']) => {
    setValidation(null);
    setDraft(current => {
      const limit = data.publish_types?.[publish_type]?.max_media || (publish_type === 'story' ? 1 : data.carousel_limit);
      const image_idx = publish_type !== 'story' && current.publish_type === 'story'
        ? data.default_image_idx.slice(0, limit)
        : current.image_idx.slice(0, limit);
      const framesByImage = new Map(current.frames.map(frame => [frame.image_idx, frame]));
      return {
        ...current,
        publish_type,
        image_idx,
        frames: image_idx.map(image_idx => ({
          ...(framesByImage.get(image_idx) || { image_idx, x: 0, y: 0 }),
          zoom: instagramDefaultZoom(data, publish_type, image_idx, current.feed_preset),
        })),
      };
    });
  };

  const setFeedPreset = (feed_preset: string) => {
    setValidation(null);
    setDraft(current => ({
      ...current,
      feed_preset,
      frames: current.frames.map(frame => {
        const previousDefault = instagramDefaultZoom(data, 'feed', frame.image_idx, current.feed_preset);
        if (Math.abs(frame.zoom - previousDefault) > 0.0001) return frame;
        return { ...frame, zoom: instagramDefaultZoom(data, 'feed', frame.image_idx, feed_preset) };
      }),
    }));
  };

  const setScheduleMode = (value: string) => {
    setValidation(null);
    setDraft(current => ({
      ...current,
      publish_at: value === 'scheduled' ? data.default_publish_at : null,
    }));
  };

  const updateFocusedFrame = (key: 'zoom' | 'x' | 'y', value: number) => {
    const target = focusedImage ?? draft.image_idx[0];
    if (target == null) return;
    setValidation(null);
    setDraft(current => ({
      ...current,
      frames: current.frames.map(frame => frame.image_idx === target ? { ...frame, [key]: value } : frame),
    }));
  };

  const moveImage = (index: number, direction: -1 | 1) => {
    setValidation(null);
    setDraft(current => {
      const position = current.image_idx.indexOf(index);
      const target = position + direction;
      if (position < 0 || target < 0 || target >= current.image_idx.length) return current;
      const next = current.image_idx.slice();
      [next[position], next[target]] = [next[target], next[position]];
      return { ...current, image_idx: next };
    });
  };

  const validate = async () => {
    if (!draft.image_idx.length || captionTooLong || storyTextTooLong || validating) return;
    setValidating(true);
    setValidation(null);
    try {
      const response = await fetch('/api/publications/instagram/dry-run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Перевірка не пройшла');
      setValidation(result);
    } catch (reason: any) {
      setValidation({ ok: false, error: reason.message || 'Не вдалося перевірити чернетку' });
    } finally {
      setValidating(false);
    }
  };

  useEffect(() => {
    if (!draft.image_idx.length) { setPreviewUrl(null); return; }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setPreviewBusy(true);
      try {
        const response = await fetch('/api/publications/instagram/render-preview', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(draft), signal: controller.signal,
        });
        if (!response.ok) throw new Error('preview');
        const objectUrl = URL.createObjectURL(await response.blob());
        setPreviewUrl(previous => { if (previous) URL.revokeObjectURL(previous); return objectUrl; });
      } catch (reason: any) {
        if (reason.name !== 'AbortError') setPreviewUrl(null);
      } finally {
        if (!controller.signal.aborted) setPreviewBusy(false);
      }
    }, 350);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [draft.product_id, draft.image_idx, draft.feed_preset, draft.publish_type, draft.background, draft.frames, draft.story_text]);

  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  const firstImage = previewUrl || (draft.image_idx.length ? data.image_urls[draft.image_idx[0]] : '');
  const focusedFrame = draft.frames.find(frame => frame.image_idx === (focusedImage ?? draft.image_idx[0]));
  const title = [data.brand, data.model].filter(Boolean).join(' ') || `Товар #${data.productnumber}`;

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center p-3 sm:p-5">
      <div className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={validating ? undefined : onCancel} />
      <div className="relative flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900">
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4 dark:border-gray-800">
          <div className="flex min-w-0 items-center gap-3">
            <InstagramMark className="h-10 w-10 text-xl" />
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-gray-900 dark:text-gray-50">Instagram-чернетка · #{data.productnumber}</h2>
              <p className="truncate text-xs text-gray-500 dark:text-gray-400">{title} · {data.connection.account}</p>
            </div>
          </div>
          <button type="button" onClick={onCancel} disabled={validating} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-50 dark:hover:bg-gray-800 dark:hover:text-gray-200"><CloseOutlined /></button>
        </div>

        <div className="overflow-y-auto p-4 sm:p-5">
          <div className={`mb-4 flex gap-2 rounded-xl border px-3 py-2.5 text-xs leading-relaxed ${liveReady ? 'border-green-200 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-900/20 dark:text-green-200' : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200'}`}>
            <SafetyCertificateOutlined className="mt-0.5 shrink-0" />
            <span>{liveReady
              ? <><b>Захищене підключення готове.</b> Публікація піде через Cloudflare Worker; токен Meta не потрапляє у BMS.</>
              : <><b>Безпечний режим:</b> редактор і повна перевірка готові, але надсилання заблоковане до завершення OAuth/Cloudflare.</>}
            </span>
          </div>

          <div className="mb-4 grid grid-cols-3 gap-2">
            {Object.entries(data.publish_types || { feed: { label: 'Пост / карусель', max_media: 10 }, story: { label: 'Story', max_media: 1 }, reel: { label: 'Reel зі слайдів', max_media: 10 } }).map(([key, value]) => (
              <button key={key} type="button" onClick={() => setPublishType(key as InstagramDraftPayload['publish_type'])}
                className={`rounded-xl border px-3 py-2.5 text-sm transition ${draft.publish_type === key ? 'border-pink-400 bg-pink-50 font-semibold text-pink-700 dark:bg-pink-900/20 dark:text-pink-300' : 'border-gray-200 text-gray-600 hover:border-pink-300 dark:border-gray-700 dark:text-gray-300'}`}>
                {value.label}
              </button>
            ))}
          </div>

          <div className="grid gap-5 lg:grid-cols-[minmax(300px,0.9fr)_minmax(420px,1.1fr)]">
            <section>
              <div className="mb-2 flex items-center justify-between">
                <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Точний вигляд після renderer</label>
                <span className="text-[11px] text-gray-400">{preset?.width}×{preset?.height}</span>
              </div>
              <div className="mx-auto max-w-[420px] overflow-hidden rounded-xl border border-gray-200 bg-gray-100 shadow-sm dark:border-gray-700 dark:bg-gray-800" style={{ aspectRatio: `${preset?.width || 1080}/${preset?.height || 1350}` }}>
                {firstImage ? (
                  <SmartImage src={firstImage} alt={title} thumb={previewUrl ? undefined : 640} className={`h-full w-full ${previewBusy ? 'opacity-70' : ''}`} spinner loading="eager" />
                ) : (
                  <div className="flex h-full items-center justify-center px-6 text-center text-sm text-gray-400">Виберіть хоча б одне фото</div>
                )}
              </div>
              <div className="mt-2 text-center text-[11px] text-gray-400">
                {previewBusy ? 'Оновлюю точний кадр…' : draft.publish_type === 'reel' ? `Reel зі слайдів · ${draft.image_idx.length} фото` : draft.publish_type === 'story' ? 'Story 9:16' : draft.image_idx.length > 1 ? `Карусель · ${draft.image_idx.length} фото · перше буде обкладинкою` : 'Одинарний пост'}
              </div>

              {draft.publish_type === 'feed' && <><label className="mt-4 block text-xs font-semibold text-gray-700 dark:text-gray-200">Формат кадру</label>
              <div className="mt-2 grid grid-cols-3 gap-2">
                {Object.entries(data.feed_presets).map(([key, value]) => (
                  <button key={key} type="button" onClick={() => setFeedPreset(key)}
                    className={`rounded-lg border px-2 py-2 text-xs transition ${draft.feed_preset === key ? 'border-pink-400 bg-pink-50 font-semibold text-pink-700 dark:bg-pink-900/20 dark:text-pink-300' : 'border-gray-200 text-gray-600 hover:border-pink-300 dark:border-gray-700 dark:text-gray-300'}`}>
                    {value.label}
                  </button>
                ))}
              </div></>}

              <label className="mt-4 block text-xs font-semibold text-gray-700 dark:text-gray-200">Тло</label>
              <div className="mt-2 grid grid-cols-3 gap-2">
                {([['white', 'Біле'], ['soft', 'Світле'], ['dark', 'Темне']] as const).map(([key, label]) => (
                  <button key={key} type="button" onClick={() => setDraft(current => ({ ...current, background: key }))}
                    className={`rounded-lg border px-2 py-2 text-xs ${draft.background === key ? 'border-pink-400 font-semibold text-pink-700 dark:text-pink-300' : 'border-gray-200 text-gray-500 dark:border-gray-700'}`}>{label}</button>
                ))}
              </div>

              {focusedFrame && <div className="mt-4 space-y-2 rounded-xl border border-gray-200 p-3 dark:border-gray-700">
                <div className="text-xs font-semibold text-gray-700 dark:text-gray-200">Кадрування фото №{draft.image_idx.indexOf(focusedFrame.image_idx) + 1}</div>
                {([['zoom', 'Масштаб', 0.5, 3, 0.01], ['x', 'Ліворуч / праворуч', -1, 1, 0.01], ['y', 'Вгору / вниз', -1, 1, 0.01]] as const).map(([key, label, min, max, step]) => (
                  <label key={key} className="grid grid-cols-[120px_1fr_42px] items-center gap-2 text-[11px] text-gray-500">
                    <span>{label}</span>
                    <input type="range" min={min} max={max} step={step} value={focusedFrame[key]} onInput={event => updateFocusedFrame(key, Number((event.target as HTMLInputElement).value))} />
                    <span className="text-right tabular-nums">{focusedFrame[key].toFixed(2)}</span>
                  </label>
                ))}
                {draft.publish_type === 'feed' && data.feed_edge_adjusted?.[draft.feed_preset]?.[focusedFrame.image_idx] && (
                  <p className="rounded-lg bg-blue-50 px-2.5 py-2 text-[11px] leading-relaxed text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">
                    Фото вже доходить до краю. Автоматичне зменшення вимкнено — масштаб {instagramDefaultZoom(data, 'feed', focusedFrame.image_idx, draft.feed_preset).toFixed(2)}. Його можна змінити вручну.
                  </p>
                )}
              </div>}
              <p className="mt-2 text-[11px] leading-relaxed text-gray-400">Crop/zoom змінюють лише окрему публікаційну похідну. Оригінал товару лишається без змін.</p>
            </section>

            <section className="space-y-4">
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Фото та порядок</label>
                  <span className={`text-xs ${draft.image_idx.length ? 'text-gray-500' : 'text-red-500'}`}>{draft.image_idx.length}/{maxMedia}</span>
                </div>
                {data.image_urls.length ? (
                  <div className="grid grid-cols-4 gap-2 sm:grid-cols-5">
                    {data.image_urls.map((url, index) => {
                      const order = selectedOrder.get(index);
                      return (
                        <div key={`${url}-${index}`} className={`relative overflow-hidden rounded-lg border-2 ${order ? 'border-pink-500' : 'border-transparent'}`}>
                          <button type="button" onClick={() => toggleImage(index)} className="block aspect-square w-full bg-gray-100 dark:bg-gray-800">
                            <SmartImage src={url} alt={data.image_names[index] || `Фото ${index + 1}`} thumb={96} thumbOnly className="h-full w-full" />
                          </button>
                          {order && <span className="absolute left-1 top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-pink-600 px-1 text-[10px] font-bold text-white">{order}</span>}
                          {order && <button type="button" onClick={event => { event.stopPropagation(); setFocusedImage(index); }} title="Налаштувати кадр" className={`absolute right-1 top-1 rounded px-1 text-[10px] text-white ${focusedImage === index || (focusedImage == null && order === 1) ? 'bg-pink-600' : 'bg-black/60'}`}>⚙</button>}
                          {order && (
                            <div className="absolute bottom-1 right-1 flex overflow-hidden rounded bg-black/65 text-white">
                              <button type="button" onClick={() => moveImage(index, -1)} disabled={order === 1} className="px-1.5 py-1 disabled:opacity-30" title="Раніше"><LeftOutlined style={{ fontSize: 9 }} /></button>
                              <button type="button" onClick={() => moveImage(index, 1)} disabled={order === draft.image_idx.length} className="px-1.5 py-1 disabled:opacity-30" title="Пізніше"><RightOutlined style={{ fontSize: 9 }} /></button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">У товару немає фото — таку публікацію перевірити неможливо.</div>
                )}
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between">
                  <label htmlFor="instagram-caption" className="text-xs font-semibold text-gray-700 dark:text-gray-200">{draft.publish_type === 'story' ? 'Текст на зображенні Story' : 'Текст поста'}</label>
                  <span className={`text-xs ${(draft.publish_type === 'story' ? storyTextTooLong : captionTooLong) ? 'font-semibold text-red-500' : 'text-gray-400'}`}>
                    {draft.publish_type === 'story' ? `${draft.story_text.length}/${storyTextLimit}` : `${draft.caption.length}/${data.caption_limit}`}
                  </span>
                </div>
                <textarea id="instagram-caption" value={draft.publish_type === 'story' ? draft.story_text : draft.caption}
                  onChange={event => { const value = event.target.value; setDraft(current => current.publish_type === 'story' ? ({ ...current, story_text: value }) : ({ ...current, caption: value })); setValidation(null); }}
                  rows={draft.publish_type === 'story' ? 6 : 10}
                  className={`w-full resize-y rounded-xl border bg-white px-3 py-2.5 text-sm leading-relaxed text-gray-800 outline-none focus:ring-2 dark:bg-gray-800 dark:text-gray-100 ${(draft.publish_type === 'story' ? storyTextTooLong : captionTooLong) ? 'border-red-400 focus:ring-red-500/20' : 'border-gray-200 focus:border-pink-400 focus:ring-pink-500/20 dark:border-gray-700'}`} />
                <p className="mt-1 text-[11px] text-gray-400">{draft.publish_type === 'story'
                  ? 'BMS нанесе цей текст прямо на нижню безпечну зону кадру; точний вигляд одразу видно в прев’ю.'
                  : 'Instagram приймає звичайний текст; службові маркери форматування тут не потрібні.'}</p>
              </div>

              {draft.publish_type === 'reel' && <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2.5 text-xs leading-relaxed text-sky-800 dark:border-sky-800 dark:bg-sky-900/20 dark:text-sky-200">
                BMS автоматично створить відео 9:16 і показуватиме кожне вибране фото приблизно 2,5 секунди. Reel публікується без звуку: Instagram API не вміє автоматично вибирати музику з бібліотеки Instagram.
              </div>}

              {draft.publish_type !== 'story' && <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Співавтори — до 3
                  <input value={draft.collaborators.join(', ')} onChange={event => setDraft(current => ({ ...current, collaborators: event.target.value.split(',').map(value => value.trim().replace(/^@/, '')).filter(Boolean).slice(0, 3) }))}
                    placeholder="username1, username2" className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm font-normal outline-none focus:border-pink-400 dark:border-gray-700 dark:bg-gray-800" />
                </label>
                {draft.publish_type === 'feed' && <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Alt text зображень
                  <input value={draft.alt_text} maxLength={1000} onChange={event => setDraft(current => ({ ...current, alt_text: event.target.value }))}
                    placeholder="Короткий опис фото" className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm font-normal outline-none focus:border-pink-400 dark:border-gray-700 dark:bg-gray-800" />
                </label>}
              </div>}

              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Коли публікувати
                  <select value={draft.publish_at ? 'scheduled' : 'now'} disabled={!liveReady}
                    onInput={event => setScheduleMode((event.currentTarget as HTMLSelectElement).value)}
                    onChange={event => setScheduleMode(event.target.value)}
                    className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm font-normal dark:border-gray-700 dark:bg-gray-800">
                    <option value="now">Зараз</option><option value="scheduled">За розкладом</option>
                  </select>
                </label>
                {draft.publish_at && <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Дата й час
                  <input type="datetime-local" value={asLocal(draft.publish_at)}
                    onInput={event => { const value = (event.currentTarget as HTMLInputElement).value; setDraft(current => ({ ...current, publish_at: value ? new Date(value).toISOString() : null })); }}
                    onChange={event => setDraft(current => ({ ...current, publish_at: event.target.value ? new Date(event.target.value).toISOString() : null }))}
                    className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm font-normal dark:border-gray-700 dark:bg-gray-800" />
                </label>}
              </div>

              <div className="flex flex-wrap gap-4 text-xs text-gray-600 dark:text-gray-300">
                {draft.publish_type === 'reel' && <label className="flex items-center gap-2"><input type="checkbox" checked={draft.share_to_feed} onChange={event => setDraft(current => ({ ...current, share_to_feed: event.target.checked }))} /> Також показати у стрічці</label>}
                <label className="flex items-center gap-2"><input type="checkbox" checked={draft.is_ai_generated} onChange={event => setDraft(current => ({ ...current, is_ai_generated: event.target.checked }))} /> Позначити AI-контент</label>
              </div>

              {data.warnings.filter(value => !value.includes('preview/dry-run')).map((warning, index) => (
                <div key={index} className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200"><WarningOutlined className="mt-0.5" />{warning}</div>
              ))}
              {data.condition_confirmation_required && <label className="flex gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200">
                <input type="checkbox" checked={draft.condition_confirmed === true} onChange={event => setDraft(current => ({ ...current, condition_confirmed: event.target.checked }))} />
                <span>Підтверджую стан «{data.condition_name}» перед живою Instagram-публікацією.</span>
              </label>}
              {validation && (
                <div className={`flex gap-2 rounded-lg border px-3 py-2.5 text-xs ${validation.ok ? 'border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-900/20 dark:text-green-300' : 'border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300'}`}>
                  {validation.ok ? <CheckCircleOutlined className="mt-0.5" /> : <WarningOutlined className="mt-0.5" />}
                  <span>{validation.ok ? `Чернетка коректна: ${validation.would_publish_as}, ${validation.media_count} медіа, формат ${validation.output.width}×${validation.output.height}. Зовнішніх викликів: 0.` : validation.error}</span>
                </div>
              )}
            </section>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 bg-gray-50/70 px-5 py-3.5 dark:border-gray-800 dark:bg-gray-950/30">
          <span className="text-xs text-gray-400">{liveReady ? 'Перед живою дією BMS ще раз перевірить renderer і свіжий стан товару.' : 'Живе надсилання заблоковане до OAuth-підключення.'}</span>
          <div className="flex gap-2">
            <button type="button" onClick={onCancel} disabled={validating || busy} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300">Закрити</button>
            <button type="button" onClick={validate} disabled={validating || busy || !draft.image_idx.length || captionTooLong || storyTextTooLong || (captionRequired && !draft.caption.trim())}
              className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-[#833AB4] to-[#E1306C] px-4 py-2 text-sm font-semibold text-white shadow-sm disabled:cursor-not-allowed disabled:opacity-40">
              {validating ? <LoadingOutlined /> : <SafetyCertificateOutlined />}
              Перевірити без надсилання
            </button>
            {liveReady && onConfirm && <button type="button" onClick={() => onConfirm(draft)}
              disabled={validating || busy || !draft.image_idx.length || captionTooLong || storyTextTooLong || (captionRequired && !draft.caption.trim()) || (data.condition_confirmation_required && draft.condition_confirmed !== true)}
              className="inline-flex items-center gap-2 rounded-lg bg-pink-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-pink-700 disabled:cursor-not-allowed disabled:opacity-40">
              {busy ? <LoadingOutlined /> : draft.publish_at ? <ClockCircleOutlined /> : <SendOutlined />}
              {draft.publish_at ? 'Запланувати' : 'Опублікувати'}
            </button>}
          </div>
        </div>
      </div>
    </div>
  );
};

export default InstagramPublishDialog;
