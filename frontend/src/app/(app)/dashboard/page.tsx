"use client";

/** Landing page after login — parity with v1's dashboard.html. */

import {
  Baby,
  CircleCheckBig,
  HandCoins,
  Layers,
  ListChecks,
  Mars,
  MoveRight,
  PawPrint,
  Scale,
  ScanLine,
  TriangleAlert,
  Venus,
} from "lucide-react";
import Link from "next/link";

import { useDashboardApiDashboardGet } from "@/api/generated/endpoints";
import type { AnimalIdentityOut, TaskOut } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { useFarmType } from "@/hooks/use-farm-type";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { StatCard } from "@/components/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
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
import { farmToday, formatDate } from "@/lib/format";
import { withReturnTo } from "@/lib/permission-navigation";
import { permittedTaskActionPath, type PermissionCheck } from "@/lib/task-action-access";
import { usePermissions } from "@/lib/use-permissions";

/** v1's Animal.display_name: tag plus optional name. */
function animalName(a: AnimalIdentityOut): string {
  return a.tag_number + (a.name ? ` · ${a.name}` : "");
}

/** Whole days from `from` to `to` (both YYYY-MM-DD), timezone-safe. */
function daysBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 86400000);
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
  const vocabulary = farmVocabulary(useFarmType());
  // Table headers and tag fallbacks start the sentence, so the female-parent
  // noun needs its display-case form ("Doe" / "Milking buffalo").
  const femaleParentLabel =
    vocabulary.femaleAdult.charAt(0).toUpperCase() + vocabulary.femaleAdult.slice(1);
  const { farms, farmId } = useAuth();
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("dashboard.view");
  const canViewAnimals = can("animals.view");
  const canViewBreeding = can("breeding.view");
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
  // The contract withholds suggestions (derived from breeding readiness or an
  // open pregnancy) without breeding.view — the same sentence that governs
  // kiddings and cull candidates — AND without animals.view (identity +
  // weights). Gate on either so a withheld section can never read as the
  // factual "No suggestions." (M-1).

  if (permsLoading) {
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }
  if (permsError) {
    return (
      <p className="text-sm text-destructive">
        Could not load your permissions — refresh the page to try again.
      </p>
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
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  const farm = farms.find((f) => f.id === farmId);
  const suggestionsWithheld = animalsWithheld || breedingWithheld;
  // Comparisons against server due dates use the active farm's calendar day
  // (farmToday), not the browser's local date.
  const today = farmToday();
  const taskTotal = payload.todays_tasks_total + payload.overdue_tasks_total;
  const maxBucketCount = Math.max(1, ...payload.buckets.map((b) => b.count));
  const recentWeights = payload.recent_weights;
  const hasBoundedPreview =
    payload.todays_tasks.length < payload.todays_tasks_total ||
    payload.overdue_tasks.length < payload.overdue_tasks_total ||
    payload.ultrasounds_due.length < payload.ultrasounds_due_total ||
    payload.kiddings_due.length < payload.kiddings_due_total ||
    payload.cull_candidates.length < (payload.cull_candidates_total ?? 0) ||
    payload.suggestions.length < payload.suggestions_total ||
    payload.recent_weights.length < (payload.recent_weights_total ?? 0);

  return (
    <div className="space-y-6">
      <PageHeader
        title={farm ? `${farm.name} — Dashboard` : "Dashboard"}
        description="Herd overview — tasks, breeding dates and recent weights."
      />
      {hasBoundedPreview && (
        <p className="text-xs text-muted-foreground">
          Dashboard operational previews are capped at {payload.preview_limit} rows; recent weights
          are capped at {payload.recent_weights_limit}. Exact totals are shown, and the operational
          links open the full registers.
        </p>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard
          label="Active animals"
          value={payload.total_active}
          icon={PawPrint}
          tint="emerald"
        />
        <StatCard label="Females" value={payload.sex_counts.F ?? 0} icon={Venus} />
        <StatCard label="Males" value={payload.sex_counts.M ?? 0} icon={Mars} />
        <StatCard
          label="Sold (all time)"
          value={payload.status_totals.SOLD ?? 0}
          icon={HandCoins}
        />
        <StatCard
          label="Tasks due + overdue"
          value={taskTotal}
          icon={ListChecks}
          tint={
            payload.overdue_tasks_total > 0 ? "red" : taskTotal > 0 ? "amber" : "default"
          }
        />
      </div>

      {payload.overdue_tasks_total > 0 && (
        <DataTableCard
          className="ring-red-200 dark:ring-red-900"
          title={
            <span className="flex items-center gap-2">
              <TriangleAlert className="size-4 text-red-600 dark:text-red-400" />
              Overdue tasks ({payload.overdue_tasks_total})
            </span>
          }
        >
          <Table>
            <TableHeader className="sr-only">
                <TableRow><th scope="col">Due</th><th scope="col">Task</th><th scope="col">Open</th></TableRow>
              </TableHeader>
              <TableBody>
              {payload.overdue_tasks.map((t) => (
                <TableRow key={t.id} className="bg-red-50/60 dark:bg-red-950/20">
                  <TableCell>
                    {formatDate(t.due_date)}{" "}
                    <span className="text-destructive">
                      ({daysBetween(t.due_date, today)}d late)
                    </span>
                  </TableCell>
                  <TableCell>{t.title}</TableCell>
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
          {payload.overdue_tasks.length < payload.overdue_tasks_total && (
            <p className="pt-3 text-sm text-muted-foreground">
              Showing {payload.overdue_tasks.length} of {payload.overdue_tasks_total}.{" "}
              {can("tasks.view") && (
                <Link href="/tasks?tab=overdue" className="text-primary underline">
                  View all overdue tasks
                </Link>
              )}
            </p>
          )}
        </DataTableCard>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <DataTableCard
          title={`Today's tasks (${payload.todays_tasks_total})`}
          actions={can("tasks.view") ? (
            <Link
              href="/tasks?tab=today"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              View all
            </Link>
          ) : undefined}
        >
          {payload.todays_tasks.length === 0 ? (
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
                {payload.todays_tasks.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell>{t.title}</TableCell>
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
          {payload.todays_tasks.length < payload.todays_tasks_total && (
            <p className="pt-3 text-sm text-muted-foreground">
              Showing {payload.todays_tasks.length} of {payload.todays_tasks_total}.
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
            <EmptyState icon={Baby} title="None." className="py-8" />
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
          {payload.kiddings_due.length < payload.kiddings_due_total && (
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
          title={`Ultrasounds due in 7 days (${payload.ultrasounds_due_total})`}
          actions={can("breeding.view") ? (
            <Link
              href="/breeding"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              Breeding
            </Link>
          ) : undefined}
        >
          {payload.ultrasounds_due.length === 0 ? (
            <EmptyState icon={ScanLine} title="None." className="py-8" />
          ) : (
            <Table>
              <TableHeader className="sr-only">
                <TableRow><th scope="col">Due</th><th scope="col">Task</th><th scope="col">Record result</th></TableRow>
              </TableHeader>
              <TableBody>
                {payload.ultrasounds_due.map((t) => (
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
          {payload.ultrasounds_due.length < payload.ultrasounds_due_total && (
            <p className="pt-3 text-sm text-muted-foreground">
              Showing {payload.ultrasounds_due.length} of {payload.ultrasounds_due_total}.
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
            <EmptyState icon={MoveRight} title="No suggestions." className="py-8" />
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
                      <Badge variant="secondary">→ {s.to}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {!suggestionsWithheld && payload.suggestions.length < payload.suggestions_total && (
            <p className="text-sm text-muted-foreground">
              Showing {payload.suggestions.length} of {payload.suggestions_total} move suggestions.
            </p>
          )}
          {/* null = withheld (no breeding access); the banner only ever
              asserts a count the caller is allowed to see */}
          {(payload.cull_candidates_total ?? 0) > 0 && (
            <p className="flex items-center gap-2 rounded-lg bg-amber-100 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-300">
              <TriangleAlert className="size-4 shrink-0" />
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
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Herd by bucket</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {payload.buckets.map((b) => {
            const content = (
              <>
              <div className="flex items-start justify-between gap-2">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300 [&_svg]:size-4.5">
                  <Layers />
                </span>
                <span className="text-2xl font-semibold tracking-tight">{b.count}</span>
              </div>
              <div>
                <div className="text-sm font-medium">{b.name}</div>
                <div className="text-xs text-muted-foreground">{b.code}</div>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-accent/60">
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width: `${Math.round((b.count / maxBucketCount) * 100)}%` }}
                />
              </div>
              </>
            );
            return canViewAnimals ? (
              <Link
                key={b.code}
                href={`/animals?bucket=${encodeURIComponent(b.code)}&status=ACTIVE`}
                className="space-y-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10 transition hover:ring-primary"
              >
                {content}
              </Link>
            ) : (
              <div
                key={b.code}
                className="space-y-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10"
              >
                {content}
              </div>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Recent weight records</h2>
        {/* null = withheld (no animals.view); a real 0 renders as a genuine
            empty state, not this permission notice */}
        {payload.recent_weights_total === null ? (
          <EmptyState
            icon={Scale}
            title="Weight records require animal access."
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
