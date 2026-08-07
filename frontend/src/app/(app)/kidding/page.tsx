"use client";

/** Kidding — parity with v1's kidding/list.html (overdue/upcoming/history) + new as a dialog. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getBreedingListApiBreedingGetQueryKey,
  getKiddingListApiKiddingGetQueryKey,
  getListAnimalsApiAnimalsGetQueryKey,
  useCreateKiddingApiKiddingPost,
  useKiddingListApiKiddingGet,
} from "@/api/generated/endpoints";
import type { BreedingRecordOut, KiddingRecordOut } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { formatDate } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";

function localToday(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
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
                + Add kid
              </Button>
            </div>
            {fields.map((field, index) => (
              <div
                key={field.id}
                className="grid grid-cols-[1fr_90px_110px_120px_auto] items-end gap-2"
              >
                <div className="space-y-1">
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
                  disabled={fields.length <= 1}
                  onClick={() => remove(index)}
                >
                  ✕
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
function KidsCell({ kidding }: { kidding: KiddingRecordOut }) {
  const kids = kidding.kids ?? [];
  if (kids.length === 0) return <span>—</span>;
  return (
    <span>
      {kids.map((kid, i) => (
        <span key={kid.id}>
          {i > 0 && ", "}
          {kid.animal_id ? (
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
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("kidding.view");
  const canManage = can("kidding.manage");
  const query = useKiddingListApiKiddingGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  const [recordFor, setRecordFor] = useState<BreedingRecordOut | null>(null);
  const [prefillDone, setPrefillDone] = useState(false);

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
    queryClient.invalidateQueries({ queryKey: getKiddingListApiKiddingGetQueryKey() });
    queryClient.invalidateQueries({ queryKey: getBreedingListApiBreedingGetQueryKey() });
    queryClient.invalidateQueries({ queryKey: getListAnimalsApiAnimalsGetQueryKey() });
  }

  if (permsLoading) {
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
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

  const today = localToday();

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
      <h1 className="text-2xl font-semibold">Kidding</h1>

      {payload.overdue.length > 0 && (
        <Card className="ring-destructive/40">
          <CardHeader>
            <CardTitle>⚠ Overdue (past expected date, no kidding recorded)</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableBody>
                {payload.overdue.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>
                      <Link
                        href={`/animals/${r.doe_id}`}
                        className="text-primary underline"
                      >
                        {r.doe_tag ?? `Doe #${r.doe_id}`}
                      </Link>
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
          </CardContent>
        </Card>
      )}

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Upcoming (next 30 days)</h2>
        {payload.upcoming.length === 0 ? (
          <p className="text-muted-foreground">
            No confirmed pregnancies due in the next 30 days.
          </p>
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
                    <Link
                      href={`/animals/${r.doe_id}`}
                      className="text-primary underline"
                    >
                      {r.doe_tag ?? `Doe #${r.doe_id}`}
                    </Link>
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
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Recent kiddings</h2>
        {payload.records.length === 0 ? (
          <p className="text-muted-foreground">No kiddings recorded yet.</p>
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
                    <Link
                      href={`/animals/${k.doe_id}`}
                      className="text-primary underline"
                    >
                      {k.doe_tag ?? `Doe #${k.doe_id}`}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{k.ease}</Badge>
                  </TableCell>
                  <TableCell>
                    <KidsCell kidding={k} />
                  </TableCell>
                  <TableCell>{k.notes ?? ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

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
