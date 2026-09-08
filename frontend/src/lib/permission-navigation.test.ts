import { describe, expect, it } from "vitest";

import {
  PERMISSION_LANDING_ROUTES,
  firstPermittedPath,
  firstPermittedPathFromList,
  permittedAppPath,
  permittedAppPathFromList,
  withReturnTo,
} from "@/lib/permission-navigation";

describe("firstPermittedPathFromList", () => {
  it("keeps the complete stable landing-route priority", () => {
    expect(PERMISSION_LANDING_ROUTES).toEqual([
      { permission: "dashboard.view", href: "/dashboard" },
      { permission: "animals.view", href: "/animals" },
      { permission: "buckets.view", href: "/buckets" },
      { permission: "breeding.view", href: "/breeding" },
      { permission: "kidding.view", href: "/kidding" },
      { permission: "health.view", href: "/health" },
      { permission: "feeding.view", href: "/feeding" },
        { permission: "purchases.view", href: "/purchases" },
      { permission: "tasks.view", href: "/tasks" },
      { permission: "finance.view", href: "/finance" },
      { permission: "simulation.view", href: "/simulation" },
      { permission: "reports.view", href: "/reports" },
      { permission: "team.manage", href: "/team" },
    ]);
  });

  it("uses the callback form and short-circuits at the first grant", () => {
    const checked: string[] = [];
    expect(
      firstPermittedPath((permission) => {
        checked.push(permission);
        return permission === "buckets.view";
      }),
    ).toBe("/buckets");
    expect(checked).toEqual(["dashboard.view", "animals.view", "buckets.view"]);
  });

  it("uses the first permitted module instead of assuming dashboard access", () => {
    expect(firstPermittedPathFromList(["health.view", "finance.view"])).toBe("/health");
    expect(firstPermittedPathFromList(["tasks.view"])).toBe("/tasks");
  });

  it("uses the safe no-access page for an empty custom role", () => {
    expect(firstPermittedPathFromList([])).toBe("/no-access");
  });
});

describe("permittedAppPath — same-farm back links", () => {
  const canView = (permission: string) =>
    ["tasks.view", "animals.view", "finance.view"].includes(permission);

  it("preserves a permitted internal detail path with its page state", () => {
    expect(permittedAppPath("/tasks?tab=overdue#task-7", canView)).toBe(
      "/tasks?tab=overdue#task-7",
    );
    // The record id is still valid here — the caller never left its farm.
    expect(permittedAppPath("/animals/7?returnTo=%2Fdashboard", canView)).toBe(
      "/animals/7?returnTo=%2Fdashboard",
    );
  });

  it("fails closed for external, traversal, unknown, and unpermitted destinations", () => {
    for (const path of [
      "https://evil.test/tasks",
      "//evil.test/tasks",
      "/tasks/../finance",
      "/tasks/./overdue",
      "/tasks/%2e%2e/finance",
      "/%74asks",
      "/unknown",
      "/tasks\\evil",
    ]) {
      expect(permittedAppPath(path, canView)).toBeNull();
    }
    expect(permittedAppPath("/health", canView)).toBeNull();
  });

  it("gates a manage route the same way with or without a trailing slash", () => {
    // Holds the view permission but not the create permission a manage
    // route requires; a trailing slash must not weaken the check to view-only.
    const viewOnly = (permission: string) => permission === "animals.view";
    expect(permittedAppPath("/animals/new", viewOnly)).toBeNull();
    expect(permittedAppPath("/animals/new/", viewOnly)).toBeNull();

    const canCreate = (permission: string) =>
      permission === "animals.view" || permission === "animals.create";
    expect(permittedAppPath("/animals/new", canCreate)).toBe("/animals/new");
    expect(permittedAppPath("/animals/new/", canCreate)).toBe("/animals/new/");
    expect(permittedAppPath("/animals/new/import", viewOnly)).toBeNull();
    expect(permittedAppPath("/animals/new/import", canCreate)).toBe(
      "/animals/new/import",
    );
  });
});

describe("permittedAppPathFromList — farm switching", () => {
  it("keeps module roots and id-free sub-routes with their page state", () => {
    expect(permittedAppPathFromList("/tasks?tab=overdue#task-7", ["tasks.view"])).toBe(
      "/tasks?tab=overdue#task-7",
    );
    expect(
      permittedAppPathFromList("/animals?status=ACTIVE&page=2", ["animals.view"]),
    ).toBe("/animals?status=ACTIVE&page=2");
    expect(
      permittedAppPathFromList("/animals/new", ["animals.view", "animals.create"]),
    ).toBe("/animals/new");
    expect(permittedAppPathFromList("/feeding/inventory", ["feeding.view"])).toBe(
      "/feeding/inventory",
    );
    expect(
      permittedAppPathFromList("/feeding/recipes?tab=archived", ["feeding.view"]),
    ).toBe("/feeding/recipes?tab=archived");
    expect(
      permittedAppPathFromList("/health/new?task_id=41", [
        "health.view",
        "health.manage",
      ]),
    ).toBe("/health/new?task_id=41");
    expect(
      permittedAppPathFromList("/kidding/new/?task_id=42", [
        "kidding.view",
        "kidding.manage",
      ]),
    ).toBe("/kidding/new/?task_id=42");
    expect(permittedAppPathFromList("/tasks/?tab=overdue#task-7", ["tasks.view"])).toBe(
      "/tasks/?tab=overdue#task-7",
    );
    expect(
      permittedAppPathFromList("/feeding/inventory/?page=2", ["feeding.view"]),
    ).toBe("/feeding/inventory/?page=2");
    expect(
      permittedAppPathFromList("/animals/new/?source=tasks", [
        "animals.view",
        "animals.create",
      ]),
    ).toBe("/animals/new/?source=tasks");
  });

  it("drops record ids that belong to the farm being left", () => {
    // Every read is farm-scoped server-side, so /animals/7 in the newly
    // selected farm is a guaranteed 404 — return to the module instead.
    expect(
      permittedAppPathFromList("/animals/7?returnTo=%2Fdashboard", ["animals.view"]),
    ).toBe("/animals");
    expect(
      permittedAppPathFromList("/breeding/3/ultrasound", ["breeding.view"]),
    ).toBe("/breeding");
    expect(permittedAppPathFromList("/health/schedule/12", ["health.view"])).toBe("/health");
  });

  it("fails closed for external, traversal, unknown, and unpermitted destinations", () => {
    for (const path of [
      "https://evil.test/tasks",
      "//evil.test/tasks",
      "/tasks/../finance",
      "/tasks/./overdue",
      "/tasks/%2e%2e/finance",
      "/%74asks",
      "/unknown",
      "/tasks\\evil",
    ]) {
      expect(permittedAppPathFromList(path, ["tasks.view", "finance.view"])).toBeNull();
    }
    expect(permittedAppPathFromList("/health", ["tasks.view"])).toBeNull();
    expect(permittedAppPathFromList("/health/new", ["health.view"])).toBeNull();
  });
});

describe("withReturnTo", () => {
  it("adds encoded return state without discarding an existing query", () => {
    expect(withReturnTo("/health/new?task_id=8", "/tasks?tab=overdue")).toBe(
      "/health/new?task_id=8&returnTo=%2Ftasks%3Ftab%3Doverdue",
    );
  });
});
