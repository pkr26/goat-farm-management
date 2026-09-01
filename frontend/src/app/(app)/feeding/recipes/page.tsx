"use client";

/** Feed recipes with ingredient lines + bucket→recipe allocation reference
 * (parity with v1 feeding/recipes.html). Mixing batches lives on the inventory page. */

import { Wheat } from "lucide-react";

import { EmptyState } from "@/components/empty-state";

import { useListRecipesApiFeedingRecipesGet } from "@/api/generated/endpoints";
import { DataTableCard } from "@/components/data-table-card";
import { PageHeader } from "@/components/page-header";
import { PageSkeleton } from "@/components/skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { FeedingNav } from "@/components/feeding-nav";
import { useFarmType } from "@/hooks/use-farm-type";
import { enumLabel } from "@/lib/enum-labels";
import { usePermissions } from "@/lib/use-permissions";
import { PermissionsError } from "@/components/permissions-error";

export default function RecipesPage() {
  const { can, loading: permsLoading, isError: permsError , refetch: permsRefetch } = usePermissions();
  const allowed = can("feeding.view");
  const farmType = useFarmType();

  const query = useListRecipesApiFeedingRecipesGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  // The header and layout stay mounted while permissions settle — a page that
  // collapses to a bare "Loading…" line reads as a broken app on slow rural
  // connections.
  if (permsLoading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="TMR recipes"
          description="Total mixed ration formulas and the bucket each one feeds."
        />
        <PageSkeleton cards={2} />
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
  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : "Could not load recipes."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry recipes
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title="TMR recipes"
          description="Total mixed ration formulas and the bucket each one feeds."
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading recipes…</span>
          <PageSkeleton cards={2} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="TMR recipes"
        description="Total mixed ration formulas and the bucket each one feeds."
      />

      <FeedingNav active="recipes" />

      {payload.recipes.length === 0 && (
        <EmptyState
          icon={Wheat}
          title="No recipes configured."
          description="The recipe catalog is provisioned by the farm's feed setup; contact an administrator."
        />
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {payload.recipes.map((recipe) => (
          <Card key={recipe.id}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Wheat className="size-4 text-primary" />
                {recipe.name}
                <Badge variant="secondary">{recipe.code}</Badge>
              </CardTitle>
              {recipe.description && (
                <p className="text-sm text-muted-foreground">{recipe.description}</p>
              )}
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Ingredient</TableHead>
                    <TableHead className="text-right">kg / 100 kg</TableHead>
                    <TableHead>Category</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(recipe.lines ?? []).map((line) => (
                    <TableRow key={line.ingredient}>
                      <TableCell>{line.ingredient}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {line.kg_per_100kg}
                      </TableCell>
                      <TableCell>{line.category}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ))}
      </div>

      <DataTableCard title="Bucket → recipe allocation (reference)">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Bucket</TableHead>
              <TableHead>Feed</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {payload.allocation.map((row) => (
              <TableRow key={row.bucket}>
                <TableCell>
                  <Badge variant="secondary">{enumLabel("bucket", row.bucket, farmType)}</Badge>
                </TableCell>
                <TableCell>{row.allocation}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DataTableCard>
    </div>
  );
}
