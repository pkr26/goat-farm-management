import type { ReactNode } from "react";

import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Consistent Card wrapper for data tables: optional header (title,
 * description, actions) and a table (or any content) in the body.
 */
export function DataTableCard({
  title,
  description,
  actions,
  children,
  className,
  contentClassName,
  id,
  tabIndex,
  ariaBusy,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
  /** Optional anchor id for in-page navigation. */
  id?: string;
  /** Forwarded to the Card so anchor targets can receive focus. */
  tabIndex?: number;
  /** Marks a background refetch so assistive tech can announce it. */
  ariaBusy?: boolean;
}) {
  const hasTitle = Boolean(title) || title === 0;
  const hasDescription = Boolean(description) || description === 0;
  const hasActions = Boolean(actions) || actions === 0;
  return (
    <Card id={id} tabIndex={tabIndex} aria-busy={ariaBusy || undefined} className={className}>
      {(hasTitle || hasDescription || hasActions) && (
        <CardHeader>
          {hasTitle && <CardTitle>{title}</CardTitle>}
          {hasDescription && <CardDescription>{description}</CardDescription>}
          {hasActions && <CardAction>{actions}</CardAction>}
        </CardHeader>
      )}
      <CardContent className={cn(contentClassName)}>{children}</CardContent>
    </Card>
  );
}
