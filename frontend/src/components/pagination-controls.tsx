"use client";

import { Button } from "@/components/ui/button";

/** Offset pagination shared by operational histories. The API owns the total;
 * this control never guesses from a short/empty page. */
export function PaginationControls({
  total,
  limit,
  offset,
  onOffsetChange,
  label = "records",
}: {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
  label?: string;
}) {
  if (total <= 0) return null;
  const first = offset + 1;
  const last = Math.min(offset + limit, total);
  return (
    <nav
      aria-label={`${label} pagination`}
      className="flex flex-wrap items-center justify-between gap-3 pt-3"
    >
      <p className="text-sm text-muted-foreground" aria-live="polite">
        Showing {first}–{last} of {total} {label}
      </p>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
        >
          Previous
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={offset + limit >= total}
          onClick={() => onOffsetChange(offset + limit)}
        >
          Next
        </Button>
      </div>
    </nav>
  );
}
