import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ClockCircleOutlined, CloseOutlined, DeleteOutlined, LeftOutlined, LoadingOutlined,
  RightOutlined, SendOutlined, WarningOutlined,
} from '@ant-design/icons';
import SmartImage from '../common/SmartImage';

export type CollectionPlatform = 'viber' | 'facebook';

export interface CollectionItem {
  product_id: number;
  productnumber: string;
  brand: string | null;
  model: string | null;
  type: string | null;
  price: string | null;
  sizes: string[];
  image_kind: 'official' | 'real' | 'none';
  image_count: number;
  image_urls: string[];
  image_names: string[];
}

export interface CollectionFrame {
  product_id: number;
  image_idx: number;
  zoom: number;
  x: number;
  y: number;
}

export interface CollectionSpec {
  version: number;
  platform: CollectionPlatform;
  layout: string;
  background: 'white' | 'soft' | 'warm' | 'dark';
  gap: number;
  width: number;
  height: number;
  cols: number;
  rows: number;
  items: CollectionFrame[];
}

export interface CollectionPreview {
  ok: boolean;
  platform: CollectionPlatform;
  platform_label: string;
  items: CollectionItem[];
  missing_ids: number[];
  spec: CollectionSpec;
  layouts: { key: string; label: string; cols: number; capacity: number }[];
  backgrounds: { key: string; label: string }[];
  caption: string;
  caption_limit: number;
  max_items: number;
  min_items: number;
  canvas: { width: number; height: number };
  default_publish_at: string;
  warnings: string[];
  connection: {
    configured?: boolean;
    live_publish_available?: boolean;
    oauth_connected?: boolean;
    account?: string;
    channel_title?: string;
    pages?: { id: string; name: string }[];
    missing?: string[];
  };
}

export interface CollectionPublishRequest {
  platform: CollectionPlatform;
  layout: string;
  background: string;
  gap: number;
  items: CollectionFrame[];
  caption: string;
  publish_at: string | null;
  page_ids?: string[];
  idempotency_key: string;
}

interface Props {
  platform: CollectionPlatform;
  productIds: number[];
  busy?: boolean;
  onCancel: () => void;
  onPublish: (request: CollectionPublishRequest, itemCount: number) => void;
}

const ACCENT: Record<CollectionPlatform, string> = { viber: '#7360F2', facebook: '#1877F2' };
const FRAME_ZOOM_MIN = 0.5;
const FRAME_ZOOM_MAX = 3;

function uuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `collection-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Українська множина: 1 товар, 2 товари, 5 товарів (з винятком 11–14). */
function plural(count: number, forms: [string, string, string]): string {
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return forms[2];
  if (ones === 1) return forms[0];
  if (ones >= 2 && ones <= 4) return forms[1];
  return forms[2];
}

function asLocal(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Підбірка — банер каналу, а не публікація товару: статуси товарів вона не чіпає.
 *  Тому діалог свідомо не показує «вже опубліковано» і не питає підтвердження стану. */
const CollectionCollageDialog: React.FC<Props> = ({ platform, productIds, busy = false, onCancel, onPublish }) => {
  const [data, setData] = useState<CollectionPreview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [items, setItems] = useState<CollectionItem[]>([]);
  const [frames, setFrames] = useState<CollectionFrame[]>([]);
  const [layout, setLayout] = useState('grid9');
  const [background, setBackground] = useState<CollectionSpec['background']>('white');
  const [gap, setGap] = useState(8);
  const [caption, setCaption] = useState('');
  const [publishAt, setPublishAt] = useState<string | null>(null);
  const [pageIds, setPageIds] = useState<string[]>([]);
  const [focused, setFocused] = useState<number | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // Розмір полотна залежить від кількості товарів і сітки, тож беремо його з
  // самої відмальованої картинки, а не з початкового прев'ю: інакше підпис
  // «1080×1080» пережив би зміну сітки й почав би брехати.
  const [canvas, setCanvas] = useState<{ width: number; height: number } | null>(null);
  const [grid, setGrid] = useState<string | null>(null);
  const idempotencyKey = useRef(uuid());

  const accent = ACCENT[platform];

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch('/api/publications/collections/preview', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_ids: productIds, platform }),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || 'Не вдалося зібрати підбірку');
        if (cancelled) return;
        setData(result);
        setItems(result.items);
        setFrames(result.spec.items);
        setLayout(result.spec.layout);
        setBackground(result.spec.background);
        setGap(result.spec.gap);
        setCaption(result.caption || '');
        setPageIds((result.connection?.pages || []).map((page: any) => page.id));
      } catch (error: any) {
        if (!cancelled) setLoadError(error.message || 'Не вдалося зібрати підбірку');
      }
    })();
    return () => { cancelled = true; };
  }, [productIds, platform]);

  const capacity = useMemo(() => {
    const preset = (data?.layouts || []).find(row => row.key === layout);
    return preset?.capacity ?? 9;
  }, [data, layout]);

  const overCapacity = frames.length > capacity;
  const captionLimit = data?.caption_limit ?? 768;
  const captionTooLong = caption.length > captionLimit;
  const captionRequired = platform === 'viber';
  const pages = data?.connection?.pages || [];
  const liveReady = platform === 'viber'
    ? Boolean(data?.connection?.live_publish_available)
    : Boolean(data?.connection?.live_publish_available && data?.connection?.oauth_connected !== false);
  const itemById = useMemo(
    () => new Map(items.map(item => [item.product_id, item])),
    [items],
  );

  // Прев'ю малює той самий backend renderer, що піде в публікацію: різниця між
  // «як я це бачив» і «що пішло в канал» тут коштувала б реального поста.
  const renderPreview = useCallback(async (signal: AbortSignal) => {
    if (frames.length < (data?.min_items ?? 2) || overCapacity) return;
    setPreviewBusy(true);
    try {
      const response = await fetch('/api/publications/collections/render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ platform, layout, background, gap, items: frames }),
        signal,
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || 'Не вдалося намалювати підбірку');
      }
      setGrid(response.headers.get('X-BMS-Grid'));
      const objectUrl = URL.createObjectURL(await response.blob());
      setPreviewUrl(previous => { if (previous) URL.revokeObjectURL(previous); return objectUrl; });
      setPreviewError(null);
    } catch (error: any) {
      if (error.name !== 'AbortError') setPreviewError(error.message || 'Не вдалося намалювати підбірку');
    } finally {
      if (!signal.aborted) setPreviewBusy(false);
    }
  }, [platform, layout, background, gap, frames, data, overCapacity]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => { void renderPreview(controller.signal); }, 320);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [renderPreview]);

  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  const moveFrame = (productId: number, direction: -1 | 1) => {
    setFrames(current => {
      const position = current.findIndex(frame => frame.product_id === productId);
      const target = position + direction;
      if (position < 0 || target < 0 || target >= current.length) return current;
      const next = current.slice();
      [next[position], next[target]] = [next[target], next[position]];
      return next;
    });
  };

  const removeFrame = (productId: number) => {
    setFrames(current => current.filter(frame => frame.product_id !== productId));
    setFocused(current => (current === productId ? null : current));
  };

  const updateFrame = (productId: number, patch: Partial<CollectionFrame>) => {
    setFrames(current => current.map(frame => (
      frame.product_id === productId ? { ...frame, ...patch } : frame
    )));
  };

  const focusedFrame = frames.find(frame => frame.product_id === focused) || null;
  const focusedItem = focusedFrame ? itemById.get(focusedFrame.product_id) : null;

  const canPublish = liveReady && !busy && frames.length >= (data?.min_items ?? 2)
    && !overCapacity && !captionTooLong && (!captionRequired || caption.trim().length > 0)
    && (platform !== 'facebook' || pages.length === 0 || pageIds.length > 0);

  const submit = () => {
    if (!canPublish) return;
    onPublish({
      platform, layout, background, gap, items: frames,
      caption: caption.trim(),
      publish_at: publishAt,
      ...(platform === 'facebook' ? { page_ids: pageIds } : {}),
      idempotency_key: idempotencyKey.current,
    }, frames.length);
  };

  const shownCanvas = canvas || data?.canvas || { width: 1080, height: 1080 };
  const aspect = `${shownCanvas.width}/${shownCanvas.height}`;

  return (
    <div className="fixed inset-0 z-[130] flex items-center justify-center p-3 sm:p-5">
      <div className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={busy ? undefined : onCancel} />
      <div className="relative flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900">
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4 dark:border-gray-800">
          <div className="flex min-w-0 items-center gap-3">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-md text-lg font-black text-white"
              style={{ backgroundColor: accent }}>{platform === 'viber' ? 'V' : 'f'}</span>
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-gray-900 dark:text-gray-50">
                Підбірка · {frames.length} {plural(frames.length, ['товар', 'товари', 'товарів'])}
              </h2>
              <p className="truncate text-xs text-gray-500 dark:text-gray-400">
                {data?.platform_label || (platform === 'viber' ? 'Viber-канал' : 'Сторінка Facebook')}
                {data?.connection?.channel_title ? ` · ${data.connection.channel_title}` : ''}
                {data?.connection?.account ? ` · ${data.connection.account}` : ''}
              </p>
            </div>
          </div>
          <button type="button" onClick={onCancel} disabled={busy}
            className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-50 dark:hover:bg-gray-800 dark:hover:text-gray-200">
            <CloseOutlined />
          </button>
        </div>

        {loadError && (
          <div className="m-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">
            {loadError}
          </div>
        )}

        {!data && !loadError && (
          <div className="flex items-center justify-center gap-2 p-10 text-sm text-gray-500">
            <LoadingOutlined /> Збираю підбірку…
          </div>
        )}

        {data && (
          <>
            <div className="overflow-y-auto p-4 sm:p-5">
              <div className="mb-4 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-xs leading-relaxed text-gray-600 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-300">
                Підбірка — рекламний банер каналу. Вона <b>не змінює статус опублікованості</b> жодного
                товару із сітки: кожен із них і далі можна окремо опублікувати як звичайний пост.
              </div>

              <div className="grid gap-5 lg:grid-cols-[minmax(300px,1fr)_minmax(360px,1fr)]">
                <section>
                  <div className="mb-2 flex items-center justify-between">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Точний вигляд сітки</label>
                    <span className="text-[11px] text-gray-400">{shownCanvas.width}×{shownCanvas.height}</span>
                  </div>
                  <div className="mx-auto max-w-[460px] overflow-hidden rounded-xl border border-gray-200 bg-gray-100 shadow-sm dark:border-gray-700 dark:bg-gray-800"
                    style={{ aspectRatio: aspect }}>
                    {previewUrl ? (
                      <img src={previewUrl} alt="Підбірка"
                        onLoad={event => setCanvas({
                          width: (event.target as HTMLImageElement).naturalWidth,
                          height: (event.target as HTMLImageElement).naturalHeight,
                        })}
                        className={`h-full w-full object-contain ${previewBusy ? 'opacity-70' : ''}`} />
                    ) : (
                      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-gray-400">
                        {previewError || (previewBusy ? 'Малюю сітку…' : 'Сітка з’явиться тут')}
                      </div>
                    )}
                  </div>
                  <div className="mt-2 text-center text-[11px] text-gray-400">
                    {previewBusy ? 'Оновлюю сітку…' : [
                      grid ? `сітка ${grid.replace('x', '×')}` : '',
                      `${frames.length} ${plural(frames.length, ['позиція', 'позиції', 'позицій'])}`,
                    ].filter(Boolean).join(' · ')}
                  </div>

                  <label className="mt-4 block text-xs font-semibold text-gray-700 dark:text-gray-200">Розмір сітки</label>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    {data.layouts.map(preset => (
                      <button key={preset.key} type="button" onClick={() => setLayout(preset.key)}
                        className={`rounded-lg border px-2 py-2 text-xs transition ${layout === preset.key ? 'font-semibold' : 'border-gray-200 text-gray-600 dark:border-gray-700 dark:text-gray-300'}`}
                        style={layout === preset.key ? { borderColor: accent, color: accent } : undefined}>
                        {preset.label}
                      </button>
                    ))}
                  </div>

                  <label className="mt-4 block text-xs font-semibold text-gray-700 dark:text-gray-200">Тло</label>
                  <div className="mt-2 grid grid-cols-4 gap-2">
                    {data.backgrounds.map(row => (
                      <button key={row.key} type="button" onClick={() => setBackground(row.key as CollectionSpec['background'])}
                        className={`rounded-lg border px-2 py-2 text-xs ${background === row.key ? 'font-semibold' : 'border-gray-200 text-gray-500 dark:border-gray-700'}`}
                        style={background === row.key ? { borderColor: accent, color: accent } : undefined}>
                        {row.label}
                      </button>
                    ))}
                  </div>

                  <label className="mt-4 grid grid-cols-[110px_1fr_42px] items-center gap-2 text-[11px] text-gray-500">
                    <span>Проміжок</span>
                    <input type="range" min={0} max={40} step={1} value={gap}
                      onInput={event => setGap(Number((event.target as HTMLInputElement).value))} />
                    <span className="text-right tabular-nums">{gap}px</span>
                  </label>

                  {focusedFrame && focusedItem && (
                    <div className="mt-4 space-y-2 rounded-xl border border-gray-200 p-3 dark:border-gray-700">
                      <div className="text-xs font-semibold text-gray-700 dark:text-gray-200">
                        Кадр #{focusedItem.productnumber}
                      </div>
                      {focusedItem.image_urls.length > 1 && (
                        <div className="flex flex-wrap gap-1.5">
                          {focusedItem.image_urls.map((url, index) => (
                            <button key={url} type="button"
                              onClick={() => updateFrame(focusedFrame.product_id, { image_idx: index })}
                              className={`h-11 w-11 overflow-hidden rounded border-2 ${focusedFrame.image_idx === index ? '' : 'border-transparent'}`}
                              style={focusedFrame.image_idx === index ? { borderColor: accent } : undefined}
                              title={`Фото ${index + 1}`}>
                              <SmartImage src={url} alt={`Фото ${index + 1}`} thumb={96} thumbOnly className="h-full w-full" />
                            </button>
                          ))}
                        </div>
                      )}
                      {([['zoom', 'Масштаб', FRAME_ZOOM_MIN, FRAME_ZOOM_MAX, 0.01],
                         ['x', 'Ліворуч / праворуч', -1, 1, 0.01],
                         ['y', 'Вгору / вниз', -1, 1, 0.01]] as const).map(([key, label, min, max, step]) => (
                        <label key={key} className="grid grid-cols-[110px_1fr_42px] items-center gap-2 text-[11px] text-gray-500">
                          <span>{label}</span>
                          <input type="range" min={min} max={max} step={step} value={focusedFrame[key]}
                            onInput={event => updateFrame(focusedFrame.product_id, { [key]: Number((event.target as HTMLInputElement).value) } as Partial<CollectionFrame>)} />
                          <span className="text-right tabular-nums">{focusedFrame[key].toFixed(2)}</span>
                        </label>
                      ))}
                      <p className="text-[11px] leading-relaxed text-gray-400">
                        Кадрування змінює лише цю картинку-банер. Оригінали фото товару лишаються без змін.
                      </p>
                    </div>
                  )}
                </section>

                <section className="space-y-4">
                  <div>
                    <div className="mb-2 flex items-center justify-between">
                      <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Товари в сітці та порядок</label>
                      <span className={`text-xs ${overCapacity ? 'font-semibold text-red-500' : 'text-gray-500'}`}>
                        {frames.length}/{capacity}
                      </span>
                    </div>
                    {overCapacity && (
                      <div className="mb-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">
                        У сітку {capacity === 9 ? '3×3' : '4×4'} влізе {capacity} товарів. Приберіть зайві або
                        виберіть більшу сітку.
                      </div>
                    )}
                    <div className="grid grid-cols-4 gap-2 sm:grid-cols-5">
                      {frames.map((frame, position) => {
                        const item = itemById.get(frame.product_id);
                        if (!item) return null;
                        const url = item.image_urls[frame.image_idx] || item.image_urls[0];
                        const beyond = position >= capacity;
                        return (
                          <div key={frame.product_id}
                            className={`relative overflow-hidden rounded-lg border-2 ${beyond ? 'border-red-400 opacity-60' : ''}`}
                            style={!beyond ? { borderColor: focused === frame.product_id ? accent : 'transparent' } : undefined}>
                            <button type="button" onClick={() => setFocused(frame.product_id)}
                              className="block aspect-square w-full bg-gray-100 dark:bg-gray-800" title={`#${item.productnumber}`}>
                              <SmartImage src={url} alt={item.productnumber} thumb={96} thumbOnly className="h-full w-full" />
                            </button>
                            <span className="absolute left-1 top-1 flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[10px] font-bold text-white"
                              style={{ backgroundColor: accent }}>{position + 1}</span>
                            <button type="button" onClick={() => removeFrame(frame.product_id)}
                              className="absolute right-1 top-1 rounded bg-black/60 px-1 text-[10px] text-white hover:bg-red-600"
                              title="Прибрати з підбірки"><DeleteOutlined /></button>
                            <div className="absolute bottom-1 right-1 flex overflow-hidden rounded bg-black/65 text-white">
                              <button type="button" onClick={() => moveFrame(frame.product_id, -1)} disabled={position === 0}
                                className="px-1.5 py-1 disabled:opacity-30" title="Раніше"><LeftOutlined style={{ fontSize: 9 }} /></button>
                              <button type="button" onClick={() => moveFrame(frame.product_id, 1)} disabled={position === frames.length - 1}
                                className="px-1.5 py-1 disabled:opacity-30" title="Пізніше"><RightOutlined style={{ fontSize: 9 }} /></button>
                            </div>
                            <span className="absolute bottom-1 left-1 max-w-[70%] truncate rounded bg-black/55 px-1 text-[9px] text-white">
                              #{item.productnumber}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  <div>
                    <div className="mb-2 flex items-center justify-between">
                      <label htmlFor="collection-caption" className="text-xs font-semibold text-gray-700 dark:text-gray-200">
                        Підпис {captionRequired ? '' : '(необов’язковий)'}
                      </label>
                      <span className={`text-xs ${captionTooLong ? 'font-semibold text-red-500' : 'text-gray-400'}`}>
                        {caption.length}/{captionLimit}
                      </span>
                    </div>
                    <textarea id="collection-caption" value={caption} rows={10}
                      onChange={event => setCaption(event.target.value)}
                      className={`w-full resize-y rounded-xl border bg-white px-3 py-2.5 text-sm leading-relaxed text-gray-800 outline-none focus:ring-2 dark:bg-gray-800 dark:text-gray-100 ${captionTooLong ? 'border-red-400 focus:ring-red-500/20' : 'border-gray-200 dark:border-gray-700'}`} />
                    <div className="mt-1 flex items-center justify-between gap-2">
                      <p className="text-[11px] text-gray-400">
                        {platform === 'viber'
                          ? 'Viber-розмітка: *жирний*, _курсив_, ```моноширинний```.'
                          : 'Текст іде у стрічку Сторінки без змін.'}
                      </p>
                      <button type="button" onClick={() => setCaption('')}
                        className="shrink-0 rounded border border-gray-200 px-2 py-1 text-[11px] text-gray-500 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800">
                        Очистити
                      </button>
                    </div>
                  </div>

                  {platform === 'facebook' && pages.length > 1 && (
                    <div>
                      <div className="mb-2 flex items-center justify-between">
                        <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Сторінки</label>
                        <span className={`text-xs ${pageIds.length ? 'text-gray-400' : 'font-semibold text-red-500'}`}>
                          {pageIds.length}/{pages.length}
                        </span>
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {pages.map(page => {
                          const checked = pageIds.includes(page.id);
                          return (
                            <label key={page.id}
                              className={`flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 text-xs transition ${checked ? 'font-semibold' : 'border-gray-200 text-gray-600 dark:border-gray-700 dark:text-gray-300'}`}
                              style={checked ? { borderColor: accent, color: accent } : undefined}>
                              <input type="checkbox" checked={checked} onChange={event => setPageIds(current => (
                                event.target.checked ? [...current, page.id] : current.filter(value => value !== page.id)
                              ))} />
                              <span className="min-w-0 truncate">{page.name}</span>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Коли публікувати
                      <select value={publishAt ? 'scheduled' : 'now'} disabled={!liveReady}
                        onChange={event => setPublishAt(event.target.value === 'scheduled' ? data.default_publish_at : null)}
                        className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm font-normal dark:border-gray-700 dark:bg-gray-800">
                        <option value="now">Зараз</option>
                        <option value="scheduled">За розкладом</option>
                      </select>
                    </label>
                    {publishAt && (
                      <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Дата й час
                        <input type="datetime-local" value={asLocal(publishAt)}
                          onChange={event => setPublishAt(event.target.value ? new Date(event.target.value).toISOString() : null)}
                          className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm font-normal dark:border-gray-700 dark:bg-gray-800" />
                      </label>
                    )}
                  </div>

                  {(data.warnings || []).map((warning, index) => (
                    <div key={index} className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
                      <WarningOutlined className="mt-0.5" />{warning}
                    </div>
                  ))}
                  {!liveReady && (
                    <div className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
                      <WarningOutlined className="mt-0.5" />
                      Надсилання заблоковане до підключення {platform === 'viber' ? 'Viber-диспетчера' : 'Сторінки Facebook'}
                      {data.connection?.missing?.length ? `: ${data.connection.missing.join(', ')}` : ''}.
                    </div>
                  )}
                </section>
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 bg-gray-50/70 px-5 py-3.5 dark:border-gray-800 dark:bg-gray-950/30">
              <span className="text-xs text-gray-400">
                Один банер · {frames.length} {plural(frames.length, ['позиція', 'позиції', 'позицій'])} · статуси товарів лишаються незмінними
              </span>
              <div className="flex gap-2">
                <button type="button" onClick={onCancel} disabled={busy}
                  className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300">
                  Закрити
                </button>
                <button type="button" onClick={submit} disabled={!canPublish}
                  className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white shadow-sm disabled:cursor-not-allowed disabled:opacity-40"
                  style={{ backgroundColor: accent }}>
                  {busy ? <LoadingOutlined /> : publishAt ? <ClockCircleOutlined /> : <SendOutlined />}
                  {publishAt ? 'Запланувати підбірку' : 'Опублікувати підбірку'}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default CollectionCollageDialog;
