"use client";

/** Breeding records — parity with v1's breeding/list.html (+ new/ultrasound as dialogs). */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { CircleCheckBig, Clock, HeartHandshake, Plus } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Controller, useForm, useWatch, type FieldPath } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useAbortPregnancyApiBreedingRecordIdAbortPost,
  useBreedingListApiBreedingGet,
  useCreateBreedingApiBreedingPost,
  useGetBreedingRecordApiBreedingRecordIdGet,
  useSubmitUltrasoundApiBreedingRecordIdUltrasoundPost,
} from "@/api/generated/endpoints";
import {
  PregnancyLossInCause,
  type BreedingCandidateAvailabilityOut,
  type BreedingRecordOut,
} from "@/api/generated/models";
import { BreedingCandidatePicker } from "@/components/breeding-candidate-picker";
import type { RemotePickerOption } from "@/components/remote-picker";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
import { PaginationControls } from "@/components/pagination-controls";
import { StatusBadge } from "@/components/status-badge";
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
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  apiValidationErrors,
  applyApiValidationToForm,
  } from "@/lib/api-client";
import { enumLabel } from "@/lib/enum-labels";
import { useLanguage, useT, type Language, type TFn } from "@/lib/i18n";
import { mapServerError } from "@/lib/server-error-phrases";
import { useMutationError } from "@/lib/mutations";
import { daysBetween, farmToday, formatDate } from "@/lib/format";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { PageSkeleton } from "@/components/skeletons";

/** Deep-link ids arrive as raw query strings; anything that is not a positive
 * safe integer is ignored. */
function parsePositiveId(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}



/** Mirrors services/breeding.py: standing heat ends within ~1 day of the
 * service and the next heat cannot return before ~18 days, so a not-pregnant
 * result dated strictly between them reports an unobservable event. */
const STANDING_HEAT_DAYS = 1;
const EARLIEST_RETURN_TO_HEAT_DAYS = 18;

/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw enum value in the closed trigger. Labels come
 * from the shared enum-labels map (same vocabulary the record rows render),
 * so the write path never shows SCREAMING_SNAKE codes either. */
const lossCauseItems = (language: Language): Record<string, string> =>
  Object.fromEntries(
    Object.values(PregnancyLossInCause).map((value) => [value, enumLabel("lossCause", value, language)]),
  );
/** Options for the loss-cause select — same catalog as the items map. */
const lossCauseOptions = (language: Language) =>
  Object.values(PregnancyLossInCause).map((value) => ({
    value,
    label: enumLabel("lossCause", value, language),
  }));

function OutcomeBadge({ outcome, language }: { outcome: string; language: Language }) {
  return (
    <StatusBadge status={outcome}>
      {enumLabel("outcome", outcome, language)}
    </StatusBadge>
  );
}

/** Exported for direct schema-level testing of the inline gates. The
 * messages resolve through the i18n catalog, so the factory takes the
 * caller's `t` and the mounted dialog rebuilds it whenever the active
 * language changes (health.buildEventSchema precedent). */
export const buildBreedingSchema = (t: TFn) =>
  z
  .object({
    doe_id: z
      .string()
      .min(1, t("breeding.validation.selectFemale", { noun: t("breeding.noun.female") })),
    // Buck is required for natural service; AI methods name a semen bull.
    buck_id: z.string(),
    method: z.enum(["NATURAL", "AI", "AI_SEXED"]),
    semen_sire_name: z.string().trim().max(120).optional(),
    breeding_date: z
      .string()
      .regex(
        /^\d{4}-\d{2}-\d{2}$/,
        t("breeding.validation.dateInvalid"),
      )
      .refine((s) => s <= farmToday(), t("breeding.validation.dateFuture")),
  })
  .superRefine((values, ctx) => {
    if (values.method === "NATURAL" && !values.buck_id) {
      ctx.addIssue({
        code: "custom",
        path: ["buck_id"],
        message: t("breeding.validation.selectMaleNatural", { noun: t("breeding.noun.male") }),
      });
    }
  });
type BreedingValues = z.infer<ReturnType<typeof buildBreedingSchema>>;

function breedingDefaults(): BreedingValues {
  return {
    doe_id: "",
    buck_id: "",
    method: "NATURAL",
    semen_sire_name: "",
    breeding_date: farmToday(),
  };
}

/** The dialog's registered field names — the 422 mapper maps loc tails
 * against these and falls through anything else to the banner. */
const BREEDING_FORM_FIELDS: readonly string[] = Object.keys(breedingDefaults());

function NewBreedingDialog({
  open,
  onOpenChange,
  candidateAvailability,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidateAvailability: BreedingCandidateAvailabilityOut | null;
  onSaved: () => void;
}) {
  const createMutation = useCreateBreedingApiBreedingPost();
  const createFlight = useSingleFlight();
  const { language } = useLanguage();
  const t = useT();
  const mutationErrorMessage = useMutationError();
  const vocabulary = farmVocabulary;
  const femaleNoun = t("breeding.noun.female");
  const femaleNounCap = t("breeding.noun.femaleCap");
  const maleNoun = t("breeding.noun.male");
  const maleNounCap = t("breeding.noun.maleCap");
  const [formError, setFormError] = useState<string | null>(null);
  // RemotePicker resolves a selected label from its loaded page, then this
  // prop, then its own state — and that state dies with the picker when the
  // dialog unmounts its content, while react-hook-form deliberately keeps the
  // id. Holding the labels here (the dialog outlives the picker) keeps the
  // trigger showing the chosen animal instead of falling back to "Select doe".
  const [doeOption, setDoeOption] = useState<RemotePickerOption | null>(null);
  const [buckOption, setBuckOption] = useState<RemotePickerOption | null>(null);
  const eligibleDoeCount = candidateAvailability?.eligible_doe_count ?? null;
  const eligibleBuckCount = candidateAvailability?.eligible_buck_count ?? null;
  const hasEligibleBuck = eligibleBuckCount !== null && eligibleBuckCount > 0;
  const localizedSchema = useMemo(() => buildBreedingSchema(t), [t]);
  const {
    control,
    register,
    handleSubmit,
    reset,
    resetField,
    setError,
    formState: { errors, isSubmitting, dirtyFields },
  } = useForm<BreedingValues>({
    resolver: zodResolver(localizedSchema),
    defaultValues: breedingDefaults(),
  });
  const method = useWatch({ control, name: "method" });

  // Read during render so RHF's formState proxy subscribes to dirty tracking.
  const breedingDateTouched = Boolean(dirtyFields.breeding_date);
  useEffect(() => {
    // The mount-time date default goes stale at midnight, but a full
    // reset() here discarded in-progress doe/buck selections whenever the
    // dialog was accidentally dismissed and reopened. Refresh only the
    // untouched date; everything else is reset after a successful save.
    if (open && !breedingDateTouched) {
      resetField("breeding_date", { defaultValue: breedingDefaults().breeding_date });
    }
  }, [open, breedingDateTouched, resetField]);

  async function onSubmit(values: BreedingValues) {
    await createFlight.run(async () => {
      setFormError(null);
        // Same-farm fence: the write keeps its captured X-Farm-Id, but this
      // continuation must not touch another farm's UI (M-2).
      const semenSirePayload = values.method !== "NATURAL" ? { method: values.method, semen_sire_name: values.semen_sire_name?.trim() || null } : {};
      const stillOwnsFarm = captureFarmScope();
      try {
        await createMutation.mutateAsync({
          data: {
            doe_id: Number(values.doe_id),
            ...(values.method === "NATURAL" ? { buck_id: Number(values.buck_id) } : {}),
            ...semenSirePayload,
            breeding_date: values.breeding_date,
          },
        });
        // The write belongs to the farm it was addressed to; the completion
        // must not toast/close/invalidate on a different farm's UI (M-2).
        if (!stillOwnsFarm()) return;
        toast.success(t("breeding.toast.saved"));
        reset(breedingDefaults());
        // Clear the lifted labels alongside the form values, or the next
        // breeding would open showing the animals this one used.
        setDoeOption(null);
        setBuckOption(null);
        onOpenChange(false);
        onSaved();
      } catch (err) {
        // L-26: after a farm switch this stale failure must not paint another farm's
        // form (inline or banner) or toast.
        if (!stillOwnsFarm()) return;
        // A 422's per-field issues land inline on their inputs (the dialog
        // already renders field-level errors with aria wiring); only issues
        // that match no form field degrade to the banner + toast.
        const unmapped = applyApiValidationToForm(
          err,
          (field, message) =>
            setError(field as FieldPath<BreedingValues>, { type: "server", message }),
          BREEDING_FORM_FIELDS,
        );
        if (unmapped.length === apiValidationErrors(err).length) {
          const message = mutationErrorMessage(err);
          setFormError(message);
          toast.error(message);
        } else if (unmapped.length > 0) {
          const message = unmapped.map((issue) => issue.msg).join("; ");
          setFormError(message);
          toast.error(message);
        }
      }
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
  if (!nextOpen && (isSubmitting || createFlight.pending)) return;
        if (!nextOpen) setFormError(null);
        onOpenChange(nextOpen);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("breeding.add")}</DialogTitle>
          <DialogDescription>
            {t("breeding.dialog.description", { days: vocabulary.facts.pregnancyCheckDays })}
          </DialogDescription>
        </DialogHeader>
        {candidateAvailability === null ? (
          <p role="alert" className="text-destructive">
            {t("breeding.dialog.availabilityUnavailable")}
          </p>
        ) : eligibleDoeCount === 0 ? (
          <p className="text-muted-foreground">
            {t("breeding.dialog.noEligibleFemales")}{" "}
            {t("breeding.dialog.gateCopy", {
              female: femaleNoun,
              femaleMonths: vocabulary.breedingEntry.female.minMonths,
              femaleWeight: vocabulary.breedingEntry.female.minWeightKg,
              males: t("breeding.noun.malePlural"),
              maleMonths: vocabulary.breedingEntry.male.minMonths,
              maleWeight: vocabulary.breedingEntry.male.minWeightKg,
            })}
          </p>
        ) : (
          <form onSubmit={handleSubmit(onSubmit)} noValidate>
            <fieldset
              disabled={isSubmitting || createFlight.pending}
              className="space-y-4"
            >
            {formError && (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="breeding-doe">
                {t("breeding.form.femaleLabel", { noun: femaleNounCap })}
              </Label>
              <Controller
                control={control}
                name="doe_id"
                render={({ field }) => (
                  <BreedingCandidatePicker
                    id="breeding-doe"
                    kind="doe"
                    value={field.value}
                    onValueChange={field.onChange}
                    onOptionChange={setDoeOption}
                selectedOption={doeOption?.value === field.value ? doeOption : null}
                    placeholder={t("breeding.form.selectFemalePlaceholder", { noun: femaleNoun })}
                    dialogTitle={t("breeding.form.chooseFemaleTitle", { noun: femaleNoun })}
                    aria-invalid={Boolean(errors.doe_id) || undefined}
                    aria-describedby={errors.doe_id ? "breeding-doe-error" : undefined}
                  />
                )}
              />
              {errors.doe_id && (
                <p id="breeding-doe-error" role="alert" className="text-sm text-destructive">{errors.doe_id.message}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label>{t("breeding.form.methodLabel")}</Label>
              <div
                role="radiogroup"
                aria-label={t("breeding.form.methodAria")}
                className="grid gap-2 sm:grid-cols-3"
              >
                {/* AI / AI_SEXED are part of the wire contract (legacy
                    records render them) but the goat protocol rejects the
                    write unconditionally (services/breeding — "AI and
                    sexed-semen services are not part of the goat protocol",
                    409). Offering the radios only produced guaranteed
                    failures after the operator filled the form. */}
                {(
                  [
                    ["NATURAL", enumLabel("method", "NATURAL", language), t("breeding.form.methodNaturalHint", { noun: maleNoun })],
                  ] as const
                ).map(([value, title, hint]) => (
                  <label
                    key={value}
                    className="flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 text-left transition has-[input:checked]:border-primary"
                  >
                    <input
                      type="radio"
                      value={value}
                      {...register("method")}
                      className="mt-1 accent-primary"
                    />
                    <span>
                      <span className="block text-sm font-medium">{title}</span>
                      <span className="block text-xs text-muted-foreground">{hint}</span>
                    </span>
                  </label>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                {t("breeding.form.methodNote", { noun: maleNoun })}
              </p>
            </div>
            {method === "NATURAL" ? (
              <div className="space-y-1.5">
                <Label htmlFor="breeding-buck">
                  {t("breeding.form.maleLabel", { noun: maleNounCap })}
                </Label>
                <Controller
                  control={control}
                  name="buck_id"
                  render={({ field }) => (
                    <BreedingCandidatePicker
                      id="breeding-buck"
                      kind="buck"
                      value={field.value}
                      onValueChange={field.onChange}
                      onOptionChange={setBuckOption}
                    selectedOption={buckOption?.value === field.value ? buckOption : null}
                      placeholder={t("breeding.form.selectMalePlaceholder", { noun: maleNoun })}
                      dialogTitle={t("breeding.form.chooseMaleTitle", { noun: maleNoun })}
                      disabled={!hasEligibleBuck}
                      aria-invalid={Boolean(errors.buck_id) || undefined}
                      aria-describedby={errors.buck_id ? "breeding-buck-error" : undefined}
                    />
                  )}
                />
                {errors.buck_id && (
                  <p id="breeding-buck-error" role="alert" className="text-sm text-destructive">{errors.buck_id.message}</p>
                )}
                {!hasEligibleBuck && (
                  <p className="text-sm text-destructive">
                    {t("breeding.form.noEligibleMales", {
                      nouns: t("breeding.noun.malePlural"),
                      nounsCap: t("breeding.noun.malePluralCap"),
                    })}
                  </p>
                )}
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label htmlFor="breeding-semen-sire">
                  {t("breeding.form.semenSireLabel", { noun: maleNoun })}
                </Label>
                <Input
                  id="breeding-semen-sire"
                  maxLength={120}
                  placeholder={t("breeding.form.semenSirePlaceholder", { noun: maleNounCap })}
                  aria-describedby="breeding-semen-sire-hint"
                  {...register("semen_sire_name")}
                />
                <p id="breeding-semen-sire-hint" className="text-xs text-muted-foreground">
                  {t("breeding.form.semenSireHint", { noun: femaleNoun })}
                </p>
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="breeding_date">{t("breeding.form.dateLabel")}</Label>
              <Input
                id="breeding_date"
                type="date"
                max={farmToday()}
                aria-invalid={Boolean(errors.breeding_date) || undefined}
                aria-describedby={errors.breeding_date ? "breeding-date-error" : undefined}
                {...register("breeding_date")}
              />
              {errors.breeding_date && (
                <p id="breeding-date-error" role="alert" className="text-sm text-destructive">
                  {errors.breeding_date.message}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button
                type="submit"
                disabled={
                  isSubmitting ||
                  createFlight.pending ||
                  (method === "NATURAL" && !hasEligibleBuck)
                }
              >
                {/* react-hook-form's isSubmitting spans the entire submit
                    window (the flight starts inside the handler). */}
                {isSubmitting
                  ? t("breeding.form.saving")
                  : formError
                    ? t("breeding.form.retrySave")
                    : t("breeding.form.submit")}
              </Button>
            </DialogFooter>
            </fieldset>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** PENDING → record the ultrasound result (pregnant checkbox + kid count). */
function UltrasoundDialog({
  record,
  onClose,
  onSaved,
}: {
  record: BreedingRecordOut;
  onClose: () => void;
  onSaved: () => void;
}) {
  const vocabulary = farmVocabulary;
  const t = useT();
  const mutationErrorMessage = useMutationError();
  // A diagnostic outcome is never pre-filled: pre-checking "pregnant" lets an
  // operator who trusts the form save a negative scan as a confirmed pregnancy,
  // which moves the doe to PREGNANCY_EARLY and spawns pre-kidding follow-up
  // tasks (record_ultrasound_result). The checkbox therefore always starts
  // unchecked and the twins kid-count default only appears once the operator
  // explicitly checks it.
  const [pregnant, setPregnant] = useState(false);
  const [kidCount, setKidCount] = useState("");
  const [resultDate, setResultDate] = useState(farmToday());
  const [saving, setSaving] = useState(false);
  const saveLock = useRef(false);
  const [formError, setFormError] = useState<string | null>(null);
  const mutation = useSubmitUltrasoundApiBreedingRecordIdUltrasoundPost();
  // Mirrors record_ultrasound_result(): every result must fall on/after the
  // breeding date and on/before today, but only a POSITIVE one has to wait for
  // the planned scan. A doe back in standing heat at the ~21-day cycle is
  // evidence the service failed, so her NOT-pregnant result is recordable the
  // day it was observed instead of being backdated to a fictitious day-32 scan.
  const earliestResultDate = pregnant
    ? (record.ultrasound_date ?? record.breeding_date)
    : record.breeding_date;
  // …but "the day it was observed" still has to be a day on which the failure
  // IS observable: the service itself (day 0/1) or the return to heat (~day
  // 18 on). The server rejects the gap between them with a 409, so bound it
  // here rather than after the operator has saved.
  const negativeResultGapDays =
    !pregnant && resultDate ? daysBetween(record.breeding_date, resultDate) : null;
  const inUnobservableNegativeWindow = negativeResultGapDays !== null && negativeResultGapDays > STANDING_HEAT_DAYS && negativeResultGapDays < EARLIEST_RETURN_TO_HEAT_DAYS;
  const resultDateError = !resultDate
    ? t("breeding.ultrasound.validation.required")
    : resultDate < earliestResultDate
      ? t("breeding.ultrasound.validation.tooEarly", { date: formatDate(earliestResultDate) })
      : resultDate > farmToday()
        ? t("breeding.ultrasound.validation.future")
        : inUnobservableNegativeWindow
          ? t("breeding.ultrasound.validation.unobservable", {
              days: negativeResultGapDays ?? 0,
              earliest: EARLIEST_RETURN_TO_HEAT_DAYS,
            })
          : null;
  // Species litter cap (goat scans reach 4) mirrors
  // backend SpeciesProfile.max_litter_size — the server 422s past the cap.
  const detectedOptions = Array.from(
    { length: vocabulary.facts.maxLitterSize },
    (_, index) => String(index + 1),
  );
  const kidCountError = pregnant && !detectedOptions.includes(kidCount)
    ? t("breeding.ultrasound.validation.youngCount", { young: t("breeding.noun.young") })
    : null;

  async function onSubmit() {
    if (resultDateError || kidCountError || saveLock.current) return;
    saveLock.current = true;
    setSaving(true);
    setFormError(null);
      // Same-farm fence: the write keeps its captured X-Farm-Id, but this
      // continuation must not touch another farm's UI (M-2).
      const stillOwnsFarm = captureFarmScope();
    try {
      await mutation.mutateAsync({
        recordId: record.id,
        data: {
          pregnant,
          date: resultDate,
          kid_count: pregnant ? Number(kidCount) : null,
        },
      });
      if (!stillOwnsFarm()) return;
      toast.success(t("breeding.toast.ultrasoundSaved"));
      onClose();
      onSaved();
    } catch (err) {
      // L-26: after a farm switch this stale failure must not paint another farm's
      // dialog or toast its error.
      if (!stillOwnsFarm()) return;
      const message = mutationErrorMessage(err);
      setFormError(message);
      toast.error(message);
    } finally {
      saveLock.current = false;
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !saving && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("breeding.ultrasound.title")}</DialogTitle>
          <DialogDescription>
            {t("breeding.ultrasound.description", {
              female: t("breeding.noun.femaleCap"),
              doeTag: record.doe_tag ?? `#${record.doe_id}`,
              date: formatDate(record.breeding_date),
              buck: record.buck_tag ?? `#${record.buck_id}`,
            })}
            {record.ultrasound_date
              ? t("breeding.ultrasound.plannedScanSuffix", {
                  date: formatDate(record.ultrasound_date),
                })
              : ""}
          </DialogDescription>
        </DialogHeader>
        {formError && (
          <p role="alert" className="text-sm text-destructive">
            {formError}
          </p>
        )}
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor={`ultrasound-result-date-${record.id}`}>
              {t("breeding.ultrasound.resultDateLabel")}
            </Label>
            <Input
              id={`ultrasound-result-date-${record.id}`}
              type="date"
              min={earliestResultDate}
              max={farmToday()}
              required
              disabled={saving}
              value={resultDate}
              aria-invalid={Boolean(resultDateError) || undefined}
              aria-describedby={resultDateError ? `ultrasound-result-date-${record.id}-error` : undefined}
              onChange={(event) => setResultDate(event.target.value)}
            />
            {resultDateError && (
              <p
                id={`ultrasound-result-date-${record.id}-error`}
                role="alert"
                className="text-sm text-destructive"
              >
                {resultDateError}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {t("breeding.ultrasound.dateExplainer", {
                date: formatDate(record.ultrasound_date),
                days: EARLIEST_RETURN_TO_HEAT_DAYS,
                female: t("breeding.noun.female"),
              })}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {/* onSubmit reads pregnant/resultDate/kidCount from the closure it
                was created in, so a change made after the click is not in the
                in-flight body — yet the dialog closes showing it, and the doe
                is recorded with the value the operator no longer sees. */}
            <Checkbox
              id="pregnant"
              disabled={saving}
              checked={pregnant}
              onCheckedChange={(checked) => {
                const selected = checked === true;
                setPregnant(selected);
                setKidCount(selected ? "2" : "");
              }}
            />
            <Label htmlFor="pregnant">{t("breeding.ultrasound.pregnantLabel")}</Label>
          </div>
          {pregnant && (
            <div className="space-y-1.5">
              <Label htmlFor={`ultrasound-kid-count-${record.id}`}>
                {t("breeding.ultrasound.youngCountLabel", { young: t("breeding.noun.youngCap") })}
              </Label>
              <Select
                value={kidCount}
                disabled={saving}
                onValueChange={(v) => setKidCount(String(v))}
              >
                <SelectTrigger id={`ultrasound-kid-count-${record.id}`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {detectedOptions.map((n) => (
                    <SelectItem key={n} value={n}>
                      {n}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {/* kidCountError's value is unreachable through the dialog (see
                  its disable above); the submit guard and the disabled button
                  carry the defense, so no dead alert paragraph renders. */}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={onSubmit}
            disabled={saving || Boolean(resultDateError) || Boolean(kidCountError)}
          >
            {saving
              ? t("breeding.form.saving")
              : formError
                ? t("breeding.ultrasound.retrySubmit")
                : t("breeding.ultrasound.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PregnancyLossDialog({
  record,
  onClose,
  onSaved,
}: {
  record: BreedingRecordOut;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mutation = useAbortPregnancyApiBreedingRecordIdAbortPost();
  const saveFlight = useSingleFlight();
  const { language } = useLanguage();
  const t = useT();
  const mutationErrorMessage = useMutationError();
  const earliestLossDate = record.ultrasound_result_date && record.ultrasound_result_date > record.breeding_date ? record.ultrasound_result_date : record.breeding_date;
  const [lossDate, setLossDate] = useState(farmToday());
  const [cause, setCause] = useState<PregnancyLossInCause>(PregnancyLossInCause.UNKNOWN);
  const [notes, setNotes] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const lossDateError = !lossDate
    ? t("breeding.loss.validation.required")
    : lossDate < earliestLossDate
      ? t("breeding.loss.validation.tooEarly", { date: formatDate(earliestLossDate) })
      : lossDate > farmToday()
        ? t("breeding.loss.validation.future")
        : null;
  const notesError =
    notes.length > 4_000 ? t("breeding.loss.validation.notesTooLong", { max: 4_000 }) : null;
  const saving = mutation.isPending || saveFlight.pending;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (lossDateError || notesError) return;
    await saveFlight.run(async () => {
      setFormError(null);
        // Same-farm fence: the write keeps its captured X-Farm-Id, but this
      // continuation must not touch another farm's UI (M-2).
      const stillOwnsFarm = captureFarmScope();
      try {
        await mutation.mutateAsync({
          recordId: record.id,
          data: {
            loss_date: lossDate,
            cause,
            notes: notes.trim() || null,
          },
        });
        if (!stillOwnsFarm()) return;
        toast.success(t("breeding.toast.lossRecorded"));
        onClose();
        onSaved();
      } catch (err) {
        // L-26: after a farm switch this stale failure must not paint another farm's
        // dialog or toast its error.
        if (!stillOwnsFarm()) return;
        const message = mutationErrorMessage(err);
        setFormError(message);
        toast.error(message);
      }
    });
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !saving && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("breeding.loss.title")}</DialogTitle>
          <DialogDescription>
            {t("breeding.loss.description", {
              female: t("breeding.noun.femaleCap"),
              doeTag: record.doe_tag ?? `#${record.doe_id}`,
              date: formatDate(record.breeding_date),
            })}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} noValidate>
          <fieldset disabled={saving} className="space-y-4">
          {formError && (
            <p role="alert" className="text-sm text-destructive">
              {formError}
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor={`pregnancy-loss-date-${record.id}`}>
              {t("breeding.loss.dateLabel")}
            </Label>
            <Input
              id={`pregnancy-loss-date-${record.id}`}
              type="date"
              min={earliestLossDate}
              max={farmToday()}
              value={lossDate}
              aria-invalid={Boolean(lossDateError) || undefined}
              aria-describedby={lossDateError ? `pregnancy-loss-date-error-${record.id}` : undefined}
              onChange={(event) => setLossDate(event.target.value)}
            />
            {lossDateError && (
              <p
                id={`pregnancy-loss-date-error-${record.id}`}
                role="alert"
                className="text-sm text-destructive"
              >
                {lossDateError}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`pregnancy-loss-cause-${record.id}`}>
              {t("breeding.loss.causeLabel")}
            </Label>
            <Select
              value={cause}
              onValueChange={(value) => setCause(value as PregnancyLossInCause)}
              items={lossCauseItems(language)}
            >
              <SelectTrigger id={`pregnancy-loss-cause-${record.id}`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {lossCauseOptions(language).map(({ value, label }) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`pregnancy-loss-notes-${record.id}`}>
              {t("breeding.loss.notesLabel")}
            </Label>
            <Textarea
              id={`pregnancy-loss-notes-${record.id}`}
              value={notes}
              maxLength={4_000}
              aria-invalid={Boolean(notesError) || undefined}
              aria-describedby={notesError ? `pregnancy-loss-notes-error-${record.id}` : undefined}
              onChange={(event) => setNotes(event.target.value)}
            />
            {notesError && (
              <p
                id={`pregnancy-loss-notes-error-${record.id}`}
                role="alert"
                className="text-sm text-destructive"
              >
                {notesError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" disabled={saving} onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button
              type="submit"
              variant="destructive"
              disabled={saving || Boolean(lossDateError) || Boolean(notesError)}
            >
              {saving
                ? t("breeding.loss.recording")
                : formError
                  ? t("breeding.loss.retrySubmit")
                  : t("breeding.loss.title")}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function BreedingPageContent({ perms }: { perms: PermissionsState }) {
  const queryClient = useQueryClient();
  const { language } = useLanguage();
  const t = useT();
  const vocabulary = farmVocabulary;
  const femaleNounCap = t("breeding.noun.femaleCap");
  const maleNounCap = t("breeding.noun.maleCap");
  const { can } = perms;
  const allowed = can("breeding.view");
  const canManage = can("breeding.manage");
  const canViewAnimals = can("animals.view");
  const [newOpen, setNewOpen] = useState(false);
  const [ultrasoundFor, setUltrasoundFor] = useState<BreedingRecordOut | null>(null);
  const [lossFor, setLossFor] = useState<BreedingRecordOut | null>(null);
  // Remember which URL intent was dismissed, not a page-lifetime boolean:
  // Next reuses this page for query-only navigation to another record.
  const [dismissedPrefillId, setDismissedPrefillId] = useState<number | null>(null);
  // Read from the router, not window.location: Next commits the browser URL
  // in an insertion effect, i.e. after this component has already rendered, so
  // a router.replace("/breeding?ultrasound_id=…") redirect is still invisible
  // to window.location during the first render.
  const searchParams = useSearchParams();
  const requestedUltrasoundId = parsePositiveId(searchParams.get("ultrasound_id"));
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const query = useBreedingListApiBreedingGet(
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
  const candidateAvailability = payload?.candidate_availability ?? null;
  const pagedPrefillRecord = payload?.records.find(
    (record) => record.id === requestedUltrasoundId,
  );
  const prefillRecordQuery = useGetBreedingRecordApiBreedingRecordIdGet(
    requestedUltrasoundId ?? 0,
    {
      query: {
        enabled:
          canManage &&
          requestedUltrasoundId !== null &&
          payload !== undefined &&
          pagedPrefillRecord === undefined,
      },
    },
  );
  const fetchedPrefillRecord =
    prefillRecordQuery.data?.status === 200 ? prefillRecordQuery.data.data : undefined;
  const requestedRecord = pagedPrefillRecord ?? fetchedPrefillRecord;
  // PENDING is the whole gate, exactly like the per-row action: the dialog
  // itself decides which results the planned scan date still constrains.
  // Wait for the screen to be free. The deep-linked record can resolve long
  // after arrival (it needs its own fetch whenever it is off the current list
  // page), and mounting it then would drop a second modal on top of a
  // half-filled loss or new-breeding form and move the focus trap into it
  // mid-typing. Once the other dialog closes this re-evaluates and the deep
  // link opens — then latches on its own id.
  const deepLinkedUltrasound =
    canManage &&
    dismissedPrefillId !== requestedUltrasoundId &&
    !lossFor &&
    !newOpen &&
    requestedRecord?.outcome === "PENDING"
      ? requestedRecord
      : null;
  const activeUltrasound = ultrasoundFor ?? deepLinkedUltrasound;
  // A deep link that resolved to a record which is no longer PENDING used to
  // vanish silently; keep the operator informed until they clear it (L18).
  const staleDeepLink =
    canManage &&
    requestedUltrasoundId !== null &&
    dismissedPrefillId !== requestedUltrasoundId &&
    !lossFor &&
    !newOpen &&
    requestedRecord !== undefined &&
    requestedRecord.outcome !== "PENDING";

  useEffect(() => {
    // Dismissal belongs to one continuous URL intent, not to this page's
    // lifetime. Next can keep the page mounted while the query is cleared and
    // later navigate back to the same task URL. Without releasing the latch in
    // that gap, the second visit to the same record stayed silently dismissed.
    if (requestedUltrasoundId === null && dismissedPrefillId !== null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- URL intent teardown
      setDismissedPrefillId(null);
    }
  }, [dismissedPrefillId, requestedUltrasoundId]);

  function refresh() {
    invalidateFarmData(queryClient);
  }

  /** Per-record management actions, shared by the desktop row and the
   * below-md card. `touch` lifts the buttons to ≥44px (h-11) for the phone
   * card list — 36px sm buttons sit under every mobile touch guideline once
   * the row's compact table context is gone (same pattern as tasks). */
  function recordActions(r: BreedingRecordOut, touch = false) {
    const actionClass = touch ? "h-11 px-4" : undefined;
    return (
      <div
        className={
          touch
            ? "flex flex-wrap items-center gap-2"
            : "flex flex-wrap items-center justify-end gap-2"
        }
      >
        {/* A not-pregnant result is recordable before the planned
            scan (doe back in heat), so the action stays open for
            every PENDING record; the plan is only a hint. */}
        {r.outcome === "PENDING" && r.ultrasound_date && r.ultrasound_date > farmToday() && (
          <span className="text-xs text-muted-foreground">
            {t("breeding.actions.scanPlanned", { date: formatDate(r.ultrasound_date) })}
          </span>
        )}
        {r.outcome === "PENDING" && (
          <Button
            variant="outline"
            size="sm"
            className={actionClass}
            onClick={() => setUltrasoundFor(r)}
          >
            {t("breeding.ultrasound.title")}
          </Button>
        )}
        {r.outcome === "CONFIRMED_PREGNANT" && !r.has_kidding && (
          <Button
            variant="destructive"
            size="sm"
            className={actionClass}
            onClick={() => setLossFor(r)}
          >
            {t("breeding.actions.recordLoss")}
          </Button>
        )}
        {r.has_kidding && (
          <span className="text-muted-foreground">
            {t("breeding.noun.parturitionPastCap")}
          </span>
        )}
      </div>
    );
  }

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div className="space-y-3" role="alert">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError
              ? mapServerError(t, query.error.detail, query.error.status, query.error.code)
              : t("breeding.error.loadFailed")}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            {t("breeding.error.retry")}
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("breeding.title")}
          description={t("breeding.description")}
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("breeding.loading")}</span>
          <PageSkeleton stats={3} cards={1} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title={t("breeding.title")}
        description={t("breeding.description")}
        actions={
          canManage ? (
            <Button onClick={() => setNewOpen(true)}>
              <Plus />
              {t("breeding.add")}
            </Button>
          ) : undefined
        }
      />

      {staleDeepLink && requestedRecord && (
        <div role="status" className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">
            {t("breeding.deepLink.notAwaiting", {
              id: requestedUltrasoundId ?? 0,
              outcome: enumLabel("outcome", requestedRecord.outcome, language),
            })}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setDismissedPrefillId(requestedUltrasoundId)}
          >
            {t("breeding.deepLink.clear")}
          </Button>
        </div>
      )}

      {prefillRecordQuery.isError && (
        <div className="flex flex-wrap items-center gap-2" role="alert">
          <span className="text-sm text-destructive">
            {prefillRecordQuery.error instanceof ApiError
              ? mapServerError(t, prefillRecordQuery.error.detail, prefillRecordQuery.error.status, prefillRecordQuery.error.code)
              : t("breeding.error.loadLinkedFailed")}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void prefillRecordQuery.refetch()}
          >
            {t("breeding.error.retryLinked")}
          </Button>
        </div>
      )}

      {payload.records.length === 0 ? (
        <EmptyState
          icon={HeartHandshake}
          title={t("breeding.empty.title")}
          description={t("breeding.empty.description", {
            parturition: t("breeding.noun.parturition"),
          })}
        >
          {canManage && (
            <Button size="sm" onClick={() => setNewOpen(true)}>
              <Plus />
              {t("breeding.add")}
            </Button>
          )}
        </EmptyState>
      ) : (
        <DataTableCard
          title={t("breeding.list.title")}
          description={t("breeding.list.description", {
            days: vocabulary.facts.pregnancyCheckDays,
            parturition: t("breeding.noun.parturition"),
          })}
        >
          {/* Below md the 9-column register becomes a card per breeding —
           * panning a 900px table inside a 390px phone is not a register,
           * it's a scroll toy (same pattern as the tasks board). */}
          <div className="space-y-2 md:hidden">
            {payload.records.map((r) => (
              <div key={r.id} className="space-y-2 rounded-xl border bg-card p-3 shadow-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{formatDate(r.breeding_date)}</span>
                  <OutcomeBadge outcome={r.outcome} language={language} />
                </div>
                <p className="text-sm">
                  {canViewAnimals ? (
                    <Link href={`/animals/${r.doe_id}`} className="text-primary underline">
                      {r.doe_tag ?? `${femaleNounCap} #${r.doe_id}`}
                    </Link>
                  ) : (
                    (r.doe_tag ?? `${femaleNounCap} #${r.doe_id}`)
                  )}
                  {" × "}
                  {canViewAnimals ? (
                    <Link href={`/animals/${r.buck_id}`} className="text-primary underline">
                      {r.buck_tag ?? `${maleNounCap} #${r.buck_id}`}
                    </Link>
                  ) : (
                    (r.buck_tag ?? `${maleNounCap} #${r.buck_id}`)
                  )}
                </p>
                <p className="text-xs text-muted-foreground tabular-nums">
                  {t("breeding.card.cycle", { number: r.heat_cycle_number })} ·{" "}
                  {r.ultrasound_done ? (
                    <>{t("breeding.card.ultrasoundDone")}</>
                  ) : r.ultrasound_date ? (
                    <>
                      {t("breeding.card.ultrasoundDue", { date: formatDate(r.ultrasound_date) })}
                    </>
                  ) : (
                    <>{t("breeding.card.noUltrasound")}</>
                  )}
                  {" · "}
                  {t("breeding.card.youngDetected", {
                    young: t("breeding.noun.youngPluralCap"),
                    count: r.kid_count_detected ?? "—",
                  })}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t("breeding.card.expectedParturition", {
                    parturition: t("breeding.noun.parturition"),
                    date: formatDate(r.expected_kidding_date),
                  })}
                </p>
                {r.outcome === "ABORTED" && r.loss_date && (
                  <div className="text-xs text-muted-foreground">
                    <p>
                      {formatDate(r.loss_date)} ·{" "}
                      {enumLabel("lossCause", r.loss_cause ?? "UNKNOWN")}
                    </p>
                    {r.loss_notes && (
                      <details>
                        <summary className="cursor-pointer">
                          {t("breeding.card.lossNotes")}
                        </summary>
                        <p className="whitespace-pre-wrap break-words">{r.loss_notes}</p>
                      </details>
                    )}
                  </div>
                )}
                {canManage && recordActions(r, true)}
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[900px]">
            <TableHeader>
              <TableRow>
                <TableHead>{t("breeding.table.bred")}</TableHead>
                <TableHead>{femaleNounCap}</TableHead>
                <TableHead>{maleNounCap}</TableHead>
                <TableHead>{t("breeding.table.cycle")}</TableHead>
                <TableHead>{t("breeding.table.ultrasound")}</TableHead>
                <TableHead>{t("breeding.noun.youngPluralCap")}</TableHead>
                <TableHead>
                  {t("breeding.table.expectedParturition", {
                    parturition: t("breeding.noun.parturition"),
                  })}
                </TableHead>
                <TableHead>{t("breeding.table.outcome")}</TableHead>
                {canManage && (
                  <TableHead className="text-right">{t("breeding.table.actions")}</TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.records.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>{formatDate(r.breeding_date)}</TableCell>
                  <TableCell>
                    {canViewAnimals ? (
                      <Link href={`/animals/${r.doe_id}`} className="text-primary underline">
                        {r.doe_tag ?? `${femaleNounCap} #${r.doe_id}`}
                      </Link>
                    ) : (
                      r.doe_tag ?? `${femaleNounCap} #${r.doe_id}`
                    )}
                  </TableCell>
                  <TableCell>
                    {canViewAnimals ? (
                      <Link href={`/animals/${r.buck_id}`} className="text-primary underline">
                        {r.buck_tag ?? `${maleNounCap} #${r.buck_id}`}
                      </Link>
                    ) : (
                      r.buck_tag ?? `${maleNounCap} #${r.buck_id}`
                    )}
                  </TableCell>
                  <TableCell>{r.heat_cycle_number}</TableCell>
                  <TableCell>
                    {r.ultrasound_done ? (
                      <span className="inline-flex items-center gap-1 text-success">
                        <CircleCheckBig aria-hidden="true" className="size-3.5" />
                        {t("breeding.table.ultrasoundDone")}
                      </span>
                    ) : r.ultrasound_date ? (
                      <span className="inline-flex items-center gap-1 text-warning-tint-foreground">
                        <Clock aria-hidden="true" className="size-3.5" />
                        {t("breeding.table.ultrasoundDue", {
                          date: formatDate(r.ultrasound_date),
                        })}
                      </span>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>{r.kid_count_detected ?? "—"}</TableCell>
                  <TableCell>{formatDate(r.expected_kidding_date)}</TableCell>
                  <TableCell>
                    <OutcomeBadge outcome={r.outcome} language={language} />
                    {r.outcome === "ABORTED" && r.loss_date && (
                      <div className="mt-1 text-xs text-muted-foreground">
                        <p>
                          {formatDate(r.loss_date)} ·{" "}
                          {enumLabel("lossCause", r.loss_cause ?? "UNKNOWN", language)}
                        </p>
                        {r.loss_notes && (
                          <details>
                            <summary className="cursor-pointer">
                              {t("breeding.card.lossNotes")}
                            </summary>
                            <p className="max-w-80 whitespace-pre-wrap break-words">{r.loss_notes}</p>
                          </details>
                        )}
                      </div>
                    )}
                  </TableCell>
                  {canManage && (
                    <TableCell className="text-right">
                      {recordActions(r)}
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          {listSettling && (
            <p role="status" className="pt-3 text-sm text-muted-foreground">
              {t("breeding.updating")}
            </p>
          )}
          <PaginationControls
            total={payload.total}
            limit={payload.limit}
            offset={payload.offset}
            onOffsetChange={setOffset}
            label={t("breeding.list.paginationLabel")}
            disabled={listSettling}
          />
        </DataTableCard>
      )}

      {canManage && (
        <NewBreedingDialog
          open={newOpen}
          onOpenChange={setNewOpen}
          candidateAvailability={candidateAvailability}
          onSaved={refresh}
        />
      )}
      {activeUltrasound && (
        <UltrasoundDialog
          key={activeUltrasound.id}
          record={activeUltrasound}
          onClose={() => {
            // Only the deep-linked record's OWN dismissal may retire the deep
            // link. This handler also serves rows the operator opened by hand
            // while the deep-linked record was still being fetched; latching
            // there silently dropped the task they arrived from, leaving
            // ?ultrasound_id= in the address bar with no UI trace and no way
            // back short of a reload.
            if (activeUltrasound.id === requestedUltrasoundId) {
              setDismissedPrefillId(requestedUltrasoundId);
            }
            setUltrasoundFor(null);
          }}
          onSaved={refresh}
        />
      )}
      {lossFor && (
        <PregnancyLossDialog
          key={lossFor.id}
          record={lossFor}
          onClose={() => setLossFor(null)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}

export default function BreedingPage() {
  const perms = usePermissions();
  const t = useT();
  return (
    <Suspense
      fallback={
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("common.loading")}</span>
          <PageSkeleton stats={3} cards={1} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="breeding.view"
        label={t("breeding.title")}
        description={t("breeding.description")}
        stats={3}
        cards={1}
        announce
      >
        <BreedingPageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
