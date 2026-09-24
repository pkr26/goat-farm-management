// Mutation-hardening tests for src/lib/format.ts.
//
// Each block pins a behaviour whose mutant survivors were reported in
// reports/mutation/lib.json: the product-default farm timezone,
// formatFarmDateTime's end-anchored offset detection, addDays' 4-digit year
// padding, and the exact MONTHS table used by formatDate (including the
// single-digit-part padding applied before timestamp validation).

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addDays,
  daysBetween,
  farmToday,
  formatDate,
  formatFarmDateTime,
  formatMoney,
  formatMoneyDecimal,
  setActiveFarmTimezone,
} from "./format";

describe("farmToday — product-default timezone", () => {
  afterEach(() => setActiveFarmTimezone(null));

  it("uses Asia/Kolkata before any farm has announced its timezone", () => {
    // 2026-08-08T20:00:00Z is already 2026-08-09 at +05:30. An empty default
    // timezone would make the Intl constructor (and its fallback) throw.
    expect(() => farmToday(new Date("2026-08-08T20:00:00Z"))).not.toThrow();
    expect(farmToday(new Date("2026-08-08T20:00:00Z"))).toBe("2026-08-09");
  });

  it("restores the default when the active timezone is cleared", () => {
    setActiveFarmTimezone("America/Phoenix");
    expect(farmToday(new Date("2026-08-08T20:00:00Z"))).toBe("2026-08-08");
    setActiveFarmTimezone(null);
    expect(farmToday(new Date("2026-08-08T20:00:00Z"))).toBe("2026-08-09");
  });
});

describe("formatFarmDateTime — offset detection is anchored to the end", () => {
  afterEach(() => setActiveFarmTimezone(null));

  it("treats an offset buried mid-string as absent and reads the value as UTC", () => {
    setActiveFarmTimezone("Asia/Kolkata");
    // The RFC-2822-style rendering carries "+0530" but does not END with an
    // offset, so the value is offset-less: "Z" is appended (14:07 UTC) and
    // rendered in the farm zone. A regex without the `$` anchor would reuse
    // the embedded +0530 and print 14:07 instead of 19:37.
    expect(
      formatFarmDateTime("Tue Aug 05 2026 14:07:00 GMT+0530 (India Standard Time)"),
    ).toBe("5 Aug 2026, 7:37 pm");
  });
});

describe("addDays — year padding below 1000", () => {
  it("zero-pads years to four digits", () => {
    expect(addDays("0999-01-01", 0)).toBe("0999-01-01");
    expect(addDays("0999-12-31", 1)).toBe("1000-01-01");
  });

  it("still degrades to the input for malformed components", () => {
    expect(addDays("not-a-date", 1)).toBe("not-a-date");
    expect(addDays("", 3)).toBe("");
  });
});

describe("formatDate — MONTHS table", () => {
  it("renders every month's exact abbreviation", () => {
    const expected = [
      ["2026-01-04", "4 Jan 2026"],
      ["2026-02-04", "4 Feb 2026"],
      ["2026-03-04", "4 Mar 2026"],
      ["2026-04-04", "4 Apr 2026"],
      ["2026-05-04", "4 May 2026"],
      ["2026-06-04", "4 Jun 2026"],
      ["2026-07-04", "4 Jul 2026"],
      ["2026-08-04", "4 Aug 2026"],
      ["2026-09-04", "4 Sep 2026"],
      ["2026-10-04", "4 Oct 2026"],
      ["2026-11-04", "4 Nov 2026"],
      ["2026-12-04", "4 Dec 2026"],
    ] as const;
    for (const [iso, text] of expected) {
      expect(formatDate(iso)).toBe(text);
    }
  });

  it("pads single-digit date parts before validating the timestamp suffix", () => {
    // Without padding, "2026-8-5T10:30:00Z" fails Date parsing and would
    // render the em dash instead of the calendar date.
    expect(formatDate("2026-8-5T10:30:00Z")).toBe("5 Aug 2026");
    expect(formatDate("2026-8-5T10:30Z")).toBe("5 Aug 2026");
    expect(formatDate("2026-8-5T10:30:00+05:30")).toBe("5 Aug 2026");
  });

  it("pads sub-1000 years before validating the timestamp suffix", () => {
    // "999-01-01T10:30:00Z" (unpadded year) is not parseable, so a missing
    // zero-fill would render the em dash instead of the calendar date.
    expect(formatDate("0999-01-01T10:30:00Z")).toBe("1 Jan 999");
    expect(formatDate("0999-12-31T23:59:59Z")).toBe("31 Dec 999");
  });
});

describe("formatMoney — exponential-notation boundary sign", () => {
  it("signs magnitudes at and beyond 1e21", () => {
    expect(formatMoney(1e21)).toBe("₹1,00,00,00,00,00,00,00,00,00,000");
    expect(formatMoney(-1e21)).toBe("-₹1,00,00,00,00,00,00,00,00,00,000");
  });
});

describe("format — module-scope constants", () => {
  it("pins DEFAULT_FARM_TIMEZONE and the MONTHS table on a fresh evaluation", async () => {
    // Re-evaluate the module inside the test so module-scope mutants are
    // attributed to (and exercised by) this test, not only by whatever test
    // first imported the module in the worker.
    vi.resetModules();
    const fresh = await import("./format");

    expect(fresh.DEFAULT_FARM_TIMEZONE).toBe("Asia/Kolkata");
    // Touches every MONTHS entry through the public formatter.
    for (const [iso, text] of [
      ["2026-01-04", "4 Jan 2026"],
      ["2026-02-04", "4 Feb 2026"],
      ["2026-03-04", "4 Mar 2026"],
      ["2026-04-04", "4 Apr 2026"],
      ["2026-05-04", "4 May 2026"],
      ["2026-06-04", "4 Jun 2026"],
      ["2026-07-04", "4 Jul 2026"],
      ["2026-08-04", "4 Aug 2026"],
      ["2026-09-04", "4 Sep 2026"],
      ["2026-10-04", "4 Oct 2026"],
      ["2026-11-04", "4 Nov 2026"],
      ["2026-12-04", "4 Dec 2026"],
    ] as const) {
      expect(fresh.formatDate(iso)).toBe(text);
    }
  });
});

/** Round 2 (2026-09-23 campaign): the survivor edges — formatMoney's guard
 *  arms and the 1e21 hand-off, addDays' day/month arithmetic and pass-through
 *  guards, daysBetween's exact day constant, and the default-timezone
 *  fallback. */
describe("format boundary round 2", () => {
  it("formatMoney returns the dash exactly for the three guard cases", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(undefined)).toBe("—");
    expect(formatMoney(Number.NaN)).toBe("—");
    expect(formatMoney(Number.POSITIVE_INFINITY)).toBe("—");
    // Finite zero stays a real value (whole rupees trim the paise).
    expect(formatMoney(0)).toBe("₹0");
    expect(formatMoney(12.5)).toBe("₹12.50");
  });

  it("formatMoney keeps tiny negatives at zero and prints the rupee sign", () => {
    expect(formatMoney(-0.001)).toBe("₹0");
    expect(formatMoney(-1)).toBe("-₹1");
    expect(formatMoney(-12.5)).toBe("-₹12.50");
    // A POSITIVE sub-rupee value must never grow a minus sign, and a real
    // negative fraction must keep it (the sign rule fires on the rounded
    // parts, not the raw value).
    expect(formatMoney(0.4)).toBe("₹0.40");
    expect(formatMoney(-0.5)).toBe("-₹0.50");
    expect(formatMoneyDecimal("-0.50")).toBe("-₹0.50");
    expect(formatMoneyDecimal("0.50")).toBe("₹0.50");
  });

  it("formatDate accepts full ISO timestamps, using only the calendar day", () => {
    expect(formatDate("2026-08-05T10:00:00Z")).toBe("5 Aug 2026");
    expect(formatDate("2026-08-05T23:59:59+05:30")).toBe("5 Aug 2026");
  });

  it("addDays passes malformed dates and non-finite day counts through", () => {
    expect(addDays("garbage", 1)).toBe("garbage");
    expect(addDays("2026-13-01", 1)).toBe("2026-13-01");
    expect(addDays("2026-01-05", Number.NaN)).toBe("2026-01-05");
    expect(addDays("2026-01-05", Number.POSITIVE_INFINITY)).toBe("2026-01-05");
  });

  it("addDays adds exact calendar days across month and year edges", () => {
    expect(addDays("2026-01-10", 5)).toBe("2026-01-15");
    expect(addDays("2026-01-31", 1)).toBe("2026-02-01");
    expect(addDays("2026-02-28", 1)).toBe("2026-03-01");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-15", -15)).toBe("2026-02-28");
  });

  it("daysBetween counts exact UTC days and zero for malformed input", () => {
    expect(daysBetween("2026-01-01", "2026-01-02")).toBe(1);
    expect(daysBetween("2026-01-01", "2026-01-31")).toBe(30);
    expect(daysBetween("2026-01-01", "2026-12-31")).toBe(364);
    expect(daysBetween("2026-01-02", "2026-01-01")).toBe(-1);
    expect(daysBetween("bad", "2026-01-02")).toBe(0);
    expect(daysBetween("2026-01-01", "bad")).toBe(0);
  });
});
