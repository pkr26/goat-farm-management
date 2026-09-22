import { randomUUID } from "node:crypto";

import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
} from "@playwright/test";

import type {
  AnimalOut,
  FarmOut,
  MembershipOut,
  TeamOut,
  TokenOut,
} from "../src/api/generated/models";

/**
 * Cross-tenant and horizontal-authorization negatives (RT-R-5): every other
 * spec proves what an identity *may* do; this one pins what it must not. A
 * regression in farm scoping or permission enforcement that unit tests miss
 * would otherwise sail through e2e green. All provisioning goes through the
 * same real API global-setup uses, issued through the frontend's /api
 * rewrite so the assertions cover the exact proxy path a browser takes.
 */

const PROXY_ORIGIN = "http://localhost:3000";
const FARM_TIMEZONE = "America/Phoenix";

interface Identity {
  email: string;
  password: string;
  token: string;
}

function uniqueSuffix(): string {
  return `${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`;
}

async function expectStatus(response: APIResponse, status: number): Promise<void> {
  expect(new URL(response.url()).origin).toBe(PROXY_ORIGIN);
  expect(response.status()).toBe(status);
}

async function jsonBody<T>(response: APIResponse, status: number): Promise<T> {
  await expectStatus(response, status);
  return (await response.json()) as T;
}

/** Register a fresh identity and provision its first farm, mirroring the
 *  provisioning pattern global-setup.ts uses. */
async function provisionFarm(
  request: APIRequestContext,
  label: string,
): Promise<{ owner: Identity; farm: FarmOut }> {
  const suffix = uniqueSuffix();
  const owner: Identity = {
    email: `${label}-${suffix}@goatfarm.test`,
    password: `Initial-${suffix}-pass`,
    token: "",
  };
  const register = await jsonBody<TokenOut>(
    await request.post("/api/auth/register", {
      data: { email: owner.email, password: owner.password, name: `${label} ${suffix}` },
    }),
    201,
  );
  owner.token = register.access_token;

  // POST /api/auth/farms requires an Idempotency-Key; a fresh random key
  // makes each provisioning request a distinct first submission.
  const farm = await jsonBody<FarmOut>(
    await request.post("/api/auth/farms", {
      data: {
        name: `${label} Farm ${suffix}`,
        location: "E2E cross-tenant fixture",
        timezone: FARM_TIMEZONE,
      },
      headers: {
        Authorization: `Bearer ${owner.token}`,
        "Idempotency-Key": randomUUID(),
      },
    }),
    201,
  );
  return { owner, farm };
}

function farmHeaders(owner: Identity, farmId: number): Record<string, string> {
  return { Authorization: `Bearer ${owner.token}`, "X-Farm-Id": String(farmId) };
}

test.describe("cross-tenant and horizontal authorization negatives", () => {
  test("a provisioned worker without finance.view is refused 403 on the finance API", async ({
    request,
  }) => {
    test.setTimeout(120_000);
    const { owner, farm } = await provisionFarm(request, "rbac-negative");

    const team = await jsonBody<TeamOut>(
      await request.get("/api/team", { headers: farmHeaders(owner, farm.id) }),
      200,
    );
    const cleaner = team.roles.find((role) => role.code === "CLEANER");
    if (!cleaner) throw new Error("New farm did not seed the CLEANER role.");
    // The seeded CLEANER bundle intentionally has no finance.view.
    expect(cleaner.permissions).not.toContain("finance.view");

    const suffix = uniqueSuffix();
    const workerEmail = `finance-worker-${suffix}@goatfarm.test`;
    const workerPassword = `Worker-${suffix}-pass`;
    await jsonBody<MembershipOut>(
      await request.post("/api/team/workers", {
        data: {
          email: workerEmail,
          password: workerPassword,
          name: `Financeless Worker ${suffix}`,
          role_id: cleaner.id,
        },
        headers: { ...farmHeaders(owner, farm.id), "Idempotency-Key": randomUUID() },
      }),
      201,
    );

    // Take possession of the credential so the must-change-password fence no
    // longer explains a denial; what remains must be the permission check.
    const login = await jsonBody<TokenOut>(
      await request.post("/api/auth/login", {
        data: { email: workerEmail, password: workerPassword },
      }),
      200,
    );
    const rotated = await jsonBody<TokenOut>(
      await request.post("/api/auth/change-password", {
        data: {
          current_password: workerPassword,
          new_password: `Owned-${uniqueSuffix()}-pass`,
        },
        headers: { Authorization: `Bearer ${login.access_token}` },
      }),
      200,
    );

    const denied = await request.get("/api/finance", {
      headers: {
        Authorization: `Bearer ${rotated.access_token}`,
        "X-Farm-Id": String(farm.id),
      },
    });
    await expectStatus(denied, 403);
    expect(await denied.json()).toEqual({
      detail: "Missing permission: finance.view",
      code: "PERMISSION_DENIED",
    });
  });

  test("another farm's owner cannot read this farm's animal: uniform 404s", async ({
    request,
  }) => {
    test.setTimeout(120_000);
    const { owner: ownerA, farm: farmA } = await provisionFarm(request, "tenant-a");
    const { owner: ownerB, farm: farmB } = await provisionFarm(request, "tenant-b");

    const animal = await jsonBody<AnimalOut>(
      await request.post("/api/animals", {
        data: {
          tag_number: `XACT-${uniqueSuffix()}`,
          sex: "F",
          source: "BORN",
          current_bucket: "FOUNDATION",
          historical_import_reason: "E2E cross-tenant isolation fixture",
        },
        headers: { ...farmHeaders(ownerA, farmA.id), "Idempotency-Key": randomUUID() },
      }),
      201,
    );
    // Control: the owning farm reads the same record through the same proxy.
    await expectStatus(
      await request.get(`/api/animals/${animal.id}`, {
        headers: farmHeaders(ownerA, farmA.id),
      }),
      200,
    );

    // Foreign owner with an honest X-Farm-Id: the farm-scoped query finds
    // nothing — 404, indistinguishable from a never-existing id.
    const foreign = await request.get(`/api/animals/${animal.id}`, {
      headers: farmHeaders(ownerB, farmB.id),
    });
    await expectStatus(foreign, 404);
    expect(await foreign.json()).toEqual({ detail: "Animal not found" });

    // Forged X-Farm-Id naming the victim farm: unknown and forbidden farms
    // share one 404 so tenant ids cannot be enumerated.
    const forged = await request.get(`/api/animals/${animal.id}`, {
      headers: farmHeaders(ownerB, farmA.id),
    });
    await expectStatus(forged, 404);
    expect(await forged.json()).toEqual({ detail: "Farm not found" });
  });

  test("logged-out API access is refused 401", async ({ request }) => {
    const anonymous = await request.get("/api/animals", {
      headers: { "X-Farm-Id": "1" },
    });
    await expectStatus(anonymous, 401);
    expect(await anonymous.json()).toEqual({
      detail: "Missing bearer token",
      code: "UNAUTHENTICATED",
    });
  });
});
