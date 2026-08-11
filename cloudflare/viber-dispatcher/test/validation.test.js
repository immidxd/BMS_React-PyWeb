import test from 'node:test';
import assert from 'node:assert/strict';

import dispatcher, { validateJob, validJpegUrl } from '../src/index.js';

const valid = {
  idempotency_key: 'batch:42',
  product_id: 42,
  caption: 'Viber test card',
  media_url: 'https://images.example/social/viber/42/card.jpeg',
  thumbnail_url: 'https://images.example/social/viber/42/card.thumb.jpeg',
};

test('accepts a safe immediate picture payload', () => {
  assert.equal(validateJob(valid), null);
});

test('never turns an expired schedule into publish now', () => {
  assert.match(validateJob({
    ...valid,
    publish_at: new Date(Date.now() - 60_000).toISOString(),
  }), /час уже минув/);
});

test('requires the exact JPEG extension used by Channels Post API', () => {
  assert.equal(validJpegUrl('https://images.example/card.jpeg'), true);
  assert.equal(validJpegUrl('http://images.example/card.jpeg'), false);
  assert.equal(validJpegUrl('https://images.example/card.jpg'), false);
});

test('configures the exact public webhook without exposing the channel token', async () => {
  const originalFetch = globalThis.fetch;
  let sentBody;
  globalThis.fetch = async (_url, options) => {
    sentBody = JSON.parse(options.body);
    return new Response(JSON.stringify({ status: 0, status_message: 'ok' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };
  try {
    const dispatcherKey = 'd'.repeat(48);
    const response = await dispatcher.fetch(new Request(
      'https://bms-viber.example.workers.dev/v1/configure-webhook',
      { method: 'POST', headers: { authorization: `Bearer ${dispatcherKey}` } },
    ), {
      BMS_DISPATCHER_KEY: dispatcherKey,
      VIBER_CHANNEL_TOKEN: 'private-channel-token',
    });
    const result = await response.json();
    assert.equal(response.status, 200);
    assert.equal(result.webhook_url, 'https://bms-viber.example.workers.dev/viber/webhook');
    assert.equal(JSON.stringify(result).includes('private-channel-token'), false);
    assert.deepEqual(sentBody, {
      auth_token: 'private-channel-token',
      url: 'https://bms-viber.example.workers.dev/viber/webhook',
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
