const DEFAULT_API_VERSION = 'v26.0';
const CAPTION_LIMIT = 2200;
const ALT_TEXT_LIMIT = 1000;
const MAX_MEDIA = 10;
const STATE_TTL_MS = 10 * 60 * 1000;
const MAX_ATTEMPTS = 5;
const MAX_BATCH_PER_TICK = 5;
const LOCAL_PUBLISHING_LIMIT = 45;
const CONTAINER_POLL_MS = 45 * 1000;
const RETRY_MINUTES = [1, 5, 15, 60, 180];
const INSTAGRAM_LOGIN_SCOPES = [
  'instagram_business_basic',
  'instagram_business_content_publish',
];

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
    },
  });
}

function error(message, status = 400) {
  return json({ ok: false, error: message }, status);
}

function authorized(request, env) {
  const expected = String(env.BMS_DISPATCHER_KEY || '');
  const received = request.headers.get('authorization') || '';
  return expected.length >= 32 && received === `Bearer ${expected}`;
}

function graphUrl(env, path) {
  const version = String(env.META_API_VERSION || DEFAULT_API_VERSION).replace(/^\/+|\/+$/g, '');
  return new URL(`${version}/${String(path).replace(/^\/+/, '')}`, 'https://graph.instagram.com/');
}

function validHttpsUrl(raw) {
  try {
    return new URL(String(raw)).protocol === 'https:';
  } catch {
    return false;
  }
}

function validMediaUrl(raw, mediaType) {
  try {
    const url = new URL(String(raw));
    if (url.protocol !== 'https:') return false;
    const path = url.pathname.toLowerCase();
    if (mediaType === 'VIDEO') return path.endsWith('.mp4') || path.endsWith('.mov');
    return path.endsWith('.jpg') || path.endsWith('.jpeg');
  } catch {
    return false;
  }
}

function listOfStrings(value, maximum) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  const result = [];
  for (const raw of value) {
    const item = String(raw || '').trim().replace(/^@/, '');
    if (!item || seen.has(item.toLowerCase())) continue;
    seen.add(item.toLowerCase());
    result.push(item);
    if (result.length >= maximum) break;
  }
  return result;
}

function normalizedPublishType(value) {
  const type = String(value || 'FEED').trim().toUpperCase();
  return ['FEED', 'STORY', 'REEL'].includes(type) ? type : null;
}

function hashtagAndMentionCounts(caption) {
  return {
    hashtags: (String(caption).match(/(^|\s)#[\p{L}\p{N}_]+/gu) || []).length,
    mentions: (String(caption).match(/(^|\s)@[A-Za-z0-9._]+/g) || []).length,
  };
}

function validateDraft(body) {
  if (!body || typeof body !== 'object') return 'Порожній запит';
  const publishType = normalizedPublishType(body.publish_type);
  if (!publishType) return 'Невідомий тип Instagram-публікації';
  const caption = String(body.caption || '');
  if (caption.length > CAPTION_LIMIT) return `Підпис перевищує ${CAPTION_LIMIT} символів`;
  const captionCounts = hashtagAndMentionCounts(caption);
  if (captionCounts.hashtags > 30) return 'У підписі може бути до 30 хештегів';
  if (captionCounts.mentions > 20) return 'У підписі може бути до 20 згадок';
  const media = Array.isArray(body.media) ? body.media : [];
  if (media.length < 1 || media.length > MAX_MEDIA) return `Потрібно від 1 до ${MAX_MEDIA} медіафайлів`;
  if (publishType === 'STORY' && media.length !== 1) return 'Одна Story підтримує рівно один медіафайл';
  if (publishType === 'REEL' && (media.length !== 1 || String(media[0]?.type || '').toUpperCase() !== 'VIDEO')) {
    return 'Reel потребує рівно один MP4/MOV відеофайл';
  }
  for (const item of media) {
    const type = String(item?.type || 'IMAGE').toUpperCase();
    if (!['IMAGE', 'VIDEO'].includes(type)) return 'Невідомий тип медіа';
    if (!validMediaUrl(item?.url, type)) {
      return type === 'VIDEO'
        ? 'Відео має бути публічним HTTPS .mp4 або .mov'
        : 'Фото має бути публічним HTTPS .jpg або .jpeg';
    }
    if (String(item?.alt_text || '').length > ALT_TEXT_LIMIT) {
      return `Alt text перевищує ${ALT_TEXT_LIMIT} символів`;
    }
    if (item?.alt_text && (type !== 'IMAGE' || publishType !== 'FEED')) {
      return 'Alt text підтримується лише для Feed-зображень';
    }
  }
  const collaborators = listOfStrings(body.collaborators, 4);
  if (collaborators.length > 3 || (Array.isArray(body.collaborators) && body.collaborators.length > 3)) {
    return 'Можна додати до 3 співавторів';
  }
  if (publishType === 'STORY' && collaborators.length) return 'Stories не підтримують співавторів';
  if (collaborators.some(username => !/^[A-Za-z0-9._]{1,30}$/.test(username))) {
    return 'Некоректне ім’я Instagram-співавтора';
  }
  const userTags = Array.isArray(body.user_tags) ? body.user_tags : [];
  if (userTags.length > 20) return 'Можна додати до 20 позначок користувачів';
  for (const tag of userTags) {
    const username = String(tag?.username || '').replace(/^@/, '');
    if (!/^[A-Za-z0-9._]{1,30}$/.test(username)) return 'Некоректна позначка Instagram-користувача';
    for (const key of ['x', 'y']) {
      if (tag?.[key] != null && (Number(tag[key]) < 0 || Number(tag[key]) > 1)) return 'Координати позначки мають бути від 0 до 1';
    }
  }
  const productTags = Array.isArray(body.product_tags) ? body.product_tags : [];
  if (productTags.length > 5) return 'На одному медіа може бути до 5 товарних позначок';
  if (publishType === 'STORY' && productTags.length) return 'Stories не підтримують товарні позначки через цей API';
  if (body.publish_at) {
    const publishAt = new Date(String(body.publish_at));
    if (Number.isNaN(publishAt.getTime())) return 'Некоректний publish_at';
    if (publishAt.getTime() <= Date.now()) return 'Запланований час уже минув';
    if (publishAt.getTime() > Date.now() + 365 * 24 * 60 * 60 * 1000) {
      return 'Розклад не може бути далі ніж на 365 днів';
    }
  }
  return null;
}

function bytesToBase64(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function base64ToBytes(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, character => character.charCodeAt(0));
}

function hexToBytes(value) {
  const normalized = String(value || '').trim();
  if (!/^[a-f0-9]{64}$/i.test(normalized)) {
    throw new Error('TOKEN_ENCRYPTION_KEY має бути 64-символьним hex-ключем');
  }
  return Uint8Array.from(normalized.match(/.{2}/g), pair => Number.parseInt(pair, 16));
}

async function encryptionKey(env) {
  return crypto.subtle.importKey('raw', hexToBytes(env.TOKEN_ENCRYPTION_KEY), 'AES-GCM', false, ['encrypt', 'decrypt']);
}

async function encryptToken(env, token) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encoded = new TextEncoder().encode(String(token));
  const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, await encryptionKey(env), encoded);
  return {
    ciphertext: bytesToBase64(new Uint8Array(ciphertext)),
    iv: bytesToBase64(iv),
  };
}

async function decryptToken(env, ciphertext, iv) {
  const plain = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: base64ToBytes(iv) },
    await encryptionKey(env),
    base64ToBytes(ciphertext),
  );
  return new TextDecoder().decode(plain);
}

async function sha256Hex(value) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

async function signatureMatches(rawBody, signatureHeader, appSecret) {
  const match = /^sha256=([a-f0-9]{64})$/i.exec(String(signatureHeader || ''));
  if (!match || !appSecret) return false;
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(String(appSecret)),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(rawBody));
  const actual = [...new Uint8Array(signature)].map(byte => byte.toString(16).padStart(2, '0')).join('');
  let difference = 0;
  for (let index = 0; index < actual.length; index += 1) {
    difference |= actual.charCodeAt(index) ^ match[1].toLowerCase().charCodeAt(index);
  }
  return difference === 0;
}

function oauthConfigured(env) {
  return Boolean(
    env.INSTAGRAM_APP_ID
      && env.INSTAGRAM_APP_SECRET
      && validHttpsUrl(env.INSTAGRAM_REDIRECT_URI)
      && env.TOKEN_ENCRYPTION_KEY,
  );
}

async function createOauthStart(env) {
  if (!env.INSTAGRAM_APP_ID || !validHttpsUrl(env.INSTAGRAM_REDIRECT_URI)) {
    return error('OAuth ще не завершено налаштований', 503);
  }
  const now = Date.now();
  const state = crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');
  await env.DB.prepare(`
    INSERT INTO oauth_states (state, created_at, expires_at, used_at)
    VALUES (?, ?, ?, NULL)
  `).bind(state, new Date(now).toISOString(), new Date(now + STATE_TTL_MS).toISOString()).run();

  const url = new URL('https://www.instagram.com/oauth/authorize');
  url.searchParams.set('force_reauth', 'true');
  url.searchParams.set('client_id', String(env.INSTAGRAM_APP_ID));
  url.searchParams.set('redirect_uri', String(env.INSTAGRAM_REDIRECT_URI));
  url.searchParams.set('state', state);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('scope', INSTAGRAM_LOGIN_SCOPES.join(','));
  return json({ ok: true, authorization_url: url.toString(), expires_in_seconds: STATE_TTL_MS / 1000 });
}

async function exchangeCode(env, code) {
  const form = new URLSearchParams();
  form.set('client_id', String(env.INSTAGRAM_APP_ID));
  form.set('client_secret', String(env.INSTAGRAM_APP_SECRET));
  form.set('grant_type', 'authorization_code');
  form.set('redirect_uri', String(env.INSTAGRAM_REDIRECT_URI));
  form.set('code', String(code));
  const response = await fetch('https://api.instagram.com/oauth/access_token', {
    method: 'POST',
    headers: { accept: 'application/json', 'content-type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.access_token) {
    throw new Error(data?.error_message || data?.error?.message || `Instagram OAuth HTTP ${response.status}`);
  }
  return data;
}

async function exchangeLongLivedInstagramToken(env, shortToken) {
  const url = new URL('https://graph.instagram.com/access_token');
  url.searchParams.set('grant_type', 'ig_exchange_token');
  url.searchParams.set('client_secret', String(env.INSTAGRAM_APP_SECRET));
  url.searchParams.set('access_token', String(shortToken));
  const response = await fetch(url, { headers: { accept: 'application/json' } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.access_token) {
    throw new Error(data?.error?.message || `Instagram long-lived token HTTP ${response.status}`);
  }
  return data;
}

async function fetchInstagramProfile(env, token, userId) {
  const profile = await graphRequest(env, token, 'me', {
    query: { fields: 'user_id,username' },
  });
  return {
    id: String(profile.user_id || profile.id || userId || ''),
    username: String(profile.username || ''),
  };
}

async function saveAccount(env, profile, token, tokenExpiresAt, scopes) {
  const secured = await encryptToken(env, token);
  const now = new Date().toISOString();
  await env.DB.prepare(`
    INSERT INTO instagram_accounts (
      id, ig_user_id, page_id, username, page_name, token_ciphertext, token_iv,
      token_expires_at, scopes, connected_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(ig_user_id) DO UPDATE SET
      page_id = excluded.page_id,
      username = excluded.username,
      page_name = excluded.page_name,
      token_ciphertext = excluded.token_ciphertext,
      token_iv = excluded.token_iv,
      token_expires_at = excluded.token_expires_at,
      scopes = excluded.scopes,
      updated_at = excluded.updated_at
  `).bind(
    crypto.randomUUID(), String(profile.id), 'instagram-login', String(profile.username || ''),
    'Direct Instagram Login', secured.ciphertext, secured.iv, tokenExpiresAt, scopes, now, now,
  ).run();
}

function htmlPage(title, message, ok) {
  const safeTitle = String(title).replace(/[<>&"]/g, '');
  const safeMessage = String(message).replace(/[<>&"]/g, '');
  return new Response(`<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>${safeTitle}</title><style>body{font:16px system-ui;margin:0;background:#f7f7fb;color:#20242c;display:grid;place-items:center;min-height:100vh}.card{max-width:560px;margin:24px;padding:32px;border:1px solid #e6e7ed;border-radius:18px;background:#fff;box-shadow:0 16px 50px #0001}.icon{font-size:30px}h1{font-size:22px;margin:12px 0 8px}p{line-height:1.55;color:#5b6170}</style><div class="card"><div class="icon">${ok ? '✅' : '⚠️'}</div><h1>${safeTitle}</h1><p>${safeMessage}</p><p>Цю вкладку можна закрити й повернутися до BMS.</p></div></html>`, {
    status: ok ? 200 : 400,
    headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' },
  });
}

async function oauthCallback(request, env) {
  if (!oauthConfigured(env)) return htmlPage('Instagram ще не підключено', 'У Cloudflare бракує захищених OAuth-налаштувань.', false);
  const url = new URL(request.url);
  if (url.searchParams.get('error')) {
    return htmlPage('Підключення скасовано', url.searchParams.get('error_description') || 'Meta не надала доступ.', false);
  }
  const state = String(url.searchParams.get('state') || '');
  const code = String(url.searchParams.get('code') || '');
  if (!state || !code) return htmlPage('Некоректна відповідь Meta', 'Не отримано code або state.', false);
  const record = await env.DB.prepare('SELECT * FROM oauth_states WHERE state = ?').bind(state).first();
  if (!record || record.used_at || new Date(record.expires_at).getTime() < Date.now()) {
    return htmlPage('Посилання вже недійсне', 'Почніть підключення з BMS ще раз.', false);
  }
  await env.DB.prepare('UPDATE oauth_states SET used_at = ? WHERE state = ? AND used_at IS NULL')
    .bind(new Date().toISOString(), state).run();
  try {
    const shortToken = await exchangeCode(env, code);
    const token = await exchangeLongLivedInstagramToken(env, shortToken.access_token);
    const profile = await fetchInstagramProfile(env, token.access_token, shortToken.user_id);
    const expected = String(env.EXPECTED_IG_USERNAME || '').replace(/^@/, '').toLowerCase();
    if (!profile.id || !profile.username) {
      throw new Error('Instagram не повернув професійний акаунт або username');
    }
    if (expected && profile.username.toLowerCase() !== expected) {
      throw new Error(`Авторизовано @${profile.username}, але BMS очікує @${expected}`);
    }
    const tokenExpiresAt = Number(token.expires_in) > 0
      ? new Date(Date.now() + Number(token.expires_in) * 1000).toISOString()
      : null;
    await saveAccount(env, profile, token.access_token, tokenExpiresAt, INSTAGRAM_LOGIN_SCOPES.join(','));
    return htmlPage('Instagram підключено', `@${profile.username} безпечно збережено в Cloudflare.`, true);
  } catch (reason) {
    return htmlPage('Не вдалося підключити Instagram', String(reason?.message || reason || 'Невідома помилка'), false);
  }
}

async function status(env) {
  const accounts = await env.DB.prepare(`
    SELECT ig_user_id, page_id, username, page_name, token_expires_at, connected_at, updated_at
      FROM instagram_accounts ORDER BY updated_at DESC
  `).all();
  return json({
    ok: true,
    app_id: env.INSTAGRAM_APP_ID || null,
    login_type: 'instagram',
    api_version: env.META_API_VERSION || DEFAULT_API_VERSION,
    oauth_configured: oauthConfigured(env),
    live_publish_enabled: String(env.INSTAGRAM_LIVE_ENABLED || '').toLowerCase() === 'true',
    publish_endpoint_available: true,
    scheduler: 'cron-every-minute',
    local_24h_limit: LOCAL_PUBLISHING_LIMIT,
    accounts: accounts.results || [],
  });
}

class MetaRequestError extends Error {
  constructor(message, { retriable = false, code = null, subcode = null, containerInvalid = false } = {}) {
    super(message);
    this.name = 'MetaRequestError';
    this.retriable = retriable;
    this.code = code;
    this.subcode = subcode;
    this.containerInvalid = containerInvalid;
  }
}

function parseJson(value, fallback) {
  try { return JSON.parse(String(value || '')); } catch { return fallback; }
}

function isoOrNull(raw) {
  if (!raw) return null;
  const parsed = new Date(String(raw));
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function liveEnabled(env) {
  return String(env.INSTAGRAM_LIVE_ENABLED || '').toLowerCase() === 'true';
}

function publicJob(row) {
  return {
    job_id: row.id,
    product_id: row.product_id,
    product_number: row.product_number,
    account_id: row.instagram_account_id,
    publish_type: row.publish_type,
    status: row.status,
    phase: row.phase,
    scheduled_at: row.publish_at,
    published_at: row.published_at,
    instagram_media_id: row.instagram_media_id,
    permalink: row.permalink,
    attempts: Number(row.attempts || 0),
    error: row.error,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

async function readJob(env, id) {
  return env.DB.prepare('SELECT * FROM instagram_jobs WHERE id = ?').bind(id).first();
}

async function accountFor(env, requested) {
  if (requested) {
    const exact = await env.DB.prepare(`
      SELECT * FROM instagram_accounts
       WHERE id = ? OR ig_user_id = ? OR lower(username) = lower(?)
       ORDER BY updated_at DESC LIMIT 1
    `).bind(String(requested), String(requested), String(requested).replace(/^@/, '')).first();
    if (exact) return exact;
  }
  const expected = String(env.EXPECTED_IG_USERNAME || '').replace(/^@/, '');
  if (expected) {
    const matched = await env.DB.prepare('SELECT * FROM instagram_accounts WHERE lower(username) = lower(?) ORDER BY updated_at DESC LIMIT 1')
      .bind(expected).first();
    if (matched) return matched;
  }
  return env.DB.prepare('SELECT * FROM instagram_accounts ORDER BY updated_at DESC LIMIT 1').first();
}

async function graphRequest(env, token, path, { method = 'GET', body = null, query = null } = {}) {
  const url = graphUrl(env, path);
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, String(value));
  }
  const form = new URLSearchParams();
  for (const [key, value] of Object.entries(body || {})) {
    if (value === undefined || value === null || value === '') continue;
    form.set(key, typeof value === 'object' ? JSON.stringify(value) : String(value));
  }
  const response = await fetch(url, {
    method,
    headers: {
      accept: 'application/json',
      authorization: `Bearer ${token}`,
      ...(body ? { 'content-type': 'application/x-www-form-urlencoded' } : {}),
    },
    body: body ? form.toString() : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data?.error) {
    const meta = data?.error || {};
    const code = Number(meta.code || response.status || 0);
    const subcode = meta.error_subcode || null;
    const retriable = Boolean(meta.is_transient || response.status >= 500 || [1, 2, 4, 17, 32, 613, 9007].includes(code));
    const detail = [code ? `code ${code}` : null, subcode ? `subcode ${subcode}` : null]
      .filter(Boolean).join(', ');
    throw new MetaRequestError(`${meta.message || `Meta HTTP ${response.status}`}${detail ? ` (${detail})` : ''}`, {
      retriable, code, subcode: meta.error_subcode || null,
    });
  }
  return data;
}

function optionalFields(target, source, keys) {
  for (const key of keys) {
    const value = source?.[key];
    if (value !== undefined && value !== null && value !== '' && value !== false
        && (!Array.isArray(value) || value.length)) target[key] = value;
  }
  return target;
}

function mediaContainerPayload(publishType, item, options, isCarouselItem = false) {
  const mediaType = String(item.type || 'IMAGE').toUpperCase();
  const payload = {};
  if (mediaType === 'VIDEO') {
    payload.video_url = item.url;
  } else {
    payload.image_url = item.url;
  }
  if (publishType === 'STORY') {
    payload.media_type = 'STORIES';
    optionalFields(payload, item, ['user_tags']);
    if (!payload.user_tags) optionalFields(payload, options, ['user_tags']);
    return payload;
  }
  if (publishType === 'REEL') {
    payload.media_type = 'REELS';
    payload.caption = String(options.caption || '');
    payload.share_to_feed = options.share_to_feed !== false;
    optionalFields(payload, options, [
      'collaborators', 'cover_url', 'audio_name', 'user_tags', 'location_id',
      'trial_params', 'branded_content_sponsor_ids', 'is_paid_partnership',
      'is_ai_generated',
    ]);
    return payload;
  }
  if (isCarouselItem) {
    payload.is_carousel_item = true;
    if (mediaType === 'VIDEO') payload.media_type = 'VIDEO';
    if (mediaType === 'IMAGE' && item.alt_text) payload.alt_text = item.alt_text;
    optionalFields(payload, item, ['user_tags', 'product_tags']);
    return payload;
  }
  if (mediaType === 'VIDEO') payload.media_type = 'VIDEO';
  payload.caption = String(options.caption || '');
  if (mediaType === 'IMAGE' && item.alt_text) payload.alt_text = item.alt_text;
  optionalFields(payload, options, [
    'collaborators', 'location_id', 'user_tags', 'product_tags',
    'branded_content_sponsor_ids', 'is_paid_partnership', 'is_ai_generated',
  ]);
  return payload;
}

function carouselParentPayload(childIds, options) {
  const payload = {
    media_type: 'CAROUSEL',
    // Instagram Login expects a comma-separated list, not a JSON array.
    children: childIds.join(','),
    caption: String(options.caption || ''),
  };
  optionalFields(payload, options, [
    'collaborators', 'location_id', 'product_tags',
    'branded_content_sponsor_ids', 'is_paid_partnership', 'is_ai_generated',
  ]);
  return payload;
}

async function publishingUsage(env, account, token) {
  const meta = await graphRequest(env, token, `${account.ig_user_id}/content_publishing_limit`, {
    query: { fields: 'quota_usage,config' },
  });
  const row = Array.isArray(meta.data) ? meta.data[0] || {} : meta;
  const local = await env.DB.prepare(`
    SELECT COUNT(*) AS count FROM instagram_jobs
     WHERE instagram_account_id = ? AND status = 'published' AND published_at >= ?
  `).bind(account.ig_user_id, new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()).first();
  return {
    meta: Number(row.quota_usage || 0),
    meta_total: Number(row.config?.quota_total || 0) || null,
    local: Number(local?.count || 0),
  };
}

async function accountCheck(env) {
  const account = await accountFor(env, null);
  if (!account) return error('Instagram-акаунт ще не підключено через OAuth', 503);
  try {
    const token = await decryptToken(env, account.token_ciphertext, account.token_iv);
    const profile = await graphRequest(env, token, account.ig_user_id, {
      query: { fields: 'user_id,username,account_type,media_count' },
    });
    const username = String(profile.username || account.username || '');
    const expected = String(env.EXPECTED_IG_USERNAME || '').replace(/^@/, '').toLowerCase();
    if (expected && username.toLowerCase() !== expected) {
      return error(`Підключено @${username}, але BMS очікує @${expected}`, 409);
    }
    const usage = await publishingUsage(env, account, token);
    return json({
      ok: true,
      account: {
        id: String(profile.user_id || profile.id || account.ig_user_id),
        username,
        account_type: profile.account_type || null,
        media_count: Number(profile.media_count || 0),
        token_expires_at: account.token_expires_at || null,
      },
      publishing_usage: usage,
      live_publish_enabled: liveEnabled(env),
    });
  } catch (reason) {
    return error(String(reason?.message || reason || 'Не вдалося перевірити Instagram-акаунт'), 502);
  }
}

async function assertPublishingCapacity(env, account, token) {
  const usage = await publishingUsage(env, account, token);
  if (usage.meta >= LOCAL_PUBLISHING_LIMIT || usage.local >= LOCAL_PUBLISHING_LIMIT) {
    throw new MetaRequestError(
      `Досягнуто консервативний добовий ліміт (${Math.max(usage.meta, usage.local)}/${LOCAL_PUBLISHING_LIMIT})`,
      { retriable: true, code: 613 },
    );
  }
  return usage;
}

async function claimJob(env, id) {
  const result = await env.DB.prepare(`
    UPDATE instagram_jobs SET status = 'processing', updated_at = ?
     WHERE id = ? AND status IN ('queued', 'scheduled', 'retrying')
  `).bind(new Date().toISOString(), id).run();
  return Number(result.meta?.changes || 0) === 1;
}

async function setJobPending(env, id, phase, delayMs, errorMessage = null) {
  const now = new Date().toISOString();
  await env.DB.prepare(`
    UPDATE instagram_jobs
       SET status = 'retrying', phase = ?, next_attempt_at = ?, error = ?, updated_at = ?
     WHERE id = ?
  `).bind(phase, new Date(Date.now() + delayMs).toISOString(), errorMessage, now, id).run();
}

async function containerStatuses(env, token, ids) {
  const values = [];
  for (const id of ids) {
    // Instagram Login exposes the documented status_code field for media
    // containers. Asking for the legacy `status` field makes the whole Graph
    // request fail with code 100 / "Invalid parameter" even when the uploaded
    // image itself is valid.
    const result = await graphRequest(env, token, id, { query: { fields: 'status_code' } });
    const statusCode = String(result.status_code || '').toUpperCase();
    if (['ERROR', 'EXPIRED'].includes(statusCode)) {
      throw new MetaRequestError(result.status || `Media container ${statusCode}`, {
        retriable: statusCode === 'EXPIRED', containerInvalid: true,
      });
    }
    values.push({ id, status_code: statusCode || 'IN_PROGRESS' });
  }
  return values;
}

async function createInitialContainers(env, row, account, token) {
  const media = parseJson(row.media_json, []);
  const options = { ...parseJson(row.options_json, {}), caption: row.caption };
  if (row.publish_type === 'FEED' && media.length > 1) {
    const children = parseJson(row.child_container_ids, []);
    for (let index = children.length; index < media.length; index += 1) {
      const created = await graphRequest(env, token, `${account.ig_user_id}/media`, {
        method: 'POST', body: mediaContainerPayload('FEED', media[index], options, true),
      });
      children.push(String(created.id));
      await env.DB.prepare(`
        UPDATE instagram_jobs SET child_container_ids = ?, updated_at = ? WHERE id = ?
      `).bind(JSON.stringify(children), new Date().toISOString(), row.id).run();
    }
    await setJobPending(env, row.id, 'children_created', 1000);
    return;
  }
  const created = await graphRequest(env, token, `${account.ig_user_id}/media`, {
    method: 'POST', body: mediaContainerPayload(row.publish_type, media[0], options, false),
  });
  await env.DB.prepare(`
    UPDATE instagram_jobs
       SET container_id = ?, phase = 'container_created', status = 'retrying',
           next_attempt_at = ?, error = NULL, updated_at = ?
     WHERE id = ?
  `).bind(String(created.id), new Date(Date.now() + 1000).toISOString(), new Date().toISOString(), row.id).run();
}

async function createCarouselParent(env, row, account, token, childIds) {
  const options = { ...parseJson(row.options_json, {}), caption: row.caption };
  const created = await graphRequest(env, token, `${account.ig_user_id}/media`, {
    method: 'POST', body: carouselParentPayload(childIds, options),
  });
  await env.DB.prepare(`
    UPDATE instagram_jobs
       SET container_id = ?, phase = 'container_created', status = 'retrying',
           next_attempt_at = ?, error = NULL, updated_at = ?
     WHERE id = ?
  `).bind(String(created.id), new Date(Date.now() + 1000).toISOString(), new Date().toISOString(), row.id).run();
}

async function publishContainer(env, row, account, token) {
  const result = await graphRequest(env, token, `${account.ig_user_id}/media_publish`, {
    method: 'POST', body: { creation_id: row.container_id },
  });
  const mediaId = String(result.id || '');
  let permalink = null;
  try {
    const media = await graphRequest(env, token, mediaId, { query: { fields: 'permalink' } });
    permalink = media.permalink || null;
  } catch { /* публікація вже успішна; permalink є лише зручним доповненням */ }
  const now = new Date().toISOString();
  await env.DB.prepare(`
    UPDATE instagram_jobs
       SET status = 'published', phase = 'published', instagram_media_id = ?,
           permalink = ?, published_at = ?, next_attempt_at = NULL,
           error = NULL, updated_at = ?
     WHERE id = ?
  `).bind(mediaId, permalink, now, now, row.id).run();
}

async function failJob(env, row, reason) {
  const attempts = Number(row.attempts || 0) + 1;
  const retriable = reason?.retriable !== false;
  const terminal = !retriable || attempts >= MAX_ATTEMPTS;
  const delay = RETRY_MINUTES[Math.min(attempts - 1, RETRY_MINUTES.length - 1)] * 60 * 1000;
  const reset = Boolean(reason?.containerInvalid);
  await env.DB.prepare(`
    UPDATE instagram_jobs
       SET status = ?, attempts = ?, next_attempt_at = ?, error = ?,
           phase = CASE WHEN ? THEN 'new' ELSE phase END,
           container_id = CASE WHEN ? THEN NULL ELSE container_id END,
           child_container_ids = CASE WHEN ? THEN '[]' ELSE child_container_ids END,
           updated_at = ?
     WHERE id = ?
  `).bind(
    terminal ? 'failed' : 'retrying', attempts,
    terminal ? null : new Date(Date.now() + delay).toISOString(),
    String(reason?.message || reason || 'Невідома помилка Meta').slice(0, 1200),
    reset ? 1 : 0, reset ? 1 : 0, reset ? 1 : 0,
    new Date().toISOString(), row.id,
  ).run();
}

async function processJob(env, id) {
  if (!await claimJob(env, id)) {
    const current = await readJob(env, id);
    return current ? publicJob(current) : null;
  }
  let row = await readJob(env, id);
  if (!row) throw new Error('Instagram job зник після блокування');
  try {
    const account = await accountFor(env, row.instagram_account_id);
    if (!account) throw new MetaRequestError('Instagram-акаунт не підключено', { retriable: false });
    const token = await decryptToken(env, account.token_ciphertext, account.token_iv);
    for (let step = 0; step < 4; step += 1) {
      row = await readJob(env, id);
      if (row.phase === 'new') {
        await assertPublishingCapacity(env, account, token);
        await createInitialContainers(env, row, account, token);
        continue;
      }
      if (row.phase === 'children_created') {
        const childIds = parseJson(row.child_container_ids, []);
        const media = parseJson(row.media_json, []);
        // Image containers are ready when /media returns an ID. Meta's status
        // endpoint is intended for asynchronously processed video containers.
        const videoIds = childIds.filter((_, index) => String(media[index]?.type || 'IMAGE').toUpperCase() === 'VIDEO');
        const statuses = videoIds.length ? await containerStatuses(env, token, videoIds) : [];
        if (!statuses.every(item => item.status_code === 'FINISHED')) {
          await setJobPending(env, id, 'children_created', CONTAINER_POLL_MS);
          break;
        }
        await createCarouselParent(env, row, account, token, childIds);
        continue;
      }
      if (row.phase === 'container_created') {
        const media = parseJson(row.media_json, []);
        const isCarouselParent = row.publish_type === 'FEED' && media.length > 1;
        const isVideo = media.some(item => String(item?.type || 'IMAGE').toUpperCase() === 'VIDEO');
        const statuses = isVideo && !isCarouselParent
          ? await containerStatuses(env, token, [row.container_id])
          : [];
        if (!statuses.every(item => item.status_code === 'FINISHED')) {
          await setJobPending(env, id, 'container_created', CONTAINER_POLL_MS);
          break;
        }
        await publishContainer(env, row, account, token);
        break;
      }
      break;
    }
  } catch (reason) {
    row = await readJob(env, id) || row;
    await failJob(env, row, reason);
  }
  return publicJob(await readJob(env, id));
}

async function createJob(request, env) {
  let body;
  try { body = await request.json(); } catch { return error('Очікується JSON'); }
  const problem = validateDraft(body);
  if (problem) return error(problem);
  if (!String(body.idempotency_key || '').trim()) return error('Немає idempotency_key');
  if (!Number.isInteger(Number(body.product_id)) || Number(body.product_id) <= 0) return error('Некоректний product_id');
  if (!liveEnabled(env)) return error('Жива Instagram-публікація вимкнена у Worker', 503);
  const account = await accountFor(env, body.account_id);
  if (!account) return error('Instagram-акаунт ще не підключено через OAuth', 503);
  const key = String(body.idempotency_key).trim().slice(0, 180);
  const existing = await env.DB.prepare('SELECT * FROM instagram_jobs WHERE idempotency_key = ?').bind(key).first();
  if (existing) return json({ ok: true, cached: true, ...publicJob(existing) });
  const publishAt = isoOrNull(body.publish_at);
  const now = new Date().toISOString();
  const id = crypto.randomUUID();
  const statusValue = publishAt ? 'scheduled' : 'queued';
  try {
    await env.DB.prepare(`
      INSERT INTO instagram_jobs (
        id, idempotency_key, instagram_account_id, product_id, product_number,
        publish_type, caption, media_json, options_json, publish_at, status,
        phase, attempts, next_attempt_at, child_container_ids, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 0, ?, '[]', ?, ?)
    `).bind(
      id, key, account.ig_user_id, Number(body.product_id), String(body.product_number || ''),
      normalizedPublishType(body.publish_type), String(body.caption || ''), JSON.stringify(body.media),
      JSON.stringify({
        collaborators: listOfStrings(body.collaborators, 3),
        user_tags: body.user_tags || [], product_tags: body.product_tags || [],
        location_id: body.location_id || null, cover_url: body.cover_url || null,
        audio_name: body.audio_name || null, share_to_feed: body.share_to_feed !== false,
        is_ai_generated: body.is_ai_generated === true,
        branded_content_sponsor_ids: body.branded_content_sponsor_ids || [],
        is_paid_partnership: body.is_paid_partnership === true,
      }), publishAt, statusValue, publishAt || now, now, now,
    ).run();
  } catch (reason) {
    const duplicate = await env.DB.prepare('SELECT * FROM instagram_jobs WHERE idempotency_key = ?').bind(key).first();
    if (duplicate) return json({ ok: true, cached: true, ...publicJob(duplicate) });
    throw reason;
  }
  if (publishAt) return json({ ok: true, ...publicJob(await readJob(env, id)) }, 202);
  return json({ ok: true, ...await processJob(env, id) }, 202);
}

async function cancelJob(env, id) {
  const now = new Date().toISOString();
  const result = await env.DB.prepare(`
    UPDATE instagram_jobs
       SET status = 'cancelled', phase = 'cancelled', next_attempt_at = NULL,
           error = NULL, updated_at = ?
     WHERE id = ?
       AND status IN ('queued', 'scheduled', 'retrying')
       AND phase = 'new'
       AND container_id IS NULL
       AND child_container_ids = '[]'
  `).bind(now, id).run();
  const row = await readJob(env, id);
  if (!row) return error('Job не знайдено', 404);
  if (Number(result.meta?.changes || 0) !== 1) {
    return error(
      row.status === 'published'
        ? 'Опублікований Instagram-допис API не дозволяє видалити'
        : 'Job уже почав створювати media container і не може бути безпечно скасований',
      409,
    );
  }
  return json({ ok: true, ...publicJob(row) });
}

async function rescheduleJob(request, env, id) {
  let body;
  try { body = await request.json(); } catch { return error('Очікується JSON'); }
  const publishAt = isoOrNull(body.publish_at);
  if (!publishAt) return error('Потрібна майбутня дата publish_at');
  const scheduled = new Date(publishAt).getTime();
  if (scheduled <= Date.now()) return error('Запланований час уже минув');
  if (scheduled > Date.now() + 365 * 24 * 60 * 60 * 1000) {
    return error('Розклад не може бути далі ніж на 365 днів');
  }
  const now = new Date().toISOString();
  const result = await env.DB.prepare(`
    UPDATE instagram_jobs
       SET publish_at = ?, next_attempt_at = ?, status = 'scheduled',
           error = NULL, updated_at = ?
     WHERE id = ?
       AND status IN ('queued', 'scheduled', 'retrying')
       AND phase = 'new'
       AND container_id IS NULL
       AND child_container_ids = '[]'
  `).bind(publishAt, publishAt, now, id).run();
  const row = await readJob(env, id);
  if (!row) return error('Job не знайдено', 404);
  if (Number(result.meta?.changes || 0) !== 1) {
    return error('Перенести можна лише job, який ще не почав створювати media container', 409);
  }
  return json({ ok: true, ...publicJob(row) });
}

async function processDue(env) {
  if (!liveEnabled(env)) return;
  const now = new Date().toISOString();
  const stale = new Date(Date.now() - 5 * 60 * 1000).toISOString();
  await env.DB.prepare(`
    UPDATE instagram_jobs
       SET status = 'retrying', next_attempt_at = ?, error = 'Відновлено після перерваного виконання', updated_at = ?
     WHERE status = 'processing' AND updated_at < ?
  `).bind(now, now, stale).run();
  const due = await env.DB.prepare(`
    SELECT id FROM instagram_jobs
     WHERE status IN ('queued', 'scheduled', 'retrying')
       AND COALESCE(next_attempt_at, publish_at, created_at) <= ?
     ORDER BY COALESCE(publish_at, created_at), created_at
     LIMIT ?
  `).bind(now, MAX_BATCH_PER_TICK).all();
  for (const item of due.results || []) await processJob(env, item.id);
}

async function webhook(request, env) {
  const url = new URL(request.url);
  if (request.method === 'GET') {
    const valid = url.searchParams.get('hub.mode') === 'subscribe'
      && url.searchParams.get('hub.verify_token') === String(env.META_WEBHOOK_VERIFY_TOKEN || '');
    return valid
      ? new Response(url.searchParams.get('hub.challenge') || '', { status: 200 })
      : new Response('Forbidden', { status: 403 });
  }
  const raw = await request.text();
  if (!await signatureMatches(raw, request.headers.get('x-hub-signature-256'), env.INSTAGRAM_APP_SECRET)) {
    return error('Некоректний підпис webhook', 401);
  }
  const hash = await sha256Hex(raw);
  await env.DB.prepare(`
    INSERT OR IGNORE INTO instagram_webhook_events (id, payload_hash, payload, received_at)
    VALUES (?, ?, ?, ?)
  `).bind(crypto.randomUUID(), hash, raw, new Date().toISOString()).run();
  return new Response('EVENT_RECEIVED', { status: 200 });
}

async function refreshExpiringTokens(env) {
  if (!oauthConfigured(env)) return;
  const refreshBefore = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
  const accounts = await env.DB.prepare(`
    SELECT * FROM instagram_accounts
     WHERE page_id = 'instagram-login'
       AND token_expires_at IS NOT NULL
       AND token_expires_at <= ?
  `).bind(refreshBefore).all();
  for (const account of accounts.results || []) {
    try {
      const currentToken = await decryptToken(env, account.token_ciphertext, account.token_iv);
      const url = new URL('https://graph.instagram.com/refresh_access_token');
      url.searchParams.set('grant_type', 'ig_refresh_token');
      url.searchParams.set('access_token', currentToken);
      const response = await fetch(url, { headers: { accept: 'application/json' } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.access_token) {
        throw new Error(data?.error?.message || `Instagram refresh HTTP ${response.status}`);
      }
      const secured = await encryptToken(env, data.access_token);
      const expiresAt = Number(data.expires_in) > 0
        ? new Date(Date.now() + Number(data.expires_in) * 1000).toISOString()
        : account.token_expires_at;
      await env.DB.prepare(`
        UPDATE instagram_accounts
           SET token_ciphertext = ?, token_iv = ?, token_expires_at = ?, updated_at = ?
         WHERE id = ?
      `).bind(secured.ciphertext, secured.iv, expiresAt, new Date().toISOString(), account.id).run();
    } catch (reason) {
      console.warn('Instagram token refresh failed', account.ig_user_id, String(reason?.message || reason));
    }
  }
}

async function cleanup(env) {
  const now = new Date().toISOString();
  const oldEvents = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
  const oldJobs = new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString();
  await env.DB.batch([
    env.DB.prepare('DELETE FROM oauth_states WHERE expires_at < ?').bind(now),
    env.DB.prepare('DELETE FROM instagram_webhook_events WHERE received_at < ?').bind(oldEvents),
    env.DB.prepare("DELETE FROM instagram_jobs WHERE status IN ('published', 'failed', 'cancelled') AND updated_at < ?").bind(oldJobs),
  ]);
}

export {
  decryptToken,
  encryptToken,
  carouselParentPayload,
  containerStatuses,
  hashtagAndMentionCounts,
  mediaContainerPayload,
  normalizedPublishType,
  signatureMatches,
  validMediaUrl,
  validateDraft,
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/health' && request.method === 'GET') {
      return json({
        ok: true,
        service: 'bms-instagram-dispatcher',
        api_version: env.META_API_VERSION || DEFAULT_API_VERSION,
        live_publish_enabled: liveEnabled(env),
      });
    }
    if (url.pathname === '/oauth/instagram/callback' && request.method === 'GET') return oauthCallback(request, env);
    if (url.pathname === '/webhooks/instagram' && ['GET', 'POST'].includes(request.method)) return webhook(request, env);
    if (!authorized(request, env)) return error('Не авторизовано', 401);
    if (url.pathname === '/v1/status' && request.method === 'GET') return status(env);
    if (url.pathname === '/v1/oauth/start' && request.method === 'POST') return createOauthStart(env);
    if (url.pathname === '/v1/account-check' && request.method === 'GET') return accountCheck(env);
    if (url.pathname === '/v1/validate-draft' && request.method === 'POST') {
      let body;
      try { body = await request.json(); } catch { return error('Очікується JSON'); }
      const problem = validateDraft(body);
      return problem ? error(problem) : json({ ok: true, live_publish_enabled: liveEnabled(env) });
    }
    if (url.pathname === '/v1/jobs' && request.method === 'POST') return createJob(request, env);
    const jobMatch = url.pathname.match(/^\/v1\/jobs\/([^/]+)$/);
    if (jobMatch && request.method === 'GET') {
      const row = await readJob(env, decodeURIComponent(jobMatch[1]));
      return row ? json({ ok: true, ...publicJob(row) }) : error('Job не знайдено', 404);
    }
    if (jobMatch && request.method === 'DELETE') {
      return cancelJob(env, decodeURIComponent(jobMatch[1]));
    }
    if (jobMatch && request.method === 'PATCH') {
      return rescheduleJob(request, env, decodeURIComponent(jobMatch[1]));
    }
    return error('Не знайдено', 404);
  },

  async scheduled(_event, env, context) {
    context.waitUntil(Promise.all([processDue(env), refreshExpiringTokens(env), cleanup(env)]));
  },
};
