import { expect, it } from 'vitest';
import { formatDate, formatFarmDateTime, setActiveFarmTimezone } from '@/lib/format';
it('CONFIRMED: offsetless UTC API timestamps are interpreted in browser time by formatDate', () => {
  setActiveFarmTimezone('Asia/Kolkata');
  const apiTimestamp = '2026-08-05T16:00:00';
  expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe('America/Phoenix');
  expect(formatFarmDateTime(apiTimestamp)).toBe('5 Aug 2026, 9:30 pm');
  expect(formatDate(apiTimestamp)).toBe('6 Aug 2026');
  expect(formatDate(`${apiTimestamp}Z`)).toBe('5 Aug 2026');
});
