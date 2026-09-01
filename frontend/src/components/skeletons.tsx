import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Loading placeholders that mirror real page structure, so navigation
 * feels continuous instead of collapsing to a bare "Loading…" line.
 * The page header, actions and layout stay visible wherever possible —
 * these fill only the data region. Containers carry aria-busy (and the
 * bars themselves are decorative); pages that want an explicit spoken
 * announcement wrap the region in their own role="status" live region.
 */

/** Wrapper that marks a skeleton region busy for assistive technology. */
function LoadingRegion({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div aria-busy="true" className={className}>
      {children}
    </div>
  );
}

export function CardSkeleton({
  lines = 3,
  className,
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <LoadingRegion
      className={cn(
        "space-y-3 rounded-xl bg-card p-5 shadow-xs ring-1 ring-foreground/[0.07]",
        className,
      )}
    >
      <Skeleton className="h-4 w-1/3" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className="h-3.5" style={{ width: `${88 - i * 14}%` }} />
      ))}
    </LoadingRegion>
  );
}

export function StatSkeleton({ className }: { className?: string }) {
  return (
    <LoadingRegion
      className={cn(
        "flex items-start gap-3.5 rounded-xl bg-card p-5 shadow-xs ring-1 ring-foreground/[0.07]",
        className,
      )}
    >
      <Skeleton className="size-10 rounded-xl" />
      <div className="flex-1 space-y-2.5">
        <Skeleton className="h-3.5 w-2/3" />
        <Skeleton className="h-7 w-1/3" />
      </div>
    </LoadingRegion>
  );
}

export function TableSkeleton({
  rows = 6,
  columns = 5,
  className,
}: {
  rows?: number;
  columns?: number;
  className?: string;
}) {
  return (
    <LoadingRegion
      className={cn(
        "space-y-3 rounded-xl bg-card p-5 shadow-xs ring-1 ring-foreground/[0.07]",
        className,
      )}
    >
      <Skeleton className="mb-4 h-4 w-1/4" />
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-4">
          {Array.from({ length: columns }).map((_, c) => (
            <Skeleton
              key={c}
              className="h-3.5"
              style={{ width: c === 0 ? "22%" : `${Math.max(8, 18 - c * 2)}%` }}
            />
          ))}
        </div>
      ))}
    </LoadingRegion>
  );
}

/** Full data-region placeholder for a standard list page. */
export function PageSkeleton({
  stats = 0,
  cards = 2,
  children,
}: {
  /** Number of stat-card placeholders above the content. */
  stats?: number;
  /** Number of content-card placeholders. */
  cards?: number;
  children?: ReactNode;
}) {
  return (
    <div className="space-y-6">
      {stats > 0 && (
        <div
          className={cn(
            "grid gap-3",
            stats >= 4 ? "grid-cols-2 lg:grid-cols-4" : "grid-cols-1 sm:grid-cols-2",
            stats === 3 && "lg:grid-cols-3",
          )}
        >
          {Array.from({ length: stats }).map((_, i) => (
            <StatSkeleton key={i} />
          ))}
        </div>
      )}
      {Array.from({ length: cards }).map((_, i) => (
        <CardSkeleton key={i} lines={i === 0 ? 2 : 5} />
      ))}
      {children}
    </div>
  );
}

/** The one inline treatment for sub-regions a skeleton can't mirror
 * (lines inside a dialog, option lists, single fields). Polite by default. */
export function InlineLoading({
  children = "Loading…",
  className,
}: {
  children?: ReactNode;
  className?: string;
}) {
  return (
    <p
      role="status"
      aria-live="polite"
      className={cn("flex items-center gap-2 text-sm text-muted-foreground", className)}
    >
      <span
        aria-hidden="true"
        className="size-3.5 shrink-0 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground"
      />
      {children}
    </p>
  );
}
