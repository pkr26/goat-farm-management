"use client";

/** Herd reports: summary, breeding performance, mortality — parity with v1's reports.html. */

import Link from "next/link";
import type { ReactNode } from "react";

import { useReportsApiDashboardReportsGet } from "@/api/generated/endpoints";
import type { AnimalOut } from "@/api/generated/models";
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
import { usePermissions } from "@/lib/use-permissions";

/** v1's Animal.display_name: tag plus optional name. */
function animalName(a: AnimalOut): string {
  return a.tag_number + (a.name ? ` · ${a.name}` : "");
}

/** Percentages arrive as 0–100 numbers; null means "not enough data". */
function pct(value: number | null): string {
  return value === null ? "—" : `${value}%`;
}

function SummaryRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <TableRow>
      <TableCell>{label}</TableCell>
      <TableCell className="text-right">{value}</TableCell>
    </TableRow>
  );
}

export default function ReportsPage() {
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("reports.view");
  const query = useReportsApiDashboardReportsGet({ query: { enabled: allowed } });
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
          {query.error instanceof ApiError ? query.error.detail : "Could not load the reports."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  const { breeding, mortality } = payload;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Reports</h1>

      <Card>
        <CardHeader>
          <CardTitle>Herd summary ({payload.total_active} active)</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 lg:grid-cols-2">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Bucket</TableHead>
                <TableHead>Heads</TableHead>
                <TableHead>Avg weight</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.bucket_rows.map((row) => (
                <TableRow key={row.code}>
                  <TableCell>
                    {row.name}{" "}
                    <span className="text-xs text-muted-foreground">{row.code}</span>
                  </TableCell>
                  <TableCell>{row.count}</TableCell>
                  <TableCell>
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
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Breeding performance</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableBody>
              <SummaryRow label="Breeding records" value={breeding.total_records} />
              <SummaryRow
                label="Conception rate (confirmed / completed)"
                value={pct(breeding.conception_rate)}
              />
              <SummaryRow label="First-cycle success" value={pct(breeding.first_cycle_rate)} />
              <SummaryRow label="Kiddings recorded" value={breeding.kiddings} />
              <SummaryRow
                label="Alive kids per kidding"
                value={breeding.kids_per_kidding ?? "—"}
              />
              <SummaryRow label="Twin rate (≥2 kids)" value={pct(breeding.twin_rate)} />
              <TableRow>
                <TableCell>Cull candidates</TableCell>
                <TableCell className="text-right">
                  {breeding.cull_candidates.length}
                  {breeding.cull_candidates.length > 0 && (
                    <span className="ml-2 inline-flex flex-wrap gap-1">
                      {breeding.cull_candidates.map((a) => (
                        <Link
                          key={a.id}
                          href={`/animals/${a.id}`}
                          className="rounded-full border px-2 py-0.5 text-xs hover:bg-muted"
                        >
                          {animalName(a)}
                        </Link>
                      ))}
                    </span>
                  )}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Mortality</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 lg:grid-cols-2">
          <Table>
            <TableBody>
              <SummaryRow label="Total deaths (herd)" value={mortality.total_deaths} />
              <SummaryRow label="Kids born (recorded)" value={mortality.total_kids_born} />
              <SummaryRow label="Stillborn" value={mortality.stillborn} />
              <SummaryRow label="Stillborn rate" value={pct(mortality.stillborn_rate)} />
            </TableBody>
          </Table>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Month</TableHead>
                <TableHead>Deaths</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {mortality.deaths_by_month.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={2} className="text-muted-foreground">
                    No deaths recorded.
                  </TableCell>
                </TableRow>
              ) : (
                mortality.deaths_by_month.map(([month, count]) => (
                  <TableRow key={month}>
                    <TableCell>{month}</TableCell>
                    <TableCell>{count}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <p>
        <Link href="/finance" className={buttonVariants({ variant: "outline" })}>
          Financial summary →
        </Link>
      </p>
    </div>
  );
}
