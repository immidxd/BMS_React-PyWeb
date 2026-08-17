import assert from 'node:assert/strict';
import test from 'node:test';

import worker, {
  carouselParentPayload, containerStatuses, mediaContainerPayload, signatureMatches,
  validMediaUrl, validateDraft, windowFreesAt,
} from '../src/index.js';

test('accepts a safe one-photo draft', () => {
  assert.equal(validateDraft({
    caption: 'Тестовий підпис',
    media: [{ type: 'IMAGE', url: 'https://cdn.example.com/product.jpeg' }],
  }), null);
});

test('rejects unsupported and non-https media', () => {
  assert.equal(validMediaUrl('http://cdn.example.com/a.jpeg', 'IMAGE'), false);
  assert.equal(validMediaUrl('https://cdn.example.com/a.png', 'IMAGE'), false);
  assert.equal(validMediaUrl('https://cdn.example.com/a.jpg', 'IMAGE'), true);
  assert.equal(validMediaUrl('https://cdn.example.com/a.mp4', 'VIDEO'), true);
});

test('rejects more than ten media items and oversized caption', () => {
  const media = Array.from({ length: 11 }, (_, index) => ({
    type: 'IMAGE', url: `https://cdn.example.com/${index}.jpeg`,
  }));
  assert.match(validateDraft({ caption: 'ok', media }), /від 1 до 10/);
  assert.match(validateDraft({
    caption: 'a'.repeat(2201),
    media: [{ type: 'IMAGE', url: 'https://cdn.example.com/a.jpeg' }],
  }), /2200/);
});

test('validates stories and reels with their real media constraints', () => {
  assert.equal(validateDraft({
    publish_type: 'STORY',
    media: [{ type: 'IMAGE', url: 'https://cdn.example.com/story.jpeg' }],
  }), null);
  assert.match(validateDraft({
    publish_type: 'STORY',
    media: [
      { type: 'IMAGE', url: 'https://cdn.example.com/one.jpeg' },
      { type: 'IMAGE', url: 'https://cdn.example.com/two.jpeg' },
    ],
  }), /рівно один/);
  assert.equal(validateDraft({
    publish_type: 'REEL',
    caption: 'Reel',
    media: [{ type: 'VIDEO', url: 'https://cdn.example.com/reel.mp4' }],
  }), null);
  assert.match(validateDraft({
    publish_type: 'REEL',
    media: [{ type: 'IMAGE', url: 'https://cdn.example.com/reel.jpeg' }],
  }), /відеофайл/);
});

test('builds Meta v26 payloads without leaking unsupported story fields', () => {
  const story = mediaContainerPayload('STORY', {
    type: 'IMAGE', url: 'https://cdn.example.com/story.jpeg',
  }, { caption: 'Не підтримується', collaborators: ['brand'] });
  assert.deepEqual(story, {
    image_url: 'https://cdn.example.com/story.jpeg', media_type: 'STORIES',
  });
  const reel = mediaContainerPayload('REEL', {
    type: 'VIDEO', url: 'https://cdn.example.com/reel.mp4',
  }, { caption: 'Reel', collaborators: ['partner'], share_to_feed: true });
  assert.equal(reel.media_type, 'REELS');
  assert.equal(reel.video_url, 'https://cdn.example.com/reel.mp4');
  assert.deepEqual(reel.collaborators, ['partner']);
  const carousel = carouselParentPayload(['c1', 'c2'], {
    caption: 'Карусель', is_ai_generated: true, is_paid_partnership: false,
  });
  assert.equal(carousel.children, 'c1,c2');
  assert.equal(carousel.is_ai_generated, true);
  assert.equal('is_paid_partnership' in carousel, false);
});

test('polls Instagram Login containers with status_code only', async () => {
  const originalFetch = globalThis.fetch;
  let requestedUrl = '';
  globalThis.fetch = async (request) => {
    requestedUrl = String(request);
    return new Response(JSON.stringify({ status_code: 'FINISHED' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };
  try {
    const result = await containerStatuses({ META_API_VERSION: 'v26.0' }, 'token', ['container-1']);
    assert.deepEqual(result, [{ id: 'container-1', status_code: 'FINISHED' }]);
    assert.equal(new URL(requestedUrl).searchParams.get('fields'), 'status_code');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('validates Meta webhook HMAC', async () => {
  const body = '{"object":"instagram"}';
  const secret = 'test-app-secret';
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  const signed = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(body));
  const hex = [...new Uint8Array(signed)].map(byte => byte.toString(16).padStart(2, '0')).join('');
  assert.equal(await signatureMatches(body, `sha256=${hex}`, secret), true);
  assert.equal(await signatureMatches(`${body} `, `sha256=${hex}`, secret), false);
});

test('health is public and live jobs stay blocked while feature flag is off', async () => {
  const health = await worker.fetch(new Request('https://worker.test/health'), {});
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), {
    ok: true,
    service: 'bms-instagram-dispatcher',
    api_version: 'v26.0',
    live_publish_enabled: false,
  });
  const publish = await worker.fetch(new Request('https://worker.test/v1/jobs', {
    method: 'POST',
    headers: { authorization: `Bearer ${'a'.repeat(32)}`, 'content-type': 'application/json' },
    body: JSON.stringify({
      idempotency_key: 'safe-test', product_id: 42, publish_type: 'FEED',
      caption: 'Тест', media: [{ type: 'IMAGE', url: 'https://cdn.example.com/a.jpeg' }],
    }),
  }), {
    BMS_DISPATCHER_KEY: 'a'.repeat(32),
    INSTAGRAM_LIVE_ENABLED: 'false',
  });
  assert.equal(publish.status, 503);
});

test('OAuth start uses direct Instagram Login with content-only scopes', async () => {
  const key = 'o'.repeat(32);
  const db = {
    prepare() {
      return {
        bind() {
          return { async run() { return { meta: { changes: 1 } }; } };
        },
      };
    },
  };
  const response = await worker.fetch(new Request('https://worker.test/v1/oauth/start', {
    method: 'POST', headers: { authorization: `Bearer ${key}` },
  }), {
    BMS_DISPATCHER_KEY: key,
    INSTAGRAM_APP_ID: '123456789',
    INSTAGRAM_APP_SECRET: 'secret',
    INSTAGRAM_REDIRECT_URI: 'https://worker.test/oauth/instagram/callback',
    TOKEN_ENCRYPTION_KEY: 'a'.repeat(64),
    DB: db,
  });
  assert.equal(response.status, 200);
  const payload = await response.json();
  const url = new URL(payload.authorization_url);
  assert.equal(url.origin, 'https://www.instagram.com');
  assert.equal(url.pathname, '/oauth/authorize');
  assert.equal(url.searchParams.get('scope'), 'instagram_business_basic,instagram_business_content_publish');
  assert.equal(url.searchParams.has('config_id'), false);
  assert.equal(url.searchParams.get('redirect_uri'), 'https://worker.test/oauth/instagram/callback');
});

function mutableJobDb(initial) {
  const state = { row: { ...initial } };
  return {
    state,
    prepare(sql) {
      return {
        bind(...values) {
          return {
            async first() { return state.row; },
            async run() {
              if (sql.includes("SET status = 'cancelled'")) {
                state.row = { ...state.row, status: 'cancelled', phase: 'cancelled', updated_at: values[0] };
                return { meta: { changes: 1 } };
              }
              if (sql.includes("SET publish_at = ?")) {
                state.row = { ...state.row, publish_at: values[0], next_attempt_at: values[1], status: 'scheduled', updated_at: values[2] };
                return { meta: { changes: 1 } };
              }
              return { meta: { changes: 0 } };
            },
          };
        },
      };
    },
  };
}

test('scheduled jobs can be rescheduled and cancelled before container creation', async () => {
  const key = 'b'.repeat(32);
  const base = {
    id: 'job-1', product_id: 42, product_number: 'Ф42', instagram_account_id: 'ig-1',
    publish_type: 'FEED', status: 'scheduled', phase: 'new', attempts: 0,
    publish_at: new Date(Date.now() + 3600000).toISOString(), child_container_ids: '[]',
    container_id: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
  };
  const db = mutableJobDb(base);
  const future = new Date(Date.now() + 7200000).toISOString();
  const rescheduled = await worker.fetch(new Request('https://worker.test/v1/jobs/job-1', {
    method: 'PATCH',
    headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json' },
    body: JSON.stringify({ publish_at: future }),
  }), { BMS_DISPATCHER_KEY: key, DB: db });
  assert.equal(rescheduled.status, 200);
  assert.equal((await rescheduled.json()).scheduled_at, future);

  const cancelled = await worker.fetch(new Request('https://worker.test/v1/jobs/job-1', {
    method: 'DELETE', headers: { authorization: `Bearer ${key}` },
  }), { BMS_DISPATCHER_KEY: key, DB: db });
  assert.equal(cancelled.status, 200);
  assert.equal((await cancelled.json()).status, 'cancelled');
});

// ─── Добова квота: чекання слота, а не смерть job ────────────────────────────

test('window frees exactly 24 hours after the oldest post in it', () => {
  const oldest = new Date('2026-08-17T09:00:00.000Z').toISOString();
  const freesAt = new Date(windowFreesAt(oldest));
  // +24 години і хвилина запасу, щоб не впертися в ту саму секунду.
  assert.equal(freesAt.toISOString(), '2026-08-18T09:01:00.000Z');
});

test('an empty or broken window never schedules a retry in the past', () => {
  const now = Date.now();
  for (const value of [null, '', 'не дата']) {
    const freesAt = new Date(windowFreesAt(value)).getTime();
    assert.ok(freesAt > now, `${value} дав час у минулому`);
  }
});

test('a long-past window still waits a few minutes instead of hammering', () => {
  // Найстаріша публікація вийшла з вікна давно: слот вільний, але повторювати
  // одразу тією ж хвилиною немає сенсу — cron і так тікає щохвилини.
  const freesAt = new Date(windowFreesAt('2020-01-01T00:00:00.000Z')).getTime();
  assert.ok(freesAt >= Date.now() + 4 * 60 * 1000);
});
