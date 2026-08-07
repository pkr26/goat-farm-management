"use client";

/** Purchase batches — list + new-batch form + per-batch detail (animals created,
 * open quarantine tasks). Parity with v1 purchases/list.html + new.html + detail.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Controller, useForm , useWatch} from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getListBatchesApiPurchasesGetQueryKey,
  useBatchDetailApiPurchasesBatchIdGet,
  useCreateBatchApiPurchasesNewPost,
  useListBatchesApiPurchasesGet,
} from "@/api/generated/endpoints";
import { PurchaseBatchInSex } from "@/api/generated/models";
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
import { formatDate, formatMoney } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";

function localToday(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
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

// Bounds mirror backend/app/schemas/purchases.py (count 1..1000, age 0..240, prices ≥ 0,
// date year ≥ 2000 and not in the future).
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
    avg_weight_kg: optNum(z.number().min(0, "Cannot be negative")),
    total_price: optNum(z.number().min(0, "Cannot be negative")),
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
function BatchDetailDialog({ batchId, onClose }: { batchId: number | null; onClose: () => void }) {
  const query = useBatchDetailApiPurchasesBatchIdGet(batchId ?? 0, {
    query: { enabled: batchId !== null },
  });
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
                <h3 className="font-medium">Animals created ({detail.animals.length})</h3>
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
                            <Link href={`/animals/${a.id}`} className="text-primary underline">
                              {a.tag_number}
                            </Link>
                          </TableCell>
                          <TableCell>{a.sex}</TableCell>
                          <TableCell>{a.current_bucket}</TableCell>
                          <TableCell>{a.status}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
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
                            <Badge variant="secondary">{t.status}</Badge>
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
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("purchases.view");
  const canManage = can("purchases.manage");
  const queryClient = useQueryClient();

  const [open, setOpen] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);

  const query = useListBatchesApiPurchasesGet({ query: { enabled: allowed } });
  const batches = query.data?.status === 200 ? query.data.data : undefined;

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
      count: 50,
      sex: PurchaseBatchInSex.F,
      create_animals: true,
    },
  });
  const wCreateAnimals = useWatch({ control, name: "create_animals" });

  async function onSubmit(values: BatchValues) {
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
      queryClient.invalidateQueries({ queryKey: getListBatchesApiPurchasesGetQueryKey() });
      setOpen(false);
      reset();
    } catch (err) {
      toast.error(mutationError(err));
    }
  }

  if (permsLoading) {
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }
  if (!allowed) {
    return <p className="text-muted-foreground">You don&apos;t have access to this page.</p>;
  }
  if (query.isLoading || !batches) {
    if (query.isError) {
      return (
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError ? query.error.detail : "Could not load purchase batches."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Purchase batches</h1>
        {canManage && (
          <Button
            onClick={() => {
              reset({
                date: localToday(),
                count: 50,
                sex: PurchaseBatchInSex.F,
                create_animals: true,
              });
              setOpen(true);
            }}
          >
            + New batch
          </Button>
        )}
      </div>

      {batches.length === 0 ? (
        <p className="text-muted-foreground">No purchase batches yet.</p>
      ) : (
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
      )}

      <BatchDetailDialog batchId={detailId} onClose={() => setDetailId(null)} />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>New purchase batch</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            The 45-day quarantine protocol (deworm → PPR → ET+TT → Goat Pox → FMD →
            footbath/release) is auto-created as dated tasks. An ANIMAL_PURCHASE expense is booked
            for the total price.
          </p>
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
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
                {isSubmitting ? "Creating…" : "Create batch"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
