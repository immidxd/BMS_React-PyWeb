import { buildDaySlots, describeSlots, parseDayTimes } from './channelSlots';

test('parses loose time lists into sorted unique HH:MM', () => {
  expect(parseDayTimes('18, 9:00; 12.30 9 25:00 abc')).toEqual(['09:00', '12:30', '18:00']);
});

test('fills slots day by day and skips the ones already in the past', () => {
  const now = new Date(2026, 9, 17, 13, 0).getTime();
  const slots = buildDaySlots('2026-10-17', ['09:00', '12:00', '15:00', '18:00'], 5, now);
  expect(slots.map(s => `${s.getDate()} ${s.getHours()}`)).toEqual(['17 15', '17 18', '18 9', '18 12', '18 15']);
  expect(describeSlots(slots)).toBe('17.10–18.10 · 2 дні');
});

test('no times — no slots (the dialog then blocks «Apply»)', () => {
  expect(buildDaySlots('2026-10-17', [], 3)).toEqual([]);
});
