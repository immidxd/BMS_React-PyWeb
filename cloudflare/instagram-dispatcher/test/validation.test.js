import assert from 'node:assert/strict';
import test from 'node:test';

import worker, { signatureMatches, validMediaUrl, validateDraft } from '../src/index.js';

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

test('health is public and no publishing endpoint exists', async () => {
  const health = await worker.fetch(new Request('https://worker.test/health'), {});
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), {
    ok: true,
    service: 'bms-instagram-dispatcher',
    live_publish_enabled: false,
  });
  const publish = await worker.fetch(new Request('https://worker.test/v1/publish', { method: 'POST' }), {
    BMS_DISPATCHER_KEY: 'a'.repeat(32),
  });
  assert.equal(publish.status, 401);
});
