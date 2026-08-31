"use client";

/** Kidding — parity with v1's kidding/list.html (overdue/upcoming/history) + new as a dialog. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Baby, CalendarClock, Plus, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Controller, useFieldArray, useForm, useWatch } from "react-hook-form";
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
import { useFarmType } from "@/hooks/use-farm-type";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { PaginationControls } from "@/components/pagination-controls";
import { StatusBadge } from "@/components/status-badge";
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
import { addDays, farmToday, formatDate } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

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

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

const EASES = ["NORMAL", "ASSISTED", "DIFFICULT"] as const;
const KID_STATUSES = ["ALIVE", "STILLBORN", "DIED"] as const;
/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
const KID_SEX_ITEMS: Record<string, string> = { F: "Female", M: "Male" };
const MAX_KIDS = 10;
const KIDDING_HISTORY_LIMIT = 50;
const DUE_LIST_LIMIT = 25;
/** Mirrors backend/app/models/constants.py — record_kidding() rejects a
 * gestation outside this window outright. */
const MIN_GESTATION_DAYS = 100;
const MAX_GESTATION_DAYS = 200;

const kidSchema = z.object({
  tag: z.string().max(50, "Max 50 characters").optional(),
  sex: z.enum(["M", "F"]),
  // Mirrors NonNegativeWeightKgFloat (le=1000, schemas/common.py): a grams-as-kg
  // typo like 5000 should fail inline instead of bouncing the whole kidding
  // with an opaque server 422, matching the purchases weight field's cap.
  birth_weight: z.number().nonnegative("Must be ≥ 0").max(1000, "At most 1000 kg").nullish(),
  status: z.enum(KID_STATUSES),
  // Required exactly when the kid died, mirroring KidIn (schemas/kidding.py).
  mortality_reported_at: z.string().optional(),
});
const kiddingSchema = z
  .object({
    date: z
      .string()
      .regex(/^\d{4}-\d{2}-\d{2}$/, "Pick a valid date")
      .refine((s) => s <= localToday(), "Date can't be in the future"),
    ease: z.enum(EASES),
    // Backend KiddingCreateIn caps free text at MAX_FREE_TEXT_LENGTH (4000);
    // an over-long pasted note should fail inline like the pregnancy-loss
    // dialog's notes rather than only as a server 422 on submit.
    notes: z.string().max(4_000, "Notes cannot exceed 4000 characters").optional(),
    kids: z.array(kidSchema).min(1, "At least one kid").max(MAX_KIDS, "At most 10 kids"),
  })
  .superRefine((values, ctx) => {
    // Mirrors services/kidding.py: a died kid needs a mortality date that is on
    // or after the kidding date and not in the future.
    values.kids.forEach((kid, index) => {
      if (kid.status !== "DIED") return;
      const reported = kid.mortality_reported_at?.trim();
      const path = ["kids", index, "mortality_reported_at"];
      if (!reported) {
        ctx.addIssue({ code: "custom", path, message: "Mortality date is required" });
      } else if (reported < values.date) {
        ctx.addIssue({ code: "custom", path, message: "Can't be before the kidding date" });
      } else if (reported > localToday()) {
        ctx.addIssue({ code: "custom", path, message: "Date can't be in the future" });
      }
    });
  });
type KiddingValues = z.infer<typeof kiddingSchema>;

/** The kidding date's floor depends on the pregnancy being closed, so it is
 * layered on per record: record_kidding() rejects both a gestation below
 * MIN_GESTATION_DAYS and a delivery predating its own confirmation scan.
 * Catching them here saves the operator from entering every kid row first. */
function kiddingSchemaFor(earliestDate: string, latestDate: string) {
  return kiddingSchema.superRefine((values, ctx) => {
    if (values.date && values.date < earliestDate) {
      ctx.addIssue({
        code: "custom",
        path: ["date"],
        message: `Kidding date cannot be before ${formatDate(earliestDate)}`,
      });
    }
    if (values.date && values.date > latestDate) {
      ctx.addIssue({
        code: "custom",
        path: ["date"],
        message: `Kidding date cannot be after ${formatDate(latestDate)} (gestation over ${MAX_GESTATION_DAYS} days)`,
      });
    }
  });
}

function emptyKid(): KiddingValues["kids"][number] {
  return { tag: "", sex: "F", birth_weight: null, status: "ALIVE", mortality_reported_at: "" };
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
  const [formError, setFormError] = useState<string | null>(null);
  const [earliestKiddingDate, latestKiddingDate] = useMemo(() => {
    const minGestationDate = addDays(breeding.breeding_date, MIN_GESTATION_DAYS);
    const earliest =
      breeding.ultrasound_result_date && breeding.ultrasound_result_date > minGestationDate
        ? breeding.ultrasound_result_date
        : minGestationDate;
    const maxGestationDate = addDays(breeding.breeding_date, MAX_GESTATION_DAYS);
    return [earliest, maxGestationDate < localToday() ? maxGestationDate : localToday()];
  }, [breeding.breeding_date, breeding.ultrasound_result_date]);
  const resolver = useMemo(
    () => zodResolver(kiddingSchemaFor(earliestKiddingDate, latestKiddingDate)),
    [earliestKiddingDate, latestKiddingDate],
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
      date: localToday(),
      ease: "NORMAL",
      notes: "",
      kids: [emptyKid(), emptyKid()], // twins are the norm (v1 parity)
    },
  });
  const { fields, append, remove } = useFieldArray({ control, name: "kids" });
  const kiddingDate = useWatch({ control, name: "date" });
  const kidValues = useWatch({ control, name: "kids" });

  async function onSubmit(values: KiddingValues) {
    await createFlight.run(async () => {
      setFormError(null);
      const requestFarmEpoch = farmScopeEpochValue();
      try {
        await mutation.mutateAsync({
          data: {
            breeding_record_id: breeding.id,
            date: values.date,
            ease: values.ease,
            notes: values.notes?.trim() ? values.notes.trim() : null,
            kids: values.kids.map((k) => ({
              tag: k.tag?.trim() ? k.tag.trim() : null,
              sex: k.sex,
              birth_weight: k.birth_weight ?? null,
              status: k.status,
              // KidIn forbids the date unless the kid died, so never send a stale one.
              mortality_reported_at:
                k.status === "DIED" ? (k.mortality_reported_at ?? null) : null,
            })),
          },
        });
        if (farmScopeEpochValue() !== requestFarmEpoch) return;
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
      onOpenChange={(open) => !open && !isSubmitting && !createFlight.pending && onClose()}
    >
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Record kidding</DialogTitle>
          <DialogDescription>
            Doe {breeding.doe_tag ?? `#${breeding.doe_id}`} · due{" "}
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
              <Label htmlFor="kidding_date">Kidding date *</Label>
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
                  >
                    <SelectTrigger id="kidding-ease" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {EASES.map((e) => (
                        <SelectItem key={e} value={e}>
                          {e}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
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
                Kids
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={fields.length >= MAX_KIDS}
                onClick={() => append(emptyKid())}
              >
                <Plus />
                Add kid
              </Button>
            </div>
            {fields.map((field, index) => (
              <div
                key={field.id}
                className="grid grid-cols-2 gap-2 rounded-lg border p-3 sm:grid-cols-[1fr_90px_110px_120px_auto] sm:items-end sm:border-0 sm:p-0"
              >
                <div className="col-span-2 space-y-1 sm:col-span-1">
                  <Label htmlFor={`kid-${field.id}-tag`} className="text-xs">
                    Kid {index + 1} tag (auto if blank)
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
                      {errors.kids[index]?.tag?.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`kid-${field.id}-sex`} className="text-xs">
                    Kid {index + 1} sex
                  </Label>
                  <Controller
                    control={control}
                    name={`kids.${index}.sex`}
                    render={({ field: f }) => (
                      <Select value={f.value} onValueChange={f.onChange} items={KID_SEX_ITEMS}>
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
                    Kid {index + 1} weight (kg)
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
                      {errors.kids[index]?.birth_weight?.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`kid-${field.id}-status`} className="text-xs">
                    Kid {index + 1} status
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
                        }}
                      >
                        <SelectTrigger id={`kid-${field.id}-status`} size="sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {KID_STATUSES.map((s) => (
                            <SelectItem key={s} value={s}>
                              {s}
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
                  aria-label={`Remove kid ${index + 1}`}
                  disabled={fields.length <= 1}
                  onClick={() => remove(index)}
                  className="justify-self-end"
                >
                  <X />
                </Button>
                {kidValues?.[index]?.status === "DIED" && (
                  <div className="col-span-2 space-y-1 sm:col-span-5">
                    <Label htmlFor={`kid-${field.id}-mortality`} className="text-xs">
                      Kid {index + 1} mortality date *
                    </Label>
                    <Input
                      id={`kid-${field.id}-mortality`}
                      type="date"
                      min={kiddingDate}
                      max={localToday()}
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
                        {errors.kids[index]?.mortality_reported_at?.message}
                      </p>
                    )}
                  </div>
                )}
              </div>
            ))}
            {errors.kids?.root && (
              <p role="alert" className="text-sm text-destructive">
                {errors.kids.root.message}
              </p>
            )}
            <p className="text-xs text-muted-foreground" aria-live="polite">
              {fields.length} kid{fields.length === 1 ? "" : "s"} listed
              {breeding.kid_count_detected !== null &&
                breeding.kid_count_detected !== undefined &&
                breeding.kid_count_detected !== fields.length &&
                ` — ultrasound detected ${breeding.kid_count_detected}. Reconcile the difference or note the reason.`}
            </p>
          </fieldset>

          <p className="text-sm text-muted-foreground">
            Alive kids are auto-created as animals (source BORN, dam/sire linked,
            RECOVERY bucket). A weaning task is auto-created for kidding date + 60 days.
          </p>
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
                  ? "Retry save kidding"
                  : "Save kidding"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Kids of a kidding record, inline: "tag (sex, status), …" with animal links. */
function KidsCell({
  kidding,
  canViewAnimals,
}: {
  kidding: KiddingRecordOut;
  canViewAnimals: boolean;
}) {
  const kids = kidding.kids ?? [];
  if (kids.length === 0) return <span>—</span>;
  return (
    <span>
      {kids.map((kid, i) => (
        <span key={kid.id}>
          {i > 0 && ", "}
          {kid.animal_id && canViewAnimals ? (
            <Link href={`/animals/${kid.animal_id}`} className="text-primary underline">
              {kid.tag ?? "kid"}
            </Link>
          ) : (
            (kid.tag ?? "kid")
          )}{" "}
          ({kid.sex === "F" ? "Female" : "Male"}, {kid.status.toLowerCase()})
        </span>
      ))}
    </span>
  );
}

function KiddingPageContent() {
  const queryClient = useQueryClient();
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("kidding.view");
  const canManage = can("kidding.manage");
  const canViewAnimals = can("animals.view");
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
    canManage &&
    requestedBreedingId !== null &&
    dismissedPrefillId !== requestedBreedingId &&
    requestedRecord !== undefined &&
    (requestedRecord.outcome !== "CONFIRMED_PREGNANT" || requestedRecord.has_kidding);

  useEffect(() => {
    // Scope dismissal to one continuous URL intent. Query-only navigation can
    // clear the link and later revisit the same pregnancy without remounting
    // this page; retaining the old id forever would suppress that new visit.
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
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (historyOffset > lastHistoryOffset) setHistoryOffset(lastHistoryOffset);
    if (upcomingOffset > lastUpcomingOffset) setUpcomingOffset(lastUpcomingOffset);
    if (overdueOffset > lastOverdueOffset) setOverdueOffset(lastOverdueOffset);
  }, [historyOffset, overdueOffset, payload, upcomingOffset]);

  function refresh() {
    invalidateFarmData(queryClient);
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
            {query.error instanceof ApiError
              ? query.error.detail
              : "Could not load kidding data."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry kidding data
          </Button>
        </div>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  // "Xd late" compares against the active farm's calendar day.
  const today = farmToday();

  function recordButton(r: BreedingRecordOut) {
    if (!canManage) return null;
    return (
      <Button
        variant="outline"
        size="sm"
        disabled={queuesSettling}
        onClick={() => setRecordFor(r)}
      >
        Record kidding
      </Button>
    );
  }

  const vocabulary = farmVocabulary(useFarmType());

  return (
    <div className="space-y-6">
      <PageHeader
        title={vocabulary.parturitionCap}
        description={`Confirmed pregnancies due soon and recent ${vocabulary.parturition} history.`}
      />

      {queuesSettling && (
        <p role="status" className="text-sm text-muted-foreground">
          Updating kidding queues…
        </p>
      )}

      {staleDeepLink && requestedRecord && (
        <div role="status" className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">
            {requestedRecord.has_kidding
              ? `Pregnancy #${requestedBreedingId} already has a kidding recorded.`
              : `Pregnancy #${requestedBreedingId} is no longer confirmed pregnant — no kidding to record.`}
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
          className="border-red-200 bg-red-50/60 dark:border-red-900 dark:bg-red-950/30"
          title={
            <span className="flex items-center gap-2 text-red-700 dark:text-red-400">
              <AlertTriangle className="size-4" />
              Overdue (past expected date, no kidding recorded)
            </span>
          }
          description={`${payload.overdue_total} overdue pregnancies in the full queue.`}
        >
          <Table className="min-w-[560px]">
            <TableHeader className="sr-only">
              <TableRow>
                <th scope="col">Doe</th>
                <th scope="col">Was due</th>
                <th scope="col">Record kidding</th>
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
                        {r.doe_tag ?? `Doe #${r.doe_id}`}
                      </Link>
                    ) : (
                      r.doe_tag ?? `Doe #${r.doe_id}`
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
          />
        ) : (
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow>
                <TableHead>Doe</TableHead>
                <TableHead>Bred</TableHead>
                <TableHead>Expected</TableHead>
                <TableHead>Days left</TableHead>
                <TableHead>Kids detected</TableHead>
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
                        {r.doe_tag ?? `Doe #${r.doe_id}`}
                      </Link>
                    ) : (
                      r.doe_tag ?? `Doe #${r.doe_id}`
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
        title="Recent kiddings"
        description="Latest recorded kiddings with ease and kid outcomes."
      >
        {payload.records.length === 0 ? (
          <EmptyState
            icon={Baby}
            title="No kiddings recorded yet."
            description="Record a kidding from the upcoming list once a doe delivers."
          />
        ) : (
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Doe</TableHead>
                <TableHead>Ease</TableHead>
                <TableHead>Kids</TableHead>
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
                        {k.doe_tag ?? `Doe #${k.doe_id}`}
                      </Link>
                    ) : (
                      k.doe_tag ?? `Doe #${k.doe_id}`
                    )}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={k.ease}>{k.ease}</StatusBadge>
                  </TableCell>
                  <TableCell>
                    <KidsCell kidding={k} canViewAnimals={canViewAnimals} />
                  </TableCell>
                  <TableCell>{k.notes ?? ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <PaginationControls
          total={payload.total}
          limit={payload.limit}
          offset={payload.offset}
          onOffsetChange={setHistoryOffset}
          label="kidding records"
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
  return (
    <Suspense fallback={<p className="py-10 text-center text-muted-foreground">Loading…</p>}>
      <KiddingPageContent />
    </Suspense>
  );
}
