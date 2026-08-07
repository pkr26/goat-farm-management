"use client";

/**
 * Permission set of the current user on the active farm (drives nav +
 * button visibility, mirroring v1's base.html `perms` checks).
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
    can: (code: string) => perms.has(code),
  };
}
