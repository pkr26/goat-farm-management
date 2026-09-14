"use client";

/** Landing page after login — parity with v1's dashboard.html. */

import {
  Baby,
  Boxes,
  CircleCheckBig,
  HandCoins,
  ListChecks,
  Mars,
  MoveRight,
  PawPrint,
  Scale,
  ScanLine,
  ShoppingCart,
  TriangleAlert,
  Venus,
} from "lucide-react";
import Link from "next/link";

import { useDashboardApiDashboardGet } from "@/api/generated/endpoints";
import type {
  AnimalIdentityOut,
  BucketCountOut,
  DashboardOutSexCounts,
  TaskOut,
} from "@/api/generated/models";
import { Donut } from "@/components/charts";
import { PageSkeleton } from "@/components/skeletons";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
import { enumLabel } from "@/lib/enum-labels";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { StatCard } from "@/components/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { daysBetween, farmToday, formatDate } from "@/lib/format";
import { withReturnTo } from "@/lib/permission-navigation";
import { permittedTaskActionPath, type PermissionCheck } from "@/lib/task-action-access";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";

/** v1's Animal.display_name: tag plus optional name. */
function animalName(a: AnimalIdentityOut): string {
  return a.tag_number + (a.name ? ` · ${a.name}` : "");
}

/** A withheld figure in a stat slot: neither 0 (a wrong number) nor blank
 * (reads as a load failure) — name the access it needs, the same sentence
 * the section markers below use (RT-P7-1). */
function WithheldStat({ permission }: { permission: string }) {
  return (
    <span className="text-sm font-normal text-muted-foreground">
      Requires {permission} access
    </span>
  );
}


/** The tasks page buckets strictly by due date, so a fallback "View" link has
 * to name the tab that can actually hold the row. */
function taskTabHref(dueDate: string, today: string): string {
  if (dueDate < today) return "/tasks?tab=overdue";
  return dueDate === today ? "/tasks?tab=today" : "/tasks?tab=upcoming";
}

/** "Open" to the task's linked form, else a plain "View" link to the tasks tab. */
function TaskLink({
  task,
  fallbackHref,
  can,
  label = "Open",
  returnTo,
}: {
  task: TaskOut;
  fallbackHref: string;
  can: PermissionCheck;
  label?: string;
  returnTo: string;
}) {
  const permittedAction = permittedTaskActionPath(task.action_url, can);
  if (permittedAction) {
    return (
      <Link
        href={withReturnTo(permittedAction, returnTo)}
        className={buttonVariants({ variant: "outline", size: "sm" })}
      >
        {label}
      </Link>
    );
  }
  if (can("tasks.view")) {
    return (
      <Link href={fallbackHref} className={buttonVariants({ variant: "outline", size: "sm" })}>
        View
      </Link>
    );
  }
  return <span className="text-xs text-muted-foreground">Action unavailable</span>;
}

export default function DashboardPage() {
  const perms = usePermissions();
  return (
    <PermissionGate
      perms={perms}
      perm="dashboard.view"
      label="Dashboard"
      description="Herd overview — tasks, breeding dates and recent weights."
      stats={5}
      cards={2}
      announce
    >
      <DashboardPageContent perms={perms} />
    </PermissionGate>
  );
}

function DashboardPageContent({ perms }: { perms: PermissionsState }) {
  const vocabulary = farmVocabulary;
  // Table headers and tag fallbacks start the sentence, so the female-parent
  // noun needs its display-case form ("Doe").
  const femaleParentLabel =
    vocabulary.femaleAdult.charAt(0).toUpperCase() + vocabulary.femaleAdult.slice(1);
  const { farms, farmId } = useAuth();
  const { can } = perms;
  const allowed = can("dashboard.view");
  const canViewAnimals = can("animals.view");
  const canViewBreeding = can("breeding.view");
  const canViewFinance = can("finance.view");
  const canViewTasks = can("tasks.view");
  const query = useDashboardApiDashboardGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  // `can(...)` comes from /api/auth/permissions — a DIFFERENT query, which
  // invalidateFarmData deliberately excludes, so the two caches drift by
  // design. Gating a data cell on permissions alone lets a withheld payload
  // (kiddings_due: [], total: 0) render as the factual "None."/"(0)" the
  // section's own comment forbids. Combine with the payload's own withheld
  // sentinel; OR keeps BOTH directions fail-closed, since trusting the
  // sentinel alone would render stale privileged rows after a revocation.
  const breedingWithheld = payload?.cull_candidates_total === null || !canViewBreeding;
  const animalsWithheld = payload?.recent_weights_total === null || !canViewAnimals;
  // Insurance is money at risk: the API nulls the section's total without
  // finance.view, and the stale permission cache must not resurrect a revoked
  // figure either (the same fail-closed OR as breeding/animals above).
  const insuranceWithheld = payload?.insurance_expiring_total === null || !canViewFinance;
  // The task previews/totals (and, without animals.view, the bucket/animal
  // aggregates read further down) arrive as the same null sentinels; the
  // generated client types lag the contract, so widen the read before
  // comparing. The API nulls the task sections without tasks.view — a
  // withheld register must never read as "Tasks due + overdue 0" /
  // "Nothing due today." (RT-P7-1).
  const tasksWithheld =
    (payload?.todays_tasks_total as number | null) === null || !canViewTasks;
  // The contract withholds suggestions (derived from breeding readiness or an
  // open pregnancy) without breeding.view — the same sentence that governs
  // kiddings and cull candidates — AND without animals.view (identity +
  // weights). Gate on either so a withheld section can never read as the
  // factual "No suggestions." (M-1).

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3 rounded-lg border border-destructive/40 p-4">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError
              ? query.error.detail
              : "Could not load the dashboard."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry dashboard
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader title="Dashboard" description="Herd overview — tasks, breeding dates and recent weights." />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading the dashboard…</span>
          <PageSkeleton stats={5} cards={2} />
        </div>
      </div>
    );
  }

  const farm = farms.find((f) => f.id === farmId);
  const suggestionsWithheld = animalsWithheld || breedingWithheld;
  // Comparisons against server due dates use the active farm's calendar day
  // (farmToday), not the browser's local date.
  const today = farmToday();
  // Withheld-sentinel reads (RT-P7-1): the API nulls the task sections
  // without tasks.view and the bucket/animal aggregates without animals.view.
  // The generated client still types them as always-present, so read through
  // these widened locals — the casts become no-ops once orval regenerates.
  const todaysTasks = (payload.todays_tasks as TaskOut[] | null) ?? [];
  const overdueTasks = (payload.overdue_tasks as TaskOut[] | null) ?? [];
  const ultrasoundsDue = (payload.ultrasounds_due as TaskOut[] | null) ?? [];
  const todaysTasksTotal = payload.todays_tasks_total as number | null;
  const overdueTasksTotal = payload.overdue_tasks_total as number | null;
  const ultrasoundsDueTotal = payload.ultrasounds_due_total as number | null;
  const bucketCounts = payload.buckets as BucketCountOut[] | null;
  const totalActive = payload.total_active as number | null;
  const sexCounts = payload.sex_counts as DashboardOutSexCounts | null;
  const taskTotal =
    todaysTasksTotal !== null && overdueTasksTotal !== null
      ? todaysTasksTotal + overdueTasksTotal
      : null;
  // Stat values: a withheld aggregate renders the access marker, never a
  // factual 0 (a null sentinel means "not allowed to know", and the stale
  // permission cache must not resurrect a revoked figure either).
  const taskStat = tasksWithheld || taskTotal === null ? null : taskTotal;
  const activeStat = animalsWithheld || totalActive === null ? null : totalActive;
  const femaleStat = animalsWithheld || sexCounts === null ? null : (sexCounts.F ?? 0);
  const maleStat = animalsWithheld || sexCounts === null ? null : (sexCounts.M ?? 0);
  const maxBucketCount = Math.max(1, ...(bucketCounts ?? []).map((b) => b.count));
  const recentWeights = payload.recent_weights;
  // A wall of identical red rows reads as alarm fatigue on the phone — cap
  // the preview; the register link carries the full list.
  const OVERDUE_PREVIEW_ROWS = 5;
  const overdueShown = overdueTasks.slice(0, OVERDUE_PREVIEW_ROWS);
  // A null total is "withheld", not "everything shown": each arm must fall
  // through as false rather than imply a bounded preview of a hidden count.
  const hasBoundedPreview =
    todaysTasks.length < (todaysTasksTotal ?? todaysTasks.length) ||
    overdueTasks.length < (overdueTasksTotal ?? overdueTasks.length) ||
    ultrasoundsDue.length < (ultrasoundsDueTotal ?? ultrasoundsDue.length) ||
    payload.kiddings_due.length < (payload.kiddings_due_total ?? 0) ||
    payload.cull_candidates.length < (payload.cull_candidates_total ?? payload.cull_candidates.length) ||
    payload.suggestions.length < (payload.suggestions_total ?? 0) ||
    recentWeights.length < (payload.recent_weights_total ?? recentWeights.length);

  return (
    // Mobile-first ordering with flex order: actionable cards (overdue, then
    // today) lead, passive stats follow. `md:order-none` restores source
    // order on desktop.
    <div className="flex flex-col gap-6">
      {query.isError && (
        <StaleDataNotice onRetry={() => void query.refetch()} />
      )}
      <PageHeader
        title={farm ? `${farm.name} — Dashboard` : "Dashboard"}
        description="Herd overview — tasks, breeding dates and recent weights."
      />
      {hasBoundedPreview && (
        <p className="order-2 text-xs text-muted-foreground md:order-none">
          Dashboard operational previews are capped at {payload.preview_limit} rows; recent weights
          are capped at {payload.recent_weights_limit}. Exact totals are shown, and the operational
          links open the full registers.
        </p>
      )}

      {/* First-run: a farm with no animals is the one moment the product
       * must teach its own workflow instead of displaying zeros. */}
      {/* A wound-down herd (everything sold/dead) is not "new" — only an
          all-zero status register is. */}
      {payload.total_active === 0 &&
        Object.values(payload.status_totals).every((total) => !total) &&
        canViewAnimals && (
        <Card className="order-3 border-primary/20 bg-gradient-to-br from-primary/[0.06] via-card to-card md:order-none">
          <CardHeader>
            <CardTitle>Welcome to your new farm</CardTitle>
            <CardDescription>
              Set up in three steps — everything else on this page fills in as you go.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-3">
            {[
              {
                step: 1,
                title: "Add your first animals",
                description:
                  "Tag every goat you own — tags are how the whole farm connects.",
                href: "/animals/new",
                cta: "Add animal",
                show: can("animals.create"),
                icon: PawPrint,
              },
              {
                step: 2,
                title: "Record a purchase batch",
                description:
                  "Buying animals? A batch auto-creates their 45-day quarantine plan.",
                href: "/purchases",
                cta: "Open purchases",
                show: can("purchases.view"),
                icon: ShoppingCart,
              },
              {
                step: 3,
                title: "Log today's work",
                description:
                  "Feeding, health events and tasks live here — check in each morning.",
                href: "/tasks",
                cta: "Open tasks",
                show: can("tasks.view"),
                icon: ListChecks,
              },
            ]
              .filter((item) => item.show)
              .map((item) => (
                <div
                  key={item.step}
                  className="flex flex-col gap-2 rounded-xl border bg-card/60 p-4"
                >
                  <div className="flex items-center gap-2.5">
                    <span className="table-numeric flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                      {item.step}
                    </span>
                    <item.icon className="size-4 text-muted-foreground" aria-hidden="true" />
                  </div>
                  <p className="text-sm font-medium">{item.title}</p>
                  <p className="text-xs text-muted-foreground">{item.description}</p>
                  <Link
                    href={item.href}
                    className={buttonVariants({ variant: "outline", size: "sm", className: "mt-auto w-fit" })}
                  >
                    {item.cta}
                  </Link>
                </div>
              ))}
          </CardContent>
        </Card>
      )}

      <div className="order-6 grid grid-cols-2 gap-3 [&>*:nth-child(5)]:col-span-2 sm:grid-cols-3 sm:[&>*:nth-child(5)]:col-span-1 md:order-none lg:grid-cols-5">
        <StatCard
          label="Active animals"
          value={activeStat ?? <WithheldStat permission="animals" />}
          icon={PawPrint}
          tint="success"
        />
        <StatCard
          label="Females"
          value={femaleStat ?? <WithheldStat permission="animals" />}
          icon={Venus}
        />
        <StatCard
          label="Males"
          value={maleStat ?? <WithheldStat permission="animals" />}
          icon={Mars}
        />
        <StatCard
          label="Sold (all time)"
          value={payload.status_totals.SOLD ?? 0}
          icon={HandCoins}
        />
        <StatCard
          label="Tasks due + overdue"
          value={taskStat ?? <WithheldStat permission="tasks" />}
          icon={ListChecks}
          tint={
            taskStat !== null && (overdueTasksTotal ?? 0) > 0
              ? "destructive"
              : (taskStat ?? 0) > 0
                ? "warning"
                : "default"
          }
        />
      </div>

      {!tasksWithheld && (overdueTasksTotal ?? 0) > 0 && (
        <DataTableCard
          className="order-4 ring-destructive/30 md:order-none"
          title={
            <span className="flex items-center gap-2 text-destructive">
              <TriangleAlert className="size-4" aria-hidden="true" />
              Overdue tasks ({overdueTasksTotal})
            </span>
          }
        >
          <Table>
            <TableHeader className="sr-only">
              <TableRow><th scope="col">Due</th><th scope="col">Task</th><th scope="col">Open</th></TableRow>
            </TableHeader>
            <TableBody>
              {overdueShown.map((t) => (
                  <TableRow key={t.id} className="bg-destructive/[0.04]">
                  <TableCell>
                    {formatDate(t.due_date)}{" "}
                    <span className="text-destructive">
                      ({daysBetween(t.due_date, today)}d late)
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className="block max-w-56 truncate sm:max-w-none">{t.title}</span>
                  </TableCell>
                  <TableCell className="text-right">
                    <TaskLink
                      task={t}
                      fallbackHref="/tasks?tab=overdue"
                      returnTo="/dashboard"
                      can={can}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {overdueShown.length < (overdueTasksTotal ?? overdueShown.length) && (
            <p className="pt-3 text-sm text-muted-foreground">
              Showing {overdueShown.length} of {overdueTasksTotal}.{" "}
              {canViewTasks && (
                <Link href="/tasks?tab=overdue" className="text-primary underline">
                  View all overdue tasks
                </Link>
              )}
            </p>
          )}
        </DataTableCard>
      )}

      <div className="order-5 grid gap-4 md:order-none lg:grid-cols-2">
        <DataTableCard
          title={
            !tasksWithheld
              ? `Today's tasks (${todaysTasksTotal ?? 0})`
              : "Today's tasks"
          }
          actions={canViewTasks ? (
            <Link
              href="/tasks?tab=today"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              View all
            </Link>
          ) : undefined}
        >
          {tasksWithheld ? (
            /* The API withholds the task register without tasks.view — say so
                instead of asserting "Nothing due today." over live duties
                (RT-P7-1). */
            <EmptyState
              icon={ListChecks}
              title="Today's tasks require tasks access."
              description="Ask an admin to grant tasks.view to see today's duties here."
            />
          ) : todaysTasks.length === 0 ? (
            <EmptyState
              icon={CircleCheckBig}
              title="Nothing due today."
              className="py-8"
            />
          ) : (
            <Table>
              <TableHeader className="sr-only">
                <TableRow><th scope="col">Task</th><th scope="col">Open</th></TableRow>
              </TableHeader>
              <TableBody>
                {todaysTasks.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell>
                      <span className="block max-w-56 truncate sm:max-w-none">{t.title}</span>
                    </TableCell>
                    <TableCell className="text-right">
                      <TaskLink
                        task={t}
                        fallbackHref="/tasks?tab=today"
                        returnTo="/dashboard"
                        can={can}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {!tasksWithheld &&
            todaysTasks.length < (todaysTasksTotal ?? todaysTasks.length) && (
            <p className="pt-3 text-sm text-muted-foreground">
              Showing {todaysTasks.length} of {todaysTasksTotal}.
            </p>
          )}
        </DataTableCard>

        {/* The total is overdue + the next 14 days, and the overdue side is
            unbounded backwards, so the title must not claim a 14-day span.
            Without breeding.view the API returns [] / 0 — a withheld section,
            which must not render as a factual count or an empty state. */}
        <DataTableCard
          title={
            !breedingWithheld
              ? `${vocabulary.parturitionCap}s due or overdue (${payload.kiddings_due_total})`
              : `${vocabulary.parturitionCap}s due or overdue`
          }
          actions={canViewBreeding ? (
            <Link
              href="/breeding"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              Breeding
            </Link>
          ) : undefined}
        >
          {breedingWithheld ? (
            <EmptyState
              icon={Baby}
              title={`${vocabulary.parturitionCap}s require breeding access.`}
              description={`Ask an admin to grant breeding.view to see ${vocabulary.parturition}s due here.`}
            />
          ) : payload.kiddings_due.length === 0 ? (
            <EmptyState icon={Baby} title={`No ${vocabulary.parturition}s due.`} description={`Confirmed pregnancies appear here as their due dates approach.`} className="py-8" />
          ) : (
            <Table>
              <TableHeader className="sr-only">
                <TableRow><th scope="col">{femaleParentLabel}</th><th scope="col">Due</th><th scope="col">Record</th></TableRow>
              </TableHeader>
              <TableBody>
                {payload.kiddings_due.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>
                      {canViewAnimals ? (
                        <Link
                          href={withReturnTo(`/animals/${r.doe_id}`, "/dashboard")}
                          className="text-primary underline"
                        >
                          {r.doe_tag ?? `${femaleParentLabel} #${r.doe_id}`}
                        </Link>
                      ) : (
                        r.doe_tag ?? `${femaleParentLabel} #${r.doe_id}`
                      )}
                    </TableCell>
                    <TableCell>
                      due {formatDate(r.expected_kidding_date)}
                      {/* Overdue pregnancies are mixed into this list; mark
                          them like the overdue-tasks card does. */}
                      {r.expected_kidding_date !== null &&
                        r.expected_kidding_date < today && (
                          <span className="text-destructive">
                            {" "}
                            ({daysBetween(r.expected_kidding_date, today)}d late)
                          </span>
                        )}
                    </TableCell>
                    <TableCell className="text-right">
                      {/* Recording needs kidding.manage — without it the link
                          lands on an access-denied page. */}
                      {can("kidding.manage") && (
                        <Link
                          href={`/kidding/new?breeding_id=${r.id}`}
                          className={buttonVariants({ variant: "outline", size: "sm" })}
                        >
                          Record
                        </Link>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {payload.kiddings_due.length < (payload.kiddings_due_total ?? 0) && (
            <p className="pt-3 text-sm text-muted-foreground">
              Showing {payload.kiddings_due.length} of {payload.kiddings_due_total}.{" "}
              {can("kidding.view") && (
                <Link href="/kidding" className="text-primary underline">
                  View the {vocabulary.parturition} register
                </Link>
              )}
            </p>
          )}
        </DataTableCard>

        <DataTableCard
          title={
            !tasksWithheld
              ? `Ultrasounds due in 7 days (${ultrasoundsDueTotal ?? 0})`
              : "Ultrasounds due in 7 days"
          }
          actions={can("breeding.view") ? (
            <Link
              href="/breeding"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              Breeding
            </Link>
          ) : undefined}
        >
          {tasksWithheld ? (
            /* Ultrasound duties come from the task register, so they are
                withheld with it — never "No ultrasounds due." over live
                pregnancy checks (RT-P7-1). */
            <EmptyState
              icon={ScanLine}
              title="Ultrasounds require tasks access."
              description="Ask an admin to grant tasks.view to see pregnancy-check scans due here."
            />
          ) : ultrasoundsDue.length === 0 ? (
            <EmptyState icon={ScanLine} title="No ultrasounds due." description="Pregnancy-check scans scheduled in the next 7 days appear here." className="py-8" />
          ) : (
            <Table>
              <TableHeader className="sr-only">
                <TableRow><th scope="col">Due</th><th scope="col">Task</th><th scope="col">Record result</th></TableRow>
              </TableHeader>
              <TableBody>
                {ultrasoundsDue.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell>
                      {formatDate(t.due_date)}
                      {t.due_date < today && (
                        <span className="text-destructive">
                          {" "}
                          ({daysBetween(t.due_date, today)}d late)
                        </span>
                      )}
                    </TableCell>
                    <TableCell>{t.title}</TableCell>
                    <TableCell className="text-right">
                      <TaskLink
                        task={t}
                        fallbackHref={taskTabHref(t.due_date, today)}
                        returnTo="/dashboard"
                        can={can}
                        label="Record result"
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {!tasksWithheld &&
            ultrasoundsDue.length < (ultrasoundsDueTotal ?? ultrasoundsDue.length) && (
            <p className="pt-3 text-sm text-muted-foreground">
              Showing {ultrasoundsDue.length} of {ultrasoundsDueTotal}.
            </p>
          )}
        </DataTableCard>

        {/* Suggestions carry animal identity, the exact latest weight AND a
            breeding-programme judgement, so the API withholds them without
            either animals.view or breeding.view. Say so rather than claiming
            the herd has nothing ready to move (M-1). */}
        <DataTableCard
          title={
            !suggestionsWithheld ? `Ready to move (${payload.suggestions_total})` : "Ready to move"
          }
          actions={canViewAnimals ? (
            <Link
              href="/animals"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              View herd
            </Link>
          ) : undefined}
          contentClassName="space-y-3"
        >
          {suggestionsWithheld ? (
            <EmptyState
              icon={MoveRight}
              title="Move suggestions require animal and breeding access."
              description="Ask an admin to grant animals.view and breeding.view to see which animals are ready to move."
            />
          ) : payload.suggestions.length === 0 ? (
            <EmptyState icon={MoveRight} title="No move suggestions." description="When an animal is ready for the next pen, the move appears here." className="py-8" />
          ) : (
            <Table>
              <TableHeader className="sr-only">
                <TableRow><th scope="col">Animal</th><th scope="col">Reason</th><th scope="col">Move to</th></TableRow>
              </TableHeader>
              <TableBody>
                {payload.suggestions.map((s) => (
                  <TableRow key={s.animal.id}>
                    <TableCell>
                      {canViewAnimals ? (
                        <Link
                          href={withReturnTo(`/animals/${s.animal.id}`, "/dashboard")}
                          className="text-primary underline"
                        >
                          {animalName(s.animal)}
                        </Link>
                      ) : (
                        animalName(s.animal)
                      )}
                    </TableCell>
                    <TableCell>{s.reason}</TableCell>
                    <TableCell className="text-right">
                      <Badge variant="secondary">
                        <MoveRight className="size-3" aria-hidden="true" />
                        {enumLabel("bucket", s.to)}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {!suggestionsWithheld && payload.suggestions.length < (payload.suggestions_total ?? 0) && (
            <p className="text-sm text-muted-foreground">
              Showing {payload.suggestions.length} of {payload.suggestions_total} move suggestions.
            </p>
          )}
          {/* null = withheld (no breeding access); the banner only ever
              asserts a count the caller is allowed to see */}
          {(payload.cull_candidates_total ?? 0) > 0 && (
            <p className="flex items-center gap-2 rounded-lg bg-warning-tint px-3 py-2 text-sm text-warning-tint-foreground">
              <TriangleAlert className="size-4 shrink-0" aria-hidden="true" />
              <span>
                {payload.cull_candidates_total} cull candidate(s) —{" "}
                {can("breeding.view") ? (
                  <Link href="/breeding" className="underline">
                    see breeding page
                  </Link>
                ) : (
                  "flagged in breeding records"
                )}
              </span>
            </p>
          )}
        </DataTableCard>

        {/* Animals frozen by a movement restriction / disease hold. The hold
            silently blocks move, breeding, sale and cull until a referenced
            clearance, and this is its only farm-wide surface — so it renders
            whenever the caller can see animal identity at all (a null total
            means the section was withheld, and 0 stays silent like the cull
            banner: no alarm fatigue when nobody is held). */}
        {(payload.restricted_animals_total ?? 0) > 0 && (
          <DataTableCard
            title={`Movement restrictions (${payload.restricted_animals_total})`}
            contentClassName="space-y-3"
          >
            <p className="flex items-center gap-2 rounded-lg bg-warning-tint px-3 py-2 text-sm text-warning-tint-foreground">
              <TriangleAlert className="size-4 shrink-0" aria-hidden="true" />
              <span>
                These animals cannot move, breed or be sold until a vet records a
                referenced clearance.
              </span>
            </p>
            <Table>
              <TableHeader className="sr-only">
                <TableRow>
                  <th scope="col">Animal</th>
                  <th scope="col">Bucket</th>
                  <th scope="col">Held since</th>
                  <th scope="col">Reason</th>
                </TableRow>
              </TableHeader>
              <TableBody>
                {payload.restricted_animals.map((r) => (
                  <TableRow key={r.animal.id}>
                    <TableCell>
                      {canViewAnimals ? (
                        <Link
                          href={withReturnTo(`/animals/${r.animal.id}`, "/dashboard")}
                          className="text-primary underline"
                        >
                          {animalName(r.animal)}
                        </Link>
                      ) : (
                        animalName(r.animal)
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{enumLabel("bucket", r.current_bucket)}</Badge>
                    </TableCell>
                    <TableCell>
                      {r.held_since ? new Date(r.held_since).toLocaleDateString() : "—"}
                    </TableCell>
                    <TableCell className="max-w-64 truncate">{r.reason ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {payload.restricted_animals.length < (payload.restricted_animals_total ?? 0) && (
              <p className="text-sm text-muted-foreground">
                Showing {payload.restricted_animals.length} of{" "}
                {payload.restricted_animals_total} held animals.
              </p>
            )}
          </DataTableCard>
        )}
        {/* Active policies renewing inside the expiry window. Money at risk
            sits behind finance.view — a null total means the section was
            withheld (the register holds the full list), and 0 stays silent
            like the cull banner: no alarm fatigue when nothing expires. */}
        {!insuranceWithheld && (payload.insurance_expiring_total ?? 0) > 0 && (
          <DataTableCard
            title={`Insurance renewals due soon (${payload.insurance_expiring_total})`}
            contentClassName="space-y-3"
          >
            <Table>
              <TableHeader className="sr-only">
                <TableRow>
                  <th scope="col">Policy</th>
                  <th scope="col">Insurer</th>
                  <th scope="col">Animal</th>
                  <th scope="col">Renewal date</th>
                </TableRow>
              </TableHeader>
              <TableBody>
                {payload.insurance_expiring.map((policy) => (
                  <TableRow key={policy.id}>
                    <TableCell className="font-medium">{policy.policy_number}</TableCell>
                    <TableCell>{policy.insurer}</TableCell>
                    <TableCell>{policy.animal_tag ?? "—"}</TableCell>
                    <TableCell>
                      {formatDate(policy.renewal_date)}{" "}
                      <span className="text-warning-tint-foreground">
                        (in {daysBetween(today, policy.renewal_date)}d)
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {canViewFinance && (
              <p className="text-sm text-muted-foreground">
                <Link href="/finance/insurance" className="text-primary underline">
                  View the insurance register
                </Link>
              </p>
            )}
          </DataTableCard>
        )}
      </div>

      <section className="order-7 space-y-3 md:order-none">
        <h2 className="font-heading text-lg font-semibold">Herd by bucket</h2>
        {/* null = withheld (no animals access): say so rather than painting an
            empty herd around a hollow donut (RT-P7-1). */}
        {animalsWithheld || bucketCounts === null ? (
          <EmptyState
            icon={Boxes}
            title="Bucket counts require animals access."
            description="Ask an admin to grant animals.view to see the herd by bucket here."
          />
        ) : (
          <Card>
            <CardContent className="grid gap-6 lg:grid-cols-2 lg:items-center">
              <Donut
                slices={bucketCounts.map((b) => ({ label: b.name, value: b.count }))}
                centerValue={totalActive ?? 0}
                centerLabel="active animals"
                showLegend={false}
              />
              <ul className="space-y-1">
                {bucketCounts.map((b) => {
                  const row = (
                    <>
                      <span className="min-w-0 flex-1 truncate text-sm">{b.name}</span>
                      <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[0.65rem] font-medium text-muted-foreground">
                        {b.code}
                      </span>
                      <span
                        aria-hidden="true"
                        className="hidden h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-muted sm:block"
                      >
                        <span
                          className="block h-full rounded-full bg-primary"
                          style={{
                            width: `${Math.round((b.count / maxBucketCount) * 100)}%`,
                          }}
                        />
                      </span>
                      <span className="table-numeric w-8 shrink-0 text-right text-sm font-semibold">
                        {b.count}
                      </span>
                    </>
                  );
                  return (
                    <li key={b.code}>
                      {canViewAnimals ? (
                        <Link
                          href={`/animals?bucket=${encodeURIComponent(b.code)}&status=ACTIVE`}
                          className="flex items-center gap-3 rounded-lg px-2 py-1.5 transition hover:bg-muted/60"
                        >
                          {row}
                        </Link>
                      ) : (
                        <div className="flex items-center gap-3 px-2 py-1.5">{row}</div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </CardContent>
          </Card>
        )}
      </section>

      <section className="order-8 space-y-3 md:order-none">
        <h2 className="font-heading text-lg font-semibold">Recent weight records</h2>
        {/* null = withheld (no animals.view); a real 0 renders as a genuine
            empty state, not this permission notice */}
        {payload.recent_weights_total === null ? (
          <EmptyState
            icon={Scale}
            title="Weight records require animals access."
            description="Ask an admin to grant animals.view to see recent weight records here."
          />
        ) : recentWeights.length === 0 ? (
          <EmptyState
            icon={Scale}
            title="No weight records yet."
            description="Weights you record will show up here."
          >
            {canViewAnimals && can("animals.create") && (
              <Link
                href="/animals/new"
                className={buttonVariants({ variant: "outline", size: "sm" })}
              >
                Add your first animal
              </Link>
            )}
          </EmptyState>
        ) : (
          <DataTableCard>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Animal</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead>Weight</TableHead>
                  <TableHead>BCS</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recentWeights.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell>
                      {canViewAnimals ? (
                        <Link
                          href={withReturnTo(`/animals/${w.animal.id}`, "/dashboard")}
                          className="text-primary underline"
                        >
                          {animalName(w.animal)}
                        </Link>
                      ) : (
                        animalName(w.animal)
                      )}
                    </TableCell>
                    <TableCell>{formatDate(w.date)}</TableCell>
                    <TableCell>{w.weight_kg.toFixed(1)} kg</TableCell>
                    <TableCell>{w.bcs ?? "—"}</TableCell>
                    <TableCell>{w.notes ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </DataTableCard>
        )}
        {recentWeights.length < (payload.recent_weights_total ?? 0) && (
          <p className="text-sm text-muted-foreground">
            Showing {recentWeights.length} of {payload.recent_weights_total} recent weight
            records.
          </p>
        )}
      </section>
    </div>
  );
}
