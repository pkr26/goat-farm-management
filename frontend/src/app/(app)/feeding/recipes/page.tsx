"use client";

/** Feed recipes with ingredient lines + bucket→recipe allocation reference
 * (parity with v1 feeding/recipes.html). Mixing batches lives on the inventory page. */

import { Wheat } from "lucide-react";
import Link from "next/link";

import { useListRecipesApiFeedingRecipesGet } from "@/api/generated/endpoints";
import { DataTableCard } from "@/components/data-table-card";
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

function FeedingNav({ active }: { active: string }) {
  const tabs = [
    { href: "/feeding", label: "Today's plan" },
    { href: "/feeding/recipes", label: "Recipes" },
    { href: "/feeding/inventory", label: "Inventory" },
  ];
  return (
    <nav className="flex flex-wrap gap-1 border-b text-sm">
      {tabs.map((t) => (
        <Link
          key={t.href}
          href={t.href}
          className={
            t.label === active
              ? "-mb-px border-b-2 border-primary px-3 py-2 font-medium text-foreground"
              : "-mb-px border-b-2 border-transparent px-3 py-2 text-muted-foreground hover:text-foreground"
          }
        >
          {t.label}
        </Link>
      ))}
    </nav>
  );
}

export default function RecipesPage() {
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("feeding.view");

  const query = useListRecipesApiFeedingRecipesGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

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
  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError ? query.error.detail : "Could not load recipes."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="TMR recipes"
        description="Total mixed ration formulas and the bucket each one feeds."
      />

      <FeedingNav active="Recipes" />

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

      <DataTableCard title={<h2>Bucket → recipe allocation (reference)</h2>}>
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
                  <Badge variant="secondary">{row.bucket}</Badge>
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
