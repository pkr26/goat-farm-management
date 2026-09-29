"use client";

/** Animal profile — parity with v1's animals/profile.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, ArrowLeftRight, Baby, GitBranch, HeartPulse, Scale } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useAnimalLifetimePnlApiFinanceAnimalsAnimalIdLifetimePnlGet,
  useAnimalProfileApiAnimalsAnimalIdGet,
  useChangeStatusApiAnimalsAnimalIdStatusPost,
  useUpdateAnimalApiAnimalsAnimalIdPatch,
  useClearMovementRestrictionApiHealthRestrictionsAnimalIdClearPost,
  useMovementRestrictionHistoryApiHealthRestrictionsAnimalIdGet,
  useMoveBucketApiAnimalsAnimalIdMovePost,
  useRecordWeightApiAnimalsAnimalIdWeightPost,
} from "@/api/generated/endpoints";
import {
  MoveInToBucket,
  StatusChangeInDisposalMethod,
  StatusChangeInMortalityCauseCode,
  StatusChangeInNewStatus,
  type AnimalProfileOut,
} from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { PageSkeleton, InlineLoading } from "@/components/skeletons";
import { PaginationControls } from "@/components/pagination-controls";
import { StatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { bucketAllowsSex } from "@/lib/bucket-sex";
import { enumLabel } from "@/lib/enum-labels";
import { useLanguage, useT, type MessageKey, type TFn } from "@/lib/i18n";
import { mapServerError } from "@/lib/server-error-phrases";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { farmToday, formatDate, formatFarmDateTime, formatMoney } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { permittedAppPath, withReturnTo } from "@/lib/permission-navigation";
import {
  isPersistableNonnegativeMoney,
} from "@/lib/persisted-numbers";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { cn } from "@/lib/utils";

const BUCKETS = Object.values(MoveInToBucket);
/** Coded mortality causes (StatusChangeInMortalityCauseCode): the select
 * offers them in the wire enum's order, labelled through the enum catalog. */
const MORTALITY_CAUSE_CODES = Object.values(StatusChangeInMortalityCauseCode);
/** value → label map for the root `items` prop, in the active language:
 * without it, Base UI's Select.Value renders the raw code in the closed
 * trigger. `emptyLabel` is the caller's catalog string for the no-code
 * sentinel (empty string is not a valid item value). */
const mortalityCauseCodeItems = (
  language: "en" | "te",
  emptyLabel: string,
): Record<string, string> => ({
  "": emptyLabel,
  ...Object.fromEntries(
    MORTALITY_CAUSE_CODES.map((code) => [code, enumLabel("mortalityCause", code, language)]),
  ),
});
/** Bounded carcass-disposal vocabulary (DisposalMethod), offered in the wire
 * enum's order and labelled through the enum catalog. */
const DISPOSAL_METHODS = Object.values(StatusChangeInDisposalMethod);
/** value → label map for the root `items` prop, in the active language. */
const disposalMethodItems = (
  language: "en" | "te",
  emptyLabel: string,
): Record<string, string> => ({
  "": emptyLabel,
  ...Object.fromEntries(
    DISPOSAL_METHODS.map((method) => [method, enumLabel("disposalMethod", method, language)]),
  ),
});
const PROFILE_HISTORY_LIMIT = 25;
const RESTRICTION_HISTORY_LIMIT = 25;

/** Phenotype coat-colour wire values → catalog keys; an unknown code renders
 * verbatim rather than silently vanishing. */
const COAT_COLOR_LABEL_KEYS: Record<string, MessageKey> = {
  black: "animals.coatColor.black",
  black_patched: "animals.coatColor.black_patched",
  brown: "animals.coatColor.brown",
  white: "animals.coatColor.white",
  spotted: "animals.coatColor.spotted",
};

const optNum = (schema: z.ZodNumber) =>
  z.preprocess(
    (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)),
    schema.optional(),
  );
const emptyToNull = (v: string | undefined) => (v ? v : null);


function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}

function useProfileRefresh(animalId: number) {
  const queryClient = useQueryClient();
  return () => {
    void animalId;
    invalidateFarmData(queryClient);
  };
}

/** Species-scaled cap (max_adult_weight_kg): the backend rejects a recorded
 * weight above it, so mirror the band inline per the farm's species. The
 * messages resolve through the i18n catalog, so the factory takes the
 * caller's `t` (health.buildEventSchema precedent) and the mounted dialog
 * rebuilds it whenever the active language changes. */
function buildWeightSchema(t: TFn, maxWeightKg: number) {
  return z.object({
    date: z
      .string()
      .optional()
      .refine((s) => !s || s <= farmToday(), t("animalDetail.validation.dateFuture")),
    weight_kg: z.coerce
      .number()
      .positive(t("animalDetail.validation.weightPositive"))
      .max(maxWeightKg, t("animalDetail.validation.weightTooLargeSpecies", { max: maxWeightKg })),
    bcs: optNum(z.number().int().min(1).max(5)),
    notes: z.string().max(255, t("animalDetail.validation.notesTooLong")).optional(),
  });
}
type WeightInput = z.input<ReturnType<typeof buildWeightSchema>>;
type WeightValues = z.output<ReturnType<typeof buildWeightSchema>>;
type ProfileActionFlight = ReturnType<typeof useSingleFlight>;

function AddWeightDialog({
  animalId,
  onDone,
  actionFlight,
  profileSettling,
}: {
  animalId: number;
  onDone: () => void;
  actionFlight: ProfileActionFlight;
  profileSettling: boolean;
}) {
  const [open, setOpen] = useState(false);
  const mut = useRecordWeightApiAnimalsAnimalIdWeightPost();
  const t = useT();
  const vocabulary = farmVocabulary;
  const schema = useMemo(
    () => buildWeightSchema(t, vocabulary.facts.maxWeightKg),
    [t, vocabulary],
  );
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<WeightInput, unknown, WeightValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: WeightValues) {
    if (profileSettling) return;
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      try {
        await mut.mutateAsync({
          animalId,
          data: {
            date: emptyToNull(values.date),
            weight_kg: values.weight_kg,
            bcs: values.bcs ?? null,
            notes: emptyToNull(values.notes),
          },
        });
        if (!farmScope()) return;
        toast.success(t("animalDetail.toast.weightRecorded"));
        reset();
        setOpen(false);
        onDone();
      } catch (err) {
        if (!farmScope()) return;
        toast.error(
          err instanceof ApiError
            ? mapServerError(t, err.detail, err.status, err.code)
            : t("common.somethingWentWrong"),
        );
      }
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        // Cancelling discards the abandoned draft instead of resurfacing it
        // on the next open (L10).
        if (!nextOpen) reset();
        setOpen(nextOpen);
      }}
    >
      <Button
        size="sm"
        variant="outline"
        disabled={actionFlight.pending || profileSettling}
        onClick={() => setOpen(true)}
      >
        {t("animalDetail.actions.recordWeight")}
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("animalDetail.actions.recordWeight")}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3" noValidate>
          <fieldset
            disabled={isSubmitting || actionFlight.pending || profileSettling}
            className="contents"
          >
          <div className="space-y-1.5">
            <Label htmlFor="w_date">{t("animalDetail.field.dateDefaultsToday")}</Label>
            <Input
              id="w_date"
              type="date"
              max={farmToday()}
              aria-invalid={Boolean(errors.date) || undefined}
              aria-describedby={errors.date ? "weight-date-error" : undefined}
              {...register("date")}
            />
            {errors.date && <p id="weight-date-error" role="alert" className="text-sm text-destructive">{errors.date.message}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="w_kg">{t("animalDetail.weightDialog.weightLabel")}</Label>
            <Input
              id="w_kg"
              type="number"
              step="0.01"
              min="0"
              aria-invalid={Boolean(errors.weight_kg) || undefined}
              aria-describedby={errors.weight_kg ? "weight-kg-error" : undefined}
              {...register("weight_kg")}
            />
            {errors.weight_kg && (
              <p id="weight-kg-error" role="alert" className="text-sm text-destructive">{errors.weight_kg.message}</p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="w_bcs">{t("animalDetail.weightDialog.bcsLabel")}</Label>
            <Input
              id="w_bcs"
              type="number"
              min="1"
              max="5"
              aria-invalid={Boolean(errors.bcs) || undefined}
              aria-describedby={errors.bcs ? "weight-bcs-error" : undefined}
              {...register("bcs")}
            />
            {errors.bcs && <p id="weight-bcs-error" role="alert" className="text-sm text-destructive">{errors.bcs.message}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="w_notes">{t("animalDetail.field.notes")}</Label>
            <Textarea
              id="w_notes"
              rows={2}
              maxLength={255}
              aria-invalid={Boolean(errors.notes) || undefined}
              aria-describedby={errors.notes ? "weight-notes-error" : undefined}
              {...register("notes")}
            />
            {errors.notes && (
              <p id="weight-notes-error" role="alert" className="text-sm text-destructive">
                {errors.notes.message}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              type="submit"
              disabled={isSubmitting || actionFlight.pending || profileSettling}
            >
              {isSubmitting || actionFlight.pending ? t("animalDetail.saving") : t("common.save")}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Phenotype edit (coat colour / horns) through PATCH /api/animals/{id},
 * via the generated client (the old "generated client lags" comment was
 * stale — it is byte-current; P3, 2026-09-20 audit). Both fields always
 * travel in the payload: the contract clears stored values on explicit
 * null, which is exactly what the "Not recorded" option means. */
function EditPhenotypeDialog({
  animal,
  onDone,
  actionFlight,
  profileSettling,
}: {
  animal: AnimalProfileOut["animal"];
  onDone: () => void;
  actionFlight: ProfileActionFlight;
  profileSettling: boolean;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const mut = useUpdateAnimalApiAnimalsAnimalIdPatch();
  const [open, setOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [coatColor, setCoatColor] = useState<string>(animal.coat_color ?? "");
  const [horned, setHorned] = useState<string>(
    animal.horned === true ? "yes" : animal.horned === false ? "no" : "",
  );
  const coatColorItems: Record<string, string> = {
    "": t("animals.notRecorded"),
    black: t("animals.coatColor.black"),
    black_patched: t("animals.coatColor.black_patched"),
    brown: t("animals.coatColor.brown"),
    white: t("animals.coatColor.white"),
    spotted: t("animals.coatColor.spotted"),
  };
  const hornedItems: Record<string, string> = {
    "": t("animals.notRecorded"),
    yes: t("common.yes"),
    no: t("common.no"),
  };

  function openDialog() {
    setSaveError(null);
    setCoatColor(animal.coat_color ?? "");
    setHorned(animal.horned === true ? "yes" : animal.horned === false ? "no" : "");
    setOpen(true);
  }

  async function save() {
    if (profileSettling) return;
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      setSaveError(null);
      try {
        await mut.mutateAsync({
          animalId: animal.id,
          data: {
            coat_color: (coatColor || null) as AnimalProfileOut["animal"]["coat_color"],
            horned: horned === "" ? null : horned === "yes",
          },
        });
        if (!farmScope()) return;
        toast.success(t("animals.phenotypeSaved"));
        setOpen(false);
        invalidateFarmData(queryClient);
        onDone();
      } catch (err) {
        if (!farmScope()) return;
        setSaveError(
          err instanceof ApiError
            ? mapServerError(t, err.detail, err.status, err.code)
            : t("common.somethingWentWrong"),
        );
      }
    });
  }

  return (
    <>
      <Button variant="outline" size="sm" disabled={profileSettling} onClick={openDialog}>
        {t("animals.editPhenotype")}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && actionFlight.pending) return;
          setOpen(nextOpen);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("animals.editPhenotype")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="edit-coat-color">{t("animals.coatColor")}</Label>
              <Select value={coatColor} onValueChange={setCoatColor} items={coatColorItems}>
                <SelectTrigger id="edit-coat-color" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(coatColorItems).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edit-horned">{t("animals.horned")}</Label>
              <Select value={horned} onValueChange={setHorned} items={hornedItems}>
                <SelectTrigger id="edit-horned" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(hornedItems).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {saveError && (
              <p role="alert" className="text-sm text-destructive">
                {saveError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={actionFlight.pending}
              onClick={() => setOpen(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button type="button" disabled={actionFlight.pending} onClick={() => void save()}>
              {actionFlight.pending ? t("common.loading") : t("common.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function buildMoveSchema(t: TFn) {
  return z.object({
    to_bucket: z.enum(BUCKETS as [string, ...string[]]),
    reason: z.string().max(255, t("animalDetail.validation.reasonTooLong")).optional(),
  });
}
type MoveValues = z.infer<ReturnType<typeof buildMoveSchema>>;

function MoveBucketDialog({
  animalId,
  currentBucket,
  sex,
  onDone,
  actionFlight,
  profileSettling,
}: {
  animalId: number;
  currentBucket: string;
  sex: string;
  onDone: () => void;
  actionFlight: ProfileActionFlight;
  profileSettling: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { language } = useLanguage();
  const t = useT();
  const mut = useMoveBucketApiAnimalsAnimalIdMovePost();
  const schema = useMemo(() => buildMoveSchema(t), [t]);
  const {
    handleSubmit,
    control,
    register,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<MoveValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: MoveValues) {
    if (profileSettling) return;
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      try {
        await mut.mutateAsync({
          animalId,
          data: {
            to_bucket: values.to_bucket as MoveInToBucket,
            reason: emptyToNull(values.reason),
          },
        });
        if (!farmScope()) return;
        toast.success(t("animalDetail.toast.moved"));
        reset();
        setOpen(false);
        onDone();
      } catch (err) {
        if (!farmScope()) return;
        toast.error(
          err instanceof ApiError
            ? mapServerError(t, err.detail, err.status, err.code)
            : t("common.somethingWentWrong"),
        );
      }
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) reset();
        setOpen(nextOpen);
      }}
    >
      <Button
        size="sm"
        variant="outline"
        disabled={actionFlight.pending || profileSettling}
        onClick={() => setOpen(true)}
      >
        {t("animalDetail.actions.moveBucket")}
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("animalDetail.actions.moveBucket")}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3" noValidate>
          <fieldset
            disabled={isSubmitting || actionFlight.pending || profileSettling}
            className="contents"
          >
          <div className="space-y-1.5">
            <Label htmlFor="move-to-bucket">{t("animalDetail.moveDialog.toBucketLabel")}</Label>
            <Controller
              control={control}
              name="to_bucket"
              render={({ field }) => (
                <Select
                  value={field.value ?? ""}
                  onValueChange={field.onChange}
                  items={Object.fromEntries(
                    BUCKETS.map((b) => [b, enumLabel("bucket", b, language)]),
                  )}
                >
                  <SelectTrigger
                    id="move-to-bucket"
                    className="w-full"
                    aria-invalid={Boolean(errors.to_bucket) || undefined}
                    aria-describedby={errors.to_bucket ? "move-to-bucket-error" : undefined}
                  >
                    <SelectValue placeholder={t("animalDetail.moveDialog.chooseBucket")} />
                  </SelectTrigger>
                  <SelectContent>
                    {BUCKETS.filter(
                      (b) => b !== currentBucket && bucketAllowsSex(b, sex),
                    ).map((b) => (
                      <SelectItem key={b} value={b}>
                        {enumLabel("bucket", b, language)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              />
            {errors.to_bucket && (
              <p id="move-to-bucket-error" role="alert" className="text-sm text-destructive">
                {errors.to_bucket.message}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="m_reason">{t("animalDetail.field.reason")}</Label>
            <Textarea
              id="m_reason"
              rows={2}
              maxLength={255}
              aria-invalid={Boolean(errors.reason) || undefined}
              aria-describedby={errors.reason ? "move-reason-error" : undefined}
              {...register("reason")}
            />
            {errors.reason && (
              <p id="move-reason-error" role="alert" className="text-sm text-destructive">
                {errors.reason.message}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              type="submit"
              disabled={isSubmitting || actionFlight.pending || profileSettling}
            >
              {isSubmitting || actionFlight.pending ? t("animalDetail.moveDialog.submitting") : t("animalDetail.moveDialog.submit")}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Messages resolve through the i18n catalog, so the schema is a factory of
 * the caller's `t`; the mounted dialog rebuilds it on every language change
 * (health.buildEventSchema precedent). The money-floor message stays the
 * animalDetail.validation.moneyMin floor message the other money forms pin. */
function buildStatusSchema(t: TFn) {
  return z
  .object({
    new_status: z.enum([
      StatusChangeInNewStatus.SOLD,
      StatusChangeInNewStatus.DEAD,
      StatusChangeInNewStatus.CULLED,
    ]),
    date: z.string().optional(),
    sale_price: optNum(
      z
        .number()
        .nonnegative()
        .max(1_000_000_000, t("animalDetail.validation.salePriceTooLarge"))
        .refine(isPersistableNonnegativeMoney, t("animalDetail.validation.moneyMin")),
    ),
    // Operational sale facts (WeightKgFloat / MoneyFloat on the wire): the
    // rate is only meaningful against a weight, enforced in the refine below.
    sale_weight_kg: optNum(
      z
        .number()
        .positive(t("animalDetail.validation.weightPositive"))
        .max(1_000, t("animalDetail.validation.weightTooLarge")),
    ),
    sale_price_per_kg: optNum(
      z
        .number()
        .positive(t("animalDetail.validation.pricePerKgPositive"))
        .max(1_000_000_000, t("animalDetail.validation.pricePerKgTooLarge"))
        .refine(isPersistableNonnegativeMoney, t("animalDetail.validation.moneyMin")),
    ),
    buyer_name: z.string().max(120, t("animalDetail.validation.buyerNameTooLong")).optional(),
    notes: z.string().max(255, t("animalDetail.validation.notesTooLong")).optional(),
    mortality_cause: z
      .string()
      .max(120, t("animalDetail.validation.mortalityCauseTooLong"))
      .optional(),
    mortality_cause_code: z.enum(MORTALITY_CAUSE_CODES as [string, ...string[]]).optional(),
    disposal_method: z.enum(DISPOSAL_METHODS as [string, ...string[]]).optional(),
    mortality_reported_at: z.string().optional(),
    necropsy_done: z.boolean(),
    necropsy_findings: z
      .string()
      .max(4_000, t("animalDetail.validation.necropsyFindingsTooLong"))
      .optional(),
    suspected_scheduled_disease: z.boolean(),
    suspected_disease: z
      .string()
      .max(120, t("animalDetail.validation.suspectedDiseaseTooLong"))
      // Keep the resolver output total. Besides simplifying the statutory
      // validation below, this prevents an omitted programmatic value from
      // ever reaching a string operation as `undefined`.
      .optional()
      .default(""),
    authority_notified_at: z.string().optional(),
    // DOB-less male sale remedy: the backend stamps this estimate onto the
    // animal so the meat-sale age floor can be verified (it 422s otherwise).
    estimated_dob: z.string().optional(),
  })
  .superRefine((values, context) => {
    const statusDate = values.date || farmToday();
    for (const field of [
      "date",
      "mortality_reported_at",
      "authority_notified_at",
      "estimated_dob",
    ] as const) {
      if (values[field] && values[field] > farmToday()) {
        context.addIssue({ code: "custom", path: [field], message: t("animalDetail.validation.dateFuture") });
      }
    }
    if (
      values.new_status !== StatusChangeInNewStatus.SOLD &&
      values.estimated_dob !== undefined &&
      values.estimated_dob !== ""
    ) {
      context.addIssue({
        code: "custom",
        path: ["estimated_dob"],
        message: t("animalDetail.validation.estimatedDobSalesOnly"),
      });
    }
    if (
      values.new_status === StatusChangeInNewStatus.DEAD &&
      values.mortality_reported_at &&
      values.mortality_reported_at < statusDate
    ) {
      context.addIssue({
        code: "custom",
        path: ["mortality_reported_at"],
        message: t("animalDetail.validation.reportBeforeDeath"),
      });
    }
    // Mirrors StatusChangeIn._death_escalation_fields_are_coherent: the rate
    // without its weight would be rejected as a server 422.
    if (values.sale_price_per_kg !== undefined && values.sale_weight_kg === undefined) {
      context.addIssue({
        code: "custom",
        path: ["sale_price_per_kg"],
        message: t("animalDetail.validation.weightBeforeRate"),
      });
    }
    if (values.necropsy_findings && values.necropsy_findings.trim() && !values.necropsy_done) {
      context.addIssue({
        code: "custom",
        path: ["necropsy_findings"],
        message: t("animalDetail.validation.necropsyFindingsNeedDone"),
      });
    }
    if (
      values.new_status === StatusChangeInNewStatus.DEAD &&
      values.suspected_scheduled_disease &&
      !values.suspected_disease.trim()
    ) {
      context.addIssue({
        code: "custom",
        path: ["suspected_disease"],
        message: t("animalDetail.validation.nameSuspectedDisease"),
      });
    }
  });
}
type StatusInput = z.input<ReturnType<typeof buildStatusSchema>>;
type StatusValues = z.output<ReturnType<typeof buildStatusSchema>>;

/** Mirrors the API's SALE_CAPABLE_STATUSES: both statuses persist sale_price
 * and post an ANIMAL_SALE transaction, so both must also render it. */
const SALE_CAPABLE_STATUSES: readonly string[] = [
  StatusChangeInNewStatus.SOLD,
  StatusChangeInNewStatus.CULLED,
];

function statusCanRecordSale(status: StatusValues["new_status"]): boolean {
  return SALE_CAPABLE_STATUSES.includes(status);
}

function StatusDialog({
  animalId,
  sex,
  hasDob,
  onDone,
  actionFlight,
  profileSettling,
}: {
  animalId: number;
  sex: string;
  hasDob: boolean;
  onDone: () => void;
  actionFlight: ProfileActionFlight;
  profileSettling: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { language } = useLanguage();
  const t = useT();
  const mut = useChangeStatusApiAnimalsAnimalIdStatusPost();
  // A male with neither a recorded nor an estimated birth date cannot pass
  // the backend's meat-sale age floor — the sale payload must carry the
  // estimate (the only field-editable remedy; DOB is not editable after
  // creation). Mirror the requirement client-side instead of 422ing.
  const needsEstimatedDob = sex === "M" && !hasDob;
  const resolverSchema = useMemo(
    () =>
      buildStatusSchema(t).superRefine((values, context) => {
        if (
          needsEstimatedDob &&
          values.new_status === StatusChangeInNewStatus.SOLD &&
          !values.estimated_dob
        ) {
          context.addIssue({
            code: "custom",
            path: ["estimated_dob"],
            message: t("animalDetail.validation.maleNeedsEstimatedDob"),
          });
        }
      }),
    [t, needsEstimatedDob],
  );
  const {
    register,
    handleSubmit,
    control,
    setValue,
    unregister,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<StatusInput, unknown, StatusValues>({
    resolver: zodResolver(resolverSchema),
    defaultValues: {
      new_status: StatusChangeInNewStatus.SOLD,
      suspected_scheduled_disease: false,
      necropsy_done: false,
    },
  });
  const newStatus = useWatch({ control, name: "new_status" });
  const suspectedScheduledDisease = useWatch({
    control,
    name: "suspected_scheduled_disease",
  });
  const necropsyDone = useWatch({ control, name: "necropsy_done" });
  const saleWeight = useWatch({ control, name: "sale_weight_kg" });
  /** value → label map for the root `items` prop, in the active language:
   * without it, Base UI's Select.Value renders the raw status code in the
   * closed trigger. */
  const statusItems: Record<string, string> = Object.fromEntries(
    [StatusChangeInNewStatus.SOLD, StatusChangeInNewStatus.DEAD, StatusChangeInNewStatus.CULLED].map(
      (status) => [status, enumLabel("status", status, language)],
    ),
  );

  async function onSubmit(values: StatusValues) {
    if (profileSettling) return;
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      try {
        await mut.mutateAsync({
          animalId,
          data: {
            new_status: values.new_status,
            date: emptyToNull(values.date),
            sale_price: statusCanRecordSale(values.new_status)
              ? (values.sale_price ?? null)
              : null,
            // Weight/rate are SOLD-only facts (_death_escalation_fields checks).
            sale_weight_kg:
              values.new_status === StatusChangeInNewStatus.SOLD
                ? (values.sale_weight_kg ?? null)
                : null,
            sale_price_per_kg:
              values.new_status === StatusChangeInNewStatus.SOLD
                ? (values.sale_price_per_kg ?? null)
                : null,
            // The DOB-less male remedy: only the backend accepts it, and
            // only for SOLD (StatusChangeIn rejects it on other exits).
            estimated_dob:
              values.new_status === StatusChangeInNewStatus.SOLD && needsEstimatedDob
                ? emptyToNull(values.estimated_dob)
                : null,
            buyer_name: statusCanRecordSale(values.new_status)
              ? emptyToNull(values.buyer_name)
              : null,
            notes: emptyToNull(values.notes),
            mortality_cause:
              values.new_status === StatusChangeInNewStatus.DEAD
                ? emptyToNull(values.mortality_cause)
                : null,
            mortality_cause_code:
              values.new_status === StatusChangeInNewStatus.DEAD
                ? ((values.mortality_cause_code ?? null) as StatusChangeInMortalityCauseCode)
                : null,
            disposal_method:
              values.new_status === StatusChangeInNewStatus.DEAD
                ? ((values.disposal_method ?? null) as StatusChangeInDisposalMethod)
                : null,
            mortality_reported_at:
              values.new_status === StatusChangeInNewStatus.DEAD
                ? emptyToNull(values.mortality_reported_at)
                : null,
            necropsy_done:
              values.new_status === StatusChangeInNewStatus.DEAD && values.necropsy_done,
            necropsy_findings:
              values.new_status === StatusChangeInNewStatus.DEAD && values.necropsy_done
                ? emptyToNull(values.necropsy_findings)
                : null,
            suspected_scheduled_disease:
              values.new_status === StatusChangeInNewStatus.DEAD &&
              values.suspected_scheduled_disease,
            suspected_disease:
              values.new_status === StatusChangeInNewStatus.DEAD &&
              values.suspected_scheduled_disease
                ? emptyToNull(values.suspected_disease)
                : null,
            authority_notified_at:
              values.new_status === StatusChangeInNewStatus.DEAD &&
              values.suspected_scheduled_disease
                ? emptyToNull(values.authority_notified_at)
                : null,
          },
        });
        if (!farmScope()) return;
        toast.success(
          t("animalDetail.toast.statusChanged", {
            status: enumLabel("status", values.new_status, language),
          }),
        );
        reset();
        setOpen(false);
        onDone();
      } catch (err) {
        if (!farmScope()) return;
        toast.error(
          err instanceof ApiError
            ? mapServerError(t, err.detail, err.status, err.code)
            : t("common.somethingWentWrong"),
        );
      }
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) reset();
        setOpen(nextOpen);
      }}
    >
      <Button
        size="sm"
        variant="outline"
        disabled={actionFlight.pending || profileSettling}
        onClick={() => setOpen(true)}
      >
        {t("animalDetail.actions.changeStatus")}
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("animalDetail.actions.changeStatus")}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3" noValidate>
          <fieldset
            disabled={isSubmitting || actionFlight.pending || profileSettling}
            className="contents"
          >
          <div className="space-y-1.5">
            <Label htmlFor="animal-new-status">{t("animalDetail.statusDialog.newStatusLabel")}</Label>
            <Controller
              control={control}
              name="new_status"
              render={({ field }) => (
                <Select
                  value={field.value}
                  items={statusItems}
                  onValueChange={(value) => {
                    const nextStatus = value as StatusValues["new_status"];
                    field.onChange(nextStatus);
                    if (!statusCanRecordSale(nextStatus)) {
                      unregister(["sale_price", "buyer_name"]);
                    }
                    // Weight, rate and the DOB-less-male estimate are
                    // SOLD-only facts: a cull records a price, never a
                    // realized ₹/kg.
                    if (nextStatus !== StatusChangeInNewStatus.SOLD) {
                      unregister(["sale_weight_kg", "sale_price_per_kg", "estimated_dob"]);
                    }
                    if (nextStatus !== StatusChangeInNewStatus.DEAD) {
                      setValue("suspected_scheduled_disease", false);
                      setValue("necropsy_done", false);
                      unregister([
                        "mortality_cause",
                        "mortality_cause_code",
                        "disposal_method",
                        "mortality_reported_at",
                        "necropsy_findings",
                        "suspected_disease",
                        "authority_notified_at",
                      ]);
                    }
                  }}
                >
                  <SelectTrigger id="animal-new-status" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[StatusChangeInNewStatus.SOLD, StatusChangeInNewStatus.DEAD, StatusChangeInNewStatus.CULLED].map(
                      (status) => (
                        <SelectItem key={status} value={status}>
                          {enumLabel("status", status, language)}
                        </SelectItem>
                      ),
                    )}
                  </SelectContent>
                </Select>
              )}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="s_date">{t("animalDetail.field.dateDefaultsToday")}</Label>
            <Input
              id="s_date"
              type="date"
              max={farmToday()}
              aria-invalid={Boolean(errors.date) || undefined}
              aria-describedby={errors.date ? "status-date-error" : undefined}
              {...register("date")}
            />
            {errors.date && <p id="status-date-error" role="alert" className="text-sm text-destructive">{errors.date.message}</p>}
          </div>
          {statusCanRecordSale(newStatus) && (
            <>
              {newStatus === StatusChangeInNewStatus.SOLD && (
                <div className="space-y-1.5">
                  <Label htmlFor="s_weight">{t("animalDetail.statusDialog.saleWeightLabel")}</Label>
                  <Input
                    id="s_weight"
                    type="number"
                    step="0.1"
                    min="0"
                    aria-invalid={Boolean(errors.sale_weight_kg) || undefined}
                    aria-describedby={errors.sale_weight_kg ? "status-sale-weight-error" : undefined}
                    {...register("sale_weight_kg")}
                  />
                  {errors.sale_weight_kg && (
                    <p id="status-sale-weight-error" role="alert" className="text-sm text-destructive">
                      {errors.sale_weight_kg.message}
                    </p>
                  )}
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="s_price">{t("animalDetail.statusDialog.salePriceLabel")}</Label>
                <Input
                  id="s_price"
                  type="number"
                  step="0.01"
                  min="0"
                  aria-invalid={Boolean(errors.sale_price) || undefined}
                  aria-describedby={errors.sale_price ? "status-sale-price-error" : undefined}
                  {...register("sale_price")}
                />
                {errors.sale_price && (
                  <p id="status-sale-price-error" role="alert" className="text-sm text-destructive">{errors.sale_price.message}</p>
                )}
              </div>
              {newStatus === StatusChangeInNewStatus.SOLD && (
                <div className="space-y-1.5">
                  <Label htmlFor="s_price_per_kg">{t("animalDetail.statusDialog.pricePerKgLabel")}</Label>
                  <Input
                    id="s_price_per_kg"
                    type="number"
                    step="0.01"
                    min="0"
                    // L-24 (2026-09-17 audit): react-hook-form holds "" once the
                    // weight box is touched, so the old null/undefined guard
                    // re-enabled the rate field for an unusable weight. Gate on
                    // the same positive-number bar the schema applies.
                    disabled={!saleWeight || Number(saleWeight) <= 0}
                    aria-invalid={Boolean(errors.sale_price_per_kg) || undefined}
                    aria-describedby={
                      errors.sale_price_per_kg ? "status-price-per-kg-error" : "status-price-per-kg-hint"
                    }
                    {...register("sale_price_per_kg")}
                  />
                  <p id="status-price-per-kg-hint" className="text-xs text-muted-foreground">
                    {t("animalDetail.statusDialog.rateHint")}
                  </p>
                  {errors.sale_price_per_kg && (
                    <p id="status-price-per-kg-error" role="alert" className="text-sm text-destructive">
                      {errors.sale_price_per_kg.message}
                    </p>
                  )}
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="s_buyer">{t("animalDetail.statusDialog.buyerNameLabel")}</Label>
                <Input
                  id="s_buyer"
                  maxLength={120}
                  aria-invalid={Boolean(errors.buyer_name) || undefined}
                  aria-describedby={errors.buyer_name ? "status-buyer-error" : undefined}
                  {...register("buyer_name")}
                />
                {errors.buyer_name && (
                  <p id="status-buyer-error" role="alert" className="text-sm text-destructive">
                    {errors.buyer_name.message}
                  </p>
                )}
              </div>
              {newStatus === StatusChangeInNewStatus.SOLD && needsEstimatedDob && (
                <div className="space-y-1.5">
                  <Label htmlFor="s_estimated_dob">{t("animalDetail.statusDialog.estimatedDobLabel")}</Label>
                  <Input
                    id="s_estimated_dob"
                    type="date"
                    max={farmToday()}
                    aria-invalid={Boolean(errors.estimated_dob) || undefined}
                    aria-describedby={
                      errors.estimated_dob ? "status-estimated-dob-error" : "status-estimated-dob-hint"
                    }
                    {...register("estimated_dob")}
                  />
                  <p id="status-estimated-dob-hint" className="text-xs text-muted-foreground">
                    {t("animalDetail.statusDialog.estimatedDobHint")}
                  </p>
                  {errors.estimated_dob && (
                    <p id="status-estimated-dob-error" role="alert" className="text-sm text-destructive">
                      {errors.estimated_dob.message}
                    </p>
                  )}
                </div>
              )}
            </>
          )}
          {newStatus === StatusChangeInNewStatus.DEAD && (
            <fieldset className="space-y-3 rounded-lg border p-3">
              <legend className="px-1 text-sm font-medium">{t("animalDetail.statusDialog.mortalityLegend")}</legend>
              <div className="space-y-1.5">
                <Label htmlFor="mortality-cause">{t("animalDetail.field.mortalityCause")}</Label>
                <Input
                  id="mortality-cause"
                  maxLength={120}
                  placeholder={t("animalDetail.statusDialog.mortalityCausePlaceholder")}
                  aria-invalid={Boolean(errors.mortality_cause) || undefined}
                  aria-describedby={
                    errors.mortality_cause ? "mortality-cause-error" : undefined
                  }
                  {...register("mortality_cause")}
                />
                {errors.mortality_cause && (
                  <p
                    id="mortality-cause-error"
                    role="alert"
                    className="text-sm text-destructive"
                  >
                    {errors.mortality_cause.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mortality-cause-code">{t("animalDetail.statusDialog.causeCodedLabel")}</Label>
                <Controller
                  control={control}
                  name="mortality_cause_code"
                  render={({ field }) => (
                    <Select
                      value={field.value ?? ""}
                      onValueChange={(value) =>
                        field.onChange(value === "" ? undefined : value)
                      }
                      items={mortalityCauseCodeItems(language, t("animalDetail.statusDialog.notCoded"))}
                    >
                      <SelectTrigger
                        id="mortality-cause-code"
                        className="w-full"
                        aria-invalid={Boolean(errors.mortality_cause_code) || undefined}
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="">{t("animalDetail.statusDialog.notCoded")}</SelectItem>
                        {MORTALITY_CAUSE_CODES.map((code) => (
                          <SelectItem key={code} value={code}>
                            {enumLabel("mortalityCause", code, language)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
                <p className="text-xs text-muted-foreground">
                  {t("animalDetail.statusDialog.codedCauseHint")}
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="disposal-method">{t("animalDetail.field.disposalMethod")}</Label>
                <Controller
                  control={control}
                  name="disposal_method"
                  render={({ field }) => (
                    <Select
                      value={field.value ?? ""}
                      onValueChange={(value) =>
                        field.onChange(value === "" ? undefined : value)
                      }
                      items={disposalMethodItems(language, t("animalDetail.statusDialog.disposalNotRecorded"))}
                    >
                      <SelectTrigger
                        id="disposal-method"
                        className="w-full"
                        aria-invalid={Boolean(errors.disposal_method) || undefined}
                        aria-describedby={
                          errors.disposal_method ? "disposal-method-error" : undefined
                        }
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="">{t("animalDetail.statusDialog.disposalNotRecorded")}</SelectItem>
                        {DISPOSAL_METHODS.map((method) => (
                          <SelectItem key={method} value={method}>
                            {enumLabel("disposalMethod", method, language)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
                {errors.disposal_method && (
                  <p id="disposal-method-error" role="alert" className="text-sm text-destructive">
                    {errors.disposal_method.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mortality-reported-at">{t("animalDetail.statusDialog.mortalityReportedDate")}</Label>
                <Input
                  id="mortality-reported-at"
                  type="date"
                  max={farmToday()}
                  aria-invalid={Boolean(errors.mortality_reported_at) || undefined}
                  aria-describedby={errors.mortality_reported_at ? "mortality-reported-error" : undefined}
                  {...register("mortality_reported_at")}
                />
                {errors.mortality_reported_at && (
                  <p id="mortality-reported-error" role="alert" className="text-sm text-destructive">
                    {errors.mortality_reported_at.message}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Controller
                  control={control}
                  name="necropsy_done"
                  render={({ field }) => (
                    <Checkbox
                      id="necropsy-done"
                      checked={field.value}
                      onCheckedChange={(checked) => {
                        const selected = checked === true;
                        if (!selected) {
                          unregister(["necropsy_findings"]);
                        }
                        field.onChange(selected);
                      }}
                    />
                  )}
                />
                <Label htmlFor="necropsy-done" className="font-normal">
                  {t("animalDetail.field.necropsyPerformed")}
                </Label>
              </div>
              {necropsyDone && (
                <div className="space-y-1.5">
                  <Label htmlFor="necropsy-findings">{t("animalDetail.field.necropsyFindings")}</Label>
                  <Textarea
                    id="necropsy-findings"
                    rows={3}
                    maxLength={4_000}
                    aria-invalid={Boolean(errors.necropsy_findings) || undefined}
                    aria-describedby={
                      errors.necropsy_findings ? "necropsy-findings-error" : undefined
                    }
                    {...register("necropsy_findings")}
                  />
                  {errors.necropsy_findings && (
                    <p
                      id="necropsy-findings-error"
                      role="alert"
                      className="text-sm text-destructive"
                    >
                      {errors.necropsy_findings.message}
                    </p>
                  )}
                </div>
              )}
              <div className="flex items-center gap-2">
                <Controller
                  control={control}
                  name="suspected_scheduled_disease"
                  render={({ field }) => (
                    <Checkbox
                      id="mortality-scheduled-disease"
                      checked={field.value}
                      onCheckedChange={(checked) => {
                        const selected = checked === true;
                        if (!selected) {
                          unregister(["suspected_disease", "authority_notified_at"]);
                        }
                        field.onChange(selected);
                      }}
                    />
                  )}
                />
                <Label htmlFor="mortality-scheduled-disease" className="font-normal">
                  {t("animalDetail.statusDialog.scheduledDiseaseCheckbox")}
                </Label>
              </div>
              {suspectedScheduledDisease && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="suspected-disease">{t("animalDetail.field.suspectedDisease")} *</Label>
                    <Input
                      id="suspected-disease"
                      maxLength={120}
                      aria-invalid={Boolean(errors.suspected_disease) || undefined}
                      aria-describedby={errors.suspected_disease ? "suspected-disease-error" : undefined}
                      {...register("suspected_disease")}
                    />
                    {errors.suspected_disease && (
                      <p id="suspected-disease-error" role="alert" className="text-sm text-destructive">
                        {errors.suspected_disease.message}
                      </p>
                    )}
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="mortality-authority-notified">{t("animalDetail.statusDialog.authorityNotifiedDate")}</Label>
                    <Input
                      id="mortality-authority-notified"
                      type="date"
                      max={farmToday()}
                      aria-invalid={Boolean(errors.authority_notified_at) || undefined}
                      aria-describedby={errors.authority_notified_at ? "mortality-authority-error" : undefined}
                      {...register("authority_notified_at")}
                    />
                    {errors.authority_notified_at && (
                      <p id="mortality-authority-error" role="alert" className="text-sm text-destructive">
                        {errors.authority_notified_at.message}
                      </p>
                    )}
                  </div>
                </>
              )}
            </fieldset>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="s_notes">{t("animalDetail.field.notes")}</Label>
            <Textarea
              id="s_notes"
              rows={2}
              maxLength={255}
              aria-invalid={Boolean(errors.notes) || undefined}
              aria-describedby={errors.notes ? "status-notes-error" : undefined}
              {...register("notes")}
            />
            {errors.notes && (
              <p id="status-notes-error" role="alert" className="text-sm text-destructive">
                {errors.notes.message}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              type="submit"
              variant="destructive"
              disabled={isSubmitting || actionFlight.pending || profileSettling}
            >
              {isSubmitting || actionFlight.pending ? t("animalDetail.saving") : t("animalDetail.statusDialog.confirm")}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ClearRestrictionDialog({
  animalId,
  restrictionVersion,
  onDone,
  actionFlight,
  profileSettling,
}: {
  animalId: number;
  restrictionVersion: number;
  onDone: () => void;
  actionFlight: ProfileActionFlight;
  profileSettling: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [conflictedVersion, setConflictedVersion] = useState<number | null>(null);
  const mutation = useClearMovementRestrictionApiHealthRestrictionsAnimalIdClearPost();
  const t = useT();
  const awaitingEpisodeRefresh = conflictedVersion === restrictionVersion;

  async function clearRestriction() {
    if (actionFlight.pending || profileSettling || awaitingEpisodeRefresh) return;
    const clearanceReference = reference.trim();
    if (!clearanceReference) {
      setError(t("animalDetail.clearance.referenceRequired"));
      return;
    }
    setError(null);
    await actionFlight.run(async () => {
      const farmScope = captureFarmScope();
      try {
        await mutation.mutateAsync({
          animalId,
          data: {
            clearance_reference: clearanceReference,
            expected_restriction_version: restrictionVersion,
          },
        });
        if (!farmScope()) return;
        toast.success(t("animalDetail.toast.restrictionCleared"));
        setReference("");
        setConflictedVersion(null);
        setOpen(false);
        onDone();
      } catch (caught) {
        if (!farmScope()) return;
        if (caught instanceof ApiError && caught.status === 409) {
          setConflictedVersion(restrictionVersion);
          setError(`${mapServerError(t, caught.detail, caught.status, caught.code)} ${t("animalDetail.clearance.refreshingEpisode")}`);
          onDone();
        } else {
          setError(
            caught instanceof ApiError
              ? mapServerError(t, caught.detail, caught.status, caught.code)
              : t("animalDetail.clearance.failed"),
          );
        }
      }
    });
  }

  function openDialog() {
    // L-25 (2026-09-17 audit): a failed attempt left `error` and
    // `conflictedVersion` set after close, so reopening showed the previous
    // failure's alert (and its refresh lock) against a fresh attempt. Reset
    // the transient state like EditPhenotypeDialog's openDialog does; the
    // reference is a one-time certificate number, so it does not carry over.
    setError(null);
    setConflictedVersion(null);
    setReference("");
    setOpen(true);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && actionFlight.pending) return;
        setOpen(nextOpen);
      }}
    >
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={actionFlight.pending || profileSettling}
        onClick={openDialog}
      >
        {t("animalDetail.actions.recordClearance")}
      </Button>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("animalDetail.clearance.title")}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t("animalDetail.clearance.description")}
        </p>
        <div className="space-y-1.5">
          <Label htmlFor={`clearance-reference-${animalId}`}>{t("animalDetail.clearance.referenceLabel")}</Label>
          <Input
            id={`clearance-reference-${animalId}`}
            value={reference}
            disabled={actionFlight.pending || profileSettling || awaitingEpisodeRefresh}
            maxLength={255}
            placeholder={t("animalDetail.clearance.referencePlaceholder")}
            aria-invalid={Boolean(error) || undefined}
            aria-describedby={error ? `clearance-reference-${animalId}-error` : undefined}
            onChange={(event) => setReference(event.target.value)}
          />
          {error && (
            <p
              id={`clearance-reference-${animalId}-error`}
              role="alert"
              className="text-sm text-destructive"
            >
              {error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={actionFlight.pending}
            onClick={() => setOpen(false)}
          >
            {t("common.cancel")}
          </Button>
          {awaitingEpisodeRefresh && (
            <Button type="button" variant="outline" onClick={onDone}>
              {t("animalDetail.clearance.refreshEpisode")}
            </Button>
          )}
          <Button
            type="button"
            disabled={
              !reference.trim() ||
              actionFlight.pending ||
              profileSettling ||
              awaitingEpisodeRefresh
            }
            onClick={() => void clearRestriction()}
          >
            {mutation.isPending
              ? t("animalDetail.clearance.recording")
              : awaitingEpisodeRefresh
                ? t("animalDetail.clearance.waiting")
                : error
                  ? t("animalDetail.clearance.retry")
                  : t("animalDetail.clearance.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Lifetime money in/out for one animal, from the finance register's
 *  per-animal endpoint. Feed is farm-level on purpose — the note row carries
 *  that caveat so the zeros are never read as "feed was free". */
function LifetimePnlCard({
  animalId,
  canViewFinance,
}: {
  animalId: number;
  canViewFinance: boolean;
}) {
  const t = useT();
  const query = useAnimalLifetimePnlApiFinanceAnimalsAnimalIdLifetimePnlGet(animalId, {
    query: { enabled: canViewFinance },
  });
  const pnl = query.data?.status === 200 ? query.data.data : undefined;

  return (
    <DataTableCard
      title={t("animalDetail.pnl.title")}
      description={t("animalDetail.pnl.description")}
      ariaBusy={query.isFetching}
    >
      {query.isLoading ? (
        <InlineLoading>{t("animalDetail.pnl.loading")}</InlineLoading>
      ) : query.isError ? (
        <div role="alert" className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError
              ? mapServerError(t, query.error.detail, query.error.status, query.error.code)
              : t("animalDetail.pnl.loadFailed")}
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void query.refetch()}
          >
            {t("animalDetail.pnl.retry")}
          </Button>
        </div>
      ) : pnl ? (
        <>
          <Table>
            <TableHeader className="sr-only">
              <TableRow>
                <th scope="col">{t("animalDetail.pnl.line")}</th>
                <th scope="col">{t("animalDetail.pnl.amount")}</th>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[
                { label: t("animalDetail.pnl.purchaseCost"), value: pnl.purchase_cost },
                { label: t("animalDetail.pnl.healthCost"), value: pnl.health_cost },
                { label: t("animalDetail.pnl.insurancePremiums"), value: pnl.insurance_premiums },
                { label: t("animalDetail.pnl.saleIncome"), value: pnl.sale_income },
              ].map((row) => (
                <TableRow key={row.label}>
                  <TableCell>{row.label}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(row.value)}
                  </TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell className="font-medium">{t("animalDetail.pnl.net")}</TableCell>
                <TableCell
                  className={cn(
                    "text-right tabular-nums font-medium",
                    pnl.net < 0 ? "text-destructive" : "text-success",
                  )}
                >
                  {formatMoney(pnl.net)}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
          <p className="mt-2 text-xs text-muted-foreground">{pnl.note}</p>
        </>
      ) : null}
    </DataTableCard>
  );
}

function ProfileBody({
  profile,
  refresh,
  profileSettling,
  historySettling,
  backHref,
  backLabel,
  onKidsOffsetChange,
  onWeightsOffsetChange,
  onMovesOffsetChange,
  onHealthEventsOffsetChange,
  onBreedingsOffsetChange,
}: {
  profile: AnimalProfileOut;
  refresh: () => void;
  /** The animal snapshot is being refreshed; lifecycle writes must wait for
   *  the authoritative status/bucket/restriction version. */
  profileSettling: boolean;
  /** The combined bounded-history query is showing its previous offsets. */
  historySettling: boolean;
  backHref: string;
  backLabel: string;
  onKidsOffsetChange: (offset: number) => void;
  onWeightsOffsetChange: (offset: number) => void;
  onMovesOffsetChange: (offset: number) => void;
  onHealthEventsOffsetChange: (offset: number) => void;
  onBreedingsOffsetChange: (offset: number) => void;
}) {
  const { can } = usePermissions();
  const { language } = useLanguage();
  const t = useT();
  const a = profile.animal;
  const vocabulary = farmVocabulary;
  // Farm-vocabulary nouns with a Telugu rendering for the localized surface
  // (simulation.token.* precedent): English reads the species vocabulary.
  const kidsNounCap =
    language === "en"
      ? vocabulary.youngPlural.charAt(0).toUpperCase() + vocabulary.youngPlural.slice(1)
      : t("simulation.token.kids");
  const kidsNoun = language === "en" ? vocabulary.youngPlural : t("simulation.token.kids");
  const parturitionNoun =
    language === "en" ? vocabulary.parturition : t("simulation.token.litter");
  const active = a.status === "ACTIVE";
  const canViewHealth = can("health.view");
  const canManageHealth = can("health.manage");
  const canViewBreeding = can("breeding.view");
  const canViewFinance = can("finance.view");
  // Weight, move, status and restriction clearance all mutate the same animal
  // lifecycle. One synchronous lock prevents a dismissed slow dialog from
  // overlapping a second action whose validity depends on the first.
  const actionFlight = useSingleFlight();
  const [restrictionOffset, setRestrictionOffset] = useState(0);
  const restrictionHistoryQuery = useMovementRestrictionHistoryApiHealthRestrictionsAnimalIdGet(
    a.id,
    { limit: RESTRICTION_HISTORY_LIMIT, offset: restrictionOffset },
    {
      query: {
        enabled: canViewHealth,
        placeholderData: (previous) => previous,
      },
    },
  );
  const restrictionHistory =
    restrictionHistoryQuery.data?.status === 200
      ? restrictionHistoryQuery.data.data
      : undefined;

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={backHref}
          className="mb-2 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
          {backLabel}
        </Link>
        <PageHeader
          title={
            <span className="inline-flex flex-wrap items-center gap-2">
              {a.tag_number}
              {a.name ? ` · ${a.name}` : ""}
              <StatusBadge status={a.status} />
            </span>
          }
          description={`${a.breed} · ${a.sex === "F" ? enumLabel("sex", "F", language) : enumLabel("sex", "M", language)} · ${enumLabel("bucket", a.current_bucket, language)}`}
          actions={
            active && (
              <>
                {can("animals.weight") && (
                  <AddWeightDialog
                    animalId={a.id}
                    onDone={refresh}
                    actionFlight={actionFlight}
                    profileSettling={profileSettling}
                  />
                )}
                {can("animals.move") && !a.movement_restricted && (
                  <MoveBucketDialog
                    animalId={a.id}
                    currentBucket={a.current_bucket}
                    sex={a.sex}
                    onDone={refresh}
                    actionFlight={actionFlight}
                    profileSettling={profileSettling}
                  />
                )}
                {can("animals.status") && (
                  <StatusDialog
                    animalId={a.id}
                    sex={a.sex}
                    hasDob={a.date_of_birth != null || a.estimated_dob != null}
                    onDone={refresh}
                    actionFlight={actionFlight}
                    profileSettling={profileSettling}
                  />
                )}
                {can("animals.create") && (
                  <EditPhenotypeDialog
                    animal={a}
                    onDone={refresh}
                    actionFlight={actionFlight}
                    profileSettling={profileSettling}
                  />
                )}
              </>
            )
          }
        />
      </div>

      {a.movement_restricted && (
        <Card className="border-destructive/30 bg-destructive/[0.04]">
          <CardContent className="flex flex-wrap items-start justify-between gap-4 pt-6">
            <div className="space-y-1">
              <h2 className="flex items-center gap-2 font-medium text-destructive">
                <AlertTriangle className="size-4" aria-hidden />
                {t("animalDetail.restriction.title")}
              </h2>
              <p className="text-sm text-destructive/90">
                {a.restriction_reason ?? t("animalDetail.restriction.holdFallback")}
              </p>
              {a.suspected_disease && (
                <p className="text-sm">{t("animalDetail.field.suspectedDisease")}: {a.suspected_disease}</p>
              )}
              {a.authority_notified_at && (
                <p className="text-xs text-muted-foreground">
                  {t("animalDetail.detail.authorityNotified")} {formatDate(a.authority_notified_at)}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                {t("animalDetail.restriction.movementNote")}
              </p>
            </div>
            {can("health.manage") && (
              <ClearRestrictionDialog
                animalId={a.id}
                restrictionVersion={a.restriction_version}
                onDone={refresh}
                actionFlight={actionFlight}
                profileSettling={profileSettling}
              />
            )}
          </CardContent>
        </Card>
      )}

      {canViewHealth &&
        (restrictionHistoryQuery.isLoading ||
          restrictionHistoryQuery.isError ||
          (restrictionHistory?.total ?? 0) > 0) && (
          <DataTableCard
            title={t("animalDetail.restrictionAudit.title", { count: restrictionHistory?.total ?? 0 })}
            description={t("animalDetail.restrictionAudit.description")}
          >
            {restrictionHistoryQuery.isLoading ? (
              <InlineLoading>{t("animalDetail.restrictionAudit.loading")}</InlineLoading>
            ) : restrictionHistoryQuery.isError ? (
              <div role="alert" className="flex flex-wrap items-center gap-3">
                <p className="text-sm text-destructive">
                  {restrictionHistoryQuery.error instanceof ApiError
                    ? mapServerError(t, restrictionHistoryQuery.error.detail, restrictionHistoryQuery.error.status, restrictionHistoryQuery.error.code)
                    : t("animalDetail.restrictionAudit.loadFailed")}
                </p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void restrictionHistoryQuery.refetch()}
                >
                  {t("animalDetail.restrictionAudit.retry")}
                </Button>
              </div>
            ) : (
              <div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("animalDetail.restrictionAudit.episode")}</TableHead>
                      <TableHead>{t("animalDetail.restrictionAudit.action")}</TableHead>
                      <TableHead>{t("animalDetail.restrictionAudit.when")}</TableHead>
                      <TableHead>{t("animalDetail.restrictionAudit.reference")}</TableHead>
                      <TableHead>{t("animalDetail.restrictionAudit.disease")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {restrictionHistory?.actions.map((action) => (
                      <TableRow key={action.id}>
                        <TableCell className="tabular-nums">
                          {action.restriction_version}
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={action.action} />
                        </TableCell>
                        <TableCell>{formatFarmDateTime(action.acted_at)}</TableCell>
                        <TableCell>{action.action_reference}</TableCell>
                        <TableCell>{action.disease_target ?? "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                <PaginationControls
                  total={restrictionHistory?.total ?? 0}
                  limit={restrictionHistory?.limit ?? RESTRICTION_HISTORY_LIMIT}
                  offset={restrictionHistory?.offset ?? restrictionOffset}
                  onOffsetChange={setRestrictionOffset}
                  label={t("animalDetail.pagination.restrictionActions")}
                  disabled={restrictionHistoryQuery.isPlaceholderData}
                />
              </div>
            )}
          </DataTableCard>
        )}

      <Card>
        <CardHeader>
          <CardTitle>{t("animalDetail.section.details")}</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <Detail label={t("animalDetail.field.sex")}>{enumLabel("sex", a.sex, language)}</Detail>
            <Detail label={t("animalDetail.detail.breed")}>{a.breed}</Detail>
            {a.coat_color ? (
              <Detail label={t("animals.coatColor")}>
                {COAT_COLOR_LABEL_KEYS[a.coat_color]
                  ? t(COAT_COLOR_LABEL_KEYS[a.coat_color])
                  : a.coat_color}
              </Detail>
            ) : null}
            {a.horned != null ? (
              <Detail label={t("animals.horned")}>
                {a.horned ? t("common.yes") : t("common.no")}
              </Detail>
            ) : null}
            <Detail label={t("animalDetail.detail.bucket")}>{enumLabel("bucket", a.current_bucket, language)}</Detail>
            <Detail label={t("animalDetail.detail.daysInBucket")}>{a.days_in_current_bucket ?? "—"}</Detail>
            <Detail label={t("animalDetail.detail.dateOfBirth")}>
              {a.date_of_birth
                ? formatDate(a.date_of_birth)
                : a.estimated_dob
                  ? `~${formatDate(a.estimated_dob)}`
                  : "—"}
            </Detail>
            <Detail label={t("animalDetail.detail.age")}>
              {a.age_months != null
                ? a.age_months === 1
                  ? t("animalDetail.detail.ageMonths_one", { count: a.age_months })
                  : t("animalDetail.detail.ageMonths", { count: a.age_months })
                : "—"}
            </Detail>
            <Detail label={t("animalDetail.detail.birthType")}>{a.birth_type ? enumLabel("birthType", a.birth_type, language) : "—"}</Detail>
            <Detail label={t("animalDetail.detail.birthWeight")}>
              {a.birth_weight != null ? `${a.birth_weight} kg` : "—"}
            </Detail>
            <Detail label={t("animalDetail.detail.latestWeight")}>
              {a.latest_weight_kg != null ? `${a.latest_weight_kg.toFixed(1)} kg` : "—"}
            </Detail>
            <Detail label={t("animalDetail.detail.source")}>{enumLabel("source", a.source, language)}</Detail>
            {a.source === "PURCHASED" && (
              <>
                <Detail label={t("animalDetail.detail.purchaseDate")}>{formatDate(a.purchase_date)}</Detail>
                <Detail label={t("animalDetail.detail.purchasePrice")}>{formatMoney(a.purchase_price)}</Detail>
                <Detail label={t("animalDetail.detail.seller")}>{a.seller_name ?? "—"}</Detail>
              </>
            )}
            {!active && (
              <>
                <Detail label={t("animalDetail.detail.statusDate")}>{formatDate(a.status_date)}</Detail>
                {SALE_CAPABLE_STATUSES.includes(a.status) && (
                  <>
                    <Detail label={t("animalDetail.detail.salePrice")}>{formatMoney(a.sale_price)}</Detail>
                    {a.sale_weight_kg != null && (
                      <Detail label={t("animalDetail.detail.saleWeight")}>{a.sale_weight_kg} kg</Detail>
                    )}
                    {/* Buyer identity is finance-gated on the API; the "—"
                        fallback covers both "none recorded" and "withheld". */}
                    <Detail label={t("animalDetail.detail.buyer")}>{a.buyer_name ?? "—"}</Detail>
                  </>
                )}
                {a.status === "DEAD" && (
                  <>
                    <Detail label={t("animalDetail.field.mortalityCause")}>{a.mortality_cause ?? "—"}</Detail>
                    {a.mortality_cause_code && (
                      <Detail label={t("animalDetail.detail.causeCode")}>
                        {enumLabel("mortalityCause", a.mortality_cause_code, language)}
                      </Detail>
                    )}
                    <Detail label={t("animalDetail.field.disposalMethod")}>
                      {enumLabel("disposalMethod", a.disposal_method, language)}
                    </Detail>
                    <Detail label={t("animalDetail.field.necropsyPerformed")}>
                      {/* Clinical facts fail closed without health.view. */}
                      {!canViewHealth ? "—" : a.necropsy_done ? t("common.yes") : t("common.no")}
                    </Detail>
                    {a.necropsy_done && a.necropsy_findings && (
                      <Detail label={t("animalDetail.field.necropsyFindings")}>{a.necropsy_findings}</Detail>
                    )}
                    <Detail label={t("animalDetail.detail.mortalityReported")}>
                      {formatDate(a.mortality_reported_at)}
                    </Detail>
                    <Detail label={t("animalDetail.detail.scheduledDiseaseSuspected")}>
                      {/* The API fails closed to `false` without health.view,
                          so a bare "No" would assert a fact we were not told. */}
                      {!canViewHealth ? "—" : a.suspected_scheduled_disease ? t("common.yes") : t("common.no")}
                    </Detail>
                    {a.suspected_disease && (
                      <Detail label={t("animalDetail.field.suspectedDisease")}>{a.suspected_disease}</Detail>
                    )}
                    {a.authority_notified_at && (
                      <Detail label={t("animalDetail.detail.authorityNotified")}>
                        {formatDate(a.authority_notified_at)}
                      </Detail>
                    )}
                  </>
                )}
              </>
            )}
            {a.restriction_cleared_at && (
              <>
                <Detail label={t("animalDetail.detail.restrictionCleared")}>
                  {formatFarmDateTime(a.restriction_cleared_at)}
                </Detail>
                <Detail label={t("animalDetail.detail.clearanceReference")}>
                  {a.restriction_clearance_reference ?? "—"}
                </Detail>
              </>
            )}
            {a.dam_id != null && (
              <Detail label={t("animalDetail.detail.dam")}>
                <Link
                  href={`/animals/${a.dam_id}`}
                  className="font-medium text-primary hover:underline"
                >
                  #{a.dam_id}
                </Link>
              </Detail>
            )}
            {a.sire_id != null && (
              <Detail label={t("animalDetail.detail.sire")}>
                <Link
                  href={`/animals/${a.sire_id}`}
                  className="font-medium text-primary hover:underline"
                >
                  #{a.sire_id}
                </Link>
              </Detail>
            )}
          </dl>
          {a.notes && <p className="mt-4 text-sm text-muted-foreground">{a.notes}</p>}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <DataTableCard title={t("animalDetail.weights.title", { count: profile.weights_total })}>
          {profile.weights.length === 0 ? (
            <EmptyState
              icon={Scale}
              title={t("animalDetail.weights.emptyTitle")}
              description={can("animals.weight") ? t("animalDetail.weights.emptyDescriptionCanRecord") : t("animalDetail.weights.emptyDescriptionNoPermission")}
              className="py-8"
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("animalDetail.table.date")}</TableHead>
                  <TableHead className="text-right">{t("animalDetail.weights.weight")}</TableHead>
                  <TableHead className="text-right">BCS</TableHead>
                  <TableHead>{t("animalDetail.field.notes")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {profile.weights.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell>{formatDate(w.date)}</TableCell>
                    <TableCell className="text-right">{w.weight_kg.toFixed(1)} kg</TableCell>
                    <TableCell className="text-right">{w.bcs ?? "—"}</TableCell>
                    <TableCell>{w.notes ?? ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <PaginationControls
            total={profile.weights_total}
            limit={profile.history_limit}
            offset={profile.weights_offset}
            onOffsetChange={onWeightsOffsetChange}
            label={t("animalDetail.pagination.weightRecords")}
            disabled={historySettling}
          />
        </DataTableCard>

        <DataTableCard title={t("animalDetail.moves.title", { count: profile.moves_total })}>
          {profile.moves.length === 0 ? (
            <EmptyState
              icon={ArrowLeftRight}
              title={t("animalDetail.moves.emptyTitle")}
              description={t("animalDetail.moves.emptyDescription")}
              className="py-8"
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("animalDetail.moves.effectiveDate")}</TableHead>
                  <TableHead>{t("animalDetail.moves.recorded")}</TableHead>
                  <TableHead>{t("animalDetail.moves.from")}</TableHead>
                  <TableHead>{t("animalDetail.moves.to")}</TableHead>
                  <TableHead>{t("animalDetail.field.reason")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {profile.moves.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell>{formatDate(m.effective_date)}</TableCell>
                    <TableCell>{formatFarmDateTime(m.moved_at)}</TableCell>
                    <TableCell>{m.from_bucket ? enumLabel("bucket", m.from_bucket, language) : "—"}</TableCell>
                    <TableCell>{enumLabel("bucket", m.to_bucket, language)}</TableCell>
                    <TableCell>{m.reason ?? ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <PaginationControls
            total={profile.moves_total}
            limit={profile.history_limit}
            offset={profile.moves_offset}
            onOffsetChange={onMovesOffsetChange}
            label={t("animalDetail.pagination.bucketMoves")}
            disabled={historySettling}
          />
        </DataTableCard>

        {canViewHealth && (
        <DataTableCard title={t("animalDetail.healthEvents.title", { count: profile.health_events_total })}>
          {profile.health_events.length === 0 ? (
            <EmptyState
              icon={HeartPulse}
              title={t("animalDetail.healthEvents.emptyTitle")}
              description={t("animalDetail.healthEvents.emptyDescription")}
              className="py-8"
            >
              {canManageHealth && (
                <Link
                  href={withReturnTo("/health/new", `/animals/${a.id}`)}
                  className={buttonVariants({ variant: "outline", size: "sm" })}
                >
                  {t("animalDetail.healthEvents.addEvent")}
                </Link>
              )}
            </EmptyState>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("animalDetail.table.date")}</TableHead>
                  <TableHead>{t("animalDetail.healthEvents.type")}</TableHead>
                  <TableHead>{t("animalDetail.healthEvents.product")}</TableHead>
                  <TableHead className="text-right">{t("animalDetail.healthEvents.cost")}</TableHead>
                  <TableHead>{t("animalDetail.healthEvents.nextDue")}</TableHead>
                  <TableHead>{t("animalDetail.healthEvents.traceability")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {profile.health_events.map((h) => (
                  <TableRow key={h.id}>
                    <TableCell>{formatDate(h.date)}</TableCell>
                    <TableCell>{enumLabel("eventType", h.type, language)}</TableCell>
                    <TableCell>{h.product_name ?? h.disease_target ?? "—"}</TableCell>
                    <TableCell className="text-right">{formatMoney(h.cost)}</TableCell>
                    <TableCell>{formatDate(h.next_due_date)}</TableCell>
                    <TableCell>
                      <span className="text-xs">
                        {[
                          h.product_lot ? t("animalDetail.healthEvents.traceLot", { lot: h.product_lot }) : null,
                          h.certificate_number ? t("animalDetail.healthEvents.traceCert", { cert: h.certificate_number }) : null,
                          h.withdrawal_until
                            ? t("animalDetail.healthEvents.traceWithdrawal", { date: formatDate(h.withdrawal_until) })
                            : null,
                          h.suspected_scheduled_disease
                            ? t("animalDetail.healthEvents.scheduledHold")
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" · ") || "—"}
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <PaginationControls
            total={profile.health_events_total}
            limit={profile.history_limit}
            offset={profile.health_events_offset}
            onOffsetChange={onHealthEventsOffsetChange}
            label={t("animalDetail.pagination.healthEvents")}
            disabled={historySettling}
          />
        </DataTableCard>
        )}

        {(a.sex === "F" || profile.kids_total > 0) && (
          <DataTableCard title={t("animalDetail.kids.title", { young: kidsNounCap, count: profile.kids_total })}>
            {profile.kids.length === 0 ? (
              <EmptyState
                icon={Baby}
                title={t("animalDetail.kids.emptyTitle", { young: kidsNoun })}
                description={t("animalDetail.kids.emptyDescription", { parturition: parturitionNoun })}
                className="py-8"
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("animalDetail.kids.tag")}</TableHead>
                    <TableHead>{t("animalDetail.field.sex")}</TableHead>
                    <TableHead>{t("animalDetail.kids.born")}</TableHead>
                    <TableHead>{t("animalDetail.kids.status")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {profile.kids.map((k) => (
                    <TableRow key={k.id}>
                      <TableCell>
                        <Link
                          href={`/animals/${k.id}`}
                          className="font-medium text-foreground hover:text-primary"
                        >
                          {k.tag_number}
                        </Link>
                      </TableCell>
                      <TableCell>{enumLabel("sex", k.sex, language)}</TableCell>
                      <TableCell>{formatDate(k.date_of_birth)}</TableCell>
                      <TableCell>
                        <StatusBadge status={k.status} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            <PaginationControls
              total={profile.kids_total}
              limit={profile.history_limit}
              offset={profile.kids_offset}
              onOffsetChange={onKidsOffsetChange}
              label={t("animalDetail.pagination.offspring")}
              disabled={historySettling}
            />
          </DataTableCard>
        )}

        {canViewBreeding && (
          <DataTableCard title={t("animalDetail.breeding.title", { count: profile.breedings_total })}>
            {profile.breedings.length === 0 ? (
              <EmptyState
                icon={GitBranch}
                title={t("animalDetail.breeding.emptyTitle")}
                description={t("animalDetail.breeding.emptyDescription")}
                className="py-8"
              />
            ) : (
              <ul className="divide-y">
                {profile.breedings.map((recordId) => (
                  <li key={recordId} className="py-2">
                    <Link
                      href={withReturnTo("/breeding", `/animals/${a.id}`)}
                      className="text-primary underline"
                    >
                      {t("animalDetail.breeding.recordLink", { id: recordId })}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            <PaginationControls
              total={profile.breedings_total}
              limit={profile.history_limit}
              offset={profile.breedings_offset}
              onOffsetChange={onBreedingsOffsetChange}
              label={t("animalDetail.pagination.breedingRecords")}
              disabled={historySettling}
            />
          </DataTableCard>
        )}

        {/* The lifetime P&L reads the finance register (money facts only), so
            it follows finance.view; the backend also enforces it on the
            endpoint itself. */}
        {canViewFinance && <LifetimePnlCard animalId={a.id} canViewFinance={canViewFinance} />}
      </div>
    </div>
  );
}

function AnimalProfilePageContent({ perms }: { perms: PermissionsState }) {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const animalId = Number(params.id);
  const validAnimalId =
    /^\d+$/.test(params.id) && Number.isSafeInteger(animalId) && animalId > 0;
  const { can } = perms;
  const allowed = can("animals.view");
  const t = useT();
  const [kidsOffset, setKidsOffset] = useState(0);
  const [weightsOffset, setWeightsOffset] = useState(0);
  const [movesOffset, setMovesOffset] = useState(0);
  const [healthEventsOffset, setHealthEventsOffset] = useState(0);
  const [breedingsOffset, setBreedingsOffset] = useState(0);
  // A deep link to a DIFFERENT animal re-renders this same component, so the
  // per-panel offsets would carry the previous animal's page into the new
  // animal's (shorter) history — reset them on the id change (P3,
  // 2026-09-20 audit: health-log pagination was lost/misapplied on deep
  // links between animals).
  /* eslint-disable react-hooks/set-state-in-effect -- URL-id re-seed of local pagination state */
  useEffect(() => {
    setKidsOffset(0);
    setWeightsOffset(0);
    setMovesOffset(0);
    setHealthEventsOffset(0);
    setBreedingsOffset(0);
  }, [animalId]);
  /* eslint-enable react-hooks/set-state-in-effect */
  const query = useAnimalProfileApiAnimalsAnimalIdGet(
    animalId,
    {
      history_limit: PROFILE_HISTORY_LIMIT,
      kids_offset: kidsOffset,
      weights_offset: weightsOffset,
      moves_offset: movesOffset,
      health_events_offset: healthEventsOffset,
      breedings_offset: breedingsOffset,
    },
    {
      query: {
        enabled: allowed && validAnimalId,
        placeholderData: (previous) => previous,
      },
    },
  );
  const profile = query.data?.status === 200 ? query.data.data : undefined;
  const refresh = useProfileRefresh(animalId);
  const backHref = permittedAppPath(searchParams.get("returnTo"), can) ?? "/animals";
  const backLabel = backHref.startsWith("/dashboard")
    ? t("animalDetail.back.dashboard")
    : backHref.startsWith("/tasks")
      ? t("animalDetail.back.tasks")
      : backHref.startsWith("/health")
        ? t("animalDetail.back.health")
        : t("animalDetail.back.animals");

  if (!validAnimalId) {
    return (
      <div role="alert" className="space-y-3 rounded-lg border border-destructive/40 p-4">
        <p className="text-sm text-destructive">{t("animalDetail.invalidId")}</p>
        <Link href={backHref} className="block text-sm text-primary underline">
          {backLabel}
        </Link>
      </div>
    );
  }
  // "The animal is gone / access was revoked" — continuing to render the
  // profile would let the operator submit weight, move and status writes
  // against it.
  const fatalProfileError =
    query.error instanceof ApiError &&
    (query.error.status === 404 || query.error.status === 403 || query.error.status === 401);
  const profileErrorBox = (
    <div role="alert" className="space-y-3 rounded-lg border border-destructive/40 p-4">
      <p className="text-sm text-destructive">
        {query.error instanceof ApiError
          ? mapServerError(t, query.error.detail, query.error.status, query.error.code)
          : t("animalDetail.loadFailed")}
      </p>
      <Button type="button" variant="outline" onClick={() => void query.refetch()}>
        {t("animalDetail.retryProfile")}
      </Button>
      <Link href={backHref} className="block text-sm text-primary underline">
        {backLabel}
      </Link>
    </div>
  );
  // Gate on data presence, not on isError: a background refetch that fails
  // transiently keeps the last good payload, and tearing the body down would
  // destroy open dialogs and typed input while claiming the animal does not
  // exist. Gate on `!profile` rather than `isLoading || !profile` so a non-200
  // envelope (profile undefined, not loading, not an error) still reaches an
  // actionable branch instead of an unrecoverable spinner.
  if (!profile) {
    if (query.isError) return profileErrorBox;
    return (
      <div className="space-y-6" role="status" aria-live="polite">
        <span className="sr-only">{t("common.loading")}</span>
        <PageHeader title={t("animalDetail.page.title")} description={t("animalDetail.page.description")} />
        <PageSkeleton cards={3} />
      </div>
    );
  }
  if (query.isError && fatalProfileError) return profileErrorBox;
  return (
    <>
      {query.isError && (
        <div
          role="status"
          className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-warning/50 bg-warning-tint p-3 text-sm text-warning-tint-foreground dark:border-warning/40"
        >
          <span>{t("animalDetail.refreshFailed")}</span>
          <Button type="button" size="sm" variant="outline" onClick={() => void query.refetch()}>
            {t("common.retry")}
          </Button>
        </div>
      )}
    <ProfileBody
      profile={profile}
      refresh={refresh}
      // A failed background refresh leaves the last good profile rendered,
      // but that snapshot is no longer authoritative enough to start a
      // lifecycle write. Keep actions inert until Retry establishes a fresh
      // server-owned version.
      profileSettling={query.isFetching || query.isError}
      historySettling={query.isPlaceholderData}
      backHref={backHref}
      backLabel={backLabel}
      onKidsOffsetChange={setKidsOffset}
      onWeightsOffsetChange={setWeightsOffset}
      onMovesOffsetChange={setMovesOffset}
      onHealthEventsOffsetChange={setHealthEventsOffset}
      onBreedingsOffsetChange={setBreedingsOffset}
      />
    </>
  );
}

export default function AnimalProfilePage() {
  const params = useParams<{ id: string }>();
  const perms = usePermissions();
  const t = useT();
  return (
    <Suspense
      fallback={
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("common.loading")}</span>
          <PageSkeleton cards={3} />
        </div>
      }
    >
      {/* Keyed by the route param: the App Router reuses this mounted tree
          when only [id] changes (dam/sire/kid links navigate profile →
          profile), and placeholderData keeps the previous profile rendered
          through the switch — so without a remount, one animal's history
          offsets (and ProfileBody's restriction offset) would be sent as the
          next animal's query params, showing a falsely empty page. */}
      <PermissionGate
        perms={perms}
        perm="animals.view"
        label={t("animalDetail.page.title")}
        description={t("animalDetail.page.description")}
        cards={3}
        announce
      >
        <AnimalProfilePageContent key={params.id} perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
