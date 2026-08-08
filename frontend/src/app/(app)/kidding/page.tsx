"use client";

/** Kidding — parity with v1's kidding/list.html (overdue/upcoming/history) + new as a dialog. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Baby, CalendarClock, Plus, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useCreateKiddingApiKiddingPost,
  useKiddingListApiKiddingGet,
} from "@/api/generated/endpoints";
import type { BreedingRecordOut, KiddingRecordOut } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
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
import { ApiError } from "@/lib/api-client";
import { farmToday, formatDate } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { usePermissions } from "@/lib/use-permissions";

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

const kidSchema = z.object({
  tag: z.string().max(50, "Max 50 characters").optional(),
  sex: z.enum(["M", "F"]),
  birth_weight: z.number().nonnegative("Must be ≥ 0").nullish(),
  status: z.enum(KID_STATUSES),
});
const kiddingSchema = z.object({
  date: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/, "Pick a valid date")
    .refine((s) => s <= localToday(), "Date can't be in the future"),
  ease: z.enum(EASES),
  notes: z.string().optional(),
  kids: z.array(kidSchema).min(1, "At least one kid").max(MAX_KIDS, "At most 10 kids"),
});
type KiddingValues = z.infer<typeof kiddingSchema>;

function emptyKid(): KiddingValues["kids"][number] {
  return { tag: "", sex: "F", birth_weight: null, status: "ALIVE" };
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
  const {
    control,
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<KiddingValues>({
    resolver: zodResolver(kiddingSchema),
    defaultValues: {
      date: localToday(),
      ease: "NORMAL",
      notes: "",
      kids: [emptyKid(), emptyKid()], // twins are the norm (v1 parity)
    },
  });
  const { fields, append, remove } = useFieldArray({ control, name: "kids" });

  async function onSubmit(values: KiddingValues) {
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
          })),
        },
      });
      toast.success("Kidding recorded.");
      onClose();
      onSaved();
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
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
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="kidding_date">Kidding date *</Label>
              <Input
                id="kidding_date"
                type="date"
                max={localToday()}
                {...register("date")}
              />
              {errors.date && (
                <p className="text-sm text-destructive">{errors.date.message}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label>Ease</Label>
              <Controller
                control={control}
                name="ease"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger className="w-full">
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
            <Textarea id="kidding_notes" rows={2} {...register("notes")} />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Kids</Label>
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
                  <Label className="text-xs">Tag (auto if blank)</Label>
                  <Input {...register(`kids.${index}.tag`)} placeholder="auto" />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Sex</Label>
                  <Controller
                    control={control}
                    name={`kids.${index}.sex`}
                    render={({ field: f }) => (
                      <Select value={f.value} onValueChange={f.onChange} items={KID_SEX_ITEMS}>
                        <SelectTrigger size="sm">
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
                  <Label className="text-xs">Weight (kg)</Label>
                  <Input
                    type="number"
                    step="0.1"
                    min="0"
                    {...register(`kids.${index}.birth_weight`, {
                      setValueAs: (v) => (v === "" || v == null ? null : Number(v)),
                    })}
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Status</Label>
                  <Controller
                    control={control}
                    name={`kids.${index}.status`}
                    render={({ field: f }) => (
                      <Select value={f.value} onValueChange={f.onChange}>
                        <SelectTrigger size="sm">
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
                  aria-label="Remove kid"
                  disabled={fields.length <= 1}
                  onClick={() => remove(index)}
                  className="justify-self-end"
                >
                  <X />
                </Button>
                {(errors.kids?.[index]?.tag || errors.kids?.[index]?.birth_weight) && (
                  <p className="col-span-full text-sm text-destructive">
                    {errors.kids[index]?.tag?.message ??
                      errors.kids[index]?.birth_weight?.message}
                  </p>
                )}
              </div>
            ))}
            {errors.kids?.root && (
              <p className="text-sm text-destructive">{errors.kids.root.message}</p>
            )}
          </div>

          <p className="text-sm text-muted-foreground">
            Alive kids are auto-created as animals (source BORN, dam/sire linked,
            RECOVERY bucket). A weaning task is auto-created for kidding date + 60 days.
          </p>
          <DialogFooter>
            <Button variant="outline" type="button" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : "Save kidding"}
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
          ({kid.sex}, {kid.status.toLowerCase()})
        </span>
      ))}
    </span>
  );
}

export default function KiddingPage() {
  const queryClient = useQueryClient();
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("kidding.view");
  const canManage = can("kidding.manage");
  const canViewAnimals = can("animals.view");
  const [recordFor, setRecordFor] = useState<BreedingRecordOut | null>(null);
  const [prefillDone, setPrefillDone] = useState(false);
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const query = useKiddingListApiKiddingGet(
    { limit, offset },
    { query: { enabled: allowed } },
  );
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  // /kidding/new?breeding_id=… redirects here: auto-open the record dialog
  // for that breeding record once the list has loaded.
  useEffect(() => {
    if (!canManage || !payload || prefillDone) return;
    const breedingId = new URLSearchParams(window.location.search).get("breeding_id");
    if (!breedingId) return;
    // One-time initialization from URL params — runs once, not reactive.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPrefillDone(true);
    const record = [...payload.overdue, ...payload.upcoming].find(
      (r) => String(r.id) === breedingId,
    );
    if (record) setRecordFor(record);
  }, [canManage, payload, prefillDone]);

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
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError
            ? query.error.detail
            : "Could not load kidding data."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  // "Xd late" compares against the backend's UTC today, not local (7-5).
  const today = farmToday();

  function recordButton(r: BreedingRecordOut) {
    if (!canManage) return null;
    return (
      <Button variant="outline" size="sm" onClick={() => setRecordFor(r)}>
        Record kidding
      </Button>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Kidding"
        description="Confirmed pregnancies due soon and recent kidding history."
      />

      {payload.overdue.length > 0 && (
        <DataTableCard
          className="border-red-200 bg-red-50/60 dark:border-red-900 dark:bg-red-950/30"
          title={
            <span className="flex items-center gap-2 text-red-700 dark:text-red-400">
              <AlertTriangle className="size-4" />
              Overdue (past expected date, no kidding recorded)
            </span>
          }
        >
          <Table>
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
        </DataTableCard>
      )}

      <DataTableCard
        title="Upcoming (next 30 days)"
        description="Confirmed pregnancies with an expected kidding date in the next 30 days."
      >
        {payload.upcoming.length === 0 ? (
          <EmptyState
            icon={CalendarClock}
            title="No confirmed pregnancies due in the next 30 days."
          />
        ) : (
          <Table>
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
          <Table>
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
          onOffsetChange={setOffset}
          label="kidding records"
        />
      </DataTableCard>

      {recordFor && (
        <RecordKiddingDialog
          key={recordFor.id}
          breeding={recordFor}
          onClose={() => setRecordFor(null)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}
