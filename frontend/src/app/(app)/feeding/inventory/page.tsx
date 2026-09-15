"use client";

/** Feed inventory — stock levels, add-stock per row, mix a recipe batch
 * (parity with v1 feeding/inventory.html + the mix form from recipes.html). */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Package, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
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
import { PermissionGate } from "@/components/permission-gate";
import { InlineLoading, PageSkeleton } from "@/components/skeletons";
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
import { mutationError } from "@/lib/mutations";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { formatMoney } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import {
  formatPersistedKg,
  isPersistableNonnegativeMoney,
  MIN_PERSISTED_KG,
  MIN_PERSISTED_KG_MESSAGE,
} from "@/lib/persisted-numbers";
import { FeedingNav } from "@/components/feeding-nav";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";


/** Optional non-negative number: blank → undefined (same shape as backend schemas). */
const optNum = (schema: z.ZodNumber) =>
  z.preprocess(
    (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)),
    schema.optional(),
  );

const addStockSchema = z
  .object({
    qty_kg: z.coerce
      .number()
      .positive("Quantity must be greater than 0")
      .min(MIN_PERSISTED_KG, MIN_PERSISTED_KG_MESSAGE)
      // Mirrors QuantityKgFloat (le=1_000_000, schemas/common.py): a fat-fingered
      // quantity should fail inline instead of as an opaque server 422.
      .max(1_000_000, "Quantity cannot exceed 1,000,000 kg"),
    price_per_kg: optNum(
      z
        .number()
        .nonnegative("Price cannot be negative")
        .max(1_000_000_000, "Price cannot exceed ₹1,000,000,000 per kg")
        .refine(
          isPersistableNonnegativeMoney,
          "Price must be ₹0 or at least ₹0.005 (or leave blank)",
        ),
    ),
  })
  .superRefine((values, ctx) => {
    if (values.price_per_kg === undefined || values.price_per_kg === 0) return;

    // Mirror the service's persisted precision closely enough to catch an
    // inevitably zero or over-limit derived expense before submission.  The
    // backend remains authoritative and performs Decimal HALF_UP arithmetic.
    const normalizedQty = Math.round(values.qty_kg * 1_000) / 1_000;
    const normalizedPrice = Math.round(values.price_per_kg * 100) / 100;
    const total = normalizedQty * normalizedPrice;
    if (total < 0.005) {
      ctx.addIssue({
        code: "custom",
        path: ["price_per_kg"],
        // The backend rounds derived expense HALF_UP to whole paise; a total
        // under half a paisa rounds to ₹0.00 and is not bookable.
        message: "Restock total is too small to round up to ₹0.01",
      });
    } else if (total > 1_000_000_000.004) {
      ctx.addIssue({
        code: "custom",
        path: ["price_per_kg"],
        message: "Restock cost cannot exceed ₹1,000,000,000",
      });
    }
  });
type AddStockInput = z.input<typeof addStockSchema>;
type AddStockValues = z.output<typeof addStockSchema>;

/** Add stock for one inventory row (feeding.manage). */
function AddStockDialog({ item, touch = false }: { item: FeedInventoryOut; touch?: boolean }) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const mut = useAddStockApiFeedingInventoryItemIdAddPost();
  const addFlight = useSingleFlight();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<AddStockInput, unknown, AddStockValues>({ resolver: zodResolver(addStockSchema) });

  async function onSubmit(values: AddStockValues) {
    await addFlight.run(async () => {
      const farmScope = captureFarmScope();
      try {
        const res = await mut.mutateAsync({
          itemId: item.id,
          data: { qty_kg: values.qty_kg, price_per_kg: values.price_per_kg ?? null },
        });
        // The API quantizes qty_kg to 3 dp (ROUND_HALF_UP) before it touches the
        // balance, so confirming the typed value would put a quantity that was
        // never stored in writing. Report the balance the server came back with.
        if (!farmScope()) return;
        toast.success(
          res.status === 200
            ? `Stock added — ${item.ingredient} is now at ${formatPersistedKg(res.data.qty_on_hand)} kg.`
            : `Stock added — ${item.ingredient}.`,
        );
        invalidateFarmData(queryClient);
        reset();
        setOpen(false);
      } catch (err) {
        if (!farmScope()) return;
        toast.error(mutationError(err));
      }
    });
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button
        size="sm"
        variant="outline"
        // ≥44px on the below-md stock cards — 36px sits under every mobile
        // touch guideline outside a compact table row.
        className={touch ? "h-11 px-4" : undefined}
        disabled={addFlight.pending}
        onClick={() => setOpen(true)}
      >
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
          <fieldset disabled={isSubmitting || addFlight.pending} className="contents">
          <div className="space-y-1.5">
            <Label htmlFor={`qty-${item.id}`}>Quantity (kg) *</Label>
            <Input
              id={`qty-${item.id}`}
              type="number"
              step="0.001"
              min="0.0005"
              aria-invalid={Boolean(errors.qty_kg) || undefined}
              aria-describedby={errors.qty_kg ? `qty-${item.id}-error` : undefined}
              {...register("qty_kg")}
            />
            {errors.qty_kg && (
              <p id={`qty-${item.id}-error`} role="alert" className="text-sm text-destructive">
                {errors.qty_kg.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`price-${item.id}`}>Price per kg (₹, optional)</Label>
            <Input
              id={`price-${item.id}`}
              type="number"
              step="0.01"
              min="0"
              max="1000000000"
              inputMode="decimal"
              aria-invalid={Boolean(errors.price_per_kg) || undefined}
              aria-describedby={errors.price_per_kg ? `price-${item.id}-error` : undefined}
              {...register("price_per_kg")}
            />
            {errors.price_per_kg && (
              <p id={`price-${item.id}-error`} role="alert" className="text-sm text-destructive">
                {errors.price_per_kg.message}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting || addFlight.pending}>
              {isSubmitting || addFlight.pending ? "Adding…" : "Add"}
            </Button>
          </DialogFooter>
          </fieldset>
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

/** Mix recipe batches (1 batch = 100 kg), decrementing inventory (feeding.manage).
 * Controlled by the page: the header action and the mixed-feed empty state
 * both open the same single dialog instance. */
function MixBatchDialog({
  open,
  onOpenChange,
  flight,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Lifted so every opener can disable itself while a mix write is in flight. */
  flight: ReturnType<typeof useSingleFlight>;
}) {
  const [shortage, setShortage] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const mut = useMixBatchApiFeedingMixPost();
  const mixFlight = flight;

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
  // The openers reset the form, which clears react-hook-form's `isSubmitting`
  // — the submit button's only in-flight guard — and blanks recipe_code so the
  // retyped request body no longer matches the in-flight one's Idempotency-Key.
  // Guard outside react-hook-form so reset cannot reach it. The dialog stays
  // mounted for the whole page, so the draft resets on every closed→open
  // transition (previously the trigger button did this before opening). A
  // stale shortage cannot survive that transition: every close path runs the
  // Dialog onOpenChange below, which clears it.
  useEffect(() => {
    if (open) {
      reset({ recipe_code: "", batches: 1 });
    }
  }, [open, reset]);

  async function onSubmit(values: MixValues) {
    await mixFlight.run(async () => {
      const farmScope = captureFarmScope();
      setShortage(null);
      try {
        await mut.mutateAsync({ data: { recipe_code: values.recipe_code, batch_kg: values.batches * 100 } });
        if (!farmScope()) return;
        toast.success(`Mixed ${values.batches * 100} kg — inventory decremented.`);
        invalidateFarmData(queryClient);
        reset();
        onOpenChange(false);
      } catch (err) {
        if (!farmScope()) return;
        // Insufficient stock comes back as a 400 with the shortage detail — show it in the dialog.
        if (err instanceof ApiError && err.status === 400) {
          setShortage(err.detail);
        } else {
          toast.error(mutationError(err));
        }
      }
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) setShortage(null);
        onOpenChange(v);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Mix a recipe batch</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          One batch is 100 kg of mixed feed. Stock is decremented per the recipe&apos;s ingredient
          lines.
        </p>
        {shortage && (
          <p className="rounded-md border border-warning/40 bg-warning-tint/50 p-3 text-sm text-warning-tint-foreground">
            Cannot mix — insufficient stock: {shortage}
          </p>
        )}
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          <fieldset disabled={isSubmitting || mixFlight.pending} className="contents">
          <div className="space-y-1.5">
            <Label htmlFor="mix-recipe">Recipe</Label>
            {recipesQuery.isLoading && <InlineLoading>Loading recipes…</InlineLoading>}
            {recipesQuery.isError && (
              <div role="alert" className="space-y-2 text-sm text-destructive">
                <p>Could not load recipes. Retry before mixing a batch.</p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void recipesQuery.refetch()}
                >
                  Retry recipes
                </Button>
              </div>
            )}
            <Select
              value={wRecipeCode}
              onValueChange={(v) => setValue("recipe_code", v, { shouldValidate: true })}
              items={recipeItems}
              disabled={recipesQuery.isLoading || recipesQuery.isError}
            >
              <SelectTrigger id="mix-recipe" className="w-full">
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
              <p role="alert" className="text-sm text-destructive">
                {errors.recipe_code.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="batches">Batches (1–50, each 100 kg)</Label>
            <Input
              id="batches"
              type="number"
              step="1"
              min="1"
              max="50"
              inputMode="numeric"
              aria-invalid={Boolean(errors.batches) || undefined}
              aria-describedby={errors.batches ? "mix-batches-error" : undefined}
              {...register("batches")}
            />
            {errors.batches && (
              <p id="mix-batches-error" role="alert" className="text-sm text-destructive">
                {errors.batches.message}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              type="submit"
              disabled={
                isSubmitting ||
                mixFlight.pending ||
                recipesQuery.isLoading ||
                recipesQuery.isError
              }
            >
              {isSubmitting || mixFlight.pending ? "Mixing…" : "Mix batch"}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function InventoryPage() {
  const perms = usePermissions();
  return (
    <PermissionGate
      perms={perms}
      perm="feeding.view"
      label="Feed inventory"
      description="Ingredient stock and ready-to-dispense mixed feed."
      cards={2}
    >
      <InventoryPageContent perms={perms} />
    </PermissionGate>
  );
}

function InventoryPageContent({ perms }: { perms: PermissionsState }) {
  const { can } = perms;
  const allowed = can("feeding.view");
  const canManage = can("feeding.manage");
  /** One mix dialog shared by the header action and the empty-state CTA. */
  const [mixOpen, setMixOpen] = useState(false);
  const mixFlight = useSingleFlight();

  const query = useListInventoryApiFeedingInventoryGet({ query: { enabled: allowed } });
  const items = query.data?.status === 200 ? query.data.data : undefined;
  const finishedQuery = useListFinishedStockApiFeedingFinishedStockGet({
    query: { enabled: allowed },
  });
  const finishedStock =
    finishedQuery.data?.status === 200 ? finishedQuery.data.data : undefined;

  if (query.isLoading && !items) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Feed inventory"
          description="Ingredient stock and ready-to-dispense mixed feed."
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading feed inventory…</span>
          <PageSkeleton cards={2} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Feed inventory"
        description="Ingredient stock and ready-to-dispense mixed feed."
        actions={
          canManage && (
            <Button
              variant="outline"
              disabled={mixFlight.pending}
              onClick={() => setMixOpen(true)}
            >
              Mix batch
            </Button>
          )
        }
      />

      <FeedingNav active="inventory" />

      <DataTableCard title="Stock on hand">
        {query.isError ? (
          <div role="alert" className="space-y-3">
            <p className="text-sm text-destructive">
              {query.error instanceof ApiError
                ? query.error.detail
                : "Could not load feed inventory."}
            </p>
            <Button type="button" variant="outline" onClick={() => void query.refetch()}>
              Retry inventory
            </Button>
          </div>
        ) : items === undefined ? (
          <InlineLoading className="py-4">Loading feed inventory…</InlineLoading>
        ) : items.length === 0 ? (
          <EmptyState
            icon={Package}
            title="No feed inventory items yet."
            description="Ingredients appear here once the first feed purchase is recorded."
          />
        ) : (
          <>
          {/* Below md the 6-column stock table becomes a card per ingredient
           * — a phone in the feed store must show the low-stock flag and the
           * Add-stock action without panning. */}
          <div className="space-y-2 md:hidden">
            {items.map((item) => {
              const low = item.reorder_level !== null && item.qty_on_hand <= item.reorder_level;
              return (
                <div
                  key={item.id}
                  className={
                    low
                      ? "space-y-1.5 rounded-xl border border-warning-tint-border bg-warning-tint/50 p-3 shadow-xs"
                      : "space-y-1.5 rounded-xl border bg-card p-3 shadow-xs"
                  }
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">{item.ingredient}</span>
                    <span className="tabular-nums text-sm">
                      {formatPersistedKg(item.qty_on_hand)} kg
                      {low && (
                        <span className="ml-1.5 inline-flex items-center align-middle text-warning">
                          <TriangleAlert className="size-4" />
                          <span className="sr-only">low</span>
                        </span>
                      )}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground tabular-nums">
                    {item.category} · Reorder at {item.reorder_level ?? "—"} kg · Last price{" "}
                    {formatMoney(item.last_purchase_price_per_kg)}/kg
                  </p>
                  {canManage && (
                    <div className="pt-0.5">
                      <AddStockDialog item={item} touch />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[640px]">
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
                    className={low ? "bg-warning-tint/50" : undefined}
                  >
                    <TableCell>{item.category}</TableCell>
                    <TableCell className="font-medium">{item.ingredient}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatPersistedKg(item.qty_on_hand)}
                      {low && (
                        <span className="ml-2 inline-flex items-center align-middle text-warning">
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
          </div>
          </>
        )}
      </DataTableCard>
      <DataTableCard
        title="Ready-to-dispense mixed feed"
        description="Mixing adds to these recipe balances; recording a recipe dispense deducts from them."
      >
        {finishedQuery.isLoading ? (
          <InlineLoading className="py-4">Loading mixed-feed stock…</InlineLoading>
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
          >
            {canManage && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={mixFlight.pending}
                onClick={() => setMixOpen(true)}
              >
                Mix your first batch
              </Button>
            )}
          </EmptyState>
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
                    {formatPersistedKg(stock.qty_on_hand)}
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

      {canManage && <MixBatchDialog open={mixOpen} onOpenChange={setMixOpen} flight={mixFlight} />}
    </div>
  );
}
