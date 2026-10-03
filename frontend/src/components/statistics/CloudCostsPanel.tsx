import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { notify } from '../../ui/feedback';

/**
 * Статистика → «Сервери й хмара»: скільки коштує серверна частина бізнесу цього місяця —
 * по сервісах (Neon, Railway, Cloudflare, AI, ручні пункти) і по напрямах
 * (Каталог, Склад, BMS) + усе разом. Дані — кеш бекенду (services/cloud_costs.py),
 * оновлюється раз на 30 хв або кнопкою; сторінка хмарну БД не будить.
 */
type AreaKey = 'catalog' | 'warehouse' | 'bms';
interface UsageLine { label: string; value: number | null; unit: string; limit?: number }
interface ServiceRow {
  key: string; name: string; status: 'ok' | 'error' | 'not_configured';
  cost_usd: number; projected_usd: number; usage: UsageLine[];
  note?: string | null; error?: string | null; setup?: string | null; manual?: boolean;
  split: Record<AreaKey, number>; budget_usd?: number; level?: string; capped?: boolean;
}
interface AreaRow { key: AreaKey; name: string; cost_usd: number; projected_usd: number; items: { name: string; cost_usd: number }[] }
interface ManualItem { name: string; usd_month: number; area: AreaKey }
interface CostsState {
  services?: ServiceRow[]; areas?: AreaRow[]; total?: { cost_usd: number; projected_usd: number };
  allocation?: Record<string, Record<AreaKey, number>>; manual?: ManualItem[];
  area_names?: Record<AreaKey, string>; period_start?: string; period_end?: string;
  updated_at?: number; usd_uah?: number;
}

const AREA_ORDER: AreaKey[] = ['catalog', 'warehouse', 'bms'];
const AREA_COLOR: Record<AreaKey, string> = {
  catalog: 'text-indigo-700 dark:text-indigo-300', warehouse: 'text-amber-700 dark:text-amber-300', bms: 'text-emerald-700 dark:text-emerald-300',
};
const AREA_BAR: Record<AreaKey, string> = { catalog: 'bg-indigo-500', warehouse: 'bg-amber-500', bms: 'bg-emerald-500' };

const usd = (v?: number) => `$${(v ?? 0).toFixed(2)}`;
const uah = (v: number | undefined, rate?: number) => (rate ? `≈ ${Math.round((v ?? 0) * rate).toLocaleString('uk-UA')} ₴` : '');
const num = (v: number | null) => (v == null ? '—' : Number(v).toLocaleString('uk-UA', { maximumFractionDigits: 3 }));

const StatusBadge: React.FC<{ s: ServiceRow }> = ({ s }) => {
  if (s.manual) return <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600 dark:bg-gray-700 dark:text-gray-300">вручну</span>;
  if (s.status === 'not_configured') return <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600 dark:bg-gray-700 dark:text-gray-300">не підключено</span>;
  if (s.status === 'error') return <span className="rounded-full bg-red-100 px-2 py-0.5 text-[11px] text-red-700 dark:bg-red-900/30 dark:text-red-300">помилка</span>;
  if (s.capped) return <span className="rounded-full bg-red-100 px-2 py-0.5 text-[11px] text-red-700 dark:bg-red-900/30 dark:text-red-300">вимкнено бюджетом</span>;
  return <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">працює</span>;
};

const Card: React.FC<{ label: string; value: string; sub?: string; extra?: string; color?: string }> = ({ label, value, sub, extra, color }) => (
  <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4 flex flex-col">
    <span className="text-xs text-gray-500 dark:text-gray-400 font-medium uppercase tracking-wide">{label}</span>
    <span className={`text-2xl font-bold mt-1 ${color || 'text-gray-900 dark:text-white'}`}>{value}</span>
    {extra && <span className="text-xs text-gray-500 dark:text-gray-400">{extra}</span>}
    {sub && <span className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{sub}</span>}
  </div>
);

const CloudCostsPanel: React.FC = () => {
  const [st, setSt] = useState<CostsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [editAlloc, setEditAlloc] = useState(false);
  const [alloc, setAlloc] = useState<Record<string, Record<AreaKey, number>>>({});
  const [manual, setManual] = useState<ManualItem[]>([]);
  const [manualDirty, setManualDirty] = useState(false);

  const apply = useCallback((data: CostsState) => {
    setSt(data);
    setAlloc(data.allocation || {});
    setManual(data.manual || []);
    setManualDirty(false);
  }, []);

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/cloud-costs');
      const data: CostsState = await r.json();
      // Порожній кеш (перший запуск) — одразу опитуємо провайдерів.
      if (!data.services) {
        const rr = await fetch('/api/cloud-costs/refresh', { method: 'POST' });
        apply(await rr.json());
      } else apply(data);
    } catch {
      notify.error('Не вдалося завантажити витрати на сервери');
    } finally {
      setLoading(false);
    }
  }, [apply]);

  useEffect(() => { void load(); }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    try {
      const r = await fetch('/api/cloud-costs/refresh', { method: 'POST' });
      apply(await r.json());
    } catch {
      notify.error('Оновлення не вдалося');
    } finally {
      setRefreshing(false);
    }
  };

  const saveConfig = async (body: Record<string, unknown>, ok: string) => {
    try {
      const r = await fetch('/api/cloud-costs/config', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail || `HTTP ${r.status}`);
      apply(await r.json());
      notify.success(ok);
    } catch (e: any) {
      notify.error({ message: 'Не збережено', description: String(e?.message || e) });
    }
  };

  const rate = st?.usd_uah;
  const services = st?.services || [];
  const areas = useMemo(() => AREA_ORDER.map(k => (st?.areas || []).find(a => a.key === k)).filter(Boolean) as AreaRow[], [st]);
  const total = st?.total || { cost_usd: 0, projected_usd: 0 };
  const month = st?.period_start ? new Date(st.period_start).toLocaleDateString('uk-UA', { month: 'long', year: 'numeric' }) : '';
  const updated = st?.updated_at ? new Date(st.updated_at * 1000).toLocaleString('uk-UA') : '—';
  const neon = services.find(s => s.key === 'neon');

  if (loading) return <div className="h-60 flex items-center justify-center text-gray-400">Завантаження…</div>;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500 dark:text-gray-400">
        <span>Період: <b className="text-gray-700 dark:text-gray-200">{month}</b> · оновлено {updated}{rate ? ` · курс НБУ ${rate.toFixed(2)} ₴/$` : ''}</span>
        <button onClick={refresh} disabled={refreshing}
                className="rounded-lg border border-gray-200 px-3 py-1.5 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700">
          {refreshing ? 'Оновлюю…' : 'Оновити зараз'}
        </button>
      </div>

      {/* Всього + напрями */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <Card label="Усього цього місяця" value={usd(total.cost_usd)} extra={uah(total.cost_usd, rate)}
              sub={`прогноз на місяць ${usd(total.projected_usd)}`} />
        {areas.map(a => (
          <Card key={a.key} label={a.name} value={usd(a.cost_usd)} extra={uah(a.cost_usd, rate)}
                sub={`прогноз ${usd(a.projected_usd)}`} color={AREA_COLOR[a.key]} />
        ))}
      </div>

      {/* Частки напрямів */}
      {total.cost_usd > 0 && (
        <div>
          <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-gray-700">
            {areas.map(a => (
              <div key={a.key} className={AREA_BAR[a.key]} style={{ width: `${(100 * a.cost_usd) / total.cost_usd}%` }}
                   title={`${a.name}: ${usd(a.cost_usd)}`} />
            ))}
          </div>
          <div className="mt-1 flex flex-wrap gap-4 text-[11px] text-gray-500 dark:text-gray-400">
            {areas.map(a => (
              <span key={a.key} className="flex items-center gap-1.5">
                <span className={`inline-block h-2 w-2 rounded-full ${AREA_BAR[a.key]}`} />
                {a.name} {Math.round((100 * a.cost_usd) / total.cost_usd)}%
              </span>
            ))}
          </div>
        </div>
      )}

      {neon?.budget_usd != null && (
        <div className={`rounded-xl border p-3 text-xs ${neon.capped ? 'border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200'
          : 'border-gray-200 bg-gray-50 text-gray-600 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-300'}`}>
          Бюджет Neon: <b>{usd(neon.cost_usd)}</b> з <b>{usd(neon.budget_usd)}</b>. На 90% база вимикається, доки ви не дозволите більше (банер ліворуч унизу).
        </div>
      )}

      {/* Сервіси */}
      <div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500 dark:bg-gray-800 dark:text-gray-400">
            <tr>
              <th className="px-3 py-2">Сервіс</th>
              <th className="px-3 py-2">Використання</th>
              <th className="px-3 py-2 text-right">Цей місяць</th>
              <th className="px-3 py-2 text-right">Прогноз</th>
              <th className="px-3 py-2">Розподіл: {AREA_ORDER.map(k => st?.area_names?.[k] || k).join(' / ')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {services.map(s => (
              <tr key={s.key} className="align-top">
                <td className="px-3 py-2.5">
                  <div className="font-medium text-gray-900 dark:text-white">{s.name}</div>
                  <div className="mt-1"><StatusBadge s={s} /></div>
                  {s.setup && s.status === 'not_configured' && (
                    <div className="mt-1 max-w-xs text-[11px] text-gray-500 dark:text-gray-400">Підключити: {s.setup}</div>
                  )}
                  {s.error && <div className="mt-1 max-w-xs text-[11px] text-red-600 dark:text-red-400">{s.error}</div>}
                </td>
                <td className="px-3 py-2.5 text-xs text-gray-600 dark:text-gray-300">
                  {s.usage.length === 0 && !s.note && '—'}
                  {s.usage.map((u, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span className="text-gray-500 dark:text-gray-400">{u.label}:</span>
                      <span className="font-medium">{num(u.value)} {u.unit}</span>
                      {u.limit != null && u.value != null && (
                        <span className={`text-[11px] ${u.value > u.limit ? 'text-red-600' : 'text-gray-400'}`}>
                          з {Number(u.limit).toLocaleString('uk-UA')} безкошт.
                        </span>
                      )}
                    </div>
                  ))}
                  {s.note && <div className="mt-1 text-[11px] text-gray-400">{s.note}</div>}
                </td>
                <td className="px-3 py-2.5 text-right font-semibold text-gray-900 dark:text-white whitespace-nowrap">
                  {usd(s.cost_usd)}<div className="text-[11px] font-normal text-gray-400">{uah(s.cost_usd, rate)}</div>
                </td>
                <td className="px-3 py-2.5 text-right text-gray-600 dark:text-gray-300 whitespace-nowrap">{usd(s.projected_usd)}</td>
                <td className="px-3 py-2.5 text-xs">
                  {editAlloc && !s.manual ? (
                    <div className="flex gap-1">
                      {AREA_ORDER.map(k => (
                        <input key={k} type="number" min={0} max={100} step={5}
                               value={alloc[s.key]?.[k] ?? 0}
                               onChange={e => setAlloc(prev => ({ ...prev, [s.key]: { ...(prev[s.key] || { catalog: 0, warehouse: 0, bms: 0 }), [k]: Number(e.target.value) } }))}
                               className="w-14 rounded border border-gray-200 px-1 py-0.5 text-right dark:border-gray-600 dark:bg-gray-700" />
                      ))}
                    </div>
                  ) : (
                    <span className="text-gray-600 dark:text-gray-300">
                      {AREA_ORDER.map(k => `${Math.round(s.split?.[k] ?? 0)}%`).join(' / ')}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {editAlloc ? (
          <>
            <button onClick={async () => { await saveConfig({ allocation: alloc }, 'Розподіл збережено'); setEditAlloc(false); }}
                    className="rounded-lg bg-gray-900 px-3 py-1.5 font-semibold text-white dark:bg-gray-100 dark:text-gray-900">Зберегти розподіл</button>
            <button onClick={() => { setAlloc(st?.allocation || {}); setEditAlloc(false); }}
                    className="rounded-lg border border-gray-200 px-3 py-1.5 text-gray-600 dark:border-gray-600 dark:text-gray-300">Скасувати</button>
          </>
        ) : (
          <button onClick={() => setEditAlloc(true)}
                  className="rounded-lg border border-gray-200 px-3 py-1.5 text-gray-600 dark:border-gray-600 dark:text-gray-300">Змінити розподіл за напрямами</button>
        )}
        <span className="text-gray-400">Каталог і Склад працюють на одному сервері й одній базі — їхню частку задаєте ви (відсотки нормуються автоматично).</span>
      </div>

      {/* Ручні пункти */}
      <div className="rounded-xl border border-gray-200 p-4 dark:border-gray-700">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Інші фіксовані витрати (вручну)</h3>
          <button onClick={() => { setManual(m => [...m, { name: '', usd_month: 0, area: 'bms' }]); setManualDirty(true); }}
                  className="rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-600 dark:border-gray-600 dark:text-gray-300">+ Додати</button>
        </div>
        <p className="mb-2 text-[11px] text-gray-400">Домен, підписки, будь-що з фіксованою сумою на місяць — щоб «усього» було справді всім.</p>
        {manual.length === 0 && <div className="text-xs text-gray-400">Поки немає.</div>}
        <div className="space-y-1.5">
          {manual.map((m, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2 text-xs">
              <input value={m.name} placeholder="Назва" maxLength={80}
                     onChange={e => { const v = e.target.value; setManual(prev => prev.map((x, j) => (j === i ? { ...x, name: v } : x))); setManualDirty(true); }}
                     className="w-56 rounded border border-gray-200 px-2 py-1 dark:border-gray-600 dark:bg-gray-700" />
              <span className="text-gray-500">$</span>
              <input type="number" min={0} step={0.5} value={m.usd_month}
                     onChange={e => { const v = Number(e.target.value); setManual(prev => prev.map((x, j) => (j === i ? { ...x, usd_month: v } : x))); setManualDirty(true); }}
                     className="w-20 rounded border border-gray-200 px-2 py-1 text-right dark:border-gray-600 dark:bg-gray-700" />
              <span className="text-gray-500">/міс →</span>
              <select value={m.area}
                      onChange={e => { const v = e.target.value as AreaKey; setManual(prev => prev.map((x, j) => (j === i ? { ...x, area: v } : x))); setManualDirty(true); }}
                      className="rounded border border-gray-200 px-2 py-1 dark:border-gray-600 dark:bg-gray-700">
                {AREA_ORDER.map(k => <option key={k} value={k}>{st?.area_names?.[k] || k}</option>)}
              </select>
              <button onClick={() => { setManual(prev => prev.filter((_, j) => j !== i)); setManualDirty(true); }}
                      className="text-gray-400 hover:text-red-600" aria-label="Видалити">✕</button>
            </div>
          ))}
        </div>
        {manualDirty && (
          <button onClick={() => saveConfig({ manual }, 'Збережено')}
                  className="mt-3 rounded-lg bg-gray-900 px-3 py-1.5 text-xs font-semibold text-white dark:bg-gray-100 dark:text-gray-900">Зберегти</button>
        )}
      </div>
    </div>
  );
};

export default CloudCostsPanel;
