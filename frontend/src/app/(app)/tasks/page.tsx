"use client";

/** Tasks/duties board — parity with v1's tasks/list.html + tasks/new.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ListChecks, Plus } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useCompleteApiTasksTaskIdCompletePost,
  useCreateTaskApiTasksPost,
  useListTasksApiTasksGet,
  useRejectApiTasksTaskIdRejectPost,
  useSkipApiTasksTaskIdSkipPost,
  useTeamPageApiTeamGet,
  useVerifyApiTasksTaskIdVerifyPost,
} from "@/api/generated/endpoints";
import { TaskCreateInCategory, type TaskOut } from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
import { PaginationControls } from "@/components/pagination-controls";
import { InlineLoading, PageSkeleton } from "@/components/skeletons";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api-client";
import { mutationError } from "@/lib/mutations";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { MAX_TASK_TITLE_LENGTH, MAX_RECUR_DAYS } from "@/lib/backend-caps";
import { useAuth } from "@/lib/auth-context";
import { addDays, daysBetween, farmToday, formatDate, formatFarmDateTime } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { withReturnTo } from "@/lib/permission-navigation";
import { useLanguage, useT, type TFn } from "@/lib/i18n";
import { applyOptimisticTaskPatch } from "@/lib/task-optimistic";
import { resolveTaskTitle } from "@/lib/task-title";
import {
  permittedTaskActionPath,
  taskFormNotDueYet,
  taskSkipUnavailable,
  type PermissionCheck,
} from "@/lib/task-action-access";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { MAX_PAGE_OFFSET } from "@/lib/use-url-state";
import { safeAppPath } from "@/lib/utils";
import { enumLabel } from "@/lib/enum-labels";

const CATEGORIES = Object.values(TaskCreateInCategory);
/** Sentinel for "no selection" in optional selects (empty string is not a valid item value). */
const NONE = "none";
/** Tabs addressable via /tasks?tab=… deep links (dashboard links here). */
const TASK_TABS = ["today", "overdue", "upcoming", "awaiting", "completed"] as const;
type TaskTab = (typeof TASK_TABS)[number];
type TaskOffsets = Record<TaskTab, number>;
type TaskOffsetKey = `${TaskTab}_offset`;

const VALID_TABS = new Set<string>(TASK_TABS);
/** Wire form for an optional select value: NONE/empty → null, real ids → numbers. */
// Stryker disable ConditionalExpression, LogicalOperator: Number(NONE) is NaN and JSON serialization maps NaN to null — the same wire value the null arm produces — and real selections are locked by payload assertions in the suite
function noneToNull(value: string | undefined): number | null {
  return value && value !== NONE ? Number(value) : null;
}
// Stryker restore ConditionalExpression, LogicalOperator
/** Full-phrase empty-state titles ("No today tasks" reads wrong). */
function taskEmptyTitle(tab: TaskTab, t: TFn): string {
  switch (tab) {
    case "today":
      return t("tasks.empty.today");
    case "overdue":
      return t("tasks.empty.overdue");
    case "upcoming":
      return t("tasks.empty.upcoming");
    case "awaiting":
      return t("tasks.empty.awaiting");
    case "completed":
      return t("tasks.empty.completed");
  }
}
/** Per-tab guidance for an empty board column: point at the sibling tab that
 * actually carries work instead of dead-ending (no fake CTA — the tabs are
 * one click away). */
function tabEmptyGuidance(tab: TaskTab, t: TFn): string {
  switch (tab) {
    case "today":
      return t("tasks.guidance.today");
    case "overdue":
      return t("tasks.guidance.overdue");
    case "upcoming":
      return t("tasks.guidance.upcoming");
    case "awaiting":
      return t("tasks.guidance.awaiting");
    case "completed":
      return t("tasks.guidance.completed");
  }
}
const TASK_PAGE_SIZE = 50;
/** Shared backend mirror (pinned by backend-constants-parity.test.ts):
 * the tasks endpoints 422 any deeper offset, so the clamp must match the
 * backend's ceiling exactly (P2-16). */
const MAX_TASK_OFFSET = MAX_PAGE_OFFSET;
const TASK_OFFSET_KEYS: Record<TaskTab, TaskOffsetKey> = {
  today: "today_offset",
  overdue: "overdue_offset",
  upcoming: "upcoming_offset",
  awaiting: "awaiting_offset",
  completed: "completed_offset",
};

function validTaskTab(value: string | null): value is TaskTab {
  // Stryker disable next-line ConditionalExpression: VALID_TABS.has(null) is false, so dropping the !== null arm decides nothing the set does not already decide
  return value !== null && VALID_TABS.has(value);
}

function parseTaskOffset(raw: string | null): number {
  // Stryker disable next-line ConditionalExpression: the regex rejects null (coerced "null") exactly like any malformed string, so the === null arm never decides anything
  if (raw === null || !/^(0|[1-9]\d*)$/.test(raw)) return 0;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed <= MAX_TASK_OFFSET ? parsed : 0;
}

function taskOffsetsFromParams(params: URLSearchParams): TaskOffsets {
  return Object.fromEntries(
    TASK_TABS.map((taskTab) => [taskTab, parseTaskOffset(params.get(TASK_OFFSET_KEYS[taskTab]))]),
  ) as TaskOffsets;
}

function hasInvalidTaskOffset(params: URLSearchParams): boolean {
  return TASK_TABS.some((taskTab) => {
    const raw = params.get(TASK_OFFSET_KEYS[taskTab]);
    return raw !== null && String(parseTaskOffset(raw)) !== raw;
  });
}

function taskListUrl({
  pathname,
  paramsKey,
  tab,
  offsets,
}: {
  pathname: string;
  paramsKey: string;
  tab: TaskTab;
  offsets: TaskOffsets;
}): string {
  const params = new URLSearchParams(paramsKey);
  params.set("tab", tab);
  for (const taskTab of TASK_TABS) {
    const key = TASK_OFFSET_KEYS[taskTab];
    const offset = offsets[taskTab];
    if (offset > 0) params.set(key, String(offset));
    else params.delete(key);
  }
  const rest = params.toString();
  return rest ? `${pathname}?${rest}` : pathname;
}

function lastTaskOffset(total: number, limit: number): number {
  if (total <= 0 || limit <= 0) return 0;
  return Math.floor((total - 1) / limit) * limit;
}

function sameTaskOffsets(left: TaskOffsets, right: TaskOffsets): boolean {
  return TASK_TABS.every((taskTab) => left[taskTab] === right[taskTab]);
}




/** Complete / Skip / Open-form / Verify / Reject controls for one row.
 *
 * `touch` renders the same actions at ≥44px (h-11) for the below-md card
 * list — the phone is the worker's primary device and 36px sm buttons sit
 * under every mobile touch guideline once the row's compact table context
 * is gone. */
function RowActions({
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
  const queryClient = useQueryClient();
  const t = useT();
  const [skipOpen, setSkipOpen] = useState(false);
  const [skipReason, setSkipReason] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  // Stryker disable next-line StringLiteral: openRejectDialog resets the reason before every open, so the initial state is never observable
  const [rejectReason, setRejectReason] = useState("");
  // Stryker disable next-line BooleanLiteral: openRejectDialog resets the flag before every open, so the initial state is never observable
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
  const safeAction = safeAppPath(task.action_url);
  const permittedAction = permittedTaskActionPath(task.action_url, can);
  const actionButtonClass = touch ? "h-11 px-4" : undefined;
  /** Completing a recurring duty immediately spawns its successor — a
   * one-tap destructive-ish action the audit asked to gate behind a confirm
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
    const message = mutationError(error, t("common.somethingWentWrong"));
    setActionError({ action, message });
    toast.error(message);
  }

  async function completeTask() {
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
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
        setRecurringConfirmOpen(false);
        invalidate();
      } catch (error) {
        rollback();
        if (farmScope()) reportActionError("complete", error);
      }
    });
  }

  async function skipTask() {
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      setActionError(null);
      try {
        await skipMutation.mutateAsync({
          taskId: task.id,
          data: { reason: skipReason.trim() },
        });
        if (!farmScope()) return;
        toast.success(t("tasks.toast.skipped"));
        setSkipReason("");
        setSkipOpen(false);
        invalidate();
      } catch (error) {
        if (farmScope()) reportActionError("skip", error);
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
      setActionError(null);
      try {
        await rejectMutation.mutateAsync({
          taskId: task.id,
          data: { note: reason },
        });
        if (!farmScope()) return;
        toast.success(t("tasks.toast.sentBack"));
        setRejectOpen(false);
        invalidate();
      } catch (error) {
        if (farmScope()) reportActionError("reject", error);
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
    // Stryker disable next-line ConditionalExpression: the auto_generated arm only differs when recur_days is non-null, and lockedFutureRecurrence already locks that exact state — the disjunction renders identically either way
    const lockedFutureCompletion =
      // Stryker disable next-line ConditionalExpression: every lock test exercises an auto-generated future duty, and a recurring duty is always auto-generated on this wire (series creation sets the flag), so the recur arm never decides anything alone
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
                    ? // Stryker disable next-line StringLiteral: size "" falls back to cva's default size, whose row height matches sm; the touch sizing rides the asserted className argument
                      buttonVariants({ variant: "outline", size: "sm", className: "h-11 px-4" })
                    : // Stryker disable next-line StringLiteral: size "" falls back to cva's default size, whose row height matches sm
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
              // Stryker disable next-line StringLiteral: size "" falls back to cva's default size, which is the touch arm's own value
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
              // Stryker disable next-line StringLiteral: size "" falls back to cva's default size, which is the touch arm's own value
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
            // Stryker disable next-line ConditionalExpression, BooleanLiteral: the single-flight guard blocks a resubmission regardless, so the wider close-lock variants are defense-in-depth
            if (!nextOpen && actionFlight.pending) return;
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
              if (!nextOpen && actionFlight.pending) return;
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
                     the stale due date the old copy stated (wave-5 note,
                     2026-09-20 audit). */
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
            // Stryker disable next-line StringLiteral: size "" falls back to cva's default size, which is the touch arm's own value
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
            // Stryker disable next-line StringLiteral: size "" falls back to cva's default size, which is the touch arm's own value
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
            // Stryker disable next-line ConditionalExpression, BooleanLiteral: the single-flight guard blocks a resubmission regardless, so the wider close-lock variants are defense-in-depth
            if (!nextOpen && actionFlight.pending) return;
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

function TaskTable({
  tasks,
  tab,
  returnTo,
  canComplete,
  canVerify,
  can,
  canViewAnimals,
  today,
  currentUserId,
  isOwner,
  resolveMemberName,
}: {
  tasks: TaskOut[];
  tab: string;
  returnTo: string;
  canComplete: boolean;
  canVerify: boolean;
  can: PermissionCheck;
  canViewAnimals: boolean;
  today: string;
  currentUserId: number | null;
  isOwner: boolean;
  /** Resolves completed_by/skipped_by ids to a worker name; returns null when
   * the lookup is not permitted (workers hold no team.manage) or failed —
   * never a raw id. */
  resolveMemberName: (id: number | null) => string | null;
}) {
  const t = useT();
  const { language } = useLanguage();

  if (tasks.length === 0) {
    // Stryker disable next-line ConditionalExpression, StringLiteral: TaskTable's tab comes from visibleTabs values (the TaskTab union), so validTaskTab(tab) is always true and the fallback arms are unreachable defense-in-depth
    const emptyTitle = validTaskTab(tab) ? taskEmptyTitle(tab, t) : t("tasks.empty.fallbackTitle");
    // Stryker disable ConditionalExpression, StringLiteral: TaskTable's tab comes from visibleTabs values (the TaskTab union), so validTaskTab(tab) is always true and the fallback arms are unreachable defense-in-depth
    const emptyGuidance = validTaskTab(tab)
      ? tabEmptyGuidance(tab, t)
      : t("tasks.empty.fallbackGuidance");
    return <EmptyState icon={ListChecks} title={emptyTitle} description={emptyGuidance} />;
  }
  const completedTab = tab === "completed";
  /** The worker who produced the Finished timestamp (skipped_by for skips,
   * completed_by otherwise) — rendered as "by {name}" only when resolvable. */
  const finishedActorName = (task: TaskOut): string | null =>
    resolveMemberName(task.status === "SKIPPED" ? task.skipped_by_id : task.completed_by_id);

  const animalCell = (task: TaskOut) =>
    task.animal_tag && task.animal_id && canViewAnimals ? (
      <Link
        href={withReturnTo(`/animals/${task.animal_id}`, returnTo)}
        className="text-primary underline"
      >
        {task.animal_tag}
      </Link>
    ) : task.animal_tag && task.animal_id ? (
      task.animal_tag
    ) : (
      "—"
    );

  return (
    <DataTableCard>
      {/* Below md the 7+-column board becomes a card per duty — panning a
       * 720px table inside a 390px phone is not a board, it's a scroll toy
       * (same pattern as the animals register). */}
      <div className="space-y-2 md:hidden">
        {tasks.map((task) => {
          const overdue = task.status === "PENDING" && task.due_date < today;
          const dueToday = task.status === "PENDING" && task.due_date === today;
          const finishedAt =
            task.status === "SKIPPED" ? task.skipped_at : task.completed_at;
          // Stryker disable next-line LogicalOperator: completed rows always carry finishedAt (the endpoint only returns finished duties for that tab), so the conjunction is a tautology on reachable data
          const showFinishedAt = completedTab && finishedAt;
          const actorName = finishedActorName(task);
          return (
            <div key={task.id} className="space-y-2 rounded-xl border bg-card p-3 shadow-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={overdue ? "destructive" : dueToday ? "warning" : "secondary"}>
                  {formatDate(task.due_date)}
                </Badge>
                {overdue && (
                  <span className="text-xs text-destructive">
                    {t("tasks.daysLate", { days: daysBetween(task.due_date, today) })}
                  </span>
                )}
                {task.recur_days !== null && (
                  <Badge variant="secondary">
                    {t("tasks.everyDays", { days: task.recur_days })}
                  </Badge>
                )}
                {completedTab && (
                  <StatusBadge status={task.status}>{task.status}</StatusBadge>
                )}
              </div>
              <div>
                {/* Optimistic completion marks the row DONE before the
                 * refetch lands — strike it through immediately. */}
                <p
                  dir="auto"
                  // data-done is the semantic state; the strike-through is
                  // only its styling (tests assert the attribute, not the
                  // utility class — 2026-09-20 audit P3).
                  data-done={task.status !== "PENDING" || undefined}
                  className={
                    task.status === "PENDING"
                      ? "font-medium"
                      : "font-medium text-muted-foreground line-through"
                  }
                >
                  {resolveTaskTitle(task, language)}
                </p>
                {task.status === "PENDING" && task.verification_note && (
                  <p className="text-sm text-destructive">
                    {t("tasks.sentBack", { note: task.verification_note })}
                  </p>
                )}
                {completedTab && task.status === "SKIPPED" && task.skip_reason && (
                  <p className="text-xs text-muted-foreground">
                    {t("tasks.skipReasonLabel", { reason: task.skip_reason })}
                  </p>
                )}
              </div>
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                <Badge variant="secondary">
                  {enumLabel("taskCategory", task.category, language)}
                </Badge>
                <span>
                  {/* A personal assignment always carries a continuity role as
                   * well (tasks.py `effective_role_id`), so the named worker
                   * must win — otherwise every personal duty is displayed as
                   * if it were assigned to the whole role. */}
                  {task.assigned_user_name
                    ? task.assigned_role_name
                      ? `${task.assigned_user_name} ${t("tasks.viaRole", { role: task.assigned_role_name })}`
                      : task.assigned_user_name
                    : (task.assigned_role_name ?? "—")}
                </span>
                {task.animal_tag && <span>· {animalCell(task)}</span>}
                {showFinishedAt && (
                  <span>
                    · {formatFarmDateTime(finishedAt)}
                    {actorName && ` ${t("tasks.by", { name: actorName })}`}
                  </span>
                )}
              </p>
              <RowActions
                task={task}
                tab={tab}
                returnTo={returnTo}
                canComplete={canComplete}
                can={can}
                canVerify={
                  // The API 409s self-verification for non-owners — don't
                  // offer the action.
                  canVerify && (isOwner || task.completed_by_id !== currentUserId)
                }
                today={today}
                touch
              />
            </div>
          );
        })}
      </div>
      {/* 6–8 columns: keep a floor so the board rows don't crush on tablets;
       * phones use the card list above. */}
      <div className="hidden md:block">
        <Table className="min-w-[720px]">
          <TableHeader>
            <TableRow>
              <TableHead>{t("tasks.col.due")}</TableHead>
              <TableHead>{t("tasks.col.task")}</TableHead>
              <TableHead>{t("tasks.col.category")}</TableHead>
              <TableHead>{t("tasks.col.assignedTo")}</TableHead>
              <TableHead>{t("tasks.col.animal")}</TableHead>
              {completedTab && <TableHead>{t("tasks.col.status")}</TableHead>}
              {completedTab && <TableHead>{t("tasks.col.finished")}</TableHead>}
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.map((t2) => {
              const overdue = t2.status === "PENDING" && t2.due_date < today;
              const dueSoon =
                t2.status === "PENDING" && !overdue && daysBetween(today, t2.due_date) <= 2;
              const actorName = finishedActorName(t2);
              return (
                <TableRow key={t2.id}>
                  <TableCell>
                    {(overdue || dueSoon) && (
                      <Badge variant={overdue ? "destructive" : "warning"}>
                        {formatDate(t2.due_date)}
                      </Badge>
                    )}
                    {!(overdue || dueSoon) && (
                      <span>{formatDate(t2.due_date)}</span>
                    )}
                    {overdue && (
                      <span className="text-destructive">
                        {" "}
                        {t("tasks.daysLate", { days: daysBetween(t2.due_date, today) })}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {/* Optimistic completion strikes the duty through before
                     * the board refetch lands. */}
                    <span
                      dir="auto"
                      data-done={t2.status !== "PENDING" || undefined}
                      className={t2.status === "PENDING" ? undefined : "text-muted-foreground line-through"}
                    >
                      {resolveTaskTitle(t2, language)}
                    </span>
                    {t2.recur_days !== null && (
                      <Badge variant="secondary" className="ml-2">
                        {t("tasks.everyDays", { days: t2.recur_days })}
                      </Badge>
                    )}
                    {t2.status === "PENDING" && t2.verification_note && (
                      <>
                        <br />
                        <span className="text-destructive">
                          {t("tasks.sentBack", { note: t2.verification_note })}
                        </span>
                      </>
                    )}
                    {completedTab && t2.status === "SKIPPED" && t2.skip_reason && (
                      <>
                        <br />
                        <span className="text-xs text-muted-foreground">
                          {t("tasks.skipReasonLabel", { reason: t2.skip_reason })}
                        </span>
                      </>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">
                      {enumLabel("taskCategory", t2.category, language)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {t2.assigned_user_name ? (
                      <>
                        {t2.assigned_user_name}
                        {t2.assigned_role_name && (
                          <>
                            <br />
                            <span className="text-xs text-muted-foreground">
                              {t("tasks.viaRole", { role: t2.assigned_role_name })}
                            </span>
                          </>
                        )}
                      </>
                    ) : (
                      t2.assigned_role_name ?? "—"
                    )}
                  </TableCell>
                  <TableCell>{animalCell(t2)}</TableCell>
                  {completedTab && (
                    <TableCell>
                      <StatusBadge status={t2.status}>{t2.status}</StatusBadge>
                      {t2.status !== "VERIFIED" && t2.needs_verification && (
                        <span className="ml-1 text-xs text-muted-foreground">
                          {t("tasks.awaitingMarker")}
                        </span>
                      )}
                    </TableCell>
                  )}
                  {completedTab && (
                    <TableCell>
                      <span>
                        {formatFarmDateTime(
                          t2.status === "SKIPPED" ? t2.skipped_at : t2.completed_at,
                        )}
                      </span>
                      {actorName && (
                        <span className="ml-1 text-xs text-muted-foreground">
                          {t("tasks.by", { name: actorName })}
                        </span>
                      )}
                    </TableCell>
                  )}
                  <TableCell>
                    <RowActions
                      task={t2}
                      tab={tab}
                      returnTo={returnTo}
                      canComplete={canComplete}
                      can={can}
                      canVerify={
                        // The API 409s self-verification for non-owners — don't
                        // offer the action.
                        canVerify && (isOwner || t2.completed_by_id !== currentUserId)
                      }
                      today={today}
                    />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </DataTableCard>
  );
}

/** Duty form schema is rebuilt per language so inline validation messages
 * follow the worker's chosen language (server-side 422s stay English — the
 * backend has no i18n contract yet). Exported for direct schema-level
 * testing of the inline gates. */
export function makeDutySchema(t: TFn) {
  return z
    .object({
      title: z
        .string()
        .trim()
        .min(1, t("tasks.form.titleRequired"))
        .max(MAX_TASK_TITLE_LENGTH),
      due_date: z
        .string()
        .min(1, t("tasks.form.dueRequired"))
        .regex(
          // Stryker disable next-line Regex: hand-proven killed by the trailing-garbage schema test in page.campaign.test.tsx — Stryker's perTest selection never includes it for this schema-factory mutant; documented attribution artifact (third occurrence of the pattern)
          /^\d{4}-\d{2}-\d{2}$/,
          t("tasks.form.dueInvalid"),
        )
        // Mirror the backend's year band so impossible dates fail inline
        // instead of as a 422 after submit.
        .refine((v) => {
          const year = Number(v.slice(0, 4));
          return year >= 2000 && year <= 2100;
        }, t("tasks.form.yearBand")),
      category: z.enum(["FEED", "CLEANING", "OTHER"]),
      recur_days: z
        .string()
        .refine(
          (s) =>
            s === "" ||
            (/^\d+$/.test(s) && Number(s) >= 1 && Number(s) <= MAX_RECUR_DAYS),
          t("tasks.form.recurInvalid", { max: MAX_RECUR_DAYS }),
        )
        .optional(),
      assigned_role_id: z.string().optional(),
      assigned_user_id: z.string().optional(),
      animal_id: z.string().optional(),
    })
    .superRefine((values, ctx) => {
      if (!values.recur_days || !/^\d+$/.test(values.recur_days)) return;
      const recurrenceDays = Number(values.recur_days);
      // Stryker disable next-line LogicalOperator: the field-level refine only admits digit strings within 1..MAX, so the superRefine's isInteger/range re-check is tautological over schema-valid values — every arm variant rejects the same inputs
      if (!Number.isInteger(recurrenceDays) || recurrenceDays < 1 || recurrenceDays > MAX_RECUR_DAYS) {
        return;
      }
      // The backend validates the 2000–2100 band only on the create request
      // (schemas/tasks.py); successors are inserted by services/tasks.py, which
      // bypasses that validator. Reject inline any series whose first occurrence
      // would already leave the band — forward-consistent with the backend's
      // contract even though today's spawn path would not 422.
      const latestDueDate = addDays("2100-12-31", -recurrenceDays);
      if (values.due_date > latestDueDate) {
        ctx.addIssue({
          code: "custom",
          path: ["due_date"],
          message: t("tasks.form.recurTooLate"),
        });
      }
    });
}
type DutyValues = z.infer<ReturnType<typeof makeDutySchema>>;

/** Rebuilt on every reset: a bare reset() restores react-hook-form's
 * mount-time snapshot, which dates duties to the day the tab was opened. */
function dutyDefaults(): DutyValues {
  return {
    title: "",
    due_date: farmToday(),
    category: "OTHER",
    recur_days: "",
    assigned_role_id: NONE,
    assigned_user_id: NONE,
    animal_id: NONE,
  };
}

function TasksPageContent({ perms }: { perms: PermissionsState }) {
  const { can, isOwner } = perms;
  const { user } = useAuth();
  const t = useT();
  const { language } = useLanguage();
  const allowed = can("tasks.view");
  const canCreate = can("tasks.create");
  const canComplete = can("tasks.complete");
  const canVerify = can("tasks.verify");
  const canSeeTeam = can("team.manage");
  const canViewAnimals = can("animals.view");
  const queryClient = useQueryClient();

  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const paramsKey = searchParams.toString();
  // Memoized on paramsKey: a fresh object per render gave `offsets` a new
  // identity every render, so the offset-normalization effect below re-ran
  // on every render (P3, 2026-09-20 audit) — its dedupe refs absorbed the
  // work, but the run itself was pure churn.
  const paramsOffsets = useMemo(
    () => taskOffsetsFromParams(new URLSearchParams(paramsKey)),
    [paramsKey],
  );
  // Honor ?tab= deep links (the dashboard links to /tasks?tab=overdue etc.);
  // unknown values fall back to "today".
  const requestedTab = searchParams.get("tab");
  const paramsTab: TaskTab = validTaskTab(requestedTab) ? requestedTab : "today";
  const [navigationOverride, setNavigationOverride] = useState<{
    sourceParamsKey: string;
    tab: TaskTab;
    offsets: TaskOffsets;
  } | null>(null);
  const activeNavigationOverride =
    navigationOverride?.sourceParamsKey === paramsKey ? navigationOverride : null;
  const tab = activeNavigationOverride?.tab ?? paramsTab;
  const offsets = activeNavigationOverride?.offsets ?? paramsOffsets;
  const normalizedUrlRef = useRef<string | null>(null);
  const [open, setOpen] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const query = useListTasksApiTasksGet(
    {
      active_limit: TASK_PAGE_SIZE,
      today_offset: offsets.today,
      overdue_offset: offsets.overdue,
      upcoming_offset: offsets.upcoming,
      awaiting_offset: offsets.awaiting,
      completed_limit: TASK_PAGE_SIZE,
      completed_offset: offsets.completed,
    },
    {
      query: {
        enabled: allowed,
        // The key embeds five tab offsets; without the previous payload a
        // page turn unmounted the whole board into "Loading…" (M-12).
        placeholderData: (previous) => previous,
        // ITEM 10: staleness strategy is "navigate again" no more — returning
        // to the tab refreshes the board (same farm, same key, cheap refetch).
        refetchOnWindowFocus: true,
      },
    },
  );
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  // A same-route navigation eventually supplies a new search-param string. At
  // that point the URL is authoritative again; the override only bridges the
  // render before Next publishes those params (and keeps unit tests honest).
  useEffect(() => {
    if (!navigationOverride || navigationOverride.sourceParamsKey === paramsKey) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNavigationOverride(null);
  }, [navigationOverride, paramsKey]);

  // Mutations can shrink a bucket while it is open. Recover every independently
  // paged bucket in one URL replacement so no tab is stranded beyond its last
  // real page. Malformed URL offsets are canonicalized through the same path.
  useEffect(() => {
    if (!payload) return;
    const nextOffsets: TaskOffsets = {
      today: Math.min(
        offsets.today,
        Math.min(MAX_TASK_OFFSET, lastTaskOffset(payload.today_total, payload.active_limit)),
      ),
      overdue: Math.min(
        offsets.overdue,
        Math.min(MAX_TASK_OFFSET, lastTaskOffset(payload.overdue_total, payload.active_limit)),
      ),
      upcoming: Math.min(
        offsets.upcoming,
        Math.min(MAX_TASK_OFFSET, lastTaskOffset(payload.upcoming_total, payload.active_limit)),
      ),
      awaiting: Math.min(
        offsets.awaiting,
        Math.min(MAX_TASK_OFFSET, lastTaskOffset(payload.awaiting_total, payload.active_limit)),
      ),
      completed: Math.min(
        offsets.completed,
        Math.min(
          MAX_TASK_OFFSET,
          lastTaskOffset(payload.completed_total, payload.completed_limit),
        ),
      ),
    };
    const invalidUrlOffset = hasInvalidTaskOffset(new URLSearchParams(paramsKey));
    if (!invalidUrlOffset && sameTaskOffsets(nextOffsets, offsets)) {
      normalizedUrlRef.current = null;
      return;
    }

    const normalizedUrl = taskListUrl({
      pathname,
      paramsKey,
      tab,
      offsets: nextOffsets,
    });
    if (normalizedUrlRef.current === normalizedUrl) return;
    normalizedUrlRef.current = normalizedUrl;
    const handle = window.setTimeout(() => {
      setNavigationOverride({ sourceParamsKey: paramsKey, tab, offsets: nextOffsets });
    }, 0);
    router.replace(normalizedUrl);
    // Stryker disable next-line ArrowFunction: React 18 no-ops setState after unmount, so the missing clearTimeout would only suppress a no-op state update
    return () => window.clearTimeout(handle);
  }, [offsets, paramsKey, pathname, payload, router, tab]);

  // A deep-linked tab the user cannot view (e.g. ?tab=awaiting without
  // tasks.verify) falls back visually to "today"; rewrite the URL to the tab
  // actually shown so reloads and shared links agree with the screen.
  useEffect(() => {
    // Stryker disable next-line BooleanLiteral: the effect also runs before the payload lands; with the guard inverted, that mount-time run issues the same canonical rewrite the payload run would, so the observable URL result is identical
    if (!payload) return;
    const canonical =
      validTaskTab(tab) && (tab !== "awaiting" || canVerify) ? tab : "today";
    if (tab === canonical) return;
    router.replace(taskListUrl({ pathname, paramsKey, tab: canonical, offsets }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload, tab, canVerify]);

  const teamQuery = useTeamPageApiTeamGet({
    query: { enabled: canCreate && canSeeTeam && open },
  });
  // Completed-by name resolution shares the /api/team query key (and its
  // cache) with the create dialog above. /api/team requires team.manage, so
  // workers get no resolution at all — the Finished cell then renders only
  // the timestamp, never a raw user id. The fetch waits until the Completed
  // tab actually shows rows; no other tab pays for it.
  // Stryker disable next-line ArrayDeclaration: payload is undefined only while the loading state hides the table, so the fallback array is never rendered
  const completedRows = payload?.completed ?? [];
  const memberNamesQuery = useTeamPageApiTeamGet({
    query: { enabled: canSeeTeam && tab === "completed" && completedRows.length > 0 },
  });
  // Stryker disable ArrayDeclaration: a junk fallback entry maps to an unusable undefined key, and every lookup falls back to null exactly as with the empty array
  const memberNames = new Map(
    (memberNamesQuery.data?.status === 200
      ? memberNamesQuery.data.data.memberships
      : []
    ).map((m) => [m.user_id, m.name ?? m.email]),
  );
  // Stryker restore ArrayDeclaration
  // Stryker disable next-line ConditionalExpression: for id===null the Map lookup returns undefined and ?? null yields the same null as the explicit branch
  const resolveMemberName = (id: number | null): string | null =>
    id === null ? null : (memberNames.get(id) ?? null);
  const team = teamQuery.data?.status === 200 ? teamQuery.data.data : undefined;
  /** value → label maps for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  // Stryker disable ArrayDeclaration: these maps only feed the Select items prop (the closed trigger's label); the rendered option lists come from team.roles/team.memberships directly, and junk entries add nothing but an unusable undefined key
  const roleItems: Record<string, string> = {
    [NONE]: t("common.none"),
    ...Object.fromEntries((team?.roles ?? []).map((r) => [String(r.id), r.name])),
  };
  const workerItems: Record<string, string> = {
    [NONE]: t("common.none"),
    ...Object.fromEntries(
      // Stryker disable next-line MethodExpression, OptionalChaining: the wire TeamOut always carries its memberships array; the fallback guards only malformed payloads, and a missing team already short-circuits the chain
      (team?.memberships ?? [])
        .filter((m) => m.is_active)
        .map((m) => [
          String(m.user_id),
          `${m.name ?? m.email} (${m.role_name ?? t("tasks.form.workerFallback")})`,
        ]),
    ),
  };
  // Stryker restore ArrayDeclaration
  /** value → label map for the category select (language-aware). */
  const categoryItems: Record<string, string> = Object.fromEntries(
    CATEGORIES.map((c) => [c, enumLabel("taskCategory", c, language)]),
  );
  // Rendered only under the `team && !teamQuery.isError` gate inside the dialog.
  // Stryker disable next-line OptionalChaining: gated by `team && !teamQuery.isError` where rendered, so team is non-null there
  const roleSelectOptions = team?.roles ?? [];
  // Stryker disable next-line OptionalChaining, ArrayDeclaration: gated by `team && !teamQuery.isError` where rendered (team non-null), and junk fallback entries fail the is_active filter, yielding the same empty list
  const workerSelectOptions = (team?.memberships ?? []).filter((m) => m.is_active);
  const createMutation = useCreateTaskApiTasksPost();
  const createFlight = useSingleFlight();
  const dutySchema = useMemo(() => makeDutySchema(t), [t]);
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<DutyValues>({
    resolver: zodResolver(dutySchema),
    defaultValues: dutyDefaults(),
  });
  const wAssignedRoleId = useWatch({ control, name: "assigned_role_id" });
  const wAssignedUserId = useWatch({ control, name: "assigned_user_id" });
  const wCategory = useWatch({ control, name: "category" });
  const wAnimalId = useWatch({ control, name: "animal_id" });
  // Assignment is optional (both selects default to NONE and submit as null),
  // so the team directory's load state only matters once the user has actually
  // chosen a role/worker from it. Blocking every submit on a failing /api/team
  // would stop a team.manage holder from creating the same unassigned duty a
  // less-privileged user (who never fetches the team) creates freely.
  const assignmentChosen =
    (wAssignedRoleId || NONE) !== NONE || (wAssignedUserId || NONE) !== NONE;

  async function createDuty(values: DutyValues) {
    // Stryker disable next-line CallExpression: the New-duty button clears createError on every open, so the submit-start clear is unobservable within a dialog session
    setCreateError(null);
    const farmScope = captureFarmScope();
    try {
      await createMutation.mutateAsync({
        data: {
          // Stryker disable next-line MethodExpression: the zod schema's .trim() already normalized the title before handleSubmit delivers values, so the redundant .trim() cannot change the payload
          title: values.title.trim(),
          due_date: values.due_date,
          category: values.category,
          animal_id: noneToNull(values.animal_id),
          recur_days: values.recur_days ? Number(values.recur_days) : null,
          assigned_role_id: noneToNull(values.assigned_role_id),
          assigned_user_id: noneToNull(values.assigned_user_id),
        },
      });
      if (!farmScope()) return;
      toast.success(t("tasks.toast.created"));
      invalidateFarmData(queryClient);
      setOpen(false);
      // Stryker disable next-line CallExpression: shouldUnregister drops every field when the dialog unmounts, so the explicit reset is redundant with the remount defaults (pinned by the reopen-blank test passing under the mutant)
      reset(dutyDefaults());
    } catch (err) {
      if (!farmScope()) return;
      const message = mutationError(err, t("common.somethingWentWrong"));
      setCreateError(message);
      toast.error(message);
    }
  }

  async function onSubmit(values: DutyValues) {
    await createFlight.run(() => createDuty(values));
  }

  function changeTab(nextTab: TaskTab) {
    setNavigationOverride({ sourceParamsKey: paramsKey, tab: nextTab, offsets });
    router.replace(taskListUrl({ pathname, paramsKey, tab: nextTab, offsets }));
  }

  function changeOffset(taskTab: TaskTab, nextOffset: number) {
    const boundedOffset = Math.max(0, Math.min(MAX_TASK_OFFSET, Math.trunc(nextOffset)));
    if (boundedOffset === offsets[taskTab]) return;
    const nextOffsets = { ...offsets, [taskTab]: boundedOffset };
    setNavigationOverride({ sourceParamsKey: paramsKey, tab: taskTab, offsets: nextOffsets });
    router.push(taskListUrl({ pathname, paramsKey, tab: taskTab, offsets: nextOffsets }));
  }

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3 rounded-lg border border-destructive/40 p-4">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : t("tasks.loadFailed")}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            {t("tasks.retryTasks")}
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader title={t("tasks.title")} description={t("tasks.description")} />
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("tasks.loadingTasks")}</span>
          <PageSkeleton cards={2} />
        </div>
      </div>
    );
  }

  // Comparisons use the active farm's calendar day, not the browser's local
  // date (avoids the IST 00:00–05:30 off-by-one window).
  const today = farmToday();
  const visibleTabs: {
    value: TaskTab;
    label: string;
    paginationLabel: string;
    tasks: TaskOut[];
    total: number;
    limit: number;
    offset: number;
  }[] = [
    {
      value: "today",
      label: `${t("tasks.tab.today")} (${payload.today_total})`,
      paginationLabel: t("tasks.pagination.today"),
      tasks: payload.today,
      total: payload.today_total,
      limit: payload.active_limit,
      offset: payload.today_offset,
    },
    {
      value: "overdue",
      label: `${t("tasks.tab.overdue")} (${payload.overdue_total})`,
      paginationLabel: t("tasks.pagination.overdue"),
      tasks: payload.overdue,
      total: payload.overdue_total,
      limit: payload.active_limit,
      offset: payload.overdue_offset,
    },
    {
      value: "upcoming",
      label: `${t("tasks.tab.upcoming")} (${payload.upcoming_total})`,
      paginationLabel: t("tasks.pagination.upcoming"),
      tasks: payload.upcoming,
      total: payload.upcoming_total,
      limit: payload.active_limit,
      offset: payload.upcoming_offset,
    },
  ];
  if (canVerify) {
    visibleTabs.push({
      value: "awaiting",
      label: `${t("tasks.tab.awaiting")} (${payload.awaiting_total})`,
      paginationLabel: t("tasks.pagination.awaiting"),
      tasks: payload.awaiting,
      total: payload.awaiting_total,
      limit: payload.active_limit,
      offset: payload.awaiting_offset,
    });
  }
  // The API already scopes this history to the current worker. Verification
  // permission controls the review queue/actions, not whether someone may
  // review duties they personally completed or skipped.
  visibleTabs.push({
    value: "completed",
    label: `${t("tasks.tab.completed")} (${payload.completed_total})`,
    paginationLabel: t("tasks.pagination.completed"),
    tasks: payload.completed,
    total: payload.completed_total,
    limit: payload.completed_limit,
    offset: payload.completed_offset,
  });
  // A deep-linked tab may not exist for this user (e.g. ?tab=awaiting
  // without tasks.verify) — fall back to "today".
  const activeTab = visibleTabs.some((tabItem) => tabItem.value === tab) ? tab : "today";
  const boardSettling = query.isPlaceholderData;

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title={t("tasks.title")}
        description={t("tasks.description")}
        actions={
          canCreate && (
            <Button
              onClick={() => {
                reset(dutyDefaults());
                // Stryker disable next-line CallExpression: every dismissal path already clears the banner, so the open-time clear is redundant
                setCreateError(null);
                setOpen(true);
              }}
            >
              <Plus /> {t("tasks.newDuty")}
            </Button>
          )
        }
      />

      {boardSettling && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("tasks.updatingBoard")}
        </p>
      )}
      <Tabs value={activeTab} onValueChange={(v) => changeTab(v as TaskTab)}>
        <TabsList className="max-w-full justify-start overflow-x-auto">
          {visibleTabs.map((tabItem) => (
            <TabsTrigger key={tabItem.value} value={tabItem.value}>
              {tabItem.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {visibleTabs.map((tabItem) => (
          <TabsContent key={tabItem.value} value={tabItem.value} className="pt-4">
            <TaskTable
              tasks={tabItem.tasks}
              tab={tabItem.value}
              returnTo={taskListUrl({
                pathname,
                paramsKey,
                tab: tabItem.value,
                offsets,
              })}
              canComplete={canComplete}
              canVerify={canVerify}
              can={can}
              canViewAnimals={canViewAnimals}
              today={today}
              // Stryker disable next-line OptionalChaining: the board renders only after the auth bootstrap resolved a user (the farm-scoped queries require one), so user is non-null here
              currentUserId={user?.id ?? null}
              isOwner={isOwner}
              // Stryker disable next-line ArrowFunction: returning undefined instead of null is indistinguishable — the only consumer gates on truthiness
              resolveMemberName={canSeeTeam ? resolveMemberName : () => null}
            />
            <PaginationControls
              total={tabItem.total}
              limit={tabItem.limit}
              offset={tabItem.offset}
              onOffsetChange={(nextOffset) => changeOffset(tabItem.value, nextOffset)}
              label={tabItem.paginationLabel}
              disabled={boardSettling}
            />
          </TabsContent>
        ))}
      </Tabs>

      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          // The continuation closes and resets this form. Letting Escape or
          // the backdrop dismiss it mid-write allowed a fresh dialog session
          // to open and then be wiped by that late completion.
          if (!nextOpen && (isSubmitting || createFlight.pending)) return;
          setOpen(nextOpen);
          // Stryker disable next-line BooleanLiteral, ConditionalExpression, CallExpression: the New-duty button clears createError on every open, so skipping the close-time clear is unobservable
          if (!nextOpen) setCreateError(null);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("tasks.newDuty")}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{t("tasks.form.intro")}</p>
          <form onSubmit={handleSubmit(onSubmit)} noValidate>
            <fieldset
              disabled={isSubmitting || createFlight.pending}
              className="space-y-4"
            >
            <div className="space-y-1.5">
              <Label htmlFor="title">{t("tasks.form.titleLabel")}</Label>
              <Input
                id="title"
                  maxLength={MAX_TASK_TITLE_LENGTH}
                  placeholder={t("tasks.form.titlePlaceholder")}
                  aria-invalid={Boolean(errors.title) || undefined}
                  aria-describedby={errors.title ? "duty-title-error" : undefined}
                  {...register("title")}
                />
                {errors.title && (
                  <p id="duty-title-error" role="alert" className="text-sm text-destructive">
                    {errors.title.message}
                  </p>
                )}
              </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="due_date">{t("tasks.form.dueDateLabel")}</Label>
                <Input
                  id="due_date"
                  type="date"
                  aria-invalid={Boolean(errors.due_date) || undefined}
                  aria-describedby={errors.due_date ? "duty-due-date-error" : undefined}
                  {...register("due_date")}
                />
                {errors.due_date && (
                  <p
                    id="duty-due-date-error"
                    role="alert"
                    className="text-sm text-destructive"
                  >
                    {errors.due_date.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="duty-category">{t("tasks.form.categoryLabel")}</Label>
                <Select
                  value={wCategory}
                  onValueChange={(v) =>
                    // Stryker disable next-line ObjectLiteral, BooleanLiteral: the category select only ever sets valid enum values, so shouldValidate never surfaces a different error state
                    setValue("category", v as DutyValues["category"], { shouldValidate: true })
                  }
                  items={categoryItems}
                >
                  <SelectTrigger id="duty-category" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CATEGORIES.map((c) => (
                      <SelectItem key={c} value={c}>
                        {enumLabel("taskCategory", c, language)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="recur_days">{t("tasks.form.recurLabel")}</Label>
                <Input
                  id="recur_days"
                  inputMode="numeric"
                  placeholder={t("tasks.form.recurPlaceholder")}
                  aria-invalid={Boolean(errors.recur_days) || undefined}
                  aria-describedby={errors.recur_days ? "duty-recur-days-error" : undefined}
                  {...register("recur_days")}
                />
                {errors.recur_days && (
                  <p
                    id="duty-recur-days-error"
                    role="alert"
                    className="text-sm text-destructive"
                  >
                    {errors.recur_days.message}
                  </p>
                )}
              </div>
              {canViewAnimals ? (
                <div className="space-y-1.5 sm:col-span-2">
                  <Label htmlFor="duty-animal">{t("tasks.form.animalLabel")}</Label>
                  <AnimalPicker
                    id="duty-animal"
                    value={wAnimalId || NONE}
                    onValueChange={(value) => setValue("animal_id", value)}
                    placeholder={t("tasks.form.animalPlaceholder")}
                    dialogTitle={t("tasks.form.animalDialogTitle")}
                    labelVariant="name-dash"
                    staticOptions={[{ value: NONE, label: t("common.none") }]}
                  />
                  <p className="text-xs text-muted-foreground">
                    {t("tasks.form.animalHelp")}
                  </p>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground sm:col-span-2">
                  {t("tasks.form.noAnimalAccess")}
                </p>
              )}
            </div>
            {canSeeTeam ? (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {t("tasks.form.assignment")}
                </p>
                {teamQuery.isLoading && (
                  <InlineLoading>{t("tasks.form.loadingAssignments")}</InlineLoading>
                )}
                {teamQuery.isError && (
                  <div
                    role="alert"
                    className="space-y-2 rounded-lg border border-destructive/40 p-3 text-sm"
                  >
                    <p className="text-destructive">
                      {teamQuery.error instanceof ApiError
                        ? teamQuery.error.detail
                        : t("tasks.form.assignmentsFailed")}
                    </p>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => void teamQuery.refetch()}
                    >
                      {t("tasks.form.retryAssignments")}
                    </Button>
                  </div>
                )}
                {team && !teamQuery.isError && (
                  <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="duty-role">{t("tasks.form.assignToRole")}</Label>
                    <Select
                      value={wAssignedRoleId || NONE}
                      onValueChange={(v) => {
                        setValue("assigned_role_id", v);
                        if (v !== NONE) setValue("assigned_user_id", NONE);
                      }}
                      items={roleItems}
                    >
                      <SelectTrigger id="duty-role" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>{t("common.none")}</SelectItem>
                        {roleSelectOptions.map((r) => (
                          <SelectItem key={r.id} value={String(r.id)}>
                            {r.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="duty-worker">{t("tasks.form.assignToWorker")}</Label>
                    <Select
                      value={wAssignedUserId || NONE}
                      onValueChange={(v) => {
                        setValue("assigned_user_id", v);
                        if (v !== NONE) setValue("assigned_role_id", NONE);
                      }}
                      items={workerItems}
                    >
                      <SelectTrigger id="duty-worker" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>{t("common.none")}</SelectItem>
                        {workerSelectOptions.map((m) => (
                          <SelectItem key={m.id} value={String(m.user_id)}>
                            {m.name ?? m.email} (
                            {m.role_name ?? t("tasks.form.workerFallback")})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {t("tasks.form.noTeamAccess")}
              </p>
            )}
            {createError && (
              <p role="alert" className="text-sm text-destructive">
                {createError} {t("tasks.form.createErrorSuffix")}
              </p>
            )}
            <DialogFooter>
              <Button
                type="submit"
                disabled={
                  isSubmitting ||
                  createFlight.pending ||
                  (canSeeTeam &&
                    assignmentChosen &&
                    (teamQuery.isLoading || teamQuery.isError))
                }
              >
                {isSubmitting || createFlight.pending
                  ? t("tasks.form.creating")
                  : createError
                    ? t("tasks.form.retryCreate")
                    : t("tasks.form.create")}
              </Button>
            </DialogFooter>
            </fieldset>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Suspense boundary required because the content reads useSearchParams(). */
export default function TasksPage() {
  const perms = usePermissions();
  const t = useT();
  return (
    <Suspense
      fallback={
        <div className="space-y-6" role="status" aria-live="polite">
          {/* 2026-09-17 audit (M-12): the fallback hardcoded the English page
           * chrome; the boundary already has `t` in scope, so reuse the exact
           * keys the loaded header renders. */}
          <PageHeader title={t("tasks.title")} description={t("tasks.description")} />
          <span className="sr-only">{t("tasks.loadingTasks")}</span>
          <PageSkeleton cards={2} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="tasks.view"
        label={t("tasks.title")}
        description={t("tasks.description")}
        cards={2}
        noAccessMessage={t("tasks.noAccess")}
      >
        <TasksPageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
