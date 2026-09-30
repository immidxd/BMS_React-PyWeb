import * as ab from './autofillBatch';
import { taskManager } from './taskManager';

const job = (over: Partial<ab.BatchJob> = {}): ab.BatchJob => ({
  id: 'j1', label: 'Завіз', state: 'running', total: 20, done: 3,
  proposed_products: 2, proposed_fields: 7, nothing: 1, errors: 0, skipped: 0,
  current: '#Ф4412', stop_reason: null, results: [], ...over,
});

test('поки йде — показуємо, ДЕ саме зараз', () => {
  expect(ab.describe(job())).toBe('3 з 20 · #Ф4412');
});

test('очікування хвилинної межі — це стан, а не помилка', () => {
  const d = ab.describe(job({ state: 'waiting', stop_reason: 'Хвилинна межа Google — чекаємо 30 с' }));
  expect(d).toContain('Хвилинна межа');
  expect(d).toContain('3 з 20');
});

test('підсумок називає і знахідки, і те, до чого не дійшла черга', () => {
  const d = ab.describe(job({
    state: 'done', done: 12, skipped: 8, nothing: 2,
    stop_reason: 'Добова квота Google вичерпана — решта лишилась нерозпізнаною.',
  }));
  expect(d).toContain('знайдено 7 полів на 2 товарах');
  expect(d).toContain('не дійшли черги: 8');
  expect(d).toContain('Добова квота');
});

test('порожній результат не бреше, що щось зроблено', () => {
  expect(ab.describe(job({ state: 'done', proposed_fields: 0, proposed_products: 0, nothing: 0 })))
    .toBe('нічого не змінилось');
});
