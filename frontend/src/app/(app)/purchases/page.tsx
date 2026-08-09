"use client";

/** Purchase batches — list + new-batch form + per-batch detail (animals created,
 * open quarantine tasks). Parity with v1 purchases/list.html + new.html + detail.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Plus, ShoppingCart } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useBatchDetailApiPurchasesBatchIdGet,
  useCreateBatchApiPurchasesNewPost,
  useListBatchesApiPurchasesGet,
} from "@/api/generated/endpoints";
import { PurchaseBatchInSex } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PaginationControls } from "@/components/pagination-controls";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { farmToday, formatDate, formatMoney } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import {
  isPersistableNonnegativeMoney,
  MIN_PERSISTED_MONEY_MESSAGE,
} from "@/lib/persisted-numbers";
import { usePermissions } from "@/lib/use-permissions";

function localToday(): string {
  return farmToday();
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

/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value ("F") in the closed trigger. */
const SEX_ITEMS: Record<string, string> = {
  [PurchaseBatchInSex.F]: "Female",
  [PurchaseBatchInSex.M]: "Male",
};

// Bounds mirror backend/app/schemas/purchases.py (count 1..1000, age 0..240,
// weight 0..1000 kg, prices ≥ 0, date year ≥ 2000 and not in the future).
const batchSchema = z
  .object({
    date: z.string().min(1, "Date is required"),
    supplier: z.string().max(120, "At most 120 characters").optional(),
    count: z.coerce
      .number()
      .int("Count must be a whole number")
      .min(1, "At least 1 animal")
      .max(1000, "At most 1000 animals"),
    sex: z.enum([PurchaseBatchInSex.F, PurchaseBatchInSex.M]),
    avg_age_months: optNum(z.number().min(0, "Cannot be negative").max(240, "At most 240 months")),
    avg_weight_kg: optNum(
      z.number().min(0, "Cannot be negative").max(1000, "At most 1000 kg"),
    ),
    total_price: optNum(
      z
        .number()
        .min(0, "Cannot be negative")
        .refine(isPersistableNonnegativeMoney, MIN_PERSISTED_MONEY_MESSAGE),
    ),
    notes: z.string().optional(),
    create_animals: z.boolean(),
  })
  .refine((v) => !v.date || Number(v.date.slice(0, 4)) >= 2000, {
    message: "Date must be year 2000 or later",
    path: ["date"],
  })
  .refine((v) => !v.date || v.date <= localToday(), {
    message: "Date cannot be in the future",
    path: ["date"],
  });
type BatchInput = z.input<typeof batchSchema>;
type BatchValues = z.output<typeof batchSchema>;

/** Created animals + open quarantine tasks for one batch. */
function BatchDetailDialog({
  batchId,
  canViewAnimals,
  onClose,
}: {
  batchId: number | null;
  canViewAnimals: boolean;
  onClose: () => void;
}) {
  // A batch may hold up to MAX_BATCH_COUNT head, so its animals arrive as a
  // bounded page. The parent keys this component by batch id, so opening a
  // different batch remounts it and the offset starts at zero again.
  const ANIMALS_LIMIT = 100;
  const [animalsOffset, setAnimalsOffset] = useState(0);
  const query = useBatchDetailApiPurchasesBatchIdGet(
    batchId ?? 0,
    { animals_limit: ANIMALS_LIMIT, animals_offset: animalsOffset },
    { query: { enabled: batchId !== null } },
  );
  const detail = query.data?.status === 200 ? query.data.data : undefined;
  const openTasks = (detail?.tasks ?? []).filter((t) => t.status === "PENDING");

  return (
    <Dialog open={batchId !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Batch #{batchId}</DialogTitle>
        </DialogHeader>
        {batchId !== null && (query.isLoading || !detail) ? (
          query.isError ? (
            <p className="text-sm text-destructive">
              {query.error instanceof ApiError ? query.error.detail : "Could not load the batch."}
            </p>
          ) : (
            <p className="py-6 text-center text-muted-foreground">Loading…</p>
          )
        ) : (
          detail && (
            <div className="space-y-5">
              <p className="text-sm text-muted-foreground">
                {formatDate(detail.batch.date)}
                {detail.batch.supplier ? ` · ${detail.batch.supplier}` : ""} ·{" "}
                {detail.batch.count} head
                {detail.batch.total_price !== null
                  ? ` · ${formatMoney(detail.batch.total_price)}`
                  : ""}
              </p>

              <section className="space-y-2">
                <h3 className="font-medium">Animals created ({detail.animals_total})</h3>
                {detail.animals.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No animal stubs for this batch.</p>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Tag</TableHead>
                        <TableHead>Sex</TableHead>
                        <TableHead>Bucket</TableHead>
                        <TableHead>Status</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {detail.animals.map((a) => (
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
                          <TableCell>{a.sex}</TableCell>
                          <TableCell>{a.current_bucket}</TableCell>
                          <TableCell>
                            <StatusBadge status={a.status}>{a.status}</StatusBadge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
                <PaginationControls
                  total={detail.animals_total}
                  limit={detail.animals_limit}
                  offset={detail.animals_offset}
                  onOffsetChange={setAnimalsOffset}
                  label="animals"
                />
              </section>

              <section className="space-y-2">
                <h3 className="font-medium">Open quarantine tasks ({openTasks.length})</h3>
                {openTasks.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No open quarantine tasks.</p>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Due</TableHead>
                        <TableHead>Task</TableHead>
                        <TableHead>Status</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {openTasks.map((t) => (
                        <TableRow key={t.id}>
                          <TableCell>{formatDate(t.due_date)}</TableCell>
                          <TableCell>{t.title}</TableCell>
                          <TableCell>
                            <StatusBadge status={t.status}>{t.status}</StatusBadge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </section>
            </div>
          )
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function PurchasesPage() {
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("purchases.view");
  const canManage = can("purchases.manage");
  const canViewAnimals = can("animals.view");
  const queryClient = useQueryClient();

  const [open, setOpen] = useState(false);
  const [pendingBatch, setPendingBatch] = useState<BatchValues | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const limit = 50;

  const query = useListBatchesApiPurchasesGet(
    { limit, offset },
    { query: { enabled: allowed } },
  );
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  const createMutation = useCreateBatchApiPurchasesNewPost();
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<BatchInput, unknown, BatchValues>({
    resolver: zodResolver(batchSchema),
    defaultValues: {
      date: localToday(),
      count: 1,
      sex: PurchaseBatchInSex.F,
      create_animals: true,
    },
  });
  const wCreateAnimals = useWatch({ control, name: "create_animals" });

  function onReview(values: BatchValues) {
    setPendingBatch(values);
  }

  async function createBatch(values: BatchValues) {
    try {
      await createMutation.mutateAsync({
        data: {
          date: values.date,
          supplier: values.supplier?.trim() ? values.supplier.trim() : null,
          count: values.count,
          sex: values.sex,
          avg_age_months: values.avg_age_months ?? null,
          avg_weight_kg: values.avg_weight_kg ?? null,
          total_price: values.total_price ?? null,
          notes: values.notes?.trim() ? values.notes.trim() : null,
          create_animals: values.create_animals,
        },
      });
      toast.success("Purchase batch created.");
      invalidateFarmData(queryClient);
      setOpen(false);
      setPendingBatch(null);
      reset({
        date: localToday(),
        count: 1,
        sex: PurchaseBatchInSex.F,
        create_animals: true,
      });
    } catch (err) {
      toast.error(mutationError(err));
    }
  }

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
          {query.error instanceof ApiError ? query.error.detail : "Could not load purchase batches."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  const batches = payload.batches;

  function openNewBatch() {
    reset({
      date: localToday(),
      count: 1,
      sex: PurchaseBatchInSex.F,
      create_animals: true,
    });
    setPendingBatch(null);
    setOpen(true);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Purchase batches"
        description="Incoming groups of goats — each batch auto-creates its 45-day quarantine protocol."
        actions={
          canManage && (
            <Button onClick={openNewBatch}>
              <Plus /> New batch
            </Button>
          )
        }
      />

      {batches.length === 0 ? (
        <EmptyState
          icon={ShoppingCart}
          title="No purchase batches yet."
          description="Record your first batch to create animal stubs and its quarantine task schedule."
        >
          {canManage && (
            <Button onClick={openNewBatch}>
              <Plus /> Add your first batch
            </Button>
          )}
        </EmptyState>
      ) : (
        <DataTableCard
          title="All batches"
          description={`${payload.total} batch${payload.total === 1 ? "" : "es"} recorded`}
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Batch</TableHead>
                <TableHead>Date</TableHead>
                <TableHead>Supplier</TableHead>
                <TableHead>Count</TableHead>
                <TableHead>Avg age</TableHead>
                <TableHead>Avg wt</TableHead>
                <TableHead>Total price</TableHead>
                <TableHead>Animals</TableHead>
                <TableHead>Open tasks</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {batches.map((b) => (
                <TableRow key={b.id}>
                  <TableCell className="font-medium">#{b.id}</TableCell>
                  <TableCell>{formatDate(b.date)}</TableCell>
                  <TableCell>{b.supplier ?? "—"}</TableCell>
                  <TableCell>{b.count}</TableCell>
                  <TableCell>{b.avg_age_months !== null ? `${b.avg_age_months} mo` : "—"}</TableCell>
                  <TableCell>{b.avg_weight_kg !== null ? `${b.avg_weight_kg} kg` : "—"}</TableCell>
                  <TableCell>{formatMoney(b.total_price)}</TableCell>
                  <TableCell>{b.animals_created ?? 0}</TableCell>
                  <TableCell>
                    {b.open_tasks ? (
                      <Badge variant="secondary">{b.open_tasks}</Badge>
                    ) : (
                      (b.open_tasks ?? 0)
                    )}
                  </TableCell>
                  <TableCell>
                    <Button size="sm" variant="outline" onClick={() => setDetailId(b.id)}>
                      View
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <PaginationControls
            total={payload.total}
            limit={payload.limit}
            offset={payload.offset}
            onOffsetChange={setOffset}
            label="purchase batches"
          />
        </DataTableCard>
      )}

      {/* Keyed by batch so opening a different one remounts the dialog and its
          animal-page offset starts at the first page again. */}
      <BatchDetailDialog
        key={detailId ?? "none"}
        batchId={detailId}
        canViewAnimals={canViewAnimals}
        onClose={() => setDetailId(null)}
      />

      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen);
          if (!nextOpen) setPendingBatch(null);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          {pendingBatch ? (
            <>
              <DialogHeader>
                <DialogTitle>Review purchase consequences</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-muted-foreground">
                Confirm the batch before these linked farm records are created.
              </p>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-lg border p-4 text-sm">
                <dt className="text-muted-foreground">Batch</dt>
                <dd className="text-right font-medium">
                  {pendingBatch.count} {pendingBatch.sex === PurchaseBatchInSex.F ? "female" : "male"}
                  {pendingBatch.count === 1 ? " goat" : " goats"}
                </dd>
                <dt className="text-muted-foreground">Purchase date</dt>
                <dd className="text-right">{formatDate(pendingBatch.date)}</dd>
                <dt className="text-muted-foreground">Animal stubs</dt>
                <dd className="text-right">
                  {pendingBatch.create_animals ? `${pendingBatch.count} in QUARANTINE` : "None"}
                </dd>
                <dt className="text-muted-foreground">Protocol tasks</dt>
                <dd className="text-right">
                  {pendingBatch.create_animals
                    ? "45-day quarantine schedule"
                    : "None (no animal stubs)"}
                </dd>
                <dt className="text-muted-foreground">Finance entry</dt>
                <dd className="text-right">
                  {pendingBatch.total_price === undefined
                    ? "No expense amount"
                    : `${formatMoney(pendingBatch.total_price)} ANIMAL_PURCHASE`}
                </dd>
              </dl>
              <p role="alert" className="text-sm font-medium text-destructive">
                Creation is immediate. This page does not currently provide a batch reversal.
              </p>
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  disabled={createMutation.isPending}
                  onClick={() => setPendingBatch(null)}
                >
                  Back and edit
                </Button>
                <Button
                  type="button"
                  disabled={createMutation.isPending}
                  onClick={() => void createBatch(pendingBatch)}
                >
                  {createMutation.isPending ? "Creating…" : "Confirm and create"}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>New purchase batch</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-muted-foreground">
                {wCreateAnimals
                  ? "Animal stubs and the 45-day quarantine protocol (deworm → PPR → ET+TT → Goat Pox → FMD → footbath/release) will be auto-created. "
                  : "With animal-stub creation off, this records only the batch and any purchase expense; it creates no quarantine protocol tasks. "}
                An ANIMAL_PURCHASE expense is booked when a total price is provided.
              </p>
              <form onSubmit={handleSubmit(onReview)} className="space-y-4" noValidate>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Batch details
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="date">Date *</Label>
                <Input id="date" type="date" max={localToday()} {...register("date")} />
                {errors.date && <p className="text-sm text-destructive">{errors.date.message}</p>}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="supplier">Supplier</Label>
                <Input id="supplier" maxLength={120} {...register("supplier")} />
                {errors.supplier && (
                  <p className="text-sm text-destructive">{errors.supplier.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="count">Count *</Label>
                <Input id="count" type="number" min="1" max="1000" {...register("count")} />
                {errors.count && <p className="text-sm text-destructive">{errors.count.message}</p>}
              </div>
              <div className="space-y-1.5">
                <Label>Sex *</Label>
                <Controller
                  control={control}
                  name="sex"
                  render={({ field }) => (
                    <Select value={field.value} onValueChange={field.onChange} items={SEX_ITEMS}>
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={PurchaseBatchInSex.F}>Female</SelectItem>
                        <SelectItem value={PurchaseBatchInSex.M}>Male</SelectItem>
                      </SelectContent>
                    </Select>
                  )}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="avg_age_months">Avg age (months)</Label>
                <Input
                  id="avg_age_months"
                  type="number"
                  step="0.5"
                  min="0"
                  max="240"
                  {...register("avg_age_months")}
                />
                <p className="text-xs text-muted-foreground">
                  Fractional months use an average 30.44-day month for the estimated birth date.
                </p>
                {errors.avg_age_months && (
                  <p className="text-sm text-destructive">{errors.avg_age_months.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="avg_weight_kg">Avg weight (kg)</Label>
                <Input
                  id="avg_weight_kg"
                  type="number"
                  step="0.1"
                  min="0"
                  max="1000"
                  {...register("avg_weight_kg")}
                />
                {errors.avg_weight_kg && (
                  <p className="text-sm text-destructive">{errors.avg_weight_kg.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="total_price">Total price (₹)</Label>
                <Input
                  id="total_price"
                  type="number"
                  step="0.01"
                  min="0"
                  {...register("total_price")}
                />
                {errors.total_price && (
                  <p className="text-sm text-destructive">{errors.total_price.message}</p>
                )}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notes">Notes</Label>
              <Input id="notes" {...register("notes")} />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="create_animals"
                checked={wCreateAnimals}
                onCheckedChange={(checked) => setValue("create_animals", checked === true)}
              />
              <Label htmlFor="create_animals" className="font-normal">
                Create animal stubs in QUARANTINE (auto tags B&lt;batch&gt;-001…)
              </Label>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Reviewing…" : "Review batch"}
              </Button>
            </DialogFooter>
          </form>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
