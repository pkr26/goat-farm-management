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

export function usePermissions() {
  const { farmId } = useAuth();
  const query = usePermissionsApiAuthPermissionsGet({
    query: { enabled: farmId !== null },
  });
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const perms = new Set(payload?.permissions ?? []);
  return {
    loading: query.isLoading,
    isError: query.isError,
    error: query.error,
    isOwner: payload?.is_owner ?? false,
    can: (code: string) => perms.has(code),
  };
}
