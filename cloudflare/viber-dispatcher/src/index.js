const VIBER_POST_URL = 'https://chatapi.viber.com/pa/post';
const VIBER_ACCOUNT_URL = 'https://chatapi.viber.com/pa/get_account_info';
const VIBER_WEBHOOK_URL = 'https://chatapi.viber.com/pa/set_webhook';
const CAPTION_LIMIT = 768;
const MAX_ATTEMPTS = 5;
const MAX_BATCH_PER_TICK = 10;
const BETWEEN_POSTS_MS = 1100;

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
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

function validJpegUrl(raw) {
  try {
    const url = new URL(String(raw));
    return url.protocol === 'https:' && url.pathname.toLowerCase().endsWith('.jpeg');
  } catch {
    return false;
  }
}

function isoOrNull(raw) {
  if (!raw) return null;
  const parsed = new Date(String(raw));
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function validateJob(body) {
  if (!body || typeof body !== 'object') return 'Порожній запит';
  if (!String(body.idempotency_key || '').trim()) return 'Немає idempotency_key';
  if (!Number.isInteger(Number(body.product_id)) || Number(body.product_id) <= 0) return 'Некоректний product_id';
  const caption = String(body.caption || '');
  if (!caption.trim()) return 'Підпис порожній';
  if (caption.length > CAPTION_LIMIT) return `Підпис перевищує ${CAPTION_LIMIT} символів`;
  if (!validJpegUrl(body.media_url)) return 'media_url має бути публічним HTTPS .jpeg';
  if (!validJpegUrl(body.thumbnail_url)) return 'thumbnail_url має бути публічним HTTPS .jpeg';
  if (body.publish_at && !isoOrNull(body.publish_at)) return 'Некоректний publish_at';
  if (body.publish_at && new Date(body.publish_at).getTime() <= Date.now()) return 'Запланований час уже минув';
  if (body.publish_at && new Date(body.publish_at).getTime() > Date.now() + 365 * 24 * 60 * 60 * 1000) return 'Розклад не може бути далі ніж на 365 днів';
  return null;
}

function publicJob(row) {
  return {
    job_id: row.id,
    product_id: row.product_id,
    product_number: row.product_number,
    status: row.status,
    scheduled_at: row.publish_at,
    published_at: row.published_at,
    message_token: row.message_token,
    error: row.error,
  };
}

async function readJob(env, id) {
  return env.DB.prepare('SELECT * FROM viber_jobs WHERE id = ?').bind(id).first();
}

async function claimJob(env, id) {
  const result = await env.DB.prepare(`
    UPDATE viber_jobs
       SET status = 'processing', updated_at = ?
     WHERE id = ? AND status IN ('queued', 'scheduled', 'retrying')
  `).bind(new Date().toISOString(), id).run();
  return Number(result.meta?.changes || 0) === 1;
}

async function publishJob(env, id) {
  if (!await claimJob(env, id)) {
    const current = await readJob(env, id);
    return current ? publicJob(current) : null;
  }
  const row = await readJob(env, id);
  if (!row) throw new Error('Job зник після блокування');
  const attempts = Number(row.attempts || 0) + 1;
  try {
    if (!env.VIBER_CHANNEL_TOKEN || !env.VIBER_CHANNEL_SENDER_ID) {
      throw new Error('Viber secrets не налаштовані у Worker');
    }
    const response = await fetch(VIBER_POST_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        auth_token: env.VIBER_CHANNEL_TOKEN,
        from: env.VIBER_CHANNEL_SENDER_ID,
        type: 'picture',
        text: row.caption,
        media: row.media_url,
        thumbnail: row.thumbnail_url,
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || Number(result.status) !== 0) {
      throw new Error(result.status_message || `Viber HTTP ${response.status}`);
    }
    const now = new Date().toISOString();
    await env.DB.prepare(`
      UPDATE viber_jobs
         SET status = 'published', attempts = ?, message_token = ?, error = NULL,
             published_at = ?, updated_at = ?, next_attempt_at = NULL
       WHERE id = ?
    `).bind(attempts, String(result.message_token || '') || null, now, now, id).run();
  } catch (reason) {
    const message = String(reason?.message || reason || 'Невідома помилка').slice(0, 1000);
    const terminal = attempts >= MAX_ATTEMPTS;
    const delayMinutes = [1, 5, 15, 60, 180][Math.min(attempts - 1, 4)];
    const next = terminal ? null : new Date(Date.now() + delayMinutes * 60 * 1000).toISOString();
    await env.DB.prepare(`
      UPDATE viber_jobs
         SET status = ?, attempts = ?, next_attempt_at = ?, error = ?, updated_at = ?
       WHERE id = ?
    `).bind(terminal ? 'failed' : 'retrying', attempts, next, message, new Date().toISOString(), id).run();
  }
  return publicJob(await readJob(env, id));
}

async function createJob(request, env) {
  let body;
  try { body = await request.json(); } catch { return error('Очікується JSON'); }
  const problem = validateJob(body);
  if (problem) return error(problem);
  const key = String(body.idempotency_key).trim().slice(0, 160);
  const existing = await env.DB.prepare('SELECT * FROM viber_jobs WHERE idempotency_key = ?').bind(key).first();
  if (existing) return json({ ok: true, cached: true, ...publicJob(existing) });

  const now = new Date().toISOString();
  const publishAt = isoOrNull(body.publish_at);
  // Наявний publish_at ніколи не перетворюємо мовчки на «зараз».
  const scheduled = !!publishAt;
  const id = crypto.randomUUID();
  try {
    await env.DB.prepare(`
      INSERT INTO viber_jobs (
        id, idempotency_key, product_id, product_number, channel_title,
        caption, media_url, thumbnail_url, publish_at, status,
        attempts, next_attempt_at, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
    `).bind(
      id, key, Number(body.product_id), String(body.product_number || ''),
      String(body.channel_title || ''), String(body.caption), String(body.media_url),
      String(body.thumbnail_url), publishAt, scheduled ? 'scheduled' : 'queued',
      scheduled ? publishAt : now, now, now,
    ).run();
  } catch (reason) {
    // Паралельний повтор міг виграти UNIQUE. Повертаємо його як успішний кеш.
    const duplicate = await env.DB.prepare('SELECT * FROM viber_jobs WHERE idempotency_key = ?').bind(key).first();
    if (duplicate) return json({ ok: true, cached: true, ...publicJob(duplicate) });
    throw reason;
  }
  if (scheduled) return json({ ok: true, ...publicJob(await readJob(env, id)) }, 202);
  return json({ ok: true, ...await publishJob(env, id) });
}

async function processDue(env) {
  // Worker, який перервався посеред fetch, не залишає job назавжди завислим.
  const stale = new Date(Date.now() - 5 * 60 * 1000).toISOString();
  await env.DB.prepare(`
    UPDATE viber_jobs
       SET status = 'retrying', next_attempt_at = ?, error = 'Відновлено після перерваного виконання', updated_at = ?
     WHERE status = 'processing' AND updated_at < ?
  `).bind(new Date().toISOString(), new Date().toISOString(), stale).run();
  const due = await env.DB.prepare(`
    SELECT id FROM viber_jobs
     WHERE status IN ('queued', 'scheduled', 'retrying')
       AND COALESCE(next_attempt_at, publish_at, created_at) <= ?
     ORDER BY COALESCE(publish_at, created_at), created_at
     LIMIT ?
  `).bind(new Date().toISOString(), MAX_BATCH_PER_TICK).all();
  for (let index = 0; index < (due.results || []).length; index += 1) {
    await publishJob(env, due.results[index].id);
    if (index < due.results.length - 1) await new Promise(resolve => setTimeout(resolve, BETWEEN_POSTS_MS));
  }
}

async function verifyAccount(env) {
  if (!env.VIBER_CHANNEL_TOKEN) return error('Viber token не налаштований', 503);
  const response = await fetch(VIBER_ACCOUNT_URL, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ auth_token: env.VIBER_CHANNEL_TOKEN }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || Number(data.status) !== 0) return error(data.status_message || `Viber HTTP ${response.status}`, 502);
  const superadmins = (data.members || []).filter(member => member.role === 'superadmin');
  return json({
    ok: true,
    channel_id: data.Id || null,
    channel_title: data.chat_hostname || data.name || data.title || null,
    sender_configured: !!env.VIBER_CHANNEL_SENDER_ID,
    configured_sender_is_superadmin: superadmins.some(member => member.id === env.VIBER_CHANNEL_SENDER_ID),
    superadmins: superadmins.map(member => ({ id: member.id, name: member.name || '' })),
  });
}

async function configureWebhook(request, env) {
  if (!env.VIBER_CHANNEL_TOKEN) return error('Viber token не налаштований', 503);
  const requestUrl = new URL(request.url);
  if (requestUrl.protocol !== 'https:') return error('Webhook потребує HTTPS', 400);
  const webhookUrl = new URL('/viber/webhook', requestUrl.origin).toString();
  const response = await fetch(VIBER_WEBHOOK_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      auth_token: env.VIBER_CHANNEL_TOKEN,
      url: webhookUrl,
    }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || Number(data.status) !== 0) {
    return error(data.status_message || `Viber HTTP ${response.status}`, 502);
  }
  return json({
    ok: true,
    status: Number(data.status),
    status_message: data.status_message || 'ok',
    webhook_url: webhookUrl,
  });
}

export { validateJob, validJpegUrl };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/viber/webhook' && request.method === 'POST') {
      // Канальний Post API вимагає доступний HTTPS webhook; вхідні події для
      // опублікованих постів він не надсилає. Тіло навмисно не журналюємо.
      return json({ status: 0, status_message: 'ok' });
    }
    if (!authorized(request, env)) return error('Не авторизовано', 401);
    if (url.pathname === '/v1/status' && request.method === 'GET') {
      return json({ ok: true, configured: !!(env.VIBER_CHANNEL_TOKEN && env.VIBER_CHANNEL_SENDER_ID && env.DB), scheduler: 'cron-every-minute' });
    }
    if (url.pathname === '/v1/verify-account' && request.method === 'POST') return verifyAccount(env);
    if (url.pathname === '/v1/configure-webhook' && request.method === 'POST') return configureWebhook(request, env);
    if (url.pathname === '/v1/jobs' && request.method === 'POST') return createJob(request, env);
    const match = url.pathname.match(/^\/v1\/jobs\/([^/]+)$/);
    if (match && request.method === 'GET') {
      const row = await readJob(env, decodeURIComponent(match[1]));
      return row ? json({ ok: true, ...publicJob(row) }) : error('Job не знайдено', 404);
    }
    return error('Маршрут не знайдено', 404);
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(processDue(env));
  },
};
