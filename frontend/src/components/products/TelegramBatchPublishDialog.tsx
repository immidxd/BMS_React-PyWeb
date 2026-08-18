import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  CheckOutlined, ClockCircleOutlined, CloseOutlined, EditOutlined, SendOutlined,
  SettingOutlined, WarningOutlined,
} from '@ant-design/icons';
import SmartImage from '../common/SmartImage';
import TelegramPublishDialog, {
  TelegramConditionPublishConfirmation,
  type TelegramPreview, type TelegramPublishPayload,
} from './TelegramPublishDialog';

interface BatchPreviewItem {
  product_id: number;
  productnumber: string;
  source_product_ids: number[];
  ok: boolean;
  preview: TelegramPreview | null;
  error?: string | null;
}

interface BatchPreviewResponse {
  selected_count: number;
  unique_count: number;
  merged_count: number;
  batch_max_products: number;
  items: BatchPreviewItem[];
}

export interface TelegramBatchRequest {
  batch_id: string;
  items: { product_id: number; payload: TelegramPublishPayload }[];
}

interface Entry {
  productId: number;
  sourceIds: number[];
  preview: TelegramPreview;
  draft: TelegramPublishPayload;
  included: boolean;
  commonSelected: boolean;
  edited: boolean;
}

interface Props {
  productIds: number[];
  busy: boolean;
  onCancel: () => void;
  onPublish: (request: TelegramBatchRequest) => void;
}

type TargetPreset = 'keep' | 'recommended' | 'channel' | 'root' | 'custom';
type SoundPreset = 'keep' | 'silent' | 'sound';
type TimePreset = 'keep' | 'stagger' | 'now';

const INPUT = 'px-2.5 py-1.5 rounded-lg text-xs border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-sky-500/30';

function uuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `tg-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** «24.08 15:04» — рівно те, що людина бачить на картці. */
function shortTime(value: Date): string {
  return value.toLocaleString('uk-UA', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function asLocal(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function defaultDraft(preview: TelegramPreview, index: number): TelegramPublishPayload {
  const at = new Date(preview.default_channel_at);
  // Канальні пости не повинні стати одним сплеском: кожна наступна картка
  // автоматично отримує +2 хв, але людина може змінити її окремо.
  if (!Number.isNaN(at.getTime())) at.setMinutes(at.getMinutes() + index * 2);
  return {
    caption: preview.caption,
    emoji: preview.emoji,
    tagline: preview.tagline,
    features: preview.features,
    search_q: preview.search_q,
    price: preview.price ?? undefined,
    size_ids: preview.sizes.map(s => s.product_id),
    image_idx: preview.default_image_idx,
    thread_ids: preview.suggested_threads.slice(0, preview.max_threads_per_post ?? 6),
    to_channel: true,
    channel_at: Number.isNaN(at.getTime()) ? preview.default_channel_at : at.toISOString(),
    test_mode: false,
    silent: false,
    force: preview.already_published > 0,
  };
}

const TelegramBatchPublishDialog: React.FC<Props> = ({ productIds, busy, onCancel, onPublish }) => {
  // Пакет є знімком виділення в момент відкриття. Новий array reference від
  // батьківського render не означає новий пакет і не повинен скидати ручні
  // галочки після повернення з редактора картки.
  const initialProductIdsRef = useRef(
    Array.from(new Set(productIds.map(Number).filter(id => Number.isFinite(id) && id > 0))),
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<BatchPreviewResponse | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [conditionConfirmOpen, setConditionConfirmOpen] = useState(false);

  const [targetPreset, setTargetPreset] = useState<TargetPreset>('keep');
  const [soundPreset, setSoundPreset] = useState<SoundPreset>('keep');
  const [timePreset, setTimePreset] = useState<TimePreset>('stagger');
  const [sharedThreads, setSharedThreads] = useState<number[]>([]);
  const [sharedChannel, setSharedChannel] = useState(true);
  const [staggerMinutes, setStaggerMinutes] = useState(2);
  const [baseTime, setBaseTime] = useState('');

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch('/api/publications/telegram/preview-posts-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_ids: initialProductIdsRef.current }),
    })
      .then(async res => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Не вдалося підготувати пакет');
        return data as BatchPreviewResponse;
      })
      .then(data => {
        if (!alive) return;
        setMeta(data);
        const ready = data.items
          .filter((item): item is BatchPreviewItem & { preview: TelegramPreview } => !!item.ok && !!item.preview)
          .map((item, index) => ({
            productId: item.product_id,
            sourceIds: item.source_product_ids,
            preview: item.preview,
            draft: defaultDraft(item.preview, index),
            // Повторну публікацію і картку без фото не вмикаємо мовчки.
            included: item.preview.already_published === 0 && item.preview.image_count > 0,
            commonSelected: true,
            edited: false,
          }));
        setEntries(ready);
        if (ready[0]) {
          setSharedThreads(ready[0].preview.suggested_threads.slice(0, ready[0].preview.max_threads_per_post ?? 6));
          setBaseTime(asLocal(ready[0].preview.default_channel_at));
        }
        const failed = data.items.filter(item => !item.ok);
        if (failed.length) setError(`${failed.length} карток не вдалося підготувати; вони не ввійдуть у пакет.`);
      })
      .catch(err => { if (alive) setError(err.message || 'Не вдалося підготувати пакет'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // Новий набір товарів відкриває новий екземпляр діалогу. Повторний fetch
    // усередині відкритого діалогу знищив би ручні налаштування користувача.
  }, []);

  const editing = entries.find(entry => entry.productId === editingId) ?? null;
  const included = entries.filter(entry => entry.included);
  const riskyIncluded = included.filter(entry => entry.preview.condition_confirmation_required);
  const commonCount = entries.filter(entry => entry.commonSelected).length;
  const baseDate = baseTime ? new Date(baseTime) : null;
  const baseTimeInvalid = timePreset === 'stagger' && (
    !baseDate || Number.isNaN(baseDate.getTime())
    || baseDate.getTime() < Date.now() + 2 * 60_000
    || baseDate.getTime() > Date.now() + 365 * 24 * 60 * 60_000
  );
  const firstPreview = entries[0]?.preview;
  const threads = useMemo(
    () => (firstPreview?.threads ?? []).filter(t => t.thread_id !== firstPreview?.root_topic.thread_id),
    [firstPreview],
  );

  const updateEntry = (id: number, patch: Partial<Entry>) =>
    setEntries(current => current.map(entry => entry.productId === id ? { ...entry, ...patch } : entry));

  const removeEntry = (id: number) =>
    setEntries(current => current.filter(entry => entry.productId !== id));

  const applyCommon = () => {
    const base = baseDate;
    let scopedIndex = 0;
    setEntries(current => current.map(entry => {
      if (!entry.commonSelected) return entry;
      const draft = { ...entry.draft };
      if (soundPreset !== 'keep') draft.silent = soundPreset === 'silent';

      if (targetPreset === 'recommended') {
        draft.thread_ids = entry.preview.suggested_threads.slice(0, entry.preview.max_threads_per_post ?? 6);
        draft.to_channel = true;
      } else if (targetPreset === 'channel') {
        draft.thread_ids = [];
        draft.to_channel = true;
      } else if (targetPreset === 'root') {
        draft.thread_ids = [];
        draft.to_channel = false;
      } else if (targetPreset === 'custom') {
        draft.thread_ids = sharedThreads.slice(0, entry.preview.max_threads_per_post ?? 6);
        draft.to_channel = sharedChannel;
      }

      if (draft.to_channel && timePreset === 'now') draft.channel_at = null;
      if (draft.to_channel && timePreset === 'stagger' && base && !Number.isNaN(base.getTime())) {
        const at = new Date(base);
        at.setMinutes(at.getMinutes() + scopedIndex * Math.max(1, staggerMinutes));
        draft.channel_at = at.toISOString();
      }
      scopedIndex += 1;
      return { ...entry, draft, edited: true };
    }));
  };

  const [autoShifted, setAutoShifted] = useState<{ number: string; from: string; to: string }[]>([]);

  /**
   * Сам розводить пости, що потрапили на ту саму хвилину каналу.
   *
   * Telegram отримав би кілька пересилань поспіль, і бекенд такий пакет просто
   * не пустить. Раніше людина ганялася за конфліктами вручну — і, зсуваючи
   * один, легко сідала на хвилину, яку вже зайняв інший.
   *
   * Перший пост групи лишається на своєму слоті контент-плану, решта йдуть на
   * найближчу вільну хвилину з кроком +2. Це навмисно не те саме, що загальне
   * «за розкладом, із паузою»: воно перебиває час УСІМ карткам і стягує
   * тижневий план в одну годину.
   *
   * Зсув ніколи не мовчазний — що саме переїхало, видно в повідомленні над
   * списком.
   */
  useEffect(() => {
    const eligible = (entry: Entry) =>
      entry.included && entry.draft.to_channel && Boolean(entry.draft.channel_at);
    const minuteOf = (entry: Entry) =>
      Math.floor(new Date(entry.draft.channel_at as string).getTime() / 60000);

    // Спершу позначаємо ВСІ зайняті хвилини, і лише потім розводимо дублікати.
    // Інакше зсунутий пост сідав би на слот сусіда й породжував новий конфлікт —
    // рівно те, на що людина натрапила вручну.
    const taken = new Set<number>();
    for (const entry of entries) {
      if (!eligible(entry)) continue;
      const minute = minuteOf(entry);
      if (!Number.isNaN(minute)) taken.add(minute);
    }

    const claimed = new Set<number>();
    const moved: { number: string; from: string; to: string }[] = [];
    const next = entries.map(entry => {
      if (!eligible(entry)) return entry;
      const minute = minuteOf(entry);
      if (Number.isNaN(minute)) return entry;
      if (!claimed.has(minute)) {
        claimed.add(minute);
        return entry;
      }
      // Зайнято своїм же дублікатом: шукаємо найближчу хвилину, вільну і від
      // чужих слотів, і від уже зайнятих цим проходом.
      let free = minute;
      do { free += 2; } while (taken.has(free) || claimed.has(free));
      claimed.add(free);
      moved.push({
        number: entry.preview.productnumber,
        from: shortTime(new Date(minute * 60000)),
        to: shortTime(new Date(free * 60000)),
      });
      return {
        ...entry,
        draft: { ...entry.draft, channel_at: new Date(free * 60000).toISOString() },
        edited: true,
      };
    });

    if (!moved.length) return;
    setEntries(next);
    setAutoShifted(moved);
  }, [entries]);

  const scopeAll = (value: boolean) =>
    setEntries(current => current.map(entry => ({ ...entry, commonSelected: value })));

  const publish = (conditionConfirmed = false) => {
    if (!included.length) return;
    onPublish({
      batch_id: uuid(),
      items: included.map(entry => ({
        product_id: entry.productId,
        payload: {
          ...entry.draft,
          condition_confirmed: conditionConfirmed && entry.preview.condition_confirmation_required
            ? true
            : undefined,
        },
      })),
    });
  };

  const submit = () => {
    if (!included.length) return;
    if (riskyIncluded.length) {
      setConditionConfirmOpen(true);
      return;
    }
    publish();
  };

  if (editing) {
    return (
      <TelegramPublishDialog
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
    <div className="bms-dialog-host fixed inset-0 z-[95] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={busy ? undefined : onCancel} />
      <div className="relative w-full max-w-6xl max-h-[92vh] flex flex-col overflow-hidden rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-2xl bms-fade-in">
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <span className="w-10 h-10 rounded-xl bg-[#229ED9] text-white flex items-center justify-center shrink-0">
            <SendOutlined style={{ fontSize: 18 }} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-base font-semibold text-gray-900 dark:text-gray-50">Пакетна публікація в Telegram</div>
            <div className="text-xs text-gray-400 mt-0.5">
              {meta ? `${meta.selected_count} рядків → ${entries.length} карток у пакеті${meta.unique_count > entries.length ? ` · ${meta.unique_count - entries.length} прибрано` : meta.merged_count ? ` · ${meta.merged_count} рядків ростовок об’єднано` : ''}` : 'Готую картки…'}
            </div>
          </div>
          <button onClick={busy ? undefined : onCancel} className="p-2 rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800" aria-label="Закрити">
            <CloseOutlined />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 bg-gray-50/50 dark:bg-gray-950/20">
          {loading && <div className="py-20 text-center text-sm text-gray-400">Збираю фото, розміри, текст і гілки для кожного поста…</div>}
          {error && (
            <div className="mb-4 flex gap-2 px-3 py-2.5 rounded-xl text-xs text-amber-800 bg-amber-50 border border-amber-200 dark:text-amber-300 dark:bg-amber-900/20 dark:border-amber-800">
              <WarningOutlined className="mt-0.5" /> <span>{error}</span>
            </div>
          )}

          {!loading && entries.length > 0 && (
            <>
              <section className="mb-5 rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 overflow-hidden">
                <button type="button" onClick={() => setSettingsOpen(v => !v)}
                        className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50">
                  <SettingOutlined className="text-sky-500" />
                  <span className="font-semibold text-sm text-gray-800 dark:text-gray-100">Загальні налаштування</span>
                  <span className="text-xs text-gray-400">· діють на {commonCount} з {entries.length} карток</span>
                  <span className="ml-auto text-gray-400">{settingsOpen ? '−' : '+'}</span>
                </button>
                {settingsOpen && (
                  <div className="px-4 pb-4 border-t border-gray-100 dark:border-gray-800">
                    <div className="flex flex-wrap items-center gap-2 py-3 text-xs">
                      <span className="text-gray-500">Область дії:</span>
                      <button onClick={() => scopeAll(true)} className="text-sky-600 hover:underline">усі картки</button>
                      <button onClick={() => setEntries(cur => cur.map(e => ({ ...e, commonSelected: e.included })))} className="text-sky-600 hover:underline">лише увімкнені</button>
                      <button onClick={() => scopeAll(false)} className="text-gray-500 hover:underline">зняти всі</button>
                      <span className="text-gray-400">Прапорець ⚙ на картці дозволяє виключити її окремо.</span>
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                      <label className="text-xs text-gray-500">
                        Куди
                        <select value={targetPreset} onChange={e => setTargetPreset(e.target.value as TargetPreset)} className={`${INPUT} mt-1 w-full`}>
                          <option value="keep">Не змінювати</option>
                          <option value="recommended">Каталог + підібрані гілки + канал</option>
                          <option value="channel">Каталог + тільки канал</option>
                          <option value="root">Тільки каталог «ВСІ ПРОПОЗИЦІЇ»</option>
                          <option value="custom">Вибрати гілки й канал</option>
                        </select>
                      </label>
                      <label className="text-xs text-gray-500">
                        Звук
                        <select value={soundPreset} onChange={e => setSoundPreset(e.target.value as SoundPreset)} className={`${INPUT} mt-1 w-full`}>
                          <option value="keep">Не змінювати</option>
                          <option value="silent">🔕 Усе без звуку</option>
                          <option value="sound">🔔 Зі звуком</option>
                        </select>
                      </label>
                      <label className="text-xs text-gray-500">
                        Час каналу
                        <select value={timePreset} onChange={e => setTimePreset(e.target.value as TimePreset)} className={`${INPUT} mt-1 w-full`}>
                          <option value="keep">Не змінювати</option>
                          <option value="stagger">За розкладом, із паузою</option>
                          <option value="now">Усі зараз (послідовно)</option>
                        </select>
                      </label>
                    </div>

                    {targetPreset === 'custom' && (
                      <div className="mt-3 p-3 rounded-xl bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700">
                        <label className="inline-flex items-center gap-2 text-xs font-medium text-gray-700 dark:text-gray-200">
                          <input type="checkbox" checked={sharedChannel} onChange={e => setSharedChannel(e.target.checked)} className="accent-sky-600" />
                          Канал «{firstPreview?.channel.chat_title}»
                        </label>
                        <div className="mt-2 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-1.5">
                          {threads.map(thread => {
                            const on = sharedThreads.includes(thread.thread_id);
                            return (
                              <button key={thread.thread_id} onClick={() => setSharedThreads(cur => on ? cur.filter(id => id !== thread.thread_id) : [...cur, thread.thread_id].slice(0, firstPreview?.max_threads_per_post ?? 6))}
                                      className={`px-2 py-1.5 rounded-md border text-[11px] text-left truncate ${on ? 'border-sky-400 bg-sky-50 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300' : 'border-gray-200 dark:border-gray-700 text-gray-500'}`}>
                                {thread.thread_title}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {timePreset === 'stagger' && (
                      <div className="mt-3 flex flex-wrap items-end gap-3">
                        <label className="text-xs text-gray-500">Перший пост
                          <input type="datetime-local" value={baseTime} min={asLocal(new Date(Date.now() + 2 * 60_000).toISOString())}
                                 max={asLocal(new Date(Date.now() + 365 * 24 * 60 * 60_000).toISOString())}
                                 onChange={e => setBaseTime(e.target.value)} className={`${INPUT} mt-1 block`} />
                          {baseTimeInvalid && <span className="block mt-1 text-[10px] text-rose-500">Не раніше ніж через 2 хвилини.</span>}
                        </label>
                        <label className="text-xs text-gray-500">Інтервал, хв
                          <input type="number" min={1} max={60} value={staggerMinutes} onChange={e => setStaggerMinutes(Math.max(1, Math.min(60, Number(e.target.value) || 1)))} className={`${INPUT} mt-1 block w-24`} />
                        </label>
                      </div>
                    )}

                    <div className="mt-3 flex items-center justify-between gap-3">
                      <span className="text-[11px] text-gray-400">«ВСІ ПРОПОЗИЦІЇ» завжди лишається оригіналом — вимкнути його не можна.</span>
                      <button onClick={applyCommon} disabled={commonCount === 0 || baseTimeInvalid}
                              className="px-3 py-2 rounded-lg bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 text-xs font-semibold disabled:opacity-40">
                        Застосувати до {commonCount}
                      </button>
                    </div>
                  </div>
                )}
              </section>

              {autoShifted.length > 0 && (
                <div className="mb-4 flex gap-2 rounded-xl border border-sky-200 bg-sky-50 px-3 py-2.5 text-xs leading-relaxed text-sky-900 dark:border-sky-800 dark:bg-sky-900/20 dark:text-sky-200">
                  <ClockCircleOutlined className="mt-0.5 shrink-0" />
                  <div className="min-w-0">
                    <b>Час каналу збігався — розвели автоматично.</b> Telegram не приймає кілька
                    пересилань в одну хвилину.
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                      {autoShifted.map(row => (
                        <span key={row.number} className="tabular-nums">
                          #{row.number}: {row.from} → <b>{row.to}</b>
                        </span>
                      ))}
                    </div>
                  </div>
                  <button type="button" onClick={() => setAutoShifted([])}
                          className="ml-auto shrink-0 self-start text-sky-500 hover:text-sky-700" aria-label="Сховати">
                    <CloseOutlined />
                  </button>
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {entries.map((entry, index) => {
                  const p = entry.preview;
                  const when = entry.draft.channel_at ? new Date(entry.draft.channel_at) : null;
                  return (
                    <article key={entry.productId} className={`rounded-2xl border bg-white dark:bg-gray-900 overflow-hidden transition-all ${entry.included ? 'border-sky-300 dark:border-sky-700 shadow-sm' : 'border-gray-200 dark:border-gray-700 opacity-70'}`}>
                      <div className="p-3 flex gap-3">
                        <div className="w-20 h-20 rounded-xl overflow-hidden bg-gray-100 dark:bg-gray-800 shrink-0">
                          {p.image_urls[entry.draft.image_idx[0] ?? 0]
                            ? <SmartImage src={p.image_urls[entry.draft.image_idx[0] ?? 0]} thumb={320} thumbOnly className="w-full h-full object-cover" />
                            : <div className="w-full h-full flex items-center justify-center text-xs text-gray-400">без фото</div>}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-start gap-2">
                            <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-800 dark:text-gray-100 cursor-pointer">
                              <input type="checkbox" checked={entry.included}
                                     aria-label={`Публікувати #${p.productnumber}`}
                                     onChange={e => updateEntry(entry.productId, { included: e.target.checked })}
                                     className="accent-sky-600" />
                              #{p.productnumber}
                            </label>
                            <label className={`ml-auto flex items-center gap-1 text-[10px] cursor-pointer ${entry.commonSelected ? 'text-sky-600' : 'text-gray-400'}`} title="Загальні налаштування діятимуть на цю картку">
                              <input type="checkbox" checked={entry.commonSelected}
                                     aria-label={`Загальні налаштування для #${p.productnumber}`}
                                     onChange={e => updateEntry(entry.productId, { commonSelected: e.target.checked })}
                                     className="accent-sky-600" /> ⚙
                            </label>
                            <button type="button" disabled={busy}
                                    onClick={() => removeEntry(entry.productId)}
                                    title="Прибрати картку з пакета"
                                    aria-label={`Прибрати #${p.productnumber} з пакета`}
                                    className="w-5 h-5 -mt-0.5 -mr-0.5 rounded-md inline-flex items-center justify-center text-gray-300 hover:text-rose-500 hover:bg-rose-50 dark:text-gray-600 dark:hover:text-rose-400 dark:hover:bg-rose-900/20 transition-colors disabled:opacity-40">
                              <CloseOutlined style={{ fontSize: 9 }} />
                            </button>
                          </div>
                          <div className="mt-1 text-sm font-medium text-gray-800 dark:text-gray-100 truncate">{p.brand || '—'} {p.model || ''}</div>
                          <div className="mt-1 text-[11px] text-gray-400">
                            {p.image_count} фото · {p.sizes.length || 'без'} розмірів
                            {entry.sourceIds.length > 1 ? ` · ${entry.sourceIds.length} рядків об’єднано` : ''}
                          </div>
                          <div className={`mt-1 text-[10px] ${p.condition_confirmation_required ? 'font-semibold text-amber-600 dark:text-amber-400' : 'text-gray-400'}`}>
                            {p.condition_icon || '✅'} {p.condition || 'Стан не вказаний'}
                            {p.condition_confirmation_required ? ' · потрібне підтвердження' : ''}
                          </div>
                          {p.already_published > 0 && <div className="mt-1 text-[10px] text-rose-500">Уже є {p.already_published} живих постів — картку вимкнено за замовчуванням</div>}
                        </div>
                      </div>
                      <div className="px-3 pb-3">
                        <div className="p-2 rounded-lg bg-gray-50 dark:bg-gray-800/60 text-[11px] text-gray-500 dark:text-gray-400 leading-relaxed">
                          Каталог + {entry.draft.thread_ids.length} гілок
                          {entry.draft.to_channel ? ` + канал (${when ? when.toLocaleString('uk-UA', { dateStyle: 'short', timeStyle: 'short' }) : 'зараз'})` : ''}
                          {entry.draft.silent ? ' · 🔕' : ' · 🔔'}
                        </div>
                        <button onClick={() => setEditingId(entry.productId)} disabled={busy}
                                className="mt-2 w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-xs font-semibold text-gray-700 dark:text-gray-200 hover:border-sky-400 hover:text-sky-600 transition-colors">
                          <EditOutlined className="mr-1.5" /> Редагувати пост {index + 1}
                          {entry.edited && <CheckOutlined className="ml-1.5 text-emerald-500" />}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            </>
          )}
          {!loading && entries.length === 0 && (
            <div className="py-20 text-center">
              <div className="text-sm font-medium text-gray-600 dark:text-gray-300">Усі картки прибрано з пакета</div>
              <div className="mt-1 text-xs text-gray-400">Закрий це вікно й вибери товари знову, якщо передумаєш.</div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-t border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900">
          <span className="text-xs text-gray-400">
            Буде опубліковано: <b className="text-gray-700 dark:text-gray-200">{included.length}</b> постів · послідовно, з контролем помилок
            {riskyIncluded.length > 0 && <span className="ml-1 font-semibold text-amber-600 dark:text-amber-400">· {riskyIncluded.length} потребують підтвердження стану</span>}
          </span>
          <div className="flex gap-2">
            <button onClick={onCancel} disabled={busy} className="px-4 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-sm text-gray-600 dark:text-gray-300 disabled:opacity-50">Скасувати</button>
            <button onClick={submit} disabled={busy || loading || included.length === 0}
                    className="px-4 py-2 rounded-lg bg-[#229ED9] text-white text-sm font-semibold flex items-center gap-1.5 disabled:opacity-50 hover:brightness-110">
              <SendOutlined /> {busy ? 'Публікую чергу…' : `Опублікувати ${included.length}`}
            </button>
          </div>
        </div>
      </div>
      {conditionConfirmOpen && (
        <TelegramConditionPublishConfirmation
          items={riskyIncluded.map(entry => ({
            productnumber: entry.preview.productnumber,
            conditionName: entry.preview.condition_name || entry.preview.condition || 'Вживаний',
            title: [entry.preview.brand, entry.preview.model, entry.preview.type].filter(Boolean).join(' '),
          }))}
          busy={busy}
          onCancel={() => setConditionConfirmOpen(false)}
          onConfirm={() => {
            setConditionConfirmOpen(false);
            publish(true);
          }}
        />
      )}
    </div>
  );
};

export default TelegramBatchPublishDialog;
