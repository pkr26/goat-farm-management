/** Unit tests for the shared display formatters (Indian digit grouping, dates). */

import { afterEach, describe, expect, it } from "vitest";

import { formatDate, formatFarmDateTime, formatMoney, setActiveFarmTimezone } from "./format";

describe("formatMoney", () => {
  it("groups digits the Indian way (2-2-3) with the rupee symbol", () => {
    expect(formatMoney(1234567.5)).toBe("₹12,34,567.50");
    expect(formatMoney(100000)).toBe("₹1,00,000");
    expect(formatMoney(1000)).toBe("₹1,000");
  });

  it("keeps two decimals only when non-zero", () => {
    expect(formatMoney(999.9)).toBe("₹999.90");
    expect(formatMoney(999.5)).toBe("₹999.50");
    expect(formatMoney(1234567)).toBe("₹12,34,567");
    expect(formatMoney(0)).toBe("₹0");
  });

  it("prefixes negatives with a minus before the rupee symbol", () => {
    expect(formatMoney(-1234.5)).toBe("-₹1,234.50");
  });

  it("renders an em dash for missing or non-finite values", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(undefined)).toBe("—");
    expect(formatMoney(NaN)).toBe("—");
    expect(formatMoney(Infinity)).toBe("—");
    expect(formatMoney(-Infinity)).toBe("—");
  });
});

describe("formatDate", () => {
  it("formats ISO dates as 'd Mon yyyy'", () => {
    expect(formatDate("2026-08-05")).toBe("5 Aug 2026");
    expect(formatDate("2026-01-01")).toBe("1 Jan 2026");
    expect(formatDate("2026-12-31")).toBe("31 Dec 2026");
  });

  it("accepts full ISO timestamps, using the date part", () => {
    expect(formatDate("2026-02-14T10:30:00")).toBe("14 Feb 2026");
  });

  it("renders an em dash for empty or invalid input", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate(undefined)).toBe("—");
    expect(formatDate("")).toBe("—");
    expect(formatDate("not-a-date")).toBe("—");
  });
});

describe("formatFarmDateTime — unified display style", () => {
  afterEach(() => setActiveFarmTimezone(null));

  it("renders 'd Mon yyyy, h:mm am/pm' in the farm timezone", () => {
    // 14:07 UTC → 19:37 at +05:30; the style matches formatDate's date part.
    setActiveFarmTimezone("Asia/Kolkata");
    expect(formatFarmDateTime("2026-08-05T14:07:00")).toBe("5 Aug 2026, 7:37 pm");
    expect(formatFarmDateTime("2026-08-05T02:07:00Z")).toBe("5 Aug 2026, 7:37 am");
    // Midnight cross: 20:00 UTC is already the next farm day at 01:30.
    expect(formatFarmDateTime("2026-08-05T20:00:00")).toBe("6 Aug 2026, 1:30 am");
  });
});
