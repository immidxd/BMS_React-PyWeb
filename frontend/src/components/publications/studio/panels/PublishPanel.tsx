import React from 'react';
import * as api from '../api';
import { BTN, BTN_GHOST, FIELD, LABEL, Section } from '../ui';
import type { CanvasFormatKey, PlatformKey, PostTarget, StudioConfig } from '../types';

/**
 * Куди й коли йде пост.
 *
 * Мережі показані завжди, навіть непідключені: людина має бачити повний
 * список і причину, чому Viber сірий, а не гадати, куди він подівся.
 */

const PLATFORM_HINT: Record<PlatformKey, string> = {
  telegram: 'канал, відправляє сама програма',
  instagram: 'стрічка та Stories, через хмару',
  facebook: 'Сторінки, через хмару',
  viber: 'канал, одна картинка',
};

const PUBLICATION_STATUS: Record<string, string> = {
  queued: 'у черзі', scheduled: 'заплановано', processing: 'відправляється',
  retrying: 'повтор', published: 'опубліковано', failed: 'помилка',
  cancelled: 'скасовано',
};

const fmtDateTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString('uk-UA', { dateStyle: 'short', timeStyle: 'short' }) : '';

type Props = {
  config: StudioConfig;
  targets: PostTarget[];
  readiness: api.PublishReadiness | null;
  caption: string;
  publishAt: string;
  busy: string | null;
  armed: boolean;
  publishResult: api.PublishResult | null;
  publications: api.StudioPublication[];
  onToggleTarget: (platform: PlatformKey) => void;
  onTargetFormat: (platform: PlatformKey, format: CanvasFormatKey) => void;
  onTargetSetting: (platform: PlatformKey, key: string, value: unknown) => void;
  onCaption: (value: string) => void;
  onPublishAt: (value: string) => void;
  onRehearse: () => void;
  onPublish: () => void;
  onSync: () => void;
};

const PublishPanel: React.FC<Props> = ({
  config, targets, readiness, caption, publishAt, busy, armed, publishResult,
  publications, onToggleTarget, onTargetFormat, onTargetSetting, onCaption,
  onPublishAt, onRehearse, onPublish, onSync,
}) => (
  <div>
    <Section title="Мережі" defaultOpen>
      <div className="space-y-2">
        {config.platforms.map(platform => {
          const target = targets.find(item => item.platform === platform.key);
          const ready = readiness?.platforms?.[platform.key];
          return (
            <div key={platform.key} className="rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-800">
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-200">
                  <input type="checkbox" checked={Boolean(target)}
                    onChange={() => onToggleTarget(platform.key)} />
                  {platform.label}
                </label>
                <span className="text-[10px] text-gray-400">{PLATFORM_HINT[platform.key]}</span>
                {ready && !ready.ready && (
                  <span title={ready.detail || undefined}
                    className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
                    не підключено
                  </span>
                )}
                {target && (
                  <select className={`${FIELD} ml-auto w-40`} value={target.format}
                    onChange={event => onTargetFormat(platform.key, event.target.value as CanvasFormatKey)}>
                    {platform.formats.map(key => (
                      <option key={key} value={key}>
                        {config.formats.find(item => item.key === key)?.label || key}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              {target && platform.key === 'telegram' && (
                <label className="mt-1.5 flex items-center gap-2 text-[11px] text-gray-500 dark:text-gray-400">
                  <input type="checkbox" checked={Boolean(target.settings.silent)}
                    onChange={event => onTargetSetting(platform.key, 'silent', event.target.checked)} />
                  🔕 Без звуку
                </label>
              )}
              {target && platform.key === 'facebook' && Boolean(ready?.pages?.length) && (
                <div className="mt-1.5 flex flex-wrap gap-3">
                  {(ready?.pages || []).map(page => {
                    const chosen = (target.settings.page_ids as string[] | undefined) || [];
                    // Порожній вибір = всі Сторінки: так само трактує це товарний контур.
                    const checked = chosen.length === 0 || chosen.includes(page.id);
                    return (
                      <label key={page.id}
                        className="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400">
                        <input type="checkbox" checked={checked}
                          onChange={() => {
                            const all = (ready?.pages || []).map(item => item.id);
                            const current = chosen.length === 0 ? all : chosen;
                            const next = current.includes(page.id)
                              ? current.filter(id => id !== page.id)
                              : [...current, page.id];
                            onTargetSetting(platform.key, 'page_ids',
                              next.length === all.length ? [] : next);
                          }} />
                        {page.name}
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <p className="text-[10px] leading-relaxed text-gray-400">
        Кожна мережа бере свій формат — кадр під неї збирається з того самого макета.
      </p>
    </Section>

    <Section title="Підпис" defaultOpen badge={caption ? `${caption.length}` : null}>
      <textarea value={caption} rows={4} className={FIELD}
        onChange={event => onCaption(event.target.value)}
        placeholder="Текст, який піде разом із картинкою" />
      <p className="text-[10px] text-gray-400">
        Stories підпису не мають — Instagram і Facebook його ігнорують.
      </p>
    </Section>

    <Section title="Відправлення" defaultOpen>
      <div className="flex flex-wrap items-end gap-2">
        <label className={LABEL}>Опублікувати о
          <input type="datetime-local" className={`${FIELD} mt-1`} value={publishAt}
            onChange={event => onPublishAt(event.target.value)} />
        </label>
        <button type="button" className={BTN_GHOST} disabled={busy !== null} onClick={onRehearse}>
          {busy === 'rehearse' ? 'Перевіряю…' : 'Репетиція'}
        </button>
        <button type="button" disabled={busy !== null || !targets.length} onClick={onPublish}
          className={`${BTN} ${armed
            ? 'bg-red-600 text-white hover:bg-red-700'
            : 'bg-[var(--bms-accent)] text-white hover:opacity-90'} disabled:opacity-50`}>
          {busy === 'publish'
            ? 'Відправляю…'
            : armed ? 'Точно публікуємо?' : (publishAt ? 'Запланувати' : 'Опублікувати')}
        </button>
      </div>
      <p className="text-[10px] leading-relaxed text-gray-400">
        «Репетиція» проходить увесь шлях, окрім самої відправки. Публікація йде в живі акаунти,
        тому кнопка спрацьовує з другого натискання. Порожній час = публікувати одразу.
      </p>

      {publishResult && (
        <div className="space-y-1">
          {publishResult.results.map((row, index) => (
            <div key={`${row.platform}-${index}`}
              className={`flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-[11px] ${
                row.ok
                  ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300'
                  : 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300'}`}>
              <span>
                {row.platform}
                {row.page ? ` · ${row.page}` : ''}
                {row.format ? ` · ${config.formats.find(item => item.key === row.format)?.label || row.format}` : ''}
              </span>
              <span className="text-[10px] opacity-80">
                {row.error
                  || (row.dry_run ? `кадр ${Math.round((row.image_bytes || 0) / 1024)} КБ` : '')
                  || (row.cached ? 'уже відправлено раніше' : row.status || '')}
              </span>
            </div>
          ))}
        </div>
      )}
    </Section>

    {Boolean(publications.length) && (
      <Section title="Уже відправлено" badge={String(publications.length)}>
        <button type="button" className="text-[10px] text-gray-400 hover:underline" onClick={onSync}>
          оновити стани
        </button>
        <div className="space-y-1">
          {publications.map(row => (
            <div key={row.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-gray-50 px-2 py-1.5 text-[11px] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
              <span>{row.platform}{row.account_label ? ` · ${row.account_label}` : ''}</span>
              <span className="text-[10px] text-gray-400">
                {PUBLICATION_STATUS[row.status] || row.status}
                {row.scheduled_at ? ` · ${fmtDateTime(row.scheduled_at)}` : ''}
                {row.error ? ` · ${row.error}` : ''}
              </span>
            </div>
          ))}
        </div>
      </Section>
    )}
  </div>
);

export default PublishPanel;
