import React from 'react';
import { InfoCircleOutlined, WarningOutlined } from '@ant-design/icons';

/** Залишок добової квоти публікацій, як його рахує Worker. */
export interface DailyCapacity {
  known: boolean;
  used?: number;
  queued?: number;
  limit?: number;
  remaining?: number;
  frees_at?: string | null;
  frees_at_label?: string;
  pages?: number;
  batch_max?: number;
  error?: string;
}

interface Props {
  capacity: DailyCapacity | null;
  /** Скільки постів людина зараз збирається надіслати. */
  planned: number;
  network: 'Instagram' | 'Facebook';
}

/**
 * Показує стелю ДО того, як людина витратить час на редагування чернеток.
 *
 * Коли квоти не вистачає, попередження навмисно не блокує кнопку: рішення
 * лишається за людиною — вона може зняти зайві картки або надіслати менший
 * пакет. Мовчати ж не можна, бо Worker відкладе надлишок до завтра, і це
 * виглядало б як «пости зникли».
 */
const DailyCapacityNote: React.FC<Props> = ({ capacity, planned, network }) => {
  if (!capacity) return null;
  if (!capacity.known) {
    return (
      <div className="mb-3 flex gap-2 rounded-xl border border-gray-200 px-3 py-2.5 text-xs text-gray-500 dark:border-gray-700 dark:text-gray-400">
        <InfoCircleOutlined className="mt-0.5 shrink-0" />
        <span>Залишок добової квоти зараз невідомий — диспетчер не відповів. Публікацію це не блокує.</span>
      </div>
    );
  }

  const remaining = capacity.remaining ?? 0;
  const overflow = planned > remaining;
  const perPage = network === 'Facebook' && (capacity.pages || 1) > 1;

  return (
    <div className={`mb-3 flex gap-2 rounded-xl border px-3 py-2.5 text-xs leading-relaxed ${
      overflow
        ? 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200'
        : 'border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400'
    }`}>
      {overflow ? <WarningOutlined className="mt-0.5 shrink-0" /> : <InfoCircleOutlined className="mt-0.5 shrink-0" />}
      <span>
        Добова квота {network}: <b>лишилося {remaining} із {capacity.limit}</b>
        {capacity.used ? ` · вже опубліковано ${capacity.used}` : ''}
        {capacity.queued ? ` · у черзі ${capacity.queued}` : ''}
        {perPage ? ' (за найтіснішою Сторінкою)' : ''}.
        {overflow && (
          <>
            {' '}Зараз вибрано {planned} — надлишок не зникне, але дочекається вільного слота
            {capacity.frees_at_label ? ` (${capacity.frees_at_label})` : ''}.
          </>
        )}
      </span>
    </div>
  );
};

export default DailyCapacityNote;
