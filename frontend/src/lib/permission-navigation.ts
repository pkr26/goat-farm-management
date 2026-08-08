export type PermissionCheck = (permission: string) => boolean;

/** Stable first-page order shared by login, farm switching, the root hub and
 * permission-aware fallback links. A valid custom role need not include the
 * dashboard, so `/dashboard` must never be assumed as a universal home. */
export const PERMISSION_LANDING_ROUTES = [
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
] as const;

export function firstPermittedPath(can: PermissionCheck): string {
  return (
    PERMISSION_LANDING_ROUTES.find(({ permission }) => can(permission))?.href ?? "/no-access"
  );
}

export function firstPermittedPathFromList(permissions: readonly string[]): string {
  const held = new Set(permissions);
  return firstPermittedPath((permission) => held.has(permission));
}
