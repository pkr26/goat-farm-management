"use client";

/** Feeding — today's 3-shift plan + dispensing log (parity with v1 feeding/plan.html). */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Wheat } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useForm , useWatch} from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useDispenseApiFeedingDispensePost,
  useFeedingHistoryApiFeedingRecordsGet,
  useFeedingTodayApiFeedingPlanGet,
  useListRecipesApiFeedingRecipesGet,
  useSaveSettingApiFeedingSettingsPost,
} from "@/api/generated/endpoints";
import {
  DispenseInBucket,
  DispenseInShift,
  type FeedSettingInBucket,
  type PlanLineOut,
} from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PaginationControls } from "@/components/pagination-controls";
import { Badge } from "@/components/ui/badge";
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
import { farmToday, formatDate } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { usePermissions } from "@/lib/use-permissions";

/** Sentinel for "no recipe" in the dispense select (empty string is not a valid item value). */
const NONE = "none";

type ShiftCell = { shift: string; pct: number; kg: number; time: string };

/** The generated PlanLineOutShiftsItem is schema-less ({[key: string]:
 *  unknown}), so validate the fields the plan table renders instead of
 *  casting. Malformed cells degrade to visible placeholders rather than
 *  crashing the row or shifting the Morning/Afternoon/Night columns. */
function toShiftCell(raw: unknown): ShiftCell {
  const cell = (raw ?? {}) as Record<string, unknown>;
  return {
    shift: typeof cell.shift === "string" ? cell.shift : "?",
    pct: typeof cell.pct === "number" ? cell.pct : 0,
    kg: typeof cell.kg === "number" ? cell.kg : 0,
    time: typeof cell.time === "string" ? cell.time : "",
  };
}

function localToday(): string {
  return farmToday();
}

function mutationError(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

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

const settingSchema = z.object({
  daily_kg_per_head: z.coerce.number().positive("kg/head must be greater than 0"),
});
type SettingInput = z.input<typeof settingSchema>;
type SettingValues = z.output<typeof settingSchema>;

/** Per-bucket kg/head override (feeding.manage). */
function KgPerHeadDialog({ line }: { line: PlanLineOut }) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const mut = useSaveSettingApiFeedingSettingsPost();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<SettingInput, unknown, SettingValues>({
    resolver: zodResolver(settingSchema),
    defaultValues: { daily_kg_per_head: line.kg_per_head },
  });

  useEffect(() => {
    reset({ daily_kg_per_head: line.kg_per_head });
  }, [line.kg_per_head, reset]);

  async function onSubmit(values: SettingValues) {
    try {
      await mut.mutateAsync({
        data: {
          bucket: line.bucket as FeedSettingInBucket,
          daily_kg_per_head: values.daily_kg_per_head,
        },
      });
      toast.success(`Saved ${values.daily_kg_per_head} kg/head for ${line.bucket}.`);
      reset({ daily_kg_per_head: values.daily_kg_per_head });
      invalidateFarmData(queryClient);
      setOpen(false);
    } catch (err) {
      toast.error(mutationError(err));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button size="sm" variant="outline" className="ml-2" onClick={() => setOpen(true)}>
        Edit
      </Button>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Daily ration — {line.bucket}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor={`kg-${line.bucket}`}>kg per head per day *</Label>
            <Input
              id={`kg-${line.bucket}`}
              type="number"
              step="0.1"
              min="0.1"
              aria-invalid={Boolean(errors.daily_kg_per_head) || undefined}
              aria-describedby={
                errors.daily_kg_per_head ? `kg-${line.bucket}-error` : undefined
              }
              {...register("daily_kg_per_head")}
            />
            {errors.daily_kg_per_head && (
              <p
                id={`kg-${line.bucket}-error`}
                role="alert"
                className="text-sm text-destructive"
              >
                {errors.daily_kg_per_head.message}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

const dispenseSchema = z.object({
  bucket: z.enum([
    "QUARANTINE",
    "FOUNDATION",
    "BREEDING",
    "PREGNANCY_EARLY",
    "PREGNANCY_LATE",
    "DELIVERY",
    "RECOVERY",
    "RESTING",
    "MALE_KIDS",
    "FEMALE_KIDS",
  ]),
  shift: z.enum(["MORNING", "AFTERNOON", "NIGHT"]),
  recipe_code: z.string().optional(),
  qty_kg: z.coerce.number().positive("Quantity must be greater than 0"),
  date: z
    .string()
    .min(1, "Date is required")
    .refine((s) => s <= localToday(), "Date can't be in the future"),
});
type DispenseInput = z.input<typeof dispenseSchema>;
type DispenseValues = z.output<typeof dispenseSchema>;

export default function FeedingPage() {
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("feeding.view");
  const canManage = can("feeding.manage");
  const queryClient = useQueryClient();

  const [dispenseOpen, setDispenseOpen] = useState(false);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const historyLimit = 50;
  const invalidHistoryRange = Boolean(dateFrom && dateTo && dateFrom > dateTo);

  const query = useFeedingTodayApiFeedingPlanGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const historyQuery = useFeedingHistoryApiFeedingRecordsGet(
    {
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      limit: historyLimit,
      offset: historyOffset,
    },
    { query: { enabled: allowed && !invalidHistoryRange } },
  );
  const history =
    historyQuery.data?.status === 200 ? historyQuery.data.data : undefined;

  const recipesQuery = useListRecipesApiFeedingRecipesGet({ query: { enabled: canManage } });
  const recipes = recipesQuery.data?.status === 200 ? recipesQuery.data.data.recipes : [];
  /** value → label map for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const recipeItems: Record<string, string> = {
    [NONE]: "— none —",
    ...Object.fromEntries(recipes.map((r) => [r.code, r.name])),
  };

  const dispenseMutation = useDispenseApiFeedingDispensePost();
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<DispenseInput, unknown, DispenseValues>({
    resolver: zodResolver(dispenseSchema),
    defaultValues: { bucket: "QUARANTINE", shift: "MORNING", recipe_code: NONE, date: localToday() },
  });
  const wBucket = useWatch({ control, name: "bucket" });
  const wRecipeCode = useWatch({ control, name: "recipe_code" });
  const wShift = useWatch({ control, name: "shift" });

  async function onDispense(values: DispenseValues) {
    try {
      await dispenseMutation.mutateAsync({
        data: {
          bucket: values.bucket,
          shift: values.shift,
          recipe_code: values.recipe_code && values.recipe_code !== NONE ? values.recipe_code : null,
          qty_kg: values.qty_kg,
          date: values.date || null,
        },
      });
      toast.success("Dispensing recorded.");
      invalidateFarmData(queryClient);
      setDispenseOpen(false);
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
          {query.error instanceof ApiError ? query.error.detail : "Could not load the feeding plan."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  const today = localToday();
  const dispensedByBucket = new Map<string, number>();
  for (const r of payload.records) {
    dispensedByBucket.set(r.bucket, (dispensedByBucket.get(r.bucket) ?? 0) + r.qty_kg);
  }
  // One bucket can be split across several plan lines (one per recipe), each
  // with its own daily_kg — the "done" badge must compare the bucket-wide
  // dispensed total against the SUM of that bucket's lines, never against a
  // single line.
  const plannedByBucket = new Map<string, number>();
  for (const l of payload.lines) {
    plannedByBucket.set(l.bucket, (plannedByBucket.get(l.bucket) ?? 0) + l.daily_kg);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Feeding — today"
        description="The 3-shift ration plan and what's been dispensed so far."
        actions={
          canManage && (
            <Button
              onClick={() => {
                reset({
                  bucket: (payload.lines[0]?.bucket ?? "QUARANTINE") as DispenseValues["bucket"],
                  shift: "MORNING",
                  recipe_code: NONE,
                  qty_kg: undefined,
                  date: today,
                });
                setDispenseOpen(true);
              }}
            >
              Record dispensing
            </Button>
          )
        }
      />

      <FeedingNav active="Today's plan" />

      <DataTableCard
        title="Plan (headcount × kg/head, split 40 / 20 / 40)"
        description="Per-bucket rations split across the three daily shifts."
      >
        {payload.lines.length === 0 ? (
          <EmptyState
            icon={Wheat}
            title="No active animals — nothing to feed."
            description="Once animals are active, their ration plan will show up here."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Bucket</TableHead>
                <TableHead>Recipe today</TableHead>
                <TableHead className="text-right">Heads</TableHead>
                <TableHead className="text-right">kg/head/day</TableHead>
                <TableHead className="text-right">Daily kg</TableHead>
                <TableHead className="text-right">Morning 40%</TableHead>
                <TableHead className="text-right">Afternoon 20%</TableHead>
                <TableHead className="text-right">Night 40%</TableHead>
                <TableHead className="text-right">Dispensed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.lines.map((line) => {
                const shifts = line.shifts.map(toShiftCell);
                const dispensed = dispensedByBucket.get(line.bucket) ?? 0;
                return (
                  // One bucket can appear on several lines (split by recipe).
                  <TableRow key={`${line.bucket}:${line.recipe_code}`}>
                    <TableCell className="font-medium">{line.bucket}</TableCell>
                    <TableCell>{line.recipe_name}</TableCell>
                    <TableCell className="text-right tabular-nums">{line.heads}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {line.kg_per_head}
                      {canManage && <KgPerHeadDialog line={line} />}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{line.daily_kg}</TableCell>
                    {shifts.map((s, index) => (
                      <TableCell
                        key={`${s.shift}:${index}`}
                        title={`${s.shift} ${s.time}`}
                        className="text-right tabular-nums"
                      >
                        {s.kg}
                      </TableCell>
                    ))}
                    <TableCell className="text-right tabular-nums">
                      {dispensed.toFixed(1)} kg
                      {dispensed >= (plannedByBucket.get(line.bucket) ?? line.daily_kg) && (
                        <Badge
                          variant="outline"
                          className="ml-2 border-transparent bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                        >
                          <Check />
                          done
                        </Badge>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
        <p className="mt-3 text-sm text-muted-foreground">
          Shifts: MORNING 6:30 AM (sweep bunks first) · AFTERNOON 1:30 PM · NIGHT 7:30 PM. RESTING
          switches MAINTENANCE → FLUSH at day 10; MALE_KIDS frame-builder → fattening at day 91.
        </p>
      </DataTableCard>

      <DataTableCard title="Today's dispensing log">
        {payload.records.length === 0 ? (
          <EmptyState
            icon={Wheat}
            title="Nothing dispensed yet today."
            description="Recorded dispensing will appear here as the shifts progress."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Shift</TableHead>
                <TableHead>Bucket</TableHead>
                <TableHead>Recipe</TableHead>
                <TableHead className="text-right">Qty (kg)</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.records.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>{r.shift}</TableCell>
                  <TableCell>{r.bucket}</TableCell>
                  <TableCell>{r.recipe_code ?? "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{r.qty_kg.toFixed(1)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DataTableCard>

      <DataTableCard
        title="Dispensing history"
        description="Browse earlier and backdated dispensing entries; the total comes from the full ledger."
      >
        <div className="mb-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
          <div className="space-y-1.5">
            <Label htmlFor="feeding-history-from">From date</Label>
            <Input
              id="feeding-history-from"
              type="date"
              value={dateFrom}
              max={dateTo || today}
              aria-invalid={invalidHistoryRange || undefined}
              aria-describedby={invalidHistoryRange ? "feeding-history-range-error" : undefined}
              onChange={(event) => {
                setDateFrom(event.target.value);
                setHistoryOffset(0);
              }}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="feeding-history-to">To date</Label>
            <Input
              id="feeding-history-to"
              type="date"
              value={dateTo}
              min={dateFrom || undefined}
              max={today}
              aria-invalid={invalidHistoryRange || undefined}
              aria-describedby={invalidHistoryRange ? "feeding-history-range-error" : undefined}
              onChange={(event) => {
                setDateTo(event.target.value);
                setHistoryOffset(0);
              }}
            />
          </div>
          <Button
            type="button"
            variant="outline"
            disabled={!dateFrom && !dateTo}
            onClick={() => {
              setDateFrom("");
              setDateTo("");
              setHistoryOffset(0);
            }}
          >
            Clear dates
          </Button>
        </div>
        {invalidHistoryRange && (
          <p id="feeding-history-range-error" role="alert" className="mb-3 text-sm text-destructive">
            From date must be on or before to date.
          </p>
        )}
        {!invalidHistoryRange && historyQuery.isLoading && (
          <p className="py-6 text-center text-sm text-muted-foreground">Loading history…</p>
        )}
        {!invalidHistoryRange && historyQuery.isError && (
          <p role="alert" className="py-3 text-sm text-destructive">
            {historyQuery.error instanceof ApiError
              ? historyQuery.error.detail
              : "Could not load dispensing history."}
          </p>
        )}
        {!invalidHistoryRange && history && history.records.length === 0 && (
          <EmptyState
            icon={Wheat}
            title="No dispensing records in this date range."
            description="Clear or widen the dates, or record a dispensing entry."
          />
        )}
        {!invalidHistoryRange && history && history.records.length > 0 && (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Shift</TableHead>
                  <TableHead>Bucket</TableHead>
                  <TableHead>Recipe</TableHead>
                  <TableHead className="text-right">Qty (kg)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.records.map((record) => (
                  <TableRow key={record.id}>
                    <TableCell>{formatDate(record.date)}</TableCell>
                    <TableCell>{record.shift}</TableCell>
                    <TableCell>{record.bucket}</TableCell>
                    <TableCell>{record.recipe_code ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {record.qty_kg.toFixed(1)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <PaginationControls
              total={history.total}
              limit={history.limit}
              offset={history.offset}
              onOffsetChange={setHistoryOffset}
              label="dispensing records"
            />
          </>
        )}
      </DataTableCard>

      <Dialog open={dispenseOpen} onOpenChange={setDispenseOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Record dispensing</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit(onDispense)} className="space-y-4" noValidate>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="dispense-bucket">Bucket</Label>
                <Select
                  value={wBucket}
                  onValueChange={(v) =>
                    setValue("bucket", v as DispenseValues["bucket"], { shouldValidate: true })
                  }
                >
                  <SelectTrigger id="dispense-bucket" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.values(DispenseInBucket).map((b) => (
                      <SelectItem key={b} value={b}>
                        {b}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="dispense-shift">Shift</Label>
                <Select
                  value={wShift}
                  onValueChange={(v) =>
                    setValue("shift", v as DispenseValues["shift"], { shouldValidate: true })
                  }
                >
                  <SelectTrigger id="dispense-shift" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.values(DispenseInShift).map((s) => (
                      <SelectItem key={s} value={s}>
                        {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="dispense-recipe">Recipe</Label>
                <Select
                  value={wRecipeCode || NONE}
                  onValueChange={(v) => setValue("recipe_code", v)}
                  items={recipeItems}
                >
                  <SelectTrigger id="dispense-recipe" className="w-full">
                    <SelectValue placeholder="recipe…" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>— none —</SelectItem>
                    {recipes.map((r) => (
                      <SelectItem key={r.code} value={r.code}>
                        {r.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="qty_kg">Quantity (kg) *</Label>
                <Input
                  id="qty_kg"
                  type="number"
                  step="0.1"
                  min="0.1"
                  placeholder="kg"
                  aria-invalid={Boolean(errors.qty_kg) || undefined}
                  aria-describedby={errors.qty_kg ? "qty-kg-error" : undefined}
                  {...register("qty_kg")}
                />
                {errors.qty_kg && (
                  <p id="qty-kg-error" role="alert" className="text-sm text-destructive">
                    {errors.qty_kg.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="dispense_date">Date</Label>
                <Input
                  id="dispense_date"
                  type="date"
                  max={today}
                  aria-invalid={Boolean(errors.date) || undefined}
                  aria-describedby={errors.date ? "dispense-date-error" : undefined}
                  {...register("date")}
                />
                {errors.date && (
                  <p id="dispense-date-error" role="alert" className="text-sm text-destructive">
                    {errors.date.message}
                  </p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Recording…" : "Record"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
