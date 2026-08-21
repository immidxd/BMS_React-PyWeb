import test from 'node:test';
import assert from 'node:assert/strict';

import { MAX_SNAPSHOT_AGE_HOURS, snapshotFreshness, snapshotWarning } from '../src/index.js';

const NOW = new Date('2026-08-21T12:00:00.000Z');

function hoursAgo(hours) {
  return new Date(NOW.getTime() - hours * 3600000);
}

test('a snapshot refreshed by a running BMS is not stale', () => {
  const freshness = snapshotFreshness(hoursAgo(0.5), NOW);
  assert.equal(freshness.stale, false);
  assert.equal(freshness.age_hours, 0.5);
  assert.equal(snapshotWarning(freshness), null);
});

test('the limit itself is still accepted, one hour past it is not', () => {
  assert.equal(snapshotFreshness(hoursAgo(MAX_SNAPSHOT_AGE_HOURS), NOW).stale, false);
  assert.equal(snapshotFreshness(hoursAgo(MAX_SNAPSHOT_AGE_HOURS + 1), NOW).stale, true);
});

test('a BMS closed for days produces a warning counted in days', () => {
  const freshness = snapshotFreshness(hoursAgo(72), NOW);
  assert.equal(freshness.stale, true);
  assert.equal(freshness.age_hours, 72);
  assert.match(snapshotWarning(freshness), /застарів на 3 дн\./);
});

test('a snapshot stale by hours reports hours, not days', () => {
  assert.match(snapshotWarning(snapshotFreshness(hoursAgo(20), NOW)), /застарів на 20 год/);
});

test('a missing or unreadable snapshot counts as stale, never as fresh', () => {
  for (const value of [null, undefined, '', 'not-a-date']) {
    const freshness = snapshotFreshness(value, NOW);
    assert.equal(freshness.stale, true, `${String(value)} must be stale`);
    assert.equal(freshness.age_hours, null);
    assert.match(snapshotWarning(freshness), /Знімок каталогу відсутній/);
  }
});

test('a clock skew into the future never reports a negative age', () => {
  const freshness = snapshotFreshness(new Date(NOW.getTime() + 3600000), NOW);
  assert.equal(freshness.age_hours, 0);
  assert.equal(freshness.stale, false);
});
