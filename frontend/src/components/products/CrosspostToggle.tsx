import React, { useEffect, useState } from 'react';
import { LoadingOutlined } from '@ant-design/icons';

/** Формати Instagram і Facebook збігаються (спільний renderer BMS), тому
 *  дзеркальна публікація — це та сама чернетка з іншим адресатом. Єдина
 *  змістовна різниця — заклик у кінці підпису, і його підмінює backend. */
export type MirrorTarget = 'facebook' | 'instagram';

export interface MirrorStatus {
  loading: boolean;
  ready: boolean;
  account: string;
  pages: { id: string; name: string }[];
  problem: string | null;
}

const LABELS: Record<MirrorTarget, { title: string; endpoint: string; accent: string }> = {
  facebook: {
    title: 'Опублікувати і у Facebook',
    endpoint: '/api/publications/facebook/status',
    accent: '#1877F2',
  },
  instagram: {
    title: 'Опублікувати і в Instagram',
    endpoint: '/api/publications/instagram/status',
    accent: '#E1306C',
  },
};

export function useMirrorStatus(target: MirrorTarget): MirrorStatus {
  const [status, setStatus] = useState<MirrorStatus>({
    loading: true, ready: false, account: '', pages: [], problem: null,
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(LABELS[target].endpoint);
        const data = await response.json().catch(() => ({}));
        if (cancelled) return;
        const ready = Boolean(data.live_publish_available) && data.oauth_connected !== false;
        setStatus({
          loading: false,
          ready,
          account: String(data.account || ''),
          pages: Array.isArray(data.pages) ? data.pages : [],
          problem: ready ? null : (
            data.dispatcher_error
            || (Array.isArray(data.missing) && data.missing.length ? data.missing.join(', ') : '')
            || 'акаунт ще не підключено'
          ),
        });
      } catch (error: any) {
        if (!cancelled) {
          setStatus({
            loading: false, ready: false, account: '', pages: [],
            problem: error?.message || 'не вдалося перевірити підключення',
          });
        }
      }
    })();
    return () => { cancelled = true; };
  }, [target]);

  return status;
}

interface Props {
  target: MirrorTarget;
  status: MirrorStatus;
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Лише для Facebook: у які Сторінки дзеркалити. */
  pageIds?: string[];
  onPageIdsChange?: (pageIds: string[]) => void;
  publishType: 'feed' | 'story' | 'reel';
  scheduled?: boolean;
}

const TYPE_NOTE: Record<'feed' | 'story' | 'reel', string> = {
  feed: 'Той самий кадр і той самий текст',
  story: 'Та сама Story 9:16 з тим самим текстом на кадрі',
  reel: 'Те саме відео зі слайдів',
};

const CrosspostToggle: React.FC<Props> = ({
  target, status, checked, onChange, pageIds = [], onPageIdsChange, publishType, scheduled = false,
}) => {
  const label = LABELS[target];
  const disabled = status.loading || !status.ready;

  return (
    <div className="rounded-xl border border-gray-200 px-3 py-2.5 dark:border-gray-700">
      <label className={`flex items-start gap-2 text-xs ${disabled ? 'cursor-not-allowed opacity-70' : 'cursor-pointer'}`}>
        <input type="checkbox" className="mt-0.5" checked={checked && !disabled} disabled={disabled}
          onChange={event => onChange(event.target.checked)} />
        <span className="min-w-0">
          <span className="font-semibold text-gray-800 dark:text-gray-100" style={checked && !disabled ? { color: label.accent } : undefined}>
            {label.title}
          </span>
          <span className="mt-0.5 block leading-relaxed text-gray-500 dark:text-gray-400">
            {status.loading ? (
              <><LoadingOutlined /> перевіряю підключення…</>
            ) : status.ready ? (
              <>{TYPE_NOTE[publishType]}{status.account ? ` · ${status.account}` : ''}. Заклик у підписі BMS замінить на місцевий.</>
            ) : (
              <>Недоступно: {status.problem}.</>
            )}
          </span>
        </span>
      </label>

      {checked && !disabled && target === 'facebook' && status.pages.length > 1 && onPageIdsChange && (
        <div className="mt-2 grid gap-1.5 pl-6 sm:grid-cols-2">
          {status.pages.map(page => {
            const on = pageIds.includes(page.id);
            return (
              <label key={page.id} className="flex cursor-pointer items-center gap-2 text-[11px] text-gray-600 dark:text-gray-300">
                <input type="checkbox" checked={on} onChange={event => onPageIdsChange(
                  event.target.checked ? [...pageIds, page.id] : pageIds.filter(value => value !== page.id),
                )} />
                <span className="min-w-0 truncate">{page.name}</span>
              </label>
            );
          })}
        </div>
      )}

      {checked && !disabled && scheduled && (
        <p className="mt-2 pl-6 text-[11px] leading-relaxed text-gray-400">
          Обидві публікації підуть за тим самим розкладом.
        </p>
      )}
    </div>
  );
};

export default CrosspostToggle;
