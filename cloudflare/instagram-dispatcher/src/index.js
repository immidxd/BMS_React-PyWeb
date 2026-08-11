const DEFAULT_API_VERSION = 'v25.0';
const CAPTION_LIMIT = 2200;
const MAX_MEDIA = 10;
const STATE_TTL_MS = 10 * 60 * 1000;

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
  return new URL(`${version}/${String(path).replace(/^\/+/, '')}`, 'https://graph.facebook.com/');
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

function validateDraft(body) {
  if (!body || typeof body !== 'object') return 'Порожній запит';
  const caption = String(body.caption || '');
  if (caption.length > CAPTION_LIMIT) return `Підпис перевищує ${CAPTION_LIMIT} символів`;
  const media = Array.isArray(body.media) ? body.media : [];
  if (media.length < 1 || media.length > MAX_MEDIA) return `Потрібно від 1 до ${MAX_MEDIA} медіафайлів`;
  for (const item of media) {
    const type = String(item?.type || 'IMAGE').toUpperCase();
    if (!['IMAGE', 'VIDEO'].includes(type)) return 'Невідомий тип медіа';
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
    env.META_APP_ID
      && env.META_APP_SECRET
      && env.META_LOGIN_CONFIG_ID
      && validHttpsUrl(env.META_REDIRECT_URI)
      && env.TOKEN_ENCRYPTION_KEY,
  );
}

async function createOauthStart(env) {
  if (!env.META_APP_ID || !env.META_LOGIN_CONFIG_ID || !validHttpsUrl(env.META_REDIRECT_URI)) {
    return error('OAuth ще не завершено налаштований', 503);
  }
  const now = Date.now();
  const state = crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');
  await env.DB.prepare(`
    INSERT INTO oauth_states (state, created_at, expires_at, used_at)
    VALUES (?, ?, ?, NULL)
  `).bind(state, new Date(now).toISOString(), new Date(now + STATE_TTL_MS).toISOString()).run();

  const url = new URL(`https://www.facebook.com/${env.META_API_VERSION || DEFAULT_API_VERSION}/dialog/oauth`);
  url.searchParams.set('client_id', String(env.META_APP_ID));
  url.searchParams.set('redirect_uri', String(env.META_REDIRECT_URI));
  url.searchParams.set('state', state);
  url.searchParams.set('config_id', String(env.META_LOGIN_CONFIG_ID));
  url.searchParams.set('response_type', 'code');
  return json({ ok: true, authorization_url: url.toString(), expires_in_seconds: STATE_TTL_MS / 1000 });
}

async function exchangeCode(env, code) {
  const url = graphUrl(env, 'oauth/access_token');
  url.searchParams.set('client_id', String(env.META_APP_ID));
  url.searchParams.set('client_secret', String(env.META_APP_SECRET));
  url.searchParams.set('redirect_uri', String(env.META_REDIRECT_URI));
  url.searchParams.set('code', String(code));
  const response = await fetch(url, { headers: { accept: 'application/json' } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.access_token) {
    throw new Error(data?.error?.message || `Meta OAuth HTTP ${response.status}`);
  }
  return data;
}

async function fetchManagedPages(env, userToken) {
  const url = graphUrl(env, 'me/accounts');
  url.searchParams.set('fields', 'id,name,access_token,tasks,instagram_business_account{id,username,name,profile_picture_url}');
  url.searchParams.set('limit', '100');
  url.searchParams.set('access_token', userToken);
  const response = await fetch(url, { headers: { accept: 'application/json' } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data?.error?.message || `Meta Pages HTTP ${response.status}`);
  return Array.isArray(data.data) ? data.data : [];
}

async function saveAccount(env, page, tokenExpiresAt, scopes) {
  const instagram = page.instagram_business_account;
  const secured = await encryptToken(env, page.access_token);
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
    crypto.randomUUID(), String(instagram.id), String(page.id), String(instagram.username || ''),
    String(page.name || ''), secured.ciphertext, secured.iv, tokenExpiresAt, scopes, now, now,
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
    const token = await exchangeCode(env, code);
    const pages = await fetchManagedPages(env, token.access_token);
    const expected = String(env.EXPECTED_IG_USERNAME || '').replace(/^@/, '').toLowerCase();
    const candidates = pages.filter(page => page.access_token && page.instagram_business_account?.id);
    const selected = candidates.find(page => String(page.instagram_business_account.username || '').toLowerCase() === expected)
      || (candidates.length === 1 ? candidates[0] : null);
    if (!selected) {
      throw new Error(expected
        ? `Серед доступних сторінок не знайдено @${expected}`
        : 'Не знайдено однозначно підключений професійний Instagram-акаунт');
    }
    const expiresAt = token.expires_in
      ? new Date(Date.now() + Number(token.expires_in) * 1000).toISOString()
      : null;
    await saveAccount(env, selected, expiresAt, String(token.granted_scopes || ''));
    return htmlPage('Instagram підключено', `@${selected.instagram_business_account.username || selected.instagram_business_account.id} безпечно збережено в Cloudflare. Публікація поки вимкнена.`, true);
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
    app_id: env.META_APP_ID || null,
    oauth_configured: oauthConfigured(env),
    live_publish_enabled: String(env.INSTAGRAM_LIVE_ENABLED || '').toLowerCase() === 'true',
    publish_endpoint_available: false,
    accounts: accounts.results || [],
  });
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
  if (!await signatureMatches(raw, request.headers.get('x-hub-signature-256'), env.META_APP_SECRET)) {
    return error('Некоректний підпис webhook', 401);
  }
  const hash = await sha256Hex(raw);
  await env.DB.prepare(`
    INSERT OR IGNORE INTO instagram_webhook_events (id, payload_hash, payload, received_at)
    VALUES (?, ?, ?, ?)
  `).bind(crypto.randomUUID(), hash, raw, new Date().toISOString()).run();
  return new Response('EVENT_RECEIVED', { status: 200 });
}

async function cleanup(env) {
  const now = new Date().toISOString();
  const oldEvents = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
  await env.DB.batch([
    env.DB.prepare('DELETE FROM oauth_states WHERE expires_at < ?').bind(now),
    env.DB.prepare('DELETE FROM instagram_webhook_events WHERE received_at < ?').bind(oldEvents),
  ]);
}

export {
  decryptToken,
  encryptToken,
  signatureMatches,
  validMediaUrl,
  validateDraft,
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/health' && request.method === 'GET') {
      return json({ ok: true, service: 'bms-instagram-dispatcher', live_publish_enabled: false });
    }
    if (url.pathname === '/oauth/callback' && request.method === 'GET') return oauthCallback(request, env);
    if (url.pathname === '/webhooks/instagram' && ['GET', 'POST'].includes(request.method)) return webhook(request, env);
    if (!authorized(request, env)) return error('Не авторизовано', 401);
    if (url.pathname === '/v1/status' && request.method === 'GET') return status(env);
    if (url.pathname === '/v1/oauth/start' && request.method === 'POST') return createOauthStart(env);
    if (url.pathname === '/v1/validate-draft' && request.method === 'POST') {
      let body;
      try { body = await request.json(); } catch { return error('Очікується JSON'); }
      const problem = validateDraft(body);
      return problem ? error(problem) : json({ ok: true, live_publish_enabled: false });
    }
    // Свідомо немає /publish та /jobs: перший live-тест потребує окремого
    // професійного тестового акаунта й явного підтвердження користувача.
    return error('Не знайдено', 404);
  },

  async scheduled(_event, env, context) {
    context.waitUntil(cleanup(env));
  },
};
