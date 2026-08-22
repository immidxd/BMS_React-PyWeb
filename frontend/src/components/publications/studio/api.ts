/** Тонкий шар над `/api/studio/*`: одна форма помилки на всю майстерню. */

import type {
  PostSpec, StudioAsset, StudioCollection, StudioConfig, StudioFont, StudioPost,
} from './types';

const json = async <T>(response: Response): Promise<T> => {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({} as any));
    throw new Error(payload.detail || `Помилка запиту (${response.status})`);
  }
  return response.json() as Promise<T>;
};

const send = <T>(url: string, method: string, body?: unknown): Promise<T> =>
  fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(json<T>);

export const fetchConfig = (): Promise<StudioConfig> =>
  fetch('/api/studio/config').then(json<StudioConfig>);

/* ── Галерея ────────────────────────────────────────────────────────────── */

export const fetchAssets = (collectionId?: number | null, search?: string) => {
  const params = new URLSearchParams();
  if (collectionId) params.set('collection_id', String(collectionId));
  if (search) params.set('search', search);
  const query = params.toString();
  return fetch(`/api/studio/assets${query ? `?${query}` : ''}`)
    .then(json<{ items: StudioAsset[]; total: number }>);
};

export const uploadAssets = (files: File[], collectionId?: number | null) => {
  const form = new FormData();
  files.forEach(file => form.append('files', file));
  if (collectionId) form.append('collection_id', String(collectionId));
  return fetch('/api/studio/assets', { method: 'POST', body: form })
    .then(json<{ added: number; items: StudioAsset[]; errors: Array<{ file: string; reason: string }> }>);
};

export const updateAsset = (id: number, patch: Record<string, unknown>) =>
  send<StudioAsset>(`/api/studio/assets/${id}`, 'PATCH', patch);

export const reorderAssets = (ids: number[]) =>
  send<{ reordered: number }>('/api/studio/assets/reorder', 'POST', { ids });

export const deleteAsset = (id: number) =>
  send<{ deleted: number }>(`/api/studio/assets/${id}`, 'DELETE');

/* ── Шрифти ─────────────────────────────────────────────────────────────── */

export const fetchFonts = () =>
  fetch('/api/studio/fonts').then(json<{ items: StudioFont[] }>);

export const uploadFonts = (files: File[]) => {
  const form = new FormData();
  files.forEach(file => form.append('files', file));
  return fetch('/api/studio/fonts', { method: 'POST', body: form })
    .then(json<{ added: number; items: StudioFont[]; errors: Array<{ file: string; reason: string }> }>);
};

export const deleteFont = (id: number) =>
  send<{ deleted: number }>(`/api/studio/fonts/${id}`, 'DELETE');

/* ── Підбірки ───────────────────────────────────────────────────────────── */

export const fetchCollections = (kind: 'media' | 'post') =>
  fetch(`/api/studio/collections?kind=${kind}`).then(json<{ items: StudioCollection[] }>);

export const createCollection = (kind: 'media' | 'post', name: string) =>
  send<StudioCollection>('/api/studio/collections', 'POST', { kind, name });

export const renameCollection = (id: number, name: string) =>
  send<StudioCollection>(`/api/studio/collections/${id}`, 'PATCH', { name });

export const deleteCollection = (id: number) =>
  send<{ deleted: number }>(`/api/studio/collections/${id}`, 'DELETE');

/* ── Пости ──────────────────────────────────────────────────────────────── */

export const fetchPosts = (collectionId?: number | null, search?: string) => {
  const params = new URLSearchParams();
  if (collectionId) params.set('collection_id', String(collectionId));
  if (search) params.set('search', search);
  const query = params.toString();
  return fetch(`/api/studio/posts${query ? `?${query}` : ''}`)
    .then(json<{ items: StudioPost[]; total: number }>);
};

export const fetchPost = (id: number) =>
  fetch(`/api/studio/posts/${id}`).then(json<StudioPost>);

export const createPost = (payload: Record<string, unknown>) =>
  send<StudioPost>('/api/studio/posts', 'POST', payload);

export const updatePost = (id: number, patch: Record<string, unknown>) =>
  send<StudioPost>(`/api/studio/posts/${id}`, 'PATCH', patch);

export const deletePost = (id: number) =>
  send<{ deleted: number }>(`/api/studio/posts/${id}`, 'DELETE');

/** Готовий растр із редактора. Саме цей файл потім забирає мережа. */
export const uploadRender = (id: number, format: string, blob: Blob) => {
  const form = new FormData();
  form.append('file', blob, `${format}.png`);
  form.append('format', format);
  return fetch(`/api/studio/posts/${id}/render`, { method: 'POST', body: form })
    .then(json<StudioPost>);
};

export type { PostSpec };
