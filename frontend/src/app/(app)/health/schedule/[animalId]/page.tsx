"use client";

/** Per-animal vaccination schedule — parity with v1's health/schedule.html. */

import { CalendarClock, Syringe } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { useVaccinationScheduleApiHealthScheduleAnimalIdGet } from "@/api/generated/endpoints";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
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
import { formatDate } from "@/lib/format";
import { permittedAppPath, withReturnTo } from "@/lib/permission-navigation";
import { usePermissions } from "@/lib/use-permissions";

/** Status pill: DONE in emerald, UPCOMING in amber, OVERDUE in red, anything
 *  else neutral. The raw status string stays visible for parity with the API. */
function ScheduleStatusBadge({ status }: { status: string }) {
  switch (status) {
    case "DONE":
      return (
        <Badge
          variant="outline"
          className="border-transparent bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
        >
          <Syringe />
          DONE
        </Badge>
      );
    case "UPCOMING":
      return (
        <Badge
          variant="outline"
          className="border-transparent bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
        >
          <CalendarClock />
          UPCOMING
        </Badge>
      );
    case "OVERDUE":
      return (
        <Badge
          variant="outline"
          className="border-transparent bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
        >
          <CalendarClock />
          OVERDUE
        </Badge>
      );
    default:
      return <Badge variant="secondary">{status}</Badge>;
  }
}

/** Subtle row tint so due/overdue vaccines stand out at a glance. */
function rowTint(status: string): string | undefined {
  switch (status) {
    case "OVERDUE":
      return "bg-red-50 dark:bg-red-950/20";
    case "UPCOMING":
      return "bg-amber-50 dark:bg-amber-950/20";
    default:
      return undefined;
  }
}

function dateOrDash(value: string | null): string {
  return value ? formatDate(value) : "—";
}

function VaccinationSchedulePageContent() {
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("health.view");
  const canManage = can("health.manage");
  const canViewAnimals = can("animals.view");
  const params = useParams<{ animalId: string }>();
  const searchParams = useSearchParams();
  const animalId = Number(params.animalId);
  const validId = Number.isInteger(animalId) && animalId > 0;

  const query = useVaccinationScheduleApiHealthScheduleAnimalIdGet(animalId, {
    query: { enabled: allowed && validId },
  });
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const returnTo =
    permittedAppPath(searchParams.get("returnTo"), can) ??
    `/health?schedule_animal_id=${encodeURIComponent(params.animalId)}`;

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
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
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
                + Add event
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
          />
        ) : (
          <Table>
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
    <Suspense fallback={<p className="py-10 text-center text-muted-foreground">Loading…</p>}>
      <VaccinationSchedulePageContent />
    </Suspense>
  );
}
