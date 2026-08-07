/**
 * Shared MSW server for the vitest suite. Started/stopped globally in
 * vitest.setup.ts; tests add or override handlers via `server.use(...)`
 * (runtime handlers take precedence over the session defaults below).
 *
 * Default handlers establish an authenticated session through the real
 * AuthProvider bootstrap path: POST /api/auth/refresh succeeds, the farm
 * list yields one farm (selected automatically), and the permissions
 * endpoint grants the full catalog (mirrors the farm owner). Test files
 * override the permissions handler when they need a reduced set.
 */

import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

export const TEST_ACCESS_TOKEN = "test-access-token";

export const TEST_USER = {
  id: 1,
  email: "owner@goatfarm.test",
  name: "Test Owner",
};

export const TEST_FARMS = [
  { id: 1, name: "Test Goat Farm", location: "Solapur", role: null },
];

/** Mirror of backend app/permissions.py ALL_PERMISSIONS (owner holds all). */
export const ALL_PERMISSIONS = [
  "dashboard.view",
  "animals.view",
  "animals.create",
  "animals.move",
  "animals.weight",
  "animals.status",
  "buckets.view",
  "breeding.view",
  "breeding.manage",
  "kidding.view",
  "kidding.manage",
  "health.view",
  "health.manage",
  "purchases.view",
  "purchases.manage",
  "feeding.view",
  "feeding.manage",
  "tasks.view",
  "tasks.create",
  "tasks.complete",
  "tasks.verify",
  "finance.view",
  "finance.manage",
  "simulation.view",
  "simulation.manage",
  "reports.view",
  "team.manage",
];

/** Permissions endpoint override for tests that need a reduced/granular set. */
export function permissionsHandler(permissions: string[]) {
  return http.get("/api/auth/permissions", () =>
    HttpResponse.json({ is_owner: false, permissions }),
  );
}

export const server = setupServer(
  http.post("/api/auth/refresh", () =>
    HttpResponse.json({ access_token: TEST_ACCESS_TOKEN, user: TEST_USER }),
  ),
  http.get("/api/auth/farms", () => HttpResponse.json(TEST_FARMS)),
  permissionsHandler(ALL_PERMISSIONS),
);
