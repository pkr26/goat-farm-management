"use client";

/** Insurance register — list, create dialog and renew action (parity with
 *  the ledger's page structure). The register is append-only: a renewal moves
 *  the horizon forward, there is no edit or delete. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useForm, useWatch, type DefaultValues } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useAddInsurancePolicyApiFinanceInsurancePost,
  useClaimPolicyApiFinanceInsurancePolicyIdClaimPost,
  useInsurancePolicyHistoryApiFinanceInsurancePolicyIdHistoryGet,
  useListInsurancePoliciesApiFinanceInsuranceGet,
  useRenewPolicyApiFinanceInsurancePolicyIdRenewPost,
} from "@/api/generated/endpoints";
import type {
  InsurancePolicyHistoryOut,
  InsurancePolicyOut,
  InsurancePremiumOut,
} from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
import { FinanceNav } from "@/components/finance-nav";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PaginationControls } from "@/components/pagination-controls";
import { PermissionGate } from "@/components/permission-gate";
import { PageSkeleton } from "@/components/skeletons";
import { StaleDataNotice } from "@/components/stale-data-notice";
import { StatusBadge } from "@/components/status-badge";
import { useEnumLabel } from "@/lib/enum-labels";
import { Button } from "@/components/ui/button";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api-client";
import { mutationError } from "@/lib/mutations";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import {
  addDays,
  farmToday,
  formatDate,
  formatFarmDateTime,
  formatMoney,
} from "@/lib/format";
import { useT } from "@/lib/i18n";
import { invalidateFarmData } from "@/lib/query-invalidation";
import {
  isPersistableNonnegativeMoney,
  MIN_PERSISTED_MONEY_MESSAGE,
} from "@/lib/persisted-numbers";
import { MAX_FREE_TEXT_LENGTH } from "@/lib/backend-caps";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

/** Backend per-amount ceiling; the validation copy prints it Indian-grouped. */
const MAX_AMOUNT = 1_000_000_000;
/** Sentinel for "no animal link" (empty string is not a valid item value). */
const NONE = "none";
const INSURANCE_PAGE_LIMIT = 50;
/** Premium history pages inside the disclosure (bounded server-side too). */
const INSURANCE_HISTORY_PAGE_LIMIT = 20;

/** Mirrors InsurancePolicyIn: identifiers 1–60/1–120, sum insured positive,
 *  premium non-negative, start ≤ today, renewal never before start. */
const policySchema = z
  .object({
    policy_number: z
      .string()
      .trim()
      .min(1, "Policy number is required")
      .max(60, "Policy number cannot exceed 60 characters"),
    insurer: z
      .string()
      .trim()
      .min(1, "Insurer is required")
      .max(120, "Insurer cannot exceed 120 characters"),
    sum_insured: z.coerce
      .number()
      .positive("Sum insured must be greater than 0")
      .max(MAX_AMOUNT, `Sum insured cannot exceed ${formatMoney(MAX_AMOUNT)}`)
      .refine(isPersistableNonnegativeMoney, MIN_PERSISTED_MONEY_MESSAGE),
    // Blank must stay distinguishable from a real ₹0: z.coerce.number() maps
    // a cleared number input ("") to 0, which would silently register the
    // required premium as ₹0 in the append-only register (2026-09-17 audit
    // M-13; same hazard the ledger-correction schema documents). The renewal
    // dialog's deliberate blank-means-keep-current is a different control.
    premium: z.preprocess(
      (raw) =>
        // Stryker disable next-line ConditionalExpression: registered number inputs only ever yield "" or a numeric string — null/undefined never arrive, and the blank arm is the behavior this pin exists for
        raw === "" || raw === null || raw === undefined ? undefined : Number(raw),
      z
        .number({ error: "Premium is required" })
        .nonnegative("Premium cannot be negative")
        .max(MAX_AMOUNT, `Premium cannot exceed ${formatMoney(MAX_AMOUNT)}`)
        .refine(isPersistableNonnegativeMoney, MIN_PERSISTED_MONEY_MESSAGE),
    ),
    start_date: z
      .string()
      .min(1, "Start date is required")
      .refine((s) => s <= farmToday(), "Start date can't be in the future"),
    renewal_date: z.string().min(1, "Renewal date is required"),
    animal_id: z.string().optional(),
    notes: z.string().max(MAX_FREE_TEXT_LENGTH, "Notes cannot exceed 4000 characters").optional(),
  })
  .refine((v) => v.renewal_date >= v.start_date, {
    message: "Renewal date cannot be before the start date",
    path: ["renewal_date"],
  });
type PolicyInput = z.input<typeof policySchema>;
type PolicyValues = z.output<typeof policySchema>;

/** Rebuilt on every open so a stale mount-time snapshot never dates a new
 *  policy to the day the tab was opened (the ledger's txnDefaults rule). */
function policyDefaults(): DefaultValues<PolicyInput> {
  return {
    policy_number: "",
    insurer: "",
    start_date: farmToday(),
    renewal_date: "",
    animal_id: NONE,
    notes: "",
  };
}

function AddPolicyDialog({
  canViewAnimals,
  onClose,
  onSaved,
}: {
  canViewAnimals: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mutation = useAddInsurancePolicyApiFinanceInsurancePost();
  const saveFlight = useSingleFlight();
  const t = useT();
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<PolicyInput, unknown, PolicyValues>({
    resolver: zodResolver(policySchema),
    defaultValues: policyDefaults(),
  });
  const animalId = useWatch({ control, name: "animal_id" });
  const saveBusy = isSubmitting || saveFlight.pending;

  async function onSubmit(values: PolicyValues) {
    await saveFlight.run(async () => {
      const farmScope = captureFarmScope();
      setFormError(null);
      try {
        await mutation.mutateAsync({
          data: {
            policy_number: values.policy_number.trim(),
            insurer: values.insurer.trim(),
            sum_insured: values.sum_insured,
            premium: values.premium,
            start_date: values.start_date,
            renewal_date: values.renewal_date,
            // Number(NONE) is NaN and JSON serialization maps NaN to null —
            // the same wire value the null arm produces (ledger precedent).
            animal_id:
              values.animal_id && values.animal_id !== NONE
                ? Number(values.animal_id)
                : null,
            notes: values.notes?.trim() || null,
          },
        });
        if (!farmScope()) return;
        toast.success(t("insurance.registeredToast"));
        onSaved();
        onClose();
      } catch (err) {
        if (!farmScope()) return;
        const message = mutationError(err);
        setFormError(message);
        toast.error(message);
      }
    });
  }

  return (
    <Dialog
      open
      onOpenChange={(nextOpen) => {
        // As the comment always claimed: never block dismissal on an
        // in-flight write — the continuation is guarded by the single-flight
        // and farm-scope fences instead (the busy guard contradicted the
        // comment and wedged the dialog shut on a slow request; P3,
        // 2026-09-20 audit).
        if (!nextOpen) reset(policyDefaults());
        onClose();
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("insurance.registerPolicy")}</DialogTitle>
          <DialogDescription id="insurance-create-consequence">
            {t("insurance.dialog.description")}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          <fieldset disabled={saveBusy} className="contents">
            {formError && (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="policy_number">{t("insurance.form.policyNumber")}</Label>
                <Input
                  id="policy_number"
                  maxLength={60}
                  aria-invalid={Boolean(errors.policy_number) || undefined}
                  aria-describedby={errors.policy_number ? "policy-number-error" : undefined}
                  {...register("policy_number")}
                />
                {errors.policy_number && (
                  <p id="policy-number-error" role="alert" className="text-sm text-destructive">
                    {errors.policy_number.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="insurer">{t("insurance.form.insurer")}</Label>
                <Input
                  id="insurer"
                  maxLength={120}
                  aria-invalid={Boolean(errors.insurer) || undefined}
                  aria-describedby={errors.insurer ? "insurer-error" : undefined}
                  {...register("insurer")}
                />
                {errors.insurer && (
                  <p id="insurer-error" role="alert" className="text-sm text-destructive">
                    {errors.insurer.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="sum_insured">{t("insurance.form.sumInsured")}</Label>
                <Input
                  id="sum_insured"
                  type="number"
                  step="0.01"
                  min="0"
                  inputMode="decimal"
                  aria-invalid={Boolean(errors.sum_insured) || undefined}
                  aria-describedby={errors.sum_insured ? "sum-insured-error" : undefined}
                  {...register("sum_insured")}
                />
                {errors.sum_insured && (
                  <p id="sum-insured-error" role="alert" className="text-sm text-destructive">
                    {errors.sum_insured.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="premium">{t("insurance.form.premium")}</Label>
                <Input
                  id="premium"
                  type="number"
                  step="0.01"
                  min="0"
                  inputMode="decimal"
                  aria-invalid={Boolean(errors.premium) || undefined}
                  aria-describedby={errors.premium ? "premium-error" : undefined}
                  {...register("premium")}
                />
                {errors.premium && (
                  <p id="premium-error" role="alert" className="text-sm text-destructive">
                    {errors.premium.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="start_date">{t("insurance.form.startDate")}</Label>
                <Input
                  id="start_date"
                  type="date"
                  max={farmToday()}
                  aria-invalid={Boolean(errors.start_date) || undefined}
                  aria-describedby={errors.start_date ? "start-date-error" : undefined}
                  {...register("start_date")}
                />
                {errors.start_date && (
                  <p id="start-date-error" role="alert" className="text-sm text-destructive">
                    {errors.start_date.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="renewal_date">{t("insurance.form.renewalDate")}</Label>
                <Input
                  id="renewal_date"
                  type="date"
                  aria-invalid={Boolean(errors.renewal_date) || undefined}
                  aria-describedby={errors.renewal_date ? "renewal-date-error" : undefined}
                  {...register("renewal_date")}
                />
                <p className="text-xs text-muted-foreground">
                  {t("insurance.form.renewalHint")}
                </p>
                {errors.renewal_date && (
                  <p id="renewal-date-error" role="alert" className="text-sm text-destructive">
                    {errors.renewal_date.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                {canViewAnimals ? (
                  <>
                    <Label htmlFor="policy-animal">{t("insurance.form.animalOptional")}</Label>
                    <AnimalPicker
                      id="policy-animal"
                      value={animalId || NONE}
                      onValueChange={(value) => setValue("animal_id", value)}
                      placeholder={t("insurance.form.noAnimal")}
                      dialogTitle={t("insurance.form.chooseAnimal")}
                      staticOptions={[{ value: NONE, label: t("common.none") }]}
                    />
                    <p className="text-xs text-muted-foreground">
                      {t("insurance.form.herdLevelHint")}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-sm font-medium">{t("insurance.form.animalOptional")}</p>
                    <p className="text-xs text-muted-foreground">
                      {t("insurance.form.noAnimalAccess")}
                    </p>
                  </>
                )}
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="policy_notes">{t("insurance.form.notes")}</Label>
                <Textarea
                  id="policy_notes"
                  rows={2}
                  maxLength={MAX_FREE_TEXT_LENGTH}
                  aria-invalid={Boolean(errors.notes) || undefined}
                  aria-describedby={errors.notes ? "policy-notes-error" : undefined}
                  {...register("notes")}
                />
                {errors.notes && (
                  <p id="policy-notes-error" role="alert" className="text-sm text-destructive">
                    {errors.notes.message}
                  </p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" disabled={saveBusy} onClick={onClose}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={saveBusy}>
                {saveBusy
                  ? t("insurance.saving")
                  : formError
                    ? t("insurance.retrySave")
                    : t("insurance.registerPolicy")}
              </Button>
            </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Renewal moves the horizon forward only; the corrected premium is optional. */
const renewalSchema = z.object({
  renewal_date: z.string().min(1, "New renewal date is required"),
  premium: z.preprocess(
    (value) =>
      // Registered number inputs only ever yield "" or a numeric string —
      // null/undefined never arrive (ledger correction precedent).
      value === "" || value === null || value === undefined ? undefined : Number(value),
    z
      .number()
      .nonnegative("Premium cannot be negative")
      .max(MAX_AMOUNT, `Premium cannot exceed ${formatMoney(MAX_AMOUNT)}`)
      .refine(isPersistableNonnegativeMoney, MIN_PERSISTED_MONEY_MESSAGE)
      .optional(),
  ),
});
type RenewalInput = z.input<typeof renewalSchema>;
type RenewalValues = z.output<typeof renewalSchema>;

function RenewPolicyDialog({
  policy,
  onClose,
  onSaved,
}: {
  policy: InsurancePolicyOut;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mutation = useRenewPolicyApiFinanceInsurancePolicyIdRenewPost();
  const renewFlight = useSingleFlight();
  const t = useT();
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RenewalInput, unknown, RenewalValues>({
    resolver: zodResolver(renewalSchema.superRefine((values, ctx) => {
      // A same-day "renewal" has no coverage interval to book and used to
      // mutate the current premium without a corresponding premium event.
      // Require a real forward extension, mirroring the server invariant.
      if (values.renewal_date <= policy.renewal_date) {
        ctx.addIssue({
          code: "custom",
          path: ["renewal_date"],
          message: `Must be after the current renewal date ${formatDate(policy.renewal_date)}`,
        });
      }
    })),
    defaultValues: { renewal_date: "", premium: "" },
  });
  const renewBusy = isSubmitting || renewFlight.pending;

  async function onSubmit(values: RenewalValues) {
    await renewFlight.run(async () => {
      const farmScope = captureFarmScope();
      setFormError(null);
      try {
        await mutation.mutateAsync({
          policyId: policy.id,
          data: {
            renewal_date: values.renewal_date,
            premium: values.premium ?? null,
          },
        });
        if (!farmScope()) return;
        toast.success(t("insurance.renewedToast"));
        onSaved();
        onClose();
      } catch (err) {
        if (!farmScope()) return;
        const message = mutationError(err);
        setFormError(message);
        toast.error(message);
      }
    });
  }

  return (
    <Dialog
      open
      onOpenChange={() => {
        // Same never-block rule as the register dialog (see its comment).
        onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("insurance.renewTitle", { number: policy.policy_number })}</DialogTitle>
          <DialogDescription>{t("insurance.renewDescription")}</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          <fieldset disabled={renewBusy} className="contents">
            {formError && (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="new_renewal_date">{t("insurance.renewNewDate")}</Label>
              <Input
                id="new_renewal_date"
                type="date"
                min={addDays(policy.renewal_date, 1)}
                aria-invalid={Boolean(errors.renewal_date) || undefined}
                aria-describedby={errors.renewal_date ? "new-renewal-date-error" : undefined}
                {...register("renewal_date")}
              />
              {errors.renewal_date && (
                <p id="new-renewal-date-error" role="alert" className="text-sm text-destructive">
                  {errors.renewal_date.message}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="renewal_premium">{t("insurance.renewPremium")}</Label>
              <Input
                id="renewal_premium"
                type="number"
                step="0.01"
                min="0"
                inputMode="decimal"
                aria-invalid={Boolean(errors.premium) || undefined}
                aria-describedby={errors.premium ? "renewal-premium-error" : undefined}
                {...register("premium")}
              />
              <p className="text-xs text-muted-foreground">
                {t("insurance.renewPremiumHint", { amount: formatMoney(policy.premium) })}
              </p>
              {errors.premium && (
                <p id="renewal-premium-error" role="alert" className="text-sm text-destructive">
                  {errors.premium.message}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" disabled={renewBusy} onClick={onClose}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={renewBusy}>
                {renewBusy
                  ? t("insurance.renewing")
                  : formError
                    ? t("insurance.retryRenewal")
                    : t("insurance.renewPolicyButton")}
              </Button>
            </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ClaimPolicyDialog({
  policy,
  onClose,
  onSaved,
}: {
  policy: InsurancePolicyOut;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mutation = useClaimPolicyApiFinanceInsurancePolicyIdClaimPost();
  const claimFlight = useSingleFlight();
  const t = useT();
  const [formError, setFormError] = useState<string | null>(null);

  async function onConfirm() {
    await claimFlight.run(async () => {
      const farmScope = captureFarmScope();
      setFormError(null);
      try {
        // No body: the claim is dated today server-side. A payout the
        // insurer settles is booked through the ledger, never here.
        await mutation.mutateAsync({ policyId: policy.id, data: {} });
        if (!farmScope()) return;
        toast.success(t("insurance.claimToast", { number: policy.policy_number }));
        onSaved();
        onClose();
      } catch (err) {
        if (!farmScope()) return;
        const message = mutationError(err);
        setFormError(message);
        toast.error(message);
      }
    });
  }

  return (
    <Dialog
      open
      onOpenChange={() => {
        // Same never-block rule as the register dialog (see its comment).
        onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("insurance.claimTitle", { number: policy.policy_number })}</DialogTitle>
          <DialogDescription>{t("insurance.claimDescription")}</DialogDescription>
        </DialogHeader>
        {formError && <p role="alert" className="text-sm text-destructive">{formError}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" disabled={claimFlight.pending} onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={claimFlight.pending}
            onClick={() => void onConfirm()}
          >
            {claimFlight.pending ? t("insurance.recording") : t("insurance.recordClaim")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Load immutable audit facts only when an operator asks to inspect them.
 * Native details/summary gives keyboard and screen-reader users the same
 * disclosure control without adding a custom button state machine. */
function PolicyHistoryDisclosure({ policy }: { policy: InsurancePolicyOut }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  // Premium rows are append-only and the server page is bounded; accumulate
  // pages client-side and offer "Load more" while entries remain.
  const [offset, setOffset] = useState(0);
  const [premiums, setPremiums] = useState<InsurancePremiumOut[]>([]);
  // Render-phase reconciliation (React's documented pattern, same as the
  // simulation number inputs): closing resets the accumulation, and each
  // distinct server page is merged exactly once, identified by its payload
  // object reference — react-query's structural sharing keeps that reference
  // stable for unchanged content, so refetches never double-append.
  const [seenOpen, setSeenOpen] = useState(false);
  const [seenPayload, setSeenPayload] = useState<InsurancePolicyHistoryOut | null>(null);

  const history = useInsurancePolicyHistoryApiFinanceInsurancePolicyIdHistoryGet(
    policy.id,
    { limit: INSURANCE_HISTORY_PAGE_LIMIT, offset },
    {
      query: {
        enabled: open,
        // Keep the previous page visible while the next one loads.
        placeholderData: (previous) => previous,
      },
    },
  );
  const payload = history.data?.status === 200 ? history.data.data : undefined;
  const total = payload && typeof payload.total === "number" ? payload.total : null;

  if (seenOpen !== open) {
    setSeenOpen(open);
    if (!open) {
      setPremiums([]);
      setOffset(0);
      setSeenPayload(null);
    }
  }
  if (payload !== null && payload !== undefined && payload !== seenPayload) {
    setSeenPayload(payload);
    const incoming = payload.premiums;
    setPremiums((existing) => {
      if (offset === 0) return incoming;
      const seenIds = new Set(existing.map((entry) => entry.id));
      return [...existing, ...incoming.filter((entry) => !seenIds.has(entry.id))];
    });
  }

  const moreAvailable = total !== null && premiums.length < total;

  return (
    <details
      className="rounded-md border border-dashed p-2 text-sm"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer font-medium text-primary">
        {t("insurance.historySummary")}
      </summary>
      {open && (
        <section
          aria-label={t("insurance.historyAria", { number: policy.policy_number })}
          className="mt-2 space-y-2"
        >
          {history.isLoading && premiums.length === 0 && (
            <p role="status" aria-live="polite" className="text-muted-foreground">
              {t("insurance.loadingHistory")}
            </p>
          )}
          {history.isError && (
            <div role="alert" className="flex flex-wrap items-center gap-2 text-destructive">
              <span>{t("insurance.historyLoadFailed")}</span>
              <Button type="button" size="sm" variant="outline" onClick={() => void history.refetch()}>
                {t("insurance.retryHistory")}
              </Button>
            </div>
          )}
          {payload && (
            <>
              <div>
                <div className="flex items-baseline justify-between gap-2">
                  <p className="font-medium">{t("insurance.premiumEntries")}</p>
                  {total !== null && (
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {t("insurance.ofTotal", { shown: premiums.length, total })}
                    </span>
                  )}
                </div>
                {premiums.length === 0 ? (
                  <p className="text-muted-foreground">{t("insurance.noPremiums")}</p>
                ) : (
                  <ul className="mt-1 space-y-1" aria-label={t("insurance.premiumEntriesAria")}>
                    {premiums.map((entry) => (
                      <li key={entry.id} className="rounded bg-muted/50 p-1.5">
                        <span className="font-medium tabular-nums">{formatMoney(entry.premium)}</span>
                        <span className="text-muted-foreground">
                          {t("insurance.coverageSuffix", {
                            from: formatDate(entry.covered_from),
                            until: formatDate(entry.covered_until),
                          })}
                          {t("insurance.recordedSuffix", { date: formatDate(entry.recorded_on) })}
                          {entry.recorded_by_id !== null
                            ? t("insurance.byUserSuffix", { id: entry.recorded_by_id })
                            : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {moreAvailable && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="mt-1"
                    disabled={history.isFetching}
                    onClick={() => setOffset(premiums.length)}
                  >
                    {history.isFetching ? t("common.loading") : t("insurance.loadMore")}
                  </Button>
                )}
              </div>
              <div>
                <p className="font-medium">{t("insurance.claimSection")}</p>
                {payload.policy.claim_date ? (
                  <p>
                    {t("insurance.claimRecorded", { date: formatDate(payload.policy.claim_date) })}
                    {payload.policy.claimed_by_id !== null
                      ? t("insurance.byUserSuffix", { id: payload.policy.claimed_by_id })
                      : ""}
                    {payload.policy.claimed_at
                      ? ` (${formatFarmDateTime(payload.policy.claimed_at)})`
                      : ""}
                  </p>
                ) : (
                  <p className="text-muted-foreground">{t("insurance.noClaim")}</p>
                )}
              </div>
            </>
          )}
        </section>
      )}
    </details>
  );
}

function InsurancePageContent({ perms }: { perms: PermissionsState }) {
  const enumLabel = useEnumLabel();
  const t = useT();
  const { can } = perms;
  const allowed = can("finance.view");
  const canManage = can("finance.manage");
  const canViewAnimals = can("animals.view");
  const queryClient = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [renewing, setRenewing] = useState<InsurancePolicyOut | null>(null);
  const [claiming, setClaiming] = useState<InsurancePolicyOut | null>(null);

  const query = useListInsurancePoliciesApiFinanceInsuranceGet(
    { limit: INSURANCE_PAGE_LIMIT, offset },
    {
      query: { enabled: allowed, placeholderData: (previous) => previous },
    },
  );
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const settling = query.isPlaceholderData;

  function refresh() {
    invalidateFarmData(queryClient);
  }

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div role="alert" className="space-y-3">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError
              ? query.error.detail
              : t("insurance.loadFailed")}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            {t("insurance.retry")}
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("insurance.title")}
          description={t("insurance.description")}
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("insurance.loading")}</span>
          <PageSkeleton cards={1} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title={t("insurance.title")}
        description={t("insurance.description")}
        actions={
          canManage && (
            <Button disabled={settling} onClick={() => setCreating(true)}>
              <Plus /> {t("insurance.registerPolicy")}
            </Button>
          )
        }
      />

      <FinanceNav active="insurance" />

      {payload.policies.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title={t("insurance.empty.title")}
          description={t("insurance.empty.description")}
        >
          {canManage && (
            <Button disabled={settling} onClick={() => setCreating(true)}>
              <Plus /> {t("insurance.registerFirstPolicy")}
            </Button>
          )}
        </EmptyState>
      ) : (
        <DataTableCard
          title={t("insurance.policiesTitle")}
          description={
            payload.total === 1
              ? t("insurance.policiesDescriptionOne", { count: payload.total })
              : t("insurance.policiesDescriptionMany", { count: payload.total })
          }
        >
          {settling && (
            <p role="status" className="pb-3 text-sm text-muted-foreground">
              {t("insurance.updating")}
            </p>
          )}
          {/* Below md the 8-column register becomes a card per policy —
           * renewal is the phone-side action, so Renew/Claim stay 44px. */}
          <div className="space-y-2 md:hidden">
            {payload.policies.map((policy) => (
              <div key={policy.id} className="space-y-1.5 rounded-xl border bg-card p-3 shadow-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{policy.policy_number}</span>
                  <StatusBadge status={policy.status}>
                    {enumLabel("insuranceStatus", policy.status)}
                  </StatusBadge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {policy.insurer}
                  {policy.animal_id !== null && policy.animal_tag ? (
                    <>
                      {" · "}
                      {canViewAnimals ? (
                        <Link
                          href={`/animals/${policy.animal_id}`}
                          className="text-primary underline"
                        >
                          {policy.animal_tag}
                        </Link>
                      ) : (
                        policy.animal_tag
                      )}
                    </>
                  ) : null}
                </p>
                <dl className="space-y-1 text-sm">
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-muted-foreground">{t("insurance.col.sumInsured")}</dt>
                    <dd className="tabular-nums">{formatMoney(policy.sum_insured)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-muted-foreground">{t("insurance.col.premium")}</dt>
                    <dd className="tabular-nums">{formatMoney(policy.premium)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-muted-foreground">{t("insurance.col.renewalDate")}</dt>
                    <dd>{formatDate(policy.renewal_date)}</dd>
                  </div>
                </dl>
                <PolicyHistoryDisclosure policy={policy} />
                {canManage && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-11 px-4"
                      disabled={settling || policy.status === "claimed"}
                      onClick={() => setRenewing(policy)}
                    >
                      {t("insurance.renew")}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-11 px-4"
                      disabled={settling || policy.status === "claimed"}
                      onClick={() => setClaiming(policy)}
                    >
                      {t("insurance.claim")}
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[840px]">
            <TableHeader>
              <TableRow>
                <TableHead>{t("insurance.col.policyNumber")}</TableHead>
                <TableHead>{t("insurance.col.insurer")}</TableHead>
                <TableHead>{t("insurance.col.animal")}</TableHead>
                <TableHead className="text-right">{t("insurance.col.sumInsured")}</TableHead>
                <TableHead className="text-right">{t("insurance.col.premium")}</TableHead>
                <TableHead>{t("insurance.col.renewalDate")}</TableHead>
                <TableHead>{t("insurance.col.status")}</TableHead>
                <TableHead>{t("insurance.col.history")}</TableHead>
                {canManage && <TableHead className="text-right" />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.policies.map((policy) => (
                <TableRow key={policy.id}>
                  <TableCell className="font-medium">{policy.policy_number}</TableCell>
                  <TableCell>{policy.insurer}</TableCell>
                  <TableCell>
                    {policy.animal_id !== null && policy.animal_tag ? (
                      canViewAnimals ? (
                        <Link
                          href={`/animals/${policy.animal_id}`}
                          className="text-primary underline"
                        >
                          {policy.animal_tag}
                        </Link>
                      ) : (
                        policy.animal_tag
                      )
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(policy.sum_insured)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(policy.premium)}
                  </TableCell>
                  <TableCell>{formatDate(policy.renewal_date)}</TableCell>
                  <TableCell>
                    <StatusBadge status={policy.status}>
                      {enumLabel("insuranceStatus", policy.status)}
                    </StatusBadge>
                  </TableCell>
                  <TableCell className="min-w-60 align-top">
                    <PolicyHistoryDisclosure policy={policy} />
                  </TableCell>
                  {canManage && (
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={settling || policy.status === "claimed"}
                          onClick={() => setRenewing(policy)}
                        >
                          {t("insurance.renew")}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={settling || policy.status === "claimed"}
                          onClick={() => setClaiming(policy)}
                        >
                          {t("insurance.claim")}
                        </Button>
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          <PaginationControls
            total={payload.total}
            limit={INSURANCE_PAGE_LIMIT}
            offset={offset}
            onOffsetChange={setOffset}
            label={t("insurance.paginationLabel")}
            disabled={settling}
          />
        </DataTableCard>
      )}

      {creating && (
        <AddPolicyDialog
          canViewAnimals={canViewAnimals}
          onClose={() => setCreating(false)}
          onSaved={refresh}
        />
      )}
      {renewing && (
        <RenewPolicyDialog
          policy={renewing}
          onClose={() => setRenewing(null)}
          onSaved={refresh}
        />
      )}
      {claiming && (
        <ClaimPolicyDialog
          policy={claiming}
          onClose={() => setClaiming(null)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}

export default function InsurancePage() {
  const perms = usePermissions();
  const t = useT();
  return (
    <PermissionGate
      perms={perms}
      perm="finance.view"
      label={t("insurance.title")}
      description={t("insurance.description")}
      cards={1}
    >
      <InsurancePageContent perms={perms} />
    </PermissionGate>
  );
}
