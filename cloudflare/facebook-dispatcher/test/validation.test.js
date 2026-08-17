import assert from 'node:assert/strict';
import test from 'node:test';

import worker, {
  encryptToken, decryptToken, normalizedPublishType, pickPages, signatureMatches,
  validMediaUrl, validateDraft, windowFreesAt,
} from '../src/index.js';

test('accepts a safe one-photo page post', () => {
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

test('allows a long page message but caps the reel description', () => {
  // Ліміт Сторінки — 63 206, а не інстаграмівські 2200: підпис на 3000 символів
  // має проходити у стрічку й НЕ проходити в Reel.
  const media = [{ type: 'IMAGE', url: 'https://cdn.example.com/a.jpeg' }];
  assert.equal(validateDraft({ caption: 'a'.repeat(3000), media }), null);
  assert.match(validateDraft({ caption: 'a'.repeat(63207), media }), /63206/);
  assert.match(validateDraft({
    publish_type: 'REEL',
    caption: 'a'.repeat(2201),
    media: [{ type: 'VIDEO', url: 'https://cdn.example.com/reel.mp4' }],
  }), /2200/);
});

test('rejects more than ten media items', () => {
  const media = Array.from({ length: 11 }, (_, index) => ({
    type: 'IMAGE', url: `https://cdn.example.com/${index}.jpeg`,
  }));
  assert.match(validateDraft({ caption: 'ok', media }), /від 1 до 10/);
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

test('video never sneaks into the page feed as a plain post', () => {
  assert.match(validateDraft({
    publish_type: 'FEED',
    caption: 'ok',
    media: [{ type: 'VIDEO', url: 'https://cdn.example.com/clip.mp4' }],
  }), /як Reel/);
});

test('normalizes and rejects unknown publish types', () => {
  assert.equal(normalizedPublishType('reel'), 'REEL');
  assert.equal(normalizedPublishType(undefined), 'FEED');
  assert.equal(normalizedPublishType('carousel'), null);
});

test('schedule window mirrors Instagram', () => {
  assert.match(validateDraft({
    caption: 'ok',
    media: [{ type: 'IMAGE', url: 'https://cdn.example.com/a.jpeg' }],
    publish_at: new Date(Date.now() - 1000).toISOString(),
  }), /уже минув/);
  assert.match(validateDraft({
    caption: 'ok',
    media: [{ type: 'IMAGE', url: 'https://cdn.example.com/a.jpeg' }],
    publish_at: new Date(Date.now() + 400 * 24 * 3600 * 1000).toISOString(),
  }), /365/);
});

test('page selection takes every listed page and never guesses', () => {
  const pages = [
    { id: '111', name: 'b.randstoreua' },
    { id: '222', name: 'Lyudmila Brand X Original Stuff' },
    { id: '333', name: 'Стороння Сторінка' },
  ];

  // Обидві цільові Сторінки — саме те, як налаштовано в BMS.
  assert.deepEqual(
    pickPages(pages, 'b.randstoreua, Lyudmila Brand X Original Stuff').map(page => page.id),
    ['111', '222'],
  );
  // Порядок у конфізі зберігається, id працює нарівні з назвою.
  assert.deepEqual(pickPages(pages, '222,111').map(page => page.id), ['111', '222'].reverse());
  // Дублікат у конфізі не створює двох підключень тієї самої Сторінки.
  assert.deepEqual(pickPages(pages, '111, b.randstoreua').map(page => page.id), ['111']);
  // Стороння Сторінка НЕ підтягується лише тому, що доступна.
  assert.equal(pickPages(pages, 'b.randstoreua').length, 1);

  assert.throws(() => pickPages(pages, ''), /кілька Сторінок/);
  assert.throws(() => pickPages(pages, 'Немає такої'), /немає/);
  assert.throws(() => pickPages([], ''), /жодної Сторінки/);
  assert.deepEqual(pickPages([{ id: '444', name: 'Одна' }], '').map(page => page.id), ['444']);
});

test('token encryption round-trips and refuses a wrong-length key', async () => {
  const env = { TOKEN_ENCRYPTION_KEY: 'a'.repeat(64) };
  const secured = await encryptToken(env, 'page-token-value');
  assert.equal(await decryptToken(env, secured.ciphertext, secured.iv), 'page-token-value');
  await assert.rejects(encryptToken({ TOKEN_ENCRYPTION_KEY: 'short' }, 'x'), /64-символьним/);
});

test('webhook signature must match the app secret', async () => {
  const secret = 'app-secret';
  const body = '{"object":"page"}';
  assert.equal(await signatureMatches(body, 'sha256=' + 'f'.repeat(64), secret), false);
  assert.equal(await signatureMatches(body, null, secret), false);
});

test('unauthenticated callers never reach job routes', async () => {
  const env = { BMS_DISPATCHER_KEY: 'k'.repeat(32) };
  const response = await worker.fetch(
    new Request('https://dispatcher.example.com/v1/jobs', { method: 'POST' }), env,
  );
  assert.equal(response.status, 401);
});

test('health check stays public and leaks nothing', async () => {
  const response = await worker.fetch(
    new Request('https://dispatcher.example.com/health'), { FACEBOOK_LIVE_ENABLED: 'false' },
  );
  const data = await response.json();
  assert.equal(response.status, 200);
  assert.equal(data.service, 'bms-facebook-dispatcher');
  assert.equal(data.live_publish_enabled, false);
  assert.equal(data.app_secret, undefined);
});

// ─── Добова квота Сторінки: перенесення, а не втрата ─────────────────────────

test('window frees exactly 24 hours after the oldest post in it', () => {
  const freesAt = new Date(windowFreesAt('2026-08-17T09:00:00.000Z'));
  assert.equal(freesAt.toISOString(), '2026-08-18T09:01:00.000Z');
});

test('an empty window never schedules a retry in the past', () => {
  assert.ok(new Date(windowFreesAt(null)).getTime() > Date.now());
});
