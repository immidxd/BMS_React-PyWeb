import test from 'node:test';
import assert from 'node:assert/strict';

import { validateJob, validJpegUrl } from '../src/index.js';

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
