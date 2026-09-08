import { describe, expect, it } from "vitest";

import { permittedAppPath, permittedAppPathFromList } from "@/lib/permission-navigation";

/** The complete module-route → permission contract. Every app route must be
 * here so a renamed module or permission fails loudly instead of locking
 * users out of (or into) a module. */
const ROUTE_PERMISSIONS: ReadonlyArray<readonly [string, string]> = [
  ["/dashboard", "dashboard.view"],
  ["/animals", "animals.view"],
  ["/buckets", "buckets.view"],
  ["/breeding", "breeding.view"],
  ["/kidding", "kidding.view"],
  ["/health", "health.view"],
  ["/feeding", "feeding.view"],
  ["/purchases", "purchases.view"],
  ["/tasks", "tasks.view"],
  ["/finance", "finance.view"],
  ["/planner", "simulation.view"],
  ["/simulation", "simulation.view"],
  ["/reports", "reports.view"],
  ["/team", "team.manage"],
];

const canOnly = (granted: string) => (permission: string) => permission === granted;
const canNothing = () => false;

describe("permittedAppPath — route → permission mapping", () => {
  it("accepts every module route exactly when its permission is held", () => {
    for (const [route, permission] of ROUTE_PERMISSIONS) {
      expect(permittedAppPath(route, canOnly(permission)), route).toBe(route);
      expect(permittedAppPath(route, canNothing), route).toBeNull();
      // A different module's permission must not open this route.
      expect(permittedAppPath(route, canOnly("some.other.permission")), route).toBeNull();
    }
  });

  it("keeps query strings and hashes on permitted routes", () => {
    for (const [route, permission] of ROUTE_PERMISSIONS) {
      expect(
        permittedAppPath(`${route}?tab=active&page=2#row-7`, canOnly(permission)),
        route,
      ).toBe(`${route}?tab=active&page=2#row-7`);
    }
  });

  it("covers sub-routes with the module-root permission", () => {
    expect(permittedAppPath("/planner/edit/3", canOnly("simulation.view"))).toBe(
      "/planner/edit/3",
    );
    expect(permittedAppPath("/team/members", canOnly("team.manage"))).toBe("/team/members");
  });

  it("lets simulation.view open both planner and simulation", () => {
    const canSim = canOnly("simulation.view");
    expect(permittedAppPath("/planner", canSim)).toBe("/planner");
    expect(permittedAppPath("/simulation", canSim)).toBe("/simulation");
  });

  it("rejects unknown and near-miss routes regardless of permissions", () => {
    const canAll = (permission: string) => permission.endsWith(".view") || permission === "team.manage";
    for (const path of [
      "/unknown",
      "/unknown/deeper/still",
      "/dashboards",
      "/dashboarding",
      "/team-invites",
      "/task",
      "/plannerX",
    ]) {
      expect(permittedAppPath(path, canAll), path).toBeNull();
    }
  });

  it("rejects empty and missing destinations", () => {
    expect(permittedAppPath("", () => true)).toBeNull();
    expect(permittedAppPath(null, () => true)).toBeNull();
    expect(permittedAppPath(undefined, () => true)).toBeNull();
  });
});

describe("permittedAppPathFromList — route → permission mapping", () => {
  it("accepts every module route exactly when its permission is in the list", () => {
    for (const [route, permission] of ROUTE_PERMISSIONS) {
      expect(permittedAppPathFromList(route, [permission]), route).toBe(route);
      expect(permittedAppPathFromList(route, []), route).toBeNull();
      expect(permittedAppPathFromList(route, ["some.other.permission"]), route).toBeNull();
    }
  });

  it("keeps query strings and hashes on permitted module roots", () => {
    for (const [route, permission] of ROUTE_PERMISSIONS) {
      expect(
        permittedAppPathFromList(`${route}?tab=active#row-7`, [permission]),
        route,
      ).toBe(`${route}?tab=active#row-7`);
    }
  });

  it("drops record-id sub-routes to their module root", () => {
    expect(permittedAppPathFromList("/team/members/3", ["team.manage"])).toBe("/team");
    expect(
      permittedAppPathFromList("/dashboard?q=1", ["dashboard.view"]),
    ).toBe("/dashboard?q=1");
  });

  it("rejects unknown routes even with every permission held", () => {
    const all = ROUTE_PERMISSIONS.map(([, permission]) => permission);
    expect(permittedAppPathFromList("/unknown", all)).toBeNull();
    expect(permittedAppPathFromList("/dashboards", all)).toBeNull();
  });
});
