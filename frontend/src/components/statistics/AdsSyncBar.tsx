import React, { useState } from 'react';
import { statisticsService, type AdvertisingStatsResponse } from '../../services/statisticsService';

/** Через скільки годин без перевірки виписки дані вважаються застиглими.
 *  Фоновий цикл BMS ходить у банк раз на 6 год, тож доба — це вже збій
 *  (або BMS довго був закритий). */
const STALE_HOURS = 24;

/** Рядок свіжості над статистикою реклами.
 *
 *  До 04.10.2026 дані застигли на 30.08 і цього ніде не було видно: графік
 *  просто закінчувався серпнем. Тепер завжди написано, коли BMS востаннє
 *  дивився у виписку, і застиглі дані підсвічуються.
 */
const AdsSyncBar: React.FC<{ sync?: AdvertisingStatsResponse['sync'] }> = ({ sync }) => {
  const [state, setState] = useState<'idle' | 'starting' | 'started' | 'busy' | 'error'>('idle');

  const last = sync?.last_run_at ? new Date(sync.last_run_at) : null;
  const hours = last ? (Date.now() - last.getTime()) / 3_600_000 : Infinity;
  const stale = hours > STALE_HOURS;
  const errors = sync?.errors;
  const warn = stale || !!errors;

  const run = async () => {
    setState('starting');
    try {
      const r = await statisticsService.syncAdvertising();
      setState(r.started ? 'started' : 'busy');
    } catch {
      setState('error');
    }
  };

  return (
    <div className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs ${
      warn
        ? 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200'
        : 'border-gray-200 bg-gray-50 text-gray-600 dark:border-gray-700 dark:bg-gray-800/40 dark:text-gray-300'
    }`}>
      <span>
        Виписку банку перевірено:{' '}
        <b>{last ? last.toLocaleString('uk-UA', { dateStyle: 'short', timeStyle: 'short' }) : 'ніколи'}</b>
        {sync?.checked_until && <> · списання Meta зібрані до <b>{sync.checked_until}</b></>}
        {stale && <> · дані застаріли — BMS перевіряє виписку сам раз на 6 год, поки відкритий</>}
        {errors && <> · помилка: {errors}</>}
      </span>
      <span className="flex items-center gap-2">
        {state === 'started' && <span>Перевірка йде у фоні (кілька хвилин) — відкрийте вкладку знову</span>}
        {state === 'busy' && <span>Перевірка вже йде</span>}
        {state === 'error' && <span className="text-red-600">Не вдалося запустити</span>}
        <button
          type="button"
          onClick={run}
          disabled={state === 'starting' || state === 'started'}
          className="rounded-md border border-gray-300 bg-white px-2.5 py-1 font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
        >
          Перевірити зараз
        </button>
      </span>
    </div>
  );
};

export default AdsSyncBar;
