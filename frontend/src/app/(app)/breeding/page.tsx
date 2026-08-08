"use client";

/** Breeding records — parity with v1's breeding/list.html (+ new/ultrasound as dialogs). */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { HeartHandshake, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useAbortPregnancyApiBreedingRecordIdAbortPost,
  useBreedingCandidatesApiBreedingCandidatesGet,
  useBreedingListApiBreedingGet,
  useCreateBreedingApiBreedingPost,
  useGetBreedingRecordApiBreedingRecordIdGet,
  useSubmitUltrasoundApiBreedingRecordIdUltrasoundPost,
} from "@/api/generated/endpoints";
import type { BreedingRecordOut } from "@/api/generated/models";
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
import { ApiError } from "@/lib/api-client";
import { farmToday, formatDate } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { usePermissions } from "@/lib/use-permissions";

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

function NewBreedingDialog({
  open,
  onOpenChange,
  candidateDoeIds,
  activeBuckIds,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidateDoeIds: number[];
  activeBuckIds: number[];
  onSaved: () => void;
}) {
  const createMutation = useCreateBreedingApiBreedingPost();
  const buckAvailability = useBreedingCandidatesApiBreedingCandidatesGet(
    { kind: "buck", limit: 1, offset: 0 },
    { query: { enabled: open && candidateDoeIds.length > 0 && activeBuckIds.length > 0 } },
  );
  const eligibleBuckTotal =
    buckAvailability.data?.status === 200 ? buckAvailability.data.data.total : null;
  const checkingBucks = activeBuckIds.length > 0 && buckAvailability.isPending;
  const buckCheckFailed = activeBuckIds.length > 0 && buckAvailability.isError;
  const hasEligibleBuck =
    activeBuckIds.length > 0 && eligibleBuckTotal !== null && eligibleBuckTotal > 0;
  const {
    control,
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<BreedingValues>({
    resolver: zodResolver(breedingSchema),
    defaultValues: { doe_id: "", buck_id: "", breeding_date: localToday() },
  });

  async function onSubmit(values: BreedingValues) {
    try {
      await createMutation.mutateAsync({
        data: {
          doe_id: Number(values.doe_id),
          buck_id: Number(values.buck_id),
          breeding_date: values.breeding_date,
        },
      });
      toast.success("Breeding saved.");
      reset({ doe_id: "", buck_id: "", breeding_date: localToday() });
      onOpenChange(false);
      onSaved();
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add breeding</DialogTitle>
          <DialogDescription>
            An ultrasound-check task is auto-created for breeding date + 32 days.
          </DialogDescription>
        </DialogHeader>
        {candidateDoeIds.length === 0 ? (
          <p className="text-muted-foreground">
            No breeding-ready does right now (female, ≥10 months, ≥22 kg, not
            pregnant).
          </p>
        ) : (
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
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
                    eligibleIds={candidateDoeIds}
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
                    eligibleIds={activeBuckIds}
                    disabled={!hasEligibleBuck}
                    aria-invalid={Boolean(errors.buck_id) || undefined}
                    aria-describedby={errors.buck_id ? "breeding-buck-error" : undefined}
                  />
                )}
              />
              {errors.buck_id && (
                <p id="breeding-buck-error" role="alert" className="text-sm text-destructive">{errors.buck_id.message}</p>
              )}
              {checkingBucks && (
                <p role="status" className="text-sm text-muted-foreground">
                  Checking eligible bucks…
                </p>
              )}
              {!checkingBucks && !buckCheckFailed && !hasEligibleBuck && (
                <p className="text-sm text-destructive">
                  No eligible bucks are available. Bucks on hold, in quarantine, or otherwise
                  restricted cannot be selected.
                </p>
              )}
              {buckCheckFailed && (
                <div className="flex flex-wrap items-center gap-2 text-sm text-destructive">
                  <span role="alert">Could not check eligible bucks.</span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => void buckAvailability.refetch()}
                  >
                    Try again
                  </Button>
                </div>
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
              <Button type="submit" disabled={isSubmitting || !hasEligibleBuck}>
                {isSubmitting ? "Saving…" : "Save breeding"}
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
  const [pregnant, setPregnant] = useState(true);
  const [kidCount, setKidCount] = useState("2");
  const [resultDate, setResultDate] = useState(localToday());
  const [saving, setSaving] = useState(false);
  const mutation = useSubmitUltrasoundApiBreedingRecordIdUltrasoundPost();
  const earliestResultDate = record.ultrasound_date ?? record.breeding_date;
  const resultDateError = !resultDate
    ? "Result date is required"
    : resultDate < earliestResultDate
      ? `Result date cannot be before ${formatDate(earliestResultDate)}`
      : resultDate > localToday()
        ? "Result date can't be in the future"
        : null;

  async function onSubmit() {
    if (resultDateError) return;
    setSaving(true);
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
      toast.error(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
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
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor={`ultrasound-result-date-${record.id}`}>Result date *</Label>
            <Input
              id={`ultrasound-result-date-${record.id}`}
              type="date"
              min={record.ultrasound_date ?? record.breeding_date}
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
              Planned check: {formatDate(record.ultrasound_date)}. Use the actual historical date
              for backdated entry.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="pregnant"
              checked={pregnant}
              onCheckedChange={(checked) => setPregnant(checked === true)}
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
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={saving || Boolean(resultDateError)}>
            {saving ? "Saving…" : "Save result"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function BreedingPage() {
  const queryClient = useQueryClient();
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("breeding.view");
  const canManage = can("breeding.manage");
  const canViewAnimals = can("animals.view");
  const [newOpen, setNewOpen] = useState(false);
  const [ultrasoundFor, setUltrasoundFor] = useState<BreedingRecordOut | null>(null);
  const [prefillDismissed, setPrefillDismissed] = useState(false);
  const [requestedUltrasoundId] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    const raw = new URLSearchParams(window.location.search).get("ultrasound_id");
    const parsed = raw === null ? Number.NaN : Number(raw);
    return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
  });
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const query = useBreedingListApiBreedingGet(
    { limit, offset },
    { query: { enabled: allowed } },
  );
  const payload = query.data?.status === 200 ? query.data.data : undefined;
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
  const deepLinkedUltrasound =
    canManage &&
    !prefillDismissed &&
    requestedRecord?.outcome === "PENDING" &&
    requestedRecord.ultrasound_date &&
    requestedRecord.ultrasound_date <= localToday()
      ? requestedRecord
      : null;
  const activeUltrasound = ultrasoundFor ?? deepLinkedUltrasound;
  const abortMutation = useAbortPregnancyApiBreedingRecordIdAbortPost();

  function refresh() {
    invalidateFarmData(queryClient);
  }

  async function markAborted(record: BreedingRecordOut) {
    if (!window.confirm("Mark this pregnancy as aborted?")) return;
    try {
      await abortMutation.mutateAsync({ recordId: record.id });
      toast.success("Pregnancy marked as aborted.");
      refresh();
    } catch (err) {
      toast.error(errorText(err));
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
          {query.error instanceof ApiError
            ? query.error.detail
            : "Could not load breeding records."}
        </p>
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
          <Table>
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
                  </TableCell>
                  {canManage && (
                    <TableCell className="space-x-2 text-right">
                      {r.outcome === "PENDING" && r.ultrasound_date && r.ultrasound_date <= localToday() && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setUltrasoundFor(r)}
                        >
                          Ultrasound result
                        </Button>
                      )}
                      {r.outcome === "PENDING" && (!r.ultrasound_date || r.ultrasound_date > localToday()) && (
                        <span className="text-xs text-muted-foreground">
                          Result available {formatDate(r.ultrasound_date)}
                        </span>
                      )}
                      {r.outcome === "CONFIRMED_PREGNANT" && !r.has_kidding && (
                        <Button
                          variant="destructive"
                          size="sm"
                          disabled={abortMutation.isPending}
                          onClick={() => markAborted(r)}
                        >
                          Abort
                        </Button>
                      )}
                      {r.has_kidding && (
                        <span className="text-muted-foreground">Kidded</span>
                      )}
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
          candidateDoeIds={payload.candidate_doe_ids}
          activeBuckIds={payload.active_buck_ids}
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
    </div>
  );
}
