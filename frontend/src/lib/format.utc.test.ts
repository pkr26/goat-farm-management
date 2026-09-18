/**
 * utcToday/addDays: comparisons against server due dates must
 * use the backend's UTC "today", not the browser's local date.
 */

import { describe, expect, it } from "vitest";

import { addDays, daysBetween, todayInTimeZone, utcToday } from "./format";

describe("utcToday", () => {
  it("returns today's UTC date as YYYY-MM-DD", () => {
    expect(utcToday()).toBe(new Date().toISOString().slice(0, 10));
    expect(utcToday()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("todayInTimeZone", () => {
  it("uses the farm's calendar date across UTC boundaries", () => {
    const instant = new Date("2026-08-08T20:00:00Z");
    expect(todayInTimeZone("Asia/Kolkata", instant)).toBe("2026-08-09");
    expect(todayInTimeZone("America/Phoenix", instant)).toBe("2026-08-08");
  });

  it("falls back safely when a stale timezone is invalid", () => {
    expect(todayInTimeZone("Not/A_Timezone", new Date("2026-08-08T20:00:00Z"))).toBe(
      "2026-08-09",
    );
  });
});

describe("addDays", () => {
  it("adds days within a month", () => {
    expect(addDays("2026-08-07", 7)).toBe("2026-08-14");
  });

  it("crosses month and year boundaries", () => {
    expect(addDays("2026-08-31", 1)).toBe("2026-09-01");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
  });

  it("handles leap years", () => {
    expect(addDays("2028-02-28", 1)).toBe("2028-02-29");
    expect(addDays("2026-02-28", 1)).toBe("2026-03-01");
  });

  it("does not normalize an impossible source date", () => {
    expect(addDays("2026-02-30", 1)).toBe("2026-02-30");
    expect(addDays("2026-13-01", 1)).toBe("2026-13-01");
  });

  it("handles years before 0100 without Date.UTC's 1900 offset", () => {
    expect(addDays("0001-01-01", 1)).toBe("0001-01-02");
    expect(addDays("0099-12-31", 1)).toBe("0100-01-01");
  });
});

describe("daysBetween", () => {
  it("does not apply Date.UTC's 1900 offset to years before 0100", () => {
    expect(daysBetween("0001-01-01", "0001-01-02")).toBe(1);
    expect(daysBetween("0099-12-31", "0100-01-01")).toBe(1);
  });

  it("does not normalize corrupt calendar dates into unrelated task deadlines", () => {
    expect(daysBetween("2026-02-30", "2026-03-01")).toBe(0);
    expect(daysBetween("2026-02-28", "not-a-date")).toBe(0);
  });
});
