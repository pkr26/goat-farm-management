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
  useListInsurancePoliciesApiFinanceInsuranceGet,
  useRenewPolicyApiFinanceInsurancePolicyIdRenewPost,
} from "@/api/generated/endpoints";
import type { InsurancePolicyOut } from "@/api/generated/models";
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
import { farmToday, formatDate, formatMoney } from "@/lib/format";
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
    premium: z.coerce
      .number()
      .nonnegative("Premium cannot be negative")
      .max(MAX_AMOUNT, `Premium cannot exceed ${formatMoney(MAX_AMOUNT)}`)
      .refine(isPersistableNonnegativeMoney, MIN_PERSISTED_MONEY_MESSAGE),
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
        toast.success("Policy registered.");
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
        // Never block dismissal on an in-flight write; the continuation is
        // guarded by the single-flight and farm-scope fences instead.
        if (!nextOpen && saveBusy) return;
        if (!nextOpen) reset(policyDefaults());
        onClose();
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Register policy</DialogTitle>
          <DialogDescription id="insurance-create-consequence">
            A future renewal date queues the renewal duty automatically. The policy number must
            be unique on this farm.
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
                <Label htmlFor="policy_number">Policy number *</Label>
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
                <Label htmlFor="insurer">Insurer *</Label>
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
                <Label htmlFor="sum_insured">Sum insured (₹) *</Label>
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
                <Label htmlFor="premium">Premium (₹) *</Label>
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
                <Label htmlFor="start_date">Start date *</Label>
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
                <Label htmlFor="renewal_date">Renewal date *</Label>
                <Input
                  id="renewal_date"
                  type="date"
                  aria-invalid={Boolean(errors.renewal_date) || undefined}
                  aria-describedby={errors.renewal_date ? "renewal-date-error" : undefined}
                  {...register("renewal_date")}
                />
                <p className="text-xs text-muted-foreground">
                  May lie ahead — the renewal duty is spawned from it.
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
                    <Label htmlFor="policy-animal">Animal (optional)</Label>
                    <AnimalPicker
                      id="policy-animal"
                      value={animalId || NONE}
                      onValueChange={(value) => setValue("animal_id", value)}
                      placeholder="No animal"
                      dialogTitle="Choose an animal for this policy"
                      staticOptions={[{ value: NONE, label: "— none —" }]}
                    />
                    <p className="text-xs text-muted-foreground">
                      Leave unlinked for a herd-level policy.
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-sm font-medium">Animal (optional)</p>
                    <p className="text-xs text-muted-foreground">
                      You don&apos;t have animal access, so this policy will be saved as
                      herd-level.
                    </p>
                  </>
                )}
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="policy_notes">Notes</Label>
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
                Cancel
              </Button>
              <Button type="submit" disabled={saveBusy}>
                {saveBusy ? "Saving…" : formError ? "Retry save" : "Register policy"}
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
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RenewalInput, unknown, RenewalValues>({
    resolver: zodResolver(renewalSchema.superRefine((values, ctx) => {
      // renew_insurance_policy refuses to move the horizon backwards.
      if (values.renewal_date < policy.renewal_date) {
        ctx.addIssue({
          code: "custom",
          path: ["renewal_date"],
          message: `Cannot be before the current renewal date ${formatDate(policy.renewal_date)}`,
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
        toast.success("Policy renewed.");
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
        if (!nextOpen && renewBusy) return;
        onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Renew policy {policy.policy_number}</DialogTitle>
          <DialogDescription>
            The policy keeps its number and history — only the renewal horizon moves forward.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          <fieldset disabled={renewBusy} className="contents">
            {formError && (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="new_renewal_date">New renewal date *</Label>
              <Input
                id="new_renewal_date"
                type="date"
                min={policy.renewal_date}
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
              <Label htmlFor="renewal_premium">Corrected premium (₹)</Label>
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
                Leave blank to keep the current {formatMoney(policy.premium)} premium.
              </p>
              {errors.premium && (
                <p id="renewal-premium-error" role="alert" className="text-sm text-destructive">
                  {errors.premium.message}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" disabled={renewBusy} onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" disabled={renewBusy}>
                {renewBusy ? "Renewing…" : formError ? "Retry renewal" : "Renew policy"}
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
        toast.success(`Claim recorded on policy ${policy.policy_number}.`);
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
        if (!nextOpen && claimFlight.pending) return;
        onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Record claim on {policy.policy_number}</DialogTitle>
          <DialogDescription>
            The register&apos;s terminal event — the policy keeps its history and
            cannot be claimed twice. Any payout the insurer settles belongs in
            the ledger as income, not here.
          </DialogDescription>
        </DialogHeader>
        {formError && <p role="alert" className="text-sm text-destructive">{formError}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" disabled={claimFlight.pending} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={claimFlight.pending}
            onClick={() => void onConfirm()}
          >
            {claimFlight.pending ? "Recording…" : "Record claim"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function InsurancePageContent({ perms }: { perms: PermissionsState }) {
  const enumLabel = useEnumLabel();
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
              : "Could not load the insurance register."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry insurance
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title="Insurance"
          description="Policy register with renewal dates, most urgent first."
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading insurance…</span>
          <PageSkeleton cards={1} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title="Insurance"
        description="Policy register with renewal dates, most urgent first."
        actions={
          canManage && (
            <Button disabled={settling} onClick={() => setCreating(true)}>
              <Plus /> Register policy
            </Button>
          )
        }
      />

      <FinanceNav active="insurance" />

      {payload.policies.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title="No policies registered."
          description="Register a policy to track its sum insured, premium and renewal duty."
        >
          {canManage && (
            <Button disabled={settling} onClick={() => setCreating(true)}>
              <Plus /> Register your first policy
            </Button>
          )}
        </EmptyState>
      ) : (
        <DataTableCard
          title="Policies"
          description={`${payload.total} polic${payload.total === 1 ? "y" : "ies"} recorded, most urgent renewal first.`}
        >
          {settling && (
            <p role="status" className="pb-3 text-sm text-muted-foreground">
              Updating policies…
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
                    <dt className="text-muted-foreground">Sum insured</dt>
                    <dd className="tabular-nums">{formatMoney(policy.sum_insured)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-muted-foreground">Premium</dt>
                    <dd className="tabular-nums">{formatMoney(policy.premium)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-muted-foreground">Renewal date</dt>
                    <dd>{formatDate(policy.renewal_date)}</dd>
                  </div>
                </dl>
                {canManage && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-11 px-4"
                      disabled={settling || policy.status === "claimed"}
                      onClick={() => setRenewing(policy)}
                    >
                      Renew
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-11 px-4"
                      disabled={settling || policy.status === "claimed"}
                      onClick={() => setClaiming(policy)}
                    >
                      Claim
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
                <TableHead>Policy number</TableHead>
                <TableHead>Insurer</TableHead>
                <TableHead>Animal</TableHead>
                <TableHead className="text-right">Sum insured</TableHead>
                <TableHead className="text-right">Premium</TableHead>
                <TableHead>Renewal date</TableHead>
                <TableHead>Status</TableHead>
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
                  {canManage && (
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={settling || policy.status === "claimed"}
                          onClick={() => setRenewing(policy)}
                        >
                          Renew
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={settling || policy.status === "claimed"}
                          onClick={() => setClaiming(policy)}
                        >
                          Claim
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
            label="policies"
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
  return (
    <PermissionGate
      perms={perms}
      perm="finance.view"
      label="Insurance"
      description="Policy register with renewal dates, most urgent first."
      cards={1}
    >
      <InsurancePageContent perms={perms} />
    </PermissionGate>
  );
}
