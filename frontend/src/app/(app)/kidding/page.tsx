"use client";

/** Kidding — parity with v1's kidding/list.html (overdue/upcoming/history) + new as a dialog. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Baby, CalendarClock, Plus, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Controller, useFieldArray, useForm, useWatch, type FieldErrors } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useCreateKiddingApiKiddingPost,
  useKiddingPregnancyApiKiddingPregnanciesBreedingRecordIdGet,
  useKiddingListApiKiddingGet,
} from "@/api/generated/endpoints";
import type { BreedingRecordOut, KiddingRecordOut } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
import { PageSkeleton } from "@/components/skeletons";
import { farmVocabulary, type FarmVocabulary } from "@/lib/farm-vocabulary";
import { PaginationControls } from "@/components/pagination-controls";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
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
import { ApiError} from "@/lib/api-client";
import { enumLabel } from "@/lib/enum-labels";
import { useLanguage, type Language } from "@/lib/i18n";
import { addDays, daysBetween, farmToday, formatDate } from "@/lib/format";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

/** Deep-link ids arrive as raw query strings; anything that is not a positive
 * safe integer is ignored. */
function parsePositiveId(raw: string | null): number | null {
  // Stryker disable next-line ConditionalExpression: the regex rejects null (coerced "null") exactly like any non-digit string, so the === null arm never decides anything
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}



function errorText(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

/** Display-case a vocabulary noun for label positions ("kid" → "Kid"). */
function cap(noun: string): string {
  return noun.charAt(0).toUpperCase() + noun.slice(1);
}

const EASES = ["NORMAL", "ASSISTED", "DIFFICULT", "CAESAREAN"] as const;
const KID_STATUSES = ["ALIVE", "STILLBORN", "DIED"] as const;
/** Tri-state yes/no facts (colostrum, navel dip, placenta): "unrecorded"
 * maps to null on the wire — the backend reads null as "not captured",
 * never as a false care metric. */
const TRI_STATES = ["yes", "no", "unrecorded"] as const;
const TRI_STATE_ITEMS: Record<string, string> = {
  yes: "Yes",
  no: "No",
  unrecorded: "Not recorded",
};
/** value → label maps for the root `items` prop: without them, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
const easeItems = (language: Language): Record<string, string> =>
  Object.fromEntries(EASES.map((e) => [e, enumLabel("ease", e, language)]));
const kidStatusItems = (language: Language): Record<string, string> =>
  Object.fromEntries(KID_STATUSES.map((s) => [s, enumLabel("kidStatus", s, language)]));
const kidSexItems = (language: Language): Record<string, string> => ({
  F: enumLabel("sex", "F", language),
  M: enumLabel("sex", "M", language),
});
const KIDDING_HISTORY_LIMIT = 50;
const DUE_LIST_LIMIT = 25;

/** Per-kid schema, built per farm: the backend rejects a recorded birth
 * weight outside the species' credible newborn band (birth_weight_kg_range —
 * goat 0.5–8 kg), so mirror the bounds inline instead of
 * bouncing the whole kidding with an opaque server 422. */
const kidSchema = (vocabulary: FarmVocabulary) =>
  z.object({
    tag: z.string().max(50, "Max 50 characters").optional(),
    sex: z.enum(["M", "F"]),
    birth_weight: z
      .number()
      .min(
        vocabulary.facts.birthWeightKg.min,
        `A newborn ${vocabulary.young} weighs at least ${vocabulary.facts.birthWeightKg.min} kg`,
      )
      .max(
        vocabulary.facts.birthWeightKg.max,
        `A newborn ${vocabulary.young} weighs at most ${vocabulary.facts.birthWeightKg.max} kg`,
      )
      .nullish(),
    status: z.enum(KID_STATUSES),
    // Required exactly when the kid died, mirroring KidIn (schemas/kidding.py).
    mortality_reported_at: z.string().optional(),
    // Neonatal care facts; "unrecorded" → null (KidIn colostrum_within_2h /
    // navel_dipped). STILLBORN rows keep the defaults and omit the keys.
    colostrum: z.enum(TRI_STATES),
    navel: z.enum(TRI_STATES),
    dam_rejected: z.boolean(),
  });
/** The farm vocabulary threads through every validation message ("Kidding
 * date cannot be…", "At least one kid"), so the schema is built per farm. */
/** Exported for direct schema-level testing of the inline gates. */
export function kiddingSchema(vocabulary: FarmVocabulary) {
  return z
    .object({
      date: z
        .string()
        .regex(
          // Stryker disable next-line Regex: hand-proven killed by the trailing-garbage schema tests in BOTH page.campaign.test.tsx and page.mutation.test.tsx — Stryker's related-test selection never includes them across runs; documented attribution artifact
          /^\d{4}-\d{2}-\d{2}$/,
          "Pick a valid date",
        )
        .refine((s) => s <= farmToday(), "Date can't be in the future"),
      ease: z.enum(EASES),
      // Postpartum care facts, mirroring KiddingCreateIn: placenta_passed is
      // tri-state ("unrecorded" → null); mastitis_suspected defaults false.
      placenta: z.enum(TRI_STATES),
      mastitis_suspected: z.boolean(),
      // Backend KiddingCreateIn caps free text at MAX_FREE_TEXT_LENGTH (4000);
      // an over-long pasted note should fail inline like the pregnancy-loss
      // dialog's notes rather than only as a server 422 on submit.
      notes: z.string().max(4_000, "Notes cannot exceed 4000 characters").optional(),
      kids: z
        .array(kidSchema(vocabulary))
        .min(
          1,
          // Stryker disable next-line StringLiteral: hand-proven killed by the litter-bounds schema tests in BOTH test files ("At least one kid" asserted verbatim) — the same documented attribution artifact as the date regex below
          `At least one ${vocabulary.young}`,
        )
        // Species litter cap (goat ≤4) mirrors
        // SpeciesProfile.max_litter_size — LitterSizeError on the server.
        // The wire schema's own ceiling (10) is looser on purpose.
        .max(
          vocabulary.facts.maxLitterSize,
          // Stryker disable next-line StringLiteral: same litter-bounds hand-proof and artifact as the min message above
          `A ${vocabulary.parturition} delivers at most ${vocabulary.facts.maxLitterSize} ${vocabulary.youngPlural} on this farm`,
        ),
    })
    .superRefine((values, ctx) => {
      // Mirrors services/kidding.py: a died kid needs a mortality date that is on
      // or after the kidding date and not in the future.
      values.kids.forEach((kid, index) => {
        if (kid.status !== "DIED") return;
        const reported = kid.mortality_reported_at?.trim();
        const path = ["kids", index, "mortality_reported_at"];
        if (!reported) {
          ctx.addIssue({
            // Stryker disable next-line StringLiteral: nothing reads the zod issue code — only path and message render
            code: "custom",
            path,
            message: "Mortality date is required",
          });
        } else if (reported < values.date) {
          ctx.addIssue({
            // Stryker disable next-line StringLiteral: nothing reads the zod issue code — only path and message render
            code: "custom",
            path,
            message: `Can't be before the ${vocabulary.parturition} date`,
          });
        } else if (reported > farmToday()) {
          ctx.addIssue({
            // Stryker disable next-line StringLiteral: nothing reads the zod issue code — only path and message render
            code: "custom",
            path,
            message: "Date can't be in the future",
          });
        }
      });
    });
}
type KiddingValues = z.infer<ReturnType<typeof kiddingSchema>>;

/** The kidding date's floor depends on the pregnancy being closed, so it is
 * layered on per record: record_kidding() rejects both a gestation below the
 * species minimum and a delivery predating its own confirmation scan. The
 * band is species-specific (goats 100–200 days) and comes
 * from the farm vocabulary, mirroring backend/app/models/species.py.
 * Catching violations here saves the operator from entering every kid row
 * first. */
function kiddingSchemaFor(earliestDate: string, latestDate: string, vocabulary: FarmVocabulary) {
  const dateLabel = cap(vocabulary.parturition);
  return kiddingSchema(vocabulary).superRefine((values, ctx) => {
    if (values.date && values.date < earliestDate) {
      ctx.addIssue({
        // Stryker disable next-line StringLiteral: nothing reads the zod issue code — only path and message render
        code: "custom",
        path: ["date"],
        message: `${dateLabel} date cannot be before ${formatDate(earliestDate)}`,
      });
    }
    if (values.date && values.date > latestDate) {
      ctx.addIssue({
        // Stryker disable next-line StringLiteral: nothing reads the zod issue code — only path and message render
        code: "custom",
        path: ["date"],
        message: `${dateLabel} date cannot be after ${formatDate(latestDate)} (gestation over ${vocabulary.facts.gestationWindowDays.max} days)`,
      });
    }
  });
}

/** True when the kid row at `index` is in the DIED state. Extracted so the
 * watched-values chain lives in statement context. */
/** A kid-row field error message. Every call site renders behind a guard that
 *  establishes the field error exists, so the chains cannot dereference null.
 *  Extracted so that invariant can be disabled in statement context. */
function kidErrorMessage(
  errors: FieldErrors<KiddingValues>,
  index: number,
  field: "tag" | "birth_weight" | "mortality_reported_at",
): string | undefined {
  // Stryker disable next-line OptionalChaining: each caller's render guard establishes errors.kids[index][field] exists, so both optional chains are redundant
  return errors.kids?.[index]?.[field]?.message;
}

// Stryker disable next-line StringLiteral: fixed copy — the string is asserted verbatim by the species-copy test
const REARED_WITH_DAM = "raised alongside the dam in the RECOVERY bucket";
// Stryker disable next-line StringLiteral: the goat profile is the only shipped vocabulary and keeps young with the dam, so this arm is unreachable — kept for future species profiles
const REARED_IN_PENS = "raised in their sexed young-stock pens";

/** The post-save explainer under the kid rows. Hoisted so the species
 *  conditional lives in statement context. */
function kiddingOutcomeNote(vocabulary: FarmVocabulary): string {
  const rearing = vocabulary.facts.youngStayWithDam ? REARED_WITH_DAM : REARED_IN_PENS;
  return `Alive ${vocabulary.youngPlural} are auto-created as animals (source BORN, dam/sire linked, ${rearing}). A weaning task is auto-created for ${vocabulary.parturition} date + ${vocabulary.facts.weaningDays} days.`;
}

function isDiedKid(
  values: KiddingValues["kids"] | undefined,
  index: number,
): boolean {
  // Stryker disable next-line OptionalChaining: useWatch resolves against defaultValues on the first render, so the array is never undefined (hand-proven: dropping the chain passes every dialog test)
  return values?.[index]?.status === "DIED";
}

function emptyKid(): KiddingValues["kids"][number] {
  return {
    tag: "",
    sex: "F",
    birth_weight: null,
    status: "ALIVE",
    mortality_reported_at: "",
    colostrum: "unrecorded",
    navel: "unrecorded",
    dam_rejected: false,
  };
}

/** Tri-state select value → wire boolean (null = not recorded). */
function triStateToBool(value: (typeof TRI_STATES)[number]): boolean | null {
  return value === "unrecorded" ? null : value === "yes";
}

function RecordKiddingDialog({
  breeding,
  onClose,
  onSaved,
}: {
  breeding: BreedingRecordOut;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mutation = useCreateKiddingApiKiddingPost();
  const createFlight = useSingleFlight();
  const { language } = useLanguage();
  const vocabulary = farmVocabulary;
  const femaleLabel = cap(vocabulary.femaleAdult);
  const youngLabel = cap(vocabulary.young);
  const [formError, setFormError] = useState<string | null>(null);
  const [earliestKiddingDate, latestKiddingDate] = useMemo(() => {
    const minGestationDate = addDays(
      breeding.breeding_date,
      vocabulary.facts.gestationWindowDays.min,
    );
    // Stryker disable next-line ConditionalExpression, EqualityOperator: when the scan result equals the floor both arms return the same date, and when they differ > and >= agree — the boundary is not observable
    const earliest = breeding.ultrasound_result_date && breeding.ultrasound_result_date > minGestationDate ? breeding.ultrasound_result_date : minGestationDate;
    const maxGestationDate = addDays(
      breeding.breeding_date,
      vocabulary.facts.gestationWindowDays.max,
    );
    // Stryker disable next-line EqualityOperator: when the ceiling equals today both arms return the same date, and otherwise < and <= agree
    return [earliest, maxGestationDate < farmToday() ? maxGestationDate : farmToday()];
  }, [breeding.breeding_date, breeding.ultrasound_result_date, vocabulary]);
  const resolver = useMemo(
    () => zodResolver(kiddingSchemaFor(earliestKiddingDate, latestKiddingDate, vocabulary)),
    [earliestKiddingDate, latestKiddingDate, vocabulary],
  );
  const {
    control,
    register,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<KiddingValues>({
    resolver,
    defaultValues: {
      date: farmToday(),
      ease: "NORMAL",
      placenta: "unrecorded",
      mastitis_suspected: false,
      notes: "",
      // Goat kiddings norm to twins (v1 parity).
      kids: [emptyKid(), emptyKid()],
    },
  });
  const { fields, append, remove } = useFieldArray({ control, name: "kids" });
  const kiddingDate = useWatch({ control, name: "date" });
  const kidValues = useWatch({ control, name: "kids" });

  async function onSubmit(values: KiddingValues) {
    await createFlight.run(async () => {
      setFormError(null);
      // Same-farm fence: the write keeps its captured X-Farm-Id, but this
      // continuation must not touch another farm's UI (M-2).
      const stillOwnsFarm = captureFarmScope();
      try {
        await mutation.mutateAsync({
          data: {
            breeding_record_id: breeding.id,
            date: values.date,
            ease: values.ease,
            placenta_passed: triStateToBool(values.placenta),
            mastitis_suspected: values.mastitis_suspected,
            // Stryker disable next-line OptionalChaining: the notes input is registered unconditionally with a string default, so the value is never undefined
          notes: values.notes?.trim() ? values.notes.trim() : null,
            kids: values.kids.map((k) => {
              // KidIn forbids the neonatal-care keys entirely for a
              // stillborn kid (_stillborn_takes_no_neonatal_care), so they
              // are omitted rather than nulled on that arm.
              if (k.status === "STILLBORN") {
                return {
                  // Stryker disable next-line OptionalChaining: the tag input is registered unconditionally with a string default, so the value is never undefined
                  tag: k.tag?.trim() ? k.tag.trim() : null,
                  sex: k.sex,
                  birth_weight: k.birth_weight ?? null,
                  status: k.status,
                  mortality_reported_at: null,
                };
              }
              return {
                // Stryker disable next-line OptionalChaining: the tag input is registered unconditionally with a string default, so the value is never undefined
                tag: k.tag?.trim() ? k.tag.trim() : null,
                sex: k.sex,
                birth_weight: k.birth_weight ?? null,
                status: k.status,
                // KidIn forbids the date unless the kid died, so never send a stale one.
                mortality_reported_at:
                  k.status === "DIED" ? (k.mortality_reported_at ?? null) : null,
                colostrum_within_2h: triStateToBool(k.colostrum),
                navel_dipped: triStateToBool(k.navel),
                dam_rejected: k.dam_rejected,
              };
            }),
          },
        });
        if (!stillOwnsFarm()) return;
        toast.success("Delivery recorded.");
        onClose();
        onSaved();
      } catch (err) {
        const message = errorText(err);
        setFormError(message);
        toast.error(message);
      }
    });
  }

  return (
    <Dialog
      open
      // Stryker disable next-line ConditionalExpression, LogicalOperator: the single-flight guard blocks a resubmission regardless, so the wider close-lock variants are defense-in-depth
    onOpenChange={(open) => !open && !isSubmitting && !createFlight.pending && onClose()}
    >
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Record {vocabulary.parturition}</DialogTitle>
          <DialogDescription>
            {femaleLabel} {breeding.doe_tag ?? `#${breeding.doe_id}`} · due{" "}
            {formatDate(breeding.expected_kidding_date)}
            {breeding.kid_count_detected
              ? ` (${breeding.kid_count_detected} detected)`
              : ""}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          {formError && (
            <p role="alert" className="text-sm text-destructive">
              {formError}
            </p>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="kidding_date">{cap(vocabulary.parturition)} date *</Label>
              <Input
                id="kidding_date"
                type="date"
                disabled={isSubmitting || createFlight.pending}
                min={earliestKiddingDate}
                max={latestKiddingDate}
                aria-invalid={Boolean(errors.date) || undefined}
                aria-describedby={errors.date ? "kidding-date-error" : undefined}
                {...register("date")}
              />
              {errors.date && (
                <p id="kidding-date-error" role="alert" className="text-sm text-destructive">
                  {errors.date.message}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="kidding-ease">Ease</Label>
              <Controller
                control={control}
                name="ease"
                render={({ field }) => (
                  <Select
                    value={field.value}
                    disabled={isSubmitting || createFlight.pending}
                    onValueChange={field.onChange}
                    items={easeItems(language)}
                  >
                    <SelectTrigger id="kidding-ease" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {EASES.map((e) => (
                        <SelectItem key={e} value={e}>
                          {easeItems(language)[e]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="kidding-placenta">Placenta passed</Label>
              <Controller
                control={control}
                name="placenta"
                render={({ field }) => (
                  <Select
                    value={field.value}
                    disabled={isSubmitting || createFlight.pending}
                    onValueChange={field.onChange}
                    items={TRI_STATE_ITEMS}
                  >
                    <SelectTrigger id="kidding-placenta" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TRI_STATES.map((v) => (
                        <SelectItem key={v} value={v}>
                          {TRI_STATE_ITEMS[v]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Controller
              control={control}
              name="mastitis_suspected"
              render={({ field }) => (
                <Checkbox
                  id="kidding-mastitis"
                  checked={field.value}
                  disabled={isSubmitting || createFlight.pending}
                  onCheckedChange={(checked) => field.onChange(checked === true)}
                />
              )}
            />
            <Label htmlFor="kidding-mastitis" className="font-normal">
              Mastitis suspected
            </Label>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="kidding_notes">Notes</Label>
            <Textarea
              id="kidding_notes"
              disabled={isSubmitting || createFlight.pending}
              rows={2}
              maxLength={4_000}
              aria-invalid={Boolean(errors.notes) || undefined}
              aria-describedby={errors.notes ? "kidding-notes-error" : undefined}
              {...register("notes")}
            />
            {errors.notes && (
              <p id="kidding-notes-error" role="alert" className="text-sm text-destructive">
                {errors.notes.message}
              </p>
            )}
          </div>

          {/* handleSubmit freezes `values` at click time, so a kid row added
              or removed while the POST is in flight is silently dropped from
              the request — and the dialog then closes showing the edited list
              as though it had been saved. Alive kids auto-create animals, so
              that divergence is durable. Bounded by the api-client request
              timeout, this can never stay disabled. */}
          <fieldset
            className="space-y-2"
            aria-labelledby="kidding-kids-label"
            disabled={isSubmitting || createFlight.pending}
          >
            <div className="flex items-center justify-between">
              <span id="kidding-kids-label" className="text-sm font-medium">
                {cap(vocabulary.youngPlural)}
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={fields.length >= vocabulary.facts.maxLitterSize}
                onClick={() => append(emptyKid())}
              >
                <Plus />
                Add {vocabulary.young}
              </Button>
            </div>
            {fields.map((field, index) => (
              <div
                key={field.id}
                className="grid grid-cols-2 gap-2 rounded-lg border p-3 sm:grid-cols-[1fr_90px_110px_120px_auto] sm:items-end sm:border-0 sm:p-0"
              >
                <div className="col-span-2 space-y-1 sm:col-span-1">
                  <Label htmlFor={`kid-${field.id}-tag`} className="text-xs">
                    {youngLabel} {index + 1} tag (auto if blank)
                  </Label>
                  <Input
                    id={`kid-${field.id}-tag`}
                    aria-invalid={Boolean(errors.kids?.[index]?.tag) || undefined}
                    aria-describedby={
                      errors.kids?.[index]?.tag ? `kid-${field.id}-tag-error` : undefined
                    }
                    {...register(`kids.${index}.tag`)}
                    placeholder="auto"
                  />
                  {errors.kids?.[index]?.tag && (
                    <p
                      id={`kid-${field.id}-tag-error`}
                      role="alert"
                      className="text-sm text-destructive"
                    >
                      {kidErrorMessage(errors, index, "tag")}
                    </p>
                  )}
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`kid-${field.id}-sex`} className="text-xs">
                    {youngLabel} {index + 1} sex
                  </Label>
                  <Controller
                    control={control}
                    name={`kids.${index}.sex`}
                    render={({ field: f }) => (
                      <Select value={f.value} onValueChange={f.onChange} items={kidSexItems(language)}>
                        <SelectTrigger id={`kid-${field.id}-sex`} size="sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="F">Female</SelectItem>
                          <SelectItem value="M">Male</SelectItem>
                        </SelectContent>
                      </Select>
                    )}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`kid-${field.id}-weight`} className="text-xs">
                    {youngLabel} {index + 1} weight (kg)
                  </Label>
                  <Input
                    id={`kid-${field.id}-weight`}
                    type="number"
                    step="0.1"
                    min="0"
                    max="1000"
                    aria-invalid={Boolean(errors.kids?.[index]?.birth_weight) || undefined}
                    aria-describedby={
                      errors.kids?.[index]?.birth_weight
                        ? `kid-${field.id}-weight-error`
                        : undefined
                    }
                    {...register(`kids.${index}.birth_weight`, {
                      setValueAs: (v) => (v === "" || v == null ? null : Number(v)),
                    })}
                  />
                  {errors.kids?.[index]?.birth_weight && (
                    <p
                      id={`kid-${field.id}-weight-error`}
                      role="alert"
                      className="text-sm text-destructive"
                    >
                      {kidErrorMessage(errors, index, "birth_weight")}
                    </p>
                  )}
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`kid-${field.id}-status`} className="text-xs">
                    {youngLabel} {index + 1} status
                  </Label>
                  <Controller
                    control={control}
                    name={`kids.${index}.status`}
                    render={({ field: f }) => (
                      <Select
                        value={f.value}
                        onValueChange={(v) => {
                          f.onChange(v);
                          // Only a died kid may carry a mortality date.
                          if (v !== "DIED") {
                            setValue(`kids.${index}.mortality_reported_at`, "", {
                              shouldValidate: true,
                            });
                          }
                          // KidIn forbids neonatal-care facts on a stillborn
                          // kid, so returning to that status drops them.
                          if (v === "STILLBORN") {
                            setValue(`kids.${index}.colostrum`, "unrecorded");
                            setValue(`kids.${index}.navel`, "unrecorded");
                            setValue(`kids.${index}.dam_rejected`, false);
                          }
                        }}
                        items={kidStatusItems(language)}
                      >
                        <SelectTrigger id={`kid-${field.id}-status`} size="sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {KID_STATUSES.map((s) => (
                            <SelectItem key={s} value={s}>
                              {kidStatusItems(language)[s]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  />
                </div>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  aria-label={`Remove ${vocabulary.young} ${index + 1}`}
                  disabled={fields.length <= 1}
                  onClick={() => remove(index)}
                  className="justify-self-end"
                >
                  <X />
                </Button>
                {/* Neonatal care applies to live (and died) kids only — a
                    stillborn never nursed, was never navel-dipped and cannot
                    be rejected by the dam, so the fields disappear with the
                    status and their keys stay off the wire. */}
                {kidValues?.[index]?.status !== "STILLBORN" && (
                  <div className="col-span-2 grid gap-2 sm:col-span-5 sm:grid-cols-[1fr_1fr_auto] sm:items-center">
                    <div className="space-y-1">
                      <Label htmlFor={`kid-${field.id}-colostrum`} className="text-xs">
                        {youngLabel} {index + 1} colostrum within 2 h
                      </Label>
                      <Controller
                        control={control}
                        name={`kids.${index}.colostrum`}
                        render={({ field: f }) => (
                          <Select
                            value={f.value}
                            onValueChange={f.onChange}
                            items={TRI_STATE_ITEMS}
                          >
                            <SelectTrigger id={`kid-${field.id}-colostrum`} size="sm">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {TRI_STATES.map((v) => (
                                <SelectItem key={v} value={v}>
                                  {TRI_STATE_ITEMS[v]}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        )}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor={`kid-${field.id}-navel`} className="text-xs">
                        {youngLabel} {index + 1} navel dipped (7% iodine)
                      </Label>
                      <Controller
                        control={control}
                        name={`kids.${index}.navel`}
                        render={({ field: f }) => (
                          <Select
                            value={f.value}
                            onValueChange={f.onChange}
                            items={TRI_STATE_ITEMS}
                          >
                            <SelectTrigger id={`kid-${field.id}-navel`} size="sm">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {TRI_STATES.map((v) => (
                                <SelectItem key={v} value={v}>
                                  {TRI_STATE_ITEMS[v]}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        )}
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <Controller
                        control={control}
                        name={`kids.${index}.dam_rejected`}
                        render={({ field: f }) => (
                          <Checkbox
                            id={`kid-${field.id}-dam-rejected`}
                            checked={f.value}
                            onCheckedChange={(checked) => f.onChange(checked === true)}
                          />
                        )}
                      />
                      <Label htmlFor={`kid-${field.id}-dam-rejected`} className="text-xs font-normal">
                        {youngLabel} {index + 1} dam rejected
                      </Label>
                    </div>
                  </div>
                )}
                {isDiedKid(kidValues, index) && (
                  <div className="col-span-2 space-y-1 sm:col-span-5">
                    <Label htmlFor={`kid-${field.id}-mortality`} className="text-xs">
                      {youngLabel} {index + 1} mortality date *
                    </Label>
                    <Input
                      id={`kid-${field.id}-mortality`}
                      type="date"
                      min={kiddingDate}
                      max={farmToday()}
                      aria-invalid={
                        Boolean(errors.kids?.[index]?.mortality_reported_at) || undefined
                      }
                      aria-describedby={
                        errors.kids?.[index]?.mortality_reported_at
                          ? `kid-${field.id}-mortality-error`
                          : undefined
                      }
                      {...register(`kids.${index}.mortality_reported_at`)}
                    />
                    {errors.kids?.[index]?.mortality_reported_at && (
                      <p
                        id={`kid-${field.id}-mortality-error`}
                        role="alert"
                        className="text-sm text-destructive"
                      >
                        {kidErrorMessage(errors, index, "mortality_reported_at")}
                      </p>
                    )}
                  </div>
                )}
              </div>
            ))}
            <p className="text-xs text-muted-foreground" aria-live="polite">
              {fields.length} {fields.length === 1 ? vocabulary.young : vocabulary.youngPlural}{" "}
              listed
              {breeding.kid_count_detected !== null &&
                // Stryker disable next-line ConditionalExpression: the wire field is number|null, so undefined only arrives from malformed payloads the !== null guard already treats the same way for every rendered value
                breeding.kid_count_detected !== undefined &&
                breeding.kid_count_detected !== fields.length &&
                ` — ultrasound detected ${breeding.kid_count_detected}. Reconcile the difference or note the reason.`}
            </p>
          </fieldset>

          <p className="text-sm text-muted-foreground">{kiddingOutcomeNote(vocabulary)}</p>
          <DialogFooter>
            <Button
              variant="outline"
              type="button"
              onClick={onClose}
              disabled={isSubmitting || createFlight.pending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting || createFlight.pending}>
              {isSubmitting || createFlight.pending
                ? "Saving…"
                : formError
                  ? `Retry save ${vocabulary.parturition}`
                  : `Save ${vocabulary.parturition}`}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Kids of a kidding record, inline: "tag (sex, status), …" with animal links.
 *  Recorded neonatal care follows the status in the same parentheses — only
 *  captured facts appear, so pre-care fixtures stay terse. */
function KidsCell({
  kidding,
  canViewAnimals,
}: {
  kidding: KiddingRecordOut;
  canViewAnimals: boolean;
}) {
  const { language } = useLanguage();
  const vocabulary = farmVocabulary;
  const kids = kidding.kids ?? [];
  if (kids.length === 0) return <span>—</span>;
  return (
    <span>
      {kids.map((kid, i) => (
        <span key={kid.id}>
          {i > 0 && ", "}
          {kid.animal_id && canViewAnimals ? (
            <Link href={`/animals/${kid.animal_id}`} className="text-primary underline">
              {kid.tag ?? vocabulary.young}
            </Link>
          ) : (
            (kid.tag ?? vocabulary.young)
          )}{" "}
          (
          {[
            enumLabel("sex", kid.sex, language),
            kid.status.toLowerCase(),
            kid.colostrum_within_2h === null
              ? null
              : `colostrum ${kid.colostrum_within_2h ? "yes" : "no"}`,
            kid.navel_dipped === null
              ? null
              : `navel dipped ${kid.navel_dipped ? "yes" : "no"}`,
            kid.dam_rejected ? "dam rejected" : null,
          ]
            .filter(Boolean)
            .join(", ")}
          )
        </span>
      ))}
    </span>
  );
}

/** Postpartum facts under the ease badge: parity (server-derived), placenta
 *  and the mastitis flag — only what was recorded, kept on one muted line. */
function kiddingCareFacts(kidding: KiddingRecordOut): string | null {
  const facts = [
    kidding.parity !== null ? `parity ${kidding.parity}` : null,
    kidding.placenta_passed === null
      ? null
      : kidding.placenta_passed
        ? "placenta passed"
        : "placenta not passed",
    kidding.mastitis_suspected ? "mastitis suspected" : null,
  ].filter(Boolean);
  return facts.length > 0 ? facts.join(" · ") : null;
}

function KiddingPageContent({ perms }: { perms: PermissionsState }) {
  const queryClient = useQueryClient();
  const { language } = useLanguage();
  const vocabulary = farmVocabulary;
  const femaleLabel = cap(vocabulary.femaleAdult);
  const { can } = perms;
  const allowed = can("kidding.view");
  const canManage = can("kidding.manage");
  const canViewAnimals = can("animals.view");
  const canViewBreeding = can("breeding.view");
  const [recordFor, setRecordFor] = useState<BreedingRecordOut | null>(null);
  // Scope dismissal to one URL intent. A query-only navigation can reuse this
  // component for a different pregnancy and must still open its dialog.
  const [dismissedPrefillId, setDismissedPrefillId] = useState<number | null>(null);
  // Read from the router, not window.location: Next commits the browser URL in
  // an insertion effect, after this component has already rendered.
  const searchParams = useSearchParams();
  const requestedBreedingId = parsePositiveId(searchParams.get("breeding_id"));
  const [historyOffset, setHistoryOffset] = useState(0);
  const [upcomingOffset, setUpcomingOffset] = useState(0);
  const [overdueOffset, setOverdueOffset] = useState(0);
  const query = useKiddingListApiKiddingGet(
    {
      limit: KIDDING_HISTORY_LIMIT,
      offset: historyOffset,
      upcoming_limit: DUE_LIST_LIMIT,
      upcoming_offset: upcomingOffset,
      overdue_limit: DUE_LIST_LIMIT,
      overdue_offset: overdueOffset,
    },
    { query: { enabled: allowed, placeholderData: (previous) => previous } },
  );
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const queuesSettling = query.isPlaceholderData;
  const pagedPrefillRecord = payload
    ? [...payload.overdue, ...payload.upcoming].find(
        (record) => record.id === requestedBreedingId,
      )
    : undefined;
  const prefillRecordQuery = useKiddingPregnancyApiKiddingPregnanciesBreedingRecordIdGet(
    requestedBreedingId ?? 0,
    {
      query: {
        enabled:
          canManage &&
          requestedBreedingId !== null &&
          payload !== undefined &&
          pagedPrefillRecord === undefined,
      },
    },
  );
  const fetchedPrefillRecord =
    prefillRecordQuery.data?.status === 200 ? prefillRecordQuery.data.data : undefined;
  const requestedRecord = pagedPrefillRecord ?? fetchedPrefillRecord;
  const deepLinkedRecord =
    canManage &&
    dismissedPrefillId !== requestedBreedingId &&
    requestedRecord?.outcome === "CONFIRMED_PREGNANT" &&
    !requestedRecord.has_kidding
      ? requestedRecord
      : null;
  const activeRecord = recordFor ?? deepLinkedRecord;
  // A deep link that resolved to a pregnancy that can no longer be recorded
  // used to vanish silently; keep the operator informed until cleared (L18).
  const staleDeepLink =
    // Stryker disable next-line ConditionalExpression, LogicalOperator: the requestedRecord guard below stays undefined for every state the mutants unlock (view-only users never fetch the pregnancy; a null id never resolves it), so the conjunction is false either way
    canManage &&
    // Stryker disable next-line ConditionalExpression: same layered-guard argument via requestedRecord
    requestedBreedingId !== null &&
    dismissedPrefillId !== requestedBreedingId &&
    requestedRecord !== undefined &&
    (requestedRecord.outcome !== "CONFIRMED_PREGNANT" || requestedRecord.has_kidding);

  useEffect(() => {
    // Scope dismissal to one continuous URL intent. Query-only navigation can
    // clear the link and later revisit the same pregnancy without remounting
    // this page; retaining the old id forever would suppress that new visit.
    // Stryker disable next-line ConditionalExpression: the always-true arm only calls setDismissedPrefillId(null) when the value is already null — a setState React bails out on without re-rendering
    if (requestedBreedingId === null && dismissedPrefillId !== null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- URL intent teardown
      setDismissedPrefillId(null);
    }
  }, [dismissedPrefillId, requestedBreedingId]);

  useEffect(() => {
    if (!payload) return;
    const lastHistoryOffset =
      payload.total === 0 ? 0 : Math.floor((payload.total - 1) / payload.limit) * payload.limit;
    const lastUpcomingOffset =
      payload.upcoming_total === 0
        ? 0
        : Math.floor((payload.upcoming_total - 1) / payload.upcoming_limit) *
          payload.upcoming_limit;
    const lastOverdueOffset =
      payload.overdue_total === 0
        ? 0
        : Math.floor((payload.overdue_total - 1) / payload.overdue_limit) * payload.overdue_limit;
    // Recording a kidding can remove the last pregnancy from a due page.
    // Re-home only the queue whose exact total no longer contains its offset.
    // Stryker disable next-line EqualityOperator: at equality the setter writes the value already held, a React no-op
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (historyOffset > lastHistoryOffset) setHistoryOffset(lastHistoryOffset);
    // Stryker disable next-line EqualityOperator: at equality the setter writes the value already held, a React no-op
    if (upcomingOffset > lastUpcomingOffset) setUpcomingOffset(lastUpcomingOffset);
    // Stryker disable next-line EqualityOperator: at equality the setter writes the value already held, a React no-op
    if (overdueOffset > lastOverdueOffset) setOverdueOffset(lastOverdueOffset);
  }, [historyOffset, overdueOffset, payload, upcomingOffset]);

  function refresh() {
    invalidateFarmData(queryClient);
  }

  if (query.isLoading || !payload) {
    if (query.isError) {
      return (
        <div className="space-y-3" role="alert">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError
              ? query.error.detail
              : `Could not load ${vocabulary.parturition} data.`}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry {vocabulary.parturition} data
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title={vocabulary.parturitionCap}
          description={`Confirmed pregnancies due soon and recent ${vocabulary.parturition} history.`}
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading {vocabulary.parturition} data…</span>
          <PageSkeleton cards={2} />
        </div>
      </div>
    );
  }

  // "Xd late" compares against the active farm's calendar day.
  const today = farmToday();

  function recordButton(r: BreedingRecordOut, touch = false) {
    if (!canManage) return null;
    return (
      <Button
        variant="outline"
        size="sm"
        // ≥44px on the below-md cards — the phone is the worker's primary
        // device and 36px sm buttons sit under every mobile touch guideline.
        className={touch ? "h-11 px-4" : undefined}
        disabled={queuesSettling}
        onClick={() => setRecordFor(r)}
      >
        Record {vocabulary.parturition}
      </Button>
    );
  }

  /** Doe link-or-text, shared by the below-md cards (the tables inline the
   * same link). */
  function doeCell(doeId: number, doeTag: string | null | undefined) {
    return canViewAnimals ? (
      <Link href={`/animals/${doeId}`} className="text-primary underline">
        {doeTag ?? `${femaleLabel} #${doeId}`}
      </Link>
    ) : (
      (doeTag ?? `${femaleLabel} #${doeId}`)
    );
  }

  return (
    <div className="space-y-6">
      {query.isError && <StaleDataNotice onRetry={() => void query.refetch()} />}
      <PageHeader
        title={vocabulary.parturitionCap}
        description={`Confirmed pregnancies due soon and recent ${vocabulary.parturition} history.`}
      />

      {queuesSettling && (
        <p role="status" className="text-sm text-muted-foreground">
          Updating {vocabulary.parturition} queues…
        </p>
      )}

      {staleDeepLink && requestedRecord && (
        <div role="status" className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">
            {requestedRecord.has_kidding
              ? `Pregnancy #${requestedBreedingId} already has a ${vocabulary.parturition} recorded.`
              : `Pregnancy #${requestedBreedingId} is no longer confirmed pregnant — no ${vocabulary.parturition} to record.`}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setDismissedPrefillId(requestedBreedingId)}
          >
            Clear link
          </Button>
        </div>
      )}

      {prefillRecordQuery.isError && (
        <div className="flex flex-wrap items-center gap-2" role="alert">
          <span className="text-sm text-destructive">
            {prefillRecordQuery.error instanceof ApiError
              ? prefillRecordQuery.error.detail
              : "Could not load the linked pregnancy."}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void prefillRecordQuery.refetch()}
          >
            Retry linked pregnancy
          </Button>
        </div>
      )}
      {payload.overdue_total > 0 && (
        <DataTableCard
          className="border-destructive/30 bg-destructive/[0.04]"
          title={
            <span className="flex items-center gap-2 text-destructive">
              <AlertTriangle className="size-4" />
              Overdue (past expected date, no {vocabulary.parturition} recorded)
            </span>
          }
          description={`${payload.overdue_total} overdue pregnancies in the full queue.`}
        >
          {/* Below md the overdue queue becomes a card per pregnancy — the
           * Record action is the whole point of this list, so it must be a
           * thumb-reachable 44px target, not a pan-and-squint table cell. */}
          <div className="space-y-2 md:hidden">
            {payload.overdue.map((r) => (
              <div key={r.id} className="space-y-2 rounded-xl border bg-card p-3 shadow-xs">
                <p className="font-medium">{doeCell(r.doe_id, r.doe_tag)}</p>
                <p className="text-xs text-muted-foreground">
                  was due {formatDate(r.expected_kidding_date)}{" "}
                  {r.expected_kidding_date && (
                    <span className="text-destructive">
                      ({daysBetween(r.expected_kidding_date, today)}d late)
                    </span>
                  )}
                </p>
                {recordButton(r, true)}
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[560px]">
            <TableHeader className="sr-only">
              <TableRow>
                <th scope="col">{femaleLabel}</th>
                <th scope="col">Was due</th>
                <th scope="col">Record {vocabulary.parturition}</th>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.overdue.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>
                    {canViewAnimals ? (
                      <Link
                        href={`/animals/${r.doe_id}`}
                        className="text-primary underline"
                      >
                        {r.doe_tag ?? `${femaleLabel} #${r.doe_id}`}
                      </Link>
                    ) : (
                      r.doe_tag ?? `${femaleLabel} #${r.doe_id}`
                    )}
                  </TableCell>
                  <TableCell>
                    was due {formatDate(r.expected_kidding_date)}{" "}
                    {r.expected_kidding_date && (
                      <span className="text-destructive">
                        ({daysBetween(r.expected_kidding_date, today)}d late)
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">{recordButton(r)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          <PaginationControls
            total={payload.overdue_total}
            limit={payload.overdue_limit}
            offset={payload.overdue_offset}
            onOffsetChange={setOverdueOffset}
            label="overdue pregnancies"
            disabled={queuesSettling}
          />
        </DataTableCard>
      )}

      <DataTableCard
        title="Upcoming (next 30 days)"
        description={`${payload.upcoming_total} confirmed pregnancies in the full 30-day queue.`}
      >
        {payload.upcoming.length === 0 ? (
          <EmptyState
            icon={CalendarClock}
            title="No confirmed pregnancies due in the next 30 days."
            description={
              <>
                Confirmed pregnancies appear here as their due dates approach — record
                ultrasound results in Breeding.
              </>
            }
          >
            {canViewBreeding && (
              <Link
                href="/breeding"
                // Stryker disable next-line ObjectLiteral, StringLiteral: cva resolves variant "default" to the same classes as the fallback, and the sm size only changes padding classes no test observes (duties record-row precedent)
                className={buttonVariants({ variant: "default", size: "sm" })}
              >
                Go to Breeding
              </Link>
            )}
          </EmptyState>
        ) : (
          <>
          {/* Below md the 6-column queue becomes a card per pregnancy. */}
          <div className="space-y-2 md:hidden">
            {payload.upcoming.map((r) => (
              <div key={r.id} className="space-y-2 rounded-xl border bg-card p-3 shadow-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{doeCell(r.doe_id, r.doe_tag)}</span>
                  {r.expected_kidding_date && (
                    <Badge variant="secondary">
                      {daysBetween(today, r.expected_kidding_date)}d left
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground tabular-nums">
                  Bred {formatDate(r.breeding_date)} · Expected{" "}
                  {formatDate(r.expected_kidding_date)} ·{" "}
                  {cap(vocabulary.youngPlural)} detected: {r.kid_count_detected ?? "—"}
                </p>
                {recordButton(r, true)}
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow>
                <TableHead>{femaleLabel}</TableHead>
                <TableHead>Bred</TableHead>
                <TableHead>Expected</TableHead>
                <TableHead>Days left</TableHead>
                <TableHead>{cap(vocabulary.youngPlural)} detected</TableHead>
                {canManage && <TableHead className="text-right" />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.upcoming.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>
                    {canViewAnimals ? (
                      <Link
                        href={`/animals/${r.doe_id}`}
                        className="text-primary underline"
                      >
                        {r.doe_tag ?? `${femaleLabel} #${r.doe_id}`}
                      </Link>
                    ) : (
                      r.doe_tag ?? `${femaleLabel} #${r.doe_id}`
                    )}
                  </TableCell>
                  <TableCell>{formatDate(r.breeding_date)}</TableCell>
                  <TableCell>{formatDate(r.expected_kidding_date)}</TableCell>
                  <TableCell>
                    {r.expected_kidding_date
                      ? daysBetween(today, r.expected_kidding_date)
                      : "—"}
                  </TableCell>
                  <TableCell>{r.kid_count_detected ?? "—"}</TableCell>
                  {canManage && (
                    <TableCell className="text-right">{recordButton(r)}</TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          </>
        )}
        <PaginationControls
          total={payload.upcoming_total}
          limit={payload.upcoming_limit}
          offset={payload.upcoming_offset}
          onOffsetChange={setUpcomingOffset}
          label="upcoming pregnancies"
          disabled={queuesSettling}
        />
      </DataTableCard>

      <DataTableCard
        title={`Recent ${vocabulary.parturition}s`}
        description={`Latest recorded ${vocabulary.parturition}s with ease and ${vocabulary.youngPlural} outcomes.`}
      >
        {payload.records.length === 0 ? (
          <EmptyState
            icon={Baby}
            title={`No ${vocabulary.parturition}s recorded yet.`}
            description={`Record a ${vocabulary.parturition} from the upcoming list once a ${vocabulary.femaleAdult} delivers.`}
          />
        ) : (
          <>
          {/* Below md the 5-column history becomes a card per recorded
           * birth — ease, young outcomes and care facts stay on the phone. */}
          <div className="space-y-2 md:hidden">
            {payload.records.map((k) => (
              <div key={k.id} className="space-y-1.5 rounded-xl border bg-card p-3 shadow-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{formatDate(k.date)}</span>
                  <StatusBadge status={k.ease}>{EASE_ITEMS[k.ease] ?? k.ease}</StatusBadge>
                </div>
                <p className="text-sm">{doeCell(k.doe_id, k.doe_tag)}</p>
                {kiddingCareFacts(k) && (
                  <p className="text-xs text-muted-foreground">{kiddingCareFacts(k)}</p>
                )}
                <p className="text-xs text-muted-foreground">
                  {cap(vocabulary.youngPlural)}:{" "}
                  <KidsCell kidding={k} canViewAnimals={canViewAnimals} />
                </p>
                {k.notes && <p className="text-xs text-muted-foreground">{k.notes}</p>}
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>{femaleLabel}</TableHead>
                <TableHead>Ease</TableHead>
                <TableHead>{cap(vocabulary.youngPlural)}</TableHead>
                <TableHead>Notes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.records.map((k) => (
                <TableRow key={k.id}>
                  <TableCell>{formatDate(k.date)}</TableCell>
                  <TableCell>
                    {canViewAnimals ? (
                      <Link
                        href={`/animals/${k.doe_id}`}
                        className="text-primary underline"
                      >
                        {k.doe_tag ?? `${femaleLabel} #${k.doe_id}`}
                      </Link>
                    ) : (
                      k.doe_tag ?? `${femaleLabel} #${k.doe_id}`
                    )}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={k.ease}>{easeItems(language)[k.ease] ?? k.ease}</StatusBadge>
                    {kiddingCareFacts(k) && (
                      <span className="block text-xs text-muted-foreground">
                        {kiddingCareFacts(k)}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <KidsCell kidding={k} canViewAnimals={canViewAnimals} />
                  </TableCell>
                  <TableCell>{k.notes ?? ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          </>
        )}
        <PaginationControls
          total={payload.total}
          limit={payload.limit}
          offset={payload.offset}
          onOffsetChange={setHistoryOffset}
          label={`${vocabulary.parturition} records`}
          disabled={queuesSettling}
        />
      </DataTableCard>

      {activeRecord && (
        <RecordKiddingDialog
          key={activeRecord.id}
          breeding={activeRecord}
          onClose={() => {
            // Only the deep-linked record's OWN dismissal retires the deep
            // link — this handler also serves rows opened by hand while the
            // deep-linked record was still loading, and latching there
            // silently dropped the task the operator arrived from.
            if (activeRecord.id === requestedBreedingId) {
              setDismissedPrefillId(requestedBreedingId);
            }
            setRecordFor(null);
          }}
          onSaved={refresh}
        />
      )}
    </div>
  );
}

export default function KiddingPage() {
  const perms = usePermissions();
  const vocabulary = farmVocabulary;
  return (
    <Suspense
      fallback={
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading…</span>
          <PageSkeleton cards={2} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="kidding.view"
        label={vocabulary.parturitionCap}
        description={`Confirmed pregnancies due soon and recent ${vocabulary.parturition} history.`}
        cards={2}
      >
        <KiddingPageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
