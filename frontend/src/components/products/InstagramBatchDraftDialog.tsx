import React, { useEffect, useMemo, useState } from 'react';
import {
  CheckCircleOutlined, ClockCircleOutlined, CloseOutlined, LoadingOutlined,
  SafetyCertificateOutlined, SendOutlined, WarningOutlined,
} from '@ant-design/icons';
import SmartImage from '../common/SmartImage';
import {
  InstagramMark, instagramDraftFromPreview,
  type InstagramDraftPayload, type InstagramPreview,
} from './InstagramPublishDialog';

interface Props {
  productIds: number[];
  busy?: boolean;
  onCancel: () => void;
  onPublish?: (request: InstagramBatchRequest) => void;
}

export interface InstagramBatchRequest {
  batch_id: string;
  items: { product_id: number; payload: InstagramDraftPayload }[];
}

interface DraftCard {
  preview: InstagramPreview;
  draft: InstagramDraftPayload;
  enabled: boolean;
  error?: string | null;
}

const toLocalDateTimeValue = (iso: string) => {
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

const InstagramBatchDraftDialog: React.FC<Props> = ({ productIds, busy = false, onCancel, onPublish }) => {
  const [loading, setLoading] = useState(true);
  const [validating, setValidating] = useState(false);
  const [cards, setCards] = useState<DraftCard[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [summary, setSummary] = useState<any | null>(null);
  const [commonPublishAt, setCommonPublishAt] = useState<string | null>(null);
  const [batchIntervalMinutes, setBatchIntervalMinutes] = useState(2);
  const requestKey = useMemo(() => productIds.join(','), [productIds]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setSummary(null);
    fetch('/api/publications/instagram/preview-posts-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_ids: productIds }),
    })
      .then(async response => {
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || 'Не вдалося зібрати пакет');
        if (cancelled) return;
        const next = (result.items || []).map((item: any): DraftCard => ({
          preview: item.preview,
          draft: instagramDraftFromPreview(item.preview),
          enabled: Boolean(item.ok && item.preview?.image_count),
          error: item.error || null,
        }));
        setCards(next);
      })
      .catch(reason => { if (!cancelled) setLoadError(reason.message || 'Не вдалося зібрати пакет'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // requestKey фіксує стабільний знімок вибраних товарів на час відкритого вікна.
  }, [requestKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const updateCard = (productId: number, change: (card: DraftCard) => DraftCard) => {
    setSummary(null);
    setCards(current => current.map(card => card.preview.product_id === productId ? change(card) : card));
  };

  const togglePhoto = (productId: number, imageIndex: number) => {
    updateCard(productId, card => {
      const current = card.draft.image_idx;
      if (current.includes(imageIndex)) {
        return { ...card, draft: { ...card.draft, image_idx: current.filter(value => value !== imageIndex), frames: card.draft.frames.filter(frame => frame.image_idx !== imageIndex) } };
      }
      const limit = card.preview.publish_types[card.draft.publish_type].max_media;
      if (current.length >= limit) return card;
      const defaultZoom = card.draft.publish_type === 'feed' ? 0.9 : 0.6;
      return { ...card, draft: { ...card.draft, image_idx: [...current, imageIndex], frames: [...card.draft.frames, { image_idx: imageIndex, zoom: defaultZoom, x: 0, y: 0 }] } };
    });
  };

  const enabled = cards.filter(card => card.enabled);
  const invalidEnabled = enabled.some(card =>
    (card.draft.publish_type !== 'story' && !card.draft.caption.trim())
    || card.draft.caption.length > card.preview.caption_limit
    || card.draft.story_text.length > (card.preview.story_text_limit || 320)
    || !card.draft.image_idx.length
    || (card.preview.condition_confirmation_required && card.draft.condition_confirmed !== true)
  );
  const liveReady = cards.length > 0 && cards.every(card => card.preview.connection.live_publish_available && card.preview.connection.oauth_connected !== false);

  const applyBatchSchedule = (baseIso: string | null, intervalMinutes = batchIntervalMinutes) => {
    setCards(current => current.map((card, index) => ({
      ...card,
      draft: {
        ...card.draft,
        publish_at: baseIso
          ? new Date(new Date(baseIso).getTime() + index * intervalMinutes * 60000).toISOString()
          : null,
      },
    })));
  };

  const validateBatch = async () => {
    if (!enabled.length || invalidEnabled || validating) return;
    setValidating(true);
    setSummary(null);
    try {
      const response = await fetch('/api/publications/instagram/dry-run-batch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: enabled.map(card => card.draft) }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || result.error || 'Пакетна перевірка не пройшла');
      setSummary(result);
      const errors = new Map<number, string>(
        (result.results || [])
          .filter((item: any) => !item.ok)
          .map((item: any): [number, string] => [Number(item.product_id), String(item.error || 'Перевірка не пройшла')]),
      );
      setCards(current => current.map(card => ({ ...card, error: errors.get(card.preview.product_id) || null })));
    } catch (reason: any) {
      setSummary({ ok: false, error: reason.message || 'Не вдалося перевірити пакет' });
    } finally {
      setValidating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center p-3 sm:p-5">
      <div className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={loading || validating ? undefined : onCancel} />
      <div className="relative flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900">
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4 dark:border-gray-800">
          <div className="flex items-center gap-3">
            <InstagramMark className="h-10 w-10 text-xl" />
            <div>
              <h2 className="text-base font-semibold text-gray-900 dark:text-gray-50">Пакет Instagram-чернеток</h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">До 10 унікальних товарів · окрема чернетка кожної картки</p>
            </div>
          </div>
          <button type="button" onClick={onCancel} disabled={loading || validating} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-800"><CloseOutlined /></button>
        </div>

        <div className="overflow-y-auto p-4 sm:p-5">
          <div className={`mb-4 flex gap-2 rounded-xl border px-3 py-2.5 text-xs ${liveReady ? 'border-green-200 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-900/20 dark:text-green-200' : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200'}`}>
            <SafetyCertificateOutlined className="mt-0.5 shrink-0" />
            <span>{liveReady ? 'Захищена черга готова. Кожна картка має власний тип, текст, формат і набір фото.' : 'Пакет можна повністю перевірити, але живе надсилання заблоковане до OAuth/Cloudflare.'}</span>
          </div>

          {loading ? (
            <div className="flex h-64 items-center justify-center gap-2 text-sm text-gray-500"><LoadingOutlined /> Готую стабільний знімок товарів…</div>
          ) : loadError ? (
            <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">{loadError}</div>
          ) : (
            <div className="space-y-3">
              {cards.map(card => {
                const preview = card.preview;
                const tooLong = card.draft.publish_type === 'story'
                  ? card.draft.story_text.length > (preview.story_text_limit || 320)
                  : card.draft.caption.length > preview.caption_limit;
                return (
                  <article key={preview.product_id} className={`rounded-xl border p-3 transition ${card.enabled ? 'border-pink-200 bg-pink-50/20 dark:border-pink-900/70 dark:bg-pink-950/10' : 'border-gray-200 opacity-70 dark:border-gray-700'}`}>
                    <div className="flex items-start gap-3">
                      <input type="checkbox" checked={card.enabled} onChange={event => updateCard(preview.product_id, current => ({ ...current, enabled: event.target.checked }))}
                        disabled={!preview.image_count} className="mt-2 rounded border-gray-300 text-pink-600 focus:ring-pink-500" />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">#{preview.productnumber} · {[preview.brand, preview.model].filter(Boolean).join(' ')}</div>
                            <div className="text-[11px] text-gray-400">{card.draft.image_idx.length} фото · {preview.image_kind === 'official' ? 'офіційні' : preview.image_kind === 'real' ? 'живі' : 'фото відсутні'}</div>
                          </div>
                          <div className="flex gap-2">
                          <select value={card.draft.publish_type} disabled={!card.enabled} onChange={event => updateCard(preview.product_id, current => {
                            const publish_type = event.target.value as InstagramDraftPayload['publish_type'];
                            const limit = preview.publish_types[publish_type].max_media;
                            const image_idx = publish_type !== 'story' && current.draft.publish_type === 'story'
                              ? preview.default_image_idx.slice(0, limit)
                              : current.draft.image_idx.slice(0, limit);
                            const defaultZoom = publish_type === 'feed' ? 0.9 : 0.6;
                            const framesByImage = new Map(current.draft.frames.map(frame => [frame.image_idx, frame]));
                            return { ...current, draft: { ...current.draft, publish_type, image_idx, frames: image_idx.map(image_idx => ({ ...(framesByImage.get(image_idx) || { image_idx, x: 0, y: 0 }), zoom: defaultZoom })) } };
                          })} className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200">
                            {Object.entries(preview.publish_types).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
                          </select>
                          {card.draft.publish_type === 'feed' && <select value={card.draft.feed_preset} disabled={!card.enabled} onChange={event => updateCard(preview.product_id, current => ({ ...current, draft: { ...current.draft, feed_preset: event.target.value } }))}
                            className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200">
                            {Object.entries(preview.feed_presets).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
                          </select>}
                          </div>
                        </div>

                        <div className="mt-3 grid gap-3 md:grid-cols-[220px_1fr]">
                          <div className="grid grid-cols-5 gap-1.5 self-start">
                            {preview.image_urls.slice(0, 10).map((url, index) => {
                              const order = card.draft.image_idx.indexOf(index);
                              return (
                                <button key={`${url}-${index}`} type="button" disabled={!card.enabled} onClick={() => togglePhoto(preview.product_id, index)}
                                  className={`relative aspect-square overflow-hidden rounded-md border-2 disabled:opacity-50 ${order >= 0 ? 'border-pink-500' : 'border-transparent'}`}>
                                  <SmartImage src={url} alt={`Фото ${index + 1}`} thumb={96} thumbOnly className="h-full w-full" />
                                  {order >= 0 && <span className="absolute left-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-pink-600 px-0.5 text-[9px] font-bold text-white">{order + 1}</span>}
                                </button>
                              );
                            })}
                          </div>
                          <div>
                            <textarea value={card.draft.publish_type === 'story' ? card.draft.story_text : card.draft.caption} disabled={!card.enabled} rows={5} onChange={event => updateCard(preview.product_id, current => ({ ...current, draft: current.draft.publish_type === 'story' ? { ...current.draft, story_text: event.target.value } : { ...current.draft, caption: event.target.value } }))}
                              className={`w-full resize-y rounded-lg border bg-white px-2.5 py-2 text-xs leading-relaxed text-gray-800 outline-none disabled:opacity-50 dark:bg-gray-800 dark:text-gray-100 ${tooLong ? 'border-red-400' : 'border-gray-200 focus:border-pink-400 dark:border-gray-700'}`} />
                            <div className={`mt-1 text-right text-[10px] ${tooLong ? 'font-semibold text-red-500' : 'text-gray-400'}`}>{card.draft.publish_type === 'story' ? `${card.draft.story_text.length}/${preview.story_text_limit || 320} · текст буде на кадрі` : `${card.draft.caption.length}/${preview.caption_limit}`}</div>
                            {preview.condition_confirmation_required && <label className="mt-2 flex gap-2 text-[11px] text-amber-700 dark:text-amber-300"><input type="checkbox" checked={card.draft.condition_confirmed === true} disabled={!card.enabled} onChange={event => updateCard(preview.product_id, current => ({ ...current, draft: { ...current.draft, condition_confirmed: event.target.checked } }))} /> Підтверджую стан «{preview.condition_name}»</label>}
                          </div>
                        </div>
                        {card.error && <div className="mt-2 flex gap-1.5 text-xs text-red-600 dark:text-red-300"><WarningOutlined />{card.error}</div>}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}

          {summary && (
            <div className={`mt-4 flex gap-2 rounded-xl border px-3 py-3 text-sm ${summary.ok ? 'border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-900/20 dark:text-green-300' : 'border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300'}`}>
              {summary.ok ? <CheckCircleOutlined className="mt-0.5" /> : <WarningOutlined className="mt-0.5" />}
              <span>{summary.ok ? `${summary.counts.success} чернеток пройшли перевірку. Зовнішніх викликів: 0.` : summary.error || `${summary.counts?.error || 0} чернеток мають помилки.`}</span>
            </div>
          )}
          {liveReady && <div className="mt-4 grid gap-3 rounded-xl border border-gray-200 p-3 sm:grid-cols-2 dark:border-gray-700">
            <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Публікація пакета
              <select value={commonPublishAt ? 'scheduled' : 'now'} onInput={event => {
                const publishAt = (event.currentTarget as HTMLSelectElement).value === 'scheduled' ? cards[0]?.preview.default_publish_at || new Date(Date.now() + 3600000).toISOString() : null;
                setCommonPublishAt(publishAt);
                applyBatchSchedule(publishAt);
              }} onChange={event => {
                const publishAt = event.target.value === 'scheduled' ? cards[0]?.preview.default_publish_at || new Date(Date.now() + 3600000).toISOString() : null;
                setCommonPublishAt(publishAt);
                applyBatchSchedule(publishAt);
              }} className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm font-normal dark:border-gray-700 dark:bg-gray-800">
                <option value="now">Послідовно зараз</option><option value="scheduled">За спільним розкладом</option>
              </select>
            </label>
            {commonPublishAt && <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Початок
              <input type="datetime-local" value={toLocalDateTimeValue(commonPublishAt)} onInput={event => {
                const value = (event.currentTarget as HTMLInputElement).value;
                const publishAt = value ? new Date(value).toISOString() : null;
                setCommonPublishAt(publishAt);
                applyBatchSchedule(publishAt);
              }} onChange={event => {
                const publishAt = event.target.value ? new Date(event.target.value).toISOString() : null;
                setCommonPublishAt(publishAt);
                applyBatchSchedule(publishAt);
              }} className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm font-normal dark:border-gray-700 dark:bg-gray-800" />
            </label>}
            {commonPublishAt && <label className="text-xs font-semibold text-gray-700 dark:text-gray-200">Інтервал між картками
              <select value={batchIntervalMinutes} onChange={event => {
                const interval = Number(event.target.value);
                setBatchIntervalMinutes(interval);
                applyBatchSchedule(commonPublishAt, interval);
              }} className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm font-normal dark:border-gray-700 dark:bg-gray-800">
                <option value={1}>1 хвилина</option><option value={2}>2 хвилини</option><option value={5}>5 хвилин</option><option value={10}>10 хвилин</option>
              </select>
            </label>}
          </div>}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 bg-gray-50/70 px-5 py-3.5 dark:border-gray-800 dark:bg-gray-950/30">
          <span className="text-xs text-gray-400">До перевірки: {enabled.length} із {cards.length}</span>
          <div className="flex gap-2">
            <button type="button" onClick={onCancel} disabled={loading || validating || busy} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300">Закрити</button>
            <button type="button" onClick={validateBatch} disabled={loading || validating || busy || !enabled.length || invalidEnabled}
              className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-[#833AB4] to-[#E1306C] px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40">
              {validating ? <LoadingOutlined /> : <SafetyCertificateOutlined />}
              Перевірити пакет без надсилання
            </button>
            {liveReady && onPublish && <button type="button" onClick={() => onPublish({ batch_id: typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `instagram-batch-${Date.now()}`, items: enabled.map(card => ({ product_id: card.preview.product_id, payload: card.draft })) })}
              disabled={loading || validating || busy || !enabled.length || invalidEnabled}
              className="inline-flex items-center gap-2 rounded-lg bg-pink-600 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40">
              {busy ? <LoadingOutlined /> : commonPublishAt ? <ClockCircleOutlined /> : <SendOutlined />}
              {commonPublishAt ? 'Запланувати пакет' : 'Опублікувати пакет'}
            </button>}
          </div>
        </div>
      </div>
    </div>
  );
};

export default InstagramBatchDraftDialog;
