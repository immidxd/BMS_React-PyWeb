import React, { useCallback, useEffect, useMemo, useState } from 'react';
import * as api from './api';
import StudioEditor, { emptySpec } from './StudioEditor';
import StudioGallery from './StudioGallery';
import StudioFonts from './StudioFonts';
import type {
  CanvasFormatKey, StudioCollection, StudioConfig, StudioFont, StudioPost,
} from './types';

/**
 * Майстерня — власні пости, які не про конкретний товар: анонси, оголошення,
 * вітання, вітринні картки.
 *
 * Чому окремо від решти «Публікацій»: усе інше в цій вкладці рахує стан
 * ТОВАРУ. Пост-анонс товару не має, і якби він писався в ті самі таблиці,
 * статистика «опубліковано» почала б брехати. Тут власний контур — рівно як у
 * підбірок.
 */

const BTN = 'rounded-lg px-3 py-1.5 text-xs font-medium transition-colors';
const BTN_MAIN = `${BTN} bg-[var(--bms-accent)] text-white hover:opacity-90 disabled:opacity-50`;
const BTN_GHOST = `${BTN} border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800`;

const VIEWS = [
  { key: 'posts', label: 'Пости' },
  { key: 'gallery', label: 'Галерея' },
  { key: 'fonts', label: 'Шрифти' },
] as const;

type View = typeof VIEWS[number]['key'];

const STATUS_LABEL: Record<StudioPost['status'], string> = {
  draft: 'Чернетка', ready: 'Готовий', scheduled: 'Заплановано',
  published: 'Опубліковано', archived: 'В архіві',
};

const fmtDate = (value?: string | null) =>
  value ? new Date(value).toLocaleString('uk-UA', { dateStyle: 'short', timeStyle: 'short' }) : '—';

const StudioPanel: React.FC = () => {
  const [view, setView] = useState<View>('posts');
  const [config, setConfig] = useState<StudioConfig | null>(null);
  const [fonts, setFonts] = useState<StudioFont[]>([]);
  const [posts, setPosts] = useState<StudioPost[]>([]);
  const [collections, setCollections] = useState<StudioCollection[]>([]);
  const [collectionId, setCollectionId] = useState<number | null>(null);
  const [editing, setEditing] = useState<StudioPost | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newFormat, setNewFormat] = useState<CanvasFormatKey>('story');

  const loadFonts = useCallback(async () => {
    const result = await api.fetchFonts();
    setFonts(result.items);
  }, []);

  const loadPosts = useCallback(async () => {
    const [postResult, collectionResult] = await Promise.all([
      api.fetchPosts(collectionId),
      api.fetchCollections('post'),
    ]);
    setPosts(postResult.items);
    setCollections(collectionResult.items);
  }, [collectionId]);

  useEffect(() => {
    void api.fetchConfig().then(setConfig).catch(reason => setError(reason.message));
    void loadFonts().catch(() => undefined);
  }, [loadFonts]);

  useEffect(() => { void loadPosts().catch(reason => setError(reason.message)); }, [loadPosts]);

  // Фірмові шрифти реєструються в документі — без цього ані прев'ю, ані
  // вимірювання переносів не бачать справжніх метрик, і рядки «стрибають»
  // після збирання кадру.
  useEffect(() => {
    let cancelled = false;
    fonts.forEach(font => {
      try {
        const face = new FontFace(font.family, `url(${font.src})`, {
          weight: String(font.weight), style: font.style,
        });
        void face.load().then(loaded => {
          if (!cancelled) (document as any).fonts.add(loaded);
        }).catch(() => undefined);
      } catch { /* стара реалізація FontFace — макет намалюється системним */ }
    });
    return () => { cancelled = true; };
  }, [fonts]);

  const createPost = async () => {
    setBusy(true); setError(null);
    try {
      const created = await api.createPost({
        title: 'Новий пост',
        base_format: newFormat,
        spec: emptySpec(newFormat),
        targets: [],
        collection_id: collectionId,
      });
      setPosts(current => [created, ...current]);
      setEditing(created);
    } catch (reason: any) {
      setError(reason.message || 'Не вдалося створити пост');
    } finally {
      setBusy(false);
    }
  };

  const openPost = async (post: StudioPost) => {
    try { setEditing(await api.fetchPost(post.id)); }
    catch (reason: any) { setError(reason.message); }
  };

  const addCollection = async () => {
    const name = window.prompt('Назва підбірки постів');
    if (!name?.trim()) return;
    try {
      const created = await api.createCollection('post', name.trim());
      setCollections(current => [...current, created]);
      setCollectionId(created.id);
    } catch (reason: any) { setError(reason.message); }
  };

  const formats = config?.formats || [];

  if (editing && config) {
    return (
      <StudioEditor
        post={editing}
        config={config}
        fonts={fonts}
        onSaved={saved => {
          setEditing(saved);
          setPosts(current => current.map(item => (item.id === saved.id ? { ...item, ...saved } : item)));
        }}
        onDeleted={id => {
          setPosts(current => current.filter(item => item.id !== id));
          setEditing(null);
        }}
        onClose={() => { setEditing(null); void loadPosts(); }}
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* gap-x-6: інакше опис підповзав упритул до кнопок праворуч */}
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Майстерня</h3>
          <p className="mt-1.5 max-w-2xl text-xs leading-relaxed text-gray-500 dark:text-gray-400">
            Пости, які не про конкретний товар: анонси, оголошення, вітання. Макет складається тут,
            готовий кадр зберігається у хмарі — і саме він потім піде в мережі. Фото й шрифти
            спільні для всіх постів, тож фірмовий вигляд не треба збирати щоразу заново.
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {VIEWS.map(item => (
            <button key={item.key} type="button" onClick={() => setView(item.key)}
              className={`${BTN} ${view === item.key
                ? 'bg-[var(--bms-accent)] text-white'
                : 'border border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-900/20 dark:text-red-300">
          {error}
        </div>
      )}

      {view === 'gallery' && <StudioGallery />}
      {view === 'fonts' && <StudioFonts fonts={fonts} onChanged={() => void loadFonts()} />}

      {view === 'posts' && (
        <>
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-gray-200 p-3 dark:border-gray-700">
            <button type="button" onClick={() => setCollectionId(null)}
              className={`${BTN} ${collectionId === null
                ? 'bg-[var(--bms-accent)] text-white'
                : 'border border-gray-200 text-gray-600 dark:border-gray-600 dark:text-gray-300'}`}>
              Усі пости
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

            <div className="ml-auto flex items-center gap-2">
              <select value={newFormat} className="rounded-lg border border-gray-200 px-2 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-800"
                onChange={event => setNewFormat(event.target.value as CanvasFormatKey)}>
                {formats.map(format => (
                  <option key={format.key} value={format.key}>{format.label}</option>
                ))}
              </select>
              <button type="button" disabled={busy || !config} onClick={() => void createPost()} className={BTN_MAIN}>
                {busy ? 'Створюю…' : '+ Новий пост'}
              </button>
            </div>
          </div>

          {!posts.length ? (
            <div className="rounded-xl border border-dashed border-gray-300 p-10 text-center text-xs text-gray-400 dark:border-gray-600">
              Постів ще немає. Створіть перший — фон із галереї, заголовок, підпис.
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              {posts.map(post => (
                <div key={post.id}
                  onClick={() => void openPost(post)}
                  className="group cursor-pointer overflow-hidden rounded-xl border border-gray-200 transition-shadow hover:shadow-md dark:border-gray-700">
                  <div className="aspect-[4/5] w-full bg-gray-50 dark:bg-gray-800">
                    {post.preview_src ? (
                      <img src={post.preview_src} alt={post.title}
                        className="h-full w-full object-cover" loading="lazy" />
                    ) : (
                      <div className="flex h-full items-center justify-center text-[11px] text-gray-400">
                        кадр ще не зібрано
                      </div>
                    )}
                  </div>
                  <div className="px-2 py-1.5">
                    <div className="truncate text-[11px] font-medium text-gray-800 dark:text-gray-100">
                      {post.title}
                    </div>
                    <div className="mt-0.5 flex items-center justify-between text-[10px] text-gray-400">
                      <span>{STATUS_LABEL[post.status]}</span>
                      <span>{fmtDate(post.updated_at)}</span>
                    </div>
                    {Boolean(post.targets?.length) && (
                      <div className="mt-1 truncate text-[10px] text-gray-400">
                        {post.targets.map(target => target.platform).join(' · ')}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default StudioPanel;
