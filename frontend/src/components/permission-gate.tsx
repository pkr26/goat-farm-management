"use client";

/**
 * The permission prologue every module page repeats: while the permission
 * set loads render the page-shaped skeleton, on a permissions failure the
 * shared retry affordance, on a denial the muted one-liner, and only then
 * the page itself.
 *
 * The gate is deliberately presentational: the page passes the result of
 * its single usePermissions() call (and hands the same object to its
 * content). A gate-internal usePermissions() would mount a SECOND query
 * observer next to the content's own, and a freshly mounted observer
 * refetches stale data — doubling the /api/auth/permissions round trips on
 * every page navigation.
 *
 * The skeleton state renders the page's real header (title/description) so
 * navigation feels continuous on slow rural connections instead of
 * collapsing to a bare "Loading…" line; `announce` additionally exposes it
 * as a polite live region for the pages that already did.
 */

import { ShieldAlert, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionsError } from "@/components/permissions-error";
import { PageSkeleton } from "@/components/skeletons";
import type { PermissionsState } from "@/lib/use-permissions";

/** Icon for the richer no-access override; the default denial stays a
 * muted paragraph exactly like the inline prologue it replaced. */
const NO_ACCESS_ICON: LucideIcon = ShieldAlert;

export function PermissionGate({
  /** The page's single usePermissions() result — shared with the content. */
  perms,
  /** Permission code that grants access to the page ("animals.view"). */
  perm,
  /** PageHeader title mirrored by the loading skeleton. */
  label,
  /** PageHeader description mirrored by the loading skeleton. */
  description,
  /** Stat-card placeholders above the content cards. */
  stats = 0,
  /** Content-card placeholders below the header. */
  cards = 2,
  /** Wrap the loading state in a polite live region (pages that announced). */
  announce = false,
  /** Extra loading condition OR-ed onto the permission fetch — e.g. the
   * finance page also waits for the auth bootstrap, whose window can show
   * an empty permission set while the farm selection is still pending. */
  alsoLoading = false,
  /** Plain-text override of the default denial line (translated surfaces
   * pass their catalog string); keeps the muted-paragraph treatment. */
  noAccessMessage,
  /** Richer no-access presentation (dashed EmptyState card). Providing
   * either override part switches the denial to that treatment. */
  noAccessTitle,
  noAccessDescription,
  children,
}: {
  perms: PermissionsState;
  perm: string;
  label: string;
  description: string;
  stats?: number;
  cards?: number;
  announce?: boolean;
  alsoLoading?: boolean;
  noAccessMessage?: string;
  noAccessTitle?: string;
  noAccessDescription?: string;
  children: ReactNode;
}) {
  const { can, loading, isError, refetch } = perms;

  if (loading || alsoLoading) {
    const header = <PageHeader title={label} description={description} />;
    const skeleton = <PageSkeleton stats={stats} cards={cards} />;
    if (announce) {
      return (
        <div className="space-y-6" role="status" aria-live="polite">
          <span className="sr-only">Loading…</span>
          {header}
          {skeleton}
        </div>
      );
    }
    return (
      <div className="space-y-6">
        {header}
        {skeleton}
      </div>
    );
  }
  if (isError) {
    return <PermissionsError onRetry={() => void refetch()} />;
  }
  if (!can(perm)) {
    if (noAccessTitle !== undefined || noAccessDescription !== undefined) {
      return (
        <EmptyState
          icon={NO_ACCESS_ICON}
          title={noAccessTitle ?? "You don't have access to this page."}
          description={noAccessDescription}
        />
      );
    }
    return (
      <p className="text-muted-foreground">
        {noAccessMessage ?? "You don't have access to this page."}
      </p>
    );
  }
  return <>{children}</>;
}
