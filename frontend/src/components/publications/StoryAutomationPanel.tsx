import React, { useCallback, useEffect, useMemo, useState } from 'react';

/**
 * Регулярні Stories: ритм, критерії добору та черга на перевірку.
 *
 * Панель нічого не публікує сама. Відправлення — окрема дія у черзі, і кнопка
 * спрацьовує лише з другого натискання: Story йде в живий акаунт, і це не має
 * бути за один випадковий клік.
 */

type Platform = 'instagram' | 'facebook';

type StoryConfig = {
  platform: Platform;
  enabled: boolean;
  auto_publish: boolean;
  items_per_run: number;
  interval_hours: number;
  local_time: string;
  timezone: string;
  cooldown_days: number;
  filters: Record<string, unknown>;
  filters_label: string;
  enabled_at?: string | null;
  next_run_at?: string | null;
  last_generated_at?: string | null;
  last_error?: string | null;
  manual_review_required: boolean;
};

type StoryDraft = {
  id: number;
  platform: Platform;
  source?: string;
  status: string;
  scheduled_for?: string;
  productnumber: string;
  story_text?: string | null;
  reserves: Array<{ productnumber: string }>;
  warnings: string[];
  audit: { filters_label?: string; eligible_pool?: number };
};

type Dashboard = { configs: StoryConfig[]; drafts: StoryDraft[]; pending_count: number };

type Reference = { id: number; name?: string; brandname?: string; typename?: string; gendername?: string };

const PLATFORM_LABEL: Record<Platform, string> = { instagram: 'Instagram', facebook: 'Facebook' };
const INTERVALS = [
  { value: 6, label: 'кожні 6 годин' },
  { value: 8, label: 'кожні 8 годин' },
  { value: 12, label: 'двічі на добу' },
  { value: 24, label: 'раз на добу' },
  { value: 48, label: 'раз на 2 дні' },
  { value: 72, label: 'раз на 3 дні' },
  { value: 168, label: 'раз на тиждень' },
];
const ITEMS_PER_RUN = [1, 2, 3, 5, 7, 10];
const COOLDOWNS = [7, 14, 30, 60, 90, 180];

const fmt = (value?: string | null) =>
  value ? new Date(value).toLocaleString('uk-UA', { dateStyle: 'medium', timeStyle: 'short' }) : '—';

const refName = (row: Reference) =>
  String(row.brandname ?? row.typename ?? row.gendername ?? row.name ?? '').trim();

const StoryAutomationPanel: React.FC = () => {
  const [data, setData] = useState<Dashboard | null>(null);
  const [options, setOptions] = useState<Record<string, Reference[]>>({});
  const [seasons, setSeasons] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [armed, setArmed] = useState<number | null>(null);
  const [openFilters, setOpenFilters] = useState<Platform | null>(null);

  const load = useCallback(async () => {
    const response = await fetch('/api/publications/stories/automation');
    if (!response.ok) throw new Error('Не вдалося прочитати налаштування Stories');
    setData(await response.json());
  }, []);

  useEffect(() => {
    void load().catch(reason => setError(reason.message));
    void (async () => {
      try {
        const response = await fetch('/api/products/filters');
        if (!response.ok) return;
        const result = await response.json();
        setOptions({
          brandids: result.brands || [], typeids: result.types || [],
          genderids: result.genders || [], styleids: result.styles || [],
        });
        setSeasons(result.seasons || []);
      } catch { /* добір лишиться без підказок, панель працює далі */ }
    })();
  }, [load]);

  const edit = (platform: Platform, patch: Partial<StoryConfig>) => setData(current => current ? {
    ...current,
    configs: current.configs.map(row => row.platform === platform ? { ...row, ...patch } : row),
  } : current);

  // Критерії оновлюються функцією, а не готовим об'єктом: два швидкі кліки по
  // чіпах читали б той самий знімок стану із замикання, і другий перетирав би
  // перший.
  const editFilters = (
    platform: Platform,
    updater: (current: Record<string, any>) => Record<string, any>,
  ) => setData(current => current ? {
    ...current,
    configs: current.configs.map(row => (
      row.platform === platform ? { ...row, filters: updater(row.filters || {}) } : row
    )),
  } : current);

  const run = async (key: string, action: () => Promise<string>) => {
    setBusy(key); setError(null); setMessage(null);
    try { setMessage(await action()); await load(); }
    catch (reason: any) { setError(reason.message || 'Не вдалося виконати дію'); }
    finally { setBusy(null); }
  };

  const save = (config: StoryConfig) => run(`save:${config.platform}`, async () => {
    const response = await fetch(`/api/publications/stories/automation/${config.platform}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled: config.enabled, auto_publish: config.auto_publish,
        items_per_run: config.items_per_run,
        interval_hours: config.interval_hours, local_time: config.local_time,
        cooldown_days: config.cooldown_days, filters: config.filters,
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || 'Не вдалося зберегти');
    return `${PLATFORM_LABEL[config.platform]}: налаштування збережено.`;
  });

  const buildNow = (platform: Platform) => run(`draft:${platform}`, async () => {
    const response = await fetch(`/api/publications/stories/automation/${platform}/drafts`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || 'Не вдалося зібрати Story');
    return result.created
      ? `Готова Story на перевірку: ${result.draft?.productnumber}.`
      : 'Для цього часу чернетка вже існує.';
  });

  const reject = (id: number) => run(`reject:${id}`, async () => {
    const response = await fetch(`/api/publications/stories/automation/drafts/${id}/reject`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'Не вдалося відхилити');
    return 'Чернетку відхилено. Товар знову доступний для добору.';
  });

  const approve = (id: number) => {
    if (armed !== id) {
      setArmed(id);
      window.setTimeout(() => setArmed(current => (current === id ? null : current)), 6000);
      return;
    }
    setArmed(null);
    void run(`approve:${id}`, async () => {
      const response = await fetch(`/api/publications/stories/automation/drafts/${id}/approve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Story не відправлено');
      const notes: string[] = result.revalidation?.warnings || [];
      return ['Story передано у захищену чергу.', ...notes].join(' ');
    });
  };

  const pending = data?.pending_count ?? 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Регулярні Stories</h3>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-gray-500 dark:text-gray-400">
            Програма сама добирає товар за вашими критеріями й ставить його в чергу. Відправлення —
            окрема дія: доки «Публікувати без перевірки» вимкнено, жодна Story не піде без вашого
            підтвердження. Товар не повертається у Stories, доки не мине захист від повторів.
          </p>
        </div>
        {pending > 0 && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
            Чекають перевірки: {pending}
          </span>
        )}
      </div>

      {(message || error) && (
        <div className={`rounded-lg px-3 py-2 text-xs ${error
          ? 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300'
          : 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300'}`}>
          {error || message}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {(data?.configs || []).map(config => (
          <div key={config.platform} className="rounded-xl border border-gray-200 p-4 dark:border-gray-700">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-gray-800 dark:text-gray-100">
                  {PLATFORM_LABEL[config.platform]}
                </div>
                <div className="text-[10px] text-gray-400">
                  {config.manual_review_required ? 'Story · лише після перевірки' : 'Story · публікує сама'}
                </div>
              </div>
              <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
                <input type="checkbox" checked={config.enabled}
                  onChange={event => edit(config.platform, { enabled: event.target.checked })} />
                {config.enabled ? 'Увімкнено' : 'Вимкнено'}
              </label>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <label className="text-[10px] font-medium text-gray-500">Скільки за раз
                <select value={config.items_per_run}
                  onChange={event => edit(config.platform, { items_per_run: Number(event.target.value) })}
                  className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-800">
                  {ITEMS_PER_RUN.map(value => (
                    <option key={value} value={value}>{value === 1 ? '1 Story' : `${value} Stories`}</option>
                  ))}
                </select>
              </label>
              <label className="text-[10px] font-medium text-gray-500">Періодичність
                <select value={config.interval_hours}
                  onChange={event => edit(config.platform, { interval_hours: Number(event.target.value) })}
                  className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-800">
                  {INTERVALS.map(row => <option key={row.value} value={row.value}>{row.label}</option>)}
                </select>
              </label>
              <label className="text-[10px] font-medium text-gray-500">Час Києва
                <input type="time" value={config.local_time}
                  onChange={event => edit(config.platform, { local_time: event.target.value })}
                  className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-800" />
              </label>
              <label className="text-[10px] font-medium text-gray-500">Без повтору
                <select value={config.cooldown_days}
                  onChange={event => edit(config.platform, { cooldown_days: Number(event.target.value) })}
                  className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-800">
                  {COOLDOWNS.map(value => <option key={value} value={value}>{value} днів</option>)}
                </select>
              </label>
            </div>

            {config.items_per_run > 1 && (
              <p className="mt-2 text-[10px] leading-relaxed text-gray-400 dark:text-gray-500">
                Серія виходить не залпом, а з проміжком у кілька хвилин: Stories живуть добу,
                тож глядач однаково гортає їх поспіль, а акаунт не впирається в ліміт Meta.
                {config.platform === 'facebook' && ' У Facebook кожна Story — це два завдання, бо Сторінок дві.'}
              </p>
            )}

            <button type="button" onClick={() => setOpenFilters(openFilters === config.platform ? null : config.platform)}
              className="mt-3 w-full rounded-lg bg-gray-50 px-3 py-2 text-left text-[11px] dark:bg-gray-800">
              <span className="font-medium text-gray-700 dark:text-gray-200">Критерії добору</span>
              <span className="ml-2 text-gray-500">{config.filters_label}</span>
            </button>

            {openFilters === config.platform && (
              <FilterEditor
                filters={config.filters}
                options={options}
                seasons={seasons}
                onChange={updater => editFilters(config.platform, updater)}
              />
            )}

            <label className="mt-3 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
              <input type="checkbox" className="mt-0.5" checked={config.auto_publish}
                onChange={event => edit(config.platform, { auto_publish: event.target.checked })} />
              <span>
                <b>Публікувати без перевірки.</b> Черга йтиме в акаунт сама, без вашого підтвердження.
                Вмикайте, коли впевнитесь, що добір стабільно дає гідні Stories.
              </span>
            </label>

            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 pt-3 dark:border-gray-700">
              <span className="text-[10px] text-gray-400">
                {config.enabled ? `Наступна: ${fmt(config.next_run_at)}` : 'Розклад неактивний'}
                {config.last_error ? ` · помилка: ${config.last_error}` : ''}
              </span>
              <div className="flex gap-2">
                <button type="button" disabled={busy !== null} onClick={() => void buildNow(config.platform)}
                  className="rounded-lg border border-gray-200 px-2.5 py-1.5 text-[11px] text-gray-600 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300">
                  {busy === `draft:${config.platform}` ? 'Збираю…' : 'Зібрати зараз'}
                </button>
                <button type="button" disabled={busy !== null} onClick={() => void save(config)}
                  className="rounded-lg bg-gray-900 px-3 py-1.5 text-[11px] font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900">
                  {busy === `save:${config.platform}` ? 'Зберігаю…' : 'Зберегти'}
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="border-t border-gray-100 pt-3 dark:border-gray-700">
        <h4 className="mb-2 text-xs font-semibold text-gray-700 dark:text-gray-200">Черга Stories</h4>
        {(data?.drafts || []).length ? (
          <div className="space-y-2">
            {(data?.drafts || []).slice(0, 12).map(draft => {
              const waiting = draft.status === 'awaiting_review';
              return (
                <div key={draft.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-100 px-3 py-2 dark:border-gray-700">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <b>{PLATFORM_LABEL[draft.platform]}</b>
                      <span className={`rounded-full px-2 py-0.5 text-[10px] ${waiting
                        ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
                        : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'}`}>
                        {waiting ? 'Чекає перевірки'
                          : draft.status === 'approved' ? 'Відправлено'
                          : draft.status === 'rejected' ? 'Відхилено' : draft.status}
                      </span>
                      <span className="font-mono text-[11px] text-gray-600 dark:text-gray-300">{draft.productnumber}</span>
                      <span className="text-[10px] text-gray-400">{fmt(draft.scheduled_for)}</span>
                    </div>
                    <div className="mt-0.5 text-[10px] text-gray-400">
                      {draft.audit?.filters_label}
                      {draft.reserves?.length ? ` · запас: ${draft.reserves.length}` : ' · запасу немає'}
                    </div>
                    {!!draft.warnings?.length && (
                      <ul className="mt-1 space-y-0.5">
                        {draft.warnings.map((warning, index) => (
                          <li key={index} className="text-[10px] leading-snug text-amber-700 dark:text-amber-300">⚠ {warning}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                  {waiting && (
                    <div className="flex gap-2">
                      <button type="button" disabled={busy !== null} onClick={() => approve(draft.id)}
                        className={`rounded-lg px-2.5 py-1.5 text-[11px] font-medium disabled:opacity-50 ${
                          armed === draft.id ? 'bg-red-600 text-white' : 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
                        }`}>
                        {busy === `approve:${draft.id}` ? 'Відправляю…'
                          : armed === draft.id ? 'Точно опублікувати?' : 'Опублікувати'}
                      </button>
                      <button type="button" disabled={busy !== null} onClick={() => void reject(draft.id)}
                        className="rounded-lg border border-red-200 px-2.5 py-1.5 text-[11px] text-red-600 disabled:opacity-50 dark:border-red-800 dark:text-red-300">
                        {busy === `reject:${draft.id}` ? 'Відхиляю…' : 'Відхилити'}
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-lg bg-gray-50 px-3 py-5 text-center text-xs text-gray-400 dark:bg-gray-800">
            Черга порожня. «Зібрати зараз» покаже, що дає поточний добір.
          </div>
        )}
      </div>
    </div>
  );
};

/** Критерії добору мовою фільтрів «Товарів»: порожній список = без обмеження. */
const FilterEditor: React.FC<{
  filters: Record<string, any>;
  options: Record<string, Reference[]>;
  seasons: string[];
  onChange: (updater: (current: Record<string, any>) => Record<string, any>) => void;
}> = ({ filters, options, seasons, onChange }) => {
  const patch = (key: string, value: any) => onChange(current => {
    const next = { ...current };
    if (value === null || value === '' || (Array.isArray(value) && !value.length)) delete next[key];
    else next[key] = value;
    return next;
  });
  const toggleList = (key: string, value: number | string) => onChange(current => {
    const list: Array<number | string> = Array.isArray(current[key]) ? current[key] : [];
    const next = { ...current };
    const updated = list.includes(value) ? list.filter(item => item !== value) : [...list, value];
    if (updated.length) next[key] = updated;
    else delete next[key];
    return next;
  });
  const groups = useMemo(() => ([
    { key: 'genderids', label: 'Стать', rows: options.genderids || [] },
    { key: 'typeids', label: 'Тип', rows: options.typeids || [] },
    { key: 'brandids', label: 'Бренд', rows: options.brandids || [] },
  ]), [options]);

  return (
    <div className="mt-2 space-y-3 rounded-lg border border-gray-100 p-3 dark:border-gray-700">
      {groups.map(group => (
        <div key={group.key}>
          <div className="mb-1 text-[10px] font-medium text-gray-500">{group.label}</div>
          <div className="flex max-h-24 flex-wrap gap-1 overflow-y-auto">
            {group.rows.slice(0, 60).map(row => {
              const active = (filters[group.key] || []).includes(row.id);
              return (
                <button key={row.id} type="button" onClick={() => toggleList(group.key, row.id)}
                  className={`rounded-full border px-2 py-0.5 text-[10px] ${active
                    ? 'border-gray-900 bg-gray-900 text-white dark:border-gray-100 dark:bg-gray-100 dark:text-gray-900'
                    : 'border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
                  {refName(row)}
                </button>
              );
            })}
          </div>
        </div>
      ))}
      {!!seasons.length && (
        <div>
          <div className="mb-1 text-[10px] font-medium text-gray-500">Сезон</div>
          <div className="flex flex-wrap gap-1">
            {seasons.map(season => {
              const active = (filters.seasons || []).includes(season);
              return (
                <button key={season} type="button"
                  onClick={() => toggleList('seasons', season)}
                  className={`rounded-full border px-2 py-0.5 text-[10px] ${active
                    ? 'border-gray-900 bg-gray-900 text-white dark:border-gray-100 dark:bg-gray-100 dark:text-gray-900'
                    : 'border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
                  {season}
                </button>
              );
            })}
          </div>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {([
          ['min_price', 'Ціна від'], ['max_price', 'Ціна до'],
          ['min_sizeeu', 'Розмір від'], ['max_sizeeu', 'Розмір до'],
        ] as const).map(([key, label]) => (
          <label key={key} className="text-[10px] font-medium text-gray-500">{label}
            <input type="number" value={filters[key] ?? ''}
              onChange={event => patch(key, event.target.value === '' ? null : Number(event.target.value))}
              className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-800" />
          </label>
        ))}
      </div>
      <button type="button" onClick={() => onChange(() => ({}))}
        className="text-[10px] text-gray-400 underline">Скинути всі критерії</button>
    </div>
  );
};

export default StoryAutomationPanel;
