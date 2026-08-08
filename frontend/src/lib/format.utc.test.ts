/**
 * utcToday/addDays: comparisons against server due dates must
 * use the backend's UTC "today", not the browser's local date.
 */

import { describe, expect, it } from "vitest";

import { addDays, utcToday } from "./format";

describe("utcToday", () => {
  it("returns today's UTC date as YYYY-MM-DD", () => {
    expect(utcToday()).toBe(new Date().toISOString().slice(0, 10));
    expect(utcToday()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
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
});
