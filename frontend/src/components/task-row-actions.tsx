"use client";

/**
 * Complete / Skip / Open-form / Verify / Reject controls for one duty row.
 *
 * Extracted from the tasks board: the board's table rows and
 * mobile cards render these actions, and any future duty surface (owner
 * console drill-throughs, print views) reuses the same permission-gated
 * state machine instead of a divergent copy. `touch` renders the same
 * actions at >=44px (h-11) for the below-md card list — the phone is the
 * worker's primary device and 36px sm buttons sit under every mobile touch
 * guideline once the row's compact table context is gone.
 */

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";

import {
  useCompleteApiTasksTaskIdCompletePost,
  useRejectApiTasksTaskIdRejectPost,
  useSkipApiTasksTaskIdSkipPost,
  useVerifyApiTasksTaskIdVerifyPost,
} from "@/api/generated/endpoints";
import type { TaskOut } from "@/api/generated/models";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { addDays, farmToday, formatDate } from "@/lib/format";
import { useT } from "@/lib/i18n";
import { useMutationError } from "@/lib/mutations";
import { withReturnTo } from "@/lib/permission-navigation";
import { invalidateFarmData } from "@/lib/query-invalidation";
import {
  permittedTaskActionPath,
  taskFormNotDueYet,
  taskSkipUnavailable,
  type PermissionCheck,
} from "@/lib/task-action-access";
import { applyOptimisticTaskPatch } from "@/lib/task-optimistic";
import { useSingleFlight } from "@/lib/use-single-flight";
import { safeAppPath } from "@/lib/utils";

export function RowActions({
  task,
  tab,
  returnTo,
  canComplete,
  canVerify,
  can,
  today,
  touch = false,
}: {
  task: TaskOut;
  tab: string;
  returnTo: string;
  canComplete: boolean;
  canVerify: boolean;
  can: PermissionCheck;
  today: string;
  touch?: boolean;
}) {
  const mutationErrorMessage = useMutationError();
  const queryClient = useQueryClient();
  const t = useT();
  const [skipOpen, setSkipOpen] = useState(false);
  const [skipReason, setSkipReason] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [rejectMissing, setRejectMissing] = useState(false);
  const [recurringConfirmOpen, setRecurringConfirmOpen] = useState(false);
  const [actionError, setActionError] = useState<{
    action: "complete" | "skip" | "verify" | "reject";
    message: string;
  } | null>(null);
  const completeMutation = useCompleteApiTasksTaskIdCompletePost();
  const skipMutation = useSkipApiTasksTaskIdSkipPost();
  const verifyMutation = useVerifyApiTasksTaskIdVerifyPost();
  const rejectMutation = useRejectApiTasksTaskIdRejectPost();
  const actionFlight = useSingleFlight();
  /** Identifies one open/submit cycle across this row's dialogs (skip /
   *  recurring-confirm / reject are mutually exclusive per status). A
   *  submission that resolves after the operator dismissed — and possibly
   *  reopened — a dialog must not close or reset the fresh session; this is
   *  the finance add-dialog's attempt fence, which lets dismissal always
   *  win instead of trapping a modal for the mutation timeout. */
  const dialogAttempt = useRef(0);
  const safeAction = safeAppPath(task.action_url);
  const permittedAction = permittedTaskActionPath(task.action_url, can);
  const actionButtonClass = touch ? "h-11 px-4" : undefined;
  /** Completing a recurring duty immediately spawns its successor — a
   * one-tap destructive-ish action requires a confirmation
   * that states the next occurrence date (max(due, today) + N days, the
   * backend's own anchor, computed locally). */
  const needsRecurringConfirm = task.recur_days !== null;

  function invalidate() {
    invalidateFarmData(queryClient);
  }

  function reportActionError(
    action: "complete" | "skip" | "verify" | "reject",
    error: unknown,
  ) {
    const message = mutationErrorMessage(error, t("common.somethingWentWrong"));
    setActionError({ action, message });
    toast.error(message);
  }

  async function completeTask() {
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      const attempt = ++dialogAttempt.current;
      setActionError(null);
      // Strike the row through instantly; a failed completion rolls the
      // board cache back to exactly what it showed before the tap.
      const rollback = applyOptimisticTaskPatch(queryClient, task.id, {
        status: "DONE",
        completed_at: new Date().toISOString(),
      });
      try {
        await completeMutation.mutateAsync({ taskId: task.id });
        if (!farmScope()) return;
        toast.success(t("tasks.toast.completed"));
        invalidate();
        if (dialogAttempt.current !== attempt) return;
        setRecurringConfirmOpen(false);
      } catch (error) {
        // Guard BEFORE rollback: after a farm switch the board cache was cleared, and
        // restoring the pre-patch snapshots would resurrect the OLD farm's rows.
        if (!farmScope()) return;
        rollback();
        if (dialogAttempt.current === attempt) reportActionError("complete", error);
      }
    });
  }

  async function skipTask() {
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      const attempt = ++dialogAttempt.current;
      setActionError(null);
      try {
        await skipMutation.mutateAsync({
          taskId: task.id,
          data: { reason: skipReason.trim() },
        });
        if (!farmScope()) return;
        toast.success(t("tasks.toast.skipped"));
        invalidate();
        if (dialogAttempt.current !== attempt) return;
        setSkipReason("");
        setSkipOpen(false);
      } catch (error) {
        if (farmScope() && dialogAttempt.current === attempt) {
          reportActionError("skip", error);
        }
      }
    });
  }

  async function verifyTask() {
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      setActionError(null);
      try {
        await verifyMutation.mutateAsync({ taskId: task.id });
        if (!farmScope()) return;
        toast.success(t("tasks.toast.verified"));
        invalidate();
      } catch (error) {
        if (farmScope()) reportActionError("verify", error);
      }
    });
  }

  function openRejectDialog() {
    // A fresh dialog per opening: no stale reason, no stale "required" flag.
    setRejectReason("");
    setRejectMissing(false);
    setRejectOpen(true);
  }

  async function rejectTask() {
    const reason = rejectReason.trim();
    if (!reason) {
      setRejectMissing(true);
      return;
    }
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      const attempt = ++dialogAttempt.current;
      setActionError(null);
      try {
        await rejectMutation.mutateAsync({
          taskId: task.id,
          data: { note: reason },
        });
        if (!farmScope()) return;
        toast.success(t("tasks.toast.sentBack"));
        invalidate();
        if (dialogAttempt.current !== attempt) return;
        setRejectOpen(false);
      } catch (error) {
        if (farmScope() && dialogAttempt.current === attempt) {
          reportActionError("reject", error);
        }
      }
    });
  }

  // While a transition write is in flight the cache may already hold the
  // optimistic status (a completion strikes the row DONE instantly). Keep
  // rendering the actions for the status the row MOUNTED with — disabled —
  // so the in-flight row (and its open dialogs) doesn't unmount or jump to
  // the wrong action set mid-write; the settled status takes over the moment
  // the write resolves.
  const [mountedStatus] = useState(task.status);
  const rowStatus = actionFlight.pending ? mountedStatus : task.status;

  if (rowStatus === "PENDING" && canComplete) {
    // Generated duties cannot complete early. Recurring duties cannot complete
    // or skip early, because either transition would advance the series before
    // its due date. Keep one-off manual duties actionable ahead of schedule.
    const future = task.due_date > today;
    const lockedFutureCompletion =
      future && (task.auto_generated || task.recur_days !== null);
    const lockedFutureRecurrence = future && task.recur_days !== null;
    return (
      <>
        <div className="flex flex-wrap items-center gap-2">
          {permittedAction ? (
            // The linked health form rejects an event before the duty's due
            // date, so offering the link early only walks the user into a
            // guaranteed 409 — mirror the bare-Complete future lock instead.
            taskFormNotDueYet(task, today) ? (
              <span className="text-xs text-muted-foreground">
                {t("tasks.notDueForm")}
              </span>
            ) : (
              <Link
                href={withReturnTo(permittedAction, returnTo)}
                className={
                  touch
                    ?
                      buttonVariants({ variant: "outline", size: "sm", className: "h-11 px-4" })
                    :
                      buttonVariants({ variant: "outline", size: "sm" })
                }
              >
                {t("tasks.openForm")}
              </Link>
            )
          ) : safeAction ? (
            <span className="text-xs text-muted-foreground">
              {t("tasks.formUnavailable")}
            </span>
          ) : lockedFutureCompletion || lockedFutureRecurrence ? (
            // A future-locked duty with no permitted linked form renders
            // nothing at all without this hint — an inert row the operator
            // cannot explain.
            <span className="text-xs text-muted-foreground">
              {t("tasks.notDueActions")}
            </span>
          ) : (
            <Button
              size={touch ? "default" : "sm"}
              variant="outline"
              className={actionButtonClass}
              disabled={actionFlight.pending}
              onClick={() =>
                needsRecurringConfirm ? setRecurringConfirmOpen(true) : void completeTask()
              }
            >
              {actionError?.action === "complete"
                ? t("tasks.retryComplete")
                : t("tasks.complete")}
            </Button>
          )}
          {/* Quarantine-gate and weaning duties always 409 a skip (see
              taskSkipUnavailable) — don't offer an action that cannot
              succeed; their Complete/Open-form workflow stays available. */}
          {!lockedFutureRecurrence && !taskSkipUnavailable(task) && (
            <Button
              size={touch ? "default" : "sm"}
              variant="outline"
              className={actionButtonClass}
              disabled={actionFlight.pending}
              onClick={() => setSkipOpen(true)}
            >
              {t("tasks.skip")}
            </Button>
          )}
        </div>
        {actionError?.action === "complete" && !recurringConfirmOpen && (
          <p role="alert" className="text-sm text-destructive">
            {actionError.message} {t("tasks.actionErrorSuffix")}
          </p>
        )}
        <Dialog
          open={skipOpen}
          onOpenChange={(nextOpen) => {
            // Never block dismissal on an in-flight write: the write itself stays
            // single-flighted, and the continuation is fenced by dialogAttempt so a
            // late resolve cannot close/reset a session the operator reopened.
            if (!nextOpen) dialogAttempt.current += 1;
            setSkipOpen(nextOpen);
          }}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{t("tasks.skip.title")}</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">{t("tasks.skip.body")}</p>
            <div className="space-y-1.5">
              <Label htmlFor={`skip-reason-${task.id}`}>{t("tasks.skip.reason")}</Label>
              <Input
                id={`skip-reason-${task.id}`}
                disabled={actionFlight.pending}
                value={skipReason}
                onChange={(event) => setSkipReason(event.target.value)}
                maxLength={255}
              />
            </div>
            {actionError?.action === "skip" && (
              <p role="alert" className="text-sm text-destructive">
                {actionError.message} {t("tasks.skip.errorSuffix")}
              </p>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={actionFlight.pending}
                onClick={() => setSkipOpen(false)}
              >
                {t("common.cancel")}
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={actionFlight.pending || !skipReason.trim()}
                onClick={() => void skipTask()}
              >
                {actionFlight.pending
                  ? t("tasks.skip.inFlight")
                  : actionError?.action === "skip"
                    ? t("tasks.skip.retry")
                    : t("tasks.skip.confirm")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        {needsRecurringConfirm && (
          <Dialog
            open={recurringConfirmOpen}
            onOpenChange={(nextOpen) => {
              // Same never-block rule as the skip dialog above.
              if (!nextOpen) dialogAttempt.current += 1;
              setRecurringConfirmOpen(nextOpen);
            }}
          >
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>{t("tasks.recurConfirm.title")}</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-muted-foreground">
                {t("tasks.recurConfirm.body", {
                  days: task.recur_days ?? 0,
                  /* Mirror the backend's anchor (services/tasks.py:
                     recurrence_anchor = max(due_date, today)): an overdue
                     duty's successor spawns from the farm's today, not from
                     the stale due date the old copy stated. */
                  date: formatDate(
                    addDays(
                      task.due_date > farmToday() ? task.due_date : farmToday(),
                      task.recur_days ?? 0,
                    ),
                  ),
                })}
              </p>
              {actionError?.action === "complete" && (
                <p role="alert" className="text-sm text-destructive">
                  {actionError.message} {t("tasks.actionErrorSuffix")}
                </p>
              )}
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  disabled={actionFlight.pending}
                  onClick={() => setRecurringConfirmOpen(false)}
                >
                  {t("common.cancel")}
                </Button>
                <Button
                  type="button"
                  disabled={actionFlight.pending}
                  onClick={() => void completeTask()}
                >
                  {t("tasks.recurConfirm.confirm")}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </>
    );
  }

  if (tab === "awaiting" && canVerify) {
    return (
      <>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size={touch ? "default" : "sm"}
            variant="outline"
            className={actionButtonClass}
            disabled={actionFlight.pending}
            onClick={() => void verifyTask()}
          >
            {actionError?.action === "verify" ? t("tasks.retryVerify") : t("tasks.verify")}
          </Button>
          {/* Rejecting sends the duty back to the worker — destructive and
           * reason-bearing, so it goes through a confirmation dialog instead
           * of the old inline 28px input + instant POST. */}
          <Button
            size={touch ? "default" : "sm"}
            variant="destructive"
            className={actionButtonClass}
            disabled={actionFlight.pending}
            onClick={openRejectDialog}
          >
            {actionError?.action === "reject" ? t("tasks.retryReject") : t("tasks.reject")}
          </Button>
          {actionError?.action === "verify" && (
            <p role="alert" className="basis-full text-sm text-destructive">
              {actionError.message} {t("tasks.actionErrorSuffix")}
            </p>
          )}
        </div>
        <Dialog
          open={rejectOpen}
          onOpenChange={(nextOpen) => {
            // Same never-block rule as the skip dialog above.
            if (!nextOpen) dialogAttempt.current += 1;
            setRejectOpen(nextOpen);
          }}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{t("tasks.reject.title")}</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">{t("tasks.reject.body")}</p>
            <div className="space-y-1.5">
              <Label htmlFor={`reject-reason-${task.id}`}>{t("tasks.reject.reason")}</Label>
              <Textarea
                id={`reject-reason-${task.id}`}
                disabled={actionFlight.pending}
                value={rejectReason}
                onChange={(event) => {
                  setRejectReason(event.target.value);
                  if (rejectMissing && event.target.value.trim()) setRejectMissing(false);
                }}
                aria-invalid={rejectMissing || undefined}
                aria-describedby={rejectMissing ? `reject-reason-missing-${task.id}` : undefined}
                maxLength={255}
                rows={3}
              />
              {rejectMissing && (
                <p
                  id={`reject-reason-missing-${task.id}`}
                  role="alert"
                  className="text-sm text-destructive"
                >
                  {t("tasks.reject.reasonMissing")}
                </p>
              )}
            </div>
            {actionError?.action === "reject" && (
              <p role="alert" className="text-sm text-destructive">
                {actionError.message} {t("tasks.actionErrorSuffix")}
              </p>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={actionFlight.pending}
                onClick={() => setRejectOpen(false)}
              >
                {t("common.cancel")}
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={actionFlight.pending}
                onClick={() => void rejectTask()}
              >
                {actionFlight.pending
                  ? t("tasks.reject.inFlight")
                  : actionError?.action === "reject"
                    ? t("tasks.retryReject")
                    : t("tasks.reject.confirm")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </>
    );
  }

  return null;
}
