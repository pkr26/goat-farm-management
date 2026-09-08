/**
 * fetchSharedPermissions: the login/farm-select prologue — fetch through the
 * shared query cache key (so the shell's usePermissions consumers reuse the
 * result), resolve the 200 payload, and convert any other settled envelope
 * into the caller-worded ApiError.
 */

import type { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import type { PermissionsOut } from "@/api/generated/models";
import { ApiError } from "@/lib/api-client";

import { fetchSharedPermissions } from "./permission-envelope";

const PAYLOAD: PermissionsOut = { is_owner: true, permissions: ["animals.view"] };

/** Captures the options handed to fetchQuery and settles `envelope`. */
function stubClient(envelope: { data: unknown; status: number }): {
  client: QueryClient;
  options: () => unknown;
} {
  let received: unknown;
  const client = {
    fetchQuery: vi.fn(async (options: unknown) => {
      received = options;
      return envelope;
    }),
  } as unknown as QueryClient;
  return { client, options: () => received };
}

describe("fetchSharedPermissions", () => {
  it("fetches via the shared permissions query options and resolves the 200 payload", async () => {
    const { client, options } = stubClient({ data: PAYLOAD, status: 200 });
    await expect(fetchSharedPermissions(client, "Could not load permissions.")).resolves.toBe(
      PAYLOAD,
    );
    const received = options() as { queryKey: readonly unknown[] };
    // Same key usePermissionsApiAuthPermissionsGet mounts — anything else
    // would refetch permissions on landing instead of reusing the result.
    expect(received.queryKey).toEqual(["/api/auth/permissions"]);
  });

  it("throws the caller-worded ApiError for a non-200 envelope", async () => {
    const { client } = stubClient({ data: { detail: "forbidden" }, status: 403 });
    const err = await fetchSharedPermissions(client, "Could not load permissions.").catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(403);
    expect((err as ApiError).detail).toBe("Could not load permissions.");
  });
});
