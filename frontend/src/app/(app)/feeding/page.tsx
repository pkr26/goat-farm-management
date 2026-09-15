"use client";

/** Feeding — today's 3-shift plan + dispensing log (parity with v1 feeding/plan.html). */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Wheat } from "lucide-react";
import Link from "next/link";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
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
  PlanLineOutBasis,
  type FeedSettingInBucket,
  type PlanLineOut,
} from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
import { PaginationControls } from "@/components/pagination-controls";
import { InlineLoading, PageSkeleton } from "@/components/skeletons";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
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
import { farmToday, formatDate } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
import { invalidateFarmData } from "@/lib/query-invalidation";
import {
  formatPersistedKg,
  MIN_PERSISTED_KG,
  MIN_PERSISTED_KG_MESSAGE,
} from "@/lib/persisted-numbers";
import { FeedingNav } from "@/components/feeding-nav";
import { enumLabel } from "@/lib/enum-labels";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { MAX_PAGE_OFFSET, useUrlState, type UrlStateUpdate } from "@/lib/use-url-state";

/** Explicit virtual recipe used by quarantine animals on days 1–3. */
const DRY_ROUGHAGE = "DRY_ROUGHAGE_ONLY";

type ShiftCell = { shift: string; kg: number; time: string };

/** A URL date param is only honoured when it is a well-formed ISO date —
 * garbage in a hand-edited or truncated link must not reach the API as a
 * 422; it falls back to "no filter" instead. */
function urlDate(value: string | null): string {
  return value !== null && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : "";
}

function allocationShiftKey(bucket: string, recipe: string, shift: string): string {
  return `${bucket}\u0000${recipe}\u0000${shift}`;
}

/** Quantities are stored to 3 decimals; tolerate floating-point addition at
 * half of the smallest persisted unit when comparing actual with planned. */
function meetsPlannedQuantity(actual: number, planned: number): boolean {
  return actual + 0.0005 >= planned;
}

/** What the API will actually store for a typed kg value.
 *
 * `POST /api/feeding/settings` answers 204, so a confirmation can only be
 * truthful by applying the server's own rounding — schemas/common.py does
 * `Decimal(str(value)).quantize("0.001", ROUND_HALF_UP)`. That rounds the
 * decimal the operator typed, not the binary double, so `toFixed(3)` is not
 * equivalent: it reports 1.2345 as 1.234 where the server stores 1.235. */
function quantizePersistedKg(value: number): number {
  const match = /^(-?)(\d+)\.(\d+)$/.exec(String(value));
  if (!match) return value;
  const [, sign, whole, fraction] = match;
  if (fraction.length <= 3) return value;
  const magnitude = (Number(whole + fraction.slice(0, 3)) + (fraction[3] >= "5" ? 1 : 0)) / 1000;
  return sign === "-" ? -magnitude : magnitude;
}

/** The generated PlanLineOutShiftsItem is schema-less ({[key: string]:
 *  unknown}), so validate the fields the plan table renders instead of
 *  casting. Malformed cells degrade to visible placeholders rather than
 *  crashing the row or shifting the Morning/Afternoon/Night columns. */
function toShiftCell(raw: unknown): ShiftCell {
  const cell = (raw ?? {}) as Record<string, unknown>;
  return {
    shift: typeof cell.shift === "string" ? cell.shift : "?",
    kg: typeof cell.kg === "number" ? cell.kg : 0,
    time: typeof cell.time === "string" ? cell.time : "",
  };
}

/** Sub-label notes of a plan line: a "scaled from mean" hint when the ration
 *  is weight-based, and the plan note when present. Only captured facts
 *  render — a flat line with no note stays as terse as before. The creep
 *  band joins the recipe label itself (see the label render sites). */
function planLineNotes(line: PlanLineOut): string[] {
  return [
    line.basis === PlanLineOutBasis.weight && line.mean_weight_kg != null
      ? `scaled from mean ${formatPersistedKg(line.mean_weight_kg)} kg`
      : null,
    line.note ?? null,
  ].filter((note): note is string => note !== null);
}



const settingSchema = z.object({
  daily_kg_per_head: z.coerce
    .number()
    .positive("kg/head must be greater than 0")
    .min(MIN_PERSISTED_KG, MIN_PERSISTED_KG_MESSAGE)
    // Mirrors QuantityKgFloat (le=1_000_000, schemas/common.py): a fat-fingered
    // quantity should fail inline instead of as an opaque server 422.
    .max(1_000_000, "Quantity cannot exceed 1,000,000 kg"),
});
type SettingInput = z.input<typeof settingSchema>;
type SettingValues = z.output<typeof settingSchema>;

/** Per-bucket kg/head override (feeding.manage). */
function KgPerHeadDialog({ line }: { line: PlanLineOut }) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const t = useT();
  const { language } = useLanguage();
  const mut = useSaveSettingApiFeedingSettingsPost();
  const settingFlight = useSingleFlight();
  const bucketLabel = enumLabel("bucket", line.bucket, language);
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
    // A background plan refetch may carry another operator's new value. Keep
    // the live draft authoritative while this dialog is open. The close
    // handler below adopts the latest server value for the next open.
    if (!open) reset({ daily_kg_per_head: line.kg_per_head });
    // `open` is deliberately not a dependency: a successful save resets to
    // the confirmed value and then closes while `line` still contains the old
    // pre-invalidation payload. Re-running solely because open became false
    // would immediately restore that stale value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line.kg_per_head, reset]);

  async function onSubmit(values: SettingValues) {
    await settingFlight.run(async () => {
      const farmScope = captureFarmScope();
      try {
        await mut.mutateAsync({
          data: {
            bucket: line.bucket as FeedSettingInBucket,
            daily_kg_per_head: values.daily_kg_per_head,
          },
        });
        if (!farmScope()) return;
        const stored = quantizePersistedKg(values.daily_kg_per_head);
        toast.success(t("feeding.savedToast", { kg: formatPersistedKg(stored), bucket: bucketLabel }));
        reset({ daily_kg_per_head: stored });
        invalidateFarmData(queryClient);
        setOpen(false);
      } catch (err) {
        if (!farmScope()) return;
        toast.error(mutationError(err, t("common.somethingWentWrong")));
      }
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && !settingFlight.pending) {
          reset({ daily_kg_per_head: line.kg_per_head });
        }
        setOpen(nextOpen);
      }}
    >
      <Button
        size="sm"
        variant="outline"
        className="ml-2"
        disabled={settingFlight.pending}
        onClick={() => setOpen(true)}
      >
        {t("feeding.edit")}
      </Button>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("feeding.dailyRation", { bucket: bucketLabel })}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          <fieldset disabled={isSubmitting || settingFlight.pending} className="contents">
          <div className="space-y-1.5">
            <Label htmlFor={`kg-${line.bucket}`}>{t("feeding.kgPerHead")}</Label>
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
            <Button type="submit" disabled={isSubmitting || settingFlight.pending}>
              {isSubmitting || settingFlight.pending ? t("feeding.saving") : t("feeding.save")}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

const dispenseSchema = z.object({
  // Derived from the generated enums so a new contract bucket/shift is
  // accepted the moment the selects offer it (was hand-copied — L20).
  bucket: z.enum([
    DispenseInBucket.QUARANTINE,
    DispenseInBucket.FOUNDATION,
    DispenseInBucket.BREEDING,
    DispenseInBucket.PREGNANCY_EARLY,
    DispenseInBucket.PREGNANCY_LATE,
    DispenseInBucket.DELIVERY,
    DispenseInBucket.RECOVERY,
    DispenseInBucket.RESTING,
    DispenseInBucket.MALE_KIDS,
    DispenseInBucket.FEMALE_KIDS,
  ]),
  shift: z.enum([
    DispenseInShift.MORNING,
    DispenseInShift.AFTERNOON,
    DispenseInShift.NIGHT,
  ]),
  recipe_code: z.string().min(1, "Pick a recipe"),
  qty_kg: z.coerce
    .number()
    .positive("Quantity must be greater than 0")
    .min(MIN_PERSISTED_KG, MIN_PERSISTED_KG_MESSAGE)
    // Mirrors QuantityKgFloat (le=1_000_000, schemas/common.py): a fat-fingered
    // quantity should fail inline instead of as an opaque server 422.
    .max(1_000_000, "Quantity cannot exceed 1,000,000 kg"),
  date: z
    .string()
    .min(1, "Date is required")
    .refine((s) => s <= farmToday(), "Date can't be in the future"),
});
type DispenseInput = z.input<typeof dispenseSchema>;
type DispenseValues = z.output<typeof dispenseSchema>;

function FeedingPageContent({ perms }: { perms: PermissionsState }) {
  const { can } = perms;
  const t = useT();
  const { language } = useLanguage();
  const allowed = can("feeding.view");
  const canManage = can("feeding.manage");
  const canCreateAnimals = can("animals.create");
  const queryClient = useQueryClient();
  /** value → label maps for the root `items` prop: without them, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const bucketItems: Record<string, string> = Object.fromEntries(
    Object.values(DispenseInBucket).map((b) => [b, enumLabel("bucket", b, language)]),
  );
  const shiftItems: Record<string, string> = Object.fromEntries(
    Object.values(DispenseInShift).map((s) => [s, enumLabel("shift", s, language)]),
  );

  // F-7: the dispensing-history window lives in the URL, so refresh and
  // shared links reopen the same slice of the ledger. State stays the source
  // of truth; edits write through with defaults stripped so a bare /feeding
  // URL stays bare. The URL is only ever replaced (never pushed), and any
  // params change this page did not itself write — a sidebar link back to
  // bare /feeding, or the browser restoring an entry — re-seeds the local
  // mirrors from the URL.
  const { get: getUrl, getNumber: getUrlNumber, set: setUrlState, searchParams } =
    useUrlState();
  const [dispenseOpen, setDispenseOpen] = useState(false);
  const [historyOffset, setHistoryOffset] = useState(() =>
    getUrlNumber("offset", 0, 0, MAX_PAGE_OFFSET),
  );
  const [dateFrom, setDateFrom] = useState(() => urlDate(getUrl("date_from")));
  const [dateTo, setDateTo] = useState(() => urlDate(getUrl("date_to")));
  const paramsKey = searchParams.toString();
  const lastWrittenParamsRef = useRef(paramsKey);
  // Latest-ref the URL readers: adoption must key on the params string
  // itself changing, not on the reader identities (which churn every render
  // when a navigation mock hands out fresh params objects).
  const getUrlRef = useRef(getUrl);
  const getUrlNumberRef = useRef(getUrlNumber);
  useEffect(() => {
    getUrlRef.current = getUrl;
    getUrlNumberRef.current = getUrlNumber;
  });
  useEffect(() => {
    if (lastWrittenParamsRef.current === paramsKey) return;
    lastWrittenParamsRef.current = paramsKey;
    setHistoryOffset(getUrlNumberRef.current("offset", 0, 0, MAX_PAGE_OFFSET));
    setDateFrom(urlDate(getUrlRef.current("date_from")));
    setDateTo(urlDate(getUrlRef.current("date_to")));
  }, [paramsKey]);
  /** Write-through that remembers which params string this page authored,
   * so the adopt-effect above only fires for external URL changes. */
  const writeUrlState = useCallback(
    (updates: UrlStateUpdate) => {
      const qs = setUrlState(updates);
      if (qs !== null) lastWrittenParamsRef.current = qs;
    },
    [setUrlState],
  );
  const historyLimit = 50;
  const invalidHistoryRange = Boolean(dateFrom && dateTo && dateFrom > dateTo);

  function changeDateFrom(value: string) {
    setDateFrom(value);
    setHistoryOffset(0);
    writeUrlState({ date_from: value || null, offset: null });
  }

  function changeDateTo(value: string) {
    setDateTo(value);
    setHistoryOffset(0);
    writeUrlState({ date_to: value || null, offset: null });
  }

  /** One-shot reset shared by the toolbar control and the empty-state CTA. */
  function clearHistoryDates() {
    setDateFrom("");
    setDateTo("");
    setHistoryOffset(0);
    writeUrlState({ date_from: null, date_to: null, offset: null });
  }

  const changeHistoryOffset = useCallback(
    (next: number) => {
      setHistoryOffset(next);
      writeUrlState({ offset: next || null });
    },
    [writeUrlState],
  );

  const query = useFeedingTodayApiFeedingPlanGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const historyQuery = useFeedingHistoryApiFeedingRecordsGet(
    {
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      limit: historyLimit,
      offset: historyOffset,
    },
    {
      query: {
        enabled: allowed && !invalidHistoryRange,
        // Keep the previous page rendered while an offset/date change settles.
        placeholderData: (previous) => previous,
      },
    },
  );
  const history =
    historyQuery.data?.status === 200 ? historyQuery.data.data : undefined;

  // A stale or hand-edited offset beyond the last page (the ledger shrank,
  // or a shared link was trimmed) must not dead-end on a false "no records"
  // state — re-home it to the real last page, exactly as the scenario pager
  // does after deletions.
  useEffect(() => {
    if (!history || historyOffset === 0) return;
    if (historyOffset < history.total) return;
    const lastOffset =
      history.total === 0
        ? 0
        : Math.floor((history.total - 1) / historyLimit) * historyLimit;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    changeHistoryOffset(lastOffset);
  }, [history, historyOffset, changeHistoryOffset]);

  const recipesQuery = useListRecipesApiFeedingRecipesGet({ query: { enabled: canManage } });
  const recipes = recipesQuery.data?.status === 200 ? recipesQuery.data.data.recipes : [];
  const recipeOptions = new Map(recipes.map((recipe) => [recipe.code, recipe.name]));
  for (const line of payload?.lines ?? []) {
    recipeOptions.set(line.recipe_code, line.recipe_name);
  }
  // Virtual code (services/feeding.py): it has no feed_recipes row, so the
  // catalog never returns it, yet the API accepts it on any past date — offer
  // it unconditionally so a backdated dry-roughage ration can be recorded.
  recipeOptions.set(DRY_ROUGHAGE, "Dry roughage only");
  /** value → label map for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const recipeItems: Record<string, string> = Object.fromEntries(recipeOptions);

  const dispenseMutation = useDispenseApiFeedingDispensePost();
  // The submit button's only guard was react-hook-form's `isSubmitting`, which
  // the header trigger's reset() clears mid-flight — and that reset also
  // re-seeds bucket/recipe, changing the request body enough to mint a NEW
  // Idempotency-Key. This ref-backed guard is outside react-hook-form, so a
  // reset cannot destroy it.
  const dispenseFlight = useSingleFlight();
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<DispenseInput, unknown, DispenseValues>({
    resolver: zodResolver(dispenseSchema),
    defaultValues: { bucket: "QUARANTINE", shift: "MORNING", recipe_code: "", date: farmToday() },
  });
  const wBucket = useWatch({ control, name: "bucket" });
  const wRecipeCode = useWatch({ control, name: "recipe_code" });
  const wShift = useWatch({ control, name: "shift" });

  async function onDispense(values: DispenseValues) {
    await dispenseFlight.run(async () => {
      const farmScope = captureFarmScope();
      try {
        await dispenseMutation.mutateAsync({
          data: {
            bucket: values.bucket,
            shift: values.shift,
            recipe_code: values.recipe_code,
            qty_kg: values.qty_kg,
            date: values.date || null,
          },
        });
        if (!farmScope()) return;
        toast.success(t("feeding.dispensedToast"));
        invalidateFarmData(queryClient);
        setDispenseOpen(false);
      } catch (err) {
        if (!farmScope()) return;
        toast.error(mutationError(err, t("common.somethingWentWrong")));
      }
    });
  }

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div
          role="alert"
          className="space-y-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4"
        >
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : "Could not load the feeding plan."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry feeding plan
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title="Feeding — today"
          description="The 3-shift ration plan and what's been dispensed so far."
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading feeding plan…</span>
          <PageSkeleton cards={2} />
        </div>
      </div>
    );
  }

  const today = farmToday();
  const dispensedByAllocationShift = new Map<string, number>();
  const dispensedByBucket = new Map<string, number>();
  for (const total of payload.dispensed_totals) {
    if (total.recipe_code) {
      const key = allocationShiftKey(total.bucket, total.recipe_code, total.shift);
      dispensedByAllocationShift.set(
        key,
        (dispensedByAllocationShift.get(key) ?? 0) + total.qty_kg,
      );
    }
    dispensedByBucket.set(
      total.bucket,
      (dispensedByBucket.get(total.bucket) ?? 0) + total.qty_kg,
    );
  }
  const plannedByBucket = new Map<string, number>();
  const bucketAllocationState = new Map<string, { complete: number; total: number }>();
  for (const l of payload.lines) {
    plannedByBucket.set(l.bucket, (plannedByBucket.get(l.bucket) ?? 0) + l.daily_kg);
    const shifts = l.shifts.map(toShiftCell);
    const lineComplete =
      shifts.length > 0 &&
      shifts.every(
        (shift) =>
          meetsPlannedQuantity(
            dispensedByAllocationShift.get(
              allocationShiftKey(l.bucket, l.recipe_code, shift.shift),
            ) ?? 0,
            shift.kg,
          ),
      );
    const current = bucketAllocationState.get(l.bucket) ?? { complete: 0, total: 0 };
    bucketAllocationState.set(l.bucket, {
      complete: current.complete + (lineComplete ? 1 : 0),
      total: current.total + 1,
    });
  }

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title="Feeding — today"
        description="The 3-shift ration plan and what's been dispensed so far."
        actions={
          canManage && (
            <Button
              disabled={dispenseFlight.pending}
              onClick={() => {
                const firstLine = payload.lines[0];
                const firstRecipe = firstLine?.recipe_code ?? recipeOptions.keys().next().value ?? "";
                reset({
                  bucket: (firstLine?.bucket ?? "QUARANTINE") as DispenseValues["bucket"],
                  shift: "MORNING",
                  recipe_code: firstRecipe,
                  qty_kg: undefined,
                  date: farmToday(),
                });
                setDispenseOpen(true);
              }}
            >
              {t("feeding.recordDispensing")}
            </Button>
          )
        }
      />

      <FeedingNav active="plan" />

      {canManage && recipesQuery.isError && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm"
        >
          <span>
            Could not load the recipe catalog. Recipes already present in today&apos;s plan remain
            available; reload the catalog before recording any other ration.
          </span>
          <Button type="button" size="sm" variant="outline" onClick={() => void recipesQuery.refetch()}>
            Retry recipes
          </Button>
        </div>
      )}

      <DataTableCard
        title="Plan (headcount × kg/head, split 40 / 20 / 40)"
        description="Per-bucket rations split across the three daily shifts."
      >
        {payload.lines.length === 0 ? (
          <EmptyState
            icon={Wheat}
            title="No active animals — nothing to feed."
            description="Once animals are active, their ration plan will show up here."
          >
            {canCreateAnimals && (
              <Link href="/animals/new" className={buttonVariants({ variant: "outline", size: "sm" })}>
                Add animals
              </Link>
            )}
          </EmptyState>
        ) : (
          <div className="space-y-4">
            {payload.records.length < payload.records_total && (
              <p
                role="status"
                className="rounded-lg border border-warning/40 bg-warning-tint/50 px-3 py-2 text-sm text-warning-tint-foreground"
              >
                Today&apos;s dispensing log contains {payload.records_total} entries; the log below
                shows only the latest {payload.records.length} (response limit {payload.records_limit}).
                Plan progress and completion remain exact because they use full-day totals computed
                by the server.
              </p>
            )}
            <div>
              <p className="mb-2 text-xs text-muted-foreground">
                Bucket totals show volume. A bucket is complete only when every planned recipe and
                shift is complete.
              </p>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {[...plannedByBucket].map(([bucketName, planned]) => {
                  const recorded = dispensedByBucket.get(bucketName) ?? 0;
                  const allocation = bucketAllocationState.get(bucketName) ?? {
                    complete: 0,
                    total: 0,
                  };
                  const complete =
                    allocation.total > 0 && allocation.complete === allocation.total;
                  return (
                    <div
                      key={bucketName}
                      className="rounded-xl bg-card p-4 shadow-xs ring-1 ring-foreground/[0.07]"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">
                          {enumLabel("bucket", bucketName, language)}
                        </span>
                        <Badge variant={complete ? "default" : "secondary"}>
                          {complete
                            ? "complete"
                            : `${allocation.complete}/${allocation.total} rations`}
                        </Badge>
                      </div>
                      <p className="mt-1 tabular-nums">
                        {formatPersistedKg(recorded)} / {formatPersistedKg(planned)} kg recorded
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>
            {/* Below md the 9-column plan table becomes a card per ration —
             * panning an 860px table inside a 390px phone is not a plan, it's
             * a scroll toy. */}
            <div className="space-y-2 md:hidden">
              {payload.lines.map((line) => {
                const shifts = line.shifts.map(toShiftCell);
                let dispensed = 0;
                let lineComplete = shifts.length > 0;
                for (const shift of shifts) {
                  const recorded =
                    dispensedByAllocationShift.get(
                      allocationShiftKey(line.bucket, line.recipe_code, shift.shift),
                    ) ?? 0;
                  dispensed += recorded;
                  lineComplete = lineComplete && meetsPlannedQuantity(recorded, shift.kg);
                }
                return (
                  <div
                    key={`${line.bucket}:${line.recipe_code}`}
                    className="rounded-xl border bg-card p-3 shadow-xs"
                  >
                    <p className="font-medium">
                      {enumLabel("bucket", line.bucket, language)} — {line.recipe_name}
                      {line.creep_band && (
                        <span className="font-normal text-muted-foreground">
                          {" "}
                          ({line.creep_band})
                        </span>
                      )}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                      {line.heads} heads · {line.kg_per_head} kg/head ·{" "}
                      {formatPersistedKg(line.daily_kg)} kg/day
                    </p>
                    {planLineNotes(line).map((note) => (
                      <p key={note} className="mt-1 text-xs text-muted-foreground tabular-nums">
                        {note}
                      </p>
                    ))}
                    <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                      Recorded {formatPersistedKg(dispensed)} / {formatPersistedKg(line.daily_kg)} kg
                      {lineComplete ? " · Done" : ""}
                    </p>
                    {/* kg/head is the phone-side management action too — a
                     * feeding.manage user must not need a desktop to adjust
                     * rations. */}
                    {canManage && (
                      <div className="mt-2">
                        <KgPerHeadDialog line={line} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="hidden md:block">
            <Table className="min-w-[860px]">
            <TableHeader>
              <TableRow>
                <TableHead>Bucket</TableHead>
                <TableHead>Recipe today</TableHead>
                <TableHead className="text-right">Heads</TableHead>
                <TableHead className="text-right">kg/head/day</TableHead>
                <TableHead className="text-right">Daily kg</TableHead>
                <TableHead className="text-right">Morning recorded / planned</TableHead>
                <TableHead className="text-right">Afternoon recorded / planned</TableHead>
                <TableHead className="text-right">Night recorded / planned</TableHead>
                <TableHead className="text-right">Recipe total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.lines.map((line) => {
                const shifts = line.shifts.map(toShiftCell);
                const shiftProgress = shifts.map((shift) => ({
                  ...shift,
                  dispensed:
                    dispensedByAllocationShift.get(
                      allocationShiftKey(line.bucket, line.recipe_code, shift.shift),
                    ) ?? 0,
                }));
                const dispensed = shiftProgress.reduce((sum, shift) => sum + shift.dispensed, 0);
                const lineComplete =
                  shiftProgress.length > 0 &&
                  shiftProgress.every((shift) =>
                    meetsPlannedQuantity(shift.dispensed, shift.kg),
                  );
                return (
                  // One bucket can appear on several lines (split by recipe).
                  <TableRow key={`${line.bucket}:${line.recipe_code}`}>
                    <TableCell className="font-medium">
                      {enumLabel("bucket", line.bucket, language)}
                    </TableCell>
                    <TableCell>
                      {line.recipe_name}
                      {line.creep_band && (
                        <span className="text-xs text-muted-foreground">
                          {" "}
                          ({line.creep_band})
                        </span>
                      )}
                      {planLineNotes(line).map((note) => (
                        <span key={note} className="block text-xs text-muted-foreground">
                          {note}
                        </span>
                      ))}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{line.heads}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {line.kg_per_head}
                      {canManage && <KgPerHeadDialog line={line} />}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{line.daily_kg}</TableCell>
                    {shiftProgress.map((s, index) => (
                      <TableCell
                        key={`${s.shift}:${index}`}
                        title={`${s.shift} ${s.time}`}
                        className="text-right tabular-nums"
                      >
                        {formatPersistedKg(s.dispensed)} / {formatPersistedKg(s.kg)} kg
                      </TableCell>
                    ))}
                    <TableCell className="text-right tabular-nums">
                      {formatPersistedKg(dispensed)} / {formatPersistedKg(line.daily_kg)} kg
                      {lineComplete && (
                        <Badge variant="success" className="ml-2">
                          <Check aria-hidden="true" />
                          Done
                        </Badge>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
            </Table>
            </div>
          </div>
        )}
        <p className="mt-3 text-sm text-muted-foreground">
          {t("feeding.shiftsLine", {
            morning: enumLabel("shift", "MORNING", language),
            afternoon: enumLabel("shift", "AFTERNOON", language),
            night: enumLabel("shift", "NIGHT", language),
          })}{" "}
          {/* MAINTENANCE/FLUSH and frame-builder/fattening are recipe names,
           * not herd buckets — they come from the catalog, not enumLabel. */}
          {t("feeding.rotationNote", {
            resting: enumLabel("bucket", "RESTING", language),
            maintenance: t("feeding.phase.maintenance"),
            flush: t("feeding.phase.flush"),
            maleKids: enumLabel("bucket", "MALE_KIDS", language),
            frameBuilder: t("feeding.frameBuilder"),
            fattening: t("feeding.fattening"),
          })}
        </p>
      </DataTableCard>

      <DataTableCard title={`Today's dispensing log (${payload.records_total})`}>
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
                  <TableCell>{enumLabel("shift", r.shift, language)}</TableCell>
                  <TableCell>{enumLabel("bucket", r.bucket, language)}</TableCell>
                  <TableCell>{r.recipe_code ?? "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatPersistedKg(r.qty_kg)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {payload.records.length < payload.records_total && (
          <p className="mt-3 text-sm text-muted-foreground">
            Showing the latest {payload.records.length} of {payload.records_total} entries. Use
            dispensing history below to browse the full ledger.
          </p>
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
              onChange={(event) => changeDateFrom(event.target.value)}
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
              onChange={(event) => changeDateTo(event.target.value)}
            />
          </div>
          <Button
            type="button"
            variant="outline"
            disabled={!dateFrom && !dateTo}
            onClick={clearHistoryDates}
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
          <InlineLoading className="justify-center py-6">Loading history…</InlineLoading>
        )}
        {!invalidHistoryRange && historyQuery.isPlaceholderData && (
          <InlineLoading className="py-2">Updating dispensing history…</InlineLoading>
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
          >
            {(dateFrom || dateTo) && (
              <Button type="button" variant="outline" size="sm" onClick={clearHistoryDates}>
                Clear dates
              </Button>
            )}
          </EmptyState>
        )}
        {!invalidHistoryRange && history && history.records.length > 0 && (
          <>
            <Table className="min-w-[640px]">
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
                    <TableCell>{enumLabel("shift", record.shift, language)}</TableCell>
                    <TableCell>{enumLabel("bucket", record.bucket, language)}</TableCell>
                    <TableCell>{record.recipe_code ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatPersistedKg(record.qty_kg)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <PaginationControls
              total={history.total}
              limit={history.limit}
              offset={history.offset}
              onOffsetChange={changeHistoryOffset}
              label="dispensing records"
              disabled={historyQuery.isPlaceholderData}
            />
          </>
        )}
      </DataTableCard>

      <Dialog open={dispenseOpen} onOpenChange={setDispenseOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("feeding.recordDispensing")}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit(onDispense)} className="space-y-4" noValidate>
            <fieldset disabled={isSubmitting || dispenseFlight.pending} className="contents">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="dispense-bucket">Bucket</Label>
                <Select
                  value={wBucket}
                  items={bucketItems}
                  onValueChange={(v) => {
                    setValue("bucket", v as DispenseValues["bucket"], { shouldValidate: true });
                    // A bucket split by age/day carries several plan lines;
                    // only prefill when the plan is unambiguous, otherwise the
                    // operator would silently debit the wrong ration.
                    const plannedRecipes = new Set(
                      payload.lines
                        .filter((line) => line.bucket === v)
                        .map((line) => line.recipe_code),
                    );
                    const [plannedRecipe] = plannedRecipes;
                    setValue("recipe_code", plannedRecipes.size === 1 ? plannedRecipe : "", {
                      shouldValidate: true,
                    });
                  }}
                >
                  <SelectTrigger id="dispense-bucket" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.values(DispenseInBucket).map((b) => (
                      <SelectItem key={b} value={b}>
                        {enumLabel("bucket", b, language)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="dispense-shift">Shift</Label>
                <Select
                  value={wShift}
                  items={shiftItems}
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
                        {enumLabel("shift", s, language)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="dispense-recipe">Recipe *</Label>
                <Select
                  value={wRecipeCode}
                  onValueChange={(v) => setValue("recipe_code", v, { shouldValidate: true })}
                  items={recipeItems}
                >
                  <SelectTrigger id="dispense-recipe" className="w-full">
                    <SelectValue placeholder="recipe…" />
                  </SelectTrigger>
                  <SelectContent>
                    {[...recipeOptions].map(([code, name]) => (
                      <SelectItem key={code} value={code}>
                        {name}
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
                <Label htmlFor="qty_kg">Quantity (kg) *</Label>
                <Input
                  id="qty_kg"
                  type="number"
                  step="0.001"
                  min="0.0005"
                  max="1000000"
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
              <Button type="submit" disabled={isSubmitting || dispenseFlight.pending}>
                {isSubmitting || dispenseFlight.pending
                  ? t("feeding.recording")
                  : t("feeding.record")}
              </Button>
            </DialogFooter>
            </fieldset>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Suspense boundary required because the content reads useSearchParams(). */
export default function FeedingPage() {
  const perms = usePermissions();
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <PageHeader
            title="Feeding"
            description="Today's three-shift feed plan, dispensing log and ration settings."
          />
          <div role="status" aria-live="polite">
            <span className="sr-only">Loading feeding plan…</span>
            <PageSkeleton cards={2} />
          </div>
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="feeding.view"
        label="Feeding — today"
        description="The 3-shift ration plan and what's been dispensed so far."
        cards={2}
      >
        <FeedingPageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
