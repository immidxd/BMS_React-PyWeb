// BMS Facebook Page dispatcher.
//
// Дзеркалить instagram-dispatcher, але Facebook Pages API — це ІНШИЙ API, а не
// той самий з іншим хостом:
//   • auth — Facebook Login (for Business), не Instagram Login: user token →
//     long-lived user token → Page access token із /me/accounts;
//   • публікація — не «media container + media_publish», а три різні механізми:
//     /photos (+ /feed для альбому), /photo_stories, /video_reels з resumable
//     upload через rupload.facebook.com;
//   • розклад — тримає Worker, а не Meta. FB має власний scheduled_publish_time,
//     але він фізично створює пост у Сторінці одразу (unpublished), і скасувати
//     його можна лише DELETE. Ми лишаємо ту саму семантику, що в Instagram:
//     доки не настав час, у Facebook НЕ існує нічого, тож cancel/reschedule —
//     локальні й безпечні.
const DEFAULT_API_VERSION = 'v23.0';
// Ліміт тексту поста Сторінки. BMS ніколи не підходить до нього близько, але
// валідація має бути за реальним лімітом Meta, а не за інстаграмівським 2200.
const MESSAGE_LIMIT = 63206;
const REEL_DESCRIPTION_LIMIT = 2200;
const MAX_MEDIA = 10;
const STATE_TTL_MS = 10 * 60 * 1000;
const MAX_ATTEMPTS = 5;
const MAX_BATCH_PER_TICK = 5;
// Meta явно обмежує Reels 30 публікаціями на 24 год і радить, щоб застосунок
// стежив за цим сам — надто якщо вміє планувати наперед (а BMS вміє). Тримаємо
// спільний консервативний ліміт по найсуворішому типу, а не по стрічці.
const LOCAL_PUBLISHING_LIMIT = 30;
const VIDEO_POLL_MS = 45 * 1000;
const RETRY_MINUTES = [1, 5, 15, 60, 180];
// Рівно ті три дозволи, що їх вимагає документація Pages/Reels Publishing API.
// `publish_video` сюди НЕ входить (перевірено в docs 2026-08-16): застосунок
// його не має, а зайвий scope в OAuth-діалозі — це відмова, а не «про запас».
const FACEBOOK_SCOPES = [
  'pages_show_list',
  'pages_read_engagement',
  'pages_manage_posts',
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

function apiVersion(env) {
  return String(env.META_API_VERSION || DEFAULT_API_VERSION).replace(/^\/+|\/+$/g, '');
}

function graphUrl(env, path) {
  return new URL(`${apiVersion(env)}/${String(path).replace(/^\/+/, '')}`, 'https://graph.facebook.com/');
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

function normalizedPublishType(value) {
  const type = String(value || 'FEED').trim().toUpperCase();
  return ['FEED', 'STORY', 'REEL'].includes(type) ? type : null;
}

function validateDraft(body) {
  if (!body || typeof body !== 'object') return 'Порожній запит';
  const publishType = normalizedPublishType(body.publish_type);
  if (!publishType) return 'Невідомий тип Facebook-публікації';
  const message = String(body.caption || '');
  if (message.length > MESSAGE_LIMIT) return `Текст перевищує ${MESSAGE_LIMIT} символів`;
  if (publishType === 'REEL' && message.length > REEL_DESCRIPTION_LIMIT) {
    return `Опис Reel перевищує ${REEL_DESCRIPTION_LIMIT} символів`;
  }
  const media = Array.isArray(body.media) ? body.media : [];
  if (media.length < 1 || media.length > MAX_MEDIA) return `Потрібно від 1 до ${MAX_MEDIA} медіафайлів`;
  if (publishType === 'STORY' && media.length !== 1) return 'Одна Story підтримує рівно один медіафайл';
  if (publishType === 'REEL' && (media.length !== 1 || String(media[0]?.type || '').toUpperCase() !== 'VIDEO')) {
    return 'Reel потребує рівно один MP4/MOV відеофайл';
  }
  for (const item of media) {
    const type = String(item?.type || 'IMAGE').toUpperCase();
    if (!['IMAGE', 'VIDEO'].includes(type)) return 'Невідомий тип медіа';
    if (publishType === 'FEED' && type === 'VIDEO') {
      return 'Відео у стрічку Сторінки публікується як Reel';
    }
    if (publishType === 'STORY' && type !== 'IMAGE') {
      return 'BMS готує Story як зображення 9:16';
    }
    if (!validMediaUrl(item?.url, type)) {
      return type === 'VIDEO'
        ? 'Відео має бути публічним HTTPS .mp4 або .mov'
        : 'Фото має бути публічним HTTPS .jpg або .jpeg';
    }
  }
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
    env.FACEBOOK_APP_ID
      && env.FACEBOOK_APP_SECRET
      && validHttpsUrl(env.FACEBOOK_REDIRECT_URI)
      && env.TOKEN_ENCRYPTION_KEY,
  );
}

async function createOauthStart(env) {
  if (!env.FACEBOOK_APP_ID || !validHttpsUrl(env.FACEBOOK_REDIRECT_URI)) {
    return error('OAuth ще не завершено налаштований', 503);
  }
  const now = Date.now();
  const state = crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');
  await env.DB.prepare(`
    INSERT INTO oauth_states (state, created_at, expires_at, used_at)
    VALUES (?, ?, ?, NULL)
  `).bind(state, new Date(now).toISOString(), new Date(now + STATE_TTL_MS).toISOString()).run();

  const url = new URL(`https://www.facebook.com/${apiVersion(env)}/dialog/oauth`);
  url.searchParams.set('client_id', String(env.FACEBOOK_APP_ID));
  url.searchParams.set('redirect_uri', String(env.FACEBOOK_REDIRECT_URI));
  url.searchParams.set('state', state);
  url.searchParams.set('response_type', 'code');
  // Facebook Login for Business віддає доступ через збережену конфігурацію
  // (config_id). Класичний Facebook Login очікує список scope. Підтримуємо
  // обидва шляхи, щоб не залежати від того, як саме заведено застосунок Meta.
  if (String(env.FACEBOOK_CONFIG_ID || '').trim()) {
    url.searchParams.set('config_id', String(env.FACEBOOK_CONFIG_ID).trim());
  } else {
    url.searchParams.set('scope', FACEBOOK_SCOPES.join(','));
  }
  return json({ ok: true, authorization_url: url.toString(), expires_in_seconds: STATE_TTL_MS / 1000 });
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
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(body ? { 'content-type': 'application/x-www-form-urlencoded' } : {}),
    },
    body: body ? form.toString() : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data?.error) {
    const meta = data?.error || {};
    const code = Number(meta.code || response.status || 0);
    const subcode = meta.error_subcode || null;
    const retriable = Boolean(
      meta.is_transient || response.status >= 500 || [1, 2, 4, 17, 32, 341, 613].includes(code),
    );
    const detail = [code ? `code ${code}` : null, subcode ? `subcode ${subcode}` : null]
      .filter(Boolean).join(', ');
    throw new MetaRequestError(`${meta.message || `Meta HTTP ${response.status}`}${detail ? ` (${detail})` : ''}`, {
      retriable, code, subcode,
    });
  }
  return data;
}

async function exchangeCode(env, code) {
  const url = graphUrl(env, 'oauth/access_token');
  url.searchParams.set('client_id', String(env.FACEBOOK_APP_ID));
  url.searchParams.set('client_secret', String(env.FACEBOOK_APP_SECRET));
  url.searchParams.set('redirect_uri', String(env.FACEBOOK_REDIRECT_URI));
  url.searchParams.set('code', String(code));
  const response = await fetch(url, { headers: { accept: 'application/json' } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.access_token) {
    throw new Error(data?.error?.message || `Facebook OAuth HTTP ${response.status}`);
  }
  return data;
}

async function exchangeLongLivedUserToken(env, shortToken) {
  const url = graphUrl(env, 'oauth/access_token');
  url.searchParams.set('grant_type', 'fb_exchange_token');
  url.searchParams.set('client_id', String(env.FACEBOOK_APP_ID));
  url.searchParams.set('client_secret', String(env.FACEBOOK_APP_SECRET));
  url.searchParams.set('fb_exchange_token', String(shortToken));
  const response = await fetch(url, { headers: { accept: 'application/json' } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.access_token) {
    throw new Error(data?.error?.message || `Facebook long-lived token HTTP ${response.status}`);
  }
  return data;
}

/** Обирає Сторінки за списком очікуваних (id або назва, через кому).
 *
 *  BMS публікує у КІЛЬКА Сторінок, тому підключаємо всі перелічені одразу. Але
 *  «всі, які знайшлися» — теж ні: назва в конфізі має збігтися, інакше це
 *  помилка. Мовчки опублікувати не в ту Сторінку неприпустимо, а порожній
 *  список очікуваних дозволений лише коли Сторінка рівно одна. */
function pickPages(pages, expected) {
  const list = Array.isArray(pages) ? pages : [];
  if (!list.length) throw new Error('Обліковий запис не адмініструє жодної Сторінки Facebook');
  const wanted = String(expected || '')
    .split(',').map(value => value.trim().toLowerCase()).filter(Boolean);
  if (!wanted.length) {
    if (list.length > 1) {
      throw new Error(`Доступно кілька Сторінок (${list.map(page => page.name).join(', ')}); вкажи EXPECTED_FB_PAGES`);
    }
    return list;
  }
  const matched = [];
  for (const name of wanted) {
    const page = list.find(item => String(item.id).toLowerCase() === name
      || String(item.name || '').trim().toLowerCase() === name);
    if (!page) {
      throw new Error(`Серед доступних Сторінок немає «${name}»: ${list.map(item => item.name).join(', ')}`);
    }
    if (!matched.some(item => String(item.id) === String(page.id))) matched.push(page);
  }
  return matched;
}

async function fetchPages(env, userToken) {
  const data = await graphRequest(env, userToken, 'me/accounts', {
    query: { fields: 'id,name,access_token,tasks' },
  });
  return Array.isArray(data.data) ? data.data : [];
}

async function saveAccount(env, page, pageToken, userToken, userTokenExpiresAt) {
  const securedPage = await encryptToken(env, pageToken);
  const securedUser = await encryptToken(env, userToken);
  const now = new Date().toISOString();
  await env.DB.prepare(`
    INSERT INTO facebook_accounts (
      id, page_id, page_name, page_token_ciphertext, page_token_iv,
      user_token_ciphertext, user_token_iv, user_token_expires_at,
      scopes, connected_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(page_id) DO UPDATE SET
      page_name = excluded.page_name,
      page_token_ciphertext = excluded.page_token_ciphertext,
      page_token_iv = excluded.page_token_iv,
      user_token_ciphertext = excluded.user_token_ciphertext,
      user_token_iv = excluded.user_token_iv,
      user_token_expires_at = excluded.user_token_expires_at,
      scopes = excluded.scopes,
      updated_at = excluded.updated_at
  `).bind(
    crypto.randomUUID(), String(page.id), String(page.name || ''),
    securedPage.ciphertext, securedPage.iv,
    securedUser.ciphertext, securedUser.iv, userTokenExpiresAt,
    (Array.isArray(page.tasks) ? page.tasks.join(',') : FACEBOOK_SCOPES.join(',')),
    now, now,
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
  if (!oauthConfigured(env)) return htmlPage('Facebook ще не підключено', 'У Cloudflare бракує захищених OAuth-налаштувань.', false);
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
    const longToken = await exchangeLongLivedUserToken(env, shortToken.access_token);
    const pages = await fetchPages(env, longToken.access_token);
    const picked = pickPages(pages, expectedPages(env));
    const expiresAt = Number(longToken.expires_in) > 0
      ? new Date(Date.now() + Number(longToken.expires_in) * 1000).toISOString()
      : null;
    for (const page of picked) {
      if (!page.access_token) {
        throw new Error(`Meta не повернула Page access token для «${page.name}»`);
      }
      await saveAccount(env, page, page.access_token, longToken.access_token, expiresAt);
    }
    const names = picked.map(page => `«${page.name}»`).join(', ');
    return htmlPage('Facebook підключено', `${picked.length > 1 ? 'Сторінки' : 'Сторінку'} ${names} безпечно збережено в Cloudflare.`, true);
  } catch (reason) {
    return htmlPage('Не вдалося підключити Facebook', String(reason?.message || reason || 'Невідома помилка'), false);
  }
}

function liveEnabled(env) {
  return String(env.FACEBOOK_LIVE_ENABLED || '').toLowerCase() === 'true';
}

async function status(env) {
  const accounts = await env.DB.prepare(`
    SELECT page_id, page_name, user_token_expires_at, connected_at, updated_at
      FROM facebook_accounts ORDER BY updated_at DESC
  `).all();
  return json({
    ok: true,
    app_id: env.FACEBOOK_APP_ID || null,
    login_type: 'facebook',
    api_version: apiVersion(env),
    oauth_configured: oauthConfigured(env),
    live_publish_enabled: liveEnabled(env),
    publish_endpoint_available: true,
    scheduler: 'cron-every-minute',
    local_24h_limit: LOCAL_PUBLISHING_LIMIT,
    accounts: accounts.results || [],
  });
}

/** Перевіряє App Secret, не проходячи весь OAuth і не розкриваючи значення.
 *
 *  `client_credentials` — єдиний спосіб спитати Meta «цей секрет чинний?» одним
 *  запитом. ⚠️ Токен, який вона повертає, має вигляд `{app_id}|{app_secret}`,
 *  тож повертати його НАЗОВНІ не можна за жодних обставин — лише прапорець.
 *  Без цієї перевірки помилковий секрет виявлявся аж на callback, після
 *  згорілого state і кількох кліків користувача. */
async function appSecretCheck(env) {
  if (!env.FACEBOOK_APP_ID || !env.FACEBOOK_APP_SECRET) {
    return json({ ok: false, app_secret_valid: false, error: 'App ID або App Secret не налаштовані' }, 503);
  }
  const url = graphUrl(env, 'oauth/access_token');
  url.searchParams.set('client_id', String(env.FACEBOOK_APP_ID));
  url.searchParams.set('client_secret', String(env.FACEBOOK_APP_SECRET));
  url.searchParams.set('grant_type', 'client_credentials');
  const response = await fetch(url, { headers: { accept: 'application/json' } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.access_token) {
    return json({
      ok: false,
      app_secret_valid: false,
      app_id: String(env.FACEBOOK_APP_ID),
      error: data?.error?.message || `Meta HTTP ${response.status}`,
    }, 200);
  }
  return json({ ok: true, app_secret_valid: true, app_id: String(env.FACEBOOK_APP_ID) });
}

class MetaRequestError extends Error {
  constructor(message, { retriable = false, code = null, subcode = null, mediaInvalid = false } = {}) {
    super(message);
    this.name = 'MetaRequestError';
    this.retriable = retriable;
    this.code = code;
    this.subcode = subcode;
    this.mediaInvalid = mediaInvalid;
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

function publicJob(row) {
  return {
    job_id: row.id,
    product_id: row.product_id,
    product_number: row.product_number,
    account_id: row.facebook_page_id,
    publish_type: row.publish_type,
    status: row.status,
    phase: row.phase,
    scheduled_at: row.publish_at,
    published_at: row.published_at,
    facebook_post_id: row.facebook_post_id,
    permalink: row.permalink,
    attempts: Number(row.attempts || 0),
    error: row.error,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

async function readJob(env, id) {
  return env.DB.prepare('SELECT * FROM facebook_jobs WHERE id = ?').bind(id).first();
}

/** Список очікуваних Сторінок. EXPECTED_FB_PAGE лишається як сумісність із
 *  однією Сторінкою — щоб старий конфіг не ламався мовчки. */
function expectedPages(env) {
  return String(env.EXPECTED_FB_PAGES || env.EXPECTED_FB_PAGE || '').trim();
}

async function allAccounts(env) {
  const rows = await env.DB.prepare('SELECT * FROM facebook_accounts ORDER BY page_name').all();
  return rows.results || [];
}

/** ⚠️ Якщо Сторінку запитали явно і її НЕ знайдено — це помилка, а не привід
 *  узяти якусь іншу. З двома підключеними Сторінками тихий fallback означав би
 *  публікацію не туди, і помітили б це вже в стрічці. */
async function accountFor(env, requested) {
  if (requested) {
    return env.DB.prepare(`
      SELECT * FROM facebook_accounts
       WHERE id = ? OR page_id = ? OR lower(page_name) = lower(?)
       ORDER BY updated_at DESC LIMIT 1
    `).bind(String(requested), String(requested), String(requested)).first();
  }
  const accounts = await allAccounts(env);
  return accounts.length === 1 ? accounts[0] : null;
}

async function pageToken(env, account) {
  return decryptToken(env, account.page_token_ciphertext, account.page_token_iv);
}

async function publishingUsage(env, account) {
  const local = await env.DB.prepare(`
    SELECT COUNT(*) AS count FROM facebook_jobs
     WHERE facebook_page_id = ? AND status = 'published' AND published_at >= ?
  `).bind(account.page_id, new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()).first();
  return { local: Number(local?.count || 0), limit: LOCAL_PUBLISHING_LIMIT };
}

async function accountCheck(env) {
  const accounts = await allAccounts(env);
  if (!accounts.length) return error('Сторінку Facebook ще не підключено через OAuth', 503);
  const checked = [];
  try {
    for (const account of accounts) {
      const token = await pageToken(env, account);
      const profile = await graphRequest(env, token, account.page_id, {
        query: { fields: 'id,name,username,fan_count,link' },
      });
      checked.push({
        id: String(profile.id || account.page_id),
        name: String(profile.name || account.page_name || ''),
        username: profile.username || null,
        followers: Number(profile.fan_count || 0),
        link: profile.link || null,
        user_token_expires_at: account.user_token_expires_at || null,
        publishing_usage: await publishingUsage(env, account),
      });
    }
    return json({
      ok: true,
      accounts: checked,
      // Сумісність зі старим однокористувацьким читанням відповіді.
      account: checked[0],
      live_publish_enabled: liveEnabled(env),
    });
  } catch (reason) {
    return error(String(reason?.message || reason || 'Не вдалося перевірити Сторінку Facebook'), 502);
  }
}

async function assertPublishingCapacity(env, account) {
  const usage = await publishingUsage(env, account);
  if (usage.local >= LOCAL_PUBLISHING_LIMIT) {
    throw new MetaRequestError(
      `Досягнуто консервативний добовий ліміт (${usage.local}/${LOCAL_PUBLISHING_LIMIT})`,
      { retriable: true, code: 613 },
    );
  }
  return usage;
}

async function claimJob(env, id) {
  const result = await env.DB.prepare(`
    UPDATE facebook_jobs SET status = 'processing', updated_at = ?
     WHERE id = ? AND status IN ('queued', 'scheduled', 'retrying')
  `).bind(new Date().toISOString(), id).run();
  return Number(result.meta?.changes || 0) === 1;
}

async function setJobPending(env, id, phase, delayMs, errorMessage = null) {
  const now = new Date().toISOString();
  await env.DB.prepare(`
    UPDATE facebook_jobs
       SET status = 'retrying', phase = ?, next_attempt_at = ?, error = ?, updated_at = ?
     WHERE id = ?
  `).bind(phase, new Date(Date.now() + delayMs).toISOString(), errorMessage, now, id).run();
}

/** Текст, який Meta очікує для стрічкового поста. Photo-endpoint називає його
 *  `caption`, feed-endpoint — `message`, Reels — `description`. */
function messageField(publishType) {
  return publishType === 'REEL' ? 'description' : 'message';
}

async function uploadUnpublishedPhoto(env, account, token, item) {
  const created = await graphRequest(env, token, `${account.page_id}/photos`, {
    method: 'POST',
    body: { url: item.url, published: 'false' },
  });
  if (!created.id) throw new MetaRequestError('Meta не повернула id завантаженого фото');
  return String(created.id);
}

/** FEED: одне фото публікується прямо через /photos із підписом; два і більше —
 *  спершу непубліковані фото, потім один пост /feed з attached_media. */
async function publishFeed(env, row, account, token) {
  const media = parseJson(row.media_json, []);
  const uploaded = parseJson(row.child_media_ids, []);
  if (media.length === 1) {
    const created = await graphRequest(env, token, `${account.page_id}/photos`, {
      method: 'POST',
      body: { url: media[0].url, caption: String(row.caption || ''), published: 'true' },
    });
    return String(created.post_id || created.id || '');
  }
  for (let index = uploaded.length; index < media.length; index += 1) {
    uploaded.push(await uploadUnpublishedPhoto(env, account, token, media[index]));
    await env.DB.prepare('UPDATE facebook_jobs SET child_media_ids = ?, updated_at = ? WHERE id = ?')
      .bind(JSON.stringify(uploaded), new Date().toISOString(), row.id).run();
  }
  const body = { message: String(row.caption || '') };
  uploaded.forEach((mediaId, index) => {
    body[`attached_media[${index}]`] = JSON.stringify({ media_fbid: mediaId });
  });
  const created = await graphRequest(env, token, `${account.page_id}/feed`, { method: 'POST', body });
  return String(created.post_id || created.id || '');
}

/** STORY: Meta вимагає спершу непубліковане фото, і аж потім /photo_stories. */
async function publishStory(env, row, account, token) {
  const media = parseJson(row.media_json, []);
  const uploaded = parseJson(row.child_media_ids, []);
  const photoId = uploaded[0] || await uploadUnpublishedPhoto(env, account, token, media[0]);
  if (!uploaded[0]) {
    await env.DB.prepare('UPDATE facebook_jobs SET child_media_ids = ?, updated_at = ? WHERE id = ?')
      .bind(JSON.stringify([photoId]), new Date().toISOString(), row.id).run();
  }
  const created = await graphRequest(env, token, `${account.page_id}/photo_stories`, {
    method: 'POST', body: { photo_id: photoId },
  });
  if (created.success === false) throw new MetaRequestError('Meta відхилила Story');
  return String(created.post_id || created.id || '');
}

/** REEL, крок 1: резервуємо video_id і просимо Meta забрати MP4 за URL.
 *  rupload — окремий хост, він НЕ приймає Bearer і НЕ приймає тіло-форму:
 *  авторизація тільки через `OAuth <token>`, а джерело — заголовок `file_url`. */
async function startReelUpload(env, row, account, token) {
  const started = await graphRequest(env, token, `${account.page_id}/video_reels`, {
    method: 'POST', body: { upload_phase: 'start' },
  });
  const videoId = String(started.video_id || '');
  const uploadUrl = String(started.upload_url || '')
    || `https://rupload.facebook.com/video-upload/${apiVersion(env)}/${videoId}`;
  if (!videoId) throw new MetaRequestError('Meta не повернула video_id для Reel');
  const media = parseJson(row.media_json, []);
  const response = await fetch(uploadUrl, {
    method: 'POST',
    headers: {
      Authorization: `OAuth ${token}`,
      file_url: String(media[0].url),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data?.error || data.success === false) {
    throw new MetaRequestError(
      data?.error?.message || `Не вдалося передати Reel у Meta (HTTP ${response.status})`,
      { retriable: response.status >= 500, mediaInvalid: response.status === 400 },
    );
  }
  await env.DB.prepare(`
    UPDATE facebook_jobs
       SET video_id = ?, phase = 'video_uploaded', status = 'retrying',
           next_attempt_at = ?, error = NULL, updated_at = ?
     WHERE id = ?
  `).bind(videoId, new Date(Date.now() + 5000).toISOString(), new Date().toISOString(), row.id).run();
}

/** REEL, крок 2: Meta кодує відео асинхронно; публікувати можна лише коли
 *  `status.video_status` = ready. */
async function reelReady(env, token, videoId) {
  const result = await graphRequest(env, token, videoId, { query: { fields: 'status' } });
  const state = String(result?.status?.video_status || '').toLowerCase();
  if (state === 'error') {
    throw new MetaRequestError(
      result?.status?.processing_phase?.error?.message || 'Meta не змогла обробити відео Reel',
      { mediaInvalid: true },
    );
  }
  return state === 'ready';
}

/** REEL, крок 3: фінальна публікація. */
async function finishReel(env, row, account, token) {
  const created = await graphRequest(env, token, `${account.page_id}/video_reels`, {
    method: 'POST',
    body: {
      video_id: row.video_id,
      upload_phase: 'finish',
      video_state: 'PUBLISHED',
      description: String(row.caption || ''),
    },
  });
  if (created.success === false) throw new MetaRequestError('Meta відхилила публікацію Reel');
  return String(created.post_id || row.video_id || '');
}

async function markPublished(env, row, token, postId) {
  let permalink = null;
  if (postId) {
    try {
      const post = await graphRequest(env, token, postId, { query: { fields: 'permalink_url' } });
      permalink = post.permalink_url || null;
    } catch { /* публікація вже успішна; permalink — зручне доповнення */ }
  }
  const now = new Date().toISOString();
  await env.DB.prepare(`
    UPDATE facebook_jobs
       SET status = 'published', phase = 'published', facebook_post_id = ?,
           permalink = ?, published_at = ?, next_attempt_at = NULL,
           error = NULL, updated_at = ?
     WHERE id = ?
  `).bind(postId || null, permalink, now, now, row.id).run();
}

async function failJob(env, row, reason) {
  const attempts = Number(row.attempts || 0) + 1;
  const retriable = reason?.retriable !== false;
  const terminal = !retriable || attempts >= MAX_ATTEMPTS;
  const delay = RETRY_MINUTES[Math.min(attempts - 1, RETRY_MINUTES.length - 1)] * 60 * 1000;
  const reset = Boolean(reason?.mediaInvalid);
  await env.DB.prepare(`
    UPDATE facebook_jobs
       SET status = ?, attempts = ?, next_attempt_at = ?, error = ?,
           phase = CASE WHEN ? THEN 'new' ELSE phase END,
           video_id = CASE WHEN ? THEN NULL ELSE video_id END,
           child_media_ids = CASE WHEN ? THEN '[]' ELSE child_media_ids END,
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
  if (!row) throw new Error('Facebook job зник після блокування');
  try {
    const account = await accountFor(env, row.facebook_page_id);
    if (!account) throw new MetaRequestError('Сторінку Facebook не підключено', { retriable: false });
    const token = await pageToken(env, account);
    for (let step = 0; step < 4; step += 1) {
      row = await readJob(env, id);
      if (row.publish_type === 'REEL') {
        if (row.phase === 'new') {
          await assertPublishingCapacity(env, account);
          await startReelUpload(env, row, account, token);
          continue;
        }
        if (row.phase === 'video_uploaded') {
          if (!await reelReady(env, token, row.video_id)) {
            await setJobPending(env, id, 'video_uploaded', VIDEO_POLL_MS);
            break;
          }
          await markPublished(env, row, token, await finishReel(env, row, account, token));
          break;
        }
        break;
      }
      if (row.phase === 'new') {
        await assertPublishingCapacity(env, account);
        const postId = row.publish_type === 'STORY'
          ? await publishStory(env, row, account, token)
          : await publishFeed(env, row, account, token);
        await markPublished(env, row, token, postId);
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
  if (!liveEnabled(env)) return error('Жива Facebook-публікація вимкнена у Worker', 503);
  const connected = await allAccounts(env);
  if (!connected.length) return error('Сторінку Facebook ще не підключено через OAuth', 503);
  if (!body.account_id && connected.length > 1) {
    return error('Підключено кілька Сторінок — job має явно вказати account_id', 400);
  }
  const account = await accountFor(env, body.account_id);
  if (!account) return error(`Сторінку «${body.account_id}» не підключено`, 404);
  const key = String(body.idempotency_key).trim().slice(0, 180);
  const existing = await env.DB.prepare('SELECT * FROM facebook_jobs WHERE idempotency_key = ?').bind(key).first();
  if (existing) return json({ ok: true, cached: true, ...publicJob(existing) });
  const publishAt = isoOrNull(body.publish_at);
  const now = new Date().toISOString();
  const id = crypto.randomUUID();
  const statusValue = publishAt ? 'scheduled' : 'queued';
  try {
    await env.DB.prepare(`
      INSERT INTO facebook_jobs (
        id, idempotency_key, facebook_page_id, product_id, product_number,
        publish_type, caption, media_json, options_json, publish_at, status,
        phase, attempts, next_attempt_at, child_media_ids, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 0, ?, '[]', ?, ?)
    `).bind(
      id, key, account.page_id, Number(body.product_id), String(body.product_number || ''),
      normalizedPublishType(body.publish_type), String(body.caption || ''), JSON.stringify(body.media),
      JSON.stringify({ link: body.link || null }),
      publishAt, statusValue, publishAt || now, now, now,
    ).run();
  } catch (reason) {
    const duplicate = await env.DB.prepare('SELECT * FROM facebook_jobs WHERE idempotency_key = ?').bind(key).first();
    if (duplicate) return json({ ok: true, cached: true, ...publicJob(duplicate) });
    throw reason;
  }
  if (publishAt) return json({ ok: true, ...publicJob(await readJob(env, id)) }, 202);
  return json({ ok: true, ...await processJob(env, id) }, 202);
}

async function cancelJob(env, id) {
  const now = new Date().toISOString();
  const result = await env.DB.prepare(`
    UPDATE facebook_jobs
       SET status = 'cancelled', phase = 'cancelled', next_attempt_at = NULL,
           error = NULL, updated_at = ?
     WHERE id = ?
       AND status IN ('queued', 'scheduled', 'retrying')
       AND phase = 'new'
       AND video_id IS NULL
       AND child_media_ids = '[]'
  `).bind(now, id).run();
  const row = await readJob(env, id);
  if (!row) return error('Job не знайдено', 404);
  if (Number(result.meta?.changes || 0) !== 1) {
    return error(
      row.status === 'published'
        ? 'Опублікований допис Сторінки треба прибирати вручну у Facebook'
        : 'Job уже почав завантажувати медіа в Meta і не може бути безпечно скасований',
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
    UPDATE facebook_jobs
       SET publish_at = ?, next_attempt_at = ?, status = 'scheduled',
           error = NULL, updated_at = ?
     WHERE id = ?
       AND status IN ('queued', 'scheduled', 'retrying')
       AND phase = 'new'
       AND video_id IS NULL
       AND child_media_ids = '[]'
  `).bind(publishAt, publishAt, now, id).run();
  const row = await readJob(env, id);
  if (!row) return error('Job не знайдено', 404);
  if (Number(result.meta?.changes || 0) !== 1) {
    return error('Перенести можна лише job, який ще не почав завантажувати медіа', 409);
  }
  return json({ ok: true, ...publicJob(row) });
}

async function processDue(env) {
  if (!liveEnabled(env)) return;
  const now = new Date().toISOString();
  const stale = new Date(Date.now() - 5 * 60 * 1000).toISOString();
  await env.DB.prepare(`
    UPDATE facebook_jobs
       SET status = 'retrying', next_attempt_at = ?, error = 'Відновлено після перерваного виконання', updated_at = ?
     WHERE status = 'processing' AND updated_at < ?
  `).bind(now, now, stale).run();
  const due = await env.DB.prepare(`
    SELECT id FROM facebook_jobs
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
  if (!await signatureMatches(raw, request.headers.get('x-hub-signature-256'), env.FACEBOOK_APP_SECRET)) {
    return error('Некоректний підпис webhook', 401);
  }
  const hash = await sha256Hex(raw);
  await env.DB.prepare(`
    INSERT OR IGNORE INTO facebook_webhook_events (id, payload_hash, payload, received_at)
    VALUES (?, ?, ?, ?)
  `).bind(crypto.randomUUID(), hash, raw, new Date().toISOString()).run();
  return new Response('EVENT_RECEIVED', { status: 200 });
}

/** Page token живе доти, доки живий user token, з якого його отримано. Тому
 *  оновлюємо саме user token і ПЕРЕвидаємо з нього Page token. */
async function refreshExpiringTokens(env) {
  if (!oauthConfigured(env)) return;
  const refreshBefore = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
  const accounts = await env.DB.prepare(`
    SELECT * FROM facebook_accounts
     WHERE user_token_expires_at IS NOT NULL AND user_token_expires_at <= ?
  `).bind(refreshBefore).all();
  for (const account of accounts.results || []) {
    try {
      const currentToken = await decryptToken(env, account.user_token_ciphertext, account.user_token_iv);
      const refreshed = await exchangeLongLivedUserToken(env, currentToken);
      const pages = await fetchPages(env, refreshed.access_token);
      const page = pages.find(item => String(item.id) === String(account.page_id));
      if (!page?.access_token) throw new Error('Сторінка більше не доступна для цього облікового запису');
      const expiresAt = Number(refreshed.expires_in) > 0
        ? new Date(Date.now() + Number(refreshed.expires_in) * 1000).toISOString()
        : account.user_token_expires_at;
      await saveAccount(env, page, page.access_token, refreshed.access_token, expiresAt);
    } catch (reason) {
      console.warn('Facebook token refresh failed', account.page_id, String(reason?.message || reason));
    }
  }
}

async function cleanup(env) {
  const now = new Date().toISOString();
  const oldEvents = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
  const oldJobs = new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString();
  await env.DB.batch([
    env.DB.prepare('DELETE FROM oauth_states WHERE expires_at < ?').bind(now),
    env.DB.prepare('DELETE FROM facebook_webhook_events WHERE received_at < ?').bind(oldEvents),
    env.DB.prepare("DELETE FROM facebook_jobs WHERE status IN ('published', 'failed', 'cancelled') AND updated_at < ?").bind(oldJobs),
  ]);
}

export {
  decryptToken,
  encryptToken,
  messageField,
  normalizedPublishType,
  pickPages,
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
        service: 'bms-facebook-dispatcher',
        api_version: apiVersion(env),
        live_publish_enabled: liveEnabled(env),
      });
    }
    if (url.pathname === '/oauth/facebook/callback' && request.method === 'GET') return oauthCallback(request, env);
    if (url.pathname === '/webhooks/facebook' && ['GET', 'POST'].includes(request.method)) return webhook(request, env);
    if (!authorized(request, env)) return error('Не авторизовано', 401);
    if (url.pathname === '/v1/status' && request.method === 'GET') return status(env);
    if (url.pathname === '/v1/oauth/start' && request.method === 'POST') return createOauthStart(env);
    if (url.pathname === '/v1/account-check' && request.method === 'GET') return accountCheck(env);
    if (url.pathname === '/v1/app-check' && request.method === 'GET') return appSecretCheck(env);
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
