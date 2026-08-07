"use client";

/** Breeding records — parity with v1's breeding/list.html (+ new/ultrasound as dialogs). */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getBreedingListApiBreedingGetQueryKey,
  getKiddingListApiKiddingGetQueryKey,
  getListAnimalsApiAnimalsGetQueryKey,
  useAbortPregnancyApiBreedingRecordIdAbortPost,
  useBreedingListApiBreedingGet,
  useCreateBreedingApiBreedingPost,
  useListAnimalsApiAnimalsGet,
  useSubmitUltrasoundApiBreedingRecordIdUltrasoundPost,
} from "@/api/generated/endpoints";
import type { AnimalOut, BreedingRecordOut } from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
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
import { formatDate } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";

function localToday(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

function animalLabel(a: AnimalOut): string {
  return a.tag_number + (a.name ? ` · ${a.name}` : "");
}

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Something went wrong";
}

/** v1's tag-{outcome} styles as a Badge. */
function OutcomeBadge({ outcome }: { outcome: string }) {
  const variant =
    outcome === "CONFIRMED_PREGNANT"
      ? "default"
      : outcome === "PENDING"
        ? "secondary"
        : outcome === "FAILED"
          ? "outline"
          : "destructive";
  return <Badge variant={variant}>{outcome.replace(/_/g, " ")}</Badge>;
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
  const animalsQuery = useListAnimalsApiAnimalsGet(undefined, {
    query: { enabled: open },
  });
  const animals =
    animalsQuery.data?.status === 200 ? animalsQuery.data.data.animals : [];
  const does = animals.filter((a) => candidateDoeIds.includes(a.id));
  const bucks = animals.filter((a) => activeBuckIds.includes(a.id));
  /** value → label maps for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const doeItems: Record<string, string> = Object.fromEntries(
    does.map((d) => [
      String(d.id),
      `${animalLabel(d)}${d.age_months != null ? ` — ${d.age_months} mo` : ""}${
        d.latest_weight_kg != null ? `, ${d.latest_weight_kg.toFixed(1)} kg` : ""
      }`,
    ]),
  );
  const buckItems: Record<string, string> = Object.fromEntries(
    bucks.map((b) => [
      String(b.id),
      `${animalLabel(b)}${b.age_months != null ? ` — ${b.age_months} mo` : ""}`,
    ]),
  );

  const createMutation = useCreateBreedingApiBreedingPost();
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
        {animalsQuery.isLoading ? (
          <p className="text-muted-foreground">Loading animals…</p>
        ) : does.length === 0 ? (
          <p className="text-muted-foreground">
            No breeding-ready does right now (female, ≥10 months, ≥22 kg, not
            pregnant).
          </p>
        ) : (
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label>Doe *</Label>
              <Controller
                control={control}
                name="doe_id"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange} items={doeItems}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Select doe" />
                    </SelectTrigger>
                    <SelectContent>
                      {does.map((d) => (
                        <SelectItem key={d.id} value={String(d.id)}>
                          {animalLabel(d)}
                          {d.age_months != null ? ` — ${d.age_months} mo` : ""}
                          {d.latest_weight_kg != null
                            ? `, ${d.latest_weight_kg.toFixed(1)} kg`
                            : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.doe_id && (
                <p className="text-sm text-destructive">{errors.doe_id.message}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label>Buck *</Label>
              <Controller
                control={control}
                name="buck_id"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange} items={buckItems}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Select buck" />
                    </SelectTrigger>
                    <SelectContent>
                      {bucks.map((b) => (
                        <SelectItem key={b.id} value={String(b.id)}>
                          {animalLabel(b)}
                          {b.age_months != null ? ` — ${b.age_months} mo` : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.buck_id && (
                <p className="text-sm text-destructive">{errors.buck_id.message}</p>
              )}
              {bucks.length === 0 && (
                <p className="text-sm text-destructive">
                  No active bucks on this farm — add one first.
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="breeding_date">Breeding date *</Label>
              <Input
                id="breeding_date"
                type="date"
                max={localToday()}
                {...register("breeding_date")}
              />
              {errors.breeding_date && (
                <p className="text-sm text-destructive">
                  {errors.breeding_date.message}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button type="submit" disabled={isSubmitting || bucks.length === 0}>
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
  const [saving, setSaving] = useState(false);
  const mutation = useSubmitUltrasoundApiBreedingRecordIdUltrasoundPost();

  async function onSubmit() {
    setSaving(true);
    try {
      await mutation.mutateAsync({
        recordId: record.id,
        data: {
          pregnant,
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
              <Label>Kid count detected</Label>
              <Select value={kidCount} onValueChange={(v) => setKidCount(String(v))}>
                <SelectTrigger>
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
          <Button onClick={onSubmit} disabled={saving}>
            {saving ? "Saving…" : "Save result"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function BreedingPage() {
  const queryClient = useQueryClient();
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("breeding.view");
  const canManage = can("breeding.manage");
  const query = useBreedingListApiBreedingGet({ query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  const [newOpen, setNewOpen] = useState(false);
  const [ultrasoundFor, setUltrasoundFor] = useState<BreedingRecordOut | null>(null);
  const [prefillDone, setPrefillDone] = useState(false);

  // /breeding/{id}/ultrasound redirects to /breeding?ultrasound_id=…: auto-open
  // the ultrasound dialog for that record once the list has loaded.
  useEffect(() => {
    if (!canManage || !payload || prefillDone) return;
    const recordId = new URLSearchParams(window.location.search).get("ultrasound_id");
    if (!recordId) return;
    // One-time initialization from URL params — runs once, not reactive.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPrefillDone(true);
    const record = payload.records.find(
      (r) => String(r.id) === recordId && r.outcome === "PENDING",
    );
    if (record) setUltrasoundFor(record);
  }, [canManage, payload, prefillDone]);
  const abortMutation = useAbortPregnancyApiBreedingRecordIdAbortPost();

  function refresh() {
    queryClient.invalidateQueries({ queryKey: getBreedingListApiBreedingGetQueryKey() });
    queryClient.invalidateQueries({ queryKey: getKiddingListApiKiddingGetQueryKey() });
    queryClient.invalidateQueries({ queryKey: getListAnimalsApiAnimalsGetQueryKey() });
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
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Breeding</h1>
        {canManage && (
          <Button onClick={() => setNewOpen(true)}>+ Add breeding</Button>
        )}
      </div>

      {payload.records.length === 0 ? (
        <p className="text-muted-foreground">No breeding records yet.</p>
      ) : (
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
                  <Link href={`/animals/${r.doe_id}`} className="text-primary underline">
                    {r.doe_tag ?? `Doe #${r.doe_id}`}
                  </Link>
                </TableCell>
                <TableCell>
                  <Link href={`/animals/${r.buck_id}`} className="text-primary underline">
                    {r.buck_tag ?? `Buck #${r.buck_id}`}
                  </Link>
                </TableCell>
                <TableCell>{r.heat_cycle_number}</TableCell>
                <TableCell>
                  {r.ultrasound_done
                    ? "done"
                    : r.ultrasound_date
                      ? `due ${formatDate(r.ultrasound_date)}`
                      : "—"}
                </TableCell>
                <TableCell>{r.kid_count_detected ?? "—"}</TableCell>
                <TableCell>{formatDate(r.expected_kidding_date)}</TableCell>
                <TableCell>
                  <OutcomeBadge outcome={r.outcome} />
                </TableCell>
                {canManage && (
                  <TableCell className="space-x-2 text-right">
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
      {ultrasoundFor && (
        <UltrasoundDialog
          key={ultrasoundFor.id}
          record={ultrasoundFor}
          onClose={() => setUltrasoundFor(null)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}
