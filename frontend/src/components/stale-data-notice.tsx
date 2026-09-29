"use client";

/**
 * Banner for a failed background refetch with a previous payload still
 * cached. TanStack Query retains `data` across a refetch error, so a page
 * whose error branch only runs while loading would silently render stale
 * herd/finance numbers. Render this above the (possibly stale) content
 * whenever the owning query `isError` and a last-loaded payload exists.
 */

import { TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

export function StaleDataNotice({ message, onRetry }: { message?: string; onRetry: () => void }) {
  const t = useT();
  return (
    <div
      role="status"
      className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-warning/50 bg-warning-tint p-3 text-sm text-warning-tint-foreground dark:border-warning/40"
    >
      <TriangleAlert className="size-4 shrink-0" aria-hidden="true" />
      <span>{message ?? t("staleNotice.message")}</span>
      <Button type="button" size="sm" variant="outline" onClick={onRetry}>
        {t("common.retry")}
      </Button>
    </div>
  );
}
