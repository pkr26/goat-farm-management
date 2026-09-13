"use client";

/**
 * Permission set of the current user on the active farm (drives nav +
 * button visibility, mirroring v1's base.html `perms` checks).
 *
 * `isError`/`error` distinguish "the permissions call failed" from a genuine
 * empty set — pages must render an error state instead of "no access"
 *. `isOwner` mirrors the backend's owner exemption (e.g. owners
 * may verify their own completions,).
 */

import { usePermissionsApiAuthPermissionsGet } from "@/api/generated/endpoints";
import { useAuth } from "@/lib/auth-context";

/** What usePermissions() resolves to — named so PermissionGate (and page
 * content components receiving the same single observer's result) can share
 * the object without re-subscribing a second query observer. */
export type PermissionsState = {
  loading: boolean;
  isError: boolean;
  error: unknown;
  refetch: ReturnType<typeof usePermissionsApiAuthPermissionsGet>["refetch"];
  isOwner: boolean;
  can: (code: string) => boolean;
};

export function usePermissions(): PermissionsState {
  const { farmId } = useAuth();
  const query = usePermissionsApiAuthPermissionsGet({
    query: { enabled: farmId !== null },
  });
  // TanStack Query retains the last successful data when a background
  // refetch fails. Permissions must fail closed once that error is known;
  // otherwise a revoked/stale grant can keep navigation and actions enabled
  // merely because an older payload is still cached.
  const payload =
    !query.isError && query.data?.status === 200 ? query.data.data : undefined;
  // Stryker disable next-line ArrayDeclaration: the fallback set's only reader is can(), and no real permission code equals the placeholder string
  const perms = new Set(payload?.permissions ?? []);
  return {
    // While the auth bootstrap hasn't produced a farm yet the permissions
    // query is disabled — isLoading is false with an EMPTY set. Treating
    // that window as "loaded" let every page flash "You don't have access"
    // for a beat before the real answer arrived.
    loading: query.isLoading || farmId === null,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
    isOwner: payload?.is_owner ?? false,
    can: (code: string) => perms.has(code),
  };
}
