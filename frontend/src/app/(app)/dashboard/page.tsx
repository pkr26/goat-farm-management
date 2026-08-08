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
import type { AnimalOut, TaskOut } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatCard } from "@/components/stat-card";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
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
import { formatDate, utcToday } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";
import { safeAppPath } from "@/lib/utils";

/** v1's Animal.display_name: tag plus optional name. */
function animalName(a: AnimalOut): string {
  return a.tag_number + (a.name ? ` · ${a.name}` : "");
}

/** Whole days from `from` to `to` (both YYYY-MM-DD), timezone-safe. */
function daysBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 86400000);
}

/** "Open" to the task's linked form, else a plain "View" link to the tasks tab. */
function TaskLink({
  task,
  fallbackHref,
  label = "Open",
}: {
  task: TaskOut;
  fallbackHref: string;
  label?: string;
}) {
  const safeAction = safeAppPath(task.action_url);
  if (safeAction) {
    return (
      <Link href={safeAction} className={buttonVariants({ variant: "outline", size: "sm" })}>
        {label}
      </Link>
    );
  }
  return (
    <Link href={fallbackHref} className={buttonVariants({ variant: "outline", size: "sm" })}>
      View
    </Link>
  );
}

export default function DashboardPage() {
  const { farms, farmId } = useAuth();
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("dashboard.view");
  const query = useDashboardApiDashboardGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

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
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError ? query.error.detail : "Could not load the dashboard."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  const farm = farms.find((f) => f.id === farmId);
  // Comparisons against server due dates use the backend's UTC today (7-5).
  const today = utcToday();
  const taskTotal = payload.todays_tasks.length + payload.overdue_tasks.length;
  const maxBucketCount = Math.max(1, ...payload.buckets.map((b) => b.count));

  return (
    <div className="space-y-6">
      <PageHeader
        title={farm ? `${farm.name} — Dashboard` : "Dashboard"}
        description="Herd overview — tasks, breeding dates and recent weights."
      />

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
            payload.overdue_tasks.length > 0 ? "red" : taskTotal > 0 ? "amber" : "default"
          }
        />
      </div>

      {payload.overdue_tasks.length > 0 && (
        <DataTableCard
          className="ring-red-200 dark:ring-red-900"
          title={
            <span className="flex items-center gap-2">
              <TriangleAlert className="size-4 text-red-600 dark:text-red-400" />
              Overdue tasks
            </span>
          }
        >
          <Table>
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
                    <TaskLink task={t} fallbackHref="/tasks?tab=overdue" />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DataTableCard>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <DataTableCard
          title="Today's tasks"
          actions={
            <Link
              href="/tasks?tab=today"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              View all
            </Link>
          }
        >
          {payload.todays_tasks.length === 0 ? (
            <EmptyState
              icon={CircleCheckBig}
              title="Nothing due today."
              className="py-8"
            />
          ) : (
            <Table>
              <TableBody>
                {payload.todays_tasks.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell>{t.title}</TableCell>
                    <TableCell className="text-right">
                      <TaskLink task={t} fallbackHref="/tasks?tab=today" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </DataTableCard>

        <DataTableCard
          title="Kiddings due in 14 days"
          actions={
            <Link
              href="/breeding"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              Breeding
            </Link>
          }
        >
          {payload.kiddings_due.length === 0 ? (
            <EmptyState icon={Baby} title="None." className="py-8" />
          ) : (
            <Table>
              <TableBody>
                {payload.kiddings_due.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>
                      <Link href={`/animals/${r.doe_id}`} className="text-primary underline">
                        {r.doe_tag ?? `Doe #${r.doe_id}`}
                      </Link>
                    </TableCell>
                    <TableCell>due {formatDate(r.expected_kidding_date)}</TableCell>
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
        </DataTableCard>

        <DataTableCard
          title="Ultrasounds due in 7 days"
          actions={
            <Link
              href="/breeding"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              Breeding
            </Link>
          }
        >
          {payload.ultrasounds_due.length === 0 ? (
            <EmptyState icon={ScanLine} title="None." className="py-8" />
          ) : (
            <Table>
              <TableBody>
                {payload.ultrasounds_due.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell>{formatDate(t.due_date)}</TableCell>
                    <TableCell>{t.title}</TableCell>
                    <TableCell className="text-right">
                      <TaskLink task={t} fallbackHref="/tasks?tab=today" label="Record result" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </DataTableCard>

        <DataTableCard
          title={`Ready to move (${payload.suggestions.length})`}
          actions={
            <Link
              href="/animals"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              View herd
            </Link>
          }
          contentClassName="space-y-3"
        >
          {payload.suggestions.length === 0 ? (
            <EmptyState icon={MoveRight} title="No suggestions." className="py-8" />
          ) : (
            <Table>
              <TableBody>
                {payload.suggestions.map((s) => (
                  <TableRow key={s.animal.id}>
                    <TableCell>
                      <Link
                        href={`/animals/${s.animal.id}`}
                        className="text-primary underline"
                      >
                        {animalName(s.animal)}
                      </Link>
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
          {payload.cull_candidates.length > 0 && (
            <p className="flex items-center gap-2 rounded-lg bg-amber-100 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-300">
              <TriangleAlert className="size-4 shrink-0" />
              <span>
                {payload.cull_candidates.length} cull candidate(s) —{" "}
                <Link href="/breeding" className="underline">
                  see breeding page
                </Link>
              </span>
            </p>
          )}
        </DataTableCard>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Herd by bucket</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {payload.buckets.map((b) => (
            <Link
              key={b.code}
              href={`/animals?bucket=${encodeURIComponent(b.code)}`}
              className="space-y-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10 transition hover:ring-primary"
            >
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
            </Link>
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Recent weight records</h2>
        {payload.recent_weights.length === 0 ? (
          <EmptyState
            icon={Scale}
            title="No weight records yet."
            description="Weights you record will show up here."
          >
            <Link
              href="/animals/new"
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              Add your first animal
            </Link>
          </EmptyState>
        ) : (
          <DataTableCard>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Weight</TableHead>
                  <TableHead>BCS</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {payload.recent_weights.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell>{formatDate(w.date)}</TableCell>
                    <TableCell>{w.weight_kg.toFixed(1)} kg</TableCell>
                    <TableCell>{w.bcs ?? "—"}</TableCell>
                    <TableCell>{w.notes ?? ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </DataTableCard>
        )}
      </section>
    </div>
  );
}
