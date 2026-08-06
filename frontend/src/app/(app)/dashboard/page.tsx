"use client";

/** Landing page after login — parity with v1's dashboard.html. */

import Link from "next/link";
import type { ReactNode } from "react";

import { useDashboardApiDashboardGet } from "@/api/generated/endpoints";
import type { AnimalOut, TaskOut } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { formatDate } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";

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

function localToday(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

function StatCard({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div className="rounded-xl bg-card p-4 ring-1 ring-foreground/10">
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-sm text-muted-foreground">{label}</div>
    </div>
  );
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
  if (task.action_url) {
    return (
      <Link href={task.action_url} className={buttonVariants({ variant: "outline", size: "sm" })}>
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
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("dashboard.view");
  const query = useDashboardApiDashboardGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

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
          {query.error instanceof ApiError ? query.error.detail : "Could not load the dashboard."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  const farm = farms.find((f) => f.id === farmId);
  const today = localToday();
  const taskTotal = payload.todays_tasks.length + payload.overdue_tasks.length;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{farm ? `${farm.name} — Dashboard` : "Dashboard"}</h1>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard value={payload.total_active} label="Active animals" />
        <StatCard value={payload.sex_counts.F ?? 0} label="Females" />
        <StatCard value={payload.sex_counts.M ?? 0} label="Males" />
        <StatCard value={payload.status_totals.SOLD ?? 0} label="Sold (all time)" />
        <StatCard value={taskTotal} label="Tasks due + overdue" />
      </div>

      {payload.overdue_tasks.length > 0 && (
        <Card className="ring-destructive/40">
          <CardHeader>
            <CardTitle>⚠ Overdue tasks</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableBody>
                {payload.overdue_tasks.map((t) => (
                  <TableRow key={t.id}>
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
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Today&apos;s tasks</CardTitle>
          </CardHeader>
          <CardContent>
            {payload.todays_tasks.length === 0 ? (
              <p className="text-muted-foreground">Nothing due today.</p>
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
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Kiddings due in 14 days</CardTitle>
          </CardHeader>
          <CardContent>
            {payload.kiddings_due.length === 0 ? (
              <p className="text-muted-foreground">None.</p>
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
                        <Link
                          href={`/kidding/new?breeding_id=${r.id}`}
                          className={buttonVariants({ variant: "outline", size: "sm" })}
                        >
                          Record
                        </Link>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Ultrasounds due in 7 days</CardTitle>
          </CardHeader>
          <CardContent>
            {payload.ultrasounds_due.length === 0 ? (
              <p className="text-muted-foreground">None.</p>
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
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Ready to move ({payload.suggestions.length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {payload.suggestions.length === 0 ? (
              <p className="text-muted-foreground">No suggestions.</p>
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
              <p className="text-destructive">
                ⚠ {payload.cull_candidates.length} cull candidate(s) —{" "}
                <Link href="/breeding" className="underline">
                  see breeding page
                </Link>
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Herd by bucket</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {payload.buckets.map((b) => (
            <Link
              key={b.code}
              href={`/animals?bucket=${encodeURIComponent(b.code)}`}
              className="rounded-xl bg-card p-4 ring-1 ring-foreground/10 transition hover:ring-primary"
            >
              <div className="text-2xl font-semibold">{b.count}</div>
              <div className="text-sm">{b.name}</div>
              <div className="text-xs text-muted-foreground">{b.code}</div>
            </Link>
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Recent weight records</h2>
        {payload.recent_weights.length === 0 ? (
          <p className="text-muted-foreground">
            No weight records yet.{" "}
            <Link href="/animals/new" className="text-primary underline">
              Add your first animal
            </Link>{" "}
            to get started.
          </p>
        ) : (
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
        )}
      </section>
    </div>
  );
}
