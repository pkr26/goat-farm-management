"use client";

/** Bucket board — card per bucket with daily kg/head + ACTIVE animals. Parity with v1's buckets.html. */

import Link from "next/link";

import { Layers } from "lucide-react";

import { useBucketsBoardApiBucketsGet } from "@/api/generated/endpoints";
import type { BucketBoardRow } from "@/api/generated/models";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
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
import { usePermissions } from "@/lib/use-permissions";

function BucketCard({ row, canViewAnimals }: { row: BucketBoardRow; canViewAnimals: boolean }) {
  const truncated = row.animals.length < row.animals_total;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span>{row.name}</span>
          <Badge variant="secondary">{row.animals_total} head</Badge>
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          {row.bucket} · {row.daily_kg_per_head.toFixed(1)} kg/head/day · {row.who}
        </p>
        {row.exit_rule && (
          <p className="text-xs text-muted-foreground">Exit: {row.exit_rule}</p>
        )}
      </CardHeader>
      <CardContent>
        {row.animals.length === 0 ? (
          <p className="text-muted-foreground">No animals in this bucket.</p>
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
              <Link href={row.animals_page_path} className="text-primary underline">
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
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("buckets.view");
  const canViewAnimals = can("animals.view");
  const query = useBucketsBoardApiBucketsGet({ query: { enabled: allowed } });
  const rows = query.data?.status === 200 ? query.data.data : undefined;

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

  return (
    <div className="space-y-6">
      <PageHeader
        title="Buckets"
        description="Daily feed plan and occupancy per bucket."
      />
      {query.isLoading ? (
        <p className="py-10 text-center text-muted-foreground">Loading…</p>
      ) : query.isError ? (
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError ? query.error.detail : "Could not load buckets."}
        </p>
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
