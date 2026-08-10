/**
 * Extended boundary tests for the shared display formatters, complementing
 * format.test.ts: Indian lakh/crore digit grouping across every magnitude,
 * paise-level decimals and rounding, negatives, zero edge cases, huge
 * values, formatDate month/day boundaries, and formatFarmDateTime's
 * naive-UTC / explicit-offset / invalid / unusable-timezone contract.
 */

import { afterEach, describe, expect, it } from "vitest";

import { formatDate, formatFarmDateTime, formatMoney, setActiveFarmTimezone } from "./format";

describe("formatMoney — Indian grouping across magnitudes", () => {
  it("leaves values below 1000 ungrouped", () => {
    expect(formatMoney(1)).toBe("₹1");
    expect(formatMoney(10)).toBe("₹10");
    expect(formatMoney(100)).toBe("₹100");
    expect(formatMoney(999)).toBe("₹999");
  });

  it("groups thousands with a single comma at the 4th digit", () => {
    expect(formatMoney(1000)).toBe("₹1,000");
    expect(formatMoney(12345)).toBe("₹12,345");
    expect(formatMoney(99999)).toBe("₹99,999");
  });

  it("switches to 2-digit grouping at one lakh", () => {
    expect(formatMoney(100000)).toBe("₹1,00,000");
    expect(formatMoney(123456)).toBe("₹1,23,456");
    expect(formatMoney(9999999)).toBe("₹99,99,999");
  });

  it("groups crores correctly", () => {
    expect(formatMoney(10000000)).toBe("₹1,00,00,000");
    expect(formatMoney(25000000)).toBe("₹2,50,00,000");
    expect(formatMoney(123456789)).toBe("₹12,34,56,789");
  });

  it("keeps the pattern going for very large values", () => {
    expect(formatMoney(1e12)).toBe("₹10,00,00,00,00,000");
    expect(formatMoney(Number.MAX_SAFE_INTEGER)).toBe("₹9,00,71,99,25,47,40,991");
  });
});

describe("formatMoney — decimals, rounding and sign", () => {
  it("renders two decimals for fractional rupees", () => {
    expect(formatMoney(5.5)).toBe("₹5.50");
    expect(formatMoney(0.01)).toBe("₹0.01");
    expect(formatMoney(0.99)).toBe("₹0.99");
  });

  it("rounds to two decimals (half up via toFixed)", () => {
    expect(formatMoney(1234.567)).toBe("₹1,234.57");
    expect(formatMoney(999.994)).toBe("₹999.99");
  });

  it("carries a decimal round-up into the grouped integer part", () => {
    expect(formatMoney(999.999)).toBe("₹1,000");
  });

  it("treats fractions that round to zero as zero", () => {
    expect(formatMoney(0.001)).toBe("₹0");
    expect(formatMoney(0.004)).toBe("₹0");
    expect(formatMoney(-0.001)).toBe("₹0");
    expect(formatMoney(-0.004)).toBe("₹0");
  });

  it("formats negative zero without a minus sign", () => {
    expect(formatMoney(-0)).toBe("₹0");
  });

  it("groups negatives with the minus before the rupee symbol", () => {
    expect(formatMoney(-0.5)).toBe("-₹0.50");
    expect(formatMoney(-99999.5)).toBe("-₹99,999.50");
    expect(formatMoney(-25000000)).toBe("-₹2,50,00,000");
  });
});

describe("formatDate — boundaries", () => {
  it("renders every month abbreviation correctly", () => {
    const expected = [
      ["2026-01-15", "15 Jan 2026"],
      ["2026-02-15", "15 Feb 2026"],
      ["2026-03-15", "15 Mar 2026"],
      ["2026-04-15", "15 Apr 2026"],
      ["2026-05-15", "15 May 2026"],
      ["2026-06-15", "15 Jun 2026"],
      ["2026-07-15", "15 Jul 2026"],
      ["2026-08-15", "15 Aug 2026"],
      ["2026-09-15", "15 Sep 2026"],
      ["2026-10-15", "15 Oct 2026"],
      ["2026-11-15", "15 Nov 2026"],
      ["2026-12-15", "15 Dec 2026"],
    ] as const;
    for (const [iso, text] of expected) {
      expect(formatDate(iso)).toBe(text);
    }
  });

  it("handles leap-day on a leap year", () => {
    expect(formatDate("2024-02-29")).toBe("29 Feb 2024");
  });

  it("handles single-digit day and month without zero padding", () => {
    expect(formatDate("2026-8-5")).toBe("5 Aug 2026");
  });

  it("ignores time and timezone suffixes on full ISO timestamps", () => {
    expect(formatDate("2026-08-05T23:59:59.999Z")).toBe("5 Aug 2026");
    expect(formatDate("2026-02-14T00:00:00+05:30")).toBe("14 Feb 2026");
  });

  it("renders an em dash for structurally incomplete dates", () => {
    expect(formatDate("2026-08")).toBe("—");
    expect(formatDate("2026")).toBe("—");
    expect(formatDate("2026-08-0")).toBe("—");
  });

  it("renders an em dash for a zero month or year", () => {
    expect(formatDate("2026-00-15")).toBe("—");
    expect(formatDate("0000-05-10")).toBe("—");
  });
});


describe("formatMoney — additional boundaries", () => {
  it("groups five-digit values at the thousand mark only", () => {
    expect(formatMoney(10000)).toBe("₹10,000");
  });

  it("groups a hundred crore", () => {
    expect(formatMoney(1000000000)).toBe("₹1,00,00,00,000");
  });

  it("formats the smallest negative unit", () => {
    expect(formatMoney(-1)).toBe("-₹1");
  });

  it("pads a single decimal digit to two places", () => {
    expect(formatMoney(100.1)).toBe("₹100.10");
  });
});

describe("formatDate — additional boundaries", () => {
  it("handles leap day across leap years", () => {
    expect(formatDate("2028-02-29")).toBe("29 Feb 2028");
  });

  it("handles month-end days", () => {
    expect(formatDate("2035-06-30")).toBe("30 Jun 2035");
    expect(formatDate("2026-01-31")).toBe("31 Jan 2026");
  });

  it("handles old years", () => {
    expect(formatDate("1999-01-01")).toBe("1 Jan 1999");
  });

  it("tolerates a trailing space after the date part", () => {
    expect(formatDate("2026-08-05 ")).toBe("5 Aug 2026");
  });
});

// One instant — 2026-08-05 20:00 UTC — expressed four ways. Asia/Kolkata
// (+05:30) puts it on the NEXT calendar day at 01:30 and America/Phoenix
// (-07:00, no DST) leaves it at 13:00 the same day, so every expectation below
// is a hand-computed literal: reading a naive timestamp as browser-local, or
// re-using an offset input's wall clock, changes the rendered string.
describe("formatFarmDateTime — instants, offsets and fallbacks", () => {
  afterEach(() => setActiveFarmTimezone(null));

  it("reads an offset-less backend datetime as UTC, not browser-local", () => {
    setActiveFarmTimezone("Asia/Kolkata");
    expect(formatFarmDateTime("2026-08-05T20:00:00")).toBe("06-08-2026 01:30");
    setActiveFarmTimezone("America/Phoenix");
    expect(formatFarmDateTime("2026-08-05T20:00:00")).toBe("05-08-2026 13:00");
  });

  it("honours a Z suffix identically to the naive form", () => {
    setActiveFarmTimezone("Asia/Kolkata");
    expect(formatFarmDateTime("2026-08-05T20:00:00Z")).toBe("06-08-2026 01:30");
    setActiveFarmTimezone("America/Phoenix");
    expect(formatFarmDateTime("2026-08-05T20:00:00Z")).toBe("05-08-2026 13:00");
  });

  it("converts an input carrying a +05:30 offset to the farm's clock", () => {
    setActiveFarmTimezone("America/Phoenix");
    expect(formatFarmDateTime("2026-08-06T01:30:00+05:30")).toBe("05-08-2026 13:00");
    setActiveFarmTimezone("Asia/Kolkata");
    expect(formatFarmDateTime("2026-08-06T01:30:00+05:30")).toBe("06-08-2026 01:30");
  });

  it("renders the em-dash placeholder for input that cannot be parsed", () => {
    setActiveFarmTimezone("Asia/Kolkata");
    // ISO-shaped but off the calendar, so Date.parse yields NaN.
    expect(formatFarmDateTime("2026-13-01T00:00:00")).toBe("—");
    expect(formatFarmDateTime("2026-08-05T25:00:00Z")).toBe("—");
    expect(formatFarmDateTime("yesterday")).toBe("—");
    expect(formatFarmDateTime(null)).toBe("—");
  });

  it("renders in the default farm timezone when Intl rejects the farm's", () => {
    // Python's zoneinfo and PostgreSQL accept "Factory"; Intl throws on it.
    setActiveFarmTimezone("Factory");
    expect(formatFarmDateTime("2026-08-05T20:00:00")).toBe("06-08-2026 01:30");
  });
});
