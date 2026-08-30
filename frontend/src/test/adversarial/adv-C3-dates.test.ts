/**
 * ADVERSARIAL AUDIT C3 — date arithmetic & timezone attacks (executed, pure).
 *
 * The whole app reasons in YYYY-MM-DD strings compared lexically, so any
 * function that can emit a non-canonical date (different length, plus-sign
 * year, locale ordering) silently inverts due-date logic elsewhere.
 */

import { afterEach, describe, expect, it } from "vitest";

import {
  addDays,
  farmToday,
  formatDate,
  setActiveFarmTimezone,
  todayInTimeZone,
} from "@/lib/format";

const CANONICAL = /^\d{4}-\d{2}-\d{2}$/;

describe("ADV C3: addDays under rollover and extreme payloads", () => {
  it.each([
    ["2026-01-31", 1, "2026-02-01"],
    ["2026-02-28", 1, "2026-03-01"],
    ["2024-02-28", 1, "2024-02-29"], // leap year
    ["2023-02-28", 1, "2023-03-01"], // non-leap
    ["2026-12-31", 1, "2027-01-01"],
    ["2026-08-30", 365, "2027-08-30"],
    ["2026-08-30", -365, "2025-08-30"],
    ["2026-03-01", -1, "2026-02-28"],
  ])("addDays(%j, %d) === %j (canonical)", (iso, days, expected) => {
    const result = addDays(iso, days);
    expect(result).toBe(expected);
    expect(result).toMatch(CANONICAL);
  });

  it("DEFENDED: crossing year 9999 formats manually instead of '+10000-…'", () => {
    // toISOString() switches to an expanded "+10000-…" spelling for years
    // > 9999; addDays now formats from UTC parts, so the result stays a
    // plain, parseable date string.
    expect(addDays("9999-12-31", 1)).toBe("10000-01-01");
  });

  it("DEFENDED: malformed input degrades instead of throwing", () => {
    // addDays runs on backend-supplied dates inside RecordKiddingDialog's
    // useMemo; a malformed breeding_date must not crash the render.
    expect(addDays("garbage", 1)).toBe("garbage");
    expect(addDays("2026-08-30", Number.NaN)).toBe("2026-08-30");
  });
});

describe("ADV C3: formatDate rejects impossible calendar dates", () => {
  it.each(["2026-02-30", "2026-13-01", "2026-00-10", "2026-04-31", "26-1-1"])(
    "impossible date %j renders as the placeholder",
    (bad) => {
      expect(formatDate(bad)).toBe("—");
    },
  );

  it("trailing junk after a valid date is rejected, not parsed", () => {
    expect(formatDate("2026-08-05Tgarbage")).toBe("—");
    expect(formatDate("2026-08-05 anything")).toBe("—");
    expect(formatDate("2026-08-05T10:00:00Z")).toBe("5 Aug 2026");
  });
});

describe("ADV C3: timezone boundaries (IST vs UTC day split)", () => {
  afterEach(() => setActiveFarmTimezone(null));

  it("18:30 UTC is the next day in IST but the same day in UTC", () => {
    const instant = new Date("2026-08-30T18:30:00Z");
    expect(todayInTimeZone("Asia/Kolkata", instant)).toBe("2026-08-31");
    expect(todayInTimeZone("UTC", instant)).toBe("2026-08-30");
  });

  it("a hostile/invalid farm timezone falls back to the product default", () => {
    setActiveFarmTimezone("Factory");
    const now = new Date("2026-08-30T18:30:00Z");
    expect(farmToday(now)).toBe(todayInTimeZone("Asia/Kolkata", now));
  });

  it("farmToday never emits a non-canonical string", () => {
    for (const tz of ["Asia/Kolkata", "America/New_York", "Pacific/Kiritimati", "Not/AZone"]) {
      setActiveFarmTimezone(tz);
      expect(farmToday()).toMatch(CANONICAL);
    }
  });
});
