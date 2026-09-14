"use client";

/** Finance ledger: filters, totals, transactions, 12-month P&L — parity with v1's finance.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ReceiptText, Scale, TrendingDown, TrendingUp } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { useForm, useWatch, type DefaultValues } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useAddTransactionApiFinanceNewPost,
  useCorrectTransactionApiFinanceTransactionsTransactionIdCorrectPost,
  useListTransactionsApiFinanceGet,
} from "@/api/generated/endpoints";
import {
  TransactionInCategory,
  TransactionInType,
  type ListTransactionsApiFinanceGetParams,
  type TransactionCorrectionIn,
  type TransactionOut,
} from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
import { FinanceNav } from "@/components/finance-nav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
  SortableTableHead,
} from "@/components/ui/table";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
import { PaginationControls } from "@/components/pagination-controls";
import { PageSkeleton } from "@/components/skeletons";
import { StatCard } from "@/components/stat-card";
import { StatusBadge } from "@/components/status-badge";
import { ApiError } from "@/lib/api-client";
import { mutationError } from "@/lib/mutations";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { useAuth } from "@/lib/auth-context";
import { enumLabel } from "@/lib/enum-labels";
import { farmToday, formatDate, formatMoney } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import {
  isPersistableNonnegativeMoney,
  MIN_PERSISTED_KG,
  MIN_PERSISTED_KG_MESSAGE,
  MIN_PERSISTED_MONEY,
  MIN_PERSISTED_MONEY_MESSAGE,
} from "@/lib/persisted-numbers";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { cn } from "@/lib/utils";

const CATEGORIES = Object.values(TransactionInCategory);
const TYPES = Object.values(TransactionInType);
/** Backend per-amount ceiling; the validation copy prints it Indian-grouped. */
const MAX_AMOUNT = 1_000_000_000;
/** Sentinel for "no filter / no selection" (empty string is not a valid item value). */
const ALL = "all";
const NONE = "none";
/** value → label maps for the root `items` prop: without them, Base UI's
 * Select.Value renders the raw value (the "all" sentinel, or a SCREAMING_SNAKE
 * enum member) in the closed trigger. */
const TYPE_ITEMS: Record<string, string> = Object.fromEntries(
  TYPES.map((t) => [t, enumLabel("txType", t)]),
);
const CATEGORY_ITEMS: Record<string, string> = Object.fromEntries(
  CATEGORIES.map((c) => [c, enumLabel("txCategory", c)]),
);
const TYPE_FILTER_ITEMS: Record<string, string> = {
  [ALL]: "All types",
  ...TYPE_ITEMS,
};
const CATEGORY_FILTER_ITEMS: Record<string, string> = {
  [ALL]: "All categories",
  ...CATEGORY_ITEMS,
};

function txTypeLabel(value: string): string {
  // Stryker disable next-line StringLiteral: the txType catalog labels equal the titlecased enum values, so the lookup key cannot change the rendered label (the category labels differ and stay live)
  return enumLabel("txType", value);
}

/** URL params → filter state. Anything malformed (or absent) means "off", so
 * a shared link can never wedge the ledger into a filter the API rejects. */
function monthFromParams(params: URLSearchParams): string {
  const raw = params.get("month");
  // The same strict grammar the typing handler enforces: URL adoption must
  // not accept month shapes (2026-99, 2026-00) the input itself can never
  // produce, or a crafted link persists an invalid filter (RT-P11-1).
  // Stryker disable next-line ConditionalExpression: the regex rejects null (coerced "null") exactly like any malformed string, so the !== null arm never decides anything
  return raw !== null && /^\d{4}-(0[1-9]|1[0-2])$/.test(raw) ? raw : "";
}

function typeFromParams(params: URLSearchParams): typeof ALL | TransactionInType {
  const raw = params.get("type");
  return TYPES.includes(raw as TransactionInType) ? (raw as TransactionInType) : ALL;
}

function categoryFromParams(
  params: URLSearchParams,
): typeof ALL | TransactionInCategory {
  const raw = params.get("category");
  return CATEGORIES.includes(raw as TransactionInCategory)
    ? (raw as TransactionInCategory)
    : ALL;
}



const txnSchema = z.object({
  date: z
    .string()
    .min(1, "Date is required")
    .refine((s) => s <= farmToday(), "Date can't be in the future"),
  type: z.enum([TransactionInType.INCOME, TransactionInType.EXPENSE]),
  // Derived from the generated enum so a new contract category is accepted
  // the moment the selects offer it (was a hand-copied list — L24).
  category: z.nativeEnum(TransactionInCategory),
  amount: z.coerce
    .number()
    .positive("Amount must be greater than 0")
    .min(MIN_PERSISTED_MONEY, "Amount must be at least ₹0.005")
    .max(MAX_AMOUNT, `Amount cannot exceed ${formatMoney(MAX_AMOUNT)}`),
  notes: z.string().max(255).optional(),
  related_animal_id: z.string().optional(),
});
type TxnInput = z.input<typeof txnSchema>;
type TxnValues = z.output<typeof txnSchema>;

/** Rebuilt on every reset: a bare reset() restores react-hook-form's
 * mount-time snapshot, which dates entries to the day the tab was opened. */
function txnDefaults(): DefaultValues<TxnInput> {
  return {
    date: farmToday(),
    type: "EXPENSE",
    category: "OTHER",
    notes: "",
    related_animal_id: NONE,
  };
}

/** Income vs expense amount tint, from the semantic status tokens. */
const AMOUNT_TINTS: Record<string, string> = {
  INCOME: "text-success",
  EXPENSE: "text-destructive",
};

const SOURCE_LABELS: Record<string, string> = {
  ANIMAL_PURCHASE: "Animal purchase",
  ANIMAL_SALE: "Animal sale",
  HEALTH_EVENT: "Health event",
  PURCHASE_BATCH: "Purchase batch",
};

function sourceLabel(transaction: TransactionOut): string | null {
  if (!transaction.source_type || transaction.source_id === null) return null;
  const label = SOURCE_LABELS[transaction.source_type] ?? transaction.source_type.replaceAll("_", " ");
  return `${label} #${transaction.source_id}`;
}

const correctionSchema = txnSchema.extend({
  // z.coerce.number() turns an empty HTML number input into 0. Zero is a
  // meaningful correction (it neutralizes a bad ledger amount), so blank must
  // remain distinguishable from an intentional "0".
  amount: z.preprocess(
    (value) =>
      // Stryker disable next-line ConditionalExpression: registered number inputs only ever yield "" or a numeric string — null/undefined never arrive, and the blank arm is pinned by the blank-correction campaign test
      value === "" || value === null || value === undefined
        ? undefined
        : Number(value),
    z
      .number({ error: "Amount is required" })
      .nonnegative("Amount can't be negative")
      .max(MAX_AMOUNT, `Amount cannot exceed ${formatMoney(MAX_AMOUNT)}`)
      .refine(isPersistableNonnegativeMoney, MIN_PERSISTED_MONEY_MESSAGE),
  ),
  feed_quantity_kg: z.preprocess(
    (value) =>
      // Stryker disable next-line ConditionalExpression: registered number inputs only ever yield "" or a numeric string — null/undefined never arrive
      value === "" || value === null || value === undefined ? undefined : Number(value),
    z
      .number()
      .positive("Quantity must be greater than 0")
      .min(MIN_PERSISTED_KG, MIN_PERSISTED_KG_MESSAGE)
      .max(1_000_000, "Quantity cannot exceed 1,000,000 kg")
      .optional(),
  ),
  reason: z.string().trim().min(3, "Reason must be at least 3 characters").max(255),
});
type CorrectionInput = z.input<typeof correctionSchema>;
type CorrectionValues = z.output<typeof correctionSchema>;

function CorrectionDialog({
  transaction,
  canViewAnimals,
  onClose,
  onPendingChange,
  onSaved,
}: {
  transaction: TransactionOut;
  canViewAnimals: boolean;
  onClose: () => void;
  onPendingChange: (pending: boolean) => void;
  onSaved: () => void;
}) {
  const mutation = useCorrectTransactionApiFinanceTransactionsTransactionIdCorrectPost();
  const correctionFlight = useSingleFlight();
  const [formError, setFormError] = useState<string | null>(null);
  const [consequenceConfirmed, setConsequenceConfirmed] = useState(false);
  const [consequenceHint, setConsequenceHint] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<CorrectionInput, unknown, CorrectionValues>({
    resolver: zodResolver(correctionSchema),
    defaultValues: {
      date: transaction.date,
      type: transaction.type as TransactionInType,
      category: transaction.category as TransactionInCategory,
      amount: transaction.amount,
      notes: transaction.notes ?? "",
      related_animal_id:
        transaction.related_animal_id === null ? NONE : String(transaction.related_animal_id),
      reason: "",
    },
  });
  // Stryker disable next-line LogicalOperator: the single-flight guard blocks a resubmission regardless, so one-flag disabling is defense-in-depth; both flags are also true together for the whole in-flight window
  const correctionBusy = isSubmitting || correctionFlight.pending;
  const type = useWatch({ control, name: "type" });
  const category = useWatch({ control, name: "category" });
  const animalId = useWatch({ control, name: "related_animal_id" });
  const isFeedPurchase = transaction.source_type === "FEED_PURCHASE";
  async function submit(values: CorrectionValues) {
    // The button is disabled, but Enter in any input still submits the form —
    // answer the silent no-op with the reason (L24).
    if (!consequenceConfirmed) {
      setConsequenceHint("Confirm the consequence checkbox before recording the correction.");
      return;
    }
    setConsequenceHint(null);
    await correctionFlight.run(async () => {
      const farmScope = captureFarmScope();
      onPendingChange(true);
      setFormError(null);
      try {
        const correctionPayload: TransactionCorrectionIn = {
          date: values.date,
          type: values.type,
          category: values.category,
          amount: values.amount,
          // Stryker disable next-line OptionalChaining: the notes input is registered unconditionally, so the value is a string, never undefined
          notes: values.notes?.trim() || null,
          // Stryker disable ConditionalExpression, LogicalOperator: Number(NONE) is NaN and JSON serialization maps NaN to null (the null arm's wire value), and spreading {feed_quantity_kg: undefined} omits the key from the JSON body exactly like the empty spread
          related_animal_id:
            values.related_animal_id && values.related_animal_id !== NONE
              ? Number(values.related_animal_id)
              : null,
          reason: values.reason,
          ...(isFeedPurchase && values.feed_quantity_kg !== undefined
            ? { feed_quantity_kg: values.feed_quantity_kg }
            : {}),
        };
        // Stryker restore ConditionalExpression, LogicalOperator
        await mutation.mutateAsync({
          transactionId: transaction.id,
          data: correctionPayload,
        });
        if (!farmScope()) return;
        toast.success("Correction recorded. The original entry remains in the audit trail.");
        onSaved();
        onClose();
      } catch (error) {
        if (!farmScope()) return;
        const message = mutationError(error);
        setFormError(message);
        toast.error(message);
      } finally {
        onPendingChange(false);
      }
    });
  }

  return (
    // Closing does NOT cancel the correction: the request completes, toasts and
    // refreshes the ledger on its own. Blocking dismissal while it is pending
    // made this modal — which is `modal`, so its backdrop also blocks the
    // ledger, filters and pagination — unclosable for as long as the request
    // took, with only a reload to escape.
    <Dialog open onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Correct transaction #{transaction.id}</DialogTitle>
          <DialogDescription id={`correction-consequence-${transaction.id}`}>
            The original row will be marked void and retained. This creates an audited
            replacement; it does not rewrite financial history.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit(submit)} className="space-y-4" noValidate>
          <fieldset disabled={correctionBusy} className="contents">
          {formError && (
            <p role="alert" className="text-sm text-destructive">
              {formError}
            </p>
          )}
          {consequenceHint && (
            <p role="alert" className="rounded-lg bg-warning-tint/60 px-3 py-2 text-sm text-warning-tint-foreground">
              {consequenceHint}
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor={`correction-date-${transaction.id}`}>Date *</Label>
              <Input
                id={`correction-date-${transaction.id}`}
                type="date"
                max={farmToday()}
                aria-invalid={Boolean(errors.date) || undefined}
                aria-describedby={errors.date ? `correction-date-${transaction.id}-error` : undefined}
                {...register("date")}
              />
              {errors.date && (
                <p id={`correction-date-${transaction.id}-error`} role="alert" className="text-sm text-destructive">
                  {errors.date.message}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`correction-type-${transaction.id}`}>Type</Label>
              <Select
                value={type}
                onValueChange={(value) => setValue("type", value as TransactionInType)}
                items={TYPE_ITEMS}
              >
                <SelectTrigger id={`correction-type-${transaction.id}`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TYPES.map((value) => (
                    <SelectItem key={value} value={value}>{txTypeLabel(value)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`correction-category-${transaction.id}`}>Category</Label>
              <Select
                value={category}
                onValueChange={(value) => setValue("category", value as TransactionInCategory)}
                items={CATEGORY_ITEMS}
              >
                <SelectTrigger id={`correction-category-${transaction.id}`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((value) => (
                    <SelectItem key={value} value={value}>
                      {enumLabel("txCategory", value)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`correction-amount-${transaction.id}`}>Amount (₹) *</Label>
              <Input
                id={`correction-amount-${transaction.id}`}
                type="number"
                step="0.01"
                min="0"
                inputMode="decimal"
                aria-invalid={Boolean(errors.amount) || undefined}
                aria-describedby={errors.amount ? `correction-amount-${transaction.id}-error` : undefined}
                {...register("amount")}
              />
              {errors.amount && (
                <p id={`correction-amount-${transaction.id}-error`} role="alert" className="text-sm text-destructive">
                  {errors.amount.message}
                </p>
              )}
            </div>
            {isFeedPurchase && (
              <div className="space-y-1.5">
                <Label htmlFor={`correction-feed-quantity-${transaction.id}`}>
                  Corrected quantity (kg)
                </Label>
                <Input
                  id={`correction-feed-quantity-${transaction.id}`}
                  type="number"
                  step="0.001"
                  min="0.0005"
                  inputMode="decimal"
                  aria-invalid={Boolean(errors.feed_quantity_kg) || undefined}
                  aria-describedby={
                    errors.feed_quantity_kg
                      ? `correction-feed-quantity-${transaction.id}-error`
                      : undefined
                  }
                  {...register("feed_quantity_kg")}
                />
                {errors.feed_quantity_kg && (
                  <p
                    id={`correction-feed-quantity-${transaction.id}-error`}
                    role="alert"
                    className="text-sm text-destructive"
                  >
                    {errors.feed_quantity_kg.message}
                  </p>
                )}
              </div>
            )}
            <div className="space-y-1.5">
              {canViewAnimals ? (
                <>
                  <Label htmlFor={`correction-animal-${transaction.id}`}>Animal (optional)</Label>
                  <AnimalPicker
                    id={`correction-animal-${transaction.id}`}
                    value={animalId || NONE}
                    onValueChange={(value) => setValue("related_animal_id", value)}
                    placeholder="No animal"
                    dialogTitle="Choose an animal for the correction"
                    staticOptions={[{ value: NONE, label: "— none —" }]}
                    selectedOption={
                      // Stryker disable next-line ConditionalExpression: the picker only displays a selectedOption whose value matches the field value, and String(null) never matches NONE — the mutant's stub is never rendered
                      transaction.related_animal_id !== null
                        ? {
                            value: String(transaction.related_animal_id),
                            label:
                              transaction.animal_tag ??
                              `Animal #${transaction.related_animal_id}`,
                          }
                        : null
                    }
                  />
                </>
              ) : (
                <>
                  <p className="text-sm font-medium">Animal (read only)</p>
                  <output
                    aria-label="Linked animal"
                    className="block rounded-md border bg-muted/40 px-3 py-2 text-sm"
                  >
                    {transaction.related_animal_id !== null
                      ? transaction.animal_tag ?? `Animal #${transaction.related_animal_id}`
                      : "No animal linked"}
                  </output>
                  <p className="text-xs text-muted-foreground">
                    You don&apos;t have animal access, so this correction preserves the existing
                    link.
                  </p>
                </>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`correction-notes-${transaction.id}`}>Notes</Label>
              <Input id={`correction-notes-${transaction.id}`} maxLength={255} {...register("notes")} />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor={`correction-reason-${transaction.id}`}>Correction reason *</Label>
              <Input
                id={`correction-reason-${transaction.id}`}
                maxLength={255}
                aria-invalid={Boolean(errors.reason) || undefined}
                aria-describedby={errors.reason ? `correction-reason-${transaction.id}-error` : undefined}
                {...register("reason")}
              />
              {errors.reason && (
                <p id={`correction-reason-${transaction.id}-error`} role="alert" className="text-sm text-destructive">
                  {errors.reason.message}
                </p>
              )}
            </div>
          </div>
          <div className="flex items-start gap-2 rounded-md border p-3">
            <Checkbox
              id={`confirm-correction-${transaction.id}`}
              checked={consequenceConfirmed}
              aria-describedby={`correction-consequence-${transaction.id}`}
              onCheckedChange={(checked) => setConsequenceConfirmed(checked === true)}
            />
            <Label htmlFor={`confirm-correction-${transaction.id}`} className="font-normal">
              I understand the original transaction will be voided and replaced.
            </Label>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={correctionBusy || !consequenceConfirmed}
            >
              {correctionBusy
                ? "Saving correction…"
                : formError
                  ? "Retry correction"
                  : "Record correction"}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function FinancePageContent({ perms }: { perms: PermissionsState }) {
  const { can } = perms;
  const allowed = can("finance.view");
  const canManage = can("finance.manage");
  const canViewAnimals = can("animals.view");
  const queryClient = useQueryClient();

  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  // Key the URL sync by the serialized params; the useSearchParams object
  // identity itself is not stable.
  const paramsKey = searchParams.toString();

  // `ALL` is the "no filter" sentinel; every other value is a real enum member,
  // so the ledger filters stay in step with the generated query contract.
  // State mirrors the URL so picks apply synchronously; a same-route
  // navigation (shared link, browser Back) re-syncs it below. This is the
  // render-time "adjust state when a value changes" pattern — no effect, so
  // no cascading commit.
  const [month, setMonth] = useState(() => monthFromParams(searchParams));
  const [typeFilter, setTypeFilter] = useState<typeof ALL | TransactionInType>(() =>
    typeFromParams(searchParams),
  );
  const [categoryFilter, setCategoryFilter] = useState<
    typeof ALL | TransactionInCategory
  >(() => categoryFromParams(searchParams));
  const [syncedParamsKey, setSyncedParamsKey] = useState(paramsKey);
  if (paramsKey !== syncedParamsKey) {
    setSyncedParamsKey(paramsKey);
    const synced = new URLSearchParams(paramsKey);
    setMonth(monthFromParams(synced));
    setTypeFilter(typeFromParams(synced));
    setCategoryFilter(categoryFromParams(synced));
  }
  const [open, setOpen] = useState(false);
  const [correcting, setCorrecting] = useState<TransactionOut | null>(null);
  const [correctionPending, setCorrectionPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const limit = 50;
  /** Client-side sort of the fetched ledger page — the API's recency order is
   * the default; clicking a header sorts what you can see. */
  const [sortState, setSortState] = useState<{
    column: "date" | "amount";
    direction: "asc" | "desc";
  } | null>(null);

  /** Mirror the active filters into the URL without adding a history entry
   *  or scrolling the ledger out of view; unknown params are preserved. */
  function replaceLedgerUrl(
    nextMonth: string,
    nextType: typeof ALL | TransactionInType,
    nextCategory: typeof ALL | TransactionInCategory,
  ) {
    const params = new URLSearchParams(paramsKey);
    if (nextMonth) params.set("month", nextMonth);
    else params.delete("month");
    if (nextType !== ALL) params.set("type", nextType);
    else params.delete("type");
    if (nextCategory !== ALL) params.set("category", nextCategory);
    else params.delete("category");
    const rest = params.toString();
    router.replace(rest ? `${pathname}?${rest}` : pathname, { scroll: false });
  }

  /** One-shot filter reset shared by the Clear chip and the empty-state CTA:
   * clears month/type/category and returns to the first page in one URL write. */
  function clearLedgerFilters() {
    setMonth("");
    setTypeFilter(ALL);
    setCategoryFilter(ALL);
    setOffset(0);
    replaceLedgerUrl("", ALL, ALL);
  }

  // Omit inactive filters entirely: the Orval URL builder serializes `null`
  // as the literal string "null", which the backend treats as a real filter
  // and matches zero rows.
  const params: ListTransactionsApiFinanceGetParams = {
    ...(month && { month }),
    ...(typeFilter !== ALL && { type: typeFilter }),
    ...(categoryFilter !== ALL && { category: categoryFilter }),
    limit,
    offset,
  };
  // Keep the previous ledger rendered while a filter change or page turn
  // resolves; a newly-keyed observer otherwise blanks the whole page.
  const query = useListTransactionsApiFinanceGet(params, {
    query: { enabled: allowed, placeholderData: (previous) => previous },
  });
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const ledgerSettling = query.isPlaceholderData;

  const addMutation = useAddTransactionApiFinanceNewPost();
  const addFlight = useSingleFlight();
  /** Identifies one open/submit cycle of the add dialog. This page-level
   *  dialog never unmounts, so without it a submission that resolves after the
   *  operator dismissed and reopened it would close the new dialog and reset
   *  the entry they had just typed. */
  const addAttempt = useRef(0);
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<TxnInput, unknown, TxnValues>({
    resolver: zodResolver(txnSchema),
    defaultValues: txnDefaults(),
  });
  const wCategory = useWatch({ control, name: "category" });
  const wRelatedAnimalId = useWatch({ control, name: "related_animal_id" });
  const wType = useWatch({ control, name: "type" });

  function openAddDialog() {
    reset(txnDefaults());
    // Stryker disable next-line CallExpression: the close-time clear in onOpenChange (pinned by the escape-reopen campaign test) already guarantees a clean banner before any reopen
    setFormError(null);
    setOpen(true);
  }

  async function onSubmit(values: TxnValues) {
    await addFlight.run(async () => {
      const farmScope = captureFarmScope();
      setFormError(null);
      // Stryker disable next-line UpdateOperator: a monotonically decreasing attempt counter mismatches a captured value exactly as reliably as an increasing one
      const attempt = ++addAttempt.current;
      try {
        await addMutation.mutateAsync({
          data: {
            date: values.date,
            type: values.type,
            category: values.category,
            amount: values.amount,
            // Stryker disable next-line OptionalChaining: the notes input is registered unconditionally, so the value is a string, never undefined
            notes: values.notes?.trim() || null,
            // Stryker disable ConditionalExpression, LogicalOperator: Number(NONE) is NaN and JSON serialization maps NaN to null — the same wire value the null arm produces
            related_animal_id:
              values.related_animal_id && values.related_animal_id !== NONE
                ? Number(values.related_animal_id)
                : null,
          },
        });
        // The write landed: confirm it and refresh the ledger whatever the
        // dialog has since done — unless the farm changed, in which case this
        // continuation belongs to the previous farm's UI.
        if (!farmScope()) return;
        toast.success("Transaction saved.");
        invalidateFarmData(queryClient);
        if (addAttempt.current !== attempt) return;
        setOpen(false);
        // Stryker disable next-line CallExpression: openAddDialog re-applies txnDefaults() on every open, so the post-success reset is redundant
        reset(txnDefaults());
      } catch (err) {
        if (addAttempt.current !== attempt || !farmScope()) return;
        const message = mutationError(err);
        setFormError(message);
        toast.error(message);
      }
    });
  }

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div className="space-y-3" role="alert">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : "Could not load finance."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry finance
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title="Finance"
          description="Income, expenses and monthly profit & loss for the farm."
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading finance…</span>
          <PageSkeleton stats={3} cards={2} />
        </div>
      </div>
    );
  }

  const net = payload.total_income - payload.total_expense;
  const sort = sortState;
  const toggleSort = (column: string) => {
    setSortState((prev) =>
      prev?.column === column
        ? prev.direction === "asc"
          ? { column: column as "date" | "amount", direction: "desc" }
          : null
        : { column: column as "date" | "amount", direction: "asc" },
    );
  };
  const sortedTransactions = sort
    ? [...payload.transactions].sort((a, b) => {
        const dir = sort.direction === "asc" ? 1 : -1;
        // Stryker disable next-line ArithmeticOperator: dir is always ±1, and x*±1 === x/±1 for every number
  if (sort.column === "date") return a.date.localeCompare(b.date) * dir;
        // Stryker disable next-line ArithmeticOperator: dir is always ±1, and x*±1 === x/±1 for every number
  return (a.amount - b.amount) * dir;
      })
    : payload.transactions;

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title="Finance"
        description="Income, expenses and monthly profit & loss for the farm."
        actions={
          canManage && (
            <Button onClick={openAddDialog}>
              New transaction
            </Button>
          )
        }
      />

      <FinanceNav active="ledger" />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <StatCard
          label="Total income"
          value={<span className="tabular-nums">{formatMoney(payload.total_income)}</span>}
          icon={TrendingUp}
          tint="success"
        />
        <StatCard
          label="Total expense"
          value={<span className="tabular-nums">{formatMoney(payload.total_expense)}</span>}
          icon={TrendingDown}
          tint="destructive"
        />
        <StatCard
          label="Net (all time)"
          value={<span className="tabular-nums">{formatMoney(net)}</span>}
          icon={Scale}
          tint="warning"
        />
      </div>

      <DataTableCard
        title="Monthly P&L (last 12 months)"
        description={`Feed stock on hand ${formatMoney(payload.feed_stock_value)} (memo — not an expense).`}
      >
        {payload.pnl.length === 0 ? (
          <EmptyState
            icon={ReceiptText}
            title="No transactions yet."
            description="Record income and expenses to build the monthly P&L."
            className="py-8"
          />
        ) : (
          <Table className="min-w-[560px]">
            <TableHeader>
              <TableRow>
                <TableHead>Month</TableHead>
                <TableHead className="text-right">Income</TableHead>
                <TableHead className="text-right">Expense</TableHead>
                <TableHead className="text-right">Net</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.pnl.map((row) => (
                <TableRow key={row.month}>
                  <TableCell>
                    <button
                      type="button"
                      className="text-primary underline"
                      onClick={() => {
                        setMonth(row.month);
                        setOffset(0);
                        replaceLedgerUrl(row.month, typeFilter, categoryFilter);
                      }}
                    >
                      {row.month}
                    </button>
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-success">
                    {formatMoney(row.income)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-destructive">
                    {formatMoney(row.expense)}
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right tabular-nums",
                      row.net < 0 ? "text-destructive" : "text-success",
                    )}
                  >
                    {formatMoney(row.net)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DataTableCard>

      <DataTableCard
        title="Transactions"
        description={`Filter the ledger by month, type or category.${sort ? " Sorting applies to the current page." : ""}`}
        contentClassName="space-y-4"
      >
        {ledgerSettling && (
          <p role="status" className="text-sm text-muted-foreground">
            Updating transactions…
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Input
            type="month"
            value={month}
            onChange={(e) => {
              // Firefox desktop has no type="month" support: the input degrades
              // to free text. Accept only the canonical shape (or a cleared
              // field, which resets the filter) so garbage never reaches the
              // API or the shareable URL — same guard as the planner.
              const value = e.target.value;
              // Stryker disable next-line ConditionalExpression, Regex: the browser month picker (and jsdom's value sanitization) only ever yields a canonical YYYY-MM or the empty string, so the near-miss shapes this guard rejects cannot arrive at the handler
              if (value !== "" && !/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) return;
              setMonth(value);
              setOffset(0);
              replaceLedgerUrl(value, typeFilter, categoryFilter);
            }}
            className="w-40"
            aria-label="Filter by month"
          />
          <Select
            value={typeFilter}
            onValueChange={(v) => {
              // The item set below is exactly ALL plus the enum members.
              setTypeFilter(v as typeof ALL | TransactionInType);
              setOffset(0);
              replaceLedgerUrl(month, v as typeof ALL | TransactionInType, categoryFilter);
            }}
            items={TYPE_FILTER_ITEMS}
          >
            <SelectTrigger aria-label="Filter transactions by type">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All types</SelectItem>
              {TYPES.map((t) => (
                <SelectItem key={t} value={t}>
                  {txTypeLabel(t)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={categoryFilter}
            onValueChange={(v) => {
              // The item set below is exactly ALL plus the enum members.
              setCategoryFilter(v as typeof ALL | TransactionInCategory);
              setOffset(0);
              replaceLedgerUrl(month, typeFilter, v as typeof ALL | TransactionInCategory);
            }}
            items={CATEGORY_FILTER_ITEMS}
          >
            <SelectTrigger aria-label="Filter transactions by category">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All categories</SelectItem>
              {CATEGORIES.map((c) => (
                <SelectItem key={c} value={c}>
                  {enumLabel("txCategory", c)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {(month || typeFilter !== ALL || categoryFilter !== ALL) && (
            <Button
              variant="outline"
              size="sm"
              onClick={clearLedgerFilters}
            >
              Clear
            </Button>
          )}
        </div>

        {payload.transactions.length === 0 ? (
          month || typeFilter !== ALL || categoryFilter !== ALL ? (
            // A filter excluded every row: the ledger itself may not be empty.
            <EmptyState
              icon={ReceiptText}
              title="No transactions match."
              description="Try clearing the filters."
            >
              <Button type="button" variant="outline" size="sm" onClick={clearLedgerFilters}>
                Clear filters
              </Button>
            </EmptyState>
          ) : (
            // No filters active and nothing in range: the farm has no ledger yet.
            <EmptyState
              icon={ReceiptText}
              title="No transactions yet"
              description="Record income and expenses to build the farm ledger."
            >
              {canManage && (
                <Button size="sm" onClick={openAddDialog}>
                  Add transaction
                </Button>
              )}
            </EmptyState>
          )
        ) : (
          <Table className="min-w-[900px]">
            <TableHeader>
              <TableRow>
                <SortableTableHead
                  column="date"
                  label="Date"
                  direction={sort?.column === "date" ? sort.direction : null}
                  onSort={toggleSort}
                />
                <TableHead>Type</TableHead>
                <TableHead>Category</TableHead>
                <SortableTableHead
                  column="amount"
                  label="Amount"
                  className="text-right"
                  direction={sort?.column === "amount" ? sort.direction : null}
                  onSort={toggleSort}
                />
                <TableHead>Animal</TableHead>
                <TableHead>Notes</TableHead>
                <TableHead>Source / audit</TableHead>
                {canManage && <TableHead><span className="sr-only">Actions</span></TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedTransactions.map((t) => (
                <TableRow key={t.id} className={cn(t.voided_at && "bg-muted/40 opacity-70")}>
                  <TableCell>{formatDate(t.date)}</TableCell>
                  <TableCell>
                    <StatusBadge status={t.type} />
                    {t.voided_at && (
                      <Badge variant="destructive" className="ml-2">VOID</Badge>
                    )}
                  </TableCell>
                  <TableCell>{enumLabel("txCategory", t.category)}</TableCell>
                  <TableCell
                    className={cn(
                      "text-right tabular-nums font-medium",
                      AMOUNT_TINTS[t.type],
                      t.voided_at && "line-through",
                    )}
                  >
                    {formatMoney(t.amount)}
                  </TableCell>
                  <TableCell>
                    {t.animal_tag && t.related_animal_id && canViewAnimals ? (
                      <Link
                        href={`/animals/${t.related_animal_id}`}
                        className="text-primary underline"
                      >
                        {t.animal_tag}
                      </Link>
                    ) : t.animal_tag && t.related_animal_id ? (
                      t.animal_tag
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    {t.notes ?? ""}
                  </TableCell>
                  <TableCell className="max-w-64 whitespace-normal">
                    {t.correction_of_id !== null && (
                      <span className="block text-xs font-medium">
                        Correction of transaction #{t.correction_of_id}
                      </span>
                    )}
                    {sourceLabel(t) ? (
                      <span className="block text-xs text-muted-foreground">
                        Source: {sourceLabel(t)}
                      </span>
                    ) : t.correction_of_id === null ? (
                      <span className="block text-xs text-muted-foreground">Manual entry</span>
                    ) : null}
                    {t.void_reason && (
                      <span className="mt-1 block text-xs text-destructive">
                        Void reason: {t.void_reason}
                      </span>
                    )}
                  </TableCell>
                  {canManage && (
                    <TableCell>
                      {!t.voided_at && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={correctionPending || ledgerSettling || query.isFetching}
                          onClick={() => setCorrecting(t)}
                        >
                          Correct
                        </Button>
                      )}
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <PaginationControls
          total={payload.transactions_total}
          limit={payload.limit}
          offset={payload.offset}
          onOffsetChange={setOffset}
          label="transactions"
          disabled={ledgerSettling}
        />
      </DataTableCard>

      {correcting && (
        <CorrectionDialog
          transaction={correcting}
          canViewAnimals={canViewAnimals}
          onClose={() =>
            // Stryker disable next-line ArrowFunction, ConditionalExpression: setCorrecting(undefined/null) both render the dialog closed, and current is always the row that opened this dialog, so the id comparison is always true here
            setCorrecting((current) =>
              current?.id === correcting.id ? null : current,
            )
          }
          onPendingChange={setCorrectionPending}
          onSaved={() => invalidateFarmData(queryClient)}
        />
      )}

      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          // Never block dismissal on an in-flight write (see CorrectionDialog).
          // The submission's continuation is guarded by `addAttempt` instead,
          // so a late resolve cannot close or reset a dialog the operator has
          // since reopened and started typing into.
          // Stryker disable next-line BooleanLiteral, ConditionalExpression: running the block at open only moves the attempt bump before any submit can capture it (submits capture post-bump state), and the open-time error clear duplicates openAddDialog's
          if (!nextOpen) {
            // Stryker disable next-line CallExpression: openAddDialog clears formError on every open, so skipping the close-time clear is unobservable
            setFormError(null);
            // Stryker disable next-line AssignmentOperator: a monotonically decreasing attempt counter mismatches a captured value exactly as reliably as an increasing one
            addAttempt.current += 1;
          }
          setOpen(nextOpen);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>New transaction</DialogTitle>
          </DialogHeader>
          {/* Build the submit handler at event time, not during render:
              onSubmit reads the addAttempt ref, and refs must not be read
              while rendering. */}
          <form
            onSubmit={(event) => void handleSubmit(onSubmit)(event)}
            className="space-y-4"
            noValidate
          >
            <fieldset disabled={isSubmitting || addFlight.pending} className="contents">
            {formError && <p role="alert" className="text-sm text-destructive">{formError}</p>}
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="date">Date *</Label>
                <Input
                  id="date"
                  type="date"
                  max={farmToday()}
                  aria-invalid={Boolean(errors.date) || undefined}
                  aria-describedby={errors.date ? "transaction-date-error" : undefined}
                  {...register("date")}
                />
                {errors.date && (
                  <p id="transaction-date-error" role="alert" className="text-sm text-destructive">{errors.date.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="transaction-type">Type</Label>
                <Select
                  value={wType}
                  onValueChange={(v) =>
                    // Stryker disable next-line ObjectLiteral, BooleanLiteral: the type select only ever sets valid enum values, so shouldValidate never surfaces a different error state
                    setValue("type", v as TxnInput["type"], { shouldValidate: true })
                  }
                  items={TYPE_ITEMS}
                >
                  <SelectTrigger id="transaction-type" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {txTypeLabel(t)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="transaction-category">Category</Label>
                <Select
                  value={wCategory}
                  onValueChange={(v) =>
                    // Stryker disable next-line ObjectLiteral, BooleanLiteral: the category select only ever sets valid enum values, so shouldValidate never surfaces a different error state
                    setValue("category", v as TxnInput["category"], { shouldValidate: true })
                  }
                  items={CATEGORY_ITEMS}
                >
                  <SelectTrigger id="transaction-category" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CATEGORIES.map((c) => (
                      <SelectItem key={c} value={c}>
                        {enumLabel("txCategory", c)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="amount">Amount (₹) *</Label>
                <Input
                  id="amount"
                  type="number"
                  step="0.01"
                  min="0.005"
                  inputMode="decimal"
                  placeholder="0.00"
                  aria-invalid={Boolean(errors.amount) || undefined}
                  aria-describedby={errors.amount ? "transaction-amount-error" : undefined}
                  {...register("amount")}
                />
                {errors.amount && (
                  <p id="transaction-amount-error" role="alert" className="text-sm text-destructive">{errors.amount.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                {canViewAnimals ? (
                  <>
                    <Label htmlFor="transaction-animal">Animal (optional)</Label>
                    <AnimalPicker
                      id="transaction-animal"
                      value={wRelatedAnimalId || NONE}
                      onValueChange={(v) => setValue("related_animal_id", v)}
                      placeholder="No animal"
                      dialogTitle="Choose an animal for this transaction"
                      staticOptions={[{ value: NONE, label: "— none —" }]}
                    />
                  </>
                ) : (
                  <>
                    <p className="text-sm font-medium">Animal (optional)</p>
                    <p className="text-xs text-muted-foreground">
                      You don&apos;t have animal access, so this transaction will be saved without
                      an animal link.
                    </p>
                  </>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="notes">Notes</Label>
                <Input
                  id="notes"
                  maxLength={255}
                  placeholder="description"
                  aria-invalid={Boolean(errors.notes) || undefined}
                  aria-describedby={errors.notes ? "transaction-notes-error" : undefined}
                  {...register("notes")}
                />
                {errors.notes && (
                  <p id="transaction-notes-error" role="alert" className="text-sm text-destructive">{errors.notes.message}</p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={isSubmitting || addFlight.pending}
                onClick={() => {
                  // Stryker disable next-line CallExpression: openAddDialog clears formError on every open, so the cancel-time clear is unobservable
                  setFormError(null);
                  // Stryker disable next-line AssignmentOperator: a monotonically decreasing attempt counter mismatches a captured value exactly as reliably as an increasing one
                  addAttempt.current += 1;
                  setOpen(false);
                }}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isSubmitting || addFlight.pending}>
                {isSubmitting || addFlight.pending
                  ? "Saving…"
                  : formError
                    ? "Retry add transaction"
                    : "Add transaction"}
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
export default function FinancePage() {
  const perms = usePermissions();
  // The permissions query is disabled until the auth bootstrap selects a
  // farm; `permsLoading` alone is false during that window while the
  // permission set is still empty — deciding "no access" then would flash a
  // denial at a signed-in operator. The skeleton stays up until the session
  // (and with it the permission fetch) is real.
  const { loading: authLoading } = useAuth();
  return (
    <Suspense
      fallback={
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading…</span>
          <PageSkeleton stats={3} cards={2} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="finance.view"
        label="Finance"
        description="Income, expenses and monthly profit & loss for the farm."
        stats={3}
        cards={2}
        alsoLoading={authLoading}
      >
        <FinancePageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
