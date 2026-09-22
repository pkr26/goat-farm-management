"use client";

/** Herd reports: summary, breeding performance, mortality — parity with v1's reports.html. */

import Link from "next/link";
import type { ReactNode } from "react";

import { useReportsApiDashboardReportsGet } from "@/api/generated/endpoints";
import type {
  AnimalIdentityOut,
  BucketReportRow,
  ReportsOutSexCounts,
} from "@/api/generated/models";
import { Button } from "@/components/ui/button";
import { DataTableCard } from "@/components/data-table-card";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
import { PageSkeleton } from "@/components/skeletons";
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
import { enumLabel } from "@/lib/enum-labels";
import { useLanguage, useT } from "@/lib/i18n";
import { withReturnTo } from "@/lib/permission-navigation";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { badgeVariants } from "@/components/ui/badge";

/** v1's Animal.display_name: tag plus optional name. */
function animalName(a: AnimalIdentityOut): string {
  return a.tag_number + (a.name ? ` · ${a.name}` : "");
}

/** Percentages arrive as 0–100 numbers; null means "not enough data".
 * One decimal — raw floats rendered "33.33333333333333". */
function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 10) / 10}%`;
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
  const t = useT();
  return <span className="text-muted-foreground">{t("reports.withheld", { permission })}</span>;
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
  const perms = usePermissions();
  const t = useT();
  return (
    <PermissionGate
      perms={perms}
      perm="reports.view"
      label={t("reports.title")}
      description={t("reports.description")}
      cards={2}
    >
      <ReportsPageContent perms={perms} />
    </PermissionGate>
  );
}

function ReportsPageContent({ perms }: { perms: PermissionsState }) {
  const { language } = useLanguage();
  const t = useT();
  const { can } = perms;
  const allowed = can("reports.view");
  const canViewAnimals = can("animals.view");
  const canViewHealth = can("health.view");
  const canViewBreeding = can("breeding.view");
  const query = useReportsApiDashboardReportsGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : t("reports.loadFailed")}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            {t("reports.retry")}
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("reports.title")}
          description={t("reports.description")}
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("reports.loading")}</span>
          <PageSkeleton cards={2} />
        </div>
      </div>
    );
  }

  const { breeding, mortality } = payload;
  // Withheld-sentinel reads: the API nulls the bucket/animal aggregates
  // without animals.view (the generated client types lag the contract, so
  // widen before comparing — the casts become no-ops once orval regenerates).
  const bucketRows = payload.bucket_rows as BucketReportRow[] | null;
  const totalActive = payload.total_active as number | null;
  const sexCounts = payload.sex_counts as ReportsOutSexCounts | null;
  // `can(...)` reads /api/auth/permissions, a different query that
  // invalidateFarmData deliberately excludes, so it can disagree with the
  // report payload for as long as staleTime allows. Trust either source
  // saying "withheld": the sentinel alone would show stale privileged rows
  // after a revocation, and the permission alone lets a withheld payload
  // render as the factual "No deaths recorded."
  const healthWithheld = mortality.total_deaths === null || !canViewHealth;
  const breedingWithheld = breeding.cull_candidates_total === null || !canViewBreeding;
  const animalsWithheld = totalActive === null || !canViewAnimals;

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title={t("reports.title")}
        description={t("reports.description")}
        actions={can("finance.view") ? (
          <Link href="/finance" className={buttonVariants({ variant: "outline" })}>
            {t("reports.financialSummary")}
          </Link>
        ) : undefined}
      />

      <DataTableCard
        title={
          animalsWithheld ? t("reports.summary.title") : t("reports.summary.titleCount", { count: totalActive ?? 0 })
        }
        description={t("reports.summary.description")}
        contentClassName="grid gap-4 lg:grid-cols-2"
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("reports.col.bucket")}</TableHead>
              <TableHead className="text-right">{t("reports.col.heads")}</TableHead>
              <TableHead className="text-right">{t("reports.col.avgWeight")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {/* null = withheld (no animals access): the bucket rows must name
                the withholding instead of rendering an empty register. */}
            {animalsWithheld || bucketRows === null ? (
              <TableRow>
                <TableCell colSpan={3} className="text-muted-foreground">
                  <Withheld permission="animals" />
                </TableCell>
              </TableRow>
            ) : (
              bucketRows.map((row) => (
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
              ))
            )}
          </TableBody>
        </Table>
        <Table>
          <TableBody>
            <SummaryRow
              label={t("reports.summary.femalesActive")}
              value={
                !animalsWithheld && sexCounts !== null ? (sexCounts.F ?? 0) : <Withheld permission="animals" />
              }
            />
            <SummaryRow
              label={t("reports.summary.malesActive")}
              value={
                !animalsWithheld && sexCounts !== null ? (sexCounts.M ?? 0) : <Withheld permission="animals" />
              }
            />
            {Object.entries(payload.status_counts).map(([status, count]) => (
              <SummaryRow
                  key={status}
                  label={`${enumLabel("status", status, language)} ${t("reports.summary.allTimeSuffix")}`}
                  value={count}
                />
            ))}
            {/* Keep the withheld clinical rows visible as withheld instead of
                letting them vanish silently from the table. */}
            {healthWithheld &&
              CLINICAL_OUTCOME_STATUSES.filter(
                (status) => !(status in payload.status_counts),
              ).map((status) => (
                <SummaryRow
                  key={status}
                  label={`${enumLabel("status", status, language)} ${t("reports.summary.allTimeSuffix")}`}
                  value={<Withheld permission="health" />}
                />
              ))}
          </TableBody>
        </Table>
      </DataTableCard>

      <DataTableCard
        title={t("reports.breeding.title")}
        description={t("reports.breeding.description")}
      >
        <Table>
          <TableBody>
            <SummaryRow label={t("reports.breeding.records")} value={breeding.total_records} />
            {/* The API nulls these four rates without breeding.view, which is
                indistinguishable on the wire from "not enough data" — the
                meaning "—" carries here. Name the withholding instead. */}
            <SummaryRow
              label={t("reports.breeding.conceptionRate")}
              value={
                !breedingWithheld ? pct(breeding.conception_rate) : <Withheld permission="breeding" />
              }
            />
            <SummaryRow
              label={t("reports.breeding.firstCycle")}
              value={
                !breedingWithheld ? pct(breeding.first_cycle_rate) : <Withheld permission="breeding" />
              }
            />
            <SummaryRow label={t("reports.breeding.kiddingsRecorded")} value={breeding.kiddings} />
            <SummaryRow
              label={t("reports.breeding.aliveYoungPerParturition")}
              value={
                /* The same OR as its siblings: the sentinel alone (stale
                   permission cache) or the revoked permission alone (stale
                   payload) must both read as withheld, never as the
                   not-enough-data "—" (RT-P2-2). */
                !breedingWithheld
                  ? (breeding.kids_per_kidding ?? "—")
                  : <Withheld permission="breeding" />
              }
            />
            <SummaryRow
              label={t("reports.breeding.twinRate")}
              value={!breedingWithheld ? pct(breeding.twin_rate) : <Withheld permission="breeding" />}
            />
            <TableRow>
              <TableCell>{t("reports.breeding.cullCandidates")}</TableCell>
              <TableCell className="text-right tabular-nums">
                {/* null = withheld (no breeding access) — never render it as a 0 count */}
                {breeding.cull_candidates_total ?? (
                  <span className="text-muted-foreground">{t("reports.withheld", { permission: "breeding" })}</span>
                )}
                {breeding.cull_candidates.length > 0 && (
                  <span className="ml-2 inline-flex flex-wrap gap-1">
                    {breeding.cull_candidates.map((a) =>
                      canViewAnimals ? (
                        <Link
                          key={a.id}
                          href={withReturnTo(`/animals/${a.id}`, "/reports")}
                          className={badgeVariants({ variant: "warning" })}
                        >
                          {animalName(a)}
                        </Link>
                      ) : (
                        <span
                          key={a.id}
                          className={badgeVariants({ variant: "warning" })}
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
                    {t("reports.breeding.showingCapped", {
                      shown: breeding.cull_candidates.length,
                      total: breeding.cull_candidates_total,
                      limit: breeding.cull_candidates_limit,
                    })}{" "}
                    {can("breeding.view") ? (
                      <Link href="/breeding" className="underline">
                        {t("reports.breeding.reviewFull")}
                      </Link>
                    ) : (
                      t("reports.breeding.fullListWithheld")
                    )}
                  </span>
                )}
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </DataTableCard>

      <DataTableCard
        title={t("reports.mortality.title")}
        description={t("reports.mortality.description")}
        contentClassName="grid gap-4 lg:grid-cols-2"
      >
        <Table>
          <TableBody>
            {/* null = withheld (no health access); 0 is a real count, so only
                null/undefined may fall through to the withheld marker. */}
            <SummaryRow
              label={t("reports.mortality.totalDeaths")}
              value={mortality.total_deaths ?? <Withheld permission="health" />}
            />
            <SummaryRow
              label={t("reports.mortality.kidsBorn")}
              value={mortality.total_kids_born}
            />
            <SummaryRow
              label={t("reports.mortality.stillborn")}
              value={mortality.stillborn ?? <Withheld permission="health" />}
            />
            <SummaryRow
              label={t("reports.mortality.stillbornRate")}
              value={
                !healthWithheld ? pct(mortality.stillborn_rate) : <Withheld permission="health" />
              }
            />
          </TableBody>
        </Table>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("reports.mortality.month")}</TableHead>
              <TableHead className="text-right">{t("reports.mortality.deaths")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {mortality.deaths_by_month.length === 0 ? (
              <TableRow>
                <TableCell colSpan={2} className="text-muted-foreground">
                  {/* The API sends an empty list when it withholds the monthly
                      breakdown — don't claim there were no deaths. */}
                  {!healthWithheld ? t("reports.mortality.noDeaths") : <Withheld permission="health" />}
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
