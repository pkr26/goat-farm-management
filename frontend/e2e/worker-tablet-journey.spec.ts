import { randomUUID } from "node:crypto";

import { expect, test, type APIRequestContext } from "@playwright/test";

import { E2E_EMAIL, E2E_PASSWORD } from "./helpers";

/**
 * Worker tablet journey on a Pixel 7 (ITEM 2 Phase 2, 2026-09-21 playbook):
 * provision a PIN worker over the API → pin the tablet → tap-name + PIN
 * sign-in → own duties only → complete a duty OFFLINE → reconnect → the
 * queued write lands exactly once and is ATTRIBUTED to the worker.
 *
 * Runs in the Mobile Chrome project (device emulation) — the desktop project
 * testIgnores this file.
 */

const PIN = "432198";

let cachedFarmId: number | null = null;

type ApiInit = {
  method?: string;
  data?: unknown;
  headers?: Record<string, string>;
};

async function ownerApi(request: APIRequestContext, path: string, init: ApiInit = {}) {
  const login = await request.post("http://localhost:8000/api/auth/login", {
    data: { email: E2E_EMAIL, password: E2E_PASSWORD },
  });
  expect(login.ok()).toBeTruthy();
  const { access_token: token } = (await login.json()) as { access_token: string };
  if (cachedFarmId === null) {
    const farms = await request.get("http://localhost:8000/api/auth/farms", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(farms.ok()).toBeTruthy();
    const list = (await farms.json()) as { id: number }[];
    expect(list.length).toBeGreaterThan(0);
    cachedFarmId = list[0].id;
  }
  const response = await request.fetch(`http://localhost:8000${path}`, {
    ...init,
    headers: {
      ...(init.headers as Record<string, string> | undefined),
      Authorization: `Bearer ${token}`,
      "X-Farm-Id": String(cachedFarmId),
      ...(init.method ? { "Idempotency-Key": randomUUID() } : {}),
    },
  });
  return response;
}

test.describe("worker tablet", () => {
  test("PIN login, offline completion, sync with attribution", async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    const suffix = Date.now().toString(36);
    // The farm id resolves on first ownerApi call; the tablet pin uses it.
    const farmProbe = await ownerApi(request, "/api/auth/farms");
    const farmId = ((await farmProbe.json()) as unknown as { id: number }[])[0].id;

    // --- Provision over the API: role, PIN worker, personal duty. ---------
    const role = await ownerApi(request, "/api/team/roles", {
      method: "POST",
      data: { name: `Tablet ${suffix}`, permissions: ["dashboard.view", "tasks.view", "tasks.complete"] },
    });
    expect(role.status(), await role.text()).toBe(201);
    const roleId = ((await role.json()) as { id: number }).id;

    const worker = await ownerApi(request, "/api/team/workers", {
      method: "POST",
      data: {
        name: `Tab Worker ${suffix}`,
        email: `tab-${suffix}@goatfarm.test`,
        password: "tablet-pass-1234",
        role_id: roleId,
        pin: PIN,
      },
    });
    expect(worker.status(), await worker.text()).toBe(201);
    const { id: membershipId } = (await worker.json()) as { id: number };

    const roster = await request.get(
      `http://localhost:8000/api/auth/worker-roster?farm_id=${farmId}`,
    );
    const rosterBody = (await roster.json()) as { items: { membership_id: number }[] };
    expect(rosterBody.items.some((item) => item.membership_id === membershipId)).toBe(true);

    // The duty: due today, personally assigned (needs the worker's user id).
    const teamBody = (await (await ownerApi(request, "/api/team")).json()) as {
      memberships: { id: number; user_id: number }[];
    };
    const me = teamBody.memberships.find((row) => row.id === membershipId);
    if (!me) throw new Error("membership not found");
    if (!me) throw new Error("membership not found");
    const today = new Date().toISOString().slice(0, 10);
    const duty = await ownerApi(request, "/api/tasks", {
      method: "POST",
      data: {
        title: `Tablet duty ${suffix}`,
        due_date: today,
        category: "OTHER",
        assigned_user_id: me.user_id,
      },
    });
    expect(duty.status(), await duty.text()).toBe(201);
    const dutyId = ((await duty.json()) as { id: number }).id;

    // --- Pin the tablet and sign in by PIN. -------------------------------
    await page.addInitScript((farm) => {
      window.localStorage.setItem("herdly.tabletFarm", String(farm));
    }, farmId);
    await page.goto("/worker/login");
    await page.getByRole("button", { name: new RegExp(`Tab Worker ${suffix}`) }).click();
    // Language-stable hooks: the surface is Telugu-first, so aria-labels
    // localize but the testids do not.
    for (const digit of PIN) {
      await page.getByTestId(`pin-key-${digit}`).click();
    }
    await page.getByTestId("pin-sign-in").click();
    await expect(page).toHaveURL(/\/worker$/, { timeout: 20_000 });

    // Own duties only: this worker sees exactly their personal duty.
    await expect(page.getByText(`Tablet duty ${suffix}`)).toBeVisible({ timeout: 20_000 });
    const dutyCount = await page.getByTestId(/^worker-duty-\d+$/).count();
    expect(dutyCount).toBeGreaterThanOrEqual(1);

    // --- Complete OFFLINE: the write queues with its idempotency key. -----
    const context = page.context();
    await context.setOffline(true);
    await page.getByTestId(`complete-${dutyId}`).click();
    await expect(page.getByTestId("worker-queue-depth")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("worker-queue-depth")).toContainText(/1/);

    // --- Reconnect: the online event drains the queue. --------------------
    await context.setOffline(false);
    await expect(page.getByTestId("worker-queue-depth")).toHaveCount(0, { timeout: 30_000 });

    // The duty is DONE and attributed to the worker (exactly once).
    await expect
      .poll(
        async () => {
          const state = await ownerApi(request, `/api/tasks/${dutyId}`);
          if (!state.ok()) return null;
          const body = (await state.json()) as {
            status: string;
            completed_by_id: number | null;
          };
          return `${body.status}|${body.completed_by_id}`;
        },
        { timeout: 30_000 },
      )
      .toBe(`DONE|${me.user_id}`);

    // End shift wipes the queue and returns to the tablet login.
    await page.getByTestId("end-shift").click();
    await expect(page).toHaveURL(/\/worker\/login$/, { timeout: 20_000 });
    expect(
      await page.evaluate(
        () => window.localStorage.getItem("goatfarm:offlineQueue:v1") ?? "[]",
      ),
    ).toBe("[]");
  });
});
