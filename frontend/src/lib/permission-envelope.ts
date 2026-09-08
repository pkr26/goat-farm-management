/** Shared post-auth permissions fetch used by login and farm selection to
 *  resolve the landing route: both pages ran the identical fetchQuery +
 *  envelope-narrowing prologue inline. */

import type { QueryClient } from "@tanstack/react-query";

import { getPermissionsApiAuthPermissionsGetQueryOptions } from "@/api/generated/endpoints";
import type { PermissionsOut } from "@/api/generated/models";
import { ApiError } from "@/lib/api-client";

/**
 * Fetches the permissions envelope through the shared query cache (the same
 * key the shell's usePermissions consumers use) so landing on the first page
 * reuses this result instead of refetching permissions a second time.
 *
 * The fetch core rejects non-2xx before an envelope is built, so a settled
 * envelope is always 200; the narrowing is for the type system only, not a
 * reachable error path. `failureDetail` becomes the ApiError detail should
 * that unreachable branch ever fire (login and farm-select word it
 * differently for their own error surfaces).
 */
export async function fetchSharedPermissions(
  queryClient: QueryClient,
  failureDetail: string,
): Promise<PermissionsOut> {
  const envelope = await queryClient.fetchQuery(
    getPermissionsApiAuthPermissionsGetQueryOptions(),
  );
  if (envelope.status !== 200) {
    throw new ApiError(envelope.status, failureDetail);
  }
  return envelope.data;
}
