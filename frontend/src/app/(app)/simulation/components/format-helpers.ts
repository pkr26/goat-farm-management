/** Small pure formatters shared by the simulation page and its extracted
 * result/editor components. Kept local to the route: they encode this
 * page's display conventions (em-dash nulls, one-decimal heads), not
 * app-wide formatting rules. */

/** snake_case → Title Case ("horizon_months" → "Horizon Months"). */
export function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Ratio with a fixed precision; non-finite (e.g. BCR = inf) or null → "—". */
export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

/** IRR is a fraction (0.18 → "18.0%"); null → "—". */
export function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(1)}%`;
}

/** Cohort head counts are expected values (float64), so they are almost never
 * integral. Render them at the precision the backend narrative uses. */
export function formatHead(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : value.toFixed(1);
}
