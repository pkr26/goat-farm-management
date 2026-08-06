"use client";

/** Feeding — today's 3-shift plan + dispensing log (parity with v1 feeding/plan.html). */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { useForm , useWatch} from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getFeedingTodayApiFeedingPlanGetQueryKey,
  useDispenseApiFeedingDispensePost,
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
import { usePermissions } from "@/lib/use-permissions";

/** Sentinel for "no recipe" in the dispense select (empty string is not a valid item value). */
const NONE = "none";

type ShiftCell = { shift: string; pct: number; kg: number; time: string };

function localToday(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
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
    formState: { errors, isSubmitting },
  } = useForm<SettingInput, unknown, SettingValues>({
    resolver: zodResolver(settingSchema),
    defaultValues: { daily_kg_per_head: line.kg_per_head },
  });

  async function onSubmit(values: SettingValues) {
    try {
      await mut.mutateAsync({
        data: {
          bucket: line.bucket as FeedSettingInBucket,
          daily_kg_per_head: values.daily_kg_per_head,
        },
      });
      toast.success(`Saved ${values.daily_kg_per_head} kg/head for ${line.bucket}.`);
      queryClient.invalidateQueries({ queryKey: getFeedingTodayApiFeedingPlanGetQueryKey() });
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
              {...register("daily_kg_per_head")}
            />
            {errors.daily_kg_per_head && (
              <p className="text-sm text-destructive">{errors.daily_kg_per_head.message}</p>
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
  date: z.string().min(1, "Date is required"),
});
type DispenseInput = z.input<typeof dispenseSchema>;
type DispenseValues = z.output<typeof dispenseSchema>;

export default function FeedingPage() {
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("feeding.view");
  const canManage = can("feeding.manage");
  const queryClient = useQueryClient();

  const [dispenseOpen, setDispenseOpen] = useState(false);

  const query = useFeedingTodayApiFeedingPlanGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

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
      queryClient.invalidateQueries({ queryKey: getFeedingTodayApiFeedingPlanGetQueryKey() });
      setDispenseOpen(false);
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Feeding — today</h1>
        {canManage && (
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
        )}
      </div>

      <FeedingNav active="Today's plan" />

      <section className="space-y-2">
        <h2 className="text-lg font-medium">Plan (headcount × kg/head, split 40 / 20 / 40)</h2>
        {payload.lines.length === 0 ? (
          <p className="text-muted-foreground">No active animals — nothing to feed.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Bucket</TableHead>
                <TableHead>Recipe today</TableHead>
                <TableHead>Heads</TableHead>
                <TableHead>kg/head/day</TableHead>
                <TableHead>Daily kg</TableHead>
                <TableHead>Morning 40%</TableHead>
                <TableHead>Afternoon 20%</TableHead>
                <TableHead>Night 40%</TableHead>
                <TableHead>Dispensed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.lines.map((line) => {
                const shifts = line.shifts as unknown as ShiftCell[];
                const dispensed = dispensedByBucket.get(line.bucket) ?? 0;
                return (
                  <TableRow key={line.bucket}>
                    <TableCell className="font-medium">{line.bucket}</TableCell>
                    <TableCell>{line.recipe_name}</TableCell>
                    <TableCell>{line.heads}</TableCell>
                    <TableCell>
                      {line.kg_per_head}
                      {canManage && <KgPerHeadDialog line={line} />}
                    </TableCell>
                    <TableCell>{line.daily_kg}</TableCell>
                    {shifts.map((s) => (
                      <TableCell key={s.shift} title={`${s.shift} ${s.time}`}>
                        {s.kg}
                      </TableCell>
                    ))}
                    <TableCell>
                      {dispensed.toFixed(1)} kg
                      {dispensed >= line.daily_kg && (
                        <Badge variant="secondary" className="ml-2">
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
        <p className="text-sm text-muted-foreground">
          Shifts: MORNING 6:30 AM (sweep bunks first) · AFTERNOON 1:30 PM · NIGHT 7:30 PM. RESTING
          switches MAINTENANCE → FLUSH at day 10; MALE_KIDS frame-builder → fattening at day 91.
        </p>
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-medium">Today&apos;s dispensing log</h2>
        {payload.records.length === 0 ? (
          <p className="text-muted-foreground">Nothing dispensed yet today.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Shift</TableHead>
                <TableHead>Bucket</TableHead>
                <TableHead>Recipe</TableHead>
                <TableHead>Qty (kg)</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.records.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>{r.shift}</TableCell>
                  <TableCell>{r.bucket}</TableCell>
                  <TableCell>{r.recipe_code ?? "—"}</TableCell>
                  <TableCell>{r.qty_kg.toFixed(1)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <Dialog open={dispenseOpen} onOpenChange={setDispenseOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Record dispensing</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit(onDispense)} className="space-y-4" noValidate>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Bucket</Label>
                <Select
                  value={wBucket}
                  onValueChange={(v) =>
                    setValue("bucket", v as DispenseValues["bucket"], { shouldValidate: true })
                  }
                >
                  <SelectTrigger className="w-full">
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
                <Label>Shift</Label>
                <Select
                  value={wShift}
                  onValueChange={(v) =>
                    setValue("shift", v as DispenseValues["shift"], { shouldValidate: true })
                  }
                >
                  <SelectTrigger className="w-full">
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
                <Label>Recipe</Label>
                <Select
                  value={wRecipeCode || NONE}
                  onValueChange={(v) => setValue("recipe_code", v)}
                  items={recipeItems}
                >
                  <SelectTrigger className="w-full">
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
                  {...register("qty_kg")}
                />
                {errors.qty_kg && (
                  <p className="text-sm text-destructive">{errors.qty_kg.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="dispense_date">Date</Label>
                <Input id="dispense_date" type="date" max={today} {...register("date")} />
                {errors.date && <p className="text-sm text-destructive">{errors.date.message}</p>}
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
