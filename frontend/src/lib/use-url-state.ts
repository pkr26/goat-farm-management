"use client";

/**
 * URL-backed UI state. List filters, pagination offsets and selected detail
 * ids survive refresh, back/forward and sharing when they live in the search
 * string — this hook is the one way pages read and write them.
 *
 * Writes are shallow (`router.replace`, no scroll): filtering is not
 * navigation history.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

export type UrlStateUpdate = Record<string, string | number | null | undefined>;

export function useUrlState() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const get = useCallback(
    (key: string, fallback: string | null = null) => searchParams.get(key) ?? fallback,
    [searchParams],
  );

  /** Numeric param clamped to [min, max]; `fallback` when missing/invalid. */
  const getNumber = useCallback(
    (
      key: string,
      fallback: number,
      min = -Number.MAX_SAFE_INTEGER,
      max = Number.MAX_SAFE_INTEGER,
    ) => {
      const parsed = Number(searchParams.get(key));
      if (!Number.isFinite(parsed)) return fallback;
      return Math.min(Math.max(Math.trunc(parsed), min), max);
    },
    [searchParams],
  );

  const set = useCallback(
    (updates: UrlStateUpdate) => {
      const params = new URLSearchParams(searchParams.toString());
      let changed = false;
      for (const [key, value] of Object.entries(updates)) {
        const serialized = value === null || value === undefined ? "" : String(value);
        const before = params.get(key);
        if (serialized === "") {
          if (before !== null) {
            params.delete(key);
            changed = true;
          }
        } else if (before !== serialized) {
          params.set(key, serialized);
          changed = true;
        }
      }
      if (!changed) return;
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams],
  );

  return { searchParams, get, getNumber, set };
}
