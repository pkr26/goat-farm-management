// REGRESSION TESTS — bug fixed; these tests pin the fix.
//
// formatDate's documented contract is "ISO date string (YYYY-MM-DD) →
// 'd Mon yyyy'; empty/invalid → '—'". The original implementation only
// checked that the three numeric parts were non-zero — it never validated
// month ≤ 12 or the day against the calendar, so out-of-range dates rendered
// as nonsense ("1 undefined 2026", "30 Feb 2024") instead of the em dash.
// These tests assert the contract-correct output and now pass.
//
// Second bug pinned below: formatFarmDateTime built an Intl.DateTimeFormat
// straight from the farm timezone with no guard. The backend validates that
// value with Python's zoneinfo, which — like PostgreSQL — accepts names Intl
// rejects ("Factory"), so the Tasks page and the Animal profile fell to the
// error boundary for every user of such a farm while todayInTimeZone, which
// already had a try/catch, survived. Third bug: the same helper rendered only
// day/month/hour/minute, so two audit rows twelve months apart were
// indistinguishable.

import { afterEach, describe, expect, it } from "vitest";

import { farmToday, formatDate, formatFarmDateTime, setActiveFarmTimezone } from "./format";

describe("formatDate — out-of-range calendar values (suspected bug)", () => {
  it("renders an em dash for month 13 instead of 'undefined'", () => {
    // Actual today: "1 undefined 2026".
    expect(formatDate("2026-13-01")).toBe("—");
  });

  it("renders an em dash for 30 Feb on a leap year", () => {
    // Actual today: "30 Feb 2024".
    expect(formatDate("2024-02-30")).toBe("—");
  });

  it("renders an em dash for 29 Feb on a non-leap year", () => {
    // Actual today: "29 Feb 2026".
    expect(formatDate("2026-02-29")).toBe("—");
  });

  it("renders an em dash for day 31 in a 30-day month", () => {
    // Actual today: "31 Apr 2026".
    expect(formatDate("2026-04-31")).toBe("—");
  });

  it("renders an em dash for day 32", () => {
    // Actual today: "32 Aug 2026".
    expect(formatDate("2026-08-32")).toBe("—");
  });
});

describe("formatFarmDateTime — naive-UTC → farm timezone", () => {
  afterEach(() => setActiveFarmTimezone(null));

  it("reads an offset-less backend timestamp as UTC and shifts it to the farm", () => {
    setActiveFarmTimezone("Asia/Kolkata");
    expect(formatFarmDateTime("2026-08-05T14:07:00")).toBe("5 Aug 2026, 7:37 pm");
    setActiveFarmTimezone("America/Phoenix");
    expect(formatFarmDateTime("2026-08-05T14:07:00")).toBe("5 Aug 2026, 7:07 am");
  });

  it("uses an explicit offset when the timestamp carries one", () => {
    setActiveFarmTimezone("Asia/Kolkata");
    expect(formatFarmDateTime("2026-08-05T14:07:00Z")).toBe("5 Aug 2026, 7:37 pm");
    expect(formatFarmDateTime("2026-08-05T19:37:00+05:30")).toBe("5 Aug 2026, 7:37 pm");
  });

  it("renders an em dash for missing or unparseable input", () => {
    expect(formatFarmDateTime(null)).toBe("—");
    expect(formatFarmDateTime(undefined)).toBe("—");
    expect(formatFarmDateTime("")).toBe("—");
    expect(formatFarmDateTime("not-a-timestamp")).toBe("—");
  });
});

describe("formatFarmDateTime — audit rows a year apart (suspected bug)", () => {
  afterEach(() => setActiveFarmTimezone(null));

  it("distinguishes two instants exactly twelve months apart", () => {
    setActiveFarmTimezone("Asia/Kolkata");
    // Both rendered "04-03 20:00" before the year was included.
    expect(formatFarmDateTime("2025-03-04T14:30:00")).toBe("4 Mar 2025, 8:00 pm");
    expect(formatFarmDateTime("2026-03-04T14:30:00")).toBe("4 Mar 2026, 8:00 pm");
  });
});

describe("farm timezones Intl rejects but the backend accepts (suspected bug)", () => {
  afterEach(() => setActiveFarmTimezone(null));

  it("falls back to the product default instead of throwing RangeError", () => {
    setActiveFarmTimezone("Factory");
    const instant = new Date("2026-08-08T20:00:00Z");

    expect(() => farmToday(instant)).not.toThrow();
    expect(farmToday(instant)).toBe("2026-08-09");
    // Threw RangeError before the guard, taking the whole page down.
    expect(() => formatFarmDateTime("2026-08-05T14:07:00")).not.toThrow();
    expect(formatFarmDateTime("2026-08-05T14:07:00")).toBe("5 Aug 2026, 7:37 pm");
  });

  it("falls back for a stale or garbage zone too", () => {
    setActiveFarmTimezone("Not/A_Timezone");
    expect(formatFarmDateTime("2026-08-05T14:07:00")).toBe("5 Aug 2026, 7:37 pm");
  });
});
