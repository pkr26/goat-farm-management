"use client";

/**
 * URL-backed UI state. List filters, pagination offsets and selected detail
 * ids survive refresh and sharing when they live in the search string —
 * this hook is the one way pages read and write them.
 *
 * Writes go through `router.replace` (no scroll, no history entry): paging
 * and filtering are not navigation. Note this is a *soft* navigation in the
 * App Router — the params this component sees update once it commits. The
 * hook therefore latches its own pending query string, so two `set()` calls
 * in one tick (a filter plus its offset reset) compose instead of
 * clobbering each other; the latch clears as soon as `searchParams` moves,
 * whether because our write committed or because something else navigated.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef } from "react";

export type UrlStateUpdate = Record<string, string | number | null | undefined>;

/** Mirror of backend schemas/common.py MAX_PAGE_OFFSET — the inclusive
 * ceiling every paginated endpoint enforces. URL offsets must never exceed
 * it or the API rejects the request with a 422. */
export const MAX_PAGE_OFFSET = 1_000_000;

export function useUrlState() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const paramsKey = searchParams.toString();
  /** Query string of the newest not-yet-committed write, plus the params
   * snapshot it was based on. Cleared whenever `searchParams` moves. */
  const pendingRef = useRef<{ qs: string; baseKey: string } | null>(null);
  useEffect(() => {
    // Any params change — our own write committing, or an external
    // navigation — invalidates the pending base; composition restarts from
    // the committed params.
    pendingRef.current = null;
  }, [paramsKey]);

  const get = useCallback(
    (key: string, fallback: string | null = null) => searchParams.get(key) ?? fallback,
    [searchParams],
  );

  /** Numeric param clamped to [min, max]; `fallback` when missing/invalid.
   * A missing or empty param maps to the fallback explicitly — `Number(null)`
   * is 0, which would silently starve callers with non-zero defaults. */
  const getNumber = useCallback(
    (
      key: string,
      fallback: number,
      min = -Number.MAX_SAFE_INTEGER,
      max = Number.MAX_SAFE_INTEGER,
    ) => {
      const raw = searchParams.get(key);
      if (raw === null || raw.trim() === "") return fallback;
      const parsed = Number(raw);
      if (!Number.isFinite(parsed)) return fallback;
      return Math.min(Math.max(Math.trunc(parsed), min), max);
    },
    [searchParams],
  );

  /** Apply `updates`; null/undefined deletes the key (callers pass
   * `next || null` to strip pagination-style defaults). Returns the committed
   * query string — callers that mirror URL state locally can remember it as
   * "the change I initiated" so they only adopt URL changes someone else
   * made — or null when nothing changed. */
  const set = useCallback(
    (updates: UrlStateUpdate) => {
      const base = pendingRef.current
        ? new URLSearchParams(pendingRef.current.qs)
        : new URLSearchParams(searchParams.toString());
      let changed = false;
      for (const [key, value] of Object.entries(updates)) {
        const serialized =
          value === null || value === undefined || value === "" ? "" : String(value);
        const before = base.get(key);
        if (serialized === "") {
          if (before !== null) {
            base.delete(key);
            changed = true;
          }
        } else if (before !== serialized) {
          base.set(key, serialized);
          changed = true;
        }
      }
      if (!changed) return null;
      const qs = base.toString();
      pendingRef.current = { qs, baseKey: paramsKey };
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
      return qs;
    },
    [router, pathname, searchParams, paramsKey],
  );

  return { searchParams, get, getNumber, set };
}
