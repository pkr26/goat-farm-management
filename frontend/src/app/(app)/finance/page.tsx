"use client";

/** Finance ledger: filters, totals, transactions, 12-month P&L — parity with v1's finance.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ReceiptText, Scale, TrendingDown, TrendingUp } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useForm , useWatch} from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getListTransactionsApiFinanceGetQueryKey,
  useAddTransactionApiFinanceNewPost,
  useListAnimalsApiAnimalsGet,
  useListTransactionsApiFinanceGet,
} from "@/api/generated/endpoints";
import {
  TransactionInCategory,
  TransactionInType,
  type ListTransactionsApiFinanceGetParams,
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
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatCard } from "@/components/stat-card";
import { ApiError } from "@/lib/api-client";
import { formatDate, formatMoney } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";
import { cn } from "@/lib/utils";

const CATEGORIES = Object.values(TransactionInCategory);
const TYPES = Object.values(TransactionInType);
/** Sentinel for "no filter / no selection" (empty string is not a valid item value). */
const ALL = "all";
const NONE = "none";

function localToday(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
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
  amount: z.coerce.number().positive("Amount must be greater than 0"),
  notes: z.string().max(255).optional(),
  related_animal_id: z.string().optional(),
});
type TxnInput = z.input<typeof txnSchema>;
type TxnValues = z.output<typeof txnSchema>;

/** Income vs expense tint pair (light + dark), reused for badges and amounts. */
const TYPE_TINTS: Record<string, string> = {
  INCOME: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  EXPENSE: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};
const AMOUNT_TINTS: Record<string, string> = {
  INCOME: "text-emerald-600 dark:text-emerald-400",
  EXPENSE: "text-red-600 dark:text-red-400",
};

export default function FinancePage() {
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("finance.view");
  const canManage = can("finance.manage");
  const queryClient = useQueryClient();

  const [month, setMonth] = useState("");
  const [typeFilter, setTypeFilter] = useState(ALL);
  const [categoryFilter, setCategoryFilter] = useState(ALL);
  const [open, setOpen] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Omit inactive filters entirely: the Orval URL builder serializes `null`
  // as the literal string "null", which the backend treats as a real filter
  // and matches zero rows.
  const params: ListTransactionsApiFinanceGetParams = {
    ...(month && { month }),
    ...(typeFilter !== ALL && { type: typeFilter }),
    ...(categoryFilter !== ALL && { category: categoryFilter }),
  };
  const query = useListTransactionsApiFinanceGet(params, { query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  const animalsQuery = useListAnimalsApiAnimalsGet(
    {},
    { query: { enabled: canManage && open } },
  );
  const animals =
    animalsQuery.data?.status === 200 ? animalsQuery.data.data.animals : [];
  /** value → label map for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const animalItems: Record<string, string> = {
    [NONE]: "— none —",
    ...Object.fromEntries(
      animals.map((a) => [String(a.id), `${a.tag_number}${a.name ? ` · ${a.name}` : ""}`]),
    ),
  };

  const addMutation = useAddTransactionApiFinanceNewPost();
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<TxnInput, unknown, TxnValues>({
    resolver: zodResolver(txnSchema),
    defaultValues: {
      date: localToday(),
      type: "EXPENSE",
      category: "OTHER",
      notes: "",
      related_animal_id: NONE,
    },
  });
  const wCategory = useWatch({ control, name: "category" });
  const wRelatedAnimalId = useWatch({ control, name: "related_animal_id" });
  const wType = useWatch({ control, name: "type" });

  async function onSubmit(values: TxnValues) {
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
      queryClient.invalidateQueries({ queryKey: getListTransactionsApiFinanceGetQueryKey() });
      setOpen(false);
      reset();
    } catch (err) {
      const message = mutationError(err);
      setFormError(message);
      toast.error(message);
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
          {query.error instanceof ApiError ? query.error.detail : "Could not load finance."}
        </p>
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
                reset();
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
          <Table>
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
                      onClick={() => setMonth(row.month)}
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
            onChange={(e) => setMonth(e.target.value)}
            className="w-40"
            aria-label="Filter by month"
          />
          <Select value={typeFilter} onValueChange={(v) => setTypeFilter(v)}>
            <SelectTrigger>
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
          <Select value={categoryFilter} onValueChange={(v) => setCategoryFilter(v)}>
            <SelectTrigger>
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
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Category</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Animal</TableHead>
                <TableHead>Notes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.transactions.map((t) => (
                <TableRow key={t.id}>
                  <TableCell>{formatDate(t.date)}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={cn("border-transparent", TYPE_TINTS[t.type])}>
                      {t.type}
                    </Badge>
                  </TableCell>
                  <TableCell>{t.category}</TableCell>
                  <TableCell
                    className={cn("text-right tabular-nums font-medium", AMOUNT_TINTS[t.type])}
                  >
                    {formatMoney(t.amount)}
                  </TableCell>
                  <TableCell>
                    {t.animal_tag && t.related_animal_id ? (
                      <Link
                        href={`/animals/${t.related_animal_id}`}
                        className="text-primary underline"
                      >
                        {t.animal_tag}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>{t.notes ?? ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DataTableCard>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>New transaction</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            {formError && <p className="text-sm text-destructive">{formError}</p>}
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="date">Date *</Label>
                <Input id="date" type="date" max={localToday()} {...register("date")} />
                {errors.date && (
                  <p className="text-sm text-destructive">{errors.date.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label>Type</Label>
                <Select
                  value={wType}
                  onValueChange={(v) =>
                    setValue("type", v as TxnInput["type"], { shouldValidate: true })
                  }
                >
                  <SelectTrigger className="w-full">
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
                <Label>Category</Label>
                <Select
                  value={wCategory}
                  onValueChange={(v) =>
                    setValue("category", v as TxnInput["category"], { shouldValidate: true })
                  }
                >
                  <SelectTrigger className="w-full">
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
                  min="0.01"
                  inputMode="decimal"
                  placeholder="0.00"
                  {...register("amount")}
                />
                {errors.amount && (
                  <p className="text-sm text-destructive">{errors.amount.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label>Animal (optional)</Label>
                <Select
                  value={wRelatedAnimalId || NONE}
                  onValueChange={(v) => setValue("related_animal_id", v)}
                  items={animalItems}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>— none —</SelectItem>
                    {animals.map((a) => (
                      <SelectItem key={a.id} value={String(a.id)}>
                        {a.tag_number}
                        {a.name ? ` · ${a.name}` : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="notes">Notes</Label>
                <Input id="notes" maxLength={255} placeholder="description" {...register("notes")} />
                {errors.notes && (
                  <p className="text-sm text-destructive">{errors.notes.message}</p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Saving…" : "Add transaction"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
