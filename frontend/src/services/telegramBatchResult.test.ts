import { formatTelegramBatchResult } from './telegramBatchResult';

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
