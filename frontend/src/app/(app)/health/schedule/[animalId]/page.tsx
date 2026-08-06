"use client";

/** Per-animal vaccination schedule — parity with v1's health/schedule.html. */

import Link from "next/link";
import { useParams } from "next/navigation";

import { useVaccinationScheduleApiHealthScheduleAnimalIdGet } from "@/api/generated/endpoints";
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
import { formatDate } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

function statusVariant(status: string): BadgeVariant {
  switch (status) {
    case "DONE":
      return "secondary";
    case "OVERDUE":
      return "destructive";
    case "UPCOMING":
      return "default";
    default:
      return "outline";
  }
}

function dateOrDash(value: string | null): string {
  return value ? formatDate(value) : "—";
}

export default function VaccinationSchedulePage() {
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("health.view");
  const params = useParams<{ animalId: string }>();
  const animalId = Number(params.animalId);
  const validId = Number.isInteger(animalId) && animalId > 0;

  const query = useVaccinationScheduleApiHealthScheduleAnimalIdGet(animalId, {
    query: { enabled: allowed && validId },
  });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  if (permsLoading) {
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
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
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError
            ? query.error.detail
            : "Could not load the vaccination schedule."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">
        Vaccination schedule —{" "}
        <Link href={`/animals/${payload.animal_id}`} className="text-primary underline">
          Animal #{payload.animal_id}
        </Link>
      </h1>

      {payload.rows.length === 0 ? (
        <p className="text-muted-foreground">No vaccination templates apply to this animal.</p>
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
              <TableRow key={row.template_id}>
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
                  <Badge variant={statusVariant(row.status)}>{row.status}</Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <p className="flex gap-2">
        <Link href="/health" className={buttonVariants({ variant: "outline" })}>
          Back to health log
        </Link>
        <Link href="/health" className={buttonVariants()}>
          + Add event
        </Link>
      </p>
    </div>
  );
}
