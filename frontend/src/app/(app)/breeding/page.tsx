"use client";

/** Breeding records — parity with v1's breeding/list.html (+ new/ultrasound as dialogs). */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { HeartHandshake, Plus } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState, type FormEvent } from "react";
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
import { ApiError } from "@/lib/api-client";
import { farmToday, formatDate } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

/** Deep-link ids arrive as raw query strings; anything that is not a positive
 * safe integer is ignored. */
function parsePositiveId(raw: string | null): number | null {
  const parsed = raw === null ? Number.NaN : Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function localToday(): string {
  return farmToday();
}

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

/** v1's tag-{outcome} styles as a tinted StatusBadge. */
const OUTCOME_TINTS: Record<string, string> = {
  CONFIRMED_PREGNANT:
    "border-transparent bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  FAILED:
    "border-transparent bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  ABORTED:
    "border-transparent bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};

function OutcomeBadge({ outcome }: { outcome: string }) {
  return (
    <StatusBadge status={outcome} className={OUTCOME_TINTS[outcome]}>
      {outcome.replace(/_/g, " ")}
    </StatusBadge>
  );
}

const breedingSchema = z.object({
  doe_id: z.string().min(1, "Select a doe"),
  buck_id: z.string().min(1, "Select a buck"),
  breeding_date: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/, "Pick a valid date")
    .refine((s) => s <= localToday(), "Date can't be in the future"),
});
type BreedingValues = z.infer<typeof breedingSchema>;

function breedingDefaults(): BreedingValues {
  return { doe_id: "", buck_id: "", breeding_date: localToday() };
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
  const [formError, setFormError] = useState<string | null>(null);
  const eligibleDoeCount = candidateAvailability?.eligible_doe_count ?? null;
  const eligibleBuckCount = candidateAvailability?.eligible_buck_count ?? null;
  const hasEligibleBuck = eligibleBuckCount !== null && eligibleBuckCount > 0;
  const {
    control,
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<BreedingValues>({
    resolver: zodResolver(breedingSchema),
    defaultValues: breedingDefaults(),
  });

  useEffect(() => {
    if (open) reset(breedingDefaults());
  }, [open, reset]);

  async function onSubmit(values: BreedingValues) {
    await createFlight.run(async () => {
      setFormError(null);
      try {
        await createMutation.mutateAsync({
          data: {
            doe_id: Number(values.doe_id),
            buck_id: Number(values.buck_id),
            breeding_date: values.breeding_date,
          },
        });
        toast.success("Breeding saved.");
        reset(breedingDefaults());
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
            An ultrasound-check task is auto-created for breeding date + 32 days.
          </DialogDescription>
        </DialogHeader>
        {candidateAvailability === null ? (
          <p role="alert" className="text-destructive">
            Candidate availability is unavailable. Refresh the breeding records before adding a
            breeding.
          </p>
        ) : eligibleDoeCount === 0 ? (
          <p className="text-muted-foreground">
            No breeding-ready does right now (female, ≥10 months, ≥22 kg, not
            pregnant).
          </p>
        ) : (
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            {formError && (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="breeding-doe">Doe *</Label>
              <Controller
                control={control}
                name="doe_id"
                render={({ field }) => (
                  <BreedingCandidatePicker
                    id="breeding-doe"
                    kind="doe"
                    value={field.value}
                    onValueChange={field.onChange}
                    placeholder="Select doe"
                    dialogTitle="Choose a breeding-ready doe"
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
              <Label htmlFor="breeding-buck">Buck *</Label>
              <Controller
                control={control}
                name="buck_id"
                render={({ field }) => (
                  <BreedingCandidatePicker
                    id="breeding-buck"
                    kind="buck"
                    value={field.value}
                    onValueChange={field.onChange}
                    placeholder="Select buck"
                    dialogTitle="Choose an active buck"
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
                  No eligible bucks are available. Bucks on hold, in quarantine, or otherwise
                  restricted cannot be selected.
                </p>
              )}
            </div>
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
                disabled={isSubmitting || createFlight.pending || !hasEligibleBuck}
              >
                {isSubmitting || createFlight.pending
                  ? "Saving…"
                  : formError
                    ? "Retry save breeding"
                    : "Save breeding"}
              </Button>
            </DialogFooter>
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
  // Opened before the planned scan, the only recordable result is a negative
  // one, so the form starts there instead of opening in an error state.
  const scanDue = !record.ultrasound_date || record.ultrasound_date <= localToday();
  const [pregnant, setPregnant] = useState(scanDue);
  const [kidCount, setKidCount] = useState(scanDue ? "2" : "");
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
  const resultDateError = !resultDate
    ? "Result date is required"
    : resultDate < earliestResultDate
      ? `Result date cannot be before ${formatDate(earliestResultDate)}`
      : resultDate > localToday()
        ? "Result date can't be in the future"
        : null;
  const kidCountError = pregnant && !["1", "2", "3"].includes(kidCount)
    ? "Select the detected kid count"
    : null;

  async function onSubmit() {
    if (resultDateError || kidCountError || saveLock.current) return;
    saveLock.current = true;
    setSaving(true);
    setFormError(null);
    try {
      await mutation.mutateAsync({
        recordId: record.id,
        data: {
          pregnant,
          date: resultDate,
          kid_count: pregnant ? Number(kidCount) : null,
        },
      });
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
            Doe {record.doe_tag ?? `#${record.doe_id}`} · bred{" "}
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
              scan; a not-pregnant one (doe back in heat) can be recorded from the breeding date.
              Use the actual historical date for backdated entry.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="pregnant"
              checked={pregnant}
              onCheckedChange={(checked) => {
                const selected = checked === true;
                setPregnant(selected);
                setKidCount(selected ? "2" : "");
              }}
            />
            <Label htmlFor="pregnant">Pregnant — confirmed</Label>
          </div>
          {pregnant && (
            <div className="space-y-1.5">
              <Label htmlFor={`ultrasound-kid-count-${record.id}`}>Kid count detected</Label>
              <Select value={kidCount} onValueChange={(v) => setKidCount(String(v))}>
                <SelectTrigger id={`ultrasound-kid-count-${record.id}`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {["1", "2", "3"].map((n) => (
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
      try {
        await mutation.mutateAsync({
          recordId: record.id,
          data: {
            loss_date: lossDate,
            cause,
            notes: notes.trim() || null,
          },
        });
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
            Doe {record.doe_tag ?? `#${record.doe_id}`} · bred {formatDate(record.breeding_date)}.
            This closes the pregnancy and retains an auditable reason.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
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
            >
              <SelectTrigger id={`pregnancy-loss-cause-${record.id}`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.values(PregnancyLossInCause).map((value) => (
                  <SelectItem key={value} value={value}>
                    {value.replace(/_/g, " ")}
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
        </form>
      </DialogContent>
    </Dialog>
  );
}

function BreedingPageContent() {
  const queryClient = useQueryClient();
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("breeding.view");
  const canManage = can("breeding.manage");
  const canViewAnimals = can("animals.view");
  const [newOpen, setNewOpen] = useState(false);
  const [ultrasoundFor, setUltrasoundFor] = useState<BreedingRecordOut | null>(null);
  const [lossFor, setLossFor] = useState<BreedingRecordOut | null>(null);
  const [prefillDismissed, setPrefillDismissed] = useState(false);
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
    { query: { enabled: allowed } },
  );
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
  const deepLinkedUltrasound =
    canManage && !prefillDismissed && requestedRecord?.outcome === "PENDING"
      ? requestedRecord
      : null;
  const activeUltrasound = ultrasoundFor ?? deepLinkedUltrasound;

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
              : "Could not load breeding records."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry breeding records
          </Button>
        </div>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
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
          description="Add a breeding to start tracking ultrasound checks and expected kidding dates."
        />
      ) : (
        <DataTableCard
          title="Breeding records"
          description="Ultrasound is due 32 days after breeding; confirmed pregnancies get an expected kidding date."
        >
          <Table className="min-w-[900px]">
            <TableHeader>
              <TableRow>
                <TableHead>Bred</TableHead>
                <TableHead>Doe</TableHead>
                <TableHead>Buck</TableHead>
                <TableHead>Cycle</TableHead>
                <TableHead>Ultrasound</TableHead>
                <TableHead>Kids</TableHead>
                <TableHead>Expected kidding</TableHead>
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
                        {r.doe_tag ?? `Doe #${r.doe_id}`}
                      </Link>
                    ) : (
                      r.doe_tag ?? `Doe #${r.doe_id}`
                    )}
                  </TableCell>
                  <TableCell>
                    {canViewAnimals ? (
                      <Link href={`/animals/${r.buck_id}`} className="text-primary underline">
                        {r.buck_tag ?? `Buck #${r.buck_id}`}
                      </Link>
                    ) : (
                      r.buck_tag ?? `Buck #${r.buck_id}`
                    )}
                  </TableCell>
                  <TableCell>{r.heat_cycle_number}</TableCell>
                  <TableCell>
                    {r.ultrasound_done ? (
                      <span className="text-emerald-700 dark:text-emerald-400">done</span>
                    ) : r.ultrasound_date ? (
                      <span className="text-amber-700 dark:text-amber-400">
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
                          {(r.loss_cause ?? "UNKNOWN").replace(/_/g, " ")}
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
                        <span className="text-muted-foreground">Kidded</span>
                      )}
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <PaginationControls
            total={payload.total}
            limit={payload.limit}
            offset={payload.offset}
            onOffsetChange={setOffset}
            label="breeding records"
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
            setUltrasoundFor(null);
            setPrefillDismissed(true);
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
    <Suspense fallback={<p className="py-10 text-center text-muted-foreground">Loading…</p>}>
      <BreedingPageContent />
    </Suspense>
  );
}
