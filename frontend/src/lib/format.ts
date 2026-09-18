/** Display formatting shared across pages (mirrors v1's utils.format_*). */

import { getActiveLanguage } from "@/lib/active-language";

/** ₹ with Indian digit grouping (12,34,567.50); non-finite → "—". */
export function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  // Number#toFixed switches to exponential notation at 1e21. Feeding that
  // string into the manual grouping below produced output such as
  // `₹1e,+21.undefined`; values at this magnitude have no representable paise,
  // so Intl can safely render the integer digits directly.
  if (Math.abs(value) >= 1e21) {
    const grouped = Math.abs(value).toLocaleString("en-IN", {
      maximumFractionDigits: 0,
      useGrouping: true,
    });
    return `${value < 0 ? "-" : ""}₹${grouped}`;
  }
  const [intPart, fracPart] = Math.abs(value).toFixed(2).split(".");
  // Determine the sign after display rounding so tiny negative floats do not
  // render as the impossible accounting value "-₹0".
  const negative = value < 0 && (intPart !== "0" || fracPart !== "00");
  const lastThree = intPart.slice(-3);
  const rest = intPart.slice(0, -3);
  const grouped = rest ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + lastThree : lastThree;
  return `${negative ? "-" : ""}₹${grouped}${fracPart === "00" ? "" : "." + fracPart}`;
}

/** ₹ with Indian digit grouping for API Decimal-as-string amounts
 * ("1234567.50"): grouped as text, never through a float, so paise survive
 * intact at any magnitude. Unparseable/missing → "—". */
export function formatMoneyDecimal(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const trimmed = value.trim();
  if (!/^-?\d+(\.\d+)?$/.test(trimmed)) return "—";
  const [intRaw, fracRaw = ""] = trimmed.replace(/^-/, "").split(".");
  const intPart = intRaw.replace(/^0+(?=\d)/, "");
  // Two paise digits by convention; more arrive only from a wider-precision
  // wire and are shown verbatim rather than silently rounded.
  const fracPart = fracRaw === "" ? "00" : fracRaw.padEnd(2, "0");
  const negative = trimmed.startsWith("-") && (intPart !== "0" || fracPart !== "00");
  const lastThree = intPart.slice(-3);
  const rest = intPart.slice(0, -3);
  const grouped = rest ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + lastThree : lastThree;
  return `${negative ? "-" : ""}₹${grouped}${fracPart === "00" ? "" : "." + fracPart}`;
}

/** YYYY-MM-DD of today in UTC. Retained for UTC-specific utilities/tests;
 * business dates should use farmToday() so they follow the selected farm. */
export function utcToday(): string {
  return new Date().toISOString().slice(0, 10);
}

export const DEFAULT_FARM_TIMEZONE = "Asia/Kolkata";
let activeFarmTimezone = DEFAULT_FARM_TIMEZONE;

/** Keep date-only business rules aligned with the farm selected in the API
 * client. This is intentionally a tiny external-store value: schemas and
 * non-React formatting helpers can consult it at validation time. */
export function setActiveFarmTimezone(timezone: string | null | undefined): void {
  activeFarmTimezone = timezone || DEFAULT_FARM_TIMEZONE;
}

/** Every formatter built from a farm timezone must go through this. The zone
 * arrives from the API, and Intl rejects names Python's zoneinfo and
 * PostgreSQL both accept ("Factory"), so an unguarded constructor throws
 * RangeError mid-render for everyone on that farm. */
function farmTimeZoneFormat(
  locale: string,
  options: Intl.DateTimeFormatOptions,
  timezone: string,
): Intl.DateTimeFormat {
  try {
    return new Intl.DateTimeFormat(locale, { ...options, timeZone: timezone });
  } catch {
    return new Intl.DateTimeFormat(locale, { ...options, timeZone: DEFAULT_FARM_TIMEZONE });
  }
}

/** YYYY-MM-DD in an IANA timezone, built from parts so it never depends on
 * locale-specific string ordering. Invalid/stale timezone values safely use
 * the product default. */
export function todayInTimeZone(
  timezone: string,
  now: Date = new Date(),
): string {
  const parts = Object.fromEntries(
    farmTimeZoneFormat(
      "en-US",
      { year: "numeric", month: "2-digit", day: "2-digit" },
      timezone,
    )
      .formatToParts(now)
      .filter((part) => part.type === "year" || part.type === "month" || part.type === "day")
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function farmToday(now: Date = new Date()): string {
  return todayInTimeZone(activeFarmTimezone, now);
}

/** Construct a UTC calendar date without Date.UTC's surprising 1900 offset
 * for years 0–99. Returning null on a failed round-trip also prevents the
 * Date constructor from silently normalizing impossible values such as 30
 * February into a different source date. */
function exactUtcCalendarDate(year: number, month: number, day: number): Date | null {
  if (
    !Number.isInteger(year) ||
    !Number.isInteger(month) ||
    !Number.isInteger(day) ||
    year < 1
  ) {
    return null;
  }
  // Start from an unambiguous instant, then set the full year explicitly.
  // `new Date(Date.UTC(1, …))` would mean 1901, whereas setUTCFullYear(1)
  // correctly represents the year 0001.
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  if (
    Number.isNaN(date.getTime()) ||
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null;
  }
  return date;
}

/** Backend datetimes without an offset are UTC. Render the instant in the
 * active farm timezone so completion/audit times agree for every operator.
 * The year is always rendered: these are audit rows, and two episodes twelve
 * months apart would otherwise be indistinguishable. English format matches
 * formatDate ("5 Aug 2026") plus a lowercase 12-hour clock — "5 Aug 2026,
 * 2:30 pm"; Telugu renders through te-IN so month names follow the worker's
 * language. */
export function formatFarmDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const date = new Date(hasOffset ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return "—";
  const options: Intl.DateTimeFormatOptions = {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  };
  if (getActiveLanguage() === "te") {
    return farmTimeZoneFormat("te-IN", options, activeFarmTimezone).format(date);
  }
  const parts = Object.fromEntries(
    farmTimeZoneFormat("en-GB", options, activeFarmTimezone)
      .formatToParts(date)
      .filter((part) =>
        ["day", "month", "year", "hour", "minute", "dayPeriod"].includes(part.type),
      )
      .map((part) => [part.type, part.value]),
  );
  return `${parts.day} ${parts.month} ${parts.year}, ${parts.hour}:${parts.minute} ${parts.dayPeriod}`;
}

/** YYYY-MM-DD `days` after `iso` (both YYYY-MM-DD), timezone-safe.
 *
 * Degrades instead of throwing: callers feed it backend-supplied dates, and a
 * malformed value used to escape as `RangeError: Invalid time value` from
 * `toISOString()` — including from inside render-path `useMemo` calls. Invalid
 * input returns the input unchanged; years past 9999 are formatted manually
 * because `toISOString()` switches to an expanded `+10000-…` spelling whose
 * first ten characters are no longer a date. */
export function addDays(iso: string, days: number): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match || !Number.isFinite(days)) return iso;
  const [y, m, d] = match.slice(1).map(Number);
  const date = exactUtcCalendarDate(y, m, d);
  if (date === null) return iso;
  date.setUTCDate(date.getUTCDate() + days);
  if (Number.isNaN(date.getTime())) return iso;
  const year = String(date.getUTCFullYear()).padStart(4, "0");
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Whole days from `from` to `to` (both YYYY-MM-DD), timezone-safe. */
export function daysBetween(from: string, to: string): number {
  const parse = (iso: string): Date | null => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    if (!match) return null;
    const [year, month, day] = match.slice(1).map(Number);
    return exactUtcCalendarDate(year, month, day);
  };
  const fromDate = parse(from);
  const toDate = parse(to);
  // The helper is called directly from render paths. A corrupt API date must
  // not turn a badge into "NaN d late" (or be normalized to a different day).
  if (fromDate === null || toDate === null) return 0;
  return Math.round((toDate.getTime() - fromDate.getTime()) / 86400000);
}

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** ISO date string (YYYY-MM-DD) → "5 Aug 2026"; empty/invalid → "—".
 * Telugu sessions render through Intl te-IN ("5 ఆగ 2026"); English keeps the
 * hand-built abbreviation table so existing output stays byte-identical. */
export function formatDate(iso: string | null | undefined, lang?: "en" | "te"): string {
  if (!iso) return "—";
  // Accept the documented date-only value, a syntactically complete ISO
  // datetime suffix, and harmless trailing whitespace. Merely starting with
  // date-shaped characters is not enough (`2026-08-05Tgarbage` and
  // `2026-08-05 anything` are not ISO dates).
  const match = /^(\d{4})-(\d{1,2})-(\d{1,2})(T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\s*$/.exec(
    iso,
  );
  if (!match) return "—";
  const [y, m, d] = match.slice(1, 4).map(Number);
  const date = exactUtcCalendarDate(y, m, d);
  if (date === null) return "—";
  const timestampSuffix = match[4];
  if (timestampSuffix) {
    const paddedDate = `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    if (Number.isNaN(new Date(`${paddedDate}${timestampSuffix}`).getTime())) return "—";
  }
  if ((lang ?? getActiveLanguage()) === "te") {
    return new Intl.DateTimeFormat("te-IN", {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    }).format(date);
  }
  return `${d} ${MONTHS[m - 1]} ${y}`;
}
