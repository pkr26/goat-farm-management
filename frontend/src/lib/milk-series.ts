/**
 * Milk trend series preparation.
 *
 * The summary endpoint returns one row per day that has records, newest
 * first (backend `services/milk.py` orders by date descending). Charts need
 * the opposite: ascending order, one point per calendar day, with unrecorded
 * days shown as zero litres — a gap in the parlour log is real information,
 * not a day that never happened. Everything that plots the daily series goes
 * through here so the sparkline window and the trend axis can't disagree.
 */

import type { MilkDayTotalOut } from "@/api/generated/models";

/** Shift a date-only ISO string (YYYY-MM-DD) by N days, staying in UTC so
 * DST transitions can't truncate the result. */
export function shiftIsoDate(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const utc = Date.UTC(year, month - 1, day) + days * 86_400_000;
  const shifted = new Date(utc);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`;
}

export interface DailyMilkPoint {
  date: string;
  litres: number;
  /** False on zero-filled days with no recorded milk. */
  recorded: boolean;
}

/** Normalise the API's newest-first sparse daily totals into an ascending,
 * zero-filled series covering the `windowDays` ending on `reference`. */
export function buildDailySeries(
  daily: MilkDayTotalOut[],
  windowDays: number,
  reference: string,
): DailyMilkPoint[] {
  const byDate = new Map(daily.map((day) => [day.date, day.litres]));
  const series: DailyMilkPoint[] = [];
  for (let offset = windowDays - 1; offset >= 0; offset--) {
    const date = shiftIsoDate(reference, -offset);
    const litres = byDate.get(date);
    series.push({ date, litres: litres ?? 0, recorded: litres !== undefined });
  }
  return series;
}
