/**
 * Daily milk series preparation: the API returns recorded days newest-first
 * and sparse; charts need ascending, zero-filled days. Every ordering bug in
 * the milk trend (reversed axis, wrong sparkline window) traces back to
 * skipping this normalisation, so it is pinned here.
 */

import { describe, expect, it } from "vitest";

import { buildDailySeries, shiftIsoDate } from "./milk-series";

function day(date: string, litres: number) {
  return { date, litres, recorded_animals: 1, avg_fat_pct: null };
}

describe("shiftIsoDate", () => {
  it("shifts forward and backward across month and year boundaries", () => {
    expect(shiftIsoDate("2026-08-31", 1)).toBe("2026-09-01");
    expect(shiftIsoDate("2026-01-01", -1)).toBe("2025-12-31");
    expect(shiftIsoDate("2024-02-28", 1)).toBe("2024-02-29"); // leap year
  });
});

describe("buildDailySeries", () => {
  const REFERENCE = "2026-09-01";

  it("returns an ascending, zero-filled day for every day of the window", () => {
    // API order: newest first, and only days with records.
    const series = buildDailySeries(
      [day("2026-09-01", 10), day("2026-08-30", 8), day("2026-08-25", 6)],
      7,
      REFERENCE,
    );

    expect(series).toHaveLength(7);
    // Oldest day first, reference day last — the trend axis reads left→right.
    expect(series.map((point) => point.date)).toEqual([
      "2026-08-26",
      "2026-08-27",
      "2026-08-28",
      "2026-08-29",
      "2026-08-30",
      "2026-08-31",
      "2026-09-01",
    ]);
    expect(series[0]).toEqual({ date: "2026-08-26", litres: 0, recorded: false });
    expect(series[4]).toEqual({ date: "2026-08-30", litres: 8, recorded: true });
    expect(series[6]).toEqual({ date: "2026-09-01", litres: 10, recorded: true });
  });

  it("feeds the sparkline the most recent days, not the oldest", () => {
    // A 30-day window with a single recorded day three days back.
    const series = buildDailySeries([day("2026-08-29", 5)], 30, REFERENCE);
    const sparkline = series.slice(-14).map((point) => point.litres);
    expect(sparkline).toHaveLength(14);
    expect(sparkline[10]).toBe(5); // 2026-08-29 is 3 days before the reference
    expect(sparkline.filter((value) => value > 0)).toEqual([5]);
  });

  it("handles an empty daily payload with an all-zero ascending window", () => {
    const series = buildDailySeries([], 3, REFERENCE);
    expect(series).toEqual([
      { date: "2026-08-30", litres: 0, recorded: false },
      { date: "2026-08-31", litres: 0, recorded: false },
      { date: "2026-09-01", litres: 0, recorded: false },
    ]);
  });
});
