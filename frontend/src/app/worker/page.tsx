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
import { formatDate } from "@/lib/format";
import { useT } from "@/lib/i18n";
import {
  enqueueOfflineMutation,
  isOfflineQueueableFailure,
} from "@/lib/offline-queue";
import { withReturnTo } from "@/lib/permission-navigation";
import { permittedTaskActionPath } from "@/lib/task-action-access";
import { applyOptimisticTaskPatch } from "@/lib/task-optimistic";
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
  const perms = usePermissions();
  const actionPath = permittedTaskActionPath(task.action_url, perms.can);
  const overdue = task.due_date < new Date().toISOString().slice(0, 10);

  return (
    <li
      className="space-y-3 rounded-xl border bg-card p-4 shadow-xs"
      data-testid={`worker-duty-${task.id}`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-lg font-medium leading-snug">{task.title}</p>
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
          </>
        ) : null}
        {actionPath !== null ? (
          <Link
            href={withReturnTo(actionPath, "/worker")}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium"
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
      active_limit: 25,
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
    return (
      <EmptyState
        icon={ClipboardList}
        title={t("worker.needFarm.title")}
        description={t("worker.genericError")}
      />
    );
  }

  /** One keyed duty mutation with offline fallback. */
  async function runDutyMutation(task: TaskOut, kind: "complete" | "skip") {
    if (user === null || farmId === null) return;
    const path = `/api/tasks/${task.id}/${kind}`;
    const idempotencyKey = crypto.randomUUID();
    const body = kind === "skip" ? JSON.stringify({ reason: "Tablet skip" }) : undefined;
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
        toast.error(
          error instanceof ApiError && error.detail
            ? error.detail
            : t("worker.genericError"),
        );
      }
    } finally {
      setBusyId(null);
    }
  }

  const overdue = payload?.overdue ?? [];
  const today = payload?.today ?? [];

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
