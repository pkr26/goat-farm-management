"use client";

/** Per-animal vaccination schedule — parity with v1's health/schedule.html. */

import { CalendarClock, Plus, Syringe } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { useVaccinationScheduleApiHealthScheduleAnimalIdGet } from "@/api/generated/endpoints";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
import { PageSkeleton, TableSkeleton } from "@/components/skeletons";
import { StatusBadge } from "@/components/status-badge";
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
import { formatDate } from "@/lib/format";
import { useT } from "@/lib/i18n";
import { permittedAppPath, withReturnTo } from "@/lib/permission-navigation";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";

/** Status chip: the shared StatusBadge resolves DONE/UPCOMING/OVERDUE to
 *  success/warning/destructive tints; labels are humanized here. */
function ScheduleStatusBadge({ status }: { status: string }) {
  const t = useT();
  switch (status) {
    case "DONE":
      return (
        <StatusBadge status={status}>
          <Syringe aria-hidden="true" /> {t("health.schedule.statusDone")}
        </StatusBadge>
      );
    case "UPCOMING":
      return (
        <StatusBadge status={status}>
          <CalendarClock aria-hidden="true" /> {t("health.schedule.statusUpcoming")}
        </StatusBadge>
      );
    case "OVERDUE":
      return (
        <StatusBadge status={status}>
          <CalendarClock aria-hidden="true" /> {t("health.schedule.statusOverdue")}
        </StatusBadge>
      );
    default:
      return <StatusBadge status={status} />;
  }
}

/** Subtle row tint so overdue vaccines stand out at a glance. */
function rowTint(status: string): string | undefined {
  return status === "OVERDUE" ? "bg-destructive/[0.04]" : undefined;
}

function dateOrDash(value: string | null): string {
  return value ? formatDate(value) : "—";
}

function VaccinationSchedulePageContent({ perms }: { perms: PermissionsState }) {
  const { can } = perms;
  // 2026-09-17 audit (M-12): this page bypassed the i18n layer entirely —
  // every visible string now resolves through health.schedule.* keys.
  const t = useT();
  const allowed = can("health.view");
  const canManage = can("health.manage");
  const canViewAnimals = can("animals.view");
  const params = useParams<{ animalId: string }>();
  const searchParams = useSearchParams();
  const animalId = Number(params.animalId);
  const validId =
    /^\d+$/.test(params.animalId) &&
    Number.isSafeInteger(animalId) &&
    animalId > 0;

  const query = useVaccinationScheduleApiHealthScheduleAnimalIdGet(animalId, {
    query: { enabled: allowed && validId },
  });
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const returnTo =
    permittedAppPath(searchParams.get("returnTo"), can) ??
    `/health?schedule_animal_id=${encodeURIComponent(params.animalId)}`;

  if (!validId) {
    return <p className="text-sm text-destructive">{t("health.schedule.invalidId")}</p>;
  }
  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3 rounded-lg border border-destructive/40 p-4">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError
              ? query.error.detail
              : t("health.schedule.loadFailed")}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            {t("health.schedule.retry")}
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("health.schedule.pageTitle")}
          description={t("health.schedule.pageDescription")}
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("health.schedule.loading")}</span>
          <TableSkeleton rows={6} columns={6} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title={
          <>
            {t("health.schedule.pageTitle")} —{" "}
            {canViewAnimals ? (
              <Link href={`/animals/${payload.animal_id}`} className="text-primary underline">
                {t("health.schedule.animalLabel", { id: payload.animal_id })}
              </Link>
            ) : (
              t("health.schedule.animalLabel", { id: payload.animal_id })
            )}
          </>
        }
        description={t("health.schedule.pageDescription")}
        actions={
          <>
            <Link href={returnTo} className={buttonVariants({ variant: "outline" })}>
              {t("health.schedule.backToHealth")}
            </Link>
            {canManage && (
              <Link
                href={withReturnTo(
                  `/health/new?animal_id=${payload.animal_id}`,
                  `/health/schedule/${payload.animal_id}?returnTo=${encodeURIComponent(returnTo)}`,
                )}
                className={buttonVariants()}
              >
                <Plus aria-hidden="true" /> {t("health.addEvent")}
              </Link>
            )}
          </>
        }
      />

      <DataTableCard title={t("health.schedule.tableTitle")}>
        {payload.rows.length === 0 ? (
          <EmptyState
            icon={Syringe}
            title={t("health.schedule.emptyTitle")}
            description={t("health.schedule.emptyDescription")}
          >
            {/* Real deep link: /health/new forwards ?animal_id= to the record
             * dialog on /health, so the CTA carries this animal's context. */}
            {canManage && (
              <Link
                href={withReturnTo(
                  `/health/new?animal_id=${payload.animal_id}`,
                  `/health/schedule/${payload.animal_id}?returnTo=${encodeURIComponent(returnTo)}`,
                )}
                className={buttonVariants({ size: "sm" })}
              >
                {t("health.schedule.recordEvent")}
              </Link>
            )}
          </EmptyState>
        ) : (
          <>
          {/* Below md the 6-column schedule becomes a card per vaccine —
           * due dates and the status chip must read on the phone without
           * panning a 640px table. */}
          <div className="space-y-2 md:hidden">
            {payload.rows.map((row) => (
              <div
                key={row.template_id}
                className={`space-y-1.5 rounded-xl border bg-card p-3 shadow-xs${rowTint(row.status) ? ` ${rowTint(row.status)}` : ""}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{row.template_name}</span>
                  <ScheduleStatusBadge status={row.status} />
                </div>
                {row.timing_note && (
                  <p className="text-xs text-muted-foreground">{row.timing_note}</p>
                )}
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                  <div className="flex items-baseline justify-between gap-2">
                    <dt className="text-muted-foreground">{t("health.schedule.colFirstDue")}</dt>
                    <dd>{dateOrDash(row.first_due)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-2">
                    <dt className="text-muted-foreground">{t("health.schedule.colBoosterDue")}</dt>
                    <dd>{dateOrDash(row.booster_due)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-2">
                    <dt className="text-muted-foreground">{t("health.schedule.colLastDone")}</dt>
                    <dd>{dateOrDash(row.last_done)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-2">
                    <dt className="text-muted-foreground">{t("health.schedule.colNextDue")}</dt>
                    <dd>{dateOrDash(row.next_due)}</dd>
                  </div>
                </dl>
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[640px]">
            <TableHeader>
              <TableRow>
                <TableHead>{t("health.schedule.colVaccine")}</TableHead>
                <TableHead>{t("health.schedule.colFirstDue")}</TableHead>
                <TableHead>{t("health.schedule.colBoosterDue")}</TableHead>
                <TableHead>{t("health.schedule.colLastDone")}</TableHead>
                <TableHead>{t("health.schedule.colNextDue")}</TableHead>
                <TableHead>{t("health.schedule.colStatus")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.rows.map((row) => (
                <TableRow key={row.template_id} className={rowTint(row.status)}>
                  <TableCell>
                    {row.template_name}
                    {row.timing_note && (
                      <>
                        <br />
                        <span className="text-xs text-muted-foreground">{row.timing_note}</span>
                      </>
                    )}
                  </TableCell>
                  <TableCell>{dateOrDash(row.first_due)}</TableCell>
                  <TableCell>{dateOrDash(row.booster_due)}</TableCell>
                  <TableCell>{dateOrDash(row.last_done)}</TableCell>
                  <TableCell>{dateOrDash(row.next_due)}</TableCell>
                  <TableCell>
                    <ScheduleStatusBadge status={row.status} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          </>
        )}
      </DataTableCard>
    </div>
  );
}

export default function VaccinationSchedulePage() {
  const perms = usePermissions();
  // Same M-12 fix (2026-09-17 audit): the boundary chrome must follow the
  // worker's language too, not just the loaded content.
  const t = useT();
  return (
    <Suspense
      fallback={
        <div className="space-y-6" role="status" aria-live="polite">
          <PageHeader
            title={t("health.schedule.pageTitle")}
            description={t("health.schedule.pageDescription")}
          />
          <span className="sr-only">{t("health.schedule.loading")}</span>
          <PageSkeleton cards={1} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="health.view"
        label={t("health.schedule.pageTitle")}
        description={t("health.schedule.pageDescription")}
        cards={1}
      >
        <VaccinationSchedulePageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
