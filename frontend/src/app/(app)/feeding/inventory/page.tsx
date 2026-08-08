"use client";

/** Feed inventory — stock levels, add-stock per row, mix a recipe batch
 * (parity with v1 feeding/inventory.html + the mix form from recipes.html). */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Package, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useAddStockApiFeedingInventoryItemIdAddPost,
  useListFinishedStockApiFeedingFinishedStockGet,
  useListInventoryApiFeedingInventoryGet,
  useListRecipesApiFeedingRecipesGet,
  useMixBatchApiFeedingMixPost,
} from "@/api/generated/endpoints";
import type { FeedInventoryOut } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api-client";
import { formatMoney } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
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

function mutationError(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

/** Optional non-negative number: blank → undefined (same shape as backend schemas). */
const optNum = (schema: z.ZodNumber) =>
  z.preprocess(
    (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)),
    schema.optional(),
  );

const addStockSchema = z.object({
  qty_kg: z.coerce.number().positive("Quantity must be greater than 0"),
  price_per_kg: optNum(z.number().positive("Price must be greater than 0 (or leave blank)")),
});
type AddStockInput = z.input<typeof addStockSchema>;
type AddStockValues = z.output<typeof addStockSchema>;

/** Add stock for one inventory row (feeding.manage). */
function AddStockDialog({ item }: { item: FeedInventoryOut }) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const mut = useAddStockApiFeedingInventoryItemIdAddPost();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<AddStockInput, unknown, AddStockValues>({ resolver: zodResolver(addStockSchema) });

  async function onSubmit(values: AddStockValues) {
    try {
      await mut.mutateAsync({
        itemId: item.id,
        data: { qty_kg: values.qty_kg, price_per_kg: values.price_per_kg ?? null },
      });
      toast.success(`Added ${values.qty_kg} kg of ${item.ingredient}.`);
      invalidateFarmData(queryClient);
      reset();
      setOpen(false);
    } catch (err) {
      toast.error(mutationError(err));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        Add stock
      </Button>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Add stock — {item.ingredient}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          Adding stock with a price books a FEED expense automatically.
        </p>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor={`qty-${item.id}`}>Quantity (kg) *</Label>
            <Input id={`qty-${item.id}`} type="number" step="1" min="1" {...register("qty_kg")} />
            {errors.qty_kg && <p className="text-sm text-destructive">{errors.qty_kg.message}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`price-${item.id}`}>Price per kg (₹, optional)</Label>
            <Input
              id={`price-${item.id}`}
              type="number"
              step="0.01"
              min="0"
              {...register("price_per_kg")}
            />
            {errors.price_per_kg && (
              <p className="text-sm text-destructive">{errors.price_per_kg.message}</p>
            )}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Adding…" : "Add"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

const mixSchema = z.object({
  recipe_code: z.string().min(1, "Pick a recipe"),
  batches: z.coerce
    .number()
    .int("Whole batches only")
    .min(1, "At least 1 batch")
    .max(50, "At most 50 batches"),
});
type MixInput = z.input<typeof mixSchema>;
type MixValues = z.output<typeof mixSchema>;

/** Mix recipe batches (1 batch = 100 kg), decrementing inventory (feeding.manage). */
function MixBatchDialog() {
  const [open, setOpen] = useState(false);
  const [shortage, setShortage] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const mut = useMixBatchApiFeedingMixPost();

  const recipesQuery = useListRecipesApiFeedingRecipesGet({ query: { enabled: open } });
  const recipes = recipesQuery.data?.status === 200 ? recipesQuery.data.data.recipes : [];
  /** value → label map for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const recipeItems: Record<string, string> = Object.fromEntries(
    recipes.map((r) => [r.code, `${r.name} (${r.code})`]),
  );

  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<MixInput, unknown, MixValues>({
    resolver: zodResolver(mixSchema),
    defaultValues: { recipe_code: "", batches: 1 },
  });
  const wRecipeCode = useWatch({ control, name: "recipe_code" });

  async function onSubmit(values: MixValues) {
    setShortage(null);
    try {
      await mut.mutateAsync({ data: { recipe_code: values.recipe_code, batch_kg: values.batches * 100 } });
      toast.success(`Mixed ${values.batches * 100} kg — inventory decremented.`);
      invalidateFarmData(queryClient);
      reset();
      setOpen(false);
    } catch (err) {
      // Insufficient stock comes back as a 400 with the shortage detail — show it in the dialog.
      if (err instanceof ApiError && err.status === 400) {
        setShortage(err.detail);
      } else {
        toast.error(mutationError(err));
      }
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) setShortage(null);
      }}
    >
      <Button
        variant="outline"
        onClick={() => {
          reset({ recipe_code: "", batches: 1 });
          setShortage(null);
          setOpen(true);
        }}
      >
        Mix batch
      </Button>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Mix a recipe batch</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          One batch is 100 kg of mixed feed. Stock is decremented per the recipe&apos;s ingredient
          lines.
        </p>
        {shortage && (
          <p className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
            Cannot mix — insufficient stock: {shortage}
          </p>
        )}
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          <div className="space-y-1.5">
            <Label>Recipe</Label>
            <Select value={wRecipeCode} onValueChange={(v) => setValue("recipe_code", v)} items={recipeItems}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="recipe…" />
              </SelectTrigger>
              <SelectContent>
                {recipes.map((r) => (
                  <SelectItem key={r.code} value={r.code}>
                    {r.name} ({r.code})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errors.recipe_code && (
              <p className="text-sm text-destructive">{errors.recipe_code.message}</p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="batches">Batches (1–50, each 100 kg)</Label>
            <Input id="batches" type="number" step="1" min="1" max="50" {...register("batches")} />
            {errors.batches && <p className="text-sm text-destructive">{errors.batches.message}</p>}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Mixing…" : "Mix batch"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function InventoryPage() {
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("feeding.view");
  const canManage = can("feeding.manage");

  const query = useListInventoryApiFeedingInventoryGet({ query: { enabled: allowed } });
  const items = query.data?.status === 200 ? query.data.data : undefined;
  const finishedQuery = useListFinishedStockApiFeedingFinishedStockGet({
    query: { enabled: allowed },
  });
  const finishedStock =
    finishedQuery.data?.status === 200 ? finishedQuery.data.data : undefined;

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
  if (query.isLoading || !items) {
    if (query.isError) {
      return (
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError ? query.error.detail : "Could not load feed inventory."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Feed inventory"
        description="Ingredient stock and ready-to-dispense mixed feed."
        actions={canManage && <MixBatchDialog />}
      />

      <FeedingNav active="Inventory" />

      <DataTableCard title="Stock on hand">
        {items.length === 0 ? (
          <EmptyState
            icon={Package}
            title="No feed inventory items yet."
            description="Ingredients appear here once the first feed purchase is recorded."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Category</TableHead>
                <TableHead>Ingredient</TableHead>
                <TableHead className="text-right">On hand (kg)</TableHead>
                <TableHead className="text-right">Reorder at</TableHead>
                <TableHead className="text-right">Last price / kg</TableHead>
                {canManage && <TableHead />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const low = item.reorder_level !== null && item.qty_on_hand <= item.reorder_level;
                return (
                  <TableRow
                    key={item.id}
                    className={low ? "bg-amber-50 dark:bg-amber-950/20" : undefined}
                  >
                    <TableCell>{item.category}</TableCell>
                    <TableCell className="font-medium">{item.ingredient}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {item.qty_on_hand.toFixed(1)}
                      {low && (
                        <span className="ml-2 inline-flex items-center align-middle text-amber-600 dark:text-amber-400">
                          <TriangleAlert className="size-4" />
                          <span className="sr-only">low</span>
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {item.reorder_level ?? "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMoney(item.last_purchase_price_per_kg)}
                    </TableCell>
                    {canManage && (
                      <TableCell>
                        <AddStockDialog item={item} />
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </DataTableCard>
      <DataTableCard
        title="Ready-to-dispense mixed feed"
        description="Mixing adds to these recipe balances; recording a recipe dispense deducts from them."
      >
        {finishedQuery.isLoading ? (
          <p className="py-4 text-sm text-muted-foreground">Loading mixed-feed stock…</p>
        ) : finishedQuery.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {finishedQuery.error instanceof ApiError
              ? finishedQuery.error.detail
              : "Could not load mixed-feed stock."}
          </p>
        ) : !finishedStock || finishedStock.length === 0 ? (
          <EmptyState
            icon={Package}
            title="No mixed feed is ready."
            description="Use Mix batch to turn ingredient stock into a ready recipe balance."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Recipe</TableHead>
                <TableHead>Code</TableHead>
                <TableHead className="text-right">Ready (kg)</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {finishedStock.map((stock) => (
                <TableRow key={stock.recipe_code}>
                  <TableCell className="font-medium">{stock.recipe_name}</TableCell>
                  <TableCell>{stock.recipe_code}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {stock.qty_on_hand.toFixed(1)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DataTableCard>
      <p className="text-sm text-muted-foreground">
        Mixing a recipe batch decrements stock per recipe lines. Purchases with a price book a FEED
        expense automatically.
      </p>
    </div>
  );
}
