import test from 'node:test';
import assert from 'node:assert/strict';

import { popularityScore, rankCandidates } from '../src/index.js';

test('popularity score keeps the agreed transparent weights', () => {
  assert.equal(popularityScore({
    unique_viewers: 2,
    active_favorites: 3,
    favorite_adds: 4,
    contact_clicks: 5,
    sold_count: 6,
  }), 2 + 9 + 16 + 40 + 72);
});

test('ranking is deterministic and produces a separate reserve', () => {
  const rows = [
    { product_id: 3, productnumber: '#Ф3', sold_count: 0, unique_viewers: 2 },
    { product_id: 1, productnumber: '#Ф1', sold_count: 1, unique_viewers: 0 },
    { product_id: 2, productnumber: '#Ф2', sold_count: 0, unique_viewers: 4 },
    { product_id: 4, productnumber: '#Ф4', sold_count: 0, unique_viewers: 1 },
  ];
  const result = rankCandidates(rows, 2);
  assert.deepEqual(result.selected.map((row) => row.productnumber), ['#Ф1', '#Ф2']);
  assert.deepEqual(result.reserves.map((row) => row.productnumber), ['#Ф3', '#Ф4']);
  assert.deepEqual(result.selected.map((row) => row.position), [1, 2]);
});
