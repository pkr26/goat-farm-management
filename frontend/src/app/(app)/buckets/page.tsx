"use client";

/** Bucket board — card per bucket with daily kg/head + ACTIVE animals. Parity with v1's buckets.html. */

import Link from "next/link";

import { Boxes, Layers } from "lucide-react";

import { useBucketsBoardApiBucketsGet } from "@/api/generated/endpoints";
import type { BucketBoardRow } from "@/api/generated/models";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { PageSkeleton } from "@/components/skeletons";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import { useEnumLabel } from "@/lib/enum-labels";
import { safeAppPath } from "@/lib/utils";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";

/** The per-head ration is a setting the operator typed and will check against
 * what they configured, and the API stores/dispenses it at 0.001 kg. Render
 * every digit that survives storage and trim trailing zeros: `toFixed(1)`
 * reported a bucket set to 1.25 kg/head as "1.2". */
const rationFormat = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 });

/** The register link is backend-supplied: safeAppPath rejects foreign
 * origins, smuggled encodings and canonicalization drift, and this module
 * check additionally keeps the destination inside the animals register even
 * if the generator ever emitted another same-origin path — any other value
 * falls back to the plain bucket filter (RT-P6-1). */
function animalsRegisterPath(raw: string | null | undefined, bucket: string): string {
  const safe = safeAppPath(raw);
  if (safe) {
    const path = safe.split(/[?#]/, 1)[0];
    if (path === "/animals" || path.startsWith("/animals/")) return safe;
  }
  return `/animals?bucket=${encodeURIComponent(bucket)}`;
}

function BucketCard({ row, canViewAnimals }: { row: BucketBoardRow; canViewAnimals: boolean }) {
  const enumLabel = useEnumLabel();
  const truncated = row.animals.length < row.animals_total;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span>{row.name}</span>
          <Badge variant="secondary">{row.animals_total} head</Badge>
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          {enumLabel("bucket", row.bucket)} · {rationFormat.format(row.daily_kg_per_head)} kg/head/day · {row.who}
        </p>
        {row.exit_rule && (
          <p className="text-xs text-muted-foreground">Exit: {row.exit_rule}</p>
        )}
      </CardHeader>
      <CardContent>
        {row.animals.length === 0 ? (
          <EmptyState
            className="py-8"
            icon={Boxes}
            title="No animals in this bucket."
            description="Animals appear here as they are moved into this bucket from the herd register."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tag</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Sex</TableHead>
                <TableHead className="text-right">Weight</TableHead>
                <TableHead className="text-right">Days in bucket</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {row.animals.map((a) => (
                <TableRow key={a.id}>
                  <TableCell>
                    {canViewAnimals ? (
                      <Link href={`/animals/${a.id}`} className="text-primary underline">
                        {a.tag_number}
                      </Link>
                    ) : (
                      a.tag_number
                    )}
                  </TableCell>
                  <TableCell>{a.name ?? "—"}</TableCell>
                  <TableCell>{a.sex}</TableCell>
                  <TableCell className="text-right">
                    {a.latest_weight_kg != null ? `${a.latest_weight_kg.toFixed(1)} kg` : "—"}
                  </TableCell>
                  <TableCell className="text-right">{a.days_in_current_bucket ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {truncated && (
          <p className="mt-3 text-sm text-muted-foreground">
            Showing {row.animals.length} of {row.animals_total} animals.{" "}
            {canViewAnimals ? (
              <Link
                href={animalsRegisterPath(row.animals_page_path, row.bucket)}
                className="text-primary underline"
              >
                View the full bucket register
              </Link>
            ) : (
              "The full register requires animal access."
            )}
            <span className="block text-xs">
              Board preview limit: {row.animals_limit} animals.
            </span>
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export default function BucketsPage() {
  const perms = usePermissions();
  return (
    <PermissionGate
      perms={perms}
      perm="buckets.view"
      label="Buckets"
      description="Daily feed plan and occupancy per bucket."
      cards={2}
    >
      <BucketsPageContent perms={perms} />
    </PermissionGate>
  );
}

function BucketsPageContent({ perms }: { perms: PermissionsState }) {
  const { can } = perms;
  const allowed = can("buckets.view");
  const canViewAnimals = can("animals.view");
  // Stryker disable next-line ObjectLiteral: PermissionGate refuses to mount this page without buckets.view, so `allowed` is always true by the time this hook runs
  const query = useBucketsBoardApiBucketsGet({ query: { enabled: allowed } });
  const rows = query.data?.status === 200 ? query.data.data : undefined;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Buckets"
        description="Daily feed plan and occupancy per bucket."
      />
      {query.isLoading ? (
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading buckets…</span>
          <PageSkeleton cards={2} />
        </div>
      ) : query.isError ? (
        <div role="alert" className="space-y-3">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : "Could not load buckets."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry buckets
          </Button>
        </div>
      ) : !rows || rows.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="No buckets configured."
          description="Buckets group animals by life stage and set their daily feed allowance."
        />
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {rows.map((row) => (
            <BucketCard key={row.bucket} row={row} canViewAnimals={canViewAnimals} />
          ))}
        </div>
      )}
    </div>
  );
}
