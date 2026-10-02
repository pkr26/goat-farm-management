"use client";

/** Finance ledger: filters, totals, transactions, 12-month P&L — parity with v1's finance.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ReceiptText, Scale, TrendingDown, TrendingUp } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useRef, useState } from "react";
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
  type MortalityMemoOut,
  type TransactionCorrectionIn,
  type TransactionOut,
} from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
import { FINANCE_TABS, SectionNav } from "@/components/section-nav";
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
import { useMutationError } from "@/lib/mutations";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { useAuth } from "@/lib/auth-context";
import { enumLabel } from "@/lib/enum-labels";
import { useLanguage, useT, type Language, type MessageKey, type TFn } from "@/lib/i18n";
import { mapServerError } from "@/lib/server-error-phrases";
import { farmToday, formatDate, formatMoney, formatMoneyDecimal } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import {
  isPersistableNonnegativeMoney,
  MIN_PERSISTED_KG,
  MIN_PERSISTED_MONEY,
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
const typeItems = (language: Language): Record<string, string> =>
  Object.fromEntries(TYPES.map((t) => [t, enumLabel("txType", t, language)]));
const categoryItems = (language: Language): Record<string, string> =>
  Object.fromEntries(CATEGORIES.map((c) => [c, enumLabel("txCategory", c, language)]));
const typeFilterItems = (language: Language, t: TFn): Record<string, string> => ({
  [ALL]: t("finance.allTypes"),
  ...typeItems(language),
});
const categoryFilterItems = (language: Language, t: TFn): Record<string, string> => ({
  [ALL]: t("finance.allCategories"),
  ...categoryItems(language),
});

function txTypeLabel(value: string, language: Language): string {
  return enumLabel("txType", value, language);
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



/** Validation copy resolves through the i18n catalog, so the factory takes
 * the caller's `t` and the form rebuilds it for the active language. */
function buildTxnSchema(t: TFn) {
  return z.object({
    date: z
      .string()
      .min(1, t("finance.validation.dateRequired"))
      .refine((s) => s <= farmToday(), t("finance.validation.dateFuture")),
    type: z.enum([TransactionInType.INCOME, TransactionInType.EXPENSE]),
    // Derived from the generated enum so a new contract category is accepted
    // the moment the selects offer it (was a hand-copied list — L24).
    category: z.nativeEnum(TransactionInCategory),
    amount: z.coerce
      .number()
      .positive(t("finance.validation.amountPositive"))
      .min(MIN_PERSISTED_MONEY, t("finance.validation.amountMin"))
      .max(MAX_AMOUNT, t("finance.validation.amountMax", { max: formatMoney(MAX_AMOUNT) })),
    notes: z.string().max(255).optional(),
    related_animal_id: z.string().optional(),
  });
}
type TxnInput = z.input<ReturnType<typeof buildTxnSchema>>;
type TxnValues = z.output<ReturnType<typeof buildTxnSchema>>;

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

const SOURCE_LABEL_KEYS: Record<string, MessageKey> = {
  ANIMAL_PURCHASE: "finance.source.animalPurchase",
  ANIMAL_SALE: "finance.source.animalSale",
  HEALTH_EVENT: "finance.source.healthEvent",
  PURCHASE_BATCH: "finance.source.purchaseBatch",
};

/** The P&L card's memo line: ledger-neutral facts that must never read as
 * transactions — feed stock valued at last purchase price, and deaths in the
 * window valued at the farm's own realized ₹/kg (kept unvalued, not zero,
 * when no weighed sale can price them). */
function memoDescription(
  feedStock: number,
  // null = withheld: the memo carries clinical death figures, so the backend
  // gates it on health.view (2026-09-17) — same sentinel as dashboard/reports.
  mortality: MortalityMemoOut | null | undefined,
  t: TFn,
): string {
  const parts = [
    t("finance.memo.feedStock", { amount: formatMoney(feedStock) }),
  ];
  if (mortality && mortality.head_count > 0) {
    const loss =
      mortality.estimated_loss === null
        ? t("finance.memo.lossUnvalued")
        : formatMoneyDecimal(mortality.estimated_loss);
    parts.push(
      t(mortality.head_count === 1 ? "finance.memo.deathsOne" : "finance.memo.deathsMany", {
        count: mortality.head_count,
        months: mortality.window_months,
        loss,
      }),
    );
  }
  return parts.join(" ");
}

function sourceLabel(transaction: TransactionOut, t: TFn): string | null {
  if (!transaction.source_type || transaction.source_id === null) return null;
  const key = SOURCE_LABEL_KEYS[transaction.source_type];
  const label = key ? t(key) : transaction.source_type.replaceAll("_", " ");
  return `${label} #${transaction.source_id}`;
}

function buildCorrectionSchema(t: TFn) {
  return buildTxnSchema(t).extend({
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
        .number({ error: t("finance.validation.amountRequired") })
        .nonnegative(t("finance.validation.amountNegative"))
        .max(MAX_AMOUNT, t("finance.validation.amountMax", { max: formatMoney(MAX_AMOUNT) }))
        .refine(isPersistableNonnegativeMoney, t("finance.validation.moneyMin")),
    ),
    feed_quantity_kg: z.preprocess(
      (value) =>
        // Stryker disable next-line ConditionalExpression: registered number inputs only ever yield "" or a numeric string — null/undefined never arrive
        value === "" || value === null || value === undefined ? undefined : Number(value),
      z
        .number()
        .positive(t("finance.validation.quantityPositive"))
        .min(MIN_PERSISTED_KG, t("finance.validation.kgMin"))
        .max(1_000_000, t("finance.validation.quantityMax"))
        .optional(),
    ),
    reason: z.string().trim().min(3, t("finance.validation.reasonMin")).max(255),
  });
}
type CorrectionInput = z.input<ReturnType<typeof buildCorrectionSchema>>;
type CorrectionValues = z.output<ReturnType<typeof buildCorrectionSchema>>;

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
  const mutationErrorMessage = useMutationError();
  const mutation = useCorrectTransactionApiFinanceTransactionsTransactionIdCorrectPost();
  const correctionFlight = useSingleFlight();
  const { language } = useLanguage();
  const t = useT();
  const localizedCorrectionSchema = useMemo(() => buildCorrectionSchema(t), [t]);
  const typeItemsMap = typeItems(language);
  const categoryItemsMap = categoryItems(language);
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
    resolver: zodResolver(localizedCorrectionSchema),
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
      setConsequenceHint(t("finance.correction.confirmHint"));
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
        toast.success(t("finance.correction.toast"));
        onSaved();
        onClose();
      } catch (error) {
        if (!farmScope()) return;
        const message = mutationErrorMessage(error);
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
          <DialogTitle>{t("finance.correction.title", { number: transaction.id })}</DialogTitle>
          <DialogDescription id={`correction-consequence-${transaction.id}`}>
            {t("finance.correction.description")}
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
              <Label htmlFor={`correction-date-${transaction.id}`}>{t("finance.date")} *</Label>
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
              <Label htmlFor={`correction-type-${transaction.id}`}>{t("finance.type")}</Label>
              <Select
                value={type}
                onValueChange={(value) => setValue("type", value as TransactionInType)}
                items={typeItemsMap}
              >
                <SelectTrigger id={`correction-type-${transaction.id}`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TYPES.map((value) => (
                    <SelectItem key={value} value={value}>{txTypeLabel(value, language)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`correction-category-${transaction.id}`}>{t("finance.category")}</Label>
              <Select
                value={category}
                onValueChange={(value) => setValue("category", value as TransactionInCategory)}
                items={categoryItemsMap}
              >
                <SelectTrigger id={`correction-category-${transaction.id}`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((value) => (
                    <SelectItem key={value} value={value}>
                      {enumLabel("txCategory", value, language)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`correction-amount-${transaction.id}`}>
                {t("finance.form.amountLabel")}
              </Label>
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
                  {t("finance.correction.feedQuantity")}
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
                  <Label htmlFor={`correction-animal-${transaction.id}`}>
                    {t("finance.form.animalOptional")}
                  </Label>
                  <AnimalPicker
                    id={`correction-animal-${transaction.id}`}
                    value={animalId || NONE}
                    onValueChange={(value) => setValue("related_animal_id", value)}
                    placeholder={t("finance.form.noAnimal")}
                    dialogTitle={t("finance.correction.chooseAnimal")}
                    staticOptions={[{ value: NONE, label: t("common.none") }]}
                    selectedOption={
                      // Stryker disable next-line ConditionalExpression: the picker only displays a selectedOption whose value matches the field value, and String(null) never matches NONE — the mutant's stub is never rendered
                      transaction.related_animal_id !== null
                        ? {
                            value: String(transaction.related_animal_id),
                            label:
                              transaction.animal_tag ??
                              t("finance.animalNumber", {
                                number: transaction.related_animal_id,
                              }),
                          }
                        : null
                    }
                  />
                </>
              ) : (
                <>
                  <p className="text-sm font-medium">{t("finance.correction.animalReadOnly")}</p>
                  <output
                    aria-label={t("finance.correction.linkedAnimal")}
                    className="block rounded-md border bg-muted/40 px-3 py-2 text-sm"
                  >
                    {transaction.related_animal_id !== null
                      ? (transaction.animal_tag ??
                        t("finance.animalNumber", { number: transaction.related_animal_id }))
                      : t("finance.correction.noAnimalLinked")}
                  </output>
                  <p className="text-xs text-muted-foreground">
                    {t("finance.correction.noAnimalAccess")}
                  </p>
                </>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`correction-notes-${transaction.id}`}>{t("finance.notes")}</Label>
              <Input id={`correction-notes-${transaction.id}`} maxLength={255} {...register("notes")} />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor={`correction-reason-${transaction.id}`}>
                {t("finance.correction.reasonLabel")}
              </Label>
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
              {t("finance.correction.confirmLabel")}
            </Label>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="submit"
              disabled={correctionBusy || !consequenceConfirmed}
            >
              {correctionBusy
                ? t("finance.correction.saving")
                : formError
                  ? t("finance.correction.retry")
                  : t("finance.correction.submit")}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function FinancePageContent({ perms }: { perms: PermissionsState }) {
  const mutationErrorMessage = useMutationError();
  const { can } = perms;
  const allowed = can("finance.view");
  const canManage = can("finance.manage");
  const canViewAnimals = can("animals.view");
  const { language } = useLanguage();
  const t = useT();
  const typeItemsMap = typeItems(language);
  const categoryItemsMap = categoryItems(language);
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
  const localizedTxnSchema = useMemo(() => buildTxnSchema(t), [t]);
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
    resolver: zodResolver(localizedTxnSchema),
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
        toast.success(t("finance.toast.saved"));
        invalidateFarmData(queryClient);
        if (addAttempt.current !== attempt) return;
        setOpen(false);
        // Stryker disable next-line CallExpression: openAddDialog re-applies txnDefaults() on every open, so the post-success reset is redundant
        reset(txnDefaults());
      } catch (err) {
        if (addAttempt.current !== attempt || !farmScope()) return;
        const message = mutationErrorMessage(err);
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
            {query.error instanceof ApiError
              ? mapServerError(t, query.error.detail, query.error.status, query.error.code)
              : t("finance.loadFailed")}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            {t("finance.retry")}
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("finance.title")}
          description={t("finance.description")}
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("finance.loading")}</span>
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
        title={t("finance.title")}
        description={t("finance.description")}
        actions={
          canManage && (
            <Button onClick={openAddDialog}>
              {t("finance.newTransaction")}
            </Button>
          )
        }
      />

      <SectionNav tabs={FINANCE_TABS} active="ledger" ariaLabelKey="finance.nav.aria" />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {/* The cards are ALL-TIME totals while the table below obeys the
         * URL filters — say so on the cards instead of letting a filtered
         * ledger read as if it summed to them (P3, 2026-09-20 audit). */}
        <StatCard
          label={t("finance.totalIncome")}
          value={<span className="tabular-nums">{formatMoney(payload.total_income)}</span>}
          icon={TrendingUp}
          tint="success"
        />
        <StatCard
          label={t("finance.totalExpense")}
          value={<span className="tabular-nums">{formatMoney(payload.total_expense)}</span>}
          icon={TrendingDown}
          tint="destructive"
        />
        <StatCard
          label={t("finance.netAllTime")}
          value={<span className="tabular-nums">{formatMoney(net)}</span>}
          icon={Scale}
          tint="warning"
        />
      </div>

      <DataTableCard
        title={t("finance.pnlTitle")}
        description={memoDescription(
          payload.feed_stock_value,
          payload.mortality_loss,
          t,
        )}
      >
        {payload.pnl.length === 0 ? (
          <EmptyState
            icon={ReceiptText}
            title={t("finance.pnlEmpty.title")}
            description={t("finance.pnlEmpty.description")}
            className="py-8"
          />
        ) : (
          <>
          {/* Below md the P&L becomes a card per month — tapping a month
           * filters the ledger, so it stays a real 44px control, not a pan
           * target inside a 560px table. */}
          <div className="space-y-2 md:hidden">
            {payload.pnl.map((row) => (
              <div key={row.month} className="rounded-xl border bg-card p-3 shadow-xs">
                <button
                  type="button"
                  className="inline-flex h-11 items-center font-medium text-primary underline"
                  onClick={() => {
                    setMonth(row.month);
                    setOffset(0);
                    replaceLedgerUrl(row.month, typeFilter, categoryFilter);
                  }}
                >
                  {row.month}
                </button>
                <dl className="space-y-1 text-sm">
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-muted-foreground">{t("finance.income")}</dt>
                    <dd className="tabular-nums text-success">{formatMoney(row.income)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-muted-foreground">{t("finance.expense")}</dt>
                    <dd className="tabular-nums text-destructive">{formatMoney(row.expense)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-muted-foreground">{t("finance.net")}</dt>
                    <dd
                      className={cn(
                        "tabular-nums font-medium",
                        row.net < 0 ? "text-destructive" : "text-success",
                      )}
                    >
                      {formatMoney(row.net)}
                    </dd>
                  </div>
                </dl>
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[560px]">
            <TableHeader>
              <TableRow>
                <TableHead>{t("finance.col.month")}</TableHead>
                <TableHead className="text-right">{t("finance.income")}</TableHead>
                <TableHead className="text-right">{t("finance.expense")}</TableHead>
                <TableHead className="text-right">{t("finance.net")}</TableHead>
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
          </div>
          </>
        )}
      </DataTableCard>

      <DataTableCard
        title={t("finance.transactionsTitle")}
        description={`${t("finance.transactionsDescription")}${sort ? ` ${t("finance.sortingNote")}` : ""}`}
        contentClassName="space-y-4"
      >
        {ledgerSettling && (
          <p role="status" className="text-sm text-muted-foreground">
            {t("finance.updating")}
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
            aria-label={t("finance.filterByMonth")}
          />
          <Select
            value={typeFilter}
            onValueChange={(v) => {
              // The item set below is exactly ALL plus the enum members.
              setTypeFilter(v as typeof ALL | TransactionInType);
              setOffset(0);
              replaceLedgerUrl(month, v as typeof ALL | TransactionInType, categoryFilter);
            }}
            items={typeFilterItems(language, t)}
          >
            <SelectTrigger aria-label={t("finance.filterByType")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("finance.allTypes")}</SelectItem>
              {TYPES.map((t) => (
                <SelectItem key={t} value={t}>
                  {txTypeLabel(t, language)}
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
            items={categoryFilterItems(language, t)}
          >
            <SelectTrigger aria-label={t("finance.filterByCategory")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("finance.allCategories")}</SelectItem>
              {CATEGORIES.map((c) => (
                <SelectItem key={c} value={c}>
                  {enumLabel("txCategory", c, language)}
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
              {t("common.clear")}
            </Button>
          )}
        </div>

        {payload.transactions.length === 0 ? (
          month || typeFilter !== ALL || categoryFilter !== ALL ? (
            // A filter excluded every row: the ledger itself may not be empty.
            <EmptyState
              icon={ReceiptText}
              title={t("finance.emptyFiltered.title")}
              description={t("finance.emptyFiltered.description")}
            >
              <Button type="button" variant="outline" size="sm" onClick={clearLedgerFilters}>
                {t("finance.clearFilters")}
              </Button>
            </EmptyState>
          ) : (
            // No filters active and nothing in range: the farm has no ledger yet.
            <EmptyState
              icon={ReceiptText}
              title={t("finance.empty.title")}
              description={t("finance.empty.description")}
            >
              {canManage && (
                <Button size="sm" onClick={openAddDialog}>
                  {t("finance.addTransaction")}
                </Button>
              )}
            </EmptyState>
          )
        ) : (
          <>
          {/* Below md the 8-column ledger becomes a card per transaction —
           * panning a 900px table inside a 390px phone is not a ledger, it's
           * a scroll toy. Amounts stay right-aligned tabular-nums; Correct
           * keeps a 44px touch target. */}
          <div className="space-y-2 md:hidden">
            {sortedTransactions.map((txn) => (
              <div
                key={txn.id}
                className={cn(
                  "space-y-1.5 rounded-xl border bg-card p-3 shadow-xs",
                  txn.voided_at && "opacity-70",
                )}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="flex items-center gap-2">
                    {/* Localized children — the badge's humanize fallback is
                     * English-only, and the type filters ten lines up already
                     * resolve through the txType family (2026-10-01 audit, 06-2). */}
                    <StatusBadge status={txn.type}>
                      {enumLabel("txType", txn.type, language)}
                    </StatusBadge>
                    {txn.voided_at && <Badge variant="destructive">{t("finance.void")}</Badge>}
                  </span>
                  <span
                    className={cn(
                      "tabular-nums font-medium",
                      AMOUNT_TINTS[txn.type],
                      txn.voided_at && "line-through",
                    )}
                  >
                    {formatMoney(txn.amount)}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground">
                  {formatDate(txn.date)} · {enumLabel("txCategory", txn.category, language)}
                  {txn.animal_tag && txn.related_animal_id ? (
                    <>
                      {" · "}
                      {canViewAnimals ? (
                        <Link
                          href={`/animals/${txn.related_animal_id}`}
                          className="text-primary underline"
                        >
                          {txn.animal_tag}
                        </Link>
                      ) : (
                        txn.animal_tag
                      )}
                    </>
                  ) : null}
                </p>
                {txn.notes && <p className="text-sm">{txn.notes}</p>}
                <div className="space-y-0.5 text-xs text-muted-foreground">
                  {txn.correction_of_id !== null && (
                    <p className="font-medium">
                      {t("finance.correctionOf", { number: txn.correction_of_id })}
                    </p>
                  )}
                  {sourceLabel(txn, t) ? (
                    <p>{t("finance.sourceLine", { source: sourceLabel(txn, t) ?? "" })}</p>
                  ) : txn.correction_of_id === null ? (
                    <p>{t("finance.manualEntry")}</p>
                  ) : null}
                  {txn.void_reason && (
                    <p className="text-destructive">
                      {t("finance.voidReasonLine", { reason: txn.void_reason })}
                    </p>
                  )}
                </div>
                {canManage && !txn.voided_at && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-11 px-4"
                    disabled={correctionPending || ledgerSettling || query.isFetching}
                    onClick={() => setCorrecting(txn)}
                  >
                    {t("finance.correct")}
                  </Button>
                )}
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[900px]">
            <TableHeader>
              <TableRow>
                <SortableTableHead
                  column="date"
                  label={t("finance.date")}
                  direction={sort?.column === "date" ? sort.direction : null}
                  onSort={toggleSort}
                />
                <TableHead>{t("finance.type")}</TableHead>
                <TableHead>{t("finance.category")}</TableHead>
                <SortableTableHead
                  column="amount"
                  label={t("finance.amount")}
                  className="text-right"
                  direction={sort?.column === "amount" ? sort.direction : null}
                  onSort={toggleSort}
                />
                <TableHead>{t("finance.animal")}</TableHead>
                <TableHead>{t("finance.notes")}</TableHead>
                <TableHead>{t("finance.col.source")}</TableHead>
                {canManage && (
                  <TableHead><span className="sr-only">{t("finance.col.actions")}</span></TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedTransactions.map((txn) => (
                <TableRow key={txn.id} className={cn(txn.voided_at && "bg-muted/40 opacity-70")}>
                  <TableCell>{formatDate(txn.date)}</TableCell>
                  <TableCell>
                    {/* Localized children — same txType family as the mobile
                     * card and the type filters (2026-10-01 audit, 06-2). */}
                    <StatusBadge status={txn.type}>
                      {enumLabel("txType", txn.type, language)}
                    </StatusBadge>
                    {txn.voided_at && (
                      <Badge variant="destructive" className="ml-2">{t("finance.void")}</Badge>
                    )}
                  </TableCell>
                  <TableCell>{enumLabel("txCategory", txn.category, language)}</TableCell>
                  <TableCell
                    className={cn(
                      "text-right tabular-nums font-medium",
                      AMOUNT_TINTS[txn.type],
                      txn.voided_at && "line-through",
                    )}
                  >
                    {formatMoney(txn.amount)}
                  </TableCell>
                  <TableCell>
                    {txn.animal_tag && txn.related_animal_id && canViewAnimals ? (
                      <Link
                        href={`/animals/${txn.related_animal_id}`}
                        className="text-primary underline"
                      >
                        {txn.animal_tag}
                      </Link>
                    ) : txn.animal_tag && txn.related_animal_id ? (
                      txn.animal_tag
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    {txn.notes ?? ""}
                  </TableCell>
                  <TableCell className="max-w-64 whitespace-normal">
                    {txn.correction_of_id !== null && (
                      <span className="block text-xs font-medium">
                        {t("finance.correctionOf", { number: txn.correction_of_id })}
                      </span>
                    )}
                    {sourceLabel(txn, t) ? (
                      <span className="block text-xs text-muted-foreground">
                        {t("finance.sourceLine", { source: sourceLabel(txn, t) ?? "" })}
                      </span>
                    ) : txn.correction_of_id === null ? (
                      <span className="block text-xs text-muted-foreground">
                        {t("finance.manualEntry")}
                      </span>
                    ) : null}
                    {txn.void_reason && (
                      <span className="mt-1 block text-xs text-destructive">
                        {t("finance.voidReasonLine", { reason: txn.void_reason })}
                      </span>
                    )}
                  </TableCell>
                  {canManage && (
                    <TableCell>
                      {!txn.voided_at && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={correctionPending || ledgerSettling || query.isFetching}
                          onClick={() => setCorrecting(txn)}
                        >
                          {t("finance.correct")}
                        </Button>
                      )}
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          </>
        )}
        <PaginationControls
          total={payload.transactions_total}
          limit={payload.limit}
          offset={payload.offset}
          onOffsetChange={setOffset}
          label={t("finance.paginationLabel")}
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
            <DialogTitle>{t("finance.newTransaction")}</DialogTitle>
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
                <Label htmlFor="date">{t("finance.date")} *</Label>
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
                <Label htmlFor="transaction-type">{t("finance.type")}</Label>
                <Select
                  value={wType}
                  onValueChange={(v) =>
                    // Stryker disable next-line ObjectLiteral, BooleanLiteral: the type select only ever sets valid enum values, so shouldValidate never surfaces a different error state
                    setValue("type", v as TxnInput["type"], { shouldValidate: true })
                  }
                  items={typeItemsMap}
                >
                  <SelectTrigger id="transaction-type" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {txTypeLabel(t, language)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="transaction-category">{t("finance.category")}</Label>
                <Select
                  value={wCategory}
                  onValueChange={(v) =>
                    // Stryker disable next-line ObjectLiteral, BooleanLiteral: the category select only ever sets valid enum values, so shouldValidate never surfaces a different error state
                    setValue("category", v as TxnInput["category"], { shouldValidate: true })
                  }
                  items={categoryItemsMap}
                >
                  <SelectTrigger id="transaction-category" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CATEGORIES.map((c) => (
                      <SelectItem key={c} value={c}>
                        {enumLabel("txCategory", c, language)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="amount">{t("finance.form.amountLabel")}</Label>
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
                    <Label htmlFor="transaction-animal">{t("finance.form.animalOptional")}</Label>
                    <AnimalPicker
                      id="transaction-animal"
                      value={wRelatedAnimalId || NONE}
                      onValueChange={(v) => setValue("related_animal_id", v)}
                      placeholder={t("finance.form.noAnimal")}
                      dialogTitle={t("finance.form.chooseAnimal")}
                      staticOptions={[{ value: NONE, label: t("common.none") }]}
                    />
                  </>
                ) : (
                  <>
                    <p className="text-sm font-medium">{t("finance.form.animalOptional")}</p>
                    <p className="text-xs text-muted-foreground">
                      {t("finance.form.noAnimalAccess")}
                    </p>
                  </>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="notes">{t("finance.notes")}</Label>
                <Input
                  id="notes"
                  maxLength={255}
                  placeholder={t("finance.form.notesPlaceholder")}
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
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={isSubmitting || addFlight.pending}>
                {isSubmitting || addFlight.pending
                  ? t("finance.saving")
                  : formError
                    ? t("finance.retryAdd")
                    : t("finance.addTransaction")}
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
  const t = useT();
  return (
    <Suspense
      fallback={
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("common.loading")}</span>
          <PageSkeleton stats={3} cards={2} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="finance.view"
        label={t("finance.title")}
        description={t("finance.description")}
        stats={3}
        cards={2}
        alsoLoading={authLoading}
      >
        <FinancePageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
