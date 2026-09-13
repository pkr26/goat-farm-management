"use client";

/** Purchase batches — list + new-batch form + per-batch detail (animals created,
 * open quarantine tasks). Parity with v1 purchases/list.html + new.html + detail.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, PawPrint, Plus, ShoppingCart } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useCallback, useRef, useState } from "react";
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
import { ApiError } from "@/lib/api-client";
import { mutationError } from "@/lib/mutations";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { MAX_AGE_MONTHS, MAX_BATCH_COUNT } from "@/lib/backend-caps";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { enumLabel } from "@/lib/enum-labels";
import { farmToday, formatDate, formatMoney } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import {
  isPersistableNonnegativeMoney,
  MIN_PERSISTED_MONEY_MESSAGE,
} from "@/lib/persisted-numbers";
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

/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value ("F") in the closed trigger. */
const SEX_ITEMS: Record<string, string> = {
  [PurchaseBatchInSex.F]: "Female",
  [PurchaseBatchInSex.M]: "Male",
};

// Bounds mirror backend/app/schemas/purchases.py (count 1..1000, age 0..240,
// prices ≥ 0, date year ≥ 2000 and not in the future). avg_weight_kg is
// species-scaled client-side to max_adult_weight_kg; the wire schema still
// hard-caps at 1000 kg and the API re-checks the species cap.
/** Species-scaled average-weight cap (max_adult_weight_kg): the backend
 * rejects a batch average above the farm species' credible adult scale. */
const batchSchema = (maxWeightKg: number) =>
  z
  .object({
    date: z.string().min(1, "Date is required"),
    supplier: z.string().max(120, "At most 120 characters").optional(),
    count: z.coerce
      .number()
      .int("Count must be a whole number")
      .min(1, "At least 1 animal")
      .max(MAX_BATCH_COUNT, `At most ${MAX_BATCH_COUNT} animals`),
    sex: z.enum([PurchaseBatchInSex.F, PurchaseBatchInSex.M]),
    avg_age_months: optNum(
      z.number().min(0, "Cannot be negative").max(MAX_AGE_MONTHS, `At most ${MAX_AGE_MONTHS} months`),
    ),
    avg_weight_kg: optNum(
      z.number().min(0, "Cannot be negative").max(maxWeightKg, `At most ${maxWeightKg} kg for this farm's species`),
    ),
    total_price: optNum(
      z
        .number()
        .min(0, "Cannot be negative")
        .max(1_000_000_000, "Total price cannot exceed ₹1,000,000,000")
        .refine(isPersistableNonnegativeMoney, MIN_PERSISTED_MONEY_MESSAGE),
    ),
    notes: z.string().max(4_000, "Notes cannot exceed 4000 characters").optional(),
    create_animals: z.boolean(),
  })
  .refine((v) => !v.date || Number(v.date.slice(0, 4)) >= 2000, {
    message: "Date must be year 2000 or later",
    path: ["date"],
  })
  .refine((v) => !v.date || v.date <= farmToday(), {
    message: "Date cannot be in the future",
    path: ["date"],
  });
type BatchInput = z.input<ReturnType<typeof batchSchema>>;
type BatchValues = z.output<ReturnType<typeof batchSchema>>;

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
          <DialogTitle>Batch #{batchId}</DialogTitle>
        </DialogHeader>
        {detailLoading ? (
          query.isError ? (
            <p className="text-sm text-destructive">
              {query.error instanceof ApiError ? query.error.detail : "Could not load the batch."}
            </p>
          ) : (
            <InlineLoading className="justify-center py-6">Loading batch…</InlineLoading>
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
                {detail.batch.count} head
                {detail.batch.total_price !== null
                  ? ` · ${formatMoney(detail.batch.total_price)}`
                  : ""}
              </p>

              <section className="space-y-2">
                <h3 className="font-medium">Animals created ({detail.animals_total})</h3>
                {detail.animals.length === 0 ? (
                  <EmptyState
                    icon={PawPrint}
                    title="No animal stubs for this batch."
                    description="This batch was recorded without creating animals."
                    className="py-8"
                  />
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
                          <TableCell>{enumLabel("sex", a.sex)}</TableCell>
                          <TableCell>{enumLabel("bucket", a.current_bucket)}</TableCell>
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
                  disabled={detailSettling}
                />
              </section>

              <section className="space-y-2">
                <h3 className="font-medium">Open quarantine tasks ({openTasks.length})</h3>
                {openTasks.length === 0 ? (
                  <EmptyState
                    icon={CheckCircle2}
                    title="No open quarantine tasks."
                    description="Every quarantine task for this batch is completed."
                    className="py-8"
                  />
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

function PurchasesPageContent({ perms }: { perms: PermissionsState }) {
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
  const [detailId, setDetailId] = useState<number | null>(() => {
    const raw = getUrl("batch");
    // Stryker disable next-line ConditionalExpression: Number(null) is 0, which fails the integer-≥1 gate exactly like NaN does
    const parsed = raw === null ? Number.NaN : Number(raw);
    return Number.isInteger(parsed) && parsed >= 1 ? parsed : null;
  });
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
    const raw = getUrlRef.current("batch");
    // Stryker disable next-line ConditionalExpression: Number(null) is 0, which fails the integer-≥1 gate exactly like NaN does
    const parsed = raw === null ? Number.NaN : Number(raw);
    setDetailId(Number.isInteger(parsed) && parsed >= 1 ? parsed : null);
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
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<BatchInput, unknown, BatchValues>({
    resolver: zodResolver(batchSchema(vocabulary.facts.maxWeightKg)),
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
            count: values.count,
            sex: values.sex,
            avg_age_months: values.avg_age_months ?? null,
            avg_weight_kg: values.avg_weight_kg ?? null,
            total_price: values.total_price ?? null,
            // Stryker disable next-line OptionalChaining: the notes input is registered unconditionally, so the value is a string, never undefined
  notes: values.notes?.trim() ? values.notes.trim() : null,
            create_animals: values.create_animals,
          },
        });
        if (!farmScope()) return;
        toast.success("Purchase batch created.");
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
        toast.error(mutationError(err));
      }
    });
  }

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : "Could not load purchase batches."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry batches
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title="Purchase batches"
          description={`Incoming groups of ${vocabulary.speciesPlural} — each batch auto-creates its 45-day quarantine protocol.`}
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading purchase batches…</span>
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
        title="Purchase batches"
        description={`Incoming groups of ${vocabulary.speciesPlural} — each batch auto-creates its 45-day quarantine protocol.`}
        actions={
          canManage && (
            <Button disabled={createFlight.pending} onClick={openNewBatch}>
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
            <Button disabled={createFlight.pending} onClick={openNewBatch}>
              <Plus /> Add your first batch
            </Button>
          )}
        </EmptyState>
      ) : (
        <DataTableCard
          title="All batches"
          description={`${payload.total} batch${payload.total === 1 ? "" : "es"} recorded`}
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
                    View
                  </Button>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {b.supplier ?? "No supplier"} · {b.count} head
                </p>
                <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                  {b.total_price !== null ? formatMoney(b.total_price) : "No price recorded"}
                  {b.open_tasks
                    ? ` · ${b.open_tasks} open task${b.open_tasks === 1 ? "" : "s"}`
                    : ""}
                </p>
                {/* Parity with the table's analytics columns — a phone
                 * shouldn't hide what was bought. */}
                <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                  {b.avg_age_months !== null ? `${b.avg_age_months} mo` : "—"} avg age ·{" "}
                  {b.avg_weight_kg !== null ? `${b.avg_weight_kg} kg` : "—"} avg weight ·{" "}
                  {b.animals_created ?? 0} animal{b.animals_created === 1 ? "" : "s"} created
                </p>
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[980px]">
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
                    <Button size="sm" variant="outline" onClick={() => openDetail(b.id)}>
                      View
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          {listSettling && (
            <p role="status" className="pt-3 text-sm text-muted-foreground">
              Updating purchase batches…
            </p>
          )}
          <PaginationControls
            total={payload.total}
            limit={payload.limit}
            offset={payload.offset}
            onOffsetChange={changeOffset}
            label="purchase batches"
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
                <DialogTitle>Review purchase consequences</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-muted-foreground">
                Confirm the batch before these linked farm records are created.
              </p>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-lg border p-4 text-sm">
                <dt className="text-muted-foreground">Batch</dt>
                <dd className="text-right font-medium">
                  {pendingBatch.count} {pendingBatch.sex === PurchaseBatchInSex.F ? "female" : "male"}
                  {pendingBatch.count === 1
                    ? ` ${vocabulary.species}`
                    : ` ${vocabulary.speciesPlural}`}
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
                  disabled={createFlight.pending}
                  onClick={() => setPendingBatch(null)}
                >
                  Back and edit
                </Button>
                <Button
                  type="button"
                  disabled={createFlight.pending}
                  onClick={() => void createBatch(pendingBatch)}
                >
                  {createFlight.pending ? "Creating…" : "Confirm and create"}
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
                <Label htmlFor="supplier">Supplier</Label>
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
                <Label htmlFor="count">Count *</Label>
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
                <Label htmlFor="purchase-sex">Sex *</Label>
                <Controller
                  control={control}
                  name="sex"
                  render={({ field }) => (
                    <Select value={field.value} onValueChange={field.onChange} items={SEX_ITEMS}>
                      <SelectTrigger id="purchase-sex" className="w-full">
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
                  max={String(MAX_AGE_MONTHS)}
                  {...register("avg_age_months")}
                />
                <p className="text-xs text-muted-foreground">
                  Fractional months are spread across the actual day span of the surrounding
                  calendar month, not a fixed average, so the estimated birth date stays accurate
                  even across February.
                </p>
                {errors.avg_age_months && (
                  <p role="alert" className="text-sm text-destructive">
                    {errors.avg_age_months.message}
                  </p>
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
                  <p role="alert" className="text-sm text-destructive">
                    {errors.avg_weight_kg.message}
                  </p>
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
                  <p role="alert" className="text-sm text-destructive">
                    {errors.total_price.message}
                  </p>
                )}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notes">Notes</Label>
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

/** Suspense boundary required because the content reads useSearchParams(). */
export default function PurchasesPage() {
  const perms = usePermissions();
  const vocabulary = farmVocabulary;
  return (
    <Suspense
      fallback={
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading…</span>
          <PageSkeleton cards={1} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="purchases.view"
        label="Purchase batches"
        description={`Incoming groups of ${vocabulary.speciesPlural} — each batch auto-creates its 45-day quarantine protocol.`}
        cards={1}
      >
        <PurchasesPageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
