"use client";

/** Feed recipes with ingredient lines + bucket→recipe allocation reference
 * (parity with v1 feeding/recipes.html). Mixing batches lives on the inventory page. */

import Link from "next/link";

import { useListRecipesApiFeedingRecipesGet } from "@/api/generated/endpoints";
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
    <nav className="flex gap-4 border-b pb-2 text-sm">
      {tabs.map((t) => (
        <Link
          key={t.href}
          href={t.href}
          className={
            t.label === active
              ? "font-semibold text-foreground"
              : "text-muted-foreground hover:text-foreground"
          }
        >
          {t.label}
        </Link>
      ))}
    </nav>
  );
}

export default function RecipesPage() {
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("feeding.view");

  const query = useListRecipesApiFeedingRecipesGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  if (permsLoading) {
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
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
      <h1 className="text-2xl font-semibold">TMR recipes</h1>

      <FeedingNav active="Recipes" />

      <div className="grid gap-4 lg:grid-cols-2">
        {payload.recipes.map((recipe) => (
          <Card key={recipe.id}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
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
                    <TableHead>kg / 100 kg</TableHead>
                    <TableHead>Category</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(recipe.lines ?? []).map((line) => (
                    <TableRow key={line.ingredient}>
                      <TableCell>{line.ingredient}</TableCell>
                      <TableCell>{line.kg_per_100kg}</TableCell>
                      <TableCell>{line.category}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ))}
      </div>

      <section className="space-y-2">
        <h2 className="text-lg font-medium">Bucket → recipe allocation (reference)</h2>
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
      </section>
    </div>
  );
}
