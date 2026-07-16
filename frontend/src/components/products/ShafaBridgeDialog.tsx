import React, { useEffect, useState } from 'react';
import { CheckOutlined, CloseOutlined, LinkOutlined, ShoppingOutlined, WarningOutlined } from '@ant-design/icons';

export interface ShafaProductStatus {
  productnumber: string;
  state: 'not_requested' | 'waiting_prom' | 'bridge_ready' | 'confirmed' | 'manual_existing' | 'removed';
  bridge_enabled: boolean;
  on_prom: boolean;
  prom_status?: string | null;
  prom_presence?: string | null;
  prom_price?: number | null;
  prom_last_synced_at?: string | null;
  pricing?: {
    base_price: number;
    target_markup_pct: number;
    target_net: number;
    prom_safe_price: number;
    shafa_safe_price: number;
    effective_price: number;
    current_prom_price?: number | null;
    price_will_change?: boolean;
    shafa_commission_pct: number;
    shafa_fee: number;
    shafa_net: number;
    shafa_margin: number;
    shafa_margin_pct: number;
    prom_net: number;
    extra_net_vs_prom: number;
    margin_safe: boolean;
    tariff_group: 'fashion_home' | 'other';
    tariff_group_label: string;
    tariff_effective_date: string;
    shafa_fee_cap: number;
  };
  verified: boolean;
  expected_since?: string | null;
  confirmation_overdue?: boolean;
  tracked?: boolean;
  on_shafa?: boolean;
  shafa_url?: string | null;
  shafa_presence?: string | null;
  shafa_checked_at?: string | null;
  gtins?: string[];
  invalid_gtins?: string[];
  variant_count?: number;
  available_qty?: number;
  warnings?: string[];
  tracked_total?: number;
  max_listings?: number;
  official_help_url?: string;
  tariffs_help_url?: string;
  prom_help_url?: string;
}

interface Props {
  data: ShafaProductStatus;
  busy: boolean;
  onClose: () => void;
  onEnableBridge: () => void;
  onDisableBridge: () => void;
  onPrepare: () => void;
  onConfirm: (url?: string) => void;
  onLinkExisting: (url?: string) => void;
  onUntrack: () => void;
  onCreateProm: () => void;
}

const stateText: Record<string, string> = {
  not_requested: 'Не передавали',
  removed: 'Не відстежується',
  waiting_prom: 'Prom обробляє',
  bridge_ready: 'Prom експортує автоматично',
  confirmed: 'Підтверджено на Shafa',
  manual_existing: 'Існувало на Shafa',
};

const ShafaBridgeDialog: React.FC<Props> = ({
  data, busy, onClose, onEnableBridge, onPrepare, onConfirm,
  onDisableBridge, onLinkExisting, onUntrack, onCreateProm,
}) => {
  const [url, setUrl] = useState(data.shafa_url || '');
  useEffect(() => setUrl(data.shafa_url || ''), [data.shafa_url, data.productnumber]);
  const state = data.state || 'not_requested';
  const verified = !!data.verified;
  const noRequest = state === 'not_requested' || state === 'removed';
  const validUrl = /^https?:\/\/([a-z0-9-]+\.)*shafa\.ua(?:\/|$)/i.test(url.trim());
  const promAvailability = data.prom_presence === 'available'
    ? 'є в наявності'
    : data.prom_presence === 'not_available'
      ? 'немає в наявності'
      : data.prom_presence || 'ще не відомо';
  const pricing = data.pricing;

  return (
    <div className="bms-dialog-host fixed inset-0 z-[110] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={busy ? undefined : onClose} />
      <div className="relative w-full max-w-xl max-h-[88vh] flex flex-col rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-2xl overflow-hidden bms-fade-in">
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <span className="w-9 h-9 rounded-xl flex items-center justify-center bg-black text-white font-black shrink-0">S</span>
          <div className="min-w-0 flex-1">
            <div className="text-base font-semibold text-gray-900 dark:text-gray-50">Shafa</div>
            <div className="text-xs text-gray-400 mt-0.5">{data.productnumber} · офіційний міст через Prom</div>
          </div>
          <span className={`px-2 py-1 rounded-md text-[11px] font-semibold border ${
            verified
              ? 'bg-violet-50 text-violet-700 border-violet-200 dark:bg-violet-900/30 dark:text-violet-300 dark:border-violet-800'
              : state === 'bridge_ready' || state === 'waiting_prom'
                // Спокійний (не тривожний) тон: це нормальний робочий автоекспорт,
                // а не завдання для власника.
                ? 'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-900/20 dark:text-sky-300 dark:border-sky-800'
                : 'bg-gray-50 text-gray-500 border-gray-200 dark:bg-gray-800 dark:border-gray-700'
          }`}>{stateText[state] || state}</span>
          <button onClick={busy ? undefined : onClose} className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400" aria-label="Закрити">
            <CloseOutlined />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          <div className="rounded-xl border border-violet-100 bg-violet-50/70 px-4 py-3 text-xs leading-relaxed text-violet-900 dark:border-violet-900 dark:bg-violet-900/15 dark:text-violet-200">
            <strong>Це не пряма публікація.</strong> Shafa не надає BMS API продавця.
            BMS керує товаром у Prom, а глобальний міст Prom→Shafa має перенести назву, опис,
            ціну й наявність. BMS не може побачити результат без посилання на оголошення Shafa.
          </div>

          {!data.bridge_enabled && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-800 dark:bg-amber-900/20">
              <div className="text-sm font-semibold text-amber-900 dark:text-amber-200">Спершу увімкни міст у Prom</div>
              <ol className="mt-1.5 ml-4 list-decimal text-xs leading-relaxed text-amber-800 dark:text-amber-300">
                <li>Prom: Маркет → Всі додатки → «Експорт товарів на Shafa.ua».</li>
                <li>Введи телефон Shafa та код підтвердження.</li>
                <li>Після цього підтвердь нижче, що міст справді увімкнено.</li>
              </ol>
              <div className="mt-2 text-xs font-medium text-amber-900 dark:text-amber-200">
                Це глобальна дія: міст забере всі доступні й сумісні товари Prom, а не лише цю картку.
              </div>
              <div className="mt-2 flex gap-3 text-xs">
                <a href={data.prom_help_url} target="_blank" rel="noreferrer" className="text-violet-700 dark:text-violet-300 hover:underline">Інструкція Prom</a>
                <a href={data.official_help_url} target="_blank" rel="noreferrer" className="text-violet-700 dark:text-violet-300 hover:underline">Умови Shafa</a>
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Info label="Prom" value={data.on_prom ? `Так${data.prom_status ? ` · ${data.prom_status}` : ''}` : 'Немає'} ok={data.on_prom} />
            <Info label="Наявність" value={String(data.available_qty ?? 0)} ok={(data.available_qty ?? 0) > 0} />
            <Info label="GTIN" value={data.gtins?.length ? `${data.gtins.length} валідн.` : 'Не задано'} ok={!!data.gtins?.length} />
            <Info label="Ліміт" value={`${data.tracked_total ?? 0}/${data.max_listings ?? 10000}`} ok={(data.tracked_total ?? 0) < (data.max_listings ?? 10000)} />
          </div>

          {pricing && (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 px-4 py-3 dark:border-emerald-800 dark:bg-emerald-900/15">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300">Shafa Price Engine</div>
                  <div className="mt-0.5 text-xl font-bold text-gray-900 dark:text-gray-50">
                    {money(pricing.effective_price)} грн
                  </div>
                  <div className="text-[11px] text-gray-500 dark:text-gray-400">
                    єдина безпечна ціна Prom→Shafa · цільова націнка {pricing.target_markup_pct}%
                  </div>
                </div>
                <div className="max-w-[210px] rounded-lg border border-emerald-200 bg-white px-2.5 py-1.5 text-right dark:border-emerald-800 dark:bg-gray-800">
                  <div className="text-[10px] uppercase tracking-wide text-gray-400">Тарифна група Shafa</div>
                  <div className="mt-0.5 text-xs font-semibold text-gray-700 dark:text-gray-200">{pricing.tariff_group_label}</div>
                  <div className="text-[10px] text-gray-400">чинна сітка від 30.06.2026</div>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
                <PriceMetric label={`Комісія ${pricing.shafa_commission_pct}%`} value={`−${money(pricing.shafa_fee)} грн`} />
                <PriceMetric label="Чистими Shafa" value={`${money(pricing.shafa_net)} грн`} positive />
                <PriceMetric label="Маржа" value={`+${money(pricing.shafa_margin)} грн · ${pricing.shafa_margin_pct}%`} positive={pricing.margin_safe} />
                <PriceMetric label="Більше ніж Prom" value={`+${money(pricing.extra_net_vs_prom)} грн`} positive />
              </div>
              {pricing.price_will_change && (
                <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
                  One-click оновить поточну ціну Prom з {money(pricing.current_prom_price)} до {money(pricing.effective_price)} грн,
                  лише якщо штатна Prom-ціна або мінімум Shafa вищі. Shafa отримає ту саму ціну автоматично.
                </div>
              )}
              <div className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">
                Мінімум лише для Shafa: {money(pricing.shafa_safe_price)} грн · штатна ціна Prom: {money(pricing.prom_safe_price)} грн.
                Наявна вища Prom-ціна не знижується; інакше застосовується більша з цих двох. Максимальна комісія Shafa — {money(pricing.shafa_fee_cap)} грн.
                {data.tariffs_help_url && <> · <a href={data.tariffs_help_url} target="_blank" rel="noreferrer" className="text-emerald-700 dark:text-emerald-300 hover:underline">чинні тарифи</a></>}
              </div>
            </div>
          )}

          <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-xs dark:border-gray-700 dark:bg-gray-800/50">
            <div className="font-semibold text-gray-800 dark:text-gray-100">Як синхронізується залишок</div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-gray-600 dark:text-gray-300">
              <FlowStep title="BMS база" value={`${data.available_qty ?? 0} шт.`} ok={(data.available_qty ?? 0) > 0} />
              <span aria-hidden="true">→</span>
              <FlowStep title="Prom" value={promAvailability} ok={data.prom_presence === 'available'} />
              <span aria-hidden="true">→</span>
              <FlowStep title="Shafa" value="автоматично з Prom" ok={data.bridge_enabled && data.prom_presence === 'available'} />
            </div>
            <div className="mt-2 text-rose-700 dark:text-rose-300">
              Зворотного каналу немає: замовлення або продаж на Shafa автоматично не списує товар у BMS.
              Його треба зафіксувати у звичному журналі/BMS, після чого BMS передасть новий залишок у Prom, а Prom — у Shafa.
            </div>
          </div>

          {data.bridge_enabled && !verified && (
            <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900 dark:border-sky-800 dark:bg-sky-900/20 dark:text-sky-200">
              <strong>Дій не потрібно.</strong> Prom сам експортує цей товар на Shafa (перший експорт не миттєвий).
              Якщо хочеш, щоб BMS показала «Підтверджено» та сама стежила за наявністю — за бажанням встав нижче
              посилання на оголошення Shafa. BMS перечитає його публічно й далі триматиме статус автоматично.
            </div>
          )}
          {verified && data.shafa_presence && (
            <div className={`rounded-lg border px-3 py-2 text-xs ${
              data.shafa_presence === 'available'
                ? 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300'
                : 'border-gray-200 bg-gray-50 text-gray-600 dark:border-gray-700 dark:bg-gray-800/50 dark:text-gray-300'
            }`}>
              Стан на Shafa (перевірено публічно): <strong>{data.shafa_presence === 'available' ? 'у наявності' : 'не в наявності / знято'}</strong>
              {data.shafa_checked_at ? ` · ${new Date(data.shafa_checked_at).toLocaleString('uk-UA')}` : ''}
            </div>
          )}

          {!!data.gtins?.length && (
            <div className="text-xs text-gray-500 dark:text-gray-400">
              GTIN у фід Prom: <span className="font-mono">{data.gtins.join(', ')}</span>
              {(data.variant_count || 1) > 1 && <span> · окремо для кожного розміру ростовки</span>}
            </div>
          )}

          {!!data.warnings?.length && (
            <div className="space-y-1.5">
              {data.warnings.map((warning, index) => (
                <div key={index} className="flex items-start gap-2 rounded-lg border border-amber-100 bg-amber-50/70 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-900/15 dark:text-amber-300">
                  <WarningOutlined className="mt-0.5 shrink-0" /><span>{warning}</span>
                </div>
              ))}
            </div>
          )}

          {(state === 'bridge_ready' || verified || noRequest) && (
            <div>
              <label className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">Посилання фактичного оголошення Shafa</label>
              <input
                value={url}
                onChange={e => setUrl(e.target.value)}
                placeholder="Обов'язково для підтвердження: https://shafa.ua/uk/..."
                disabled={busy}
                className="mt-1 w-full px-3 py-2 rounded-lg text-sm border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-violet-500/30"
              />
            </div>
          )}

          {state === 'manual_existing' && !data.on_prom && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">
              «Зворотний міст» не має API: BMS може створити Prom-джерело зі своєї картки, але глобальний
              експорт може додати друге оголошення на Shafa. Перед дією перевір спосіб об'єднання з підтримкою Shafa.
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-3.5 border-t border-gray-100 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-800/30">
          <div className="flex items-center gap-2">
            {data.bridge_enabled && (
              <button onClick={onDisableBridge} disabled={busy}
                className="px-3 py-2 rounded-lg text-xs font-medium text-gray-400 hover:text-rose-600 disabled:opacity-60">
                Позначити міст вимкненим
              </button>
            )}
            {data.bridge_enabled && noRequest && (
              <button onClick={() => onLinkExisting(url || undefined)} disabled={busy || !validUrl}
                className="px-3 py-2 rounded-lg text-xs font-medium border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-white dark:hover:bg-gray-800 disabled:opacity-60">
                <LinkOutlined /> Прив'язати URL
              </button>
            )}
            {state === 'manual_existing' && !data.on_prom && (
              <button onClick={onCreateProm} disabled={busy}
                className="px-3 py-2 rounded-lg text-xs font-medium border border-rose-200 text-rose-700 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-300 disabled:opacity-60">
                Створити на Prom
              </button>
            )}
            {data.state !== 'not_requested' && data.state !== 'removed' && (
              <button onClick={onUntrack} disabled={busy}
                className="px-3 py-2 rounded-lg text-xs font-medium text-gray-400 hover:text-rose-600 disabled:opacity-60">
                Не відстежувати
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <button onClick={onClose} disabled={busy} className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 disabled:opacity-60">Закрити</button>
            {!data.bridge_enabled ? (
              <button onClick={onEnableBridge} disabled={busy} className="primary-btn">
                <CheckOutlined /> Я вже підключив міст у Prom
              </button>
            ) : noRequest ? (
              <button onClick={onPrepare} disabled={busy || (data.available_qty ?? 0) <= 0} className="primary-btn">
                <ShoppingOutlined /> Опублікувати автоматично{pricing?.effective_price ? ` · ${money(pricing.effective_price)} грн` : ''}
              </button>
            ) : state === 'waiting_prom' && !data.on_prom ? (
              <button disabled className="primary-btn"><ShoppingOutlined /> Prom обробляє товар…</button>
            ) : state === 'waiting_prom' ? (
              <button disabled className="primary-btn"><ShoppingOutlined /> Очікуємо Prom…</button>
            ) : state === 'bridge_ready' ? (
              <>
                {/* Нічого робити не треба — Prom експортує сам. Прив'язка URL
                    лишається необов'язковою опцією, а не вимогою. */}
                <a href="https://shafa.ua/uk/my/clothes" target="_blank" rel="noreferrer"
                  className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 no-underline">
                  Перевірити на Shafa
                </a>
                {validUrl && (
                  <button onClick={() => onConfirm(url || undefined)} disabled={busy}
                    className="px-3 py-2 rounded-lg text-xs font-medium border border-emerald-200 text-emerald-700 dark:border-emerald-800 dark:text-emerald-300">
                    <CheckOutlined /> Прив'язати це оголошення
                  </button>
                )}
                <button onClick={onClose} disabled={busy} className="primary-btn">
                  <CheckOutlined /> Готово
                </button>
              </>
            ) : verified && data.shafa_url ? (
              <>
                <button onClick={onPrepare} disabled={busy} className="px-3 py-2 rounded-lg text-xs font-medium border border-emerald-200 text-emerald-700 dark:border-emerald-800 dark:text-emerald-300">
                  Синхронізувати ціну й залишок
                </button>
                <a href={data.shafa_url} target="_blank" rel="noreferrer" className="primary-btn no-underline"><LinkOutlined /> Відкрити Shafa</a>
              </>
            ) : null}
          </div>
        </div>
      </div>
      <style>{`.primary-btn{display:inline-flex;align-items:center;gap:.4rem;padding:.5rem 1rem;border-radius:.5rem;background:#7c3aed;color:white;font-size:.875rem;font-weight:600;border:0;cursor:pointer}.primary-btn:disabled{opacity:.55;cursor:not-allowed}.primary-btn:hover:not(:disabled){filter:brightness(1.08)}`}</style>
    </div>
  );
};

const Info: React.FC<{ label: string; value: string; ok: boolean }> = ({ label, value, ok }) => (
  <div className="rounded-lg border border-gray-100 bg-gray-50 px-2.5 py-2 dark:border-gray-800 dark:bg-gray-800/50">
    <div className="text-[10px] uppercase tracking-wide text-gray-400">{label}</div>
    <div className={`mt-0.5 text-xs font-semibold ${ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-600 dark:text-gray-300'}`}>{value}</div>
  </div>
);

const FlowStep: React.FC<{ title: string; value: string; ok: boolean }> = ({ title, value, ok }) => (
  <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 ${
    ok
      ? 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300'
      : 'border-gray-200 bg-white text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300'
  }`}>
    <strong>{title}:</strong> {value}
  </span>
);

const money = (value: number | null | undefined) => {
  const n = Number(value || 0);
  return new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 2 }).format(n);
};

const PriceMetric: React.FC<{ label: string; value: string; positive?: boolean }> = ({ label, value, positive }) => (
  <div className="rounded-lg border border-emerald-100 bg-white/80 px-2.5 py-2 dark:border-emerald-900 dark:bg-gray-800/60">
    <div className="text-[10px] uppercase tracking-wide text-gray-400">{label}</div>
    <div className={`mt-0.5 text-xs font-semibold ${positive ? 'text-emerald-700 dark:text-emerald-300' : 'text-gray-700 dark:text-gray-200'}`}>{value}</div>
  </div>
);

export default ShafaBridgeDialog;
