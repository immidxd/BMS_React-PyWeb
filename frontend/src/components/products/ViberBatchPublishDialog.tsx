import React, { useEffect, useRef, useState } from 'react';
import {
  CheckOutlined, CloseOutlined, EditOutlined, SendOutlined,
  SettingOutlined, WarningOutlined,
} from '@ant-design/icons';
import SmartImage from '../common/SmartImage';
import ViberPublishDialog, {
  ViberConditionPublishConfirmation,
  ViberLivePublishConfirmation,
  type ViberCollageSpec, type ViberPreview, type ViberPublishPayload,
} from './ViberPublishDialog';

interface BatchPreviewItem {
  product_id: number;
  productnumber: string;
  source_product_ids: number[];
  ok: boolean;
  preview: ViberPreview | null;
  error?: string | null;
}

interface BatchPreviewResponse {
  selected_count: number;
  unique_count: number;
  merged_count: number;
  batch_max_products: number;
  items: BatchPreviewItem[];
}

interface Entry {
  productId: number;
  sourceIds: number[];
  preview: ViberPreview;
  draft: ViberPublishPayload;
  included: boolean;
  commonSelected: boolean;
  edited: boolean;
}

export interface ViberBatchRequest {
  batch_id: string;
  items: { product_id: number; payload: ViberPublishPayload }[];
}

interface Props {
  productIds: number[];
  busy: boolean;
  onCancel: () => void;
  onPublish: (request: ViberBatchRequest) => void;
}

type LayoutPreset = 'keep' | ViberCollageSpec['layout'];
type BackgroundPreset = 'keep' | ViberCollageSpec['background'];
type TimePreset = 'keep' | 'now' | 'stagger';

const INPUT = 'rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-800 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100';

function uuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `viber-batch-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function asLocal(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function defaultDraft(preview: ViberPreview, index: number): ViberPublishPayload {
  const at = new Date(preview.default_publish_at);
  if (!Number.isNaN(at.getTime())) at.setMinutes(at.getMinutes() + index * 2);
  return {
    caption: preview.caption,
    collage: preview.collage,
    publish_at: Number.isNaN(at.getTime()) ? preview.default_publish_at : at.toISOString(),
    idempotency_key: uuid(),
    force: preview.already_published > 0,
  };
}

const ViberBatchPublishDialog: React.FC<Props> = ({ productIds, busy, onCancel, onPublish }) => {
  // Вибір є незмінним знімком моменту відкриття. Повернення з редактора картки
  // не перезавантажує пакет і не відновлює вручну зняті прапорці.
  const initialProductIds = useRef(Array.from(new Set(productIds.map(Number).filter(id => Number.isFinite(id) && id > 0))));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<BatchPreviewResponse | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [conditionConfirmOpen, setConditionConfirmOpen] = useState(false);
  const [liveConfirmOpen, setLiveConfirmOpen] = useState(false);
  const [conditionApproved, setConditionApproved] = useState(false);
  const [layoutPreset, setLayoutPreset] = useState<LayoutPreset>('keep');
  const [backgroundPreset, setBackgroundPreset] = useState<BackgroundPreset>('keep');
  const [timePreset, setTimePreset] = useState<TimePreset>('stagger');
  const [baseTime, setBaseTime] = useState('');
  const [intervalMinutes, setIntervalMinutes] = useState(2);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch('/api/publications/viber/preview-posts-batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_ids: initialProductIds.current }),
    })
      .then(async response => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Не вдалося підготувати Viber-пакет');
        return data as BatchPreviewResponse;
      })
      .then(data => {
        if (!alive) return;
        setMeta(data);
        const ready = data.items
          .filter((item): item is BatchPreviewItem & { preview: ViberPreview } => !!item.ok && !!item.preview)
          .map((item, index) => ({
            productId: item.product_id,
            sourceIds: item.source_product_ids,
            preview: item.preview,
            draft: defaultDraft(item.preview, index),
            included: item.preview.already_published === 0 && item.preview.pending_publications === 0 && item.preview.image_count > 0,
            commonSelected: true,
            edited: false,
          }));
        setEntries(ready);
        if (ready[0]) setBaseTime(asLocal(ready[0].preview.default_publish_at));
        const failed = data.items.filter(item => !item.ok);
        if (failed.length) setError(`${failed.length} карток не вдалося підготувати; вони не ввійдуть у пакет.`);
      })
      .catch(reason => { if (alive) setError(reason?.message || 'Не вдалося підготувати Viber-пакет'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const editing = entries.find(entry => entry.productId === editingId) ?? null;
  const included = entries.filter(entry => entry.included);
  const risky = included.filter(entry => entry.preview.condition_confirmation_required);
  const commonCount = entries.filter(entry => entry.commonSelected).length;
  const unconfigured = included.some(entry => !entry.preview.connection.live_publish_available);
  const baseDate = baseTime ? new Date(baseTime) : null;
  const timeProblem = timePreset === 'stagger' && (
    !baseDate || Number.isNaN(baseDate.getTime())
      ? 'Вкажи коректний час першого поста'
      : baseDate.getTime() < Date.now() + 2 * 60_000
        ? 'Перший пост має бути щонайменше через 2 хвилини'
        : baseDate.getTime() > Date.now() + 365 * 24 * 60 * 60_000
          ? 'Розклад можливий не далі ніж на 365 днів'
          : null
  );

  const updateEntry = (id: number, patch: Partial<Entry>) =>
    setEntries(current => current.map(entry => entry.productId === id ? { ...entry, ...patch } : entry));

  const applyCommon = () => {
    let order = 0;
    setEntries(current => current.map(entry => {
      if (!entry.commonSelected) return entry;
      const collage = { ...entry.draft.collage };
      if (layoutPreset !== 'keep') collage.layout = layoutPreset;
      if (backgroundPreset !== 'keep') collage.background = backgroundPreset;
      let publishAt = entry.draft.publish_at;
      if (timePreset === 'now') publishAt = null;
      if (timePreset === 'stagger' && baseDate && !Number.isNaN(baseDate.getTime())) {
        const at = new Date(baseDate);
        at.setMinutes(at.getMinutes() + order * Math.max(1, intervalMinutes));
        publishAt = at.toISOString();
      }
      order += 1;
      return { ...entry, draft: { ...entry.draft, collage, publish_at: publishAt }, edited: true };
    }));
  };

  const publish = (conditionConfirmed = false) => onPublish({
    batch_id: uuid(),
    items: included.map(entry => ({
      product_id: entry.productId,
      payload: {
        ...entry.draft,
        condition_confirmed: conditionConfirmed && entry.preview.condition_confirmation_required ? true : undefined,
      },
    })),
  });

  const submit = () => {
    if (!included.length || unconfigured || timeProblem) return;
    if (risky.length) setConditionConfirmOpen(true);
    else {
      setConditionApproved(false);
      setLiveConfirmOpen(true);
    }
  };

  if (editing) {
    return (
      <ViberPublishDialog
        data={editing.preview}
        busy={busy}
        mode="draft"
        initialPayload={editing.draft}
        onPreviewChange={preview => updateEntry(editing.productId, { preview })}
        onCancel={() => setEditingId(null)}
        onConfirm={draft => {
          updateEntry(editing.productId, { draft, edited: true });
          setEditingId(null);
        }}
      />
    );
  }

  return (
    <div className="bms-dialog-host fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={busy ? undefined : onCancel} />
      <div className="relative flex max-h-[92vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900">
        <header className="flex items-center gap-3 border-b border-gray-100 px-5 py-4 dark:border-gray-800">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#7360F2] text-lg font-black text-white">V</span>
          <div className="min-w-0 flex-1">
            <div className="font-semibold text-gray-900 dark:text-gray-50">Пакетна публікація у Viber</div>
            <div className="mt-0.5 text-xs text-gray-400">{meta ? `${meta.selected_count} рядків → ${entries.length} унікальних Viber-карток${meta.merged_count ? ` · ${meta.merged_count} рядків ростовок об’єднано` : ''}` : 'Готую картки…'}</div>
          </div>
          <button type="button" onClick={busy ? undefined : onCancel} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800" aria-label="Закрити"><CloseOutlined /></button>
        </header>

        <main className="flex-1 overflow-y-auto bg-gray-50/50 p-5 dark:bg-gray-950/20">
          {loading && <div className="py-20 text-center text-sm text-gray-400">Збираю фото, колажі й підписи для пакета…</div>}
          {error && <div className="mb-4 flex gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-300"><WarningOutlined />{error}</div>}

          {!loading && entries.length > 0 && (
            <>
              <section className="mb-5 overflow-hidden rounded-2xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
                <button type="button" onClick={() => setSettingsOpen(value => !value)} className="flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50">
                  <SettingOutlined className="text-violet-500" />
                  <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">Загальні налаштування</span>
                  <span className="text-xs text-gray-400">· діють на {commonCount} з {entries.length} карток</span>
                  <span className="ml-auto text-gray-400">{settingsOpen ? '−' : '+'}</span>
                </button>
                {settingsOpen && (
                  <div className="border-t border-gray-100 px-4 pb-4 dark:border-gray-800">
                    <div className="flex flex-wrap items-center gap-2 py-3 text-xs">
                      <span className="text-gray-500">Область дії:</span>
                      <button type="button" onClick={() => setEntries(current => current.map(entry => ({ ...entry, commonSelected: true })))} className="text-violet-600 hover:underline">усі картки</button>
                      <button type="button" onClick={() => setEntries(current => current.map(entry => ({ ...entry, commonSelected: entry.included })))} className="text-violet-600 hover:underline">лише увімкнені</button>
                      <button type="button" onClick={() => setEntries(current => current.map(entry => ({ ...entry, commonSelected: false })))} className="text-gray-500 hover:underline">зняти всі</button>
                      <span className="text-gray-400">Прапорець ⚙ виключає окрему картку зі спільних змін.</span>
                    </div>
                    <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
                      <label className="text-xs text-gray-500">Композиція
                        <select value={layoutPreset} onChange={event => setLayoutPreset(event.target.value as LayoutPreset)} className={`${INPUT} mt-1 w-full`}>
                          <option value="keep">Не змінювати</option><option value="auto">Розумна</option><option value="hero">Головне фото</option><option value="grid">Рівна сітка</option>
                        </select>
                      </label>
                      <label className="text-xs text-gray-500">Тло
                        <select value={backgroundPreset} onChange={event => setBackgroundPreset(event.target.value as BackgroundPreset)} className={`${INPUT} mt-1 w-full`}>
                          <option value="keep">Не змінювати</option><option value="white">Біле</option><option value="soft">Світле</option><option value="warm">Тепле</option><option value="dark">Темне</option>
                        </select>
                      </label>
                      <label className="text-xs text-gray-500">Час
                        <select value={timePreset} onChange={event => setTimePreset(event.target.value as TimePreset)} className={`${INPUT} mt-1 w-full`}>
                          <option value="keep">Не змінювати</option><option value="stagger">За розкладом, із паузою</option><option value="now">Послідовно зараз</option>
                        </select>
                      </label>
                    </div>
                    {timePreset === 'stagger' && (
                      <div className="mt-3 flex flex-wrap items-end gap-3">
                        <label className="text-xs text-gray-500">Перший пост
                          <input type="datetime-local" value={baseTime} onChange={event => setBaseTime(event.target.value)} className={`${INPUT} mt-1 block`} />
                        </label>
                        <label className="text-xs text-gray-500">Інтервал, хв
                          <input type="number" min={1} max={60} value={intervalMinutes} onChange={event => setIntervalMinutes(Math.max(1, Math.min(60, Number(event.target.value) || 1)))} className={`${INPUT} mt-1 block w-24`} />
                        </label>
                        {timeProblem && <span className="pb-2 text-[11px] text-rose-500">{timeProblem}</span>}
                      </div>
                    )}
                    <div className="mt-3 flex items-center justify-between gap-3">
                      <span className="text-[11px] text-gray-400">Кожен товар має окремий колаж і власну чернетку підпису.</span>
                      <button type="button" onClick={applyCommon} disabled={!commonCount || !!timeProblem} className="rounded-lg bg-gray-900 px-3 py-2 text-xs font-semibold text-white disabled:opacity-40 dark:bg-gray-100 dark:text-gray-900">Застосувати до {commonCount}</button>
                    </div>
                  </div>
                )}
              </section>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                {entries.map((entry, index) => {
                  const firstImage = entry.draft.collage.image_idx[0] ?? 0;
                  const when = entry.draft.publish_at ? new Date(entry.draft.publish_at) : null;
                  return (
                    <article key={entry.productId} className={`overflow-hidden rounded-2xl border bg-white transition-all dark:bg-gray-900 ${entry.included ? 'border-violet-300 shadow-sm dark:border-violet-700' : 'border-gray-200 opacity-70 dark:border-gray-700'}`}>
                      <div className="flex gap-3 p-3">
                        <div className="h-20 w-20 shrink-0 overflow-hidden rounded-xl bg-gray-100 dark:bg-gray-800">
                          {entry.preview.image_urls[firstImage] ? <SmartImage src={entry.preview.image_urls[firstImage]} thumb={320} thumbOnly className="h-full w-full object-cover" /> : <div className="flex h-full items-center justify-center text-xs text-gray-400">без фото</div>}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-start gap-2">
                            <label className="flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-gray-800 dark:text-gray-100">
                              <input type="checkbox" checked={entry.included} onChange={event => updateEntry(entry.productId, { included: event.target.checked })} className="accent-violet-600" />#{entry.preview.productnumber}
                            </label>
                            <label className={`ml-auto flex cursor-pointer items-center gap-1 text-[10px] ${entry.commonSelected ? 'text-violet-600' : 'text-gray-400'}`} title="Загальні налаштування діятимуть на цю картку">
                              <input type="checkbox" checked={entry.commonSelected} onChange={event => updateEntry(entry.productId, { commonSelected: event.target.checked })} className="accent-violet-600" />⚙
                            </label>
                            <button type="button" onClick={() => setEntries(current => current.filter(item => item.productId !== entry.productId))} disabled={busy} aria-label={`Прибрати #${entry.preview.productnumber} з пакета`} title="Прибрати картку з пакета" className="-mr-0.5 -mt-0.5 flex h-5 w-5 items-center justify-center rounded-md text-gray-300 hover:bg-rose-50 hover:text-rose-500 dark:text-gray-600 dark:hover:bg-rose-900/20 dark:hover:text-rose-400"><CloseOutlined style={{ fontSize: 9 }} /></button>
                          </div>
                          <div className="mt-1 truncate text-sm font-medium text-gray-800 dark:text-gray-100">{entry.preview.brand || '—'} {entry.preview.model || entry.preview.type || ''}</div>
                          <div className="mt-1 text-[11px] text-gray-400">{entry.draft.collage.image_idx.length} фото в колажі{entry.sourceIds.length > 1 ? ` · ${entry.sourceIds.length} рядків об’єднано` : ''}</div>
                          <div className={`mt-1 text-[10px] ${entry.preview.condition_confirmation_required ? 'font-semibold text-amber-600' : 'text-gray-400'}`}>{entry.preview.condition || 'Стан не вказаний'}{entry.preview.condition_confirmation_required ? ' · потрібне підтвердження' : ''}</div>
                          {entry.preview.already_published > 0 && <div className="mt-1 text-[10px] text-rose-500">Уже публікувався — вимкнено за замовчуванням</div>}
                          {entry.preview.pending_publications > 0 && <div className="mt-1 text-[10px] text-amber-600">Уже в черзі або розкладі — вимкнено за замовчуванням</div>}
                        </div>
                      </div>
                      <div className="px-3 pb-3">
                        <div className="rounded-lg bg-gray-50 p-2 text-[11px] text-gray-500 dark:bg-gray-800/60 dark:text-gray-400">{entry.draft.collage.layout === 'auto' ? 'Розумний колаж' : entry.draft.collage.layout === 'hero' ? 'Головне фото' : 'Рівна сітка'} · {when ? when.toLocaleString('uk-UA', { dateStyle: 'short', timeStyle: 'short' }) : 'зараз'}</div>
                        <button type="button" onClick={() => setEditingId(entry.productId)} disabled={busy} className="mt-2 w-full rounded-lg border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-700 transition hover:border-violet-400 hover:text-violet-600 dark:border-gray-700 dark:text-gray-200"><EditOutlined className="mr-1.5" />Редагувати картку {index + 1}{entry.edited && <CheckOutlined className="ml-1.5 text-emerald-500" />}</button>
                      </div>
                    </article>
                  );
                })}
              </div>
            </>
          )}
          {!loading && entries.length === 0 && <div className="py-20 text-center"><div className="text-sm font-medium text-gray-600 dark:text-gray-300">Усі картки прибрано з пакета</div><div className="mt-1 text-xs text-gray-400">Закрий вікно й вибери товари знову, якщо передумаєш.</div></div>}
        </main>

        <footer className="flex items-center justify-between gap-3 border-t border-gray-100 bg-white px-5 py-3.5 dark:border-gray-800 dark:bg-gray-900">
          <span className="text-xs text-gray-400">Буде опубліковано: <b className="text-gray-700 dark:text-gray-200">{included.length}</b> постів · послідовно, з контролем помилок{risky.length ? <span className="ml-1 font-semibold text-amber-600">· {risky.length} потребують підтвердження</span> : null}</span>
          <div className="flex gap-2">
            <button type="button" onClick={onCancel} disabled={busy} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300">Скасувати</button>
            <button type="button" onClick={submit} disabled={busy || loading || !included.length || !!timeProblem || unconfigured} className="flex items-center gap-1.5 rounded-lg bg-[#7360F2] px-4 py-2 text-sm font-semibold text-white disabled:opacity-45"><SendOutlined />{busy ? 'Публікую чергу…' : `Опублікувати ${included.length}`}</button>
          </div>
        </footer>
      </div>
      {conditionConfirmOpen && (
        <ViberConditionPublishConfirmation
          items={risky.map(entry => ({ productnumber: entry.preview.productnumber, conditionName: entry.preview.condition_name || entry.preview.condition || 'Вживаний', title: [entry.preview.brand, entry.preview.model, entry.preview.type].filter(Boolean).join(' ') }))}
          busy={busy}
          onCancel={() => setConditionConfirmOpen(false)}
          onConfirm={() => {
            setConditionConfirmOpen(false);
            setConditionApproved(true);
            setLiveConfirmOpen(true);
          }}
        />
      )}
      {liveConfirmOpen && included.length > 0 && (
        <ViberLivePublishConfirmation
          count={included.length}
          channelTitle={included[0].preview.channel.title}
          publishAt={included.length === 1 ? included[0].draft.publish_at : null}
          scheduledCount={included.filter(entry => !!entry.draft.publish_at).length}
          busy={busy}
          onCancel={() => setLiveConfirmOpen(false)}
          onConfirm={() => {
            setLiveConfirmOpen(false);
            publish(conditionApproved);
          }}
        />
      )}
    </div>
  );
};

export default ViberBatchPublishDialog;
