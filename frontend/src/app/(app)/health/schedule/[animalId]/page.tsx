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
import { permittedAppPath, withReturnTo } from "@/lib/permission-navigation";
import { usePermissions } from "@/lib/use-permissions";
import { PermissionsError } from "@/components/permissions-error";

/** Status chip: the shared StatusBadge resolves DONE/UPCOMING/OVERDUE to
 *  success/warning/destructive tints; labels are humanized here. */
function ScheduleStatusBadge({ status }: { status: string }) {
  switch (status) {
    case "DONE":
      return (
        <StatusBadge status={status}>
          <Syringe aria-hidden="true" /> Done
        </StatusBadge>
      );
    case "UPCOMING":
      return (
        <StatusBadge status={status}>
          <CalendarClock aria-hidden="true" /> Upcoming
        </StatusBadge>
      );
    case "OVERDUE":
      return (
        <StatusBadge status={status}>
          <CalendarClock aria-hidden="true" /> Overdue
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

function VaccinationSchedulePageContent() {
  const { can, loading: permsLoading, isError: permsError , refetch: permsRefetch } = usePermissions();
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

  // The header stays mounted while the permission set settles — a page that
  // collapses to a bare "Loading…" line reads as a broken app on slow
  // rural connections. The animal's identity arrives with the payload.
  if (permsLoading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Vaccination schedule"
          description="Due dates and boosters from the vaccination templates that apply to this animal."
        />
        <PageSkeleton cards={1} />
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
  if (!validId) {
    return <p className="text-sm text-destructive">Invalid animal id.</p>;
  }
  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3 rounded-lg border border-destructive/40 p-4">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError
              ? query.error.detail
              : "Could not load the vaccination schedule."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry schedule
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title="Vaccination schedule"
          description="Due dates and boosters from the vaccination templates that apply to this animal."
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading vaccination schedule…</span>
          <TableSkeleton rows={6} columns={6} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <>
            Vaccination schedule —{" "}
            {canViewAnimals ? (
              <Link href={`/animals/${payload.animal_id}`} className="text-primary underline">
                Animal #{payload.animal_id}
              </Link>
            ) : (
              `Animal #${payload.animal_id}`
            )}
          </>
        }
        description="Due dates and boosters from the vaccination templates that apply to this animal."
        actions={
          <>
            <Link href={returnTo} className={buttonVariants({ variant: "outline" })}>
              Back to health log
            </Link>
            {canManage && (
              <Link
                href={withReturnTo(
                  `/health/new?animal_id=${payload.animal_id}`,
                  `/health/schedule/${payload.animal_id}?returnTo=${encodeURIComponent(returnTo)}`,
                )}
                className={buttonVariants()}
              >
                <Plus aria-hidden="true" /> Add event
              </Link>
            )}
          </>
        }
      />

      <DataTableCard title="Vaccines & boosters">
        {payload.rows.length === 0 ? (
          <EmptyState
            icon={Syringe}
            title="No vaccination templates apply to this animal."
            description="Templates are matched on the animal's bucket and age."
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
                Record a health event
              </Link>
            )}
          </EmptyState>
        ) : (
          <Table className="min-w-[640px]">
            <TableHeader>
              <TableRow>
                <TableHead>Vaccine</TableHead>
                <TableHead>First dose due</TableHead>
                <TableHead>Booster due</TableHead>
                <TableHead>Last done</TableHead>
                <TableHead>Next due</TableHead>
                <TableHead>Status</TableHead>
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
        )}
      </DataTableCard>
    </div>
  );
}

export default function VaccinationSchedulePage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6" role="status" aria-live="polite">
          <PageHeader
            title="Vaccination schedule"
            description="Due dates and boosters from the vaccination templates that apply to this animal."
          />
          <span className="sr-only">Loading vaccination schedule…</span>
          <PageSkeleton cards={1} />
        </div>
      }
    >
      <VaccinationSchedulePageContent />
    </Suspense>
  );
}
