import { taskManager } from './taskManager';

test('завершені записи зникають зі Сповіщень з часом, помилки живуть довше', () => {
  const t0 = 1_000_000;
  taskManager.setExternal('a', 'Готово', 'success');
  taskManager.setExternal('b', 'Помилка', 'error', 'x');
  taskManager.setExternal('c', 'Іде', 'running');
  const ids = () => taskManager.getTasks().map(t => t.id).sort();
  expect(ids()).toEqual(['a', 'b', 'c']);
  const ended = Date.now();
  taskManager.expire(ended + 11 * 60_000);          // успіх — 10 хв
  expect(ids()).toEqual(['b', 'c']);
  taskManager.expire(ended + 61 * 60_000);          // помилка — 60 хв
  expect(ids()).toEqual(['c']);                      // те, що виконується, не чіпаємо
  void t0;
});
