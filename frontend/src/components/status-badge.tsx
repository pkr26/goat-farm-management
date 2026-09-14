import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * The single status-chip system for the app. Every enum that reads as a
 * state (animals, kids, tasks, schedule, breeding outcomes, ledger
 * kinds…) maps here — never re-implement a tinted chip locally.
 *
 * Tints come from the semantic tokens (success / warning / info /
 * destructive) so light and dark themes stay correct by construction.
 * Labels are Title Case; unknown values fall back to a neutral badge.
 */

export type StatusTone = "success" | "warning" | "info" | "destructive";

const STATUS_TONES: Record<string, StatusTone> = {
  // lifecycle — healthy / done
  ACTIVE: "success",
  ALIVE: "success",
  BORN: "success",
  COMPLETED: "success",
  DONE: "success",
  VERIFIED: "success",
  NORMAL: "success",
  CONFIRMED_PREGNANT: "success",
  PREGNANT: "success",
  INCOME: "success",
  FEASIBLE: "success",
  // transitions / informational
  SOLD: "info",
  PURCHASED: "info",
  SKIPPED: "info",
  // needs attention
  QUARANTINE: "warning",
  PENDING: "warning",
  AWAITING_VERIFICATION: "warning",
  ASSISTED: "warning",
  UPCOMING: "warning",
  DUE_SOON: "warning",
  SCHEDULED: "warning",
  EXPENSE: "warning",
  // adverse
  CULLED: "destructive",
  STILLBORN: "destructive",
  REJECTED: "destructive",
  DIFFICULT: "destructive",
  OVERDUE: "destructive",
  FAILED: "destructive",
  DEAD: "destructive",
  // Kid deaths and aborted pregnancies are adverse events like their
  // siblings (STILLBORN/FAILED), not neutral transitions.
  DIED: "destructive",
  ABORTED: "destructive",
  // A caesarean delivery is as adverse as a difficult one.
  CAESAREAN: "destructive",
};
/** Resolve any status string to its semantic tone (neutral → null). */
export function statusTone(status: string): StatusTone | null {
  return STATUS_TONES[normalize(status)] ?? null;
}

function normalize(status: string) {
  return status.trim().toUpperCase().replace(/[\s-]+/g, "_");
}

function humanize(status: string) {
  return normalize(status)
    .toLowerCase()
    .replace(/(^|_)(\w)/g, (_, sep: string, c: string) =>
      (sep === "_" ? " " : "") + c.toUpperCase(),
    );
}

export function StatusBadge({
  status,
  className,
  children,
  ...props
}: {
  status: string;
  children?: React.ReactNode;
} & Omit<React.ComponentProps<typeof Badge>, "children" | "variant">) {
  const tone = statusTone(status);
  return (
    <Badge
      variant={tone ?? "secondary"}
      className={cn(className)}
      {...props}
    >
      <span
        aria-hidden="true"
        className="size-1.5 shrink-0 rounded-full bg-current opacity-70"
      />
      {children ?? humanize(status)}
    </Badge>
  );
}
