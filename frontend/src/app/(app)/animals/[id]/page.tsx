"use client";

/** Animal profile — parity with v1's animals/profile.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type ReactNode } from "react";
import { Controller, useForm , useWatch} from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getAnimalProfileApiAnimalsAnimalIdGetQueryKey,
  getListAnimalsApiAnimalsGetQueryKey,
  useAnimalProfileApiAnimalsAnimalIdGet,
  useChangeStatusApiAnimalsAnimalIdStatusPost,
  useMoveBucketApiAnimalsAnimalIdMovePost,
  useRecordWeightApiAnimalsAnimalIdWeightPost,
} from "@/api/generated/endpoints";
import {
  MoveInToBucket,
  StatusChangeInNewStatus,
  type AnimalProfileOut,
} from "@/api/generated/models";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { formatDate, formatMoney } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";

const BUCKETS = Object.values(MoveInToBucket);
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
    queryClient.invalidateQueries({
      queryKey: getAnimalProfileApiAnimalsAnimalIdGetQueryKey(animalId),
    });
    queryClient.invalidateQueries({ queryKey: getListAnimalsApiAnimalsGetQueryKey() });
  };
}

const weightSchema = z.object({
  date: z.string().optional(),
  weight_kg: z.coerce.number().positive("Weight must be greater than 0"),
  bcs: optNum(z.number().int().min(1).max(5)),
  notes: z.string().optional(),
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
            <Input id="w_date" type="date" {...register("date")} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="w_kg">Weight (kg) *</Label>
            <Input id="w_kg" type="number" step="0.01" min="0" {...register("weight_kg")} />
            {errors.weight_kg && (
              <p className="text-sm text-destructive">{errors.weight_kg.message}</p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="w_bcs">BCS (1–5)</Label>
            <Input id="w_bcs" type="number" min="1" max="5" {...register("bcs")} />
            {errors.bcs && <p className="text-sm text-destructive">{errors.bcs.message}</p>}
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
  reason: z.string().optional(),
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
    formState: { isSubmitting },
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
            <Label>To bucket *</Label>
            <Controller
              control={control}
              name="to_bucket"
              render={({ field }) => (
                <Select value={field.value ?? ""} onValueChange={field.onChange} items={BUCKET_ITEMS}>
                  <SelectTrigger className="w-full">
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

const statusSchema = z.object({
  new_status: z.enum([
    StatusChangeInNewStatus.SOLD,
    StatusChangeInNewStatus.DEAD,
    StatusChangeInNewStatus.CULLED,
  ]),
  date: z.string().optional(),
  sale_price: optNum(z.number().nonnegative()),
  buyer_name: z.string().max(120).optional(),
  notes: z.string().optional(),
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
    reset,
    formState: { errors, isSubmitting },
  } = useForm<StatusInput, unknown, StatusValues>({
    resolver: zodResolver(statusSchema),
    defaultValues: { new_status: StatusChangeInNewStatus.SOLD },
  });
  const newStatus = useWatch({ control, name: "new_status" });

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
            <Label>New status *</Label>
            <Controller
              control={control}
              name="new_status"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger className="w-full">
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
            <Input id="s_date" type="date" {...register("date")} />
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
                  {...register("sale_price")}
                />
                {errors.sale_price && (
                  <p className="text-sm text-destructive">{errors.sale_price.message}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="s_buyer">Buyer name</Label>
                <Input id="s_buyer" {...register("buyer_name")} />
              </div>
            </>
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

function ProfileBody({ profile, refresh }: { profile: AnimalProfileOut; refresh: () => void }) {
  const { can } = usePermissions();
  const a = profile.animal;
  const active = a.status === "ACTIVE";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">
          {a.tag_number}
          {a.name ? ` · ${a.name}` : ""}{" "}
          <Badge variant={active ? "default" : "secondary"}>{a.status}</Badge>
        </h1>
        {active && (
          <div className="flex gap-2">
            {can("animals.weight") && <AddWeightDialog animalId={a.id} onDone={refresh} />}
            {can("animals.move") && (
              <MoveBucketDialog animalId={a.id} currentBucket={a.current_bucket} onDone={refresh} />
            )}
            {can("animals.status") && <StatusDialog animalId={a.id} onDone={refresh} />}
          </div>
        )}
      </div>

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
              </>
            )}
            {a.dam_id != null && (
              <Detail label="Dam">
                <Link href={`/animals/${a.dam_id}`} className="text-primary underline">
                  #{a.dam_id}
                </Link>
              </Detail>
            )}
            {a.sire_id != null && (
              <Detail label="Sire">
                <Link href={`/animals/${a.sire_id}`} className="text-primary underline">
                  #{a.sire_id}
                </Link>
              </Detail>
            )}
          </dl>
          {a.notes && <p className="mt-4 text-sm text-muted-foreground">{a.notes}</p>}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Weight history ({profile.weights.length})</CardTitle>
          </CardHeader>
          <CardContent>
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
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Bucket moves ({profile.moves.length})</CardTitle>
          </CardHeader>
          <CardContent>
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
                      <TableCell>{formatDate(m.moved_at)}</TableCell>
                      <TableCell>{m.from_bucket?.replace(/_/g, " ") ?? "—"}</TableCell>
                      <TableCell>{m.to_bucket.replace(/_/g, " ")}</TableCell>
                      <TableCell>{m.reason ?? ""}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Health events ({profile.health_events.length})</CardTitle>
          </CardHeader>
          <CardContent>
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
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {a.sex === "F" && (
          <Card>
            <CardHeader>
              <CardTitle>Kids ({profile.kids.length})</CardTitle>
            </CardHeader>
            <CardContent>
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
                          <Link href={`/animals/${k.id}`} className="text-primary underline">
                            {k.tag_number}
                          </Link>
                        </TableCell>
                        <TableCell>{k.sex}</TableCell>
                        <TableCell>{formatDate(k.date_of_birth)}</TableCell>
                        <TableCell>
                          <Badge variant={k.status === "ACTIVE" ? "default" : "secondary"}>
                            {k.status}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

export default function AnimalProfilePage() {
  const params = useParams<{ id: string }>();
  const animalId = Number(params.id);
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("animals.view");
  const query = useAnimalProfileApiAnimalsAnimalIdGet(animalId, {
    query: { enabled: allowed && Number.isFinite(animalId) },
  });
  const profile = query.data?.status === 200 ? query.data.data : undefined;
  const refresh = useProfileRefresh(animalId);

  if (permsLoading) {
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }
  if (!allowed) {
    return <p className="text-muted-foreground">You don&apos;t have access to this page.</p>;
  }
  if (query.isLoading) {
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }
  if (query.isError || !profile) {
    return (
      <p className="text-sm text-destructive">
        {query.error instanceof ApiError ? query.error.detail : "Animal not found."}
      </p>
    );
  }
  return <ProfileBody profile={profile} refresh={refresh} />;
}
