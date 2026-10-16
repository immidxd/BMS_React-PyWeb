import { formatChannelPlan, formatTelegramBatchResult } from './telegramBatchResult';

test('shows and groups the real Telegram destination error', () => {
  const detail = formatTelegramBatchResult({
    status: 'partial',
    counts: { success: 0, partial: 2, error: 0, skipped: 0 },
    results: [
      {
        productnumber: 'Ф4200', status: 'partial',
        result: { failed: [{ channel: 'BrandStore', error: 'Розклад заповнений' }] },
      },
      {
        productnumber: 'Ф4199', status: 'partial',
        result: { failed: [{ channel: 'BrandStore', error: 'Розклад заповнений' }] },
      },
    ],
  });

  expect(detail).toContain('2 частково');
  expect(detail).toContain('BrandStore: Розклад заповнений');
  expect(detail).toContain('2 товари: #Ф4200, #Ф4199');
  expect(detail.match(/Розклад заповнений/g)).toHaveLength(1);
});

test('says how much of the channel plan is already in Telegram and that the rest is not lost', () => {
  const results = Array.from({ length: 29 }, (_, i) => ({
    productnumber: `Ф${i}`, status: 'success', result: { failed: [], channel: { queue_id: i + 1 } },
  }));
  expect(formatChannelPlan({ results, channel_queue: { queued: 17, left_planned: 12 } }))
    .toBe('Канал: 29 заплановано — 17 уже в розкладі Telegram, решту BMS поставить, щойно звільниться місце. Стан — «Публікації → Розклад каналу»');
  expect(formatChannelPlan({ results: results.slice(0, 3), channel_queue: { queued: 3 } }))
    .toContain('усі вже в розкладі Telegram');
  expect(formatChannelPlan({ results: [{ status: 'success', result: { failed: [] } }] })).toBe('');
});
