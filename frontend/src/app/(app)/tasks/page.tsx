"use client";

/** Tasks/duties board — parity with v1's tasks/list.html + tasks/new.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ListChecks, Plus } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
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
import { ApiError } from "@/lib/api-client";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { MAX_TASK_TITLE_LENGTH, MAX_RECUR_DAYS } from "@/lib/backend-caps";
import { useAuth } from "@/lib/auth-context";
import { addDays, farmToday, formatDate, formatFarmDateTime } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { withReturnTo } from "@/lib/permission-navigation";
import {
  permittedTaskActionPath,
  taskFormNotDueYet,
  taskSkipUnavailable,
  type PermissionCheck,
} from "@/lib/task-action-access";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { safeAppPath } from "@/lib/utils";
import { PermissionsError } from "@/components/permissions-error";
import { enumLabel } from "@/lib/enum-labels";

const CATEGORIES = Object.values(TaskCreateInCategory);
/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
const CATEGORY_ITEMS: Record<string, string> = Object.fromEntries(
  CATEGORIES.map((c) => [c, enumLabel("taskCategory", c)]),
);
/** Sentinel for "no selection" in optional selects (empty string is not a valid item value). */
const NONE = "none";
/** Tabs addressable via /tasks?tab=… deep links (dashboard links here). */
const TASK_TABS = ["today", "overdue", "upcoming", "awaiting", "completed"] as const;
type TaskTab = (typeof TASK_TABS)[number];
type TaskOffsets = Record<TaskTab, number>;
type TaskOffsetKey = `${TaskTab}_offset`;

const VALID_TABS = new Set<string>(TASK_TABS);
/** Full-phrase empty-state titles ("No today tasks" reads wrong). */
const TASK_EMPTY_TITLE: Record<TaskTab, string> = {
  today: "No tasks for today.",
  overdue: "No overdue tasks.",
  upcoming: "No upcoming tasks.",
  awaiting: "No tasks awaiting verification.",
  completed: "No completed tasks.",
};
/** Per-tab guidance for an empty board column: point at the sibling tab that
 * actually carries work instead of dead-ending (no fake CTA — the tabs are
 * one click away). */
const TAB_EMPTY_GUIDANCE: Record<TaskTab, string> = {
  today: "Nothing is due today. Late work shows on the Overdue tab.",
  overdue: "Nothing is overdue. Today's duties show on the Today tab.",
  upcoming:
    "No duties scheduled ahead. Overdue and today's work appear on their own tabs as duties are spawned.",
  awaiting:
    "Nothing awaits verification. Duties land here for review once they are completed.",
  completed: "No completed or skipped duties yet. Work you finish will land here.",
};
const TASK_PAGE_SIZE = 50;
const MAX_TASK_OFFSET = 1_000_000;
const TASK_OFFSET_KEYS: Record<TaskTab, TaskOffsetKey> = {
  today: "today_offset",
  overdue: "overdue_offset",
  upcoming: "upcoming_offset",
  awaiting: "awaiting_offset",
  completed: "completed_offset",
};

function validTaskTab(value: string | null): value is TaskTab {
  return value !== null && VALID_TABS.has(value);
}

function parseTaskOffset(raw: string | null): number {
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

function localToday(): string {
  return farmToday();
}

/** Whole days from `from` to `to` (both YYYY-MM-DD), timezone-safe. */
function daysBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 86400000);
}

function mutationError(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

/** Complete / Skip / Open-form / Verify / Reject controls for one row. */
function RowActions({
  task,
  tab,
  returnTo,
  canComplete,
  canVerify,
  can,
  today,
}: {
  task: TaskOut;
  tab: string;
  returnTo: string;
  canComplete: boolean;
  canVerify: boolean;
  can: PermissionCheck;
  today: string;
}) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [skipOpen, setSkipOpen] = useState(false);
  const [skipReason, setSkipReason] = useState("");
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

  function invalidate() {
    invalidateFarmData(queryClient);
  }

  function reportActionError(
    action: "complete" | "skip" | "verify" | "reject",
    error: unknown,
  ) {
    const message = mutationError(error);
    setActionError({ action, message });
    toast.error(message);
  }

  async function completeTask() {
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      setActionError(null);
      try {
        await completeMutation.mutateAsync({ taskId: task.id });
        if (!farmScope()) return;
        toast.success("Task completed.");
        invalidate();
      } catch (error) {
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
          data: { reason: skipReason.trim() || null },
        });
        if (!farmScope()) return;
        toast.success("Task skipped.");
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
        toast.success("Task verified.");
        invalidate();
      } catch (error) {
        if (farmScope()) reportActionError("verify", error);
      }
    });
  }

  async function rejectTask() {
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      setActionError(null);
      try {
        await rejectMutation.mutateAsync({
          taskId: task.id,
          data: { note: note.trim() || null },
        });
        if (!farmScope()) return;
        toast.success("Task sent back.");
        setNote("");
        invalidate();
      } catch (error) {
        if (farmScope()) reportActionError("reject", error);
      }
    });
  }

  if (task.status === "PENDING" && canComplete) {
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
                Not due yet — the linked form opens on the due date.
              </span>
            ) : (
              <Link
                href={withReturnTo(permittedAction, returnTo)}
                className={buttonVariants({ variant: "outline", size: "sm" })}
              >
                Open form
              </Link>
            )
          ) : safeAction ? (
            <span className="text-xs text-muted-foreground">
              Linked form unavailable with your permissions.
            </span>
          ) : lockedFutureCompletion || lockedFutureRecurrence ? (
            // A future-locked duty with no permitted linked form renders
            // nothing at all without this hint — an inert row the operator
            // cannot explain.
            <span className="text-xs text-muted-foreground">
              Not due yet — actions open on the due date.
            </span>
          ) : (
            <Button
              size="sm"
              variant="outline"
              disabled={actionFlight.pending}
              onClick={() => void completeTask()}
            >
              {actionError?.action === "complete" ? "Retry complete" : "Complete"}
            </Button>
          )}
          {/* Quarantine-gate and weaning duties always 409 a skip (see
              taskSkipUnavailable) — don't offer an action that cannot
              succeed; their Complete/Open-form workflow stays available. */}
          {!lockedFutureRecurrence && !taskSkipUnavailable(task) && (
            <Button
              size="sm"
              variant="outline"
              disabled={actionFlight.pending}
              onClick={() => setSkipOpen(true)}
            >
              Skip
            </Button>
          )}
        </div>
        {actionError?.action === "complete" && (
          <p role="alert" className="text-sm text-destructive">
            {actionError.message} Review the duty, then try again.
          </p>
        )}
        <Dialog
          open={skipOpen}
          onOpenChange={(nextOpen) => {
            if (!nextOpen && actionFlight.pending) return;
            setSkipOpen(nextOpen);
          }}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Skip this task?</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">
              Skipping moves this duty to its audit history. Add a reason so the team can
              understand why it was not completed.
            </p>
            <div className="space-y-1.5">
              <Label htmlFor={`skip-reason-${task.id}`}>Reason (optional)</Label>
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
                {actionError.message} Check the reason, then try again.
              </p>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={actionFlight.pending}
                onClick={() => setSkipOpen(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={actionFlight.pending}
                onClick={() => void skipTask()}
              >
                {actionFlight.pending
                  ? "Skipping…"
                  : actionError?.action === "skip"
                    ? "Retry skip"
                    : "Skip task"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </>
    );
  }

  if (tab === "awaiting" && canVerify) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={actionFlight.pending}
          onClick={() => void verifyTask()}
        >
          {actionError?.action === "verify" ? "Retry verify" : "Verify"}
        </Button>
        <Input
          disabled={actionFlight.pending}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="reason (sent back)"
          aria-label="Rejection reason"
          className="h-7 w-44"
          maxLength={255}
        />
        <Button
          size="sm"
          variant="destructive"
          disabled={actionFlight.pending}
          onClick={() => void rejectTask()}
        >
          {actionError?.action === "reject" ? "Retry reject" : "Reject"}
        </Button>
        {actionError && (
          <p role="alert" className="basis-full text-sm text-destructive">
            {actionError.message} Review the duty, then try again.
          </p>
        )}
      </div>
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
}) {
  if (tasks.length === 0) {
    return (
      <EmptyState
        icon={ListChecks}
        title={validTaskTab(tab) ? TASK_EMPTY_TITLE[tab] : "No tasks yet."}
        description={
          validTaskTab(tab)
            ? TAB_EMPTY_GUIDANCE[tab]
            : "New duties and auto-generated protocol tasks will show up here."
        }
      />
    );
  }
  const completedTab = tab === "completed";
  return (
    <DataTableCard>
      {/* 6–8 columns: keep a floor so the board rows don't crush on tablets;
       * the surrounding rows are already responsive down to phones. */}
      <Table className="min-w-[720px]">
        <TableHeader>
          <TableRow>
            <TableHead>Due</TableHead>
            <TableHead>Task</TableHead>
            <TableHead>Category</TableHead>
            <TableHead>Assigned to</TableHead>
            <TableHead>Animal</TableHead>
            {completedTab && <TableHead>Status</TableHead>}
            {completedTab && <TableHead>Finished</TableHead>}
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {tasks.map((t) => {
            const overdue = t.status === "PENDING" && t.due_date < today;
            const dueSoon =
              t.status === "PENDING" && !overdue && daysBetween(today, t.due_date) <= 2;
            return (
              <TableRow key={t.id}>
                <TableCell>
                  {(overdue || dueSoon) && (
                    <Badge variant={overdue ? "destructive" : "warning"}>
                      {formatDate(t.due_date)}
                    </Badge>
                  )}
                  {!(overdue || dueSoon) && (
                    <span>{formatDate(t.due_date)}</span>
                  )}
                  {overdue && (
                    <span className="text-destructive">
                      {" "}
                      ({daysBetween(t.due_date, today)}d late)
                    </span>
                  )}
                </TableCell>
                <TableCell>
                  {t.title}
                  {t.recur_days !== null && (
                    <Badge variant="secondary" className="ml-2">
                      every {t.recur_days}d
                    </Badge>
                  )}
                  {t.status === "PENDING" && t.verification_note && (
                    <>
                      <br />
                      <span className="text-destructive">Sent back: {t.verification_note}</span>
                    </>
                  )}
                  {completedTab && t.status === "SKIPPED" && t.skip_reason && (
                    <>
                      <br />
                      <span className="text-xs text-muted-foreground">
                        Reason: {t.skip_reason}
                      </span>
                    </>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">{enumLabel("taskCategory", t.category)}</Badge>
                </TableCell>
                <TableCell>
                  {/* A personal assignment always carries a continuity role as
                      well (tasks.py `effective_role_id`), so the named worker
                      must win — otherwise every personal duty is displayed as
                      if it were assigned to the whole role. The role stays
                      visible as a sub-label. */}
                  {t.assigned_user_name ? (
                    <>
                      {t.assigned_user_name}
                      {t.assigned_role_name && (
                        <>
                          <br />
                          <span className="text-xs text-muted-foreground">
                            via {t.assigned_role_name}
                          </span>
                        </>
                      )}
                    </>
                  ) : (
                    t.assigned_role_name ?? "—"
                  )}
                </TableCell>
                <TableCell>
                  {t.animal_tag && t.animal_id && canViewAnimals ? (
                    <Link
                      href={withReturnTo(`/animals/${t.animal_id}`, returnTo)}
                      className="text-primary underline"
                    >
                      {t.animal_tag}
                    </Link>
                  ) : t.animal_tag && t.animal_id ? (
                    t.animal_tag
                  ) : (
                    "—"
                  )}
                </TableCell>
                {completedTab && (
                  <TableCell>
                    <StatusBadge status={t.status}>{t.status}</StatusBadge>
                    {t.status !== "VERIFIED" && t.needs_verification && (
                      <span className="ml-1 text-xs text-muted-foreground">awaiting</span>
                    )}
                  </TableCell>
                )}
                {completedTab && (
                  <TableCell>
                    {formatFarmDateTime(t.status === "SKIPPED" ? t.skipped_at : t.completed_at)}
                  </TableCell>
                )}
                <TableCell>
                  <RowActions
                    task={t}
                    tab={tab}
                    returnTo={returnTo}
                    canComplete={canComplete}
                    can={can}
                    canVerify={
                      // The API 409s self-verification for non-owners — don't
                      // offer the action.
                      canVerify && (isOwner || t.completed_by_id !== currentUserId)
                    }
                    today={today}
                  />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </DataTableCard>
  );
}

const dutySchema = z
  .object({
    title: z.string().trim().min(1, "Title is required").max(MAX_TASK_TITLE_LENGTH),
    due_date: z
      .string()
      .min(1, "Due date is required")
      .regex(/^\d{4}-\d{2}-\d{2}$/, "Pick a valid due date")
      // Mirror the backend's year band so impossible dates fail inline
      // instead of as a 422 after submit.
      .refine((v) => {
        const year = Number(v.slice(0, 4));
        return year >= 2000 && year <= 2100;
      }, "Year must be between 2000 and 2100"),
    category: z.enum(["FEED", "CLEANING", "OTHER"]),
    recur_days: z
      .string()
      .refine(
        (s) =>
          s === "" ||
          (/^\d+$/.test(s) && Number(s) >= 1 && Number(s) <= MAX_RECUR_DAYS),
        `Must be a whole number of days (1–${MAX_RECUR_DAYS})`,
      )
      .optional(),
    assigned_role_id: z.string().optional(),
    assigned_user_id: z.string().optional(),
    animal_id: z.string().optional(),
  })
  .superRefine((values, ctx) => {
    if (!values.recur_days || !/^\d+$/.test(values.recur_days)) return;
    const recurrenceDays = Number(values.recur_days);
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
        message: "Recurring due date is too late to schedule its next occurrence",
      });
    }
  });
type DutyValues = z.infer<typeof dutySchema>;

/** Rebuilt on every reset: a bare reset() restores react-hook-form's
 * mount-time snapshot, which dates duties to the day the tab was opened. */
function dutyDefaults(): DutyValues {
  return {
    title: "",
    due_date: localToday(),
    category: "OTHER",
    recur_days: "",
    assigned_role_id: NONE,
    assigned_user_id: NONE,
    animal_id: NONE,
  };
}

function TasksPageContent() {
  const { can, loading: permsLoading, isError: permsError, isOwner , refetch: permsRefetch } = usePermissions();
  const { user } = useAuth();
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
  const paramsOffsets = taskOffsetsFromParams(new URLSearchParams(paramsKey));
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
    return () => window.clearTimeout(handle);
  }, [offsets, paramsKey, pathname, payload, router, tab]);

  // A deep-linked tab the user cannot view (e.g. ?tab=awaiting without
  // tasks.verify) falls back visually to "today"; rewrite the URL to the tab
  // actually shown so reloads and shared links agree with the screen.
  useEffect(() => {
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
  const team = teamQuery.data?.status === 200 ? teamQuery.data.data : undefined;
  /** value → label maps for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const roleItems: Record<string, string> = {
    [NONE]: "— none —",
    ...Object.fromEntries((team?.roles ?? []).map((r) => [String(r.id), r.name])),
  };
  const workerItems: Record<string, string> = {
    [NONE]: "— none —",
    ...Object.fromEntries(
      (team?.memberships ?? [])
        .filter((m) => m.is_active)
        .map((m) => [String(m.user_id), `${m.name ?? m.email} (${m.role_name ?? "worker"})`]),
    ),
  };
  const createMutation = useCreateTaskApiTasksPost();
  const createFlight = useSingleFlight();
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
    setCreateError(null);
    const farmScope = captureFarmScope();
    try {
      await createMutation.mutateAsync({
        data: {
          title: values.title.trim(),
          due_date: values.due_date,
          category: values.category,
          animal_id:
            values.animal_id && values.animal_id !== NONE ? Number(values.animal_id) : null,
          recur_days: values.recur_days ? Number(values.recur_days) : null,
          assigned_role_id:
            values.assigned_role_id && values.assigned_role_id !== NONE
              ? Number(values.assigned_role_id)
              : null,
          assigned_user_id:
            values.assigned_user_id && values.assigned_user_id !== NONE
              ? Number(values.assigned_user_id)
              : null,
        },
      });
      if (!farmScope()) return;
      toast.success("Duty created.");
      invalidateFarmData(queryClient);
      setOpen(false);
      reset(dutyDefaults());
    } catch (err) {
      if (!farmScope()) return;
      const message = mutationError(err);
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

  // The header and page structure stay mounted while the permission set
  // settles — a page that collapses to a bare "Loading…" line reads as a
  // broken app on slow rural connections.
  if (permsLoading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Tasks"
          description="Duties and auto-generated protocol tasks, grouped by when they're due."
        />
        <PageSkeleton cards={2} />
      </div>
    );
  }
  if (permsError) {
    return (
      <PermissionsError onRetry={() => void permsRefetch()} />
    );
  }
  if (!allowed) {
    return <p className="text-muted-foreground">You don&apos;t have access to this page.</p>;
  }
  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3 rounded-lg border border-destructive/40 p-4">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : "Could not load tasks."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry tasks
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title="Tasks"
          description="Duties and auto-generated protocol tasks, grouped by when they're due."
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading tasks…</span>
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
      label: `Today (${payload.today_total})`,
      paginationLabel: "today tasks",
      tasks: payload.today,
      total: payload.today_total,
      limit: payload.active_limit,
      offset: payload.today_offset,
    },
    {
      value: "overdue",
      label: `Overdue (${payload.overdue_total})`,
      paginationLabel: "overdue tasks",
      tasks: payload.overdue,
      total: payload.overdue_total,
      limit: payload.active_limit,
      offset: payload.overdue_offset,
    },
    {
      value: "upcoming",
      label: `Upcoming (${payload.upcoming_total})`,
      paginationLabel: "upcoming tasks",
      tasks: payload.upcoming,
      total: payload.upcoming_total,
      limit: payload.active_limit,
      offset: payload.upcoming_offset,
    },
  ];
  if (canVerify) {
    visibleTabs.push({
      value: "awaiting",
      label: `Awaiting verification (${payload.awaiting_total})`,
      paginationLabel: "awaiting verification tasks",
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
    label: `Completed (${payload.completed_total})`,
    paginationLabel: "completed tasks",
    tasks: payload.completed,
    total: payload.completed_total,
    limit: payload.completed_limit,
    offset: payload.completed_offset,
  });
  // A deep-linked tab may not exist for this user (e.g. ?tab=awaiting
  // without tasks.verify) — fall back to "today".
  const activeTab = visibleTabs.some((t) => t.value === tab) ? tab : "today";
  const boardSettling = query.isPlaceholderData;

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title="Tasks"
        description="Duties and auto-generated protocol tasks, grouped by when they're due."
        actions={
          canCreate && (
            <Button
              onClick={() => {
                reset(dutyDefaults());
                setCreateError(null);
                setOpen(true);
              }}
            >
              <Plus /> New duty
            </Button>
          )
        }
      />

      {boardSettling && (
        <p role="status" className="text-sm text-muted-foreground">
          Updating the duty board…
        </p>
      )}
      <Tabs value={activeTab} onValueChange={(v) => changeTab(v as TaskTab)}>
        <TabsList className="max-w-full justify-start overflow-x-auto">
          {visibleTabs.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {visibleTabs.map((t) => (
          <TabsContent key={t.value} value={t.value} className="pt-4">
            <TaskTable
              tasks={t.tasks}
              tab={t.value}
              returnTo={taskListUrl({
                pathname,
                paramsKey,
                tab: t.value,
                offsets,
              })}
              canComplete={canComplete}
              canVerify={canVerify}
              can={can}
              canViewAnimals={canViewAnimals}
              today={today}
              currentUserId={user?.id ?? null}
              isOwner={isOwner}
            />
            <PaginationControls
              total={t.total}
              limit={t.limit}
              offset={t.offset}
              onOffsetChange={(nextOffset) => changeOffset(t.value, nextOffset)}
              label={t.paginationLabel}
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
          if (!nextOpen) setCreateError(null);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>New duty</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Assign to a role (everyone with that role sees it) or to one specific worker. Set
            &quot;repeats every&quot; for recurring duties like daily cleaning — completing one
            schedules the next.
          </p>
          <form onSubmit={handleSubmit(onSubmit)} noValidate>
            <fieldset
              disabled={isSubmitting || createFlight.pending}
              className="space-y-4"
            >
            <div className="space-y-1.5">
              <Label htmlFor="title">Title *</Label>
              <Input
                id="title"
                  maxLength={MAX_TASK_TITLE_LENGTH}
                  placeholder="e.g. Clean water troughs in BREEDING pen"
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
                <Label htmlFor="due_date">Due date *</Label>
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
                <Label htmlFor="duty-category">Category</Label>
                <Select
                  value={wCategory}
                  onValueChange={(v) =>
                    setValue("category", v as DutyValues["category"], { shouldValidate: true })
                  }
                  items={CATEGORY_ITEMS}
                >
                  <SelectTrigger id="duty-category" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CATEGORIES.map((c) => (
                      <SelectItem key={c} value={c}>
                        {enumLabel("taskCategory", c)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="recur_days">Repeats every (days)</Label>
                <Input
                  id="recur_days"
                  inputMode="numeric"
                  placeholder="blank = one-off"
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
                <Label htmlFor="duty-animal">Animal (optional)</Label>
                <AnimalPicker
                  id="duty-animal"
                  value={wAnimalId || NONE}
                  onValueChange={(value) => setValue("animal_id", value)}
                  placeholder="No animal"
                  dialogTitle="Choose an animal for this duty"
                  labelVariant="name-dash"
                  staticOptions={[{ value: NONE, label: "— none —" }]}
                />
                <p className="text-xs text-muted-foreground">
                  Link vaccine or deworming duties to an animal to open the matching health
                  form. Unlinked duties remain ordinary checklists.
                </p>
              </div>
              ) : (
                <p className="text-sm text-muted-foreground sm:col-span-2">
                  You don&apos;t have animal access, so this duty will be created without an
                  animal link.
                </p>
              )}
            </div>
            {canSeeTeam ? (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Assignment
                </p>
                {teamQuery.isLoading && (
                  <InlineLoading>Loading assignment options…</InlineLoading>
                )}
                {teamQuery.isError && (
                  <div
                    role="alert"
                    className="space-y-2 rounded-lg border border-destructive/40 p-3 text-sm"
                  >
                    <p className="text-destructive">
                      {teamQuery.error instanceof ApiError
                        ? teamQuery.error.detail
                        : "Could not load assignment options."}
                    </p>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => void teamQuery.refetch()}
                    >
                      Retry assignments
                    </Button>
                  </div>
                )}
                {team && !teamQuery.isError && (
                  <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="duty-role">Assign to role</Label>
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
                        <SelectItem value={NONE}>— none —</SelectItem>
                        {(team?.roles ?? []).map((r) => (
                          <SelectItem key={r.id} value={String(r.id)}>
                            {r.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="duty-worker">or assign to worker</Label>
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
                        <SelectItem value={NONE}>— none —</SelectItem>
                        {(team?.memberships ?? [])
                          .filter((m) => m.is_active)
                          .map((m) => (
                            <SelectItem key={m.id} value={String(m.user_id)}>
                              {m.name ?? m.email} ({m.role_name ?? "worker"})
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
                You don&apos;t have team access — the duty will be created unassigned.
              </p>
            )}
            {createError && (
              <p role="alert" className="text-sm text-destructive">
                {createError} Check the duty details, then try again.
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
                  ? "Creating…"
                  : createError
                    ? "Retry create"
                    : "Create duty"}
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
  return (
    <Suspense
      fallback={
        <div className="space-y-6" role="status" aria-live="polite">
          <PageHeader
            title="Tasks"
            description="Duties and auto-generated protocol tasks, grouped by when they're due."
          />
          <span className="sr-only">Loading tasks…</span>
          <PageSkeleton cards={2} />
        </div>
      }
    >
      <TasksPageContent />
    </Suspense>
  );
}
