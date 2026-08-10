"use client";

/** Finance ledger: filters, totals, transactions, 12-month P&L — parity with v1's finance.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ReceiptText, Scale, TrendingDown, TrendingUp } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
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
  type TransactionOut,
} from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
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
} from "@/components/ui/table";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PaginationControls } from "@/components/pagination-controls";
import { StatCard } from "@/components/stat-card";
import { ApiError } from "@/lib/api-client";
import { farmToday, formatDate, formatMoney } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import {
  isPersistableNonnegativeMoney,
  MIN_PERSISTED_MONEY,
  MIN_PERSISTED_MONEY_MESSAGE,
} from "@/lib/persisted-numbers";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { cn } from "@/lib/utils";

const CATEGORIES = Object.values(TransactionInCategory);
const TYPES = Object.values(TransactionInType);
/** Sentinel for "no filter / no selection" (empty string is not a valid item value). */
const ALL = "all";
const NONE = "none";
/** value → label maps for the root `items` prop: without them, Base UI's
 * Select.Value renders the raw value (the "all" sentinel) in the closed
 * trigger. */
const TYPE_FILTER_ITEMS: Record<string, string> = {
  [ALL]: "All types",
  ...Object.fromEntries(TYPES.map((t) => [t, t])),
};
const CATEGORY_FILTER_ITEMS: Record<string, string> = {
  [ALL]: "All categories",
  ...Object.fromEntries(CATEGORIES.map((c) => [c, c])),
};

function localToday(): string {
  return farmToday();
}

function mutationError(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

const txnSchema = z.object({
  date: z
    .string()
    .min(1, "Date is required")
    .refine((s) => s <= localToday(), "Date can't be in the future"),
  type: z.enum([TransactionInType.INCOME, TransactionInType.EXPENSE]),
  category: z.enum([
    "ANIMAL_SALE",
    "ANIMAL_PURCHASE",
    "FEED",
    "MEDICINE",
    "VET",
    "LABOUR",
    "EQUIPMENT",
    "MILK",
    "MANURE",
    "OTHER",
  ]),
  amount: z.coerce
    .number()
    .positive("Amount must be greater than 0")
    .min(MIN_PERSISTED_MONEY, "Amount must be at least ₹0.005"),
  notes: z.string().max(255).optional(),
  related_animal_id: z.string().optional(),
});
type TxnInput = z.input<typeof txnSchema>;
type TxnValues = z.output<typeof txnSchema>;

/** Rebuilt on every reset: a bare reset() restores react-hook-form's
 * mount-time snapshot, which dates entries to the day the tab was opened. */
function txnDefaults(): DefaultValues<TxnInput> {
  return {
    date: localToday(),
    type: "EXPENSE",
    category: "OTHER",
    notes: "",
    related_animal_id: NONE,
  };
}

/** Income vs expense tint pair (light + dark), reused for badges and amounts. */
const TYPE_TINTS: Record<string, string> = {
  INCOME: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  EXPENSE: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};
const AMOUNT_TINTS: Record<string, string> = {
  INCOME: "text-emerald-600 dark:text-emerald-400",
  EXPENSE: "text-red-600 dark:text-red-400",
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
      value === "" || value === null || value === undefined
        ? undefined
        : Number(value),
    z
      .number({ error: "Amount is required" })
      .nonnegative("Amount can't be negative")
      .refine(isPersistableNonnegativeMoney, MIN_PERSISTED_MONEY_MESSAGE),
  ),
  reason: z.string().trim().min(3, "Reason must be at least 3 characters").max(255),
});
type CorrectionInput = z.input<typeof correctionSchema>;
type CorrectionValues = z.output<typeof correctionSchema>;

function CorrectionDialog({
  transaction,
  canViewAnimals,
  onClose,
  onSaved,
}: {
  transaction: TransactionOut;
  canViewAnimals: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mutation = useCorrectTransactionApiFinanceTransactionsTransactionIdCorrectPost();
  const correctionFlight = useSingleFlight();
  const [formError, setFormError] = useState<string | null>(null);
  const [consequenceConfirmed, setConsequenceConfirmed] = useState(false);
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
  const type = useWatch({ control, name: "type" });
  const category = useWatch({ control, name: "category" });
  const animalId = useWatch({ control, name: "related_animal_id" });
  async function submit(values: CorrectionValues) {
    if (!consequenceConfirmed) return;
    await correctionFlight.run(async () => {
      setFormError(null);
      try {
        await mutation.mutateAsync({
          transactionId: transaction.id,
          data: {
            date: values.date,
            type: values.type,
            category: values.category,
            amount: values.amount,
            notes: values.notes?.trim() || null,
            related_animal_id:
              values.related_animal_id && values.related_animal_id !== NONE
                ? Number(values.related_animal_id)
                : null,
            reason: values.reason,
          },
        });
        toast.success("Correction recorded. The original entry remains in the audit trail.");
        onSaved();
        onClose();
      } catch (error) {
        const message = mutationError(error);
        setFormError(message);
        toast.error(message);
      }
    });
  }

  return (
    <Dialog
      open
      onOpenChange={(nextOpen) =>
        !nextOpen && !isSubmitting && !correctionFlight.pending && onClose()
      }
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Correct transaction #{transaction.id}</DialogTitle>
          <DialogDescription id={`correction-consequence-${transaction.id}`}>
            The original row will be marked void and retained. This creates an audited
            replacement; it does not rewrite financial history.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit(submit)} className="space-y-4" noValidate>
          {formError && (
            <p role="alert" className="text-sm text-destructive">
              {formError}
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor={`correction-date-${transaction.id}`}>Date *</Label>
              <Input
                id={`correction-date-${transaction.id}`}
                type="date"
                max={localToday()}
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
              >
                <SelectTrigger id={`correction-type-${transaction.id}`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TYPES.map((value) => (
                    <SelectItem key={value} value={value}>{value}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`correction-category-${transaction.id}`}>Category</Label>
              <Select
                value={category}
                onValueChange={(value) => setValue("category", value as TransactionInCategory)}
              >
                <SelectTrigger id={`correction-category-${transaction.id}`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((value) => (
                    <SelectItem key={value} value={value}>{value}</SelectItem>
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
              disabled={isSubmitting || correctionFlight.pending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={isSubmitting || correctionFlight.pending || !consequenceConfirmed}
            >
              {isSubmitting || correctionFlight.pending
                ? "Saving correction…"
                : formError
                  ? "Retry correction"
                  : "Record correction"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function FinancePage() {
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("finance.view");
  const canManage = can("finance.manage");
  const canViewAnimals = can("animals.view");
  const queryClient = useQueryClient();

  const [month, setMonth] = useState("");
  // `ALL` is the "no filter" sentinel; every other value is a real enum member,
  // so the ledger filters stay in step with the generated query contract.
  const [typeFilter, setTypeFilter] = useState<typeof ALL | TransactionInType>(ALL);
  const [categoryFilter, setCategoryFilter] = useState<typeof ALL | TransactionInCategory>(ALL);
  const [open, setOpen] = useState(false);
  const [correcting, setCorrecting] = useState<TransactionOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const limit = 50;

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
  const query = useListTransactionsApiFinanceGet(params, { query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  const addMutation = useAddTransactionApiFinanceNewPost();
  const addFlight = useSingleFlight();
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

  async function onSubmit(values: TxnValues) {
    await addFlight.run(async () => {
      setFormError(null);
      try {
        await addMutation.mutateAsync({
          data: {
            date: values.date,
            type: values.type,
            category: values.category,
            amount: values.amount,
            notes: values.notes?.trim() || null,
            related_animal_id:
              values.related_animal_id && values.related_animal_id !== NONE
                ? Number(values.related_animal_id)
                : null,
          },
        });
        toast.success("Transaction saved.");
        invalidateFarmData(queryClient);
        setOpen(false);
        reset(txnDefaults());
      } catch (err) {
        const message = mutationError(err);
        setFormError(message);
        toast.error(message);
      }
    });
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
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  const net = payload.total_income - payload.total_expense;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Finance"
        description="Income, expenses and monthly profit & loss for the farm."
        actions={
          canManage && (
            <Button
              onClick={() => {
                reset(txnDefaults());
                setFormError(null);
                setOpen(true);
              }}
            >
              New transaction
            </Button>
          )
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <StatCard
          label="Total income"
          value={<span className="tabular-nums">{formatMoney(payload.total_income)}</span>}
          icon={TrendingUp}
          tint="emerald"
        />
        <StatCard
          label="Total expense"
          value={<span className="tabular-nums">{formatMoney(payload.total_expense)}</span>}
          icon={TrendingDown}
          tint="red"
        />
        <StatCard
          label="Net (all time)"
          value={<span className="tabular-nums">{formatMoney(net)}</span>}
          icon={Scale}
          tint="amber"
        />
      </div>

      <DataTableCard title="Monthly P&L (last 12 months)">
        {payload.pnl.length === 0 ? (
          <p className="text-muted-foreground">No transactions yet.</p>
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
                      }}
                    >
                      {row.month}
                    </button>
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-emerald-600 dark:text-emerald-400">
                    {formatMoney(row.income)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-red-600 dark:text-red-400">
                    {formatMoney(row.expense)}
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right tabular-nums",
                      row.net < 0
                        ? "text-destructive"
                        : "text-emerald-600 dark:text-emerald-400",
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
        description="Filter the ledger by month, type or category."
        contentClassName="space-y-4"
      >
        <div className="flex flex-wrap items-center gap-3">
          <Input
            type="month"
            value={month}
            onChange={(e) => {
              setMonth(e.target.value);
              setOffset(0);
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
                  {t}
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
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {(month || typeFilter !== ALL || categoryFilter !== ALL) && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setMonth("");
                setTypeFilter(ALL);
                setCategoryFilter(ALL);
                setOffset(0);
              }}
            >
              Clear
            </Button>
          )}
        </div>

        {payload.transactions.length === 0 ? (
          <EmptyState
            icon={ReceiptText}
            title="No transactions match."
            description="Try clearing the filters or add a new transaction."
          />
        ) : (
          <Table className="min-w-[900px]">
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Category</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Animal</TableHead>
                <TableHead>Notes</TableHead>
                <TableHead>Source / audit</TableHead>
                {canManage && <TableHead><span className="sr-only">Actions</span></TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.transactions.map((t) => (
                <TableRow key={t.id} className={cn(t.voided_at && "bg-muted/40 opacity-70")}>
                  <TableCell>{formatDate(t.date)}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={cn("border-transparent", TYPE_TINTS[t.type])}>
                      {t.type}
                    </Badge>
                    {t.voided_at && (
                      <Badge variant="destructive" className="ml-2">VOID</Badge>
                    )}
                  </TableCell>
                  <TableCell>{t.category}</TableCell>
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
                        <Button type="button" size="sm" variant="outline" onClick={() => setCorrecting(t)}>
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
        />
      </DataTableCard>

      {correcting && (
        <CorrectionDialog
          transaction={correcting}
          canViewAnimals={canViewAnimals}
          onClose={() => setCorrecting(null)}
          onSaved={() => invalidateFarmData(queryClient)}
        />
      )}

      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && (isSubmitting || addFlight.pending)) return;
          if (!nextOpen) setFormError(null);
          setOpen(nextOpen);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>New transaction</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            {formError && <p role="alert" className="text-sm text-destructive">{formError}</p>}
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="date">Date *</Label>
                <Input
                  id="date"
                  type="date"
                  max={localToday()}
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
                    setValue("type", v as TxnInput["type"], { shouldValidate: true })
                  }
                >
                  <SelectTrigger id="transaction-type" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {t}
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
                    setValue("category", v as TxnInput["category"], { shouldValidate: true })
                  }
                >
                  <SelectTrigger id="transaction-category" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CATEGORIES.map((c) => (
                      <SelectItem key={c} value={c}>
                        {c}
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
              <Button type="submit" disabled={isSubmitting || addFlight.pending}>
                {isSubmitting || addFlight.pending
                  ? "Saving…"
                  : formError
                    ? "Retry add transaction"
                    : "Add transaction"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
