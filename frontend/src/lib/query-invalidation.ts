import type { Query, QueryClient } from "@tanstack/react-query";

/**
 * Farm data has cross-domain side effects (a purchase creates animals, tasks
 * and finance rows; a status or kidding changes dashboards, buckets and feed
 * plans). Generated query keys contain the URL as their first element, so a
 * single predicate can mark every affected farm view stale without coupling
 * mutations to a long list of generated hook names.
 *
 * Authentication, static simulation defaults and saved scenarios are
 * deliberately excluded. Invalidating defaults while a user edits a
 * simulation would overwrite their draft when the defaults query refetched.
 */
const FARM_DATA_PATHS = [
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
] as const;

function isFarmDataQuery(query: Query): boolean {
  const path = query.queryKey[0];
  if (typeof path !== "string") return false;
  if (path === "/api/simulation/herd-snapshot") return true;
  return FARM_DATA_PATHS.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

/** Mark all cached farm-derived views stale; only currently mounted queries
 * refetch immediately, while inactive pages refresh on the next visit. */
export function invalidateFarmData(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ predicate: isFarmDataQuery });
}
