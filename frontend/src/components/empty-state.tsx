import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function EmptyState({
  icon: Icon,
  title,
  description,
  children,
  className,
}: {
  icon: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  /** Action area — pass the primary CTA (e.g. "Add your first animal"). */
  children?: ReactNode;
  className?: string;
}) {
  const hasDescription = Boolean(description) || description === 0;
  const hasChildren = Boolean(children) || children === 0;
  return (
    <div
      className={cn(
        "relative flex flex-col items-center justify-center gap-2 overflow-hidden rounded-xl border border-dashed px-6 py-12 text-center",
        className,
      )}
    >
      {/* Soft radial glow keeps the state feeling inviting rather than
       * broken — the color derives from the brand token, not a raw class. */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute -top-10 left-1/2 size-40 -translate-x-1/2 rounded-full bg-primary/5 blur-2xl"
      />
      <span className="relative flex size-12 items-center justify-center rounded-2xl bg-accent text-accent-foreground ring-1 ring-border [&_svg]:size-6">
        <Icon aria-hidden="true" />
      </span>
      {/* A visual paragraph, not a section heading: EmptyState often renders
       * directly under a page's h1 with no h2 in between, and an h3 there
       * skips a level for screen-reader users. */}
      <p className="relative font-heading text-base font-medium">{title}</p>
      {hasDescription && (
        <p className="relative max-w-sm text-sm text-muted-foreground">
          {description}
        </p>
      )}
      {hasChildren && (
        <div className="relative mt-2 flex flex-wrap items-center justify-center gap-2">
          {children}
        </div>
      )}
    </div>
  );
}
