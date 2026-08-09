import { safeAppPath } from "@/lib/utils";

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

const APP_ROUTE_PERMISSIONS = [
  { path: "/dashboard", permission: "dashboard.view" },
  { path: "/animals", permission: "animals.view" },
  { path: "/buckets", permission: "buckets.view" },
  { path: "/breeding", permission: "breeding.view" },
  { path: "/kidding", permission: "kidding.view" },
  { path: "/health", permission: "health.view" },
  { path: "/feeding", permission: "feeding.view" },
  { path: "/purchases", permission: "purchases.view" },
  { path: "/tasks", permission: "tasks.view" },
  { path: "/finance", permission: "finance.view" },
  { path: "/simulation", permission: "simulation.view" },
  { path: "/reports", permission: "reports.view" },
  { path: "/team", permission: "team.manage" },
] as const;

const MANAGE_ROUTE_PERMISSIONS = [
  { path: "/animals/new", permission: "animals.create" },
  { path: "/health/new", permission: "health.manage" },
  { path: "/kidding/new", permission: "kidding.manage" },
] as const;

/** Sub-routes that carry no tenant-scoped id, so they mean the same thing in
 * any farm. Every other sub-route embeds a record id (`/animals/7`,
 * `/breeding/3/ultrasound`, `/health/schedule/12`). */
const FARM_AGNOSTIC_SUBROUTES: readonly string[] = [
  "/animals/new",
  "/feeding/inventory",
  "/feeding/recipes",
  "/health/new",
  "/kidding/new",
];

interface ResolvedAppPath {
  /** The requested path, without query or hash. */
  path: string;
  /** The module root that granted access to it. */
  root: string;
  /** The requested query and hash, ready to re-append. */
  state: string;
}

/** Shared validation for an app-local return destination.
 *
 * A return destination is accepted from the URL, so this intentionally fails
 * closed for unknown roots, encoded traversal/separators, backslashes,
 * protocol-relative URLs, and module routes the caller cannot view.
 */
function resolveAppPath(
  raw: string | null | undefined,
  can: PermissionCheck,
): ResolvedAppPath | null {
  const safe = safeAppPath(raw);
  if (!safe) return null;
  const rawPath = safe.split(/[?#]/, 1)[0];
  if (rawPath.includes("%") || rawPath.includes("\\")) return null;

  const url = new URL(safe, "https://goatfarm.invalid");
  const path = url.pathname;
  // URL parsing normalizes literal dot segments (for example
  // `/tasks/../finance`). A return destination is user-controlled URL state,
  // so accept only its already-canonical spelling instead of silently
  // changing which module was requested.
  if (path !== rawPath) return null;
  const special = MANAGE_ROUTE_PERMISSIONS.find(({ path: root }) => path === root);
  if (special && !can(special.permission)) return null;

  const route = APP_ROUTE_PERMISSIONS.find(
    ({ path: root }) => path === root || path.startsWith(`${root}/`),
  );
  if (!route || !can(route.permission)) return null;
  return { path, root: route.path, state: `${url.search}${url.hash}` };
}

/** Validate a same-farm return destination and its module permission. Record
 * ids stay intact: the caller never left the farm that issued them. */
export function permittedAppPath(
  raw: string | null | undefined,
  can: PermissionCheck,
): string | null {
  const resolved = resolveAppPath(raw, can);
  if (!resolved) return null;
  return `${resolved.path}${resolved.state}`;
}

/** Validate a farm-switch destination. The new farm's permissions arrive as a
 * plain list from /api/auth/permissions, and this is the only caller shape
 * that crosses farms.
 *
 * Holding `animals.view` in the selected farm says nothing about animal 7,
 * which belongs to the farm being left — every API read is farm-scoped, so
 * returning to `/animals/7` guarantees a permanent "Animal not found". Module
 * roots and id-free sub-routes survive intact; anything deeper falls back to
 * its module root, dropping page state that described the previous farm.
 */
export function permittedAppPathFromList(
  raw: string | null | undefined,
  permissions: readonly string[],
): string | null {
  const held = new Set(permissions);
  const resolved = resolveAppPath(raw, (permission) => held.has(permission));
  if (!resolved) return null;
  if (resolved.path === resolved.root || FARM_AGNOSTIC_SUBROUTES.includes(resolved.path)) {
    return `${resolved.path}${resolved.state}`;
  }
  return resolved.root;
}

/** Add/replace a returnTo parameter on a trusted app path. */
export function withReturnTo(path: string, returnTo: string): string {
  const url = new URL(path, "https://goatfarm.invalid");
  url.searchParams.set("returnTo", returnTo);
  return `${url.pathname}${url.search}${url.hash}`;
}
