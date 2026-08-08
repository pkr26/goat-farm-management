/** Display formatting shared across pages (mirrors v1's utils.format_*). */

/** ₹ with Indian digit grouping (12,34,567.50); non-finite → "—". */
export function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const negative = value < 0;
  const [intPart, fracPart] = Math.abs(value).toFixed(2).split(".");
  const lastThree = intPart.slice(-3);
  const rest = intPart.slice(0, -3);
  const grouped = rest ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + lastThree : lastThree;
  return `${negative ? "-" : ""}₹${grouped}${fracPart === "00" ? "" : "." + fracPart}`;
}

/** YYYY-MM-DD of today in UTC — the backend's "today" everywhere. Use this
 *  for overdue/due-soon/late-by comparisons; browser-local today disagrees
 *  with the server during the IST 00:00–05:30 window. Date-only
 *  *writes* keep the local date — the backend accepts one day of headroom. */
export function utcToday(): string {
  return new Date().toISOString().slice(0, 10);
}

/** YYYY-MM-DD `days` after `iso` (both YYYY-MM-DD), timezone-safe. */
export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** ISO date string (YYYY-MM-DD) → "5 Aug 2026"; empty/invalid → "—". */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return "—";
  // Round-trip through Date: out-of-range values (month 13, 30 Feb, …) roll
  // over into a different calendar day, which this check rejects.
  const date = new Date(Date.UTC(y, m - 1, d));
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== m - 1 || date.getUTCDate() !== d) {
    return "—";
  }
  return `${d} ${MONTHS[m - 1]} ${y}`;
}
