/**
 * invalidateFarmData's predicate decides which cached views a farm-data
 * mutation makes stale. Two halves are easy to break silently: the
 * `/api/simulation/herd-snapshot` special case (the live herd count must
 * refresh after a kidding/purchase/sale) and the deliberate exclusion of the
 * rest of /api/simulation (invalidating defaults or scenarios would overwrite
 * a user's in-progress projection draft).
 */

import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { invalidateFarmData } from "@/lib/query-invalidation";

const FARM_DATA_ROOTS = [
  "/api/animals",
  "/api/buckets",
  "/api/breeding",
  "/api/kidding",
  "/api/health",
  "/api/tasks",
  "/api/feeding",
  "/api/finance",
  "/api/purchases",
  "/api/dashboard",
  "/api/team",
];

/** Seeds one cache entry per key and reports which ones came back invalidated. */
function invalidatedAfterMutation(keys: readonly unknown[][]): Set<string> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  for (const key of keys) queryClient.setQueryData(key, { seeded: true });

  invalidateFarmData(queryClient);

  const stale = new Set<string>();
  for (const key of keys) {
    if (queryClient.getQueryState(key)?.isInvalidated) stale.add(JSON.stringify(key));
  }
  return stale;
}

describe("invalidateFarmData", () => {
  it("marks every farm-data root and its sub-paths stale", () => {
    const keys = [
      ...FARM_DATA_ROOTS.map((root) => [root]),
      ["/api/animals/7"],
      ["/api/health/schedule", { animal_id: 3 }],
      ["/api/finance/summary"],
      // A role rename or worker reassignment must bust /api/tasks and
      // /api/dashboard caches, which embed role/worker display names.
      ["/api/team/roles/3"],
    ];

    expect(invalidatedAfterMutation(keys)).toEqual(
      new Set(keys.map((key) => JSON.stringify(key))),
    );
  });

  it("refreshes the live herd snapshot without disturbing a simulation draft", () => {
    const snapshot = ["/api/simulation/herd-snapshot"];
    const defaults = ["/api/simulation/defaults"];
    const scenarios = ["/api/simulation/scenarios"];

    const stale = invalidatedAfterMutation([snapshot, defaults, scenarios]);

    expect(stale).toEqual(new Set([JSON.stringify(snapshot)]));
  });

  it("leaves auth and unrelated keys alone", () => {
    const keys = [
      ["/api/auth/farms"],
      ["/api/auth/permissions"],
      ["/api/reports/summary"],
      // A path that merely shares a prefix must not match either.
      ["/api/animalsx"],
      [42],
    ];

    expect(invalidatedAfterMutation(keys)).toEqual(new Set());
  });
});
