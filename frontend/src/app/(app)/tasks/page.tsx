"use client";

/** Tasks/duties board — parity with v1's tasks/list.html + tasks/new.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ListChecks, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useForm , useWatch} from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getListTasksApiTasksGetQueryKey,
  useCompleteApiTasksTaskIdCompletePost,
  useCreateTaskApiTasksPost,
  useListTasksApiTasksGet,
  useRejectApiTasksTaskIdRejectPost,
  useSkipApiTasksTaskIdSkipPost,
  useTeamPageApiTeamGet,
  useVerifyApiTasksTaskIdVerifyPost,
} from "@/api/generated/endpoints";
import { TaskCreateInCategory, type TaskOut } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
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
import { formatDate } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";
import { cn } from "@/lib/utils";

const CATEGORIES = Object.values(TaskCreateInCategory);
/** Sentinel for "no selection" in optional selects (empty string is not a valid item value). */
const NONE = "none";

function localToday(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

/** Whole days from `from` to `to` (both YYYY-MM-DD), timezone-safe. */
function daysBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 86400000);
}

/** Backend datetimes are naive UTC (no offset suffix) — parse as UTC, not
 *  browser-local, then format in the viewer's locale. */
function fmtDateTime(iso: string): string {
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasOffset ? iso : `${iso}Z`);
  return `${String(d.getDate()).padStart(2, "0")}-${String(d.getMonth() + 1).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function mutationError(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

/** Complete / Skip / Open-form / Verify / Reject controls for one row. */
function RowActions({
  task,
  tab,
  canComplete,
  canVerify,
  today,
}: {
  task: TaskOut;
  tab: string;
  canComplete: boolean;
  canVerify: boolean;
  today: string;
}) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const completeMutation = useCompleteApiTasksTaskIdCompletePost();
  const skipMutation = useSkipApiTasksTaskIdSkipPost();
  const verifyMutation = useVerifyApiTasksTaskIdVerifyPost();
  const rejectMutation = useRejectApiTasksTaskIdRejectPost();

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: getListTasksApiTasksGetQueryKey() });
  }

  if (task.status === "PENDING" && canComplete) {
    // Auto-generated duties unlock on their due date (backend 409 otherwise).
    const lockedFutureAuto = task.auto_generated && task.due_date > today;
    return (
      <div className="flex flex-wrap items-center gap-2">
        {task.action_url ? (
          <Link href={task.action_url} className={buttonVariants({ size: "sm" })}>
            Open form
          </Link>
        ) : (
          !lockedFutureAuto && (
            <Button
              size="sm"
              disabled={completeMutation.isPending}
              onClick={() =>
                completeMutation
                  .mutateAsync({ taskId: task.id })
                  .then(() => {
                    toast.success("Task completed.");
                    invalidate();
                  })
                  .catch((err) => toast.error(mutationError(err)))
              }
            >
              Complete
            </Button>
          )
        )}
        <Button
          size="sm"
          variant="outline"
          disabled={skipMutation.isPending}
          onClick={() =>
            skipMutation
              .mutateAsync({ taskId: task.id })
              .then(() => {
                toast.success("Task skipped.");
                invalidate();
              })
              .catch((err) => toast.error(mutationError(err)))
          }
        >
          Skip
        </Button>
      </div>
    );
  }

  if (tab === "awaiting" && canVerify) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={verifyMutation.isPending}
          onClick={() =>
            verifyMutation
              .mutateAsync({ taskId: task.id })
              .then(() => {
                toast.success("Task verified.");
                invalidate();
              })
              .catch((err) => toast.error(mutationError(err)))
          }
        >
          Verify
        </Button>
        <Input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="reason (sent back)"
          className="h-7 w-44"
          maxLength={255}
        />
        <Button
          size="sm"
          variant="destructive"
          disabled={rejectMutation.isPending}
          onClick={() =>
            rejectMutation
              .mutateAsync({ taskId: task.id, data: { note: note.trim() || null } })
              .then(() => {
                toast.success("Task sent back.");
                setNote("");
                invalidate();
              })
              .catch((err) => toast.error(mutationError(err)))
          }
        >
          Reject
        </Button>
      </div>
    );
  }

  return null;
}

function TaskTable({
  tasks,
  tab,
  canComplete,
  canVerify,
  today,
}: {
  tasks: TaskOut[];
  tab: string;
  canComplete: boolean;
  canVerify: boolean;
  today: string;
}) {
  if (tasks.length === 0) {
    return (
      <EmptyState
        icon={ListChecks}
        title={`No ${tab} tasks.`}
        description="New duties and auto-generated protocol tasks will show up here."
      />
    );
  }
  const completedTab = tab === "completed";
  return (
    <DataTableCard>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Due</TableHead>
            <TableHead>Task</TableHead>
            <TableHead>Category</TableHead>
            <TableHead>Assigned to</TableHead>
            <TableHead>Animal</TableHead>
            {completedTab && <TableHead>Status</TableHead>}
            {completedTab && <TableHead>Completed</TableHead>}
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
                  <span
                    className={cn(
                      overdue &&
                        "rounded-md bg-red-100 px-1.5 py-0.5 text-red-700 dark:bg-red-950 dark:text-red-300",
                      dueSoon &&
                        "rounded-md bg-amber-100 px-1.5 py-0.5 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
                    )}
                  >
                    {formatDate(t.due_date)}
                  </span>
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
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">{t.category}</Badge>
                </TableCell>
                <TableCell>{t.assigned_role_name ?? t.assigned_user_name ?? "—"}</TableCell>
                <TableCell>
                  {t.animal_tag && t.animal_id ? (
                    <Link href={`/animals/${t.animal_id}`} className="text-primary underline">
                      {t.animal_tag}
                    </Link>
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
                  <TableCell>{t.completed_at ? fmtDateTime(t.completed_at) : "—"}</TableCell>
                )}
                <TableCell>
                  <RowActions
                    task={t}
                    tab={tab}
                    canComplete={canComplete}
                    canVerify={canVerify}
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

const dutySchema = z.object({
  title: z.string().trim().min(1, "Title is required").max(200),
  due_date: z.string().min(1, "Due date is required"),
  category: z.enum([
    "VACCINE",
    "DEWORMING",
    "ULTRASOUND",
    "KIDDING_DUE",
    "WEANING",
    "BUCKET_MOVE",
    "QUARANTINE",
    "FEED",
    "CLEANING",
    "OTHER",
  ]),
  recur_days: z
    .string()
    .refine(
      (s) => s === "" || (/^\d+$/.test(s) && Number(s) >= 1 && Number(s) <= 3650),
      "Must be a whole number of days (1–3650)",
    )
    .optional(),
  assigned_role_id: z.string().optional(),
  assigned_user_id: z.string().optional(),
});
type DutyValues = z.infer<typeof dutySchema>;

export default function TasksPage() {
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("tasks.view");
  const canCreate = can("tasks.create");
  const canComplete = can("tasks.complete");
  const canVerify = can("tasks.verify");
  const canSeeTeam = can("team.manage");
  const queryClient = useQueryClient();

  const [tab, setTab] = useState("today");
  const [open, setOpen] = useState(false);

  const query = useListTasksApiTasksGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

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
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<DutyValues>({
    resolver: zodResolver(dutySchema),
    defaultValues: {
      title: "",
      due_date: localToday(),
      category: "OTHER",
      recur_days: "",
      assigned_role_id: NONE,
      assigned_user_id: NONE,
    },
  });
  const wAssignedRoleId = useWatch({ control, name: "assigned_role_id" });
  const wAssignedUserId = useWatch({ control, name: "assigned_user_id" });
  const wCategory = useWatch({ control, name: "category" });

  async function onSubmit(values: DutyValues) {
    try {
      await createMutation.mutateAsync({
        data: {
          title: values.title.trim(),
          due_date: values.due_date,
          category: values.category,
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
      toast.success("Duty created.");
      queryClient.invalidateQueries({ queryKey: getListTasksApiTasksGetQueryKey() });
      setOpen(false);
      reset();
    } catch (err) {
      toast.error(mutationError(err));
    }
  }

  if (permsLoading) {
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }
  if (!allowed) {
    return <p className="text-muted-foreground">You don&apos;t have access to this page.</p>;
  }
  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError ? query.error.detail : "Could not load tasks."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  const today = localToday();
  const visibleTabs: { value: string; label: string; tasks: TaskOut[] }[] = [
    { value: "today", label: `Today (${payload.today.length})`, tasks: payload.today },
    { value: "overdue", label: `Overdue (${payload.overdue.length})`, tasks: payload.overdue },
    { value: "upcoming", label: `Upcoming (${payload.upcoming.length})`, tasks: payload.upcoming },
  ];
  if (canVerify) {
    visibleTabs.push({
      value: "awaiting",
      label: `Awaiting verification (${payload.awaiting.length})`,
      tasks: payload.awaiting,
    });
    visibleTabs.push({ value: "completed", label: "Completed", tasks: payload.completed });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tasks"
        description="Duties and auto-generated protocol tasks, grouped by when they're due."
        actions={
          canCreate && (
            <Button
              onClick={() => {
                reset();
                setOpen(true);
              }}
            >
              <Plus /> New duty
            </Button>
          )
        }
      />

      <Tabs value={tab} onValueChange={(v) => setTab(v as string)}>
        <TabsList>
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
              canComplete={canComplete}
              canVerify={canVerify}
              today={today}
            />
          </TabsContent>
        ))}
      </Tabs>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>New duty</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Assign to a role (everyone with that role sees it) or to one specific worker. Set
            &quot;repeats every&quot; for recurring duties like daily cleaning — completing one
            schedules the next.
          </p>
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="title">Title *</Label>
              <Input
                id="title"
                maxLength={200}
                placeholder="e.g. Clean water troughs in BREEDING pen"
                {...register("title")}
              />
              {errors.title && <p className="text-sm text-destructive">{errors.title.message}</p>}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="due_date">Due date *</Label>
                <Input id="due_date" type="date" {...register("due_date")} />
                {errors.due_date && (
                  <p className="text-sm text-destructive">{errors.due_date.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label>Category</Label>
                <Select
                  value={wCategory}
                  onValueChange={(v) =>
                    setValue("category", v as DutyValues["category"], { shouldValidate: true })
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CATEGORIES.map((c) => (
                      <SelectItem key={c} value={c}>
                        {c}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="recur_days">Repeats every (days)</Label>
                <Input id="recur_days" inputMode="numeric" placeholder="blank = one-off" {...register("recur_days")} />
                {errors.recur_days && (
                  <p className="text-sm text-destructive">{errors.recur_days.message}</p>
                )}
              </div>
            </div>
            {canSeeTeam ? (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Assignment
                </p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>Assign to role</Label>
                    <Select
                      value={wAssignedRoleId || NONE}
                      onValueChange={(v) => {
                        setValue("assigned_role_id", v);
                        if (v !== NONE) setValue("assigned_user_id", NONE);
                      }}
                      items={roleItems}
                    >
                      <SelectTrigger className="w-full">
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
                    <Label>or assign to worker</Label>
                    <Select
                      value={wAssignedUserId || NONE}
                      onValueChange={(v) => {
                        setValue("assigned_user_id", v);
                        if (v !== NONE) setValue("assigned_role_id", NONE);
                      }}
                      items={workerItems}
                    >
                      <SelectTrigger className="w-full">
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
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                You don&apos;t have team access — the duty will be created unassigned.
              </p>
            )}
            <DialogFooter>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Creating…" : "Create duty"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
