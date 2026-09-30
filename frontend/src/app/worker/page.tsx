"use client";

/**
 * Worker tablet duty board (ITEM 2 Phase 2, 2026-09-21 playbook).
 *
 * Today + overdue duties as large cards with ≥44px targets. Completion is
 * offline-aware: the mutation carries a fresh Idempotency-Key, so a network
 * failure enqueues it verbatim and the replay lands exactly once. Form-linked
 * duties deep-link to their form with ?returnTo=/worker.
 */

import { AlertTriangle, Check, ClipboardList, ExternalLink, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { useListTasksApiTasksGet } from "@/api/generated/endpoints";
import type { TaskOut } from "@/api/generated/models";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { apiFetch, ApiError } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { farmToday, formatDate } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
import {
  enqueueOfflineMutation,
  isOfflineQueueableFailure,
} from "@/lib/offline-queue";
import { withReturnTo } from "@/lib/permission-navigation";
import { mapServerError } from "@/lib/server-error-phrases";
import { permittedTaskActionPath, taskSkipUnavailable } from "@/lib/task-action-access";
import { applyOptimisticTaskPatch } from "@/lib/task-optimistic";
import { resolveTaskTitle } from "@/lib/task-title";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useQueryClient } from "@tanstack/react-query";

function DutyCard({
  task,
  canComplete,
  busy,
  onComplete,
  onSkip,
}: {
  task: TaskOut;
  canComplete: boolean;
  busy: boolean;
  onComplete: (task: TaskOut) => void;
  onSkip: (task: TaskOut) => void;
}) {
  const t = useT();
  const { language } = useLanguage();
  const perms = usePermissions();
  const actionPath = permittedTaskActionPath(task.action_url, perms.can);
  // Generated linked duties (purchase pickups, ultrasounds, weaning or moving
  // a specific animal) answer a bare skip with a definitive refusal
  // server-side — the manager board hides Skip for exactly these
  // (taskSkipUnavailable); offering it here only recorded a write the server
  // would refuse (2026-09-29 audit).
  const skippable = !taskSkipUnavailable(task);
  // The farm's calendar day, not UTC: in the IST 00:00–05:30 window the UTC
  // date is still yesterday and every duty due then lost its overdue badge
  // (2026-09-28 audit — the tasks board compares the same way).
  const overdue = task.due_date < farmToday();

  return (
    <li
      className="space-y-3 rounded-xl border bg-card p-4 shadow-xs"
      data-testid={`worker-duty-${task.id}`}
    >
      <div className="flex items-start justify-between gap-2">
        {/* Auto-generated duties carry title_key/title_args — render them in
         * the worker's language, falling back to the payload's English title
         * (2026-09-28 audit, H5). */}
        <p className="text-lg font-medium leading-snug">{resolveTaskTitle(task, language)}</p>
        {overdue ? (
          <StatusBadge status="ERROR">
            <AlertTriangle aria-hidden className="size-4" />
          </StatusBadge>
        ) : null}
      </div>
      <p className="text-sm text-muted-foreground">{formatDate(task.due_date)}</p>
      <div className="flex flex-wrap gap-2 pt-1">
        {canComplete && task.status === "PENDING" ? (
          <>
            <Button
              size="lg"
              className="min-h-11"
              disabled={busy}
              onClick={() => onComplete(task)}
              data-testid={`complete-${task.id}`}
            >
              <Check aria-hidden /> {t("worker.complete")}
            </Button>
            {skippable ? (
              <Button
                size="lg"
                variant="outline"
                className="min-h-11"
                disabled={busy}
                onClick={() => onSkip(task)}
                data-testid={`skip-${task.id}`}
              >
                <X aria-hidden /> {t("worker.skip")}
              </Button>
            ) : null}
          </>
        ) : null}
        {actionPath !== null ? (
          <Link
            href={withReturnTo(actionPath, "/worker")}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium"
            data-testid={`open-form-${task.id}`}
          >
            <ExternalLink aria-hidden className="size-4" /> {t("worker.openForm")}
          </Link>
        ) : null}
      </div>
    </li>
  );
}

function WorkerBoardContent({ perms }: { perms: PermissionsState }) {
  const t = useT();
  const { user, farmId } = useAuth();
  const queryClient = useQueryClient();
  const [busyId, setBusyId] = useState<number | null>(null);
  const allowed = perms.can("tasks.view");

  const query = useListTasksApiTasksGet(
    {
      // The board must never silently drop duties (2026-09-28 audit, W4):
      // fetch up to the server cap and surface the "and N more" note below
      // when the totals say more exist.
      active_limit: 200,
      today_offset: 0,
      overdue_offset: 0,
      upcoming_offset: 0,
      awaiting_offset: 0,
      completed_limit: 10,
      completed_offset: 0,
    },
    { query: { enabled: allowed, refetchOnWindowFocus: true } },
  );
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const canComplete = perms.can("tasks.complete");

  if (!allowed) {
    // The tablet HAS a farm — this account simply lacks tasks.view. The old
    // "No farm on this tablet" copy sent workers re-pinning a healthy tablet
    // instead of asking for access (2026-09-28 audit).
    return (
      <EmptyState
        icon={ClipboardList}
        title={t("worker.noPermission.title")}
        description={t("worker.noPermission.description")}
      />
    );
  }

  /** One keyed duty mutation with offline fallback. */
  async function runDutyMutation(task: TaskOut, kind: "complete" | "skip") {
    if (user === null || farmId === null) return;
    const farmScope = captureFarmScope();
    const path = `/api/tasks/${task.id}/${kind}`;
    const idempotencyKey = crypto.randomUUID();
    // The skip reason is user-authored content (like a typed reason): persist
    // it in the device's language rather than a fixed English string
    // (2026-09-28 audit — "Tablet skip" was hardcoded English).
    const body = kind === "skip" ? JSON.stringify({ reason: t("worker.skipReason") }) : undefined;
    const rollback = applyOptimisticTaskPatch(
      queryClient,
      task.id,
      kind === "complete" ? { status: "DONE" } : { status: "SKIPPED" },
    );
    setBusyId(task.id);
    try {
      await apiFetch(path, {
        method: "POST",
        body,
        headers: { "Idempotency-Key": idempotencyKey },
      });
      if (kind === "complete") toast.success(t("worker.completedToast"));
      else toast.success(t("worker.skippedToast"));
      await query.refetch();
    } catch (error) {
      // Guard BEFORE any rollback: after a farm-scope change (farm switch,
      // end-shift) the board cache was cleared, and restoring the pre-patch
      // snapshots would resurrect the old scope's rows (2026-09-28 audit, W6).
      if (!farmScope()) {
        // The write is deliberately discarded with the old scope — say so
        // instead of vanishing with the strike-through (2026-09-29 audit).
        toast.info(t("worker.dutyDiscarded"));
        return;
      }
      if (isOfflineQueueableFailure(error)) {
        const queued = enqueueOfflineMutation(
          path,
          {
            method: "POST",
            body,
            headers: { "Idempotency-Key": idempotencyKey },
          },
          { actorScope: String(user.id), farmScope: String(farmId) },
        );
        if (queued) {
          // The optimistic strike-through stays: the write WILL land.
          toast.info(t("worker.queuedToast"));
        } else {
          rollback();
          toast.error(t("worker.queueFull"));
        }
      } else {
        rollback();
        // Route the server's answer through the error mapper — the common
        // duty rejections ("Task is not pending", "not due yet", …) have
        // Telugu phrases, and this Telugu-first surface must not render raw
        // English prose (2026-09-28 audit, H6 residue; 2026-09-29 fix).
        toast.error(
          error instanceof ApiError
            ? mapServerError(t, error.detail, error.status, error.code)
            : t("worker.genericError"),
        );
      }
    } finally {
      setBusyId(null);
    }
  }

  const overdue = payload?.overdue ?? [];
  const today = payload?.today ?? [];
  // Duties past the fetched page must surface as a count, never vanish
  // silently (2026-09-28 audit, W4).
  const hiddenDuties =
    Math.max(0, (payload?.overdue_total ?? 0) - overdue.length) +
    Math.max(0, (payload?.today_total ?? 0) - today.length);

  return (
    <div className="space-y-6">
      <PageHeader title={t("worker.title")} description={t("worker.description")} />
      {query.isPending ? (
        <p role="status" aria-live="polite" className="text-muted-foreground">
          {t("common.loading")}
        </p>
      ) : !payload ? (
        <EmptyState
          icon={ClipboardList}
          title={t("common.somethingWentWrong")}
          description={t("worker.genericError")}
        >
          <Button variant="outline" onClick={() => void query.refetch()}>
            {t("common.retry")}
          </Button>
        </EmptyState>
      ) : overdue.length + today.length === 0 ? (
        <EmptyState
          icon={ClipboardList}
          title={t("worker.empty.title")}
          description={t("worker.empty.description")}
        />
      ) : (
        <>
          {overdue.length > 0 && (
            <section className="space-y-3" aria-labelledby="worker-overdue">
              <h2 id="worker-overdue" className="text-lg font-semibold text-destructive">
                {t("worker.overdueSection")} ({overdue.length})
              </h2>
              <ul className="space-y-3">
                {overdue.map((task) => (
                  <DutyCard
                    key={task.id}
                    task={task}
                    canComplete={canComplete}
                    busy={busyId === task.id}
                    onComplete={(tk) => void runDutyMutation(tk, "complete")}
                    onSkip={(tk) => void runDutyMutation(tk, "skip")}
                  />
                ))}
              </ul>
            </section>
          )}
          {today.length > 0 && (
            <section className="space-y-3" aria-labelledby="worker-today">
              <h2 id="worker-today" className="text-lg font-semibold">
                {t("worker.todaySection")} ({today.length})
              </h2>
              <ul className="space-y-3">
                {today.map((task) => (
                  <DutyCard
                    key={task.id}
                    task={task}
                    canComplete={canComplete}
                    busy={busyId === task.id}
                    onComplete={(tk) => void runDutyMutation(tk, "complete")}
                    onSkip={(tk) => void runDutyMutation(tk, "skip")}
                  />
                ))}
              </ul>
            </section>
          )}
          {hiddenDuties > 0 && (
            <p className="text-sm text-muted-foreground">
              {t(
                hiddenDuties === 1 ? "worker.moreDuties_one" : "worker.moreDuties_many",
                { count: hiddenDuties },
              )}
            </p>
          )}
        </>
      )}
    </div>
  );
}

export default function WorkerBoardPage() {
  const perms = usePermissions();
  const router = useRouter();
  const { user, farmId, loading } = useAuth();

  // Gate: a signed-in session without a farm belongs on the tablet login
  // (effect, never render-phase navigation).
  useEffect(() => {
    if (loading) return;
    if (user === null || farmId === null) router.replace("/worker/login");
  }, [loading, user, farmId, router]);
  if (!loading && (user === null || farmId === null)) return null;
  return <WorkerBoardContent perms={perms} />;
}
