import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CloseOutlined, SendOutlined, WarningOutlined, PlusOutlined,
  DeleteOutlined, LockOutlined, ClockCircleOutlined, ThunderboltOutlined,
  ExperimentOutlined, CheckOutlined, LoadingOutlined, SwapOutlined,
} from '@ant-design/icons';
import SmartImage from '../common/SmartImage';
import TextFormattingToolbar from '../common/TextFormattingToolbar';
import { productService } from '../../services/productService';
import { emitProductPhotosChanged, taskManager } from '../../services/taskManager';

/*
 * Діалог створення поста в Telegram — дзеркало PromPublishDialog за структурою
 * і стилем, але про інше: тут редагується не картка товару, а САМ ТЕКСТ поста
 * і список гілок, куди він піде.
 *
 * Порядок публікації повторює багаторічний ручний флоу власника:
 *   «ВСІ ПРОПОЗИЦІЇ» (оригінал, єдина редагована копія)
 *      → копії в тематичні гілки
 *      → форвард у канал BrandStore (штатно — завтра о 08:00).
 *
 * Текст збирається НА БЕКЕНДІ (`/telegram/build-caption`): шаблон описаний в
 * одному місці, тож прев'ю показує рівно те, що піде в канал, а не наближення.
 */

export interface TelegramThread { thread_id: number; thread_title: string; auto_suggest: boolean; }

export interface TelegramPreview {
  product_id: number;
  productnumber: string;
  brand: string | null;
  model: string | null;
  type: string | null;
  emoji: string;
  tagline: string;
  features: string[];
  search_q: string;
  condition: string | null;
  condition_icon: string;
  condition_name: string | null;
  condition_confirmation_required: boolean;
  price: string | null;
  sizes: { product_id: number; size: string; measurementscm: string; available: number }[];
  is_bag: boolean;
  dimensions: string | null;
  caption: string;
  caption_len: number;
  caption_limit: number;
  image_count: number;
  image_kind: 'official' | 'real' | 'none';
  image_urls: string[];
  image_names: string[];
  album_limit: number;
  album_hard_limit: number;
  max_threads_per_post?: number;
  default_image_idx: number[];
  archive: { configured: boolean; title: string };
  threads: TelegramThread[];
  suggested_threads: number[];
  root_topic: { thread_id: number; thread_title: string };
  channel: { chat_id: number; chat_title: string };
  default_channel_at: string;
  already_published: number;
  seed_source: 'template' | 'history' | null;
  warnings: string[];
}

export interface TelegramPublishPayload {
  caption: string;
  emoji: string;
  tagline: string;
  features: string[];
  search_q: string;
  price?: string;
  size_ids: number[];
  image_idx: number[];
  thread_ids: number[];
  to_channel: boolean;
  channel_at: string | null;
  test_mode: boolean;
  silent: boolean;
  /** Не перезбирати відредагований вручну текст після повернення з batch-картки. */
  caption_manual?: boolean;
  force?: boolean;
  condition_confirmed?: boolean;
}

interface Props {
  data: TelegramPreview;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (payload: TelegramPublishPayload) => void;
  /** У batch-режимі це редактор чернетки: X/клік по фону зберігає й повертає
   *  до плиток, а не публікує пост. */
  mode?: 'publish' | 'draft';
  initialPayload?: TelegramPublishPayload;
  /** Канонічне редагування фото змінює URL-версію і в батьківській картці,
   *  щоб пакетне прев'ю одразу показало нові пікселі. */
  onPreviewChange?: (preview: TelegramPreview) => void;
}

const TG_BLUE = '#229ED9';
const INPUT_CLS = 'w-full px-2.5 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:border-sky-400 transition-colors disabled:opacity-60 disabled:bg-gray-50 dark:disabled:bg-gray-800/50';
const LABEL_CLS = 'text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500';

export interface TelegramConditionConfirmationItem {
  productnumber: string;
  conditionName: string;
  title?: string;
}

export const TelegramConditionPublishConfirmation: React.FC<{
  items: TelegramConditionConfirmationItem[];
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}> = ({ items, busy = false, onCancel, onConfirm }) => {
  if (!items.length) return null;
  const many = items.length > 1;
  return (
    <div className="fixed inset-0 z-[140] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={busy ? undefined : onCancel} />
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-amber-200 dark:border-amber-800 bg-white dark:bg-gray-900 shadow-2xl bms-fade-in">
        <div className="flex items-start gap-3 px-5 pt-5">
          <span className="w-10 h-10 rounded-xl bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300 flex items-center justify-center shrink-0">
            <WarningOutlined style={{ fontSize: 18 }} />
          </span>
          <div>
            <div className="text-base font-semibold text-gray-900 dark:text-gray-50">
              {many ? 'Перевір стани перед публікацією' : 'Опублікувати товар із цим станом?'}
            </div>
            <p className="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
              {many
                ? `У пакеті є ${items.length} товарів зі станом «Вживаний» або «Пошкоджений».`
                : 'Цей стан буде прямо вказаний у пості й пост побачать підписники Telegram.'}
            </p>
          </div>
        </div>
        <div className="mx-5 mt-4 max-h-52 overflow-y-auto space-y-2">
          {items.map(item => (
            <div key={`${item.productnumber}-${item.conditionName}`} className="rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2.5 dark:border-amber-800 dark:bg-amber-900/20">
              <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                #{item.productnumber} · {item.conditionName}
              </div>
              {item.title && <div className="mt-0.5 text-xs text-gray-500 dark:text-gray-400 truncate">{item.title}</div>}
            </div>
          ))}
        </div>
        <p className="mx-5 mt-3 text-[11px] leading-relaxed text-amber-800 dark:text-amber-300">
          Переконайся, що поточний стан у BMS правильний. Після підтвердження почнеться жива публікація.
        </p>
        <div className="mt-5 flex justify-end gap-2 border-t border-gray-100 px-5 py-3.5 dark:border-gray-800">
          <button type="button" onClick={onCancel} disabled={busy}
                  className="px-4 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-sm text-gray-600 dark:text-gray-300 disabled:opacity-50">
            Повернутися й перевірити
          </button>
          <button type="button" onClick={onConfirm} disabled={busy}
                  className="px-4 py-2 rounded-lg bg-amber-600 text-white text-sm font-semibold hover:bg-amber-700 disabled:opacity-50">
            Так, опублікувати
          </button>
        </div>
      </div>
    </div>
  );
};

function localDateTimeValue(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function withImageVersion(url: string, version: string): string {
  const [path, query = ''] = url.split('?');
  const params = new URLSearchParams(query);
  params.set('v', version);
  return `${path}?${params.toString()}`;
}

// Найчастіші емодзі з реальних постів каналу — щоб не шукати в системній
// палітрі. Поле лишається вільним: там регулярно зʼявляється творчий вибір
// (🐈‍⬛ під котячий принт, 🌋, 🥏).
const EMOJI_CHOICES = ['👟', '👞', '🥾', '👡', '🩴', '🥿', '👠', '🐊', '🏖', '👜', '🎒', '🧳', '👛', '🧥', '⚡️', '🔥'];

/* Прев'ю мусить показувати пост ТАК, ЯК ЙОГО ПОБАЧАТЬ ПІДПИСНИКИ, а не сирий
   текст із зірочками: інакше неможливо оцінити, де жирне, а де випадково
   зламана розмітка. Розбираємо той самий діалект, що приймає Telegram:
   **жирний**, __курсив__, `моно`, ~~закреслений~~, [текст](посилання). Вкладеність (**__X__**)
   у постах каналу трапляється постійно, тому парсер рекурсивний. */
const MD_TOKEN = /(\*\*|__|~~|`|\[)/;

/** Українська форма числа: 1 копія, 2 копії, 5 копій. */
function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function renderMarkdown(src: string, keyPrefix = 'm'): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let rest = src;
  let k = 0;
  while (rest) {
    const m = rest.match(MD_TOKEN);
    if (!m || m.index === undefined) { out.push(rest); break; }
    if (m.index > 0) { out.push(rest.slice(0, m.index)); rest = rest.slice(m.index); }

    const tok = m[1];
    if (tok === '[') {
      const link = rest.match(/^\[([^\]]*)\]\(([^)]*)\)/);
      if (link) {
        out.push(
          <span key={`${keyPrefix}${k++}`} className="text-sky-600 dark:text-sky-400 underline decoration-sky-300"
                title={link[2]}>
            {renderMarkdown(link[1], `${keyPrefix}${k}l`)}
          </span>,
        );
        rest = rest.slice(link[0].length);
        continue;
      }
      out.push('['); rest = rest.slice(1); continue;
    }

    const close = rest.indexOf(tok, tok.length);
    if (close === -1) { out.push(rest); break; }   // непарний маркер — лишаємо як текст
    const inner = rest.slice(tok.length, close);
    const kids = tok === '`' ? [inner] : renderMarkdown(inner, `${keyPrefix}${k}i`);
    out.push(
      tok === '**' ? <b key={`${keyPrefix}${k++}`}>{kids}</b>
      : tok === '__' ? <i key={`${keyPrefix}${k++}`}>{kids}</i>
      : tok === '~~' ? <s key={`${keyPrefix}${k++}`}>{kids}</s>
      : <code key={`${keyPrefix}${k++}`} className="px-1 rounded bg-gray-200/70 dark:bg-gray-700 font-mono text-[11px]">{kids}</code>,
    );
    rest = rest.slice(close + tok.length);
  }
  return out;
}

const TelegramPublishDialog: React.FC<Props> = ({
  data, busy, onCancel, onConfirm, mode = 'publish', initialPayload, onPreviewChange,
}) => {
  const draftMode = mode === 'draft';
  const [emoji, setEmoji] = useState(initialPayload?.emoji ?? data.emoji ?? '👟');
  const [tagline, setTagline] = useState(initialPayload?.tagline ?? data.tagline ?? '');
  const [searchQ, setSearchQ] = useState(initialPayload?.search_q ?? data.search_q ?? '');
  const [price, setPrice] = useState(initialPayload?.price ?? data.price ?? '');
  const [features, setFeatures] = useState<string[]>(
    initialPayload ? initialPayload.features : (data.features.length ? data.features : ['']),
  );
  const [sizeIds, setSizeIds] = useState<number[]>(initialPayload?.size_ids ?? data.sizes.map(s => s.product_id));
  // Порядок у масиві = порядок фото в альбомі; перше — обкладинка поста.
  const [imageIdx, setImageIdx] = useState<number[]>(initialPayload?.image_idx ?? data.default_image_idx ?? []);
  const [imageUrls, setImageUrls] = useState<string[]>(data.image_urls);
  const [mirroringImage, setMirroringImage] = useState<number | null>(null);
  const [threadIds, setThreadIds] = useState<number[]>(initialPayload?.thread_ids ?? data.suggested_threads ?? []);
  const [toChannel, setToChannel] = useState(initialPayload?.to_channel ?? true);
  const initialChannelAt = initialPayload?.channel_at;
  const [channelTiming, setChannelTiming] = useState<'default' | 'now' | 'custom'>(() => {
    if (!initialPayload) return 'default';
    if (initialChannelAt === null) return 'now';
    return initialChannelAt === data.default_channel_at ? 'default' : 'custom';
  });
  const [customChannelAt, setCustomChannelAt] = useState(
    localDateTimeValue(initialChannelAt && initialChannelAt !== data.default_channel_at ? initialChannelAt : data.default_channel_at),
  );
  const [testMode, setTestMode] = useState(initialPayload?.test_mode ?? false);
  const [silent, setSilent] = useState(initialPayload?.silent ?? false);

  const [caption, setCaption] = useState(initialPayload?.caption ?? data.caption);
  const [captionLen, setCaptionLen] = useState((initialPayload?.caption ?? data.caption).length);
  const [captionManual, setCaptionManual] = useState(initialPayload?.caption_manual ?? false);
  const captionRef = useRef<HTMLTextAreaElement>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [conditionConfirmOpen, setConditionConfirmOpen] = useState(false);

  // Гілки без «ВСІ ПРОПОЗИЦІЇ» — вона завжди публікується й вимкнути її не можна:
  // це оригінал, з якого живуть усі копії та вся подальша знімалка з продажу.
  const pickableThreads = useMemo(
    () => data.threads.filter(t => t.thread_id !== data.root_topic.thread_id),
    [data.threads, data.root_topic.thread_id],
  );

  // ── Живе прев'ю: текст перезбирає бекенд ──────────────────────────────────
  const rebuildTimer = useRef<number | null>(null);
  const reqSeq = useRef(0);
  const rebuild = useCallback(async () => {
    if (captionManual) return;
    const seq = ++reqSeq.current;
    setRebuilding(true);
    try {
      const res = await fetch('/api/publications/telegram/build-caption', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          product_id: data.product_id,
          emoji, tagline, search_q: searchQ, price,
          features: features.filter(f => f.trim()),
          size_ids: sizeIds,
        }),
      });
      const d = await res.json();
      // Відповідь, що прилетіла після наступного редагування, — застаріла.
      if (seq !== reqSeq.current) return;
      if (res.ok) {
        setCaption(d.caption);
        setCaptionLen(d.caption_len);
        setProblem(d.problem || null);
      } else {
        setProblem(d.detail || 'Не вдалося зібрати текст');
      }
    } catch (e: any) {
      if (seq === reqSeq.current) setProblem(e.message || 'Помилка звʼязку');
    } finally {
      if (seq === reqSeq.current) setRebuilding(false);
    }
  }, [data.product_id, emoji, tagline, searchQ, price, features, sizeIds, captionManual]);

  useEffect(() => {
    if (rebuildTimer.current) window.clearTimeout(rebuildTimer.current);
    rebuildTimer.current = window.setTimeout(rebuild, 350);
    return () => { if (rebuildTimer.current) window.clearTimeout(rebuildTimer.current); };
  }, [rebuild]);

  const setFeature = (i: number, v: string) =>
    setFeatures(fs => fs.map((f, j) => (j === i ? v : f)));
  const setManualCaption = (value: string) => {
    // Інвалідуємо вже запущену автоматичну перебудову: її запізніла відповідь
    // не повинна перезаписати щойно відформатований користувачем текст.
    reqSeq.current += 1;
    setRebuilding(false);
    setCaptionManual(true);
    setCaption(value);
    setCaptionLen(value.length);
    setProblem(!value.trim()
      ? 'Порожній текст поста'
      : value.length > data.caption_limit
        ? `Текст завдовгий: ${value.length} символів, ліміт Telegram — ${data.caption_limit}`
        : null);
  };
  const toggle = (arr: number[], id: number) =>
    arr.includes(id) ? arr.filter(x => x !== id) : [...arr, id];

  const customWhen = channelTiming === 'custom' && customChannelAt ? new Date(customChannelAt) : null;
  const customWhenValid = !!customWhen && !Number.isNaN(customWhen.getTime());
  const channelAt = channelTiming === 'now'
    ? null
    : channelTiming === 'custom'
      ? (customWhenValid ? customWhen!.toISOString() : '')
      : data.default_channel_at;
  const channelWhenLabel = useMemo(() => {
    const d = new Date(data.default_channel_at);
    const day = d.toDateString() === new Date().toDateString() ? 'сьогодні' : 'завтра';
    return `${day} о ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }, [data.default_channel_at]);

  const noPhotos = data.image_count === 0;
  const overLimit = captionLen > data.caption_limit;
  const scheduleTooSoon = !!(toChannel && channelTiming === 'custom' && (
    !customWhenValid || customWhen!.getTime() < Date.now() + 2 * 60_000
  ));
  const scheduleTooFar = !!(customWhenValid && customWhen!.getTime() > Date.now() + 365 * 24 * 60 * 60_000);
  const dialogBusy = busy || mirroringImage !== null;
  const blocked = dialogBusy || rebuilding || noPhotos || overLimit || !!problem || imageIdx.length === 0 || scheduleTooSoon || scheduleTooFar;
  const maxThreads = data.max_threads_per_post ?? 6;

  // Клік по фото: додає в кінець альбому або прибирає. Знімати останнє не даємо
  // — альбом без жодного фото Telegram не приймає.
  const toggleImage = (i: number) => setImageIdx(cur => {
    if (cur.includes(i)) return cur.length > 1 ? cur.filter(x => x !== i) : cur;
    if (cur.length >= data.album_hard_limit) return cur;
    return [...cur, i];
  });

  const toggleThread = (id: number) => setThreadIds(cur => {
    if (cur.includes(id)) return cur.filter(x => x !== id);
    return cur.length >= maxThreads ? cur : [...cur, id];
  });

  const mirrorPhoto = async (index: number) => {
    const filename = data.image_names[index];
    if (!filename || dialogBusy) return;
    setMirroringImage(index);
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
      const nextUrls = imageUrls.map((url, i) => i === index ? withImageVersion(url, version) : url);
      setImageUrls(nextUrls);
      onPreviewChange?.({ ...data, image_urls: nextUrls });
      emitProductPhotosChanged(data.product_id);
    } catch {
      // Task Center уже зберіг точну помилку й показав сповіщення.
    } finally {
      setMirroringImage(null);
    }
  };

  const makePayload = (conditionConfirmed = false): TelegramPublishPayload => ({
    caption,
    emoji, tagline, search_q: searchQ, price,
    features: features.filter(f => f.trim()),
    size_ids: sizeIds,
    image_idx: imageIdx,
    thread_ids: testMode ? [] : threadIds,
    to_channel: testMode ? false : toChannel,
    channel_at: channelAt,
    test_mode: testMode,
    silent,
    caption_manual: captionManual || undefined,
    force: data.already_published > 0,
    condition_confirmed: conditionConfirmed || undefined,
  });

  const submit = () => {
    const needsConfirmation = !draftMode && !testMode && data.condition_confirmation_required;
    if (needsConfirmation) {
      setConditionConfirmOpen(true);
      return;
    }
    onConfirm(makePayload());
  };

  // У редакторі batch-чернетки «згорнути назад» означає зберегти поточний
  // валідний стан. Під час перебудови або за невалідних даних редактор лишається
  // відкритим — X ніколи не має мовчки загубити щойно внесені зміни.
  const closeDialog = () => {
    if (draftMode) {
      if (!blocked) submit();
      return;
    }
    onCancel();
  };

  return (
    <div className="bms-dialog-host fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={dialogBusy ? undefined : closeDialog} />

      <div className="relative w-full max-w-4xl max-h-[90vh] flex flex-col rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-2xl overflow-hidden bms-fade-in">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <span className="w-9 h-9 rounded-xl flex items-center justify-center text-white shrink-0" style={{ backgroundColor: TG_BLUE }}>
            <SendOutlined style={{ fontSize: 17 }} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-base font-semibold text-gray-900 dark:text-gray-50 leading-tight">
              {draftMode ? 'Редагування Telegram-поста' : data.already_published > 0 ? 'Опублікувати ще раз у Telegram' : 'Публікація в Telegram'}
            </div>
            <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
              #{data.productnumber} · спершу «{data.root_topic.thread_title}», далі копії в обрані гілки
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {data.seed_source && (
              <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800"
                    title={data.seed_source === 'template' ? 'Текст узято з памʼяті цієї моделі' : 'Текст переписано з попереднього поста тієї ж моделі'}>
                {data.seed_source === 'template' ? 'З памʼяті моделі' : 'З минулого поста'}
              </span>
            )}
            {data.sizes.length > 1 && (
              <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-violet-50 text-violet-700 border border-violet-200 dark:bg-violet-900/30 dark:text-violet-300 dark:border-violet-800">
                Ростовка: {data.sizes.length} розмірів
              </span>
            )}
            <button onClick={dialogBusy ? undefined : closeDialog}
                    className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
                    aria-label="Закрити">
              <CloseOutlined className="text-sm" />
            </button>
          </div>
        </div>

        {/* Попередження */}
        {(data.warnings.length > 0 || data.already_published > 0) && (
          <div className="px-5 pt-3 space-y-1.5">
            {data.already_published > 0 && (
              <div className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs bg-rose-50 text-rose-800 border border-rose-200 dark:bg-rose-900/20 dark:text-rose-300 dark:border-rose-800">
                <WarningOutlined className="mt-0.5 shrink-0" />
                <span>Товар уже має {data.already_published} живих постів. Публікація створить ще одні — старі не зникнуть.</span>
              </div>
            )}
            {data.warnings.map((w, i) => (
              <div key={i} className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs bg-amber-50 text-amber-800 border border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-800">
                <WarningOutlined className="mt-0.5 shrink-0" />
                <span>{w}</span>
              </div>
            ))}
          </div>
        )}

        {/* Body: ліворуч редагування, праворуч живе прев'ю */}
        <div className="flex-1 overflow-y-auto px-5 py-4 grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* ── Ліва колонка: складові поста ── */}
          <div className="space-y-4 min-w-0">
            {/* Фото */}
            <div>
              <div className="flex items-center justify-between">
                <div className={LABEL_CLS}>
                  Фото альбому · обрано {imageIdx.length} з {data.image_count}
                  {data.image_kind === 'official' ? ' (офіційні)' : data.image_kind === 'real' ? ' (реальні)' : ''}
                </div>
                {imageIdx.length !== data.album_limit && data.image_count >= data.album_limit && (
                  <button type="button" disabled={busy}
                          onClick={() => setImageIdx(data.default_image_idx)}
                          className="text-[11px] font-medium text-sky-600 dark:text-sky-400 hover:text-sky-800 transition-colors">
                    Перші {data.album_limit}
                  </button>
                )}
              </div>
              {noPhotos ? (
                <div className="mt-1 text-xs text-rose-600 dark:text-rose-400">
                  Немає фото — Telegram не приймає пост без альбому.
                </div>
              ) : (
                <>
                  <div className="text-[11px] text-gray-400 dark:text-gray-500 mt-0.5 mb-1.5 leading-snug">
                    У твоїх постах їх зазвичай {data.album_limit}. Клік — додати чи прибрати;
                    цифра показує порядок, перше фото стане обкладинкою. ↔ віддзеркалює
                    канонічне фото в BMS і Cloudflare.
                  </div>
                  <div className="flex gap-1.5 overflow-x-auto pb-1">
                    {imageUrls.map((u, i) => {
                      const pos = imageIdx.indexOf(i);
                      const on = pos >= 0;
                      return (
                        <div key={data.image_names[i] || i} className="relative h-16 w-16 shrink-0 group/tgphoto">
                          <button type="button" disabled={dialogBusy} onClick={() => toggleImage(i)}
                                  title={data.image_names[i] || undefined}
                                  className={`relative h-full w-full rounded-lg overflow-hidden border-2 transition-all ${
                                    on ? 'border-sky-500' : 'border-transparent opacity-40 hover:opacity-70'}`}>
                            <SmartImage src={u} thumb={96} thumbOnly className="h-full w-full object-cover" />
                            {on && (
                              <span className="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-sky-500 text-white text-[10px] font-bold flex items-center justify-center shadow">
                                {pos + 1}
                              </span>
                            )}
                          </button>
                          <button type="button" disabled={dialogBusy}
                                  onClick={(e) => { e.stopPropagation(); void mirrorPhoto(i); }}
                                  title="Віддзеркалити горизонтально та зберегти в BMS і Cloudflare"
                                  aria-label={`Віддзеркалити фото ${i + 1}`}
                                  className="absolute bottom-1 right-1 w-5 h-5 rounded-full inline-flex items-center justify-center bg-gray-950/75 text-white shadow backdrop-blur-sm opacity-80 hover:opacity-100 hover:bg-sky-600 active:scale-95 transition disabled:opacity-50">
                            {mirroringImage === i
                              ? <LoadingOutlined spin style={{ fontSize: 10 }} />
                              : <SwapOutlined style={{ fontSize: 10 }} />}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                  {imageIdx.length > data.album_limit && (
                    <div className="text-[11px] text-amber-600 dark:text-amber-400">
                      Обрано більше, ніж {data.album_limit} — пост відрізнятиметься від решти каналу.
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Заголовок */}
            <div>
              <div className={LABEL_CLS}>Заголовок</div>
              <div className="mt-1 flex gap-1.5">
                <input className={`${INPUT_CLS} !w-14 text-center text-base shrink-0`} value={emoji}
                       onChange={e => setEmoji(e.target.value)} disabled={busy} title="Емодзі на початку поста" />
                <input className={`${INPUT_CLS} min-w-0`} value={tagline} onChange={e => setTagline(e.target.value)}
                       disabled={busy} placeholder="короткий опис після «• », напр. «жіночі босоніжки на платформі»" />
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {EMOJI_CHOICES.map(e => (
                  <button key={e} type="button" onClick={() => setEmoji(e)} disabled={busy}
                          className={`w-7 h-7 rounded-md text-base leading-none transition-colors ${
                            emoji === e ? 'bg-sky-100 dark:bg-sky-900/40 ring-1 ring-sky-400'
                                        : 'hover:bg-gray-100 dark:hover:bg-gray-800'}`}>
                    {e}
                  </button>
                ))}
              </div>
              <div className="mt-2">
                <div className={LABEL_CLS}>Пошуковий запит під посиланням моделі</div>
                <input className={`${INPUT_CLS} mt-1 text-xs font-mono`} value={searchQ}
                       onChange={e => setSearchQ(e.target.value)} disabled={busy}
                       placeholder="Бренд-Модель-Маркування" />
              </div>
            </div>

            {/* Переваги */}
            <div>
              <div className="flex items-center justify-between mb-0.5">
                <div className={LABEL_CLS}>Переваги (рядки «▪️»)</div>
                <button onClick={() => setFeatures(fs => [...fs, ''])} disabled={busy}
                        className="flex items-center gap-1 text-[11px] font-medium text-sky-600 dark:text-sky-400 hover:text-sky-800 dark:hover:text-sky-300 transition-colors">
                  <PlusOutlined style={{ fontSize: 10 }} /> Додати
                </button>
              </div>
              <div className="text-[11px] text-gray-400 dark:text-gray-500 mb-2 leading-snug">
                Чернетка складена з матеріалів і підошви товару. Внутрішні опис і примітка
                BMS сюди не потрапляють ніколи. Розмітка Telegram працює: <code>**жирний**</code>,
                <code> __курсив__</code>.
              </div>
              <div className="space-y-1.5">
                {features.map((f, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <span className="text-gray-300 dark:text-gray-600 text-xs shrink-0">▪️</span>
                    <input className={`${INPUT_CLS} text-xs`} value={f} disabled={busy}
                           onChange={e => setFeature(i, e.target.value)}
                           placeholder="напр. Натуральна шкіра" />
                    <button onClick={() => setFeatures(fs => fs.filter((_, j) => j !== i))} disabled={busy}
                            className="w-7 flex justify-center text-gray-300 hover:text-red-500 dark:text-gray-600 dark:hover:text-red-400 transition-colors"
                            title="Прибрати рядок">
                      <DeleteOutlined style={{ fontSize: 12 }} />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            {/* Ціна + стан */}
            <div className="flex items-start gap-4">
              <div className="w-32 shrink-0">
                <div className={LABEL_CLS}>Ціна, грн</div>
                <input type="number" min={1} className={`${INPUT_CLS} mt-1 font-semibold`} value={price}
                       onChange={e => setPrice(e.target.value)} disabled={busy} />
              </div>
              <div className="text-xs text-gray-500 dark:text-gray-400 pt-5 leading-relaxed">
                Стан: <b>{data.condition ? `${data.condition_icon || '✅'} ${data.condition}` : '—'}</b>
                <span className="text-gray-400 dark:text-gray-500"> · береться зі стану й пакування товару</span>
              </div>
            </div>

            {/* Розміри поста */}
            {data.sizes.length > 0 && (
              <div>
                <div className={LABEL_CLS}>Розміри в пості</div>
                <div className="text-[11px] text-gray-400 dark:text-gray-500 mt-0.5 mb-1.5">
                  Показані лише ті, що є в наявності. Знята галочка прибирає розмір із тексту.
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {data.sizes.map(s => {
                    const on = sizeIds.includes(s.product_id);
                    return (
                      <button key={s.product_id} type="button" disabled={busy}
                              onClick={() => setSizeIds(v => toggle(v, s.product_id))}
                              title={s.measurementscm ? `на ніжку ${s.measurementscm} см` : undefined}
                              className={`px-2.5 py-1 rounded-md text-xs font-medium border transition-colors ${
                                on ? 'bg-sky-50 dark:bg-sky-900/30 border-sky-400 text-sky-700 dark:text-sky-300'
                                   : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-400'}`}>
                        {s.size || '—'}
                        {s.available > 1 && <span className="ml-1 text-violet-500">×{s.available}</span>}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* ── Права колонка: прев'ю + куди публікуємо ── */}
          <div className="space-y-4 min-w-0">
            <div>
              <div className="flex items-center justify-between">
                <div className={LABEL_CLS}>Як побачать підписники</div>
                <span className={`text-[11px] font-medium ${overLimit ? 'text-rose-500' : 'text-gray-400'}`}>
                  {rebuilding ? 'оновлюю…' : `${captionLen} / ${data.caption_limit}`}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                <TextFormattingToolbar dialect="telegram" targetRef={captionRef} value={caption} onChange={setManualCaption} disabled={dialogBusy} accent="sky" />
                {captionManual && (
                  <button type="button" onClick={() => {
                    reqSeq.current += 1;
                    setRebuilding(false);
                    setCaptionManual(false);
                    setProblem(null);
                  }} disabled={dialogBusy}
                          className="text-[10px] font-medium text-sky-600 hover:underline disabled:opacity-50 dark:text-sky-400">
                    Знову зібрати з полів
                  </button>
                )}
              </div>
              <textarea ref={captionRef} aria-label="Текст Telegram-поста" rows={7} value={caption}
                        onChange={event => setManualCaption(event.target.value)} disabled={dialogBusy}
                        className={`${INPUT_CLS} mt-2 resize-y font-sans text-xs leading-relaxed`} />
              {captionManual && <div className="mt-1 text-[10px] text-sky-600 dark:text-sky-400">Ручне редагування активне — поля ліворуч не перезапишуть цей текст.</div>}
              <div className="mt-2 p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 text-[12px] leading-relaxed text-gray-800 dark:text-gray-100 whitespace-pre-wrap break-words max-h-56 overflow-y-auto">
                {renderMarkdown(caption)}
              </div>
              {problem && (
                <div className="mt-1.5 flex items-start gap-2 px-3 py-2 rounded-lg text-xs bg-rose-50 text-rose-800 border border-rose-200 dark:bg-rose-900/20 dark:text-rose-300 dark:border-rose-800">
                  <WarningOutlined className="mt-0.5 shrink-0" /><span>{problem}</span>
                </div>
              )}
            </div>

            {/* Куди */}
            <div>
              <div className={LABEL_CLS}>Куди публікуємо</div>

              {/* Репетиція: той самий пост, але лише в приватний WORKSHOP.
                  Ніхто, крім власника, його не бачить, у базі не лишається
                  сліду, і товар не стає «опублікованим». */}
              {data.archive.configured && (
                <label className={`mt-1.5 flex items-start gap-3 rounded-xl border px-3.5 py-3 cursor-pointer transition-colors ${
                  testMode ? 'border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-900/20'
                           : 'border-gray-200 dark:border-gray-700'}`}>
                  <input type="checkbox" checked={testMode} disabled={busy}
                         onChange={e => setTestMode(e.target.checked)}
                         className="mt-0.5 h-4 w-4 rounded border-gray-300 accent-amber-500 shrink-0" />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-gray-800 dark:text-gray-100">
                      <ExperimentOutlined className="mr-1 text-amber-500" />
                      Тестовий пост у «{data.archive.title}»
                    </span>
                    <span className="block mt-0.5 text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
                      Репетиція: той самий альбом і текст, але тільки в твій приватний архів.
                      У каталог і канал нічого не піде, товар не стане опублікованим.
                    </span>
                  </span>
                </label>
              )}

              <div className={`mt-1.5 flex items-center gap-2 px-3 py-2 rounded-xl border transition-opacity ${
                testMode ? 'border-gray-200 dark:border-gray-700 opacity-40'
                         : 'border-sky-200 bg-sky-50/60 dark:border-sky-800 dark:bg-sky-900/15'}`}>
                <LockOutlined className="text-sky-500 shrink-0" style={{ fontSize: 12 }} />
                <span className="text-xs text-gray-700 dark:text-gray-200 min-w-0">
                  <b>{data.root_topic.thread_title}</b>
                  <span className="text-gray-500 dark:text-gray-400"> — оригінал, завжди. Лише його Telegram дозволяє редагувати, коли розмір продасться.</span>
                </span>
              </div>

              <div className={`mt-2 flex items-center justify-between ${testMode ? 'opacity-40' : ''}`}>
                <span className="text-[11px] text-gray-400 dark:text-gray-500">
                  Тематичні гілки · обрано {threadIds.length}
                </span>
                <button type="button" disabled={busy}
                        onClick={() => setThreadIds(threadIds.length ? [] : data.suggested_threads.slice(0, maxThreads))}
                        className="text-[11px] font-medium text-sky-600 dark:text-sky-400 hover:text-sky-800 transition-colors">
                  {threadIds.length ? 'Зняти всі' : 'Повернути підбір'}
                </button>
              </div>
              <div className={`mt-1 grid grid-cols-2 gap-1.5 max-h-44 overflow-y-auto pr-1 ${testMode ? 'opacity-40 pointer-events-none' : ''}`}>
                {pickableThreads.map(t => {
                  const on = threadIds.includes(t.thread_id);
                  const suggested = data.suggested_threads.includes(t.thread_id);
                  return (
                    <button key={t.thread_id} type="button" disabled={busy}
                            onClick={() => toggleThread(t.thread_id)}
                            title={!on && threadIds.length >= maxThreads
                              ? `Максимум ${maxThreads} тематичних гілок на один пост`
                              : suggested ? 'Запропоновано за типом/статтю/сезоном товару' : undefined}
                            className={`relative px-2 py-1.5 rounded-md text-[11px] font-medium border text-left truncate transition-colors ${
                              on ? 'bg-sky-50 dark:bg-sky-900/30 border-sky-400 text-sky-700 dark:text-sky-300'
                                 : `bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:border-gray-400 ${threadIds.length >= maxThreads ? 'opacity-45' : ''}`}`}>
                      {t.thread_title}
                      {suggested && !on && (
                        <span className="absolute top-0.5 right-1 text-[9px] text-sky-400" title="Підібрано автоматично">•</span>
                      )}
                    </button>
                  );
                })}
                {pickableThreads.length === 0 && (
                  <div className="col-span-2 text-xs text-gray-400 py-2">
                    Гілок немає — онови їх кнопкою «Гілки форуму» в «Інтеграціях».
                  </div>
                )}
              </div>

              {/* Канал */}
              <div className={`mt-3 flex items-start gap-3 rounded-xl border px-3.5 py-3 transition-colors ${
                testMode ? 'border-gray-200 dark:border-gray-700 opacity-40 pointer-events-none'
                  : toChannel ? 'border-sky-200 bg-sky-50/60 dark:border-sky-800 dark:bg-sky-900/15'
                          : 'border-gray-200 dark:border-gray-700'}`}>
                <input type="checkbox" checked={toChannel && !testMode} disabled={busy || testMode}
                       onChange={e => setToChannel(e.target.checked)}
                       className="mt-0.5 h-4 w-4 rounded border-gray-300 accent-sky-600 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-gray-800 dark:text-gray-100">
                    Канал «{data.channel.chat_title}»
                  </span>
                  <span className="block mt-0.5 text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
                    Форвардом із каталогу — підпис «Переслано з» веде підписників у каталог.
                  </span>
                  {toChannel && (
                    <span className="mt-2 flex gap-1.5">
                      {([
                        { mode: 'default' as const, icon: <ClockCircleOutlined style={{ fontSize: 11 }} />, label: channelWhenLabel },
                        { mode: 'now' as const, icon: <ThunderboltOutlined style={{ fontSize: 11 }} />, label: 'зараз' },
                        { mode: 'custom' as const, icon: <ClockCircleOutlined style={{ fontSize: 11 }} />, label: 'свій час' },
                      ]).map(opt => (
                        <button key={opt.mode} type="button" disabled={busy}
                                onClick={() => setChannelTiming(opt.mode)}
                                className={`flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium border transition-colors ${
                                  channelTiming === opt.mode
                                    ? 'bg-white dark:bg-gray-800 border-sky-400 text-sky-700 dark:text-sky-300'
                                    : 'bg-transparent border-transparent text-gray-400 hover:text-gray-600'}`}>
                          {opt.icon}{opt.label}
                        </button>
                      ))}
                    </span>
                  )}
                  {toChannel && channelTiming === 'custom' && (
                    <span className="mt-2 block">
                      <input type="datetime-local" value={customChannelAt} disabled={busy}
                             min={localDateTimeValue(new Date(Date.now() + 2 * 60_000).toISOString())}
                             max={localDateTimeValue(new Date(Date.now() + 365 * 24 * 60 * 60_000).toISOString())}
                             onChange={e => setCustomChannelAt(e.target.value)}
                             className={`${INPUT_CLS} !w-auto text-xs`} />
                      {(scheduleTooSoon || scheduleTooFar) && (
                        <span className="block mt-1 text-[10px] text-rose-500">
                          {scheduleTooFar ? 'Telegram не приймає настільки далекий розклад.' : 'Обери час щонайменше через 2 хвилини.'}
                        </span>
                      )}
                    </span>
                  )}
                </span>
              </div>

              {/* Один прапорець керує всіма повідомленнями цієї операції:
                  тестом, оригіналом, копіями у гілки й форвардом у канал. */}
              <label className={`mt-2 flex items-start gap-3 rounded-xl border px-3.5 py-2.5 cursor-pointer transition-colors ${
                silent
                  ? 'border-violet-300 bg-violet-50/70 dark:border-violet-700 dark:bg-violet-900/20'
                  : 'border-gray-200 dark:border-gray-700'
              }`}>
                <input type="checkbox" checked={silent} disabled={busy}
                       onChange={e => setSilent(e.target.checked)}
                       className="mt-0.5 h-4 w-4 rounded border-gray-300 accent-violet-600 shrink-0" />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-gray-800 dark:text-gray-100">
                    🔕 Все без звуку
                  </span>
                  <span className="block mt-0.5 text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
                    Оригінал, усі копії та канал підуть без звукового сповіщення.
                    Галочку можна зняти — тоді Telegram повідомить підписників як звичайно.
                  </span>
                </span>
              </label>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 px-5 py-3.5 border-t border-gray-100 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-800/30">
          <span className={`text-[11px] min-w-0 truncate ${testMode ? 'text-amber-600 dark:text-amber-400 font-medium' : 'text-gray-400 dark:text-gray-500'}`}>
            {testMode ? (
              <>Репетиція: 1 альбом лише в «{data.archive.title}». У каталог і канал — нічого.{silent ? ' · 🔕 без звуку' : ''}</>
            ) : (
              <>
                Публікується живим: 1 оригінал
                {threadIds.length > 0 && ` + ${threadIds.length} ${plural(threadIds.length, 'копія', 'копії', 'копій')}`}
                {toChannel ? ` + канал (${channelTiming === 'now' ? 'зараз' : channelTiming === 'custom' ? (customWhenValid ? customWhen!.toLocaleString('uk-UA', { dateStyle: 'short', timeStyle: 'short' }) : 'час не вибрано') : channelWhenLabel})` : ''}
                {silent ? ' · 🔕 без звуку' : ''}
              </>
            )}
          </span>
          <div className="flex items-center gap-2 shrink-0">
            <button onClick={onCancel} disabled={dialogBusy}
                    className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 hover:text-gray-900 hover:bg-gray-50 dark:text-gray-300 dark:hover:text-gray-100 dark:hover:bg-gray-800 transition-colors duration-150 disabled:opacity-60">
              {draftMode ? 'Без останніх змін' : 'Скасувати'}
            </button>
            <button onClick={submit} disabled={blocked}
                    title={noPhotos ? 'Немає фото' : overLimit ? 'Текст довший за ліміт Telegram' : undefined}
                    className="px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors duration-150 flex items-center gap-1.5 disabled:opacity-60 hover:brightness-110"
                    style={{ backgroundColor: draftMode ? '#111827' : testMode ? '#D97706' : TG_BLUE }}>
              {draftMode ? <CheckOutlined style={{ fontSize: 14 }} /> : testMode ? <ExperimentOutlined style={{ fontSize: 14 }} /> : <SendOutlined style={{ fontSize: 14 }} />}
              {draftMode ? 'Зберегти й назад' : busy ? (testMode ? 'Надсилаю…' : 'Публікую…') : (testMode ? 'Надіслати тест' : 'Опублікувати')}
            </button>
          </div>
        </div>
      </div>
      {conditionConfirmOpen && (
        <TelegramConditionPublishConfirmation
          items={[{
            productnumber: data.productnumber,
            conditionName: data.condition_name || data.condition || 'Вживаний',
            title: [data.brand, data.model, data.type].filter(Boolean).join(' '),
          }]}
          busy={busy}
          onCancel={() => setConditionConfirmOpen(false)}
          onConfirm={() => {
            setConditionConfirmOpen(false);
            onConfirm(makePayload(true));
          }}
        />
      )}
    </div>
  );
};

export default TelegramPublishDialog;
