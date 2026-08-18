"use client";

/** Herd reports: summary, breeding performance, mortality — parity with v1's reports.html. */

import Link from "next/link";
import type { ReactNode } from "react";

import { useReportsApiDashboardReportsGet } from "@/api/generated/endpoints";
import type { AnimalIdentityOut } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { PageHeader } from "@/components/page-header";
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
import { usePermissions } from "@/lib/use-permissions";

/** v1's Animal.display_name: tag plus optional name. */
function animalName(a: AnimalIdentityOut): string {
  return a.tag_number + (a.name ? ` · ${a.name}` : "");
}

/** Percentages arrive as 0–100 numbers; null means "not enough data". */
function pct(value: number | null): string {
  return value === null ? "—" : `${value}%`;
}

/** Clinical outcomes: the API leaves these out of `status_counts` for viewers
 * without health.view (dashboard.py `_CLINICAL_OUTCOME_STATUSES`), the same
 * way it nulls out the mortality figures. Nothing here may assume they are
 * present — a permitted farm with no such animals has no entry either. */
const CLINICAL_OUTCOME_STATUSES = ["DEAD", "CULLED"] as const;

/** A figure the API withheld for lack of a permission. Withheld is neither
 * blank (reads as a load failure) nor "—" (reads as "not enough data") nor 0
 * (a wrong number) — say which access it needs, like the cull row does. */
function Withheld({ permission }: { permission: string }) {
  return <span className="text-muted-foreground">Requires {permission} access</span>;
}

function SummaryRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <TableRow>
      <TableCell>{label}</TableCell>
      <TableCell className="text-right tabular-nums">{value}</TableCell>
    </TableRow>
  );
}

export default function ReportsPage() {
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("reports.view");
  const canViewAnimals = can("animals.view");
  const canViewHealth = can("health.view");
  const canViewBreeding = can("breeding.view");
  const query = useReportsApiDashboardReportsGet({ query: { enabled: allowed } });
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
          {query.error instanceof ApiError ? query.error.detail : "Could not load the reports."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  const { breeding, mortality } = payload;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Reports"
        description="Key herd, breeding and mortality numbers at a glance."
        actions={can("finance.view") ? (
          <Link href="/finance" className={buttonVariants({ variant: "outline" })}>
            Financial summary →
          </Link>
        ) : undefined}
      />

      <DataTableCard
        title={`Herd summary (${payload.total_active} active)`}
        description="Headcount and average weight per bucket, plus sex and status totals."
        contentClassName="grid gap-4 lg:grid-cols-2"
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Bucket</TableHead>
              <TableHead className="text-right">Heads</TableHead>
              <TableHead className="text-right">Avg weight</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {payload.bucket_rows.map((row) => (
              <TableRow key={row.code}>
                <TableCell>
                  {row.name}{" "}
                  <span className="text-xs text-muted-foreground">{row.code}</span>
                </TableCell>
                <TableCell className="text-right tabular-nums">{row.count}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {row.avg_weight !== null ? `${row.avg_weight.toFixed(1)} kg` : "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <Table>
          <TableBody>
            <SummaryRow label="Females (active)" value={payload.sex_counts.F ?? 0} />
            <SummaryRow label="Males (active)" value={payload.sex_counts.M ?? 0} />
            {Object.entries(payload.status_counts).map(([status, count]) => (
              <SummaryRow key={status} label={`${status} (all time)`} value={count} />
            ))}
            {/* Keep the withheld clinical rows visible as withheld instead of
                letting them vanish silently from the table. */}
            {!canViewHealth &&
              CLINICAL_OUTCOME_STATUSES.filter(
                (status) => !(status in payload.status_counts),
              ).map((status) => (
                <SummaryRow
                  key={status}
                  label={`${status} (all time)`}
                  value={<Withheld permission="health" />}
                />
              ))}
          </TableBody>
        </Table>
      </DataTableCard>

      <DataTableCard
        title="Breeding performance"
        description={
          "Conception, kidding and twinning rates across all breeding records. " +
          "A pregnancy confirmed by ultrasound counts as a conception even if it was later lost."
        }
      >
        <Table>
          <TableBody>
            <SummaryRow label="Breeding records" value={breeding.total_records} />
            {/* The API nulls these four rates without breeding.view, which is
                indistinguishable on the wire from "not enough data" — the
                meaning "—" carries here. Name the withholding instead. */}
            <SummaryRow
              label="Conception rate (ultrasound-confirmed / completed)"
              value={
                canViewBreeding ? pct(breeding.conception_rate) : <Withheld permission="breeding" />
              }
            />
            <SummaryRow
              label="First-cycle success"
              value={
                canViewBreeding ? pct(breeding.first_cycle_rate) : <Withheld permission="breeding" />
              }
            />
            <SummaryRow label="Kiddings recorded" value={breeding.kiddings} />
            <SummaryRow
              label="Alive kids per kidding"
              value={
                canViewBreeding
                  ? (breeding.kids_per_kidding ?? "—")
                  : <Withheld permission="breeding" />
              }
            />
            <SummaryRow
              label="Twin rate (≥2 kids)"
              value={canViewBreeding ? pct(breeding.twin_rate) : <Withheld permission="breeding" />}
            />
            <TableRow>
              <TableCell>Cull candidates</TableCell>
              <TableCell className="text-right tabular-nums">
                {/* null = withheld (no breeding access) — never render it as a 0 count */}
                {breeding.cull_candidates_total ?? (
                  <span className="text-muted-foreground">Requires breeding access</span>
                )}
                {breeding.cull_candidates.length > 0 && (
                  <span className="ml-2 inline-flex flex-wrap gap-1">
                    {breeding.cull_candidates.map((a) =>
                      canViewAnimals ? (
                        <Link
                          key={a.id}
                          href={`/animals/${a.id}`}
                          className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800 hover:bg-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:hover:bg-amber-900"
                        >
                          {animalName(a)}
                        </Link>
                      ) : (
                        <span
                          key={a.id}
                          className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                        >
                          {animalName(a)}
                        </span>
                      ),
                    )}
                  </span>
                )}
                {breeding.cull_candidates_total !== null &&
                  breeding.cull_candidates.length < breeding.cull_candidates_total && (
                  <span className="ml-2 text-xs text-muted-foreground">
                    Showing {breeding.cull_candidates.length} of {breeding.cull_candidates_total};
                    this report preview is capped at {breeding.cull_candidates_limit}.{" "}
                    {can("breeding.view") ? (
                      <Link href="/breeding" className="underline">
                        Review the full operational list
                      </Link>
                    ) : (
                      "The full list requires breeding access."
                    )}
                  </span>
                )}
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </DataTableCard>

      <DataTableCard
        title="Mortality"
        description="Deaths, stillbirths and monthly losses for the herd."
        contentClassName="grid gap-4 lg:grid-cols-2"
      >
        <Table>
          <TableBody>
            {/* null = withheld (no health access); 0 is a real count, so only
                null/undefined may fall through to the withheld marker. */}
            <SummaryRow
              label="Total deaths (herd)"
              value={mortality.total_deaths ?? <Withheld permission="health" />}
            />
            <SummaryRow label="Kids born (recorded)" value={mortality.total_kids_born} />
            <SummaryRow
              label="Stillborn"
              value={mortality.stillborn ?? <Withheld permission="health" />}
            />
            <SummaryRow
              label="Stillborn rate"
              value={
                canViewHealth ? pct(mortality.stillborn_rate) : <Withheld permission="health" />
              }
            />
          </TableBody>
        </Table>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Month</TableHead>
              <TableHead className="text-right">Deaths</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {mortality.deaths_by_month.length === 0 ? (
              <TableRow>
                <TableCell colSpan={2} className="text-muted-foreground">
                  {/* The API sends an empty list when it withholds the monthly
                      breakdown — don't claim there were no deaths. */}
                  {canViewHealth ? "No deaths recorded." : <Withheld permission="health" />}
                </TableCell>
              </TableRow>
            ) : (
              mortality.deaths_by_month.map(([month, count]) => (
                <TableRow key={month}>
                  <TableCell>{month}</TableCell>
                  <TableCell className="text-right tabular-nums">{count}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </DataTableCard>
    </div>
  );
}
