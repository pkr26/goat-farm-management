"use client";

/** Feed recipes with ingredient lines + bucket→recipe allocation reference
 * (parity with v1 feeding/recipes.html). Mixing batches lives on the inventory page. */

import { Wheat } from "lucide-react";

import { EmptyState } from "@/components/empty-state";

import { useListRecipesApiFeedingRecipesGet } from "@/api/generated/endpoints";
import { DataTableCard } from "@/components/data-table-card";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
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
import { useEnumLabel } from "@/lib/enum-labels";
import { useT } from "@/lib/i18n";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";

export default function RecipesPage() {
  const perms = usePermissions();
  const t = useT();
  return (
    <PermissionGate
      perms={perms}
      perm="feeding.view"
      label={t("feedingRecipes.title")}
      description={t("feedingRecipes.description")}
      cards={2}
    >
      <RecipesPageContent perms={perms} />
    </PermissionGate>
  );
}

function RecipesPageContent({ perms }: { perms: PermissionsState }) {
  const enumLabel = useEnumLabel();
  const t = useT();
  const { can } = perms;
  const allowed = can("feeding.view");

  const query = useListRecipesApiFeedingRecipesGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError
              ? query.error.detail
              : t("feedingRecipes.loadFailed")}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            {t("feedingRecipes.retry")}
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("feedingRecipes.title")}
          description={t("feedingRecipes.description")}
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("feedingRecipes.loading")}</span>
          <PageSkeleton cards={2} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title={t("feedingRecipes.title")}
        description={t("feedingRecipes.description")}
      />

      <FeedingNav active="recipes" />

      {payload.recipes.length === 0 && (
        <EmptyState
          icon={Wheat}
          title={t("feedingRecipes.empty.title")}
          description={t("feedingRecipes.empty.description")}
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
                    <TableHead>{t("feedingRecipes.col.ingredient")}</TableHead>
                    <TableHead className="text-right">{t("feedingRecipes.col.kgPer100")}</TableHead>
                    <TableHead>{t("feedingRecipes.col.category")}</TableHead>
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

      <DataTableCard title={t("feedingRecipes.allocationTitle")}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("feedingRecipes.col.bucket")}</TableHead>
              <TableHead>{t("feedingRecipes.col.feed")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {payload.allocation.map((row) => (
              <TableRow key={row.bucket}>
                <TableCell>
                  <Badge variant="secondary">{enumLabel("bucket", row.bucket)}</Badge>
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
