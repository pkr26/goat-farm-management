"use client";

/** Animal profile — parity with v1's animals/profile.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState, type ReactNode } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useAnimalProfileApiAnimalsAnimalIdGet,
  useChangeStatusApiAnimalsAnimalIdStatusPost,
  useClearMovementRestrictionApiHealthRestrictionsAnimalIdClearPost,
  useMovementRestrictionHistoryApiHealthRestrictionsAnimalIdGet,
  useMoveBucketApiAnimalsAnimalIdMovePost,
  useRecordWeightApiAnimalsAnimalIdWeightPost,
} from "@/api/generated/endpoints";
import {
  MoveInToBucket,
  StatusChangeInNewStatus,
  type AnimalProfileOut,
} from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { PageHeader } from "@/components/page-header";
import { PaginationControls } from "@/components/pagination-controls";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
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
import { farmToday, formatDate, formatFarmDateTime, formatMoney } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { permittedAppPath } from "@/lib/permission-navigation";
import {
  isPersistableNonnegativeMoney,
  MIN_PERSISTED_MONEY_MESSAGE,
} from "@/lib/persisted-numbers";
import { usePermissions } from "@/lib/use-permissions";

const BUCKETS = Object.values(MoveInToBucket);
const PROFILE_HISTORY_LIMIT = 25;
const RESTRICTION_HISTORY_LIMIT = 25;
/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
const BUCKET_ITEMS: Record<string, string> = Object.fromEntries(
  BUCKETS.map((b) => [b, b.replace(/_/g, " ")]),
);

const optNum = (schema: z.ZodNumber) =>
  z.preprocess(
    (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)),
    schema.optional(),
  );
const emptyToNull = (v: string | undefined) => (v ? v : null);

function localToday(): string {
  return farmToday();
}

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

const weightSchema = z.object({
  date: z
    .string()
    .optional()
    .refine((s) => !s || s <= localToday(), "Date can't be in the future"),
  weight_kg: z.coerce
    .number()
    .positive("Weight must be greater than 0")
    .max(1000, "Weight must be at most 1000 kg"),
  bcs: optNum(z.number().int().min(1).max(5)),
  notes: z.string().max(255).optional(),
});
type WeightInput = z.input<typeof weightSchema>;
type WeightValues = z.output<typeof weightSchema>;

function AddWeightDialog({ animalId, onDone }: { animalId: number; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const mut = useRecordWeightApiAnimalsAnimalIdWeightPost();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<WeightInput, unknown, WeightValues>({ resolver: zodResolver(weightSchema) });

  async function onSubmit(values: WeightValues) {
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
      toast.success("Weight recorded.");
      reset();
      setOpen(false);
      onDone();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Something went wrong");
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        Record weight
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Record weight</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor="w_date">Date (defaults to today)</Label>
            <Input
              id="w_date"
              type="date"
              max={localToday()}
              aria-invalid={Boolean(errors.date) || undefined}
              aria-describedby={errors.date ? "weight-date-error" : undefined}
              {...register("date")}
            />
            {errors.date && <p id="weight-date-error" role="alert" className="text-sm text-destructive">{errors.date.message}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="w_kg">Weight (kg) *</Label>
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
            <Label htmlFor="w_bcs">BCS (1–5)</Label>
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
            <Label htmlFor="w_notes">Notes</Label>
            <Textarea id="w_notes" rows={2} {...register("notes")} />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

const moveSchema = z.object({
  to_bucket: z.enum(BUCKETS as [string, ...string[]]),
  reason: z.string().max(255).optional(),
});
type MoveValues = z.infer<typeof moveSchema>;

function MoveBucketDialog({
  animalId,
  currentBucket,
  onDone,
}: {
  animalId: number;
  currentBucket: string;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const mut = useMoveBucketApiAnimalsAnimalIdMovePost();
  const {
    handleSubmit,
    control,
    register,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<MoveValues>({ resolver: zodResolver(moveSchema) });

  async function onSubmit(values: MoveValues) {
    try {
      await mut.mutateAsync({
        animalId,
        data: {
          to_bucket: values.to_bucket as MoveInToBucket,
          reason: emptyToNull(values.reason),
        },
      });
      toast.success("Animal moved.");
      reset();
      setOpen(false);
      onDone();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Something went wrong");
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        Move bucket
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Move bucket</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor="move-to-bucket">To bucket *</Label>
            <Controller
              control={control}
              name="to_bucket"
              render={({ field }) => (
                <Select value={field.value ?? ""} onValueChange={field.onChange} items={BUCKET_ITEMS}>
                  <SelectTrigger
                    id="move-to-bucket"
                    className="w-full"
                    aria-invalid={Boolean(errors.to_bucket) || undefined}
                    aria-describedby={errors.to_bucket ? "move-to-bucket-error" : undefined}
                  >
                    <SelectValue placeholder="Choose bucket…" />
                  </SelectTrigger>
                  <SelectContent>
                    {BUCKETS.filter((b) => b !== currentBucket).map((b) => (
                      <SelectItem key={b} value={b}>
                        {b.replace(/_/g, " ")}
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
            <Label htmlFor="m_reason">Reason</Label>
            <Textarea id="m_reason" rows={2} {...register("reason")} />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Moving…" : "Move"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

const statusSchema = z
  .object({
    new_status: z.enum([
      StatusChangeInNewStatus.SOLD,
      StatusChangeInNewStatus.DEAD,
      StatusChangeInNewStatus.CULLED,
    ]),
    date: z.string().optional(),
    sale_price: optNum(
      z.number().nonnegative().refine(isPersistableNonnegativeMoney, MIN_PERSISTED_MONEY_MESSAGE),
    ),
    buyer_name: z.string().max(120).optional(),
    notes: z.string().max(255).optional(),
    mortality_cause: z.string().max(120).optional(),
    mortality_reported_at: z.string().optional(),
    suspected_scheduled_disease: z.boolean(),
    suspected_disease: z.string().max(120).optional(),
    authority_notified_at: z.string().optional(),
  })
  .superRefine((values, context) => {
    for (const field of ["date", "mortality_reported_at", "authority_notified_at"] as const) {
      if (values[field] && values[field] > localToday()) {
        context.addIssue({ code: "custom", path: [field], message: "Date can't be in the future" });
      }
    }
    if (
      values.new_status === StatusChangeInNewStatus.DEAD &&
      values.suspected_scheduled_disease &&
      !values.suspected_disease?.trim()
    ) {
      context.addIssue({
        code: "custom",
        path: ["suspected_disease"],
        message: "Identify the suspected scheduled disease",
      });
    }
  });
type StatusInput = z.input<typeof statusSchema>;
type StatusValues = z.output<typeof statusSchema>;

function StatusDialog({
  animalId,
  onDone,
}: {
  animalId: number;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const mut = useChangeStatusApiAnimalsAnimalIdStatusPost();
  const {
    register,
    handleSubmit,
    control,
    setValue,
    unregister,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<StatusInput, unknown, StatusValues>({
    resolver: zodResolver(statusSchema),
    defaultValues: {
      new_status: StatusChangeInNewStatus.SOLD,
      suspected_scheduled_disease: false,
    },
  });
  const newStatus = useWatch({ control, name: "new_status" });
  const suspectedScheduledDisease = useWatch({
    control,
    name: "suspected_scheduled_disease",
  });

  async function onSubmit(values: StatusValues) {
    try {
      await mut.mutateAsync({
        animalId,
        data: {
          new_status: values.new_status,
          date: emptyToNull(values.date),
          sale_price:
            values.new_status === StatusChangeInNewStatus.SOLD
              ? (values.sale_price ?? null)
              : null,
          buyer_name:
            values.new_status === StatusChangeInNewStatus.SOLD
              ? emptyToNull(values.buyer_name)
              : null,
          notes: emptyToNull(values.notes),
          mortality_cause:
            values.new_status === StatusChangeInNewStatus.DEAD
              ? emptyToNull(values.mortality_cause)
              : null,
          mortality_reported_at:
            values.new_status === StatusChangeInNewStatus.DEAD
              ? emptyToNull(values.mortality_reported_at)
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
      toast.success(`Marked ${values.new_status}.`);
      reset();
      setOpen(false);
      onDone();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Something went wrong");
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button size="sm" variant="destructive" onClick={() => setOpen(true)}>
        Change status
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Change status</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor="animal-new-status">New status *</Label>
            <Controller
              control={control}
              name="new_status"
              render={({ field }) => (
                <Select
                  value={field.value}
                  onValueChange={(value) => {
                    const nextStatus = value as StatusValues["new_status"];
                    field.onChange(nextStatus);
                    if (nextStatus !== StatusChangeInNewStatus.SOLD) {
                      unregister(["sale_price", "buyer_name"]);
                    }
                    if (nextStatus !== StatusChangeInNewStatus.DEAD) {
                      setValue("suspected_scheduled_disease", false);
                      unregister([
                        "mortality_cause",
                        "mortality_reported_at",
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
                    <SelectItem value={StatusChangeInNewStatus.SOLD}>SOLD</SelectItem>
                    <SelectItem value={StatusChangeInNewStatus.DEAD}>DEAD</SelectItem>
                    <SelectItem value={StatusChangeInNewStatus.CULLED}>CULLED</SelectItem>
                  </SelectContent>
                </Select>
              )}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="s_date">Date (defaults to today)</Label>
            <Input
              id="s_date"
              type="date"
              max={localToday()}
              aria-invalid={Boolean(errors.date) || undefined}
              aria-describedby={errors.date ? "status-date-error" : undefined}
              {...register("date")}
            />
            {errors.date && <p id="status-date-error" role="alert" className="text-sm text-destructive">{errors.date.message}</p>}
          </div>
          {newStatus === StatusChangeInNewStatus.SOLD && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="s_price">Sale price (₹)</Label>
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
              <div className="space-y-1.5">
                <Label htmlFor="s_buyer">Buyer name</Label>
                <Input id="s_buyer" {...register("buyer_name")} />
              </div>
            </>
          )}
          {newStatus === StatusChangeInNewStatus.DEAD && (
            <fieldset className="space-y-3 rounded-lg border p-3">
              <legend className="px-1 text-sm font-medium">Mortality & disease reporting</legend>
              <div className="space-y-1.5">
                <Label htmlFor="mortality-cause">Mortality cause</Label>
                <Input
                  id="mortality-cause"
                  maxLength={120}
                  placeholder="confirmed or suspected cause"
                  {...register("mortality_cause")}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mortality-reported-at">Mortality reported date</Label>
                <Input
                  id="mortality-reported-at"
                  type="date"
                  max={localToday()}
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
                  Suspected scheduled/notifiable disease
                </Label>
              </div>
              {suspectedScheduledDisease && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="suspected-disease">Suspected disease *</Label>
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
                    <Label htmlFor="mortality-authority-notified">Authority notified date</Label>
                    <Input
                      id="mortality-authority-notified"
                      type="date"
                      max={localToday()}
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
            <Label htmlFor="s_notes">Notes</Label>
            <Textarea id="s_notes" rows={2} {...register("notes")} />
          </div>
          <DialogFooter>
            <Button type="submit" variant="destructive" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : "Confirm"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ClearRestrictionDialog({
  animalId,
  restrictionVersion,
  onDone,
}: {
  animalId: number;
  restrictionVersion: number;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [conflictedVersion, setConflictedVersion] = useState<number | null>(null);
  const requestInFlight = useRef(false);
  const mutation = useClearMovementRestrictionApiHealthRestrictionsAnimalIdClearPost();
  const awaitingEpisodeRefresh = conflictedVersion === restrictionVersion;

  async function clearRestriction() {
    if (requestInFlight.current || awaitingEpisodeRefresh) return;
    const clearanceReference = reference.trim();
    if (!clearanceReference) {
      setError("A veterinary or authority clearance reference is required.");
      return;
    }
    setError(null);
    requestInFlight.current = true;
    try {
      await mutation.mutateAsync({
        animalId,
        data: {
          clearance_reference: clearanceReference,
          expected_restriction_version: restrictionVersion,
        },
      });
      toast.success("Movement restriction cleared with an audit reference.");
      setReference("");
      setConflictedVersion(null);
      setOpen(false);
      onDone();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setConflictedVersion(restrictionVersion);
        setError(`${caught.detail} Refreshing the current restriction episode before retrying.`);
        onDone();
      } else {
        setError(caught instanceof ApiError ? caught.detail : "Could not clear the restriction.");
      }
    } finally {
      requestInFlight.current = false;
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && mutation.isPending) return;
        setOpen(nextOpen);
      }}
    >
      <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
        Record clearance
      </Button>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Clear movement restriction?</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          Only record a factual release issued by a veterinarian or competent authority. The
          hold and this clearance remain in the audit trail.
        </p>
        <div className="space-y-1.5">
          <Label htmlFor={`clearance-reference-${animalId}`}>Clearance reference *</Label>
          <Input
            id={`clearance-reference-${animalId}`}
            value={reference}
            maxLength={255}
            placeholder="certificate/order number and issuing authority"
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
            disabled={mutation.isPending}
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
          {awaitingEpisodeRefresh && (
            <Button type="button" variant="outline" onClick={onDone}>
              Refresh episode
            </Button>
          )}
          <Button
            type="button"
            disabled={!reference.trim() || mutation.isPending || awaitingEpisodeRefresh}
            onClick={() => void clearRestriction()}
          >
            {mutation.isPending
              ? "Recording…"
              : awaitingEpisodeRefresh
                ? "Waiting for current episode…"
                : error
                  ? "Retry clearance"
                  : "Confirm clearance"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ProfileBody({
  profile,
  refresh,
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
  backHref: string;
  backLabel: string;
  onKidsOffsetChange: (offset: number) => void;
  onWeightsOffsetChange: (offset: number) => void;
  onMovesOffsetChange: (offset: number) => void;
  onHealthEventsOffsetChange: (offset: number) => void;
  onBreedingsOffsetChange: (offset: number) => void;
}) {
  const { can } = usePermissions();
  const a = profile.animal;
  const active = a.status === "ACTIVE";
  const canViewHealth = can("health.view");
  const canViewBreeding = can("breeding.view");
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
              <StatusBadge status={a.status}>{a.status}</StatusBadge>
            </span>
          }
          description={`${a.breed} · ${a.sex === "F" ? "Female" : "Male"} · ${a.current_bucket.replace(/_/g, " ")}`}
          actions={
            active && (
              <>
                {can("animals.weight") && <AddWeightDialog animalId={a.id} onDone={refresh} />}
                {can("animals.move") && !a.movement_restricted && (
                  <MoveBucketDialog
                    animalId={a.id}
                    currentBucket={a.current_bucket}
                    onDone={refresh}
                  />
                )}
                {can("animals.status") && <StatusDialog animalId={a.id} onDone={refresh} />}
              </>
            )
          }
        />
      </div>

      {a.movement_restricted && (
        <Card className="border-red-300 bg-red-50/70 dark:border-red-900 dark:bg-red-950/30">
          <CardContent className="flex flex-wrap items-start justify-between gap-4 pt-6">
            <div className="space-y-1">
              <h2 className="flex items-center gap-2 font-medium text-red-800 dark:text-red-300">
                <AlertTriangle className="size-4" aria-hidden />
                Movement restricted
              </h2>
              <p className="text-sm text-red-800/90 dark:text-red-200">
                {a.restriction_reason ?? "A health hold is active for this animal."}
              </p>
              {a.suspected_disease && (
                <p className="text-sm">Suspected disease: {a.suspected_disease}</p>
              )}
              {a.authority_notified_at && (
                <p className="text-xs text-muted-foreground">
                  Authority notified {formatDate(a.authority_notified_at)}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                Bucket movement is unavailable until a factual clearance is recorded.
              </p>
            </div>
            {can("health.manage") && (
              <ClearRestrictionDialog
                animalId={a.id}
                restrictionVersion={a.restriction_version}
                onDone={refresh}
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
            title={`Movement restriction audit (${restrictionHistory?.total ?? 0})`}
            description="Immutable placement and clearance actions grouped by restriction episode."
          >
            {restrictionHistoryQuery.isLoading ? (
              <p className="text-sm text-muted-foreground">Loading restriction audit…</p>
            ) : restrictionHistoryQuery.isError ? (
              <div role="alert" className="flex flex-wrap items-center gap-3">
                <p className="text-sm text-destructive">
                  {restrictionHistoryQuery.error instanceof ApiError
                    ? restrictionHistoryQuery.error.detail
                    : "Could not load the restriction audit."}
                </p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void restrictionHistoryQuery.refetch()}
                >
                  Retry audit
                </Button>
              </div>
            ) : (
              <div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Episode</TableHead>
                      <TableHead>Action</TableHead>
                      <TableHead>When</TableHead>
                      <TableHead>Reference</TableHead>
                      <TableHead>Disease</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {restrictionHistory?.actions.map((action) => (
                      <TableRow key={action.id}>
                        <TableCell className="tabular-nums">
                          {action.restriction_version}
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={action.action}>{action.action}</StatusBadge>
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
                  label="movement restriction actions"
                />
              </div>
            )}
          </DataTableCard>
        )}

      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <Detail label="Sex">{a.sex === "F" ? "Female" : "Male"}</Detail>
            <Detail label="Breed">{a.breed}</Detail>
            <Detail label="Bucket">{a.current_bucket.replace(/_/g, " ")}</Detail>
            <Detail label="Days in bucket">{a.days_in_current_bucket ?? 0}</Detail>
            <Detail label="Date of birth">
              {a.date_of_birth
                ? formatDate(a.date_of_birth)
                : a.estimated_dob
                  ? `~${formatDate(a.estimated_dob)}`
                  : "—"}
            </Detail>
            <Detail label="Age">{a.age_months != null ? `${a.age_months} months` : "—"}</Detail>
            <Detail label="Birth type">{a.birth_type ?? "—"}</Detail>
            <Detail label="Birth weight">
              {a.birth_weight != null ? `${a.birth_weight} kg` : "—"}
            </Detail>
            <Detail label="Latest weight">
              {a.latest_weight_kg != null ? `${a.latest_weight_kg.toFixed(1)} kg` : "—"}
            </Detail>
            <Detail label="Source">{a.source}</Detail>
            {a.source === "PURCHASED" && (
              <>
                <Detail label="Purchase date">{formatDate(a.purchase_date)}</Detail>
                <Detail label="Purchase price">{formatMoney(a.purchase_price)}</Detail>
                <Detail label="Seller">{a.seller_name ?? "—"}</Detail>
              </>
            )}
            {!active && (
              <>
                <Detail label="Status date">{formatDate(a.status_date)}</Detail>
                {a.status === "SOLD" && (
                  <Detail label="Sale price">{formatMoney(a.sale_price)}</Detail>
                )}
                {a.status === "DEAD" && (
                  <>
                    <Detail label="Mortality cause">{a.mortality_cause ?? "—"}</Detail>
                    <Detail label="Mortality reported">
                      {formatDate(a.mortality_reported_at)}
                    </Detail>
                    <Detail label="Scheduled disease suspected">
                      {a.suspected_scheduled_disease ? "Yes" : "No"}
                    </Detail>
                    {a.suspected_disease && (
                      <Detail label="Suspected disease">{a.suspected_disease}</Detail>
                    )}
                    {a.authority_notified_at && (
                      <Detail label="Authority notified">
                        {formatDate(a.authority_notified_at)}
                      </Detail>
                    )}
                  </>
                )}
              </>
            )}
            {a.restriction_cleared_at && (
              <>
                <Detail label="Restriction cleared">
                  {formatFarmDateTime(a.restriction_cleared_at)}
                </Detail>
                <Detail label="Clearance reference">
                  {a.restriction_clearance_reference ?? "—"}
                </Detail>
              </>
            )}
            {a.dam_id != null && (
              <Detail label="Dam">
                <Link
                  href={`/animals/${a.dam_id}`}
                  className="font-medium text-primary hover:underline"
                >
                  #{a.dam_id}
                </Link>
              </Detail>
            )}
            {a.sire_id != null && (
              <Detail label="Sire">
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
        <DataTableCard title={`Weight history (${profile.weights_total})`}>
          {profile.weights.length === 0 ? (
            <p className="text-muted-foreground">No weight records yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead className="text-right">Weight</TableHead>
                  <TableHead className="text-right">BCS</TableHead>
                  <TableHead>Notes</TableHead>
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
            label="weight records"
          />
        </DataTableCard>

        <DataTableCard title={`Bucket moves (${profile.moves_total})`}>
          {profile.moves.length === 0 ? (
            <p className="text-muted-foreground">No moves recorded.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>From</TableHead>
                  <TableHead>To</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {profile.moves.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell>{formatFarmDateTime(m.moved_at)}</TableCell>
                    <TableCell>{m.from_bucket?.replace(/_/g, " ") ?? "—"}</TableCell>
                    <TableCell>{m.to_bucket.replace(/_/g, " ")}</TableCell>
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
            label="bucket moves"
          />
        </DataTableCard>

        {canViewHealth && (
        <DataTableCard title={`Health events (${profile.health_events_total})`}>
          {profile.health_events.length === 0 ? (
            <p className="text-muted-foreground">No health events.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead className="text-right">Cost</TableHead>
                  <TableHead>Next due</TableHead>
                  <TableHead>Traceability</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {profile.health_events.map((h) => (
                  <TableRow key={h.id}>
                    <TableCell>{formatDate(h.date)}</TableCell>
                    <TableCell>{h.type}</TableCell>
                    <TableCell>{h.product_name ?? h.disease_target ?? "—"}</TableCell>
                    <TableCell className="text-right">{formatMoney(h.cost)}</TableCell>
                    <TableCell>{formatDate(h.next_due_date)}</TableCell>
                    <TableCell>
                      <span className="text-xs">
                        {[
                          h.product_lot ? `Lot ${h.product_lot}` : null,
                          h.certificate_number ? `Cert ${h.certificate_number}` : null,
                          h.withdrawal_until
                            ? `Withdrawal to ${formatDate(h.withdrawal_until)}`
                            : null,
                          h.suspected_scheduled_disease
                            ? "Scheduled-disease hold"
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
            label="health events"
          />
        </DataTableCard>
        )}

        {a.sex === "F" && (
          <DataTableCard title={`Kids (${profile.kids_total})`}>
            {profile.kids.length === 0 ? (
              <p className="text-muted-foreground">No kids recorded.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tag</TableHead>
                    <TableHead>Sex</TableHead>
                    <TableHead>Born</TableHead>
                    <TableHead>Status</TableHead>
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
                      <TableCell>{k.sex}</TableCell>
                      <TableCell>{formatDate(k.date_of_birth)}</TableCell>
                      <TableCell>
                        <StatusBadge status={k.status}>{k.status}</StatusBadge>
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
              label="offspring"
            />
          </DataTableCard>
        )}

        {a.sex === "F" && canViewBreeding && (
          <DataTableCard title={`Breeding history (${profile.breedings_total})`}>
            {profile.breedings.length === 0 ? (
              <p className="text-muted-foreground">No breeding records.</p>
            ) : (
              <ul className="divide-y">
                {profile.breedings.map((recordId) => (
                  <li key={recordId} className="py-2">
                    <Link href="/breeding" className="text-primary underline">
                      Breeding record #{recordId}
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
              label="breeding records"
            />
          </DataTableCard>
        )}
      </div>
    </div>
  );
}

function AnimalProfilePageContent() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const animalId = Number(params.id);
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("animals.view");
  const [kidsOffset, setKidsOffset] = useState(0);
  const [weightsOffset, setWeightsOffset] = useState(0);
  const [movesOffset, setMovesOffset] = useState(0);
  const [healthEventsOffset, setHealthEventsOffset] = useState(0);
  const [breedingsOffset, setBreedingsOffset] = useState(0);
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
        enabled: allowed && Number.isFinite(animalId),
        placeholderData: (previous) => previous,
      },
    },
  );
  const profile = query.data?.status === 200 ? query.data.data : undefined;
  const refresh = useProfileRefresh(animalId);
  const backHref = permittedAppPath(searchParams.get("returnTo"), can) ?? "/animals";
  const backLabel = backHref.startsWith("/dashboard")
    ? "Back to dashboard"
    : backHref.startsWith("/tasks")
      ? "Back to tasks"
      : backHref.startsWith("/health")
        ? "Back to health"
        : "Back to animals";

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
  if (query.isLoading) {
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }
  if (query.isError || !profile) {
    return (
      <div role="alert" className="space-y-3 rounded-lg border border-destructive/40 p-4">
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError ? query.error.detail : "Animal not found."}
        </p>
        <Button type="button" variant="outline" onClick={() => void query.refetch()}>
          Retry animal profile
        </Button>
        <Link href={backHref} className="block text-sm text-primary underline">
          {backLabel}
        </Link>
      </div>
    );
  }
  return (
    <ProfileBody
      profile={profile}
      refresh={refresh}
      backHref={backHref}
      backLabel={backLabel}
      onKidsOffsetChange={setKidsOffset}
      onWeightsOffsetChange={setWeightsOffset}
      onMovesOffsetChange={setMovesOffset}
      onHealthEventsOffsetChange={setHealthEventsOffset}
      onBreedingsOffsetChange={setBreedingsOffset}
    />
  );
}

export default function AnimalProfilePage() {
  return (
    <Suspense fallback={<p className="py-10 text-center text-muted-foreground">Loading…</p>}>
      <AnimalProfilePageContent />
    </Suspense>
  );
}
