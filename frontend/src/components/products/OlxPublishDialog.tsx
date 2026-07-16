import React from 'react';
import { CheckOutlined, CloseOutlined, LinkOutlined, ShoppingOutlined, WarningOutlined } from '@ant-design/icons';

export interface OlxProductStatus {
  productnumber: string;
  typename?: string | null;
  gendername?: string | null;
  category_id?: number | null;
  authorized: boolean;
  on_olx: boolean;
  olx_status?: string | null;
  olx_url?: string | null;
  olx_id?: number | null;
  needs_package?: boolean;
  created_by_bms?: boolean;
  last_error?: string | null;
  image_count?: number;
  packet_unit?: number | null;
  pricing?: {
    base_price: number;
    target_markup_pct: number;
    target_net: number;
    packet_unit: number;
    ad_spend: number;
    use_delivery: boolean;
    delivery_commission: number;
    delivery_commission_pct: number;
    effective_price: number;
    current_olx_price?: number | null;
    price_will_change?: boolean;
    net: number;
    margin: number;
    margin_pct: number;
    total_platform_cost: number;
    margin_safe: boolean;
  } | null;
  config?: {
    ad_spend?: number;
    advertiser_type?: string;
    use_delivery?: boolean;
    branch_payment?: boolean;
  };
  warnings?: string[];
}

interface Props {
  data: OlxProductStatus;
  busy: boolean;
  onClose: () => void;
  onPublish: () => void;
  onSaveConfig: (patch: Record<string, any>) => void;
}

const money = (v: number | null | undefined) =>
  new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 2 }).format(Number(v || 0));

const OlxPublishDialog: React.FC<Props> = ({ data, busy, onClose, onPublish, onSaveConfig }) => {
  const pricing = data.pricing;
  const live = data.on_olx;
  const cfg = data.config || {};
  const canPublish = data.authorized && !!data.category_id && (data.image_count ?? 0) > 0 && !!pricing?.margin_safe;

  return (
    <div className="bms-dialog-host fixed inset-0 z-[110] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={busy ? undefined : onClose} />
      <div className="relative w-full max-w-xl max-h-[88vh] flex flex-col rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-2xl overflow-hidden bms-fade-in">
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <span className="px-2 h-9 rounded-xl flex items-center justify-center bg-[#002f34] text-[#a9e000] font-black shrink-0 text-sm">OLX</span>
          <div className="min-w-0 flex-1">
            <div className="text-base font-semibold text-gray-900 dark:text-gray-50">OLX</div>
            <div className="text-xs text-gray-400 mt-0.5">{data.productnumber} · {data.typename || 'товар'}</div>
          </div>
          <span className={`px-2 py-1 rounded-md text-[11px] font-semibold border ${
            live
              ? 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800'
              : data.needs_package
                ? 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-800'
                : 'bg-gray-50 text-gray-500 border-gray-200 dark:bg-gray-800 dark:border-gray-700'
          }`}>{live ? 'Опубліковано' : data.needs_package ? 'Потрібен пакет' : 'Не опубліковано'}</span>
          <button onClick={busy ? undefined : onClose} className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400" aria-label="Закрити">
            <CloseOutlined />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {!data.authorized && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
              OLX ще не авторизовано. Пройдіть одноразову авторизацію в розділі «Публікації».
            </div>
          )}

          {pricing && (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 px-4 py-3 dark:border-emerald-800 dark:bg-emerald-900/15">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300">OLX Price Engine</div>
                  <div className="mt-0.5 text-xl font-bold text-gray-900 dark:text-gray-50">{money(pricing.effective_price)} грн</div>
                  <div className="text-[11px] text-gray-500 dark:text-gray-400">
                    ціна покриває пакет + рекламу + комісію · цільова націнка {pricing.target_markup_pct}%
                  </div>
                </div>
                <div className="max-w-[210px] rounded-lg border border-emerald-200 bg-white px-2.5 py-1.5 text-right dark:border-emerald-800 dark:bg-gray-800">
                  <div className="text-[10px] uppercase tracking-wide text-gray-400">Витрати платформи</div>
                  <div className="mt-0.5 text-xs font-semibold text-gray-700 dark:text-gray-200">−{money(pricing.total_platform_cost)} грн</div>
                  <div className="text-[10px] text-gray-400">пакет+реклама+комісія</div>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Metric label="Пакет (1 публ.)" value={`−${money(pricing.packet_unit)} грн`} />
                <Metric label="Реклама" value={`−${money(pricing.ad_spend)} грн`} />
                <Metric label={pricing.use_delivery ? `Комісія ${pricing.delivery_commission_pct}%` : 'Комісія'} value={pricing.use_delivery ? `−${money(pricing.delivery_commission)} грн` : '—'} />
                <Metric label="Маржа" value={`+${money(pricing.margin)} грн · ${pricing.margin_pct}%`} positive={pricing.margin_safe} />
              </div>
              <div className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">
                Чистими після всіх витрат: <strong>{money(pricing.net)} грн</strong> (собівартість {money(pricing.base_price)} грн).
                {pricing.price_will_change && data.on_olx && <> · Оновлення змінить поточну ціну OLX з {money(pricing.current_olx_price)} до {money(pricing.effective_price)} грн.</>}
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Info label="Авторизація" value={data.authorized ? 'OK' : 'Немає'} ok={data.authorized} />
            <Info label="Категорія OLX" value={data.category_id ? String(data.category_id) : 'Не визначено'} ok={!!data.category_id} />
            <Info label="Фото" value={String(data.image_count ?? 0)} ok={(data.image_count ?? 0) > 0} />
            <Info label="Пакет/шт" value={data.packet_unit ? `${money(data.packet_unit)} грн` : '—'} ok={!!data.packet_unit} />
          </div>

          <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-xs dark:border-gray-700 dark:bg-gray-800/50">
            <div className="flex items-center justify-between gap-3">
              <span className="text-gray-700 dark:text-gray-200">Враховувати комісію OLX Доставки у ціні</span>
              <button
                onClick={() => onSaveConfig({ use_delivery: !cfg.use_delivery })}
                disabled={busy}
                className={`px-2 py-1 rounded-md text-[11px] font-semibold border ${cfg.use_delivery ? 'bg-emerald-600 text-white border-emerald-700' : 'bg-gray-100 text-gray-500 border-gray-300 dark:bg-gray-700 dark:text-gray-300'}`}
              >{cfg.use_delivery ? 'Так' : 'Ні'}</button>
            </div>
            <label className="mt-3 flex items-center justify-between gap-3">
              <span className="text-gray-700 dark:text-gray-200">Середні витрати на рекламу/шт (грн)</span>
              <input
                type="number" min={0} defaultValue={cfg.ad_spend ?? 0} disabled={busy}
                onBlur={(e) => { const v = Number(e.target.value); if (v !== (cfg.ad_spend ?? 0)) onSaveConfig({ ad_spend: v }); }}
                className="w-24 px-2 py-1 rounded-md text-sm border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-right"
              />
            </label>
          </div>

          {!!data.warnings?.length && (
            <div className="space-y-1.5">
              {data.warnings.map((w, i) => (
                <div key={i} className="flex items-start gap-2 rounded-lg border border-amber-100 bg-amber-50/70 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-900/15 dark:text-amber-300">
                  <WarningOutlined className="mt-0.5 shrink-0" /><span>{w}</span>
                </div>
              ))}
            </div>
          )}

          {data.needs_package && (
            <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200">
              Оголошення створене, але <strong>не активоване</strong>: OLX вимагає активний пакет публікацій.
              Купіть/активуйте пакет у кабінеті OLX — після цього воно стане видимим.
            </div>
          )}
          {data.last_error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">
              {data.last_error}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 px-5 py-3.5 border-t border-gray-100 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-800/30">
          <button onClick={onClose} disabled={busy} className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 disabled:opacity-60">Закрити</button>
          {data.olx_url && (
            <a href={data.olx_url} target="_blank" rel="noreferrer" className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 no-underline"><LinkOutlined /> Відкрити на OLX</a>
          )}
          <button onClick={onPublish} disabled={busy || !canPublish} className="olx-btn">
            <ShoppingOutlined /> {live ? 'Оновити на OLX' : 'Опублікувати на OLX'}{pricing?.effective_price ? ` · ${money(pricing.effective_price)} грн` : ''}
          </button>
        </div>
      </div>
      <style>{`.olx-btn{display:inline-flex;align-items:center;gap:.4rem;padding:.5rem 1rem;border-radius:.5rem;background:#002f34;color:#a9e000;font-size:.875rem;font-weight:700;border:0;cursor:pointer}.olx-btn:disabled{opacity:.5;cursor:not-allowed}.olx-btn:hover:not(:disabled){filter:brightness(1.12)}`}</style>
    </div>
  );
};

const Info: React.FC<{ label: string; value: string; ok: boolean }> = ({ label, value, ok }) => (
  <div className="rounded-lg border border-gray-100 bg-gray-50 px-2.5 py-2 dark:border-gray-800 dark:bg-gray-800/50">
    <div className="text-[10px] uppercase tracking-wide text-gray-400">{label}</div>
    <div className={`mt-0.5 text-xs font-semibold ${ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-600 dark:text-gray-300'}`}>{value}</div>
  </div>
);

const Metric: React.FC<{ label: string; value: string; positive?: boolean }> = ({ label, value, positive }) => (
  <div className="rounded-lg border border-emerald-100 bg-white/80 px-2.5 py-2 dark:border-emerald-900 dark:bg-gray-800/60">
    <div className="text-[10px] uppercase tracking-wide text-gray-400">{label}</div>
    <div className={`mt-0.5 text-xs font-semibold ${positive ? 'text-emerald-700 dark:text-emerald-300' : 'text-gray-700 dark:text-gray-200'}`}>{value}</div>
  </div>
);

export default OlxPublishDialog;
