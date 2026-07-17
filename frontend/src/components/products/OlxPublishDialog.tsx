import React, { useMemo, useState } from 'react';
import { CloseOutlined, ShoppingOutlined, WarningOutlined, LinkOutlined } from '@ant-design/icons';

/*
 * Діалог публікації товару на OLX — той самий сенс і стиль, що й у Prom:
 * перед підтвердженням можна підкоригувати заголовок, ціну (з живим
 * перерахунком «чистими» після пакета/реклами/комісії OLX Доставки), опис
 * і характеристики (селект лише з допустимих значень категорії OLX).
 * OLX бере плату за ПУБЛІКАЦІЮ (пакет), а не % з продажу — це показано явно.
 */

export interface OlxAttrOption { code: string; label: string; }
export interface OlxAttr {
  code: string;
  label: string;
  required: boolean;
  value?: string | null;
  options: OlxAttrOption[];
}

export interface OlxPreview {
  product_id: number;
  productnumber: string;
  category_id: number;
  category_name?: string | null;
  category_path?: string | null;
  title: string;
  description: string;
  price: number;
  title_max?: number;
  image_count: number;
  packet_unit?: number | null;
  already_on_olx?: boolean;
  olx_id?: number | null;
  olx_url?: string | null;
  attributes: OlxAttr[];
  warnings?: string[];
  pricing: {
    base_price: number;
    target_net: number;
    target_markup_pct: number;
    packet_unit: number;
    ad_spend: number;
    use_delivery: boolean;
    delivery_commission_pct: number;
    delivery_cap: number;
    effective_price: number;
    net: number;
    margin: number;
    margin_pct: number;
    margin_safe: boolean;
  };
}

interface Props {
  data: OlxPreview;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (overrides: {
    title?: string; description?: string; price?: number;
    attributes: { code: string; value: string }[];
  }) => void;
}

const INPUT_CLS = 'w-full px-2.5 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-emerald-500/40 focus:border-emerald-400 transition-colors disabled:opacity-60';
const LABEL_CLS = 'text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500';
const money = (v: number | null | undefined) =>
  new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 2 }).format(Number(v || 0));

const OlxPublishDialog: React.FC<Props> = ({ data, busy, onCancel, onConfirm }) => {
  const pk = data.pricing;
  const titleMax = data.title_max || 70;
  const [title, setTitle] = useState<string>(data.title || '');
  const [price, setPrice] = useState<string>(String(data.price || ''));
  const [description, setDescription] = useState<string>(data.description || '');
  const [attrs, setAttrs] = useState<Record<string, string>>(
    Object.fromEntries((data.attributes || []).map(a => [a.code, a.value || ''])),
  );

  // Живий перерахунок: ціна − комісія OLX Доставки − пакет − реклама
  const calc = useMemo(() => {
    const p = parseFloat(price);
    if (!isFinite(p) || p <= 0) return null;
    const comm = pk.use_delivery
      ? Math.min(p * (pk.delivery_commission_pct / 100) + 20, pk.delivery_cap || 499)
      : 0;
    const net = p - comm - pk.packet_unit - pk.ad_spend;
    return { comm, net, margin: net - pk.base_price, safe: net >= pk.target_net - 0.01 };
  }, [price, pk]);

  const missingRequired = (data.attributes || []).filter(a => a.required && !attrs[a.code]);
  const canSubmit = !busy && title.trim().length > 0 && (parseFloat(price) > 0)
    && description.trim().length > 0 && missingRequired.length === 0;

  const submit = () => {
    onConfirm({
      title: title.trim() || undefined,
      description: description.trim() || undefined,
      price: parseFloat(price) > 0 ? parseFloat(price) : undefined,
      attributes: Object.entries(attrs).filter(([, v]) => v).map(([code, value]) => ({ code, value })),
    });
  };

  return (
    <div className="bms-dialog-host fixed inset-0 z-[120] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={busy ? undefined : onCancel} />
      <div className="relative w-full max-w-2xl max-h-[90vh] flex flex-col rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-2xl overflow-hidden bms-fade-in">
        {/* Шапка */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <span className="px-2 h-9 rounded-xl flex items-center justify-center bg-[#002f34] text-[#a9e000] font-black shrink-0 text-sm">OLX</span>
          <div className="min-w-0 flex-1">
            <div className="text-base font-semibold text-gray-900 dark:text-gray-50">Публікація на OLX</div>
            <div className="text-xs text-gray-400 mt-0.5 truncate">
              #{data.productnumber} · {data.category_path || data.category_name || `категорія ${data.category_id}`}
            </div>
          </div>
          <button onClick={busy ? undefined : onCancel} className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400" aria-label="Закрити">
            <CloseOutlined />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Заголовок */}
          <div>
            <div className="flex items-baseline justify-between">
              <span className={LABEL_CLS}>Заголовок оголошення</span>
              <span className={`text-[10px] ${title.length > titleMax ? 'text-rose-500 font-semibold' : 'text-gray-400'}`}>
                {title.length}/{titleMax}
              </span>
            </div>
            <input className={`${INPUT_CLS} mt-1`} value={title} maxLength={titleMax}
              onChange={e => setTitle(e.target.value)} disabled={busy} />
          </div>

          {/* Ціна + живий розрахунок */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <span className={LABEL_CLS}>Ціна, грн</span>
              <input type="number" min={1} className={`${INPUT_CLS} mt-1 font-semibold`} value={price}
                onChange={e => setPrice(e.target.value)} disabled={busy} />
            </div>
            <div className="sm:col-span-2 rounded-lg border border-emerald-200 bg-emerald-50/60 px-3 py-2 dark:border-emerald-800 dark:bg-emerald-900/15">
              <div className="text-[10px] uppercase tracking-wide text-emerald-700 dark:text-emerald-300">Чистими після витрат OLX</div>
              {calc ? (
                <>
                  <div className={`text-sm font-bold ${calc.safe ? 'text-gray-900 dark:text-gray-50' : 'text-rose-600 dark:text-rose-400'}`}>
                    {money(calc.net)} грн · маржа {money(calc.margin)} грн
                  </div>
                  <div className="text-[10px] text-gray-500 dark:text-gray-400">
                    пакет −{money(pk.packet_unit)} · реклама −{money(pk.ad_spend)}
                    {pk.use_delivery ? ` · комісія ${pk.delivery_commission_pct}% −${money(calc.comm)}` : ''}
                    {' · '}собівартість {money(pk.base_price)}
                  </div>
                  {!calc.safe && (
                    <div className="text-[10px] text-rose-600 dark:text-rose-400 font-semibold mt-0.5">
                      ⚠ Нижче цільової націнки {pk.target_markup_pct}% (треба ≥ {money(pk.target_net)} грн чистими)
                    </div>
                  )}
                </>
              ) : <div className="text-sm text-gray-400">—</div>}
            </div>
          </div>

          {/* Характеристики (лише допустимі значення категорії OLX) */}
          {!!(data.attributes || []).length && (
            <div>
              <span className={LABEL_CLS}>Характеристики OLX</span>
              <div className="mt-1 grid grid-cols-1 sm:grid-cols-2 gap-2">
                {data.attributes.map(a => (
                  <label key={a.code} className="block">
                    <span className="text-[11px] text-gray-500 dark:text-gray-400">
                      {a.label}{a.required && <span className="text-rose-500"> *</span>}
                    </span>
                    {a.options.length ? (
                      <select className={`${INPUT_CLS} mt-0.5`} value={attrs[a.code] || ''} disabled={busy}
                        onChange={e => setAttrs(s => ({ ...s, [a.code]: e.target.value }))}>
                        <option value="">— не вказувати —</option>
                        {a.options.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
                      </select>
                    ) : (
                      <input className={`${INPUT_CLS} mt-0.5`} value={attrs[a.code] || ''} disabled={busy}
                        onChange={e => setAttrs(s => ({ ...s, [a.code]: e.target.value }))} />
                    )}
                  </label>
                ))}
              </div>
              {!!missingRequired.length && (
                <div className="mt-1 text-[11px] text-rose-600 dark:text-rose-400">
                  Заповніть обов'язкові: {missingRequired.map(a => a.label).join(', ')}
                </div>
              )}
            </div>
          )}

          {/* Опис */}
          <div>
            <div className="flex items-baseline justify-between">
              <span className={LABEL_CLS}>Опис (чистий текст, як бачить покупець)</span>
              <span className="text-[10px] text-gray-400">{description.length} символів</span>
            </div>
            <textarea className={`${INPUT_CLS} mt-1 font-mono text-xs leading-relaxed`} rows={12}
              value={description} onChange={e => setDescription(e.target.value)} disabled={busy} />
          </div>

          {/* Інфо-плитки */}
          <div className="grid grid-cols-3 gap-2">
            <Info label="Фото" value={String(data.image_count)} ok={data.image_count > 0} />
            <Info label="Пакет / шт" value={data.packet_unit ? `${money(data.packet_unit)} грн` : '—'} ok={!!data.packet_unit} />
            <Info label="Категорія" value={String(data.category_id)} ok />
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
        </div>

        {/* Футер */}
        <div className="flex flex-wrap items-center justify-end gap-2 px-5 py-3.5 border-t border-gray-100 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-800/30">
          {data.olx_url && (
            <a href={data.olx_url} target="_blank" rel="noreferrer"
              className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 no-underline mr-auto">
              <LinkOutlined /> Наявне оголошення
            </a>
          )}
          <button onClick={onCancel} disabled={busy} className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 disabled:opacity-60">
            Скасувати
          </button>
          <button onClick={submit} disabled={!canSubmit} className="olx-btn">
            <ShoppingOutlined /> {data.already_on_olx ? 'Опублікувати ще одне' : 'Опублікувати на OLX'}
            {parseFloat(price) > 0 ? ` · ${money(parseFloat(price))} грн` : ''}
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

export default OlxPublishDialog;
