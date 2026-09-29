"use client";

/** Purchase batches — list + new-batch form + per-batch detail (animals created,
 * open quarantine tasks). Parity with v1 purchases/list.html + new.html + detail.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, PawPrint, Plus, ShoppingCart } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useCallback, useMemo, useRef, useState } from "react";
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
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
import { PaginationControls } from "@/components/pagination-controls";
import { InlineLoading, PageSkeleton, TableSkeleton } from "@/components/skeletons";
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
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api-client";
import { useMutationError } from "@/lib/mutations";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { MAX_AGE_MONTHS, MAX_BATCH_COUNT, MAX_TRANSPORT_HOURS } from "@/lib/backend-caps";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { enumLabel } from "@/lib/enum-labels";
import { useLanguage, useT, type TFn } from "@/lib/i18n";
import { mapServerError } from "@/lib/server-error-phrases";
import { resolveTaskTitle } from "@/lib/task-title";
import { farmToday, formatDate, formatMoney } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { isPersistableNonnegativeMoney } from "@/lib/persisted-numbers";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { MAX_PAGE_OFFSET, useUrlState, type UrlStateUpdate } from "@/lib/use-url-state";



/** Optional non-negative number: blank → undefined (same shape as backend schemas). */
const optNum = (schema: z.ZodNumber) =>
  z.preprocess(
    // Stryker disable next-line ConditionalExpression: registered number inputs only ever yield "" or a numeric string — null/undefined never arrive, and the blank arm is pinned by the payload campaign test
    (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)),
    schema.optional(),
  );

/** Deep-link batch ids arrive as raw query strings; anything that is not a
 * positive safe integer written in plain digits is ignored, so JavaScript's
 * permissive Number() syntax ("1e2", "0x64") can never silently target a
 * batch the link did not name (RT-P2-3). */
function parseBatchId(raw: string | null): number | null {
  // Stryker disable next-line ConditionalExpression: the regex rejects null (coerced "null") exactly like any non-digit string, so the === null arm never decides anything
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed >= 1 ? parsed : null;
}

/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value ("F") in the closed trigger. Labels
 * resolve through the shared sex enum labels in the active language. */
const sexItems = (language: "en" | "te"): Record<string, string> => ({
  [PurchaseBatchInSex.F]: enumLabel("sex", PurchaseBatchInSex.F, language),
  [PurchaseBatchInSex.M]: enumLabel("sex", PurchaseBatchInSex.M, language),
});

// Bounds mirror backend/app/schemas/purchases.py (count 1..1000, age 0..240,
// prices ≥ 0, date year ≥ 2000 and not in the future). avg_weight_kg is
// species-scaled client-side to max_adult_weight_kg; the wire schema still
// hard-caps at 1000 kg and the API re-checks the species cap.
/** One arrival weight per non-empty line, trimmed. Empty textarea → no lines. */
function weightLines(raw: string | undefined): string[] {
  return (raw ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/** Credibility band for a single arrival weight: the species' adult scale
 * floors it above a typo'd 0 and caps it at the same max_adult_weight_kg the
 * average-weight field uses. */
const MIN_ARRIVAL_WEIGHT_KG = 0.1;

/** Species-scaled average-weight cap (max_adult_weight_kg): the backend
 * rejects a batch average above the farm species' credible adult scale.
 * The messages resolve through the i18n catalog, so the factory takes the
 * caller's `t` and the mounted page rebuilds it per language (health page
 * precedent). */
const buildBatchSchema = (t: TFn, maxWeightKg: number) =>
  z
  .object({
    date: z.string().min(1, t("purchases.validation.dateRequired")),
    supplier: z.string().max(120, t("purchases.validation.textMax", { max: 120 })).optional(),
    origin_market: z.string().max(120, t("purchases.validation.textMax", { max: 120 })).optional(),
    // StrictInt on the wire: whole hours only, 0–240 (MAX_TRANSPORT_HOURS).
    transport_hours: optNum(
      z
        .number()
        .int(t("purchases.validation.wholeHours"))
        .min(0, t("purchases.validation.nonnegative"))
        .max(MAX_TRANSPORT_HOURS, t("purchases.validation.transportMax", { max: MAX_TRANSPORT_HOURS })),
    ),
    // Prior vaccinations/deworming reported by the seller at source.
    seller_health_history: z
      .string()
      .max(4_000, t("purchases.validation.historyMax", { max: 4_000 }))
      .optional(),
    count: z.coerce
      .number()
      .int(t("purchases.validation.countWhole"))
      .min(1, t("purchases.validation.countMin"))
      .max(MAX_BATCH_COUNT, t("purchases.validation.countMax", { max: MAX_BATCH_COUNT })),
    sex: z.enum([PurchaseBatchInSex.F, PurchaseBatchInSex.M]),
    avg_age_months: optNum(
      z.number().min(0, t("purchases.validation.nonnegative")).max(MAX_AGE_MONTHS, t("purchases.validation.ageMax", { max: MAX_AGE_MONTHS })),
    ),
    avg_weight_kg: optNum(
      z.number().min(0, t("purchases.validation.nonnegative")).max(maxWeightKg, t("purchases.validation.weightMax", { max: maxWeightKg })),
    ),
    // One weight per line; parsed against `count` in the superRefine below.
    individual_weights: z.string().optional(),
    total_price: optNum(
      z
        .number()
        .min(0, t("purchases.validation.nonnegative"))
        .max(1_000_000_000, t("purchases.validation.priceMax"))
        .refine(isPersistableNonnegativeMoney, t("purchases.validation.moneyMin")),
    ),
    notes: z.string().max(4_000, t("purchases.validation.notesMax", { max: 4_000 })).optional(),
    create_animals: z.boolean(),
  })
  .superRefine((values, ctx) => {
    const lines = weightLines(values.individual_weights);
    if (lines.length === 0) return;
    // Per-head arrival weights only make sense as a complete set written to
    // the created stub animals, mirroring _individual_weights_are_plausible.
    if (!values.create_animals) {
      ctx.addIssue({
        code: "custom",
        path: ["individual_weights"],
        message: t("purchases.validation.weightsNeedStubs"),
      });
      return;
    }
    if (lines.length !== values.count) {
      ctx.addIssue({
        code: "custom",
        path: ["individual_weights"],
        message: t("purchases.validation.weightsCount", {
          count: values.count,
          got: lines.length,
        }),
      });
      return;
    }
    for (const [index, line] of lines.entries()) {
      const weight = Number(line);
      if (!Number.isFinite(weight) || weight < MIN_ARRIVAL_WEIGHT_KG || weight > maxWeightKg) {
        ctx.addIssue({
          code: "custom",
          path: ["individual_weights"],
          message: t("purchases.validation.weightLineRange", {
            line: index + 1,
            min: MIN_ARRIVAL_WEIGHT_KG,
            max: maxWeightKg,
          }),
        });
        return;
      }
    }
  })
  .refine((v) => !v.date || Number(v.date.slice(0, 4)) >= 2000, {
    message: t("purchases.validation.dateYearMin"),
    path: ["date"],
  })
  .refine((v) => !v.date || v.date <= farmToday(), {
    message: t("purchases.validation.dateFuture"),
    path: ["date"],
  });
type BatchInput = z.input<ReturnType<typeof buildBatchSchema>>;
type BatchValues = z.output<ReturnType<typeof buildBatchSchema>>;

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
  // Bucket short labels are shared, so
  // the chip resolves through the active farm's vocabulary.
  // A batch may hold up to MAX_BATCH_COUNT head, so its animals arrive as a
  // bounded page. The parent keys this component by batch id, so opening a
  // different batch remounts it and the offset starts at zero again.
  const { language } = useLanguage();
  const t = useT();
  const ANIMALS_LIMIT = 100;
  const [animalsOffset, setAnimalsOffset] = useState(0);
  const query = useBatchDetailApiPurchasesBatchIdGet(
    batchId ?? 0,
    { animals_limit: ANIMALS_LIMIT, animals_offset: animalsOffset },
    {
      query: {
        enabled: batchId !== null,
        // The animals offset lives in the key; keep the previous page while a
        // page turn inside the dialog settles (M-12).
        placeholderData: (previous) => previous,
      },
    },
  );
  const detailSettling = query.isPlaceholderData;
  const detail = query.data?.status === 200 ? query.data.data : undefined;
  // Stryker disable next-line ArrayDeclaration: junk fallback strings fail the t.status === "PENDING" filter, yielding the same empty task list as the empty array
  const openTasks = (detail?.tasks ?? []).filter((t) => t.status === "PENDING");
  // Stryker disable ConditionalExpression, LogicalOperator: this content only renders while the dialog is open (batchId non-null), so the first operand's variants are unreachable
  const detailLoading = batchId !== null && (query.isLoading || !detail);
  // Stryker restore ConditionalExpression, LogicalOperator

  return (
    <Dialog open={batchId !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("purchases.detail.title", { id: batchId ?? "—" })}</DialogTitle>
        </DialogHeader>
        {detailLoading ? (
          query.isError ? (
            <p className="text-sm text-destructive">
              {query.error instanceof ApiError
                ? mapServerError(t, query.error.detail, query.error.status, query.error.code)
                : t("purchases.detail.loadFailed")}
            </p>
          ) : (
            <InlineLoading className="justify-center py-6">{t("purchases.detail.loading")}</InlineLoading>
          )
        ) : (
          detail && (
            <div className="space-y-5">
              {query.isError && (
                <StaleDataNotice onRetry={() => void query.refetch()} />
              )}
              <p className="text-sm text-muted-foreground">
                {formatDate(detail.batch.date)}
                {detail.batch.supplier ? ` · ${detail.batch.supplier}` : ""} ·{" "}
                {t("purchases.list.headCount", { count: detail.batch.count })}
                {detail.batch.total_price !== null
                  ? ` · ${formatMoney(detail.batch.total_price)}`
                  : ""}
              </p>

              <section className="space-y-2">
                <h3 className="font-medium">
                  {t("purchases.detail.animalsCreated", { count: detail.animals_total })}
                </h3>
                {detail.animals.length === 0 ? (
                  <EmptyState
                    icon={PawPrint}
                    title={t("purchases.detail.noAnimals")}
                    description={t("purchases.detail.noAnimalsDescription")}
                    className="py-8"
                  />
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{t("purchases.detail.colTag")}</TableHead>
                        <TableHead>{t("purchases.detail.colSex")}</TableHead>
                        <TableHead>{t("purchases.detail.colBucket")}</TableHead>
                        <TableHead>{t("purchases.detail.colStatus")}</TableHead>
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
                          <TableCell>{enumLabel("sex", a.sex, language)}</TableCell>
                          <TableCell>{enumLabel("bucket", a.current_bucket, language)}</TableCell>
                          <TableCell>
                            <StatusBadge status={a.status}>
                              {enumLabel("status", a.status, language)}
                            </StatusBadge>
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
                  label={t("purchases.detail.paginationLabel")}
                  disabled={detailSettling}
                />
              </section>

              <section className="space-y-2">
                <h3 className="font-medium">
                  {t("purchases.detail.openTasks", { count: openTasks.length })}
                </h3>
                {openTasks.length === 0 ? (
                  <EmptyState
                    icon={CheckCircle2}
                    title={t("purchases.detail.noTasks")}
                    description={t("purchases.detail.noTasksDescription")}
                    className="py-8"
                  />
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{t("purchases.detail.colDue")}</TableHead>
                        <TableHead>{t("purchases.detail.colTask")}</TableHead>
                        <TableHead>{t("purchases.detail.colStatus")}</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {openTasks.map((t) => (
                        <TableRow key={t.id}>
                          <TableCell>{formatDate(t.due_date)}</TableCell>
                          <TableCell>{resolveTaskTitle(t, language)}</TableCell>
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

function PurchasesPageContent({ perms }: { perms: PermissionsState }) {
  const mutationErrorMessage = useMutationError();
  const t = useT();
  const { language } = useLanguage();
  const vocabulary = farmVocabulary;
  const { can } = perms;
  const allowed = can("purchases.view");
  const canManage = can("purchases.manage");
  const canViewAnimals = can("animals.view");
  const queryClient = useQueryClient();

  const [open, setOpen] = useState(false);
  const [pendingBatch, setPendingBatch] = useState<BatchValues | null>(null);
  /** Open/submit cycle fence: a late success must not close/reset a dialog
   *  the operator has since reopened and re-filled (finance addAttempt). */
  const createAttempt = useRef(0);
  // F-7: the list page and the open batch detail live in the URL, so
  // refresh and shared links reopen the same view. State stays the source of
  // truth; edits write through with defaults stripped. The URL is only ever
  // replaced (never pushed) — Back returns to the page, not to a previous
  // page number.
  const { get: getUrl, getNumber: getUrlNumber, set: setUrlState, searchParams } =
    useUrlState();
  const [detailId, setDetailId] = useState<number | null>(() =>
    parseBatchId(getUrl("batch")),
  );
  const [offset, setOffset] = useState(() =>
    getUrlNumber("offset", 0, 0, MAX_PAGE_OFFSET),
  );
  const limit = 50;

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
    // Stryker disable next-line ConditionalExpression: re-running for this page's own writes re-applies values the page already holds, so the guard only saves a no-op state write
    if (lastWrittenParamsRef.current === paramsKey) return;
    lastWrittenParamsRef.current = paramsKey;
    setDetailId(parseBatchId(getUrlRef.current("batch")));
    setOffset(getUrlNumberRef.current("offset", 0, 0, MAX_PAGE_OFFSET));
  }, [paramsKey]);
  /** Write-through that remembers which params string this page authored,
   * so the adopt-effect above only fires for external URL changes. */
  const writeUrlState = useCallback(
    (updates: UrlStateUpdate) => {
      const qs = setUrlState(updates);
      // Stryker disable next-line ConditionalExpression, EqualityOperator: a stale or null last-written marker only makes the adopt-effect re-apply values the page already holds
      if (qs !== null) lastWrittenParamsRef.current = qs;
    },
    // Stryker disable next-line ArrayDeclaration: setUrlState is a stable useCallback in useUrlState, so the dep list's contents cannot change behavior
    [setUrlState],
  );

  function openDetail(id: number) {
    setDetailId(id);
    writeUrlState({ batch: id });
  }

  function closeDetail() {
    setDetailId(null);
    writeUrlState({ batch: null });
  }

  function changeOffset(next: number) {
    setOffset(next);
    writeUrlState({ offset: next || null });
  }

  const query = useListBatchesApiPurchasesGet(
    { limit, offset },
    {
      query: {
        enabled: allowed,
        // Keep the previous page rendered while a page turn settles (M-12).
        placeholderData: (previous) => previous,
      },
    },
  );
  const listSettling = query.isPlaceholderData;
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  const createMutation = useCreateBatchApiPurchasesNewPost();
  const createFlight = useSingleFlight();
  // Rebuilt when the language changes so client-side validation messages
  // render in the active language; react-hook-form re-reads the resolver
  // option every render (health page precedent).
  const localizedSchema = useMemo(
    () => buildBatchSchema(t, vocabulary.facts.maxWeightKg),
    [t, vocabulary.facts.maxWeightKg],
  );
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<BatchInput, unknown, BatchValues>({
    resolver: zodResolver(localizedSchema),
    // Stryker disable ObjectLiteral, BooleanLiteral: every dialog open passes through openNewBatch, whose reset() re-applies these exact defaults before the form is ever visible
    defaultValues: {
      date: farmToday(),
      count: 1,
      sex: PurchaseBatchInSex.F,
      create_animals: true,
    },
    // Stryker restore ObjectLiteral, BooleanLiteral
  });
  const wCreateAnimals = useWatch({ control, name: "create_animals" });
  // z.coerce.number() types the pre-parse input as unknown; the field only
  // ever holds the numeric string the number input yields.
  const wCount = useWatch({ control, name: "count" }) as number | undefined;

  function onReview(values: BatchValues) {
    setPendingBatch(values);
  }

  async function createBatch(values: BatchValues) {
    await createFlight.run(async () => {
      const farmScope = captureFarmScope();
      // Stryker disable next-line UpdateOperator: a monotonically decreasing attempt counter mismatches a captured value exactly as reliably as an increasing one
      const attempt = ++createAttempt.current;
      try {
        await createMutation.mutateAsync({
          data: {
            date: values.date,
            // Stryker disable next-line OptionalChaining: the supplier input is registered unconditionally, so the value is a string, never undefined
  supplier: values.supplier?.trim() ? values.supplier.trim() : null,
            // Stryker disable next-line OptionalChaining: the origin-market input is registered unconditionally, so the value is a string, never undefined
  origin_market: values.origin_market?.trim() ? values.origin_market.trim() : null,
            transport_hours: values.transport_hours ?? null,
            // Stryker disable next-line OptionalChaining: the history textarea is registered unconditionally, so the value is a string, never undefined
  seller_health_history: values.seller_health_history?.trim()
              ? values.seller_health_history.trim()
              : null,
            count: values.count,
            sex: values.sex,
            avg_age_months: values.avg_age_months ?? null,
            avg_weight_kg: values.avg_weight_kg ?? null,
            individual_weights_kg:
              weightLines(values.individual_weights).length > 0
                ? weightLines(values.individual_weights).map(Number)
                : null,
            total_price: values.total_price ?? null,
            // Stryker disable next-line OptionalChaining: the notes input is registered unconditionally, so the value is a string, never undefined
  notes: values.notes?.trim() ? values.notes.trim() : null,
            create_animals: values.create_animals,
          },
        });
        if (!farmScope()) return;
        toast.success(t("purchases.toast.created"));
        invalidateFarmData(queryClient);
        // Stryker disable ConditionalExpression, CallExpression: openNewBatch refuses to open while the flight is pending and the dialog cannot otherwise reopen, so no fresh session exists for a stale continuation to close or clear — the attempt arms are unreachable defense-in-depth
        if (createAttempt.current !== attempt) return;
        setOpen(false);
        setPendingBatch(null);
        // Stryker restore ConditionalExpression, CallExpression
        // Stryker disable ObjectLiteral, BooleanLiteral, CallExpression: openNewBatch re-applies these exact defaults on the next open before the form is ever visible, so the post-success reset is redundant
        reset({
          date: farmToday(),
          count: 1,
          sex: PurchaseBatchInSex.F,
          create_animals: true,
        });
        // Stryker restore ObjectLiteral, BooleanLiteral
      } catch (err) {
        if (createAttempt.current !== attempt || !farmScope()) return;
        toast.error(mutationErrorMessage(err));
      }
    });
  }

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError
              ? mapServerError(t, query.error.detail, query.error.status, query.error.code)
              : t("purchases.page.loadFailed")}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            {t("purchases.page.retry")}
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("purchases.page.title")}
          description={t("purchases.page.description", { species: t("purchases.speciesPlural") })}
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("purchases.page.loading")}</span>
          <TableSkeleton />
        </div>
      </div>
    );
  }

  const batches = payload.batches;

  function openNewBatch() {
    // Stryker disable next-line ConditionalExpression: the page's New-batch button sits inert behind the open modal whenever a create is pending, so the guard is unreachable
    if (createFlight.pending) return;
    reset({
      date: farmToday(),
      count: 1,
      sex: PurchaseBatchInSex.F,
      create_animals: true,
    });
    // Stryker disable next-line CallExpression: the dialog's own dismissal handler clears pendingBatch on every close (the only way a new-batch session ends), so the open-time clear is redundant
    setPendingBatch(null);
    setOpen(true);
  }

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title={t("purchases.page.title")}
        description={t("purchases.page.description", { species: t("purchases.speciesPlural") })}
        actions={
          canManage && (
            <Button disabled={createFlight.pending} onClick={openNewBatch}>
              <Plus /> {t("purchases.page.newBatch")}
            </Button>
          )
        }
      />

      {batches.length === 0 ? (
        <EmptyState
          icon={ShoppingCart}
          title={t("purchases.empty.title")}
          description={t("purchases.empty.description")}
        >
          {canManage && (
            <Button disabled={createFlight.pending} onClick={openNewBatch}>
              <Plus /> {t("purchases.empty.addFirst")}
            </Button>
          )}
        </EmptyState>
      ) : (
        <DataTableCard
          title={t("purchases.list.title")}
          description={
            payload.total === 1
              ? t("purchases.list.description_one", { count: payload.total })
              : t("purchases.list.description_many", { count: payload.total })
          }
        >
          {/* Below md the 10-column batch table becomes a card per batch —
           * panning a 980px table inside a 390px phone is not a list, it's a
           * scroll toy. */}
          <div className="space-y-2 md:hidden">
            {batches.map((b) => (
              <div key={b.id} className="rounded-xl border bg-card p-3 shadow-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">
                    #{b.id} · {formatDate(b.date)}
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => openDetail(b.id)}
                  >
                    {t("purchases.table.view")}
                  </Button>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {b.supplier ?? t("purchases.list.noSupplier")} ·{" "}
                  {t("purchases.list.headCount", { count: b.count })}
                </p>
                <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                  {b.total_price !== null ? formatMoney(b.total_price) : t("purchases.list.noPrice")}
                  {b.open_tasks
                    ? b.open_tasks === 1
                      ? t("purchases.list.openTasks_one", { count: b.open_tasks })
                      : t("purchases.list.openTasks_many", { count: b.open_tasks })
                    : ""}
                </p>
                {/* Parity with the table's analytics columns — a phone
                 * shouldn't hide what was bought. */}
                <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                  {(b.animals_created ?? 0) === 1
                    ? t("purchases.list.cardLine_one", {
                        age: b.avg_age_months !== null ? t("purchases.list.ageMo", { months: b.avg_age_months }) : "—",
                        weight: b.avg_weight_kg !== null ? t("purchases.list.weightKg", { kg: b.avg_weight_kg }) : "—",
                        count: b.animals_created ?? 0,
                      })
                    : t("purchases.list.cardLine_many", {
                        age: b.avg_age_months !== null ? t("purchases.list.ageMo", { months: b.avg_age_months }) : "—",
                        weight: b.avg_weight_kg !== null ? t("purchases.list.weightKg", { kg: b.avg_weight_kg }) : "—",
                        count: b.animals_created ?? 0,
                      })}
                </p>
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[980px]">
            <TableHeader>
              <TableRow>
                <TableHead>{t("purchases.table.batch")}</TableHead>
                <TableHead>{t("purchases.table.date")}</TableHead>
                <TableHead>{t("purchases.table.supplier")}</TableHead>
                <TableHead className="text-right">{t("purchases.table.count")}</TableHead>
                <TableHead className="text-right">{t("purchases.table.avgAge")}</TableHead>
                <TableHead className="text-right">{t("purchases.table.avgWt")}</TableHead>
                <TableHead className="text-right">{t("purchases.table.totalPrice")}</TableHead>
                <TableHead className="text-right">{t("purchases.table.animals")}</TableHead>
                <TableHead className="text-right">{t("purchases.table.openTasks")}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {batches.map((b) => (
                <TableRow key={b.id}>
                  <TableCell className="font-medium">#{b.id}</TableCell>
                  <TableCell>{formatDate(b.date)}</TableCell>
                  <TableCell>{b.supplier ?? "—"}</TableCell>
                  <TableCell className="table-numeric text-right">{b.count}</TableCell>
                  <TableCell className="table-numeric text-right">
                    {b.avg_age_months !== null
                      ? t("purchases.list.ageMo", { months: b.avg_age_months })
                      : "—"}
                  </TableCell>
                  <TableCell className="table-numeric text-right">
                    {b.avg_weight_kg !== null
                      ? t("purchases.list.weightKg", { kg: b.avg_weight_kg })
                      : "—"}
                  </TableCell>
                  <TableCell className="table-numeric text-right">{formatMoney(b.total_price)}</TableCell>
                  <TableCell className="table-numeric text-right">{b.animals_created ?? 0}</TableCell>
                  <TableCell className="table-numeric text-right">
                    {b.open_tasks ? (
                      <Badge variant="secondary">{b.open_tasks}</Badge>
                    ) : (
                      (b.open_tasks ?? 0)
                    )}
                  </TableCell>
                  <TableCell>
                    <Button size="sm" variant="outline" onClick={() => openDetail(b.id)}>
                      {t("purchases.table.view")}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          {listSettling && (
            <p role="status" className="pt-3 text-sm text-muted-foreground">
              {t("purchases.list.updating")}
            </p>
          )}
          <PaginationControls
            total={payload.total}
            limit={payload.limit}
            offset={payload.offset}
            onOffsetChange={changeOffset}
            label={t("purchases.pagination.label")}
            disabled={listSettling}
          />
        </DataTableCard>
      )}

      {/* Keyed by batch so opening a different one remounts the dialog and its
          animal-page offset starts at the first page again. */}
      <BatchDetailDialog
        // Stryker disable next-line StringLiteral: the null-arm sentinel only needs any constant — no second dialog exists for a collision
        key={detailId ?? "none"}
        batchId={detailId}
        canViewAnimals={canViewAnimals}
        onClose={closeDetail}
      />

      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          // Dismissal supersedes any in-flight continuation for this session.
          // Stryker disable next-line BooleanLiteral, ConditionalExpression, AssignmentOperator: running the bump at open only moves it before any submit can capture it, and a decreasing counter mismatches a captured value exactly as reliably
          if (!nextOpen) createAttempt.current += 1;
          setOpen(nextOpen);
          // Stryker disable next-line BooleanLiteral, ConditionalExpression, CallExpression: openNewBatch clears pendingBatch on every open, so skipping the close-time clear is unobservable
          if (!nextOpen) setPendingBatch(null);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          {pendingBatch ? (
            <>
              <DialogHeader>
                <DialogTitle>{t("purchases.review.title")}</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-muted-foreground">
                {t("purchases.review.description")}
              </p>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-lg border p-4 text-sm">
                <dt className="text-muted-foreground">{t("purchases.review.batch")}</dt>
                <dd className="text-right font-medium">
                  {t("purchases.review.batchSummary", {
                    count: pendingBatch.count,
                    sex:
                      pendingBatch.sex === PurchaseBatchInSex.F
                        ? t("purchases.review.sexFemale")
                        : t("purchases.review.sexMale"),
                    species:
                      pendingBatch.count === 1
                        ? t("purchases.species")
                        : t("purchases.speciesPlural"),
                  })}
                </dd>
                <dt className="text-muted-foreground">{t("purchases.review.purchaseDate")}</dt>
                <dd className="text-right">{formatDate(pendingBatch.date)}</dd>
                <dt className="text-muted-foreground">{t("purchases.review.animalStubs")}</dt>
                <dd className="text-right">
                  {pendingBatch.create_animals
                    ? t("purchases.review.stubsInQuarantine", { count: pendingBatch.count })
                    : t("purchases.review.none")}
                </dd>
                <dt className="text-muted-foreground">{t("purchases.review.protocolTasks")}</dt>
                <dd className="text-right">
                  {pendingBatch.create_animals
                    ? t("purchases.review.quarantineSchedule")
                    : t("purchases.review.noneNoStubs")}
                </dd>
                <dt className="text-muted-foreground">{t("purchases.review.financeEntry")}</dt>
                <dd className="text-right">
                  {pendingBatch.total_price === undefined
                    ? t("purchases.review.noExpense")
                    : t("purchases.review.financeAmount", {
                        amount: formatMoney(pendingBatch.total_price),
                      })}
                </dd>
              </dl>
              <p role="alert" className="text-sm font-medium text-destructive">
                {t("purchases.review.noReversal")}
              </p>
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  disabled={createFlight.pending}
                  onClick={() => setPendingBatch(null)}
                >
                  {t("purchases.review.back")}
                </Button>
                <Button
                  type="button"
                  disabled={createFlight.pending}
                  onClick={() => void createBatch(pendingBatch)}
                >
                  {createFlight.pending ? t("purchases.review.creating") : t("purchases.review.confirm")}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>{t("purchases.dialog.title")}</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-muted-foreground">
                {wCreateAnimals
                  ? t("purchases.dialog.createAnimalsHint")
                  : t("purchases.dialog.noAnimalsHint")}
                {t("purchases.dialog.expenseHint")}
              </p>
              <form onSubmit={handleSubmit(onReview)} className="space-y-4" noValidate>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {t("purchases.form.sectionTitle")}
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="date">{t("purchases.form.date")}</Label>
                <Input
                  id="date"
                  type="date"
                  max={farmToday()}
                  aria-invalid={Boolean(errors.date) || undefined}
                  aria-describedby={errors.date ? "purchase-date-error" : undefined}
                  {...register("date")}
                />
                {errors.date && (
                  <p id="purchase-date-error" role="alert" className="text-sm text-destructive">
                    {errors.date.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="supplier">{t("purchases.form.supplier")}</Label>
                <Input
                  id="supplier"
                  maxLength={120}
                  aria-invalid={Boolean(errors.supplier) || undefined}
                  aria-describedby={errors.supplier ? "purchase-supplier-error" : undefined}
                  {...register("supplier")}
                />
                {errors.supplier && (
                  <p id="purchase-supplier-error" role="alert" className="text-sm text-destructive">
                    {errors.supplier.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="origin_market">{t("purchases.form.originMarket")}</Label>
                <Input
                  id="origin_market"
                  maxLength={120}
                  aria-invalid={Boolean(errors.origin_market) || undefined}
                  aria-describedby={errors.origin_market ? "purchase-origin-error" : undefined}
                  {...register("origin_market")}
                />
                {errors.origin_market && (
                  <p id="purchase-origin-error" role="alert" className="text-sm text-destructive">
                    {errors.origin_market.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="transport_hours">{t("purchases.form.transportHours")}</Label>
                <Input
                  id="transport_hours"
                  type="number"
                  step="1"
                  min="0"
                  max={String(MAX_TRANSPORT_HOURS)}
                  aria-invalid={Boolean(errors.transport_hours) || undefined}
                  aria-describedby={errors.transport_hours ? "purchase-transport-error" : undefined}
                  {...register("transport_hours")}
                />
                <p className="text-xs text-muted-foreground">
                  {t("purchases.form.transportHint", { max: MAX_TRANSPORT_HOURS })}
                </p>
                {errors.transport_hours && (
                  <p id="purchase-transport-error" role="alert" className="text-sm text-destructive">
                    {errors.transport_hours.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="count">{t("purchases.form.count")}</Label>
                <Input
                  id="count"
                  type="number"
                  min="1"
                  max={String(MAX_BATCH_COUNT)}
                  inputMode="numeric"
                  aria-invalid={Boolean(errors.count) || undefined}
                  aria-describedby={errors.count ? "purchase-count-error" : undefined}
                  {...register("count")}
                />
                {errors.count && (
                  <p id="purchase-count-error" role="alert" className="text-sm text-destructive">
                    {errors.count.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="purchase-sex">{t("purchases.form.sex")}</Label>
                <Controller
                  control={control}
                  name="sex"
                  render={({ field }) => (
                    <Select value={field.value} onValueChange={field.onChange} items={sexItems(language)}>
                      <SelectTrigger id="purchase-sex" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={PurchaseBatchInSex.F}>
                          {enumLabel("sex", PurchaseBatchInSex.F, language)}
                        </SelectItem>
                        <SelectItem value={PurchaseBatchInSex.M}>
                          {enumLabel("sex", PurchaseBatchInSex.M, language)}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  )}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="avg_age_months">{t("purchases.form.avgAge")}</Label>
                <Input
                  id="avg_age_months"
                  type="number"
                  step="0.5"
                  min="0"
                  max={String(MAX_AGE_MONTHS)}
                  {...register("avg_age_months")}
                />
                <p className="text-xs text-muted-foreground">
                  {t("purchases.form.avgAgeHint")}
                </p>
                {errors.avg_age_months && (
                  <p role="alert" className="text-sm text-destructive">
                    {errors.avg_age_months.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="avg_weight_kg">{t("purchases.form.avgWeight")}</Label>
                <Input
                  id="avg_weight_kg"
                  type="number"
                  step="0.1"
                  min="0"
                  max="1000"
                  {...register("avg_weight_kg")}
                />
                {errors.avg_weight_kg && (
                  <p role="alert" className="text-sm text-destructive">
                    {errors.avg_weight_kg.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="total_price">{t("purchases.form.totalPrice")}</Label>
                <Input
                  id="total_price"
                  type="number"
                  step="0.01"
                  min="0"
                  {...register("total_price")}
                />
                {errors.total_price && (
                  <p role="alert" className="text-sm text-destructive">
                    {errors.total_price.message}
                  </p>
                )}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="individual_weights">{t("purchases.form.weights")}</Label>
              <Textarea
                id="individual_weights"
                rows={3}
                disabled={!wCreateAnimals}
                placeholder={"24.5\n23.8"}
                aria-invalid={Boolean(errors.individual_weights) || undefined}
                aria-describedby={
                  errors.individual_weights ? "purchase-weights-error" : "purchase-weights-help"
                }
                {...register("individual_weights")}
              />
              <p id="purchase-weights-help" className="text-xs text-muted-foreground">
                {(wCount ?? 1) === 1
                  ? t("purchases.form.weightsHelp_one", { count: wCount ?? 1 })
                  : t("purchases.form.weightsHelp_many", { count: wCount ?? 1 })}
              </p>
              {errors.individual_weights && (
                <p id="purchase-weights-error" role="alert" className="text-sm text-destructive">
                  {errors.individual_weights.message}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="seller_health_history">{t("purchases.form.history")}</Label>
              <Textarea
                id="seller_health_history"
                rows={2}
                maxLength={4_000}
                aria-invalid={Boolean(errors.seller_health_history) || undefined}
                aria-describedby={
                  errors.seller_health_history ? "purchase-history-error" : undefined
                }
                {...register("seller_health_history")}
              />
              <p className="text-xs text-muted-foreground">
                {t("purchases.form.historyHint")}
              </p>
              {errors.seller_health_history && (
                <p id="purchase-history-error" role="alert" className="text-sm text-destructive">
                  {errors.seller_health_history.message}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notes">{t("purchases.form.notes")}</Label>
              <Input
                id="notes"
                maxLength={4_000}
                aria-invalid={Boolean(errors.notes) || undefined}
                aria-describedby={errors.notes ? "purchase-notes-error" : undefined}
                {...register("notes")}
              />
              {errors.notes && (
                <p id="purchase-notes-error" role="alert" className="text-sm text-destructive">
                  {errors.notes.message}
                </p>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="create_animals"
                checked={wCreateAnimals}
                onCheckedChange={(checked) => setValue("create_animals", checked === true)}
              />
              <Label htmlFor="create_animals" className="font-normal">
                {t("purchases.form.createStubs")}
              </Label>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? t("purchases.form.reviewing") : t("purchases.form.review")}
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

/** Suspense boundary required because the content reads useSearchParams(). */
export default function PurchasesPage() {
  const perms = usePermissions();
  const t = useT();
  return (
    <Suspense
      fallback={
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("common.loading")}</span>
          <PageSkeleton cards={1} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="purchases.view"
        label={t("purchases.page.title")}
        description={t("purchases.page.description", { species: t("purchases.speciesPlural") })}
        cards={1}
      >
        <PurchasesPageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
