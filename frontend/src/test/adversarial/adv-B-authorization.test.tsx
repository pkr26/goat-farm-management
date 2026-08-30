/**
 * ADVERSARIAL AUDIT B1/B2/B6 — authorization rendering attacks (executed).
 *
 * B1  Fail-closed: the permissions endpoint fails → nothing may render as if
 *     the user held grants (an empty Set on error, actions inert).
 * B2  Withheld-as-empty: the /api/dashboard contract returns suggestions []
 *     + total 0 for a caller without breeding.view (verified in openapi.json).
 *     The page must NOT render that as the factual "No suggestions.".
 *     → expected to CONFIRM open finding M-1.
 * B6  Route × permission matrix: every landing/permission combination resolves
 *     to a route the caller can actually view, or /no-access — never an
 *     accidental privilege grant through ordering bugs.
 */

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "@/app/(app)/dashboard/page";
import { PERMISSION_LANDING_ROUTES } from "@/lib/permission-navigation";
import { firstPermittedPathFromList } from "@/lib/permission-navigation";
import { ALL_PERMISSIONS, permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const BASE_DASHBOARD = {
  total_active: 12,
  sex_counts: { F: 10, M: 2 },
  status_totals: { SOLD: 3 },
  buckets: [{ name: "Breeding", code: "BREEDING", count: 6 }],
  todays_tasks: [],
  todays_tasks_total: 0,
  overdue_tasks: [],
  overdue_tasks_total: 0,
  ultrasounds_due: [],
  ultrasounds_due_total: 0,
  kiddings_due: [],
  kiddings_due_total: 0,
  cull_candidates: [],
  suggestions: [],
  suggestions_total: 0,
  recent_weights: [],
  recent_weights_limit: 10,
  preview_limit: 5,
};

function dashboardHandler() {
  return http.get("/api/dashboard", () => HttpResponse.json(BASE_DASHBOARD));
}

describe("ADV B2: breeding-withheld suggestions announce the withholding (M-1 fixed)", () => {
  beforeEach(() => {
    server.use(
      dashboardHandler(),
      // animals.view held; breeding.view withheld — exactly the contract's
      // withheld-suggestions caller ([] + total 0, not 403).
      permissionsHandler(ALL_PERMISSIONS.filter((p) => p !== "breeding.view")),
    );
  });

  it("DEFENDED: a breeding-withheld caller sees the withheld notice, not 'No suggestions.'", async () => {
    renderWithProviders(<DashboardPage />);
    // Both breeding-derived sections announce the withholding…
    expect(
      await screen.findByText("Kiddings require breeding access."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Move suggestions require animal and breeding access."),
    ).toBeInTheDocument();
    // …and neither renders a factual empty state for withheld data.
    expect(screen.queryByText("No suggestions.")).toBeNull();
  });
});

describe("ADV B1: permissions endpoint failure fails closed", () => {
  beforeEach(() => {
    server.use(
      dashboardHandler(),
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
  });

  it("renders the permission error, never data or action affordances", async () => {
    renderWithProviders(<DashboardPage />);
    expect(
      await screen.findByText(/Could not load your permissions/i),
    ).toBeInTheDocument();
    // No privileged content may render from a failed permission check.
    await waitFor(() => {
      expect(screen.queryByText("Ready to move")).toBeNull();
      expect(screen.queryByText("Active animals")).toBeNull();
    });
  });
});

describe("ADV B6: route × permission matrix", () => {
  it("an empty permission set never lands on an app route", () => {
    expect(firstPermittedPathFromList([])).toBe("/no-access");
  });

  it("each single permission lands on exactly its own module route", () => {
    for (const { permission, href } of PERMISSION_LANDING_ROUTES) {
      expect(firstPermittedPathFromList([permission])).toBe(href);
    }
  });

  it("every permission subset resolves to a permitted route or /no-access", () => {
    // All 2^13 landing-permission subsets is 8192 runs — cheap for a pure map
    // walk, and exhaustive over ordering bugs.
    const perms = PERMISSION_LANDING_ROUTES.map((r) => r.permission);
    for (let mask = 0; mask < 1 << perms.length; mask += 1) {
      const held = perms.filter((_, bit) => mask & (1 << bit));
      const result = firstPermittedPathFromList(held);
      const firstHeld = PERMISSION_LANDING_ROUTES.find((r) => held.includes(r.permission));
      expect(result).toBe(firstHeld ? firstHeld.href : "/no-access");
    }
  });

  it("unknown/garbage permissions grant nothing", () => {
    expect(firstPermittedPathFromList(["admin", "*", "dashboard.view "])).toBe("/no-access");
    expect(firstPermittedPathFromList(["DASHBOARD.VIEW"])).toBe("/no-access");
  });
});
