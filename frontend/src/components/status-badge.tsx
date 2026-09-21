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
  PROCESSING: "info",
  HEALTHY: "success",
  FLAGGED: "destructive",
  PENDING_REVIEW: "warning",
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
  // Screening run status: an errored cascade is adverse (the photo was not
  // screened and needs attention/retry), not a neutral unknown — a neutral
  // chip hid failed runs next to their flagged siblings (wave-5, 2026-09-20
  // audit).
  ERROR: "destructive",
  // Vet review verdicts (the finding-level counterparts of CONFIRMED_/
  // REJECTED_OUTCOMES): a confirmed disease finding is adverse.
  CONFIRMED: "destructive",
  // Movement-restriction episode actions: a PLACED hold needs attention; a
  // CLEARED hold is a completed protocol step.
  PLACED: "warning",
  CLEARED: "success",
  // Kid deaths and aborted pregnancies are adverse events like their
  // siblings (STILLBORN/FAILED), not neutral transitions.
  DIED: "destructive",
  ABORTED: "destructive",
  // A caesarean delivery is as adverse as a difficult one.
  CAESAREAN: "destructive",
  // Insurance register lifecycle: "renewed" was never a real status value
  // (renewal keeps a policy ACTIVE — removed with cad1e2f3a4b5); a lapsed
  // policy is cover that has ended (attention, not disaster — the dashboard
  // stops nagging it); a claim is the terminal settlement.
  LAPSED: "warning",
  CLAIMED: "info",
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
