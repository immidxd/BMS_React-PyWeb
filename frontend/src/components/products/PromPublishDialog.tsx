import React, { useMemo, useState } from 'react';
import { CloseOutlined, ShoppingOutlined, WarningOutlined, PlusOutlined, DeleteOutlined, LockOutlined } from '@ant-design/icons';

/*
 * Діалог публікації товару на Prom — замість системного window.confirm.
 * Мінімалістичний, у стилі програми. Перед підтвердженням можна підкоригувати:
 * назви (укр/рос), ціну (з живим перерахунком «чистими» після комісій Prom),
 * характеристики (селект зі словника Prom або вільний текст, + додати/прибрати).
 * Розмірні атрибути (Розмір/Довжина устілки) — завжди АВТО (свої на кожен розмір
 * ростовки), показані як заблоковані.
 */

interface ParamRow { n: string; v: string; }

interface Props {
  data: any;                       // відповідь preview з /prom/export-product
  busy: boolean;
  onCancel: () => void;
  onConfirm: (overrides: { name_ua?: string; name_ru?: string; price?: number; params: [string, string][] }) => void;
}

const INPUT_CLS = 'w-full px-2.5 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-violet-500/40 focus:border-violet-400 transition-colors disabled:opacity-60 disabled:bg-gray-50 dark:disabled:bg-gray-800/50';
const LABEL_CLS = 'text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500';

const PromPublishDialog: React.FC<Props> = ({ data, busy, onCancel, onConfirm }) => {
  const rostovka = (data.sizes_count || 1) > 1;
  const autoSet = new Set<string>(data.auto_params || []);
  const opts: Record<string, string[]> = data.param_options || {};
  const optKeys = Object.keys(opts);

  const [nameUa, setNameUa] = useState<string>(data.name || '');
  const [nameRu, setNameRu] = useState<string>(data.name_ru || '');
  const [price, setPrice] = useState<string>(String(data.price_prom || ''));
  const initial: ParamRow[] = (data.params || []).map((x: any[]) => ({ n: String(x[0]), v: String(x[1]) }));
  const [params, setParams] = useState<ParamRow[]>(initial);

  // Живий розрахунок «чистими»: ціна × (1 − комісія) − післяплата
  const c = (data.commission_pct ?? 18) / 100;
  const fee = data.postpay_fee ?? 60;
  const net = useMemo(() => {
    const p = parseFloat(price);
    return isFinite(p) && p > 0 ? Math.round(p * (1 - c) - fee) : null;
  }, [price, c, fee]);
  const netLow = net !== null && data.price_base > 0 && net < data.price_base * 1.05;

  const warns: string[] = [];
  if (data.image_kind === 'real') warns.push('Немає офіційних фото — публікація піде з РЕАЛЬНИМИ фото.');
  if (data.condition_warn) warns.push(`Стан «${data.condition}» → на Prom як «${data.condition_prom}».`);

  const setP = (i: number, patch: Partial<ParamRow>) =>
    setParams(ps => ps.map((p, j) => (j === i ? { ...p, ...patch } : p)));

  const submit = () => {
    const edited = params
      .filter(p => p.n.trim() && p.v.trim() && !autoSet.has(p.n.trim()))
      .map(p => [p.n.trim(), p.v.trim()] as [string, string]);
    onConfirm({
      name_ua: rostovka ? undefined : nameUa.trim() || undefined,
      name_ru: rostovka ? undefined : nameRu.trim() || undefined,
      price: parseFloat(price) > 0 ? parseFloat(price) : undefined,
      params: edited,
    });
  };

  return (
    <div className="bms-dialog-host fixed inset-0 z-[100] flex items-center justify-center p-4">
      {/* Оверлей */}
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={busy ? undefined : onCancel} />

      {/* Картка */}
      <div className="relative w-full max-w-2xl max-h-[88vh] flex flex-col rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-2xl overflow-hidden bms-fade-in">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <span className="w-9 h-9 rounded-xl flex items-center justify-center text-white shrink-0" style={{ backgroundColor: '#5B2D8E' }}>
            <ShoppingOutlined style={{ fontSize: 17 }} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-base font-semibold text-gray-900 dark:text-gray-50 leading-tight">
              {data.already_on_prom ? 'Перезаписати на Prom' : 'Публікація на Prom'}
            </div>
            <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
              {data.sku} · публікується одразу живим, видимим покупцям
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {data.kids && (
              <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-sky-50 text-sky-700 border border-sky-200 dark:bg-sky-900/30 dark:text-sky-300 dark:border-sky-800">
                Дитяче
              </span>
            )}
            {rostovka && (
              <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-violet-50 text-violet-700 border border-violet-200 dark:bg-violet-900/30 dark:text-violet-300 dark:border-violet-800">
                Ростовка: {data.sizes_count} лістингів
              </span>
            )}
            <button onClick={busy ? undefined : onCancel} className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors" aria-label="Закрити">
              <CloseOutlined className="text-sm" />
            </button>
          </div>
        </div>

        {/* Попередження */}
        {warns.length > 0 && (
          <div className="px-5 pt-3 space-y-1.5">
            {warns.map((w, i) => (
              <div key={i} className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs bg-amber-50 text-amber-800 border border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-800">
                <WarningOutlined className="mt-0.5 shrink-0" />
                <span>{w}</span>
              </div>
            ))}
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Назви */}
          <div className="grid grid-cols-1 gap-2.5">
            <div>
              <div className={LABEL_CLS}>Назва (українська)</div>
              <input className={`${INPUT_CLS} mt-1`} value={nameUa} onChange={e => setNameUa(e.target.value)} disabled={rostovka || busy} />
            </div>
            <div>
              <div className={LABEL_CLS}>Назва (російська)</div>
              <input className={`${INPUT_CLS} mt-1`} value={nameRu} onChange={e => setNameRu(e.target.value)} disabled={rostovka || busy} />
            </div>
            {rostovka && (
              <div className="text-[11px] text-gray-400 dark:text-gray-500 -mt-1">
                Ростовка: назва й розмір генеруються автоматично на кожен розмір.
              </div>
            )}
          </div>

          {/* Ціна */}
          <div className="flex items-start gap-4">
            <div className="w-40 shrink-0">
              <div className={LABEL_CLS}>Ціна на Prom, грн</div>
              <input type="number" min={1} className={`${INPUT_CLS} mt-1 font-semibold`} value={price} onChange={e => setPrice(e.target.value)} disabled={busy} />
            </div>
            <div className="text-xs text-gray-500 dark:text-gray-400 pt-5 leading-relaxed">
              База: <b>{data.price_base}</b> грн ·
              чистими після комісій ≈ <b className={netLow ? 'text-red-500' : 'text-emerald-600 dark:text-emerald-400'}>{net ?? '—'}</b> грн
              <span className="text-gray-400 dark:text-gray-500"> (комісія {data.commission_pct}% + {fee} грн післяплата)</span>
              {netLow && <div className="text-red-500 font-medium mt-0.5">⚠ Чистими майже без націнки — перевір ціну.</div>}
            </div>
          </div>

          {/* Фото */}
          <div className="text-xs text-gray-500 dark:text-gray-400">
            Фото: <b>{data.image_count}</b> {data.image_kind === 'official' ? '(офіційні)' : data.image_kind === 'real' ? '(реальні)' : ''}
            <span className="text-gray-400 dark:text-gray-500"> · опис і пошукові теги (укр+рос) генеруються автоматично</span>
          </div>

          {/* Характеристики */}
          <div>
            <div className="flex items-center justify-between mb-0.5">
              <div className={LABEL_CLS}>Характеристики</div>
              <button
                onClick={() => setParams(ps => [...ps, { n: '', v: '' }])}
                disabled={busy}
                className="flex items-center gap-1 text-[11px] font-medium text-violet-600 dark:text-violet-400 hover:text-violet-800 dark:hover:text-violet-300 transition-colors"
              >
                <PlusOutlined style={{ fontSize: 10 }} /> Додати
              </button>
            </div>
            <div className="text-[11px] text-gray-400 dark:text-gray-500 mb-2 leading-snug">
              Технічні назви — російською (шаблон фільтрів Prom). На сайті Prom покупець бачить їх
              двомовно автоматично. «Додати» — власна характеристика (будь-яка назва й значення).
            </div>
            <div className="space-y-1.5">
              {params.map((p, i) => {
                const auto = autoSet.has(p.n);
                const optIdx = optKeys.indexOf(p.n);
                return (
                  <div key={i} className="flex items-center gap-1.5">
                    <input
                      className={`${INPUT_CLS} !w-44 shrink-0 text-xs`}
                      value={p.n}
                      onChange={e => setP(i, { n: e.target.value })}
                      disabled={auto || busy}
                      list="prom-param-names"
                      placeholder="Характеристика"
                    />
                    <input
                      className={`${INPUT_CLS} text-xs`}
                      value={p.v}
                      onChange={e => setP(i, { v: e.target.value })}
                      disabled={auto || busy}
                      list={optIdx >= 0 ? `ppopt-${optIdx}` : undefined}
                      placeholder="Значення"
                    />
                    {auto ? (
                      <span className="w-7 flex justify-center text-gray-300 dark:text-gray-600" title="Авто: підставляється на кожен розмір">
                        <LockOutlined style={{ fontSize: 12 }} />
                      </span>
                    ) : (
                      <button
                        onClick={() => setParams(ps => ps.filter((_, j) => j !== i))}
                        disabled={busy}
                        className="w-7 flex justify-center text-gray-300 hover:text-red-500 dark:text-gray-600 dark:hover:text-red-400 transition-colors"
                        title="Прибрати характеристику"
                      >
                        <DeleteOutlined style={{ fontSize: 12 }} />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
            {/* Словники для підказок: назви характеристик + значення кожного словника */}
            <datalist id="prom-param-names">
              {optKeys.map(k => <option key={k} value={k} />)}
            </datalist>
            {optKeys.map((k, i) => (
              <datalist key={k} id={`ppopt-${i}`}>
                {(opts[k] || []).map(v => <option key={v} value={v} />)}
              </datalist>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-3.5 border-t border-gray-100 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-800/30">
          <button
            onClick={onCancel}
            disabled={busy}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-700 text-gray-600 hover:text-gray-900 hover:bg-gray-50 dark:text-gray-300 dark:hover:text-gray-100 dark:hover:bg-gray-800 transition-colors duration-150 disabled:opacity-60"
          >
            Скасувати
          </button>
          <button
            onClick={submit}
            disabled={busy}
            className="px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors duration-150 flex items-center gap-1.5 disabled:opacity-60 hover:brightness-110"
            style={{ backgroundColor: '#5B2D8E' }}
          >
            <ShoppingOutlined style={{ fontSize: 14 }} />
            {busy ? 'Публікація…' : data.already_on_prom ? 'Перезаписати' : 'Опублікувати'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default PromPublishDialog;
