"use client";

import { Button } from "@/components/ui/button";

/** Offset pagination shared by operational histories. The API owns the total;
 * this control never guesses from a short/empty page. */
/** Truncates a hostile offset (NaN/negative/fractional) to a whole,
 * non-negative page start. Exported for direct sanitisation testing. */
export function sanitizeOffset(offset: number): number {
  return Number.isFinite(offset) ? Math.max(0, Math.trunc(offset)) : 0;
}

export function PaginationControls({
  total,
  limit,
  offset,
  onOffsetChange,
  label = "records",
  disabled = false,
}: {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
  label?: string;
  /** Keep placeholder rows from dispatching another page transition while
   * the page they describe is no longer the one being requested. */
  disabled?: boolean;
}) {
  if (!Number.isFinite(total) || total <= 0) return null;
  // The component is also a trust boundary: URL-derived state has reached it
  // as NaN/negative/fractional values in the wild. Sanitize instead of
  // rendering "Showing NaN–NaN" (L8).
  // Stryker disable next-line ConditionalExpression, LogicalOperator, EqualityOperator: Infinity (the only differing input) clamps to the same rendered range via Math.min below, and the 0 case now normalizes through Math.max
  const safeLimit =
    Number.isFinite(limit) && limit > 0 ? Math.max(1, Math.trunc(limit)) : 1;
  const safeOffset = sanitizeOffset(offset);
  const last = Math.min(safeOffset + safeLimit, total);
  // A parent can hold an offset past the end of the list — the data shrank
  // under it (deletion/filter) or a stale offset was carried over. Clamp the
  // range start to the end so the label can never invert into "Showing 91–5
  // of 5"; Previous stays enabled, so the user can page back to real rows.
  const first = Math.min(safeOffset + 1, last);
  return (
    <nav
      aria-label={`${label} pagination`}
      aria-busy={disabled || undefined}
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
          disabled={disabled || safeOffset === 0}
          onClick={() => onOffsetChange(Math.max(0, safeOffset - safeLimit))}
        >
          Previous
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || safeOffset + safeLimit >= total}
          onClick={() => onOffsetChange(safeOffset + safeLimit)}
        >
          Next
        </Button>
      </div>
    </nav>
  );
}
