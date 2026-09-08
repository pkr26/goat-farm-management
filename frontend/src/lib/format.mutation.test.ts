// Mutation-hardening tests for src/lib/format.ts.
//
// Each block pins a behaviour whose mutant survivors were reported in
// reports/mutation/lib.json: formatLitres' unavailable-value marker and Indian
// grouping, the product-default farm timezone, formatFarmDateTime's
// end-anchored offset detection, addDays' 4-digit year padding, and the exact
// MONTHS table used by formatDate (including the single-digit-part padding
// applied before timestamp validation).

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addDays,
  farmToday,
  formatDate,
  formatFarmDateTime,
  formatLitres,
  formatMoney,
  setActiveFarmTimezone,
} from "./format";

describe("formatLitres — mutation boundaries", () => {
  it("renders an em dash for missing and non-finite values", () => {
    expect(formatLitres(null)).toBe("—");
    expect(formatLitres(undefined)).toBe("—");
    expect(formatLitres(Number.NaN)).toBe("—");
    expect(formatLitres(Number.POSITIVE_INFINITY)).toBe("—");
    expect(formatLitres(Number.NEGATIVE_INFINITY)).toBe("—");
  });

  it("rounds to whole litres with Indian digit grouping", () => {
    expect(formatLitres(0)).toBe("0");
    expect(formatLitres(999)).toBe("999");
    expect(formatLitres(1000)).toBe("1,000");
    expect(formatLitres(29999.6)).toBe("30,000");
    expect(formatLitres(123456789)).toBe("12,34,56,789");
    expect(formatLitres(-1)).toBe("-1");
  });
});

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
