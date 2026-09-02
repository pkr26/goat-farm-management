"use client";

/** Breeding records — parity with v1's breeding/list.html (+ new/ultrasound as dialogs). */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { CircleCheckBig, Clock, HeartHandshake, Plus } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Controller, useForm } from "react-hook-form";
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
import { ApiError, farmScopeEpochValue } from "@/lib/api-client";
import { enumLabel } from "@/lib/enum-labels";
import { farmToday, formatDate } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { useFarmType } from "@/hooks/use-farm-type";
import { farmVocabulary, type FarmVocabulary } from "@/lib/farm-vocabulary";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { PermissionsError } from "@/components/permissions-error";
import { PageSkeleton } from "@/components/skeletons";

/** Deep-link ids arrive as raw query strings; anything that is not a positive
 * safe integer is ignored. */
function parsePositiveId(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function localToday(): string {
  return farmToday();
}

/** Whole days from `from` to `to` (both YYYY-MM-DD), timezone-safe. */
function daysBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 86400000);
}

/** Mirrors services/breeding.py: standing heat ends within ~1 day of the
 * service and the next heat cannot return before ~18 days, so a not-pregnant
 * result dated strictly between them reports an unobservable event. */
const STANDING_HEAT_DAYS = 1;
const EARLIEST_RETURN_TO_HEAT_DAYS = 18;

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

/** Display-case a vocabulary noun for label positions ("doe" → "Doe"). */
function cap(noun: string): string {
  return noun.charAt(0).toUpperCase() + noun.slice(1);
}

/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw enum value in the closed trigger. Labels come
 * from the shared enum-labels map (same vocabulary the record rows render),
 * so the write path never shows SCREAMING_SNAKE codes either. */
const LOSS_CAUSE_ITEMS: Record<string, string> = Object.fromEntries(
  Object.values(PregnancyLossInCause).map((value) => [value, enumLabel("lossCause", value)]),
);

function OutcomeBadge({ outcome }: { outcome: string }) {
  // The shared chip system already maps breeding outcomes to semantic tones
  // (CONFIRMED_PREGNANT/PREGNANT → success, FAILED → destructive, the rest
  // neutral) and humanises the code — no local tint table belongs here.
  return <StatusBadge status={outcome} />;
}

const breedingSchema = (vocabulary: FarmVocabulary) =>
  z
  .object({
    doe_id: z.string().min(1, `Select a ${vocabulary.femaleAdult}`),
    // Buck is required for natural service; AI methods name a semen bull.
    buck_id: z.string(),
    method: z.enum(["NATURAL", "AI", "AI_SEXED"]),
    semen_sire_name: z.string().trim().max(120).optional(),
    breeding_date: z
      .string()
      .regex(/^\d{4}-\d{2}-\d{2}$/, "Pick a valid date")
      .refine((s) => s <= localToday(), "Date can't be in the future"),
  })
  .superRefine((values, ctx) => {
    if (values.method === "NATURAL" && !values.buck_id) {
      ctx.addIssue({
        code: "custom",
        path: ["buck_id"],
        message: `Select a ${vocabulary.maleAdult} for a natural service`,
      });
    }
    if (values.method !== "NATURAL" && values.semen_sire_name?.trim() === undefined) {
      return; // semen identity is optional; the straw may not record it
    }
  });
type BreedingValues = z.infer<ReturnType<typeof breedingSchema>>;

function breedingDefaults(): BreedingValues {
  return {
    doe_id: "",
    buck_id: "",
    method: "NATURAL",
    semen_sire_name: "",
    breeding_date: localToday(),
  };
}

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
  const vocabulary = farmVocabulary(useFarmType());
  const femaleLabel = cap(vocabulary.femaleAdult);
  const maleLabel = cap(vocabulary.maleAdult);
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
  const {
    control,
    register,
    handleSubmit,
    reset,
    resetField,
    watch,
    formState: { errors, isSubmitting, dirtyFields },
  } = useForm<BreedingValues>({
    resolver: zodResolver(useMemo(() => breedingSchema(vocabulary), [vocabulary])),
    defaultValues: breedingDefaults(),
  });
  const method = watch("method");

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
      const requestFarmEpoch = farmScopeEpochValue();
      try {
        await createMutation.mutateAsync({
          data: {
            doe_id: Number(values.doe_id),
            ...(values.method === "NATURAL" ? { buck_id: Number(values.buck_id) } : {}),
            ...(values.method !== "NATURAL"
              ? { method: values.method, semen_sire_name: values.semen_sire_name?.trim() || null }
              : {}),
            breeding_date: values.breeding_date,
          },
        });
        // The write belongs to the farm it was addressed to; the completion
        // must not toast/close/invalidate on a different farm's UI (M-2).
        if (farmScopeEpochValue() !== requestFarmEpoch) return;
        toast.success("Breeding saved.");
        reset(breedingDefaults());
        // Clear the lifted labels alongside the form values, or the next
        // breeding would open showing the animals this one used.
        setDoeOption(null);
        setBuckOption(null);
        onOpenChange(false);
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
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && (isSubmitting || createFlight.pending)) return;
        if (!nextOpen) setFormError(null);
        onOpenChange(nextOpen);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add breeding</DialogTitle>
          <DialogDescription>
            A pregnancy-check task is auto-created ({vocabulary.facts.pregnancyCheckDays} days
            after the service{vocabulary.dairy ? "; buffaloes are bred back during lactation" : ""}
            ).
          </DialogDescription>
        </DialogHeader>
        {candidateAvailability === null ? (
          <p role="alert" className="text-destructive">
            Candidate availability is unavailable. Refresh the breeding records before adding a
            breeding.
          </p>
        ) : eligibleDoeCount === 0 ? (
          <p className="text-muted-foreground">
            No breeding-ready females right now. {vocabulary.breedingGateCopy}
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
              <Label htmlFor="breeding-doe">{femaleLabel} *</Label>
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
                    placeholder={`Select ${vocabulary.femaleAdult}`}
                    dialogTitle={`Choose a breeding-ready ${vocabulary.femaleAdult}`}
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
              <Label>Method *</Label>
              <div
                role="radiogroup"
                aria-label="Breeding method"
                className="grid gap-2 sm:grid-cols-3"
              >
                {(
                  [
                    ["NATURAL", enumLabel("method", "NATURAL"), `Herd ${vocabulary.maleAdult}`],
                    ["AI", enumLabel("method", "AI"), "Conventional semen"],
                    [
                      "AI_SEXED",
                      enumLabel("method", "AI_SEXED"),
                      `~90% female ${vocabulary.youngPlural}`,
                    ],
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
            </div>
            {method === "NATURAL" ? (
              <div className="space-y-1.5">
                <Label htmlFor="breeding-buck">{maleLabel} *</Label>
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
                      placeholder={`Select ${vocabulary.maleAdult}`}
                      dialogTitle={`Choose an active ${vocabulary.maleAdult}`}
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
                    No eligible {vocabulary.maleAdult}s are available. {maleLabel}s on hold, in
                    quarantine, or otherwise restricted cannot be selected.
                  </p>
                )}
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label htmlFor="breeding-semen-sire">Semen {vocabulary.maleAdult} (optional)</Label>
                <Input
                  id="breeding-semen-sire"
                  maxLength={120}
                  placeholder={`${cap(vocabulary.maleAdult)} name / straw code${vocabulary.dairy ? ", e.g. Karanvir 999" : ""}`}
                  aria-describedby="breeding-semen-sire-hint"
                  {...register("semen_sire_name")}
                />
                <p id="breeding-semen-sire-hint" className="text-xs text-muted-foreground">
                  {vocabulary.dairy
                    ? "The sire on the straw — verifiable daughters' yield >3,000 kg/lactation."
                    : `The sire named on the straw used for this ${vocabulary.femaleAdult}.`}
                </p>
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="breeding_date">Breeding date *</Label>
              <Input
                id="breeding_date"
                type="date"
                max={localToday()}
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
                {isSubmitting || createFlight.pending
                  ? "Saving…"
                  : formError
                    ? "Retry save breeding"
                    : "Save breeding"}
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
  const vocabulary = farmVocabulary(useFarmType());
  // A diagnostic outcome is never pre-filled: pre-checking "pregnant" lets an
  // operator who trusts the form save a negative scan as a confirmed pregnancy,
  // which moves the doe to PREGNANCY_EARLY and spawns pre-kidding follow-up
  // tasks (record_ultrasound_result). The checkbox therefore always starts
  // unchecked and the twins kid-count default only appears once the operator
  // explicitly checks it.
  const [pregnant, setPregnant] = useState(false);
  const [kidCount, setKidCount] = useState("");
  const [resultDate, setResultDate] = useState(localToday());
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
  const resultDateError = !resultDate
    ? "Result date is required"
    : resultDate < earliestResultDate
      ? `Result date cannot be before ${formatDate(earliestResultDate)}`
      : resultDate > localToday()
        ? "Result date can't be in the future"
        : negativeResultGapDays !== null &&
            negativeResultGapDays > STANDING_HEAT_DAYS &&
            negativeResultGapDays < EARLIEST_RETURN_TO_HEAT_DAYS
          ? `A not-pregnant result ${negativeResultGapDays} days after service is not observable — record it on the service day or the day after, or from day ${EARLIEST_RETURN_TO_HEAT_DAYS} (return to heat).`
          : null;
  // Species litter cap (goat scans reach 4; buffalo cap at 2) mirrors
  // backend SpeciesProfile.max_litter_size — the server 422s past the cap.
  const detectedOptions = Array.from(
    { length: vocabulary.facts.maxLitterSize },
    (_, index) => String(index + 1),
  );
  const kidCountError = pregnant && !detectedOptions.includes(kidCount)
    ? `Select the detected ${vocabulary.young} count`
    : null;

  async function onSubmit() {
    if (resultDateError || kidCountError || saveLock.current) return;
    saveLock.current = true;
    setSaving(true);
    setFormError(null);
    const requestFarmEpoch = farmScopeEpochValue();
    try {
      await mutation.mutateAsync({
        recordId: record.id,
        data: {
          pregnant,
          date: resultDate,
          kid_count: pregnant ? Number(kidCount) : null,
        },
      });
      if (farmScopeEpochValue() !== requestFarmEpoch) return;
      toast.success("Ultrasound result saved.");
      onClose();
      onSaved();
    } catch (err) {
      const message = errorText(err);
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
          <DialogTitle>Ultrasound result</DialogTitle>
          <DialogDescription>
            {cap(vocabulary.femaleAdult)} {record.doe_tag ?? `#${record.doe_id}`} · bred{" "}
            {formatDate(record.breeding_date)} by{" "}
            {record.buck_tag ?? `#${record.buck_id}`}
            {record.ultrasound_date
              ? ` · planned scan ${formatDate(record.ultrasound_date)}`
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
            <Label htmlFor={`ultrasound-result-date-${record.id}`}>Result date *</Label>
            <Input
              id={`ultrasound-result-date-${record.id}`}
              type="date"
              min={earliestResultDate}
              max={localToday()}
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
              Planned check: {formatDate(record.ultrasound_date)}. A pregnant result needs that
              scan. A not-pregnant one is recordable on the service day or the day after (the
              service was watched and failed), or from day {EARLIEST_RETURN_TO_HEAT_DAYS} onwards
              ({vocabulary.femaleAdult} back in heat) — not in the gap between, where neither is
              observable. Use the actual historical date for backdated entry.
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
                // Goat parity norms to twins; a buffalo pregnancy is a single
                // calf unless twins are detected.
                setKidCount(selected ? (vocabulary.dairy ? "1" : "2") : "");
              }}
            />
            <Label htmlFor="pregnant">Pregnant — confirmed</Label>
          </div>
          {pregnant && (
            <div className="space-y-1.5">
              <Label htmlFor={`ultrasound-kid-count-${record.id}`}>
                {cap(vocabulary.young)} count detected
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
              {kidCountError && (
                <p role="alert" className="text-sm text-destructive">
                  {kidCountError}
                </p>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            onClick={onSubmit}
            disabled={saving || Boolean(resultDateError) || Boolean(kidCountError)}
          >
            {saving ? "Saving…" : formError ? "Retry save result" : "Save result"}
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
  const vocabulary = farmVocabulary(useFarmType());
  const earliestLossDate =
    record.ultrasound_result_date && record.ultrasound_result_date > record.breeding_date
      ? record.ultrasound_result_date
      : record.breeding_date;
  const [lossDate, setLossDate] = useState(localToday());
  const [cause, setCause] = useState<PregnancyLossInCause>(PregnancyLossInCause.UNKNOWN);
  const [notes, setNotes] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const lossDateError = !lossDate
    ? "Loss date is required"
    : lossDate < earliestLossDate
      ? `Loss date cannot be before ${formatDate(earliestLossDate)}`
      : lossDate > localToday()
        ? "Loss date can't be in the future"
        : null;
  const notesError = notes.length > 4_000 ? "Notes cannot exceed 4000 characters" : null;
  const saving = mutation.isPending || saveFlight.pending;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (lossDateError || notesError) return;
    await saveFlight.run(async () => {
      setFormError(null);
      const requestFarmEpoch = farmScopeEpochValue();
      try {
        await mutation.mutateAsync({
          recordId: record.id,
          data: {
            loss_date: lossDate,
            cause,
            notes: notes.trim() || null,
          },
        });
        if (farmScopeEpochValue() !== requestFarmEpoch) return;
        toast.success("Pregnancy loss recorded.");
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
    <Dialog open onOpenChange={(open) => !open && !saving && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Record pregnancy loss</DialogTitle>
          <DialogDescription>
            {cap(vocabulary.femaleAdult)} {record.doe_tag ?? `#${record.doe_id}`} · bred{" "}
            {formatDate(record.breeding_date)}. This closes the pregnancy and retains an auditable
            reason.
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
            <Label htmlFor={`pregnancy-loss-date-${record.id}`}>Loss date *</Label>
            <Input
              id={`pregnancy-loss-date-${record.id}`}
              type="date"
              min={earliestLossDate}
              max={localToday()}
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
            <Label htmlFor={`pregnancy-loss-cause-${record.id}`}>Cause *</Label>
            <Select
              value={cause}
              onValueChange={(value) => setCause(value as PregnancyLossInCause)}
              items={LOSS_CAUSE_ITEMS}
            >
              <SelectTrigger id={`pregnancy-loss-cause-${record.id}`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.values(PregnancyLossInCause).map((value) => (
                  <SelectItem key={value} value={value}>
                    {enumLabel("lossCause", value)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`pregnancy-loss-notes-${record.id}`}>Notes</Label>
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
              Cancel
            </Button>
            <Button
              type="submit"
              variant="destructive"
              disabled={saving || Boolean(lossDateError) || Boolean(notesError)}
            >
              {saving ? "Recording…" : formError ? "Retry record loss" : "Record pregnancy loss"}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function BreedingPageContent() {
  const queryClient = useQueryClient();
  const vocabulary = farmVocabulary(useFarmType());
  const femaleLabel = cap(vocabulary.femaleAdult);
  const maleLabel = cap(vocabulary.maleAdult);
  const { can, loading: permsLoading, isError: permsError , refetch: permsRefetch } = usePermissions();
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

  if (permsLoading) {
    return (
      <div className="space-y-6" role="status" aria-live="polite">
        <span className="sr-only">Loading…</span>
        <PageHeader
          title="Breeding"
          description="Breeding records, ultrasound checks and pregnancy outcomes."
        />
        <PageSkeleton stats={3} cards={1} />
      </div>
    );
  }
  if (permsError) {
    return (
      <PermissionsError onRetry={() => void permsRefetch()} />
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
            {query.error instanceof ApiError
              ? query.error.detail
              : "Could not load breeding records."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry breeding records
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title="Breeding"
          description="Breeding records, ultrasound checks and pregnancy outcomes."
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading breeding records…</span>
          <PageSkeleton stats={3} cards={1} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Breeding"
        description="Breeding records, ultrasound checks and pregnancy outcomes."
        actions={
          canManage ? (
            <Button onClick={() => setNewOpen(true)}>
              <Plus />
              Add breeding
            </Button>
          ) : undefined
        }
      />

      {staleDeepLink && requestedRecord && (
        <div role="status" className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">
            Ultrasound record #{requestedUltrasoundId} (
            {requestedRecord.outcome.replace(/_/g, " ").toLowerCase()}) is not awaiting a
            result — nothing to record.
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setDismissedPrefillId(requestedUltrasoundId)}
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
              : "Could not load the linked ultrasound record."}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void prefillRecordQuery.refetch()}
          >
            Retry ultrasound record
          </Button>
        </div>
      )}

      {payload.records.length === 0 ? (
        <EmptyState
          icon={HeartHandshake}
          title="No breeding records yet."
          description={`Add a breeding to start tracking ultrasound checks and expected ${vocabulary.parturition} dates.`}
        >
          {canManage && (
            <Button size="sm" onClick={() => setNewOpen(true)}>
              <Plus />
              Add breeding
            </Button>
          )}
        </EmptyState>
      ) : (
        <DataTableCard
          title="Breeding records"
          description={`Ultrasound is due ${vocabulary.facts.pregnancyCheckDays} days after breeding; confirmed pregnancies get an expected ${vocabulary.parturition} date.`}
        >
          <Table className="min-w-[900px]">
            <TableHeader>
              <TableRow>
                <TableHead>Bred</TableHead>
                <TableHead>{femaleLabel}</TableHead>
                <TableHead>{maleLabel}</TableHead>
                <TableHead>Cycle</TableHead>
                <TableHead>Ultrasound</TableHead>
                <TableHead>{cap(vocabulary.youngPlural)}</TableHead>
                <TableHead>Expected {vocabulary.parturition}</TableHead>
                <TableHead>Outcome</TableHead>
                {canManage && <TableHead className="text-right">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.records.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>{formatDate(r.breeding_date)}</TableCell>
                  <TableCell>
                    {canViewAnimals ? (
                      <Link href={`/animals/${r.doe_id}`} className="text-primary underline">
                        {r.doe_tag ?? `${femaleLabel} #${r.doe_id}`}
                      </Link>
                    ) : (
                      r.doe_tag ?? `${femaleLabel} #${r.doe_id}`
                    )}
                  </TableCell>
                  <TableCell>
                    {canViewAnimals ? (
                      <Link href={`/animals/${r.buck_id}`} className="text-primary underline">
                        {r.buck_tag ?? `${maleLabel} #${r.buck_id}`}
                      </Link>
                    ) : (
                      r.buck_tag ?? `${maleLabel} #${r.buck_id}`
                    )}
                  </TableCell>
                  <TableCell>{r.heat_cycle_number}</TableCell>
                  <TableCell>
                    {r.ultrasound_done ? (
                      <span className="inline-flex items-center gap-1 text-success">
                        <CircleCheckBig aria-hidden="true" className="size-3.5" />
                        done
                      </span>
                    ) : r.ultrasound_date ? (
                      <span className="inline-flex items-center gap-1 text-warning-tint-foreground">
                        <Clock aria-hidden="true" className="size-3.5" />
                        due {formatDate(r.ultrasound_date)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>{r.kid_count_detected ?? "—"}</TableCell>
                  <TableCell>{formatDate(r.expected_kidding_date)}</TableCell>
                  <TableCell>
                    <OutcomeBadge outcome={r.outcome} />
                    {r.outcome === "ABORTED" && r.loss_date && (
                      <div className="mt-1 text-xs text-muted-foreground">
                        <p>
                          {formatDate(r.loss_date)} ·{" "}
                          {enumLabel("lossCause", r.loss_cause ?? "UNKNOWN")}
                        </p>
                        {r.loss_notes && (
                          <details>
                            <summary className="cursor-pointer">Loss notes</summary>
                            <p className="max-w-80 whitespace-pre-wrap break-words">{r.loss_notes}</p>
                          </details>
                        )}
                      </div>
                    )}
                  </TableCell>
                  {canManage && (
                    <TableCell className="text-right">
                      <div className="flex flex-wrap items-center justify-end gap-2">
                      {/* A not-pregnant result is recordable before the planned
                          scan (doe back in heat), so the action stays open for
                          every PENDING record; the plan is only a hint. */}
                      {r.outcome === "PENDING" && r.ultrasound_date && r.ultrasound_date > localToday() && (
                        <span className="text-xs text-muted-foreground">
                          Scan planned {formatDate(r.ultrasound_date)}
                        </span>
                      )}
                      {r.outcome === "PENDING" && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setUltrasoundFor(r)}
                        >
                          Ultrasound result
                        </Button>
                      )}
                      {r.outcome === "CONFIRMED_PREGNANT" && !r.has_kidding && (
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => setLossFor(r)}
                        >
                          Record loss
                        </Button>
                      )}
                      {r.has_kidding && (
                        <span className="text-muted-foreground">
                          {cap(vocabulary.parturitionPast)}
                        </span>
                      )}
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {listSettling && (
            <p role="status" className="pt-3 text-sm text-muted-foreground">
              Updating breeding records…
            </p>
          )}
          <PaginationControls
            total={payload.total}
            limit={payload.limit}
            offset={payload.offset}
            onOffsetChange={setOffset}
            label="breeding records"
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
  return (
    <Suspense
      fallback={
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading…</span>
          <PageSkeleton stats={3} cards={1} />
        </div>
      }
    >
      <BreedingPageContent />
    </Suspense>
  );
}
