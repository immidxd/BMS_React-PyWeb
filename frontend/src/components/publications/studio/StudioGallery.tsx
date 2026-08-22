import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from './api';
import type { StudioAsset, StudioCollection } from './types';

/**
 * Галерея майстерні: фони, накладки, логотипи.
 *
 * Взаємодія свідомо повторює менеджер фото в картці товару — заливка кількох
 * файлів одразу, перетягування порядку, видалення однією дією. Людина вже
 * знає ці рухи, і вчити її другій мові заради тієї ж роботи немає сенсу.
 *
 * Майстер лежить у хмарі (R2), програма роздає його своїм ендпоінтом — тому
 * галерея однакова на будь-якій машині, а не залежить від локальної теки.
 */

const CARD = 'rounded-xl border border-gray-200 dark:border-gray-700';
const BTN = 'rounded-lg px-3 py-1.5 text-xs font-medium transition-colors';
const BTN_MAIN = `${BTN} bg-[var(--bms-accent)] text-white hover:opacity-90`;
const BTN_GHOST = `${BTN} border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800`;

const humanSize = (bytes: number): string =>
  bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} МБ` : `${Math.round(bytes / 1024)} КБ`;

type GridProps = {
  assets: StudioAsset[];
  selectedId?: number | null;
  onPick?: (asset: StudioAsset) => void;
  onDelete?: (asset: StudioAsset) => void;
  onReorder?: (ids: number[]) => void;
};

/** Плитка галереї. Мініатюра, а не оригінал: фон 2560 px у сітці — це мегабайти
 *  трафіку на кожне відкриття вкладки. */
export const AssetGrid: React.FC<GridProps> = ({ assets, selectedId, onPick, onDelete, onReorder }) => {
  const dragId = useRef<number | null>(null);

  const drop = (targetId: number) => {
    const sourceId = dragId.current;
    dragId.current = null;
    if (!onReorder || !sourceId || sourceId === targetId) return;
    const ids = assets.map(asset => asset.id);
    const from = ids.indexOf(sourceId);
    const to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    onReorder(ids);
  };

  if (!assets.length) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 p-8 text-center text-xs text-gray-400 dark:border-gray-600">
        Тут поки порожньо. Залийте фони, накладки чи логотипи — і вони будуть під рукою в кожному пості.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {assets.map(asset => (
        <div
          key={asset.id}
          draggable={Boolean(onReorder)}
          onDragStart={() => { dragId.current = asset.id; }}
          onDragOver={event => { if (onReorder) event.preventDefault(); }}
          onDrop={() => drop(asset.id)}
          onClick={() => onPick?.(asset)}
          className={`group relative overflow-hidden rounded-xl border transition-shadow ${
            selectedId === asset.id
              ? 'border-[var(--bms-accent)] shadow-md'
              : 'border-gray-200 hover:shadow-sm dark:border-gray-700'
          } ${onPick ? 'cursor-pointer' : ''}`}
        >
          <img
            src={asset.thumb_src}
            alt={asset.title || asset.filename}
            loading="lazy"
            className="aspect-square w-full bg-gray-50 object-cover dark:bg-gray-800"
          />
          <div className="px-2 py-1.5">
            <div className="truncate text-[11px] text-gray-700 dark:text-gray-200">
              {asset.title || asset.filename}
            </div>
            <div className="text-[10px] text-gray-400">
              {asset.width}×{asset.height} · {humanSize(asset.bytes)}
            </div>
          </div>
          {onDelete && (
            <button
              type="button"
              onClick={event => { event.stopPropagation(); onDelete(asset); }}
              className="absolute right-1.5 top-1.5 hidden rounded-md bg-black/60 px-1.5 py-0.5 text-[10px] text-white group-hover:block"
            >
              Видалити
            </button>
          )}
        </div>
      ))}
    </div>
  );
};

/* ── Вибір фото з галереї (для фону й накладок) ─────────────────────────── */

export const AssetPicker: React.FC<{
  open: boolean;
  onClose: () => void;
  onPick: (asset: StudioAsset) => void;
}> = ({ open, onClose, onPick }) => {
  const [assets, setAssets] = useState<StudioAsset[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    void api.fetchAssets().then(result => setAssets(result.items)).catch(() => setAssets([]));
  }, [open]);

  if (!open) return null;

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    try {
      const result = await api.uploadAssets(Array.from(files));
      setAssets(current => [...result.items.filter(
        item => !current.some(existing => existing.id === item.id)), ...current]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="max-h-[85vh] w-full max-w-4xl overflow-auto rounded-2xl bg-white p-4 shadow-xl dark:bg-gray-900">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Галерея</h4>
          <div className="flex items-center gap-2">
            <label className={`${BTN_GHOST} cursor-pointer`}>
              {busy ? 'Заливаю…' : 'Залити фото'}
              <input type="file" accept="image/*" multiple hidden
                onChange={event => void upload(event.target.files)} />
            </label>
            <button type="button" className={BTN_GHOST} onClick={onClose}>Закрити</button>
          </div>
        </div>
        <AssetGrid assets={assets} onPick={asset => { onPick(asset); onClose(); }} />
      </div>
    </div>
  );
};

/* ── Вкладка «Галерея» ──────────────────────────────────────────────────── */

const StudioGallery: React.FC = () => {
  const [assets, setAssets] = useState<StudioAsset[]>([]);
  const [collections, setCollections] = useState<StudioCollection[]>([]);
  const [collectionId, setCollectionId] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [assetResult, collectionResult] = await Promise.all([
      api.fetchAssets(collectionId, search.trim() || undefined),
      api.fetchCollections('media'),
    ]);
    setAssets(assetResult.items);
    setCollections(collectionResult.items);
  }, [collectionId, search]);

  useEffect(() => { void load().catch(reason => setError(reason.message)); }, [load]);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await api.uploadAssets(Array.from(files), collectionId);
      const duplicates = result.items.filter(item => (item as any).duplicate).length;
      setMessage(
        `Додано: ${result.added - duplicates}` +
        (duplicates ? ` · вже було в галереї: ${duplicates}` : '') +
        (result.errors.length ? ` · не вдалося: ${result.errors.length}` : ''),
      );
      await load();
    } catch (reason: any) {
      setError(reason.message || 'Не вдалося залити фото');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (asset: StudioAsset) => {
    if (!window.confirm(`Прибрати «${asset.title || asset.filename}» з галереї?`)) return;
    try {
      await api.deleteAsset(asset.id);
      setAssets(current => current.filter(item => item.id !== asset.id));
    } catch (reason: any) {
      setError(reason.message);
    }
  };

  const reorder = async (ids: number[]) => {
    setAssets(current => ids.map(id => current.find(asset => asset.id === id)!).filter(Boolean));
    try { await api.reorderAssets(ids); } catch (reason: any) { setError(reason.message); }
  };

  const addCollection = async () => {
    const name = window.prompt('Назва підбірки');
    if (!name?.trim()) return;
    try {
      const created = await api.createCollection('media', name.trim());
      setCollections(current => [...current, created]);
      setCollectionId(created.id);
    } catch (reason: any) { setError(reason.message); }
  };

  const total = useMemo(() => assets.length, [assets]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Галерея майстерні</h3>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Фони, накладки й логотипи для постів. Зберігаються у хмарі, тож доступні з будь-якої машини.
            Фото товарів лишаються окремо — тут вони нічого не ламають.
          </p>
        </div>
        <label className={`${BTN_MAIN} cursor-pointer`}>
          {busy ? 'Заливаю…' : 'Залити фото'}
          <input type="file" accept="image/*" multiple hidden
            onChange={event => void upload(event.target.files)} />
        </label>
      </div>

      {(message || error) && (
        <div className={`rounded-lg px-3 py-2 text-xs ${error
          ? 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300'
          : 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300'}`}>
          {error || message}
        </div>
      )}

      <div className={`${CARD} flex flex-wrap items-center gap-2 p-3`}>
        <button type="button" onClick={() => setCollectionId(null)}
          className={`${BTN} ${collectionId === null
            ? 'bg-[var(--bms-accent)] text-white'
            : 'border border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
          Усі фото
        </button>
        {collections.map(collection => (
          <button key={collection.id} type="button" onClick={() => setCollectionId(collection.id)}
            className={`${BTN} ${collectionId === collection.id
              ? 'bg-[var(--bms-accent)] text-white'
              : 'border border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
            {collection.name}
          </button>
        ))}
        <button type="button" onClick={() => void addCollection()} className={BTN_GHOST}>+ Підбірка</button>
        <input
          value={search}
          onChange={event => setSearch(event.target.value)}
          placeholder="Пошук за назвою"
          className="ml-auto w-48 rounded-lg border border-gray-200 px-2 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-800"
        />
        <span className="text-[11px] text-gray-400">{total} шт.</span>
      </div>

      <AssetGrid assets={assets} onDelete={remove} onReorder={reorder} />
    </div>
  );
};

export default StudioGallery;
