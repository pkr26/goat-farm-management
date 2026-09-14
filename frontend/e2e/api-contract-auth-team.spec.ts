import { randomUUID } from "node:crypto";

import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
} from "@playwright/test";

import type {
  AccountDeleteIn,
  AccountExportOut,
  ChangePasswordIn,
  FarmCreateIn,
  FarmOut,
  LoginIn,
  MembershipOut,
  PasswordResetIn,
  ReadinessStatusOut,
  RegisterIn,
  RoleIn,
  RoleOut,
  RoleUpdateIn,
  TeamOut,
  TokenOut,
  UserOut,
  WorkerCreateIn,
} from "../src/api/generated/models";

const FRONTEND_ORIGIN = "http://localhost:3000";

interface Identity {
  email: string;
  name: string;
  password: string;
  token: string;
  userId: number;
}

interface FarmIdentity {
  farm: FarmOut;
  headers: Record<string, string>;
}

function uniqueSuffix(): string {
  return `${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`;
}

function expectedUrl(path: string): string {
  return new URL(path, FRONTEND_ORIGIN).toString();
}

async function jsonResponse<T>(
  response: APIResponse,
  path: string,
  status: number,
): Promise<T> {
  expect(response.url()).toBe(expectedUrl(path));
  expect(response.status()).toBe(status);
  // The body must be JSON — the parse below rejects on anything else. The
  // content-type header itself is not asserted because the Next dev
  // server's rewrite proxy rewrites it to text/plain (the prod standalone
  // server preserves application/json).
  return (await response.json()) as T;
}

async function emptyResponse(
  response: APIResponse,
  path: string,
  status: number,
): Promise<void> {
  expect(response.url()).toBe(expectedUrl(path));
  expect(response.status()).toBe(status);
  expect(await response.text()).toBe("");
}

function expectPrivateResponse(response: APIResponse): void {
  expect(response.headers()["cache-control"]).toBe("no-store");
  expect(response.headers().pragma).toBe("no-cache");
}

function expectTokenOut(
  body: TokenOut,
  expected: { email: string; name: string | null; userId?: number },
): void {
  expect(body).toEqual({
    access_token: expect.stringMatching(/^[^.]+\.[^.]+\.[^.]+$/),
    token_type: "bearer",
    user: {
      id: expected.userId ?? expect.any(Number),
      email: expected.email,
      name: expected.name,
      must_change_password: expect.any(Boolean),
    },
  });
  expect(body.user.id).toBeGreaterThan(0);
}

function expectMembershipOut(
  body: MembershipOut,
  expected: {
    email: string;
    name: string | null;
    roleId: number;
    roleName: string;
    membershipId?: number;
    userId?: number;
  },
): void {
  expect(body).toEqual({
    id: expected.membershipId ?? expect.any(Number),
    user_id: expected.userId ?? expect.any(Number),
    email: expected.email,
    name: expected.name,
    role_id: expected.roleId,
    role_name: expected.roleName,
    is_active: true,
    can_reset_password: true,
    reset_password_block_reason: null,
  });
  expect(body.id).toBeGreaterThan(0);
  expect(body.user_id).toBeGreaterThan(0);
}

function expectRoleOut(body: RoleOut, expected: Omit<RoleOut, "id"> & { id?: number }): void {
  expect(body).toEqual({
    id: expected.id ?? expect.any(Number),
    code: expected.code,
    name: expected.name,
    description: expected.description,
    permissions: expected.permissions,
    revision: expected.revision,
    member_count: expected.member_count,
  });
  expect(body.id).toBeGreaterThan(0);
}

function expectIsoTimestamp(value: string): void {
  expect(Number.isNaN(Date.parse(value))).toBe(false);
}

async function refreshCookieValue(request: APIRequestContext): Promise<string> {
  const state = await request.storageState();
  const cookies = state.cookies.filter((cookie) =>
    /^(?:__Host-)?goatfarm_refresh$/.test(cookie.name),
  );
  expect(cookies).toHaveLength(1);
  const cookie = cookies[0];
  expect(cookie).toEqual(
    expect.objectContaining({
      domain: "localhost",
      httpOnly: true,
      path: "/",
      sameSite: "Lax",
    }),
  );
  expect(cookie.value.length).toBeGreaterThan(20);
  return cookie.value;
}

async function registerIdentity(
  request: APIRequestContext,
  label: string,
): Promise<Identity> {
  const suffix = uniqueSuffix();
  const payload: RegisterIn = {
    email: `${label}-${suffix}@goatfarm.test`,
    password: `Initial-${suffix}-pass`,
    name: `${label} ${suffix}`,
  };
  expect(payload).toStrictEqual({
    email: `${label}-${suffix}@goatfarm.test`,
    password: `Initial-${suffix}-pass`,
    name: `${label} ${suffix}`,
  });

  const response = await request.post("/api/auth/register", { data: payload });
  const body = await jsonResponse<TokenOut>(response, "/api/auth/register", 201);
  expectPrivateResponse(response);
  expectTokenOut(body, { email: payload.email, name: payload.name ?? null });
  await refreshCookieValue(request);
  return {
    email: payload.email,
    name: payload.name ?? "",
    password: payload.password,
    token: body.access_token,
    userId: body.user.id,
  };
}

async function createFarm(
  request: APIRequestContext,
  owner: Identity,
  label: string,
): Promise<FarmIdentity> {
  const payload: FarmCreateIn = {
    name: `${label} ${uniqueSuffix()}`,
    location: "E2E API contract pasture",
    timezone: "America/Phoenix",
  };
  expect(payload).toStrictEqual({
    name: payload.name,
    location: "E2E API contract pasture",
    timezone: "America/Phoenix",
  });
  const response = await request.post("/api/auth/farms", {
    data: payload,
    // Farm creation requires an Idempotency-Key (RT-M-1).
    headers: { Authorization: `Bearer ${owner.token}`, "Idempotency-Key": crypto.randomUUID() },
  });
  const farm = await jsonResponse<FarmOut>(response, "/api/auth/farms", 201);
  expectPrivateResponse(response);
  expect(farm).toEqual({
    id: expect.any(Number),
    name: payload.name,
    location: payload.location,
    timezone: payload.timezone,
    role: null,
  });
  expect(farm.id).toBeGreaterThan(0);
  return {
    farm,
    headers: {
      Authorization: `Bearer ${owner.token}`,
      "X-Farm-Id": String(farm.id),
    },
  };
}

test.describe("frontend proxy auth and team API contracts", () => {
  test.describe.configure({ mode: "serial" });

  test("proxies the liveness and database-readiness probes", async ({ request }) => {
    // /healthz is the frontend process's own liveness probe and answers with
    // the plain text body "ok" (not JSON); /readyz proxies the backend's
    // database-readiness JSON.
    const healthResponse = await request.get("/healthz");
    expect(healthResponse.url()).toBe(expectedUrl("/healthz"));
    expect(healthResponse.status()).toBe(200);
    expect(await healthResponse.text()).toBe("ok");

    const readyResponse = await request.get("/readyz");
    const readiness = await jsonResponse<ReadinessStatusOut>(readyResponse, "/readyz", 200);
    expect(readiness).toStrictEqual({ status: "ready" });
  });

  test("persists password changes and returns exact me/export data through the proxy", async ({
    request,
  }) => {
    test.setTimeout(120_000);
    const owner = await registerIdentity(request, "auth-contract-owner");
    const { farm } = await createFarm(request, owner, "Auth Contract Farm");
    const originalCookie = await refreshCookieValue(request);
    const changedPassword = `Changed-${uniqueSuffix()}-pass`;
    const changePayload: ChangePasswordIn = {
      current_password: owner.password,
      new_password: changedPassword,
    };
    expect(changePayload).toStrictEqual({
      current_password: owner.password,
      new_password: changedPassword,
    });

    const changeResponse = await request.post("/api/auth/change-password", {
      data: changePayload,
      headers: { Authorization: `Bearer ${owner.token}` },
    });
    const changed = await jsonResponse<TokenOut>(
      changeResponse,
      "/api/auth/change-password",
      200,
    );
    expectPrivateResponse(changeResponse);
    expectTokenOut(changed, {
      email: owner.email,
      name: owner.name,
      userId: owner.userId,
    });
    expect(changed.access_token).not.toBe(owner.token);
    const changedCookie = await refreshCookieValue(request);
    expect(changedCookie).not.toBe(originalCookie);

    // This succeeds only if the HttpOnly refresh cookie issued through the
    // Next rewrite was stored and sent back by Playwright's real cookie jar.
    const refreshResponse = await request.post("/api/auth/refresh", {
      headers: { Origin: FRONTEND_ORIGIN },
    });
    const refreshed = await jsonResponse<TokenOut>(refreshResponse, "/api/auth/refresh", 200);
    expectPrivateResponse(refreshResponse);
    expectTokenOut(refreshed, {
      email: owner.email,
      name: owner.name,
      userId: owner.userId,
    });
    expect(await refreshCookieValue(request)).not.toBe(changedCookie);

    const meResponse = await request.get("/api/auth/me", {
      headers: { Authorization: `Bearer ${refreshed.access_token}` },
    });
    const me = await jsonResponse<UserOut>(meResponse, "/api/auth/me", 200);
    expectPrivateResponse(meResponse);
    expect(me).toStrictEqual({
      id: owner.userId,
      email: owner.email,
      name: owner.name,
      must_change_password: expect.any(Boolean),
    });

    const exportResponse = await request.get("/api/auth/account/export", {
      headers: { Authorization: `Bearer ${refreshed.access_token}` },
    });
    const exported = await jsonResponse<AccountExportOut>(
      exportResponse,
      "/api/auth/account/export",
      200,
    );
    expectPrivateResponse(exportResponse);
    expect(exportResponse.headers()["content-disposition"]).toBe(
      `attachment; filename="goatfarm-account-${owner.userId}.json"`,
    );
    expect(exported).toEqual({
      exported_at: expect.any(String),
      account: {
        id: owner.userId,
        email: owner.email,
        name: owner.name,
        created_at: expect.any(String),
      },
      owned_farms: [
        {
          id: farm.id,
          name: farm.name,
          location: farm.location,
          timezone: farm.timezone,
          created_at: expect.any(String),
        },
      ],
      memberships: [],
    });
    expectIsoTimestamp(exported.exported_at);
    expectIsoTimestamp(exported.account.created_at);
    expectIsoTimestamp(exported.owned_farms[0].created_at);

    const oldLoginPayload: LoginIn = { email: owner.email, password: owner.password };
    expect(oldLoginPayload).toStrictEqual({ email: owner.email, password: owner.password });
    const oldLoginResponse = await request.post("/api/auth/login", { data: oldLoginPayload });
    const oldLogin = await jsonResponse<{ detail: string }>(
      oldLoginResponse,
      "/api/auth/login",
      401,
    );
    expect(oldLogin).toStrictEqual({ detail: "Invalid email or password." });

    const newLoginPayload: LoginIn = { email: owner.email, password: changedPassword };
    expect(newLoginPayload).toStrictEqual({ email: owner.email, password: changedPassword });
    const newLoginResponse = await request.post("/api/auth/login", { data: newLoginPayload });
    const newLogin = await jsonResponse<TokenOut>(newLoginResponse, "/api/auth/login", 200);
    expectTokenOut(newLogin, {
      email: owner.email,
      name: owner.name,
      userId: owner.userId,
    });
  });

  test("deletes a separate registered non-owner and makes its login unusable", async ({
    request,
  }) => {
    test.setTimeout(120_000);
    const user = await registerIdentity(request, "account-delete-non-owner");

    const farmsResponse = await request.get("/api/auth/farms", {
      headers: { Authorization: `Bearer ${user.token}` },
    });
    const farms = await jsonResponse<FarmOut[]>(farmsResponse, "/api/auth/farms", 200);
    expect(farms).toStrictEqual([]);

    const deletePayload: AccountDeleteIn = { current_password: user.password };
    expect(deletePayload).toStrictEqual({ current_password: user.password });
    const deleteResponse = await request.delete("/api/auth/account", {
      data: deletePayload,
      headers: { Authorization: `Bearer ${user.token}` },
    });
    await emptyResponse(deleteResponse, "/api/auth/account", 204);
    expectPrivateResponse(deleteResponse);
    const remainingRefreshCookies = (await request.storageState()).cookies.filter((cookie) =>
      /^(?:__Host-)?goatfarm_refresh$/.test(cookie.name),
    );
    expect(remainingRefreshCookies).toStrictEqual([]);

    const loginPayload: LoginIn = { email: user.email, password: user.password };
    expect(loginPayload).toStrictEqual({ email: user.email, password: user.password });
    const loginResponse = await request.post("/api/auth/login", { data: loginPayload });
    const login = await jsonResponse<{ detail: string }>(loginResponse, "/api/auth/login", 401);
    expect(login).toStrictEqual({ detail: "Invalid email or password." });
  });

  test("persists worker resets and the full custom-role create/update/delete lifecycle", async ({
    request,
  }) => {
    test.setTimeout(180_000);
    const owner = await registerIdentity(request, "team-contract-owner");
    const { farm, headers } = await createFarm(request, owner, "Team Contract Farm");

    const initialTeamResponse = await request.get("/api/team", { headers });
    const initialTeam = await jsonResponse<TeamOut>(initialTeamResponse, "/api/team", 200);
    expect(initialTeam).toEqual({
      memberships: [],
      roles: expect.any(Array),
      permission_groups: expect.any(Array),
      permission_labels: expect.any(Object),
    });
    const cleaner = initialTeam.roles.find((role) => role.code === "CLEANER");
    expect(cleaner).toBeDefined();
    if (!cleaner) throw new Error("New farm did not seed the CLEANER role.");
    expectRoleOut(cleaner, {
      code: "CLEANER",
      name: "Cleaner",
      description: "Performs cleaning duties and marks them done.",
      permissions: ["dashboard.view", "tasks.view", "tasks.complete"],
      revision: 1,
      member_count: 0,
    });

    const workerSuffix = uniqueSuffix();
    const workerPassword = `Worker-${workerSuffix}-pass`;
    const workerEmail = `reset-worker-${workerSuffix}@goatfarm.test`;
    const workerName = `Reset Worker ${workerSuffix}`;
    const workerPayload: WorkerCreateIn = {
      email: workerEmail,
      password: workerPassword,
      name: workerName,
      role_id: cleaner.id,
    };
    expect(workerPayload).toStrictEqual({
      email: workerEmail,
      password: workerPassword,
      name: workerName,
      role_id: cleaner.id,
    });
    const workerResponse = await request.post("/api/team/workers", {
      data: workerPayload,
      headers: { ...headers, "Idempotency-Key": `worker-${workerSuffix}` },
    });
    const worker = await jsonResponse<MembershipOut>(
      workerResponse,
      "/api/team/workers",
      201,
    );
    expectMembershipOut(worker, {
      email: workerEmail,
      name: workerName,
      roleId: cleaner.id,
      roleName: cleaner.name,
    });

    const workerReadbackResponse = await request.get("/api/team", { headers });
    const workerReadback = await jsonResponse<TeamOut>(
      workerReadbackResponse,
      "/api/team",
      200,
    );
    expect(workerReadback.memberships.find((item) => item.id === worker.id)).toStrictEqual(worker);
    expect(workerReadback.roles.find((role) => role.id === cleaner.id)?.member_count).toBe(1);

    const originalWorkerLoginPayload: LoginIn = {
      email: workerEmail,
      password: workerPassword,
    };
    expect(originalWorkerLoginPayload).toStrictEqual({
      email: workerEmail,
      password: workerPassword,
    });
    const originalWorkerLoginResponse = await request.post("/api/auth/login", {
      data: originalWorkerLoginPayload,
    });
    const originalWorkerLogin = await jsonResponse<TokenOut>(
      originalWorkerLoginResponse,
      "/api/auth/login",
      200,
    );
    expectTokenOut(originalWorkerLogin, {
      email: workerEmail,
      name: workerName,
      userId: worker.user_id,
    });

    const resetPassword = `Reset-${uniqueSuffix()}-pass`;
    const resetPayload: PasswordResetIn = { password: resetPassword };
    expect(resetPayload).toStrictEqual({ password: resetPassword });
    const resetPath = `/api/team/workers/${worker.id}/reset-password`;
    const resetResponse = await request.post(resetPath, { data: resetPayload, headers });
    const reset = await jsonResponse<MembershipOut>(resetResponse, resetPath, 200);
    expectMembershipOut(reset, {
      email: workerEmail,
      name: workerName,
      roleId: cleaner.id,
      roleName: cleaner.name,
      membershipId: worker.id,
      userId: worker.user_id,
    });

    const oldWorkerLoginResponse = await request.post("/api/auth/login", {
      data: originalWorkerLoginPayload,
    });
    const oldWorkerLogin = await jsonResponse<{ detail: string }>(
      oldWorkerLoginResponse,
      "/api/auth/login",
      401,
    );
    expect(oldWorkerLogin).toStrictEqual({ detail: "Invalid email or password." });

    const resetLoginPayload: LoginIn = { email: workerEmail, password: resetPassword };
    expect(resetLoginPayload).toStrictEqual({ email: workerEmail, password: resetPassword });
    const resetLoginResponse = await request.post("/api/auth/login", {
      data: resetLoginPayload,
    });
    const resetLogin = await jsonResponse<TokenOut>(resetLoginResponse, "/api/auth/login", 200);
    expectTokenOut(resetLogin, {
      email: workerEmail,
      name: workerName,
      userId: worker.user_id,
    });
    const workerMeResponse = await request.get("/api/auth/me", {
      headers: { Authorization: `Bearer ${resetLogin.access_token}` },
    });
    const workerMe = await jsonResponse<UserOut>(workerMeResponse, "/api/auth/me", 200);
    expect(workerMe).toStrictEqual({
      id: worker.user_id,
      email: workerEmail,
      name: workerName,
      must_change_password: expect.any(Boolean),
    });

    const roleSuffix = uniqueSuffix();
    const createRolePayload: RoleIn = {
      name: `API Helper ${roleSuffix}`,
      description: "Created through the frontend proxy",
      permissions: ["dashboard.view", "tasks.view"],
    };
    expect(createRolePayload).toStrictEqual({
      name: `API Helper ${roleSuffix}`,
      description: "Created through the frontend proxy",
      permissions: ["dashboard.view", "tasks.view"],
    });
    const createRoleResponse = await request.post("/api/team/roles", {
      data: createRolePayload,
      headers,
    });
    const createdRole = await jsonResponse<RoleOut>(
      createRoleResponse,
      "/api/team/roles",
      201,
    );
    expectRoleOut(createdRole, {
      code: null,
      name: createRolePayload.name,
      description: createRolePayload.description ?? null,
      permissions: createRolePayload.permissions ?? [],
      revision: 1,
      member_count: 0,
    });

    const createdReadbackResponse = await request.get("/api/team", { headers });
    const createdReadback = await jsonResponse<TeamOut>(
      createdReadbackResponse,
      "/api/team",
      200,
    );
    expect(createdReadback.roles.find((role) => role.id === createdRole.id)).toStrictEqual(
      createdRole,
    );

    const updateRolePayload: RoleUpdateIn = {
      name: `Senior API Helper ${roleSuffix}`,
      description: "Updated and persisted through the frontend proxy",
      permissions: ["dashboard.view", "tasks.view", "tasks.complete"],
      expected_revision: createdRole.revision,
    };
    expect(updateRolePayload).toStrictEqual({
      name: `Senior API Helper ${roleSuffix}`,
      description: "Updated and persisted through the frontend proxy",
      permissions: ["dashboard.view", "tasks.view", "tasks.complete"],
      expected_revision: 1,
    });
    const rolePath = `/api/team/roles/${createdRole.id}`;
    const updateRoleResponse = await request.put(rolePath, {
      data: updateRolePayload,
      headers,
    });
    const updatedRole = await jsonResponse<RoleOut>(updateRoleResponse, rolePath, 200);
    expectRoleOut(updatedRole, {
      id: createdRole.id,
      code: null,
      name: updateRolePayload.name,
      description: updateRolePayload.description ?? null,
      permissions: updateRolePayload.permissions ?? [],
      revision: createdRole.revision + 1,
      member_count: 0,
    });

    const updatedReadbackResponse = await request.get("/api/team", { headers });
    const updatedReadback = await jsonResponse<TeamOut>(
      updatedReadbackResponse,
      "/api/team",
      200,
    );
    expect(updatedReadback.roles.find((role) => role.id === createdRole.id)).toStrictEqual(
      updatedRole,
    );

    const deleteRoleResponse = await request.delete(rolePath, { headers });
    await emptyResponse(deleteRoleResponse, rolePath, 204);
    const deletedReadbackResponse = await request.get("/api/team", { headers });
    const deletedReadback = await jsonResponse<TeamOut>(
      deletedReadbackResponse,
      "/api/team",
      200,
    );
    expect(deletedReadback.roles.some((role) => role.id === createdRole.id)).toBe(false);
    expect(deletedReadback.memberships.find((item) => item.id === worker.id)).toStrictEqual(reset);
    expect(farm.name).toContain("Team Contract Farm");
  });
});
