import { farmScopeEpochValue } from "@/lib/api-client";

/**
 * Fence for async write continuations across a farm switch.
 *
 * Capture before `mutateAsync`, then call `stillCurrent()` when the response
 * arrives: it returns false once the operator has switched farms, so the
 * stale continuation must not toast, close dialogs or invalidate queries
 * into the new farm's UI. The write itself is always safe — X-Farm-Id is
 * captured per request — this protects only the continuation.
 *
 * Same contract health/kidding/breeding already inline; hoisted here so
 * every write surface gets it (M-2's remaining class).
 */
export function captureFarmScope(): () => boolean {
  const epoch = farmScopeEpochValue();
  return () => farmScopeEpochValue() === epoch;
}
