"use client";

/** Herd list with bucket/sex/status filters + tag search — parity with v1's animals/list.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Search, SearchX } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Controller, useForm , useWatch} from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getListAnimalsApiAnimalsGetQueryKey,
  useCreateAnimalApiAnimalsPost,
  useListAnimalsApiAnimalsGet,
} from "@/api/generated/endpoints";
import {
  AnimalCreateInBirthType,
  AnimalCreateInCurrentBucket,
  AnimalCreateInSex,
  AnimalCreateInSource,
  ListAnimalsApiAnimalsGetBucket,
  ListAnimalsApiAnimalsGetSex,
  ListAnimalsApiAnimalsGetStatus,
  type ListAnimalsApiAnimalsGetParams,
} from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
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
import { usePermissions } from "@/lib/use-permissions";

const ALL = "ALL";
const BUCKETS = Object.values(AnimalCreateInCurrentBucket);
const BIRTH_TYPES = Object.values(AnimalCreateInBirthType);

const bucketLabel = (b: string) => b.replace(/_/g, " ");
/** value → label maps for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
const BUCKET_ITEMS: Record<string, string> = Object.fromEntries(
  BUCKETS.map((b) => [b, bucketLabel(b)]),
);
const SEX_ITEMS: Record<string, string> = {
  [AnimalCreateInSex.F]: "Female",
  [AnimalCreateInSex.M]: "Male",
};
const SOURCE_ITEMS: Record<string, string> = {
  [AnimalCreateInSource.BORN]: "Born on farm",
  [AnimalCreateInSource.PURCHASED]: "Purchased",
};
const BUCKET_FILTER_ITEMS: Record<string, string> = {
  [ALL]: "All buckets",
  ...Object.fromEntries(Object.values(ListAnimalsApiAnimalsGetBucket).map((b) => [b, bucketLabel(b)])),
};
const SEX_FILTER_ITEMS: Record<string, string> = { [ALL]: "Both sexes", ...SEX_ITEMS };

const optNum = (schema: z.ZodNumber) =>
  z.preprocess(
    (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)),
    schema.optional(),
  );

const createSchema = z.object({
  tag_number: z.string().max(50).optional().or(z.literal("")),
  name: z.string().max(80).optional(),
  sex: z.enum([AnimalCreateInSex.M, AnimalCreateInSex.F]),
  source: z.enum([AnimalCreateInSource.BORN, AnimalCreateInSource.PURCHASED]),
  current_bucket: z.enum(BUCKETS as [string, ...string[]]),
  breed: z.string().max(60).optional(),
  date_of_birth: z.string().optional(),
  estimated_dob: z.string().optional(),
  birth_type: z.enum([...BIRTH_TYPES] as [string, ...string[]]).optional(),
  birth_weight: optNum(z.number().nonnegative()),
  purchase_date: z.string().optional(),
  purchase_price: optNum(z.number().nonnegative()),
  seller_name: z.string().max(120).optional(),
  weight_kg: optNum(z.number().positive()),
  notes: z.string().optional(),
});
type CreateInput = z.input<typeof createSchema>;
type CreateValues = z.output<typeof createSchema>;

const emptyToNull = (v: string | undefined) => (v ? v : null);

function CreateAnimalDialog({
  onCreated,
  startOpen = false,
}: {
  onCreated: () => void;
  startOpen?: boolean;
}) {
  const [open, setOpen] = useState(startOpen);
  const createMut = useCreateAnimalApiAnimalsPost();
  const {
    register,
    handleSubmit,
    control,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<CreateInput, unknown, CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      sex: AnimalCreateInSex.F,
      source: AnimalCreateInSource.BORN,
      current_bucket: AnimalCreateInCurrentBucket.QUARANTINE,
      breed: "Osmanabadi",
    },
  });
  const source = useWatch({ control, name: "source" });

  async function onSubmit(values: CreateValues) {
    try {
      await createMut.mutateAsync({
        data: {
          tag_number: values.tag_number?.trim() || undefined,
          name: emptyToNull(values.name),
          sex: values.sex,
          source: values.source,
          current_bucket: values.current_bucket as AnimalCreateInCurrentBucket,
          breed: values.breed || "Osmanabadi",
          date_of_birth: emptyToNull(values.date_of_birth),
          estimated_dob: emptyToNull(values.estimated_dob),
          birth_type: (values.birth_type || null) as AnimalCreateInBirthType,
          birth_weight: values.birth_weight ?? null,
          purchase_date: emptyToNull(values.purchase_date),
          purchase_price: values.purchase_price ?? null,
          seller_name: emptyToNull(values.seller_name),
          weight_kg: values.weight_kg ?? null,
          notes: emptyToNull(values.notes),
        },
      });
      toast.success("Animal added.");
      reset();
      setOpen(false);
      onCreated();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Something went wrong");
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button onClick={() => setOpen(true)}>Add animal</Button>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add animal</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3" noValidate>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="tag_number">Tag number</Label>
              <Input
                id="tag_number"
                placeholder="Auto-generated if left blank (e.g. G-7KP2D)"
                {...register("tag_number")}
              />
              {errors.tag_number && (
                <p className="text-sm text-destructive">{errors.tag_number.message}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="name">Name</Label>
              <Input id="name" {...register("name")} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="animal-sex">Sex *</Label>
              <Controller
                control={control}
                name="sex"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange} items={SEX_ITEMS}>
                    <SelectTrigger id="animal-sex" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={AnimalCreateInSex.F}>Female</SelectItem>
                      <SelectItem value={AnimalCreateInSex.M}>Male</SelectItem>
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="animal-source">Source *</Label>
              <Controller
                control={control}
                name="source"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange} items={SOURCE_ITEMS}>
                    <SelectTrigger id="animal-source" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={AnimalCreateInSource.BORN}>Born on farm</SelectItem>
                      <SelectItem value={AnimalCreateInSource.PURCHASED}>Purchased</SelectItem>
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="animal-bucket">Bucket *</Label>
              <Controller
                control={control}
                name="current_bucket"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange} items={BUCKET_ITEMS}>
                    <SelectTrigger id="animal-bucket" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {BUCKETS.map((b) => (
                        <SelectItem key={b} value={b}>
                          {bucketLabel(b)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="breed">Breed</Label>
              <Input id="breed" {...register("breed")} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="date_of_birth">Date of birth</Label>
              <Input id="date_of_birth" type="date" {...register("date_of_birth")} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="estimated_dob">Estimated DOB</Label>
              <Input id="estimated_dob" type="date" {...register("estimated_dob")} />
            </div>
            <div className="space-y-1.5">
              <Label>Birth type</Label>
              <Controller
                control={control}
                name="birth_type"
                render={({ field }) => (
                  <Select value={field.value ?? ""} onValueChange={field.onChange}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="—" />
                    </SelectTrigger>
                    <SelectContent>
                      {BIRTH_TYPES.map((t) => (
                        <SelectItem key={t} value={t}>
                          {t}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="birth_weight">Birth weight (kg)</Label>
              <Input id="birth_weight" type="number" step="0.01" min="0" {...register("birth_weight")} />
              {errors.birth_weight && (
                <p className="text-sm text-destructive">{errors.birth_weight.message}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="weight_kg">Entry weight (kg)</Label>
              <Input id="weight_kg" type="number" step="0.01" min="0" {...register("weight_kg")} />
              {errors.weight_kg && (
                <p className="text-sm text-destructive">{errors.weight_kg.message}</p>
              )}
            </div>
          </div>

          {source === AnimalCreateInSource.PURCHASED && (
            <div className="grid grid-cols-2 gap-3 rounded-lg border p-3">
              <div className="space-y-1.5">
                <Label htmlFor="purchase_date">Purchase date</Label>
                <Input id="purchase_date" type="date" {...register("purchase_date")} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="purchase_price">Purchase price (₹)</Label>
                <Input
                  id="purchase_price"
                  type="number"
                  step="0.01"
                  min="0"
                  {...register("purchase_price")}
                />
                {errors.purchase_price && (
                  <p className="text-sm text-destructive">{errors.purchase_price.message}</p>
                )}
              </div>
              <div className="col-span-2 space-y-1.5">
                <Label htmlFor="seller_name">Seller name</Label>
                <Input id="seller_name" {...register("seller_name")} />
              </div>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="notes">Notes</Label>
            <Textarea id="notes" rows={2} {...register("notes")} />
          </div>

          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : "Save animal"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AnimalsPageContent() {
  const queryClient = useQueryClient();
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("animals.view");
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const [bucket, setBucket] = useState(searchParams.get("bucket") ?? ALL);
  const [sex, setSex] = useState(searchParams.get("sex") ?? ALL);
  const [status, setStatus] = useState(searchParams.get("status") ?? ALL);
  const [q, setQ] = useState(searchParams.get("q") ?? "");
  // Same-route client navigations (e.g. a dashboard bucket link while already
  // on /animals) change the params — re-sync the filters. Keyed
  // off the param STRING: useSearchParams' object identity isn't stable.
  const paramsKey = searchParams.toString();
  useEffect(() => {
    const params = new URLSearchParams(paramsKey);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setBucket(params.get("bucket") ?? ALL);
    setSex(params.get("sex") ?? ALL);
    setStatus(params.get("status") ?? ALL);
    setQ(params.get("q") ?? "");
  }, [paramsKey]);
  // ?new=1 opens the create dialog once; strip it so a reload doesn't reopen
  // the dialog.
  useEffect(() => {
    const params = new URLSearchParams(paramsKey);
    if (params.get("new") !== "1") return;
    params.delete("new");
    const rest = params.toString();
    router.replace(rest ? `${pathname}?${rest}` : pathname);
  }, [paramsKey, pathname, router]);
  // Debounce the search box (~300ms) so typing doesn't fire a request per
  // keystroke; the input itself stays instant.
  const [debouncedQ, setDebouncedQ] = useState(q);
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(handle);
  }, [q]);

  const params: ListAnimalsApiAnimalsGetParams = {
    ...(bucket !== ALL && { bucket: bucket as ListAnimalsApiAnimalsGetBucket }),
    ...(sex !== ALL && { sex: sex as ListAnimalsApiAnimalsGetSex }),
    ...(status !== ALL && { status: status as ListAnimalsApiAnimalsGetStatus }),
    ...(debouncedQ.trim() && { q: debouncedQ.trim() }),
  };
  const query = useListAnimalsApiAnimalsGet(params, { query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;

  function refresh() {
    queryClient.invalidateQueries({ queryKey: getListAnimalsApiAnimalsGetQueryKey() });
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

  return (
    <div className="space-y-6">
      <PageHeader
        title="Animals"
        description="Your herd at a glance — filter by bucket, sex or status, or search by tag."
        actions={
          can("animals.create") && (
            <CreateAnimalDialog
              onCreated={refresh}
              startOpen={searchParams.get("new") === "1"}
            />
          )
        }
      />

      <div className="flex flex-wrap items-center gap-3">
        <Select value={bucket} onValueChange={setBucket} items={BUCKET_FILTER_ITEMS}>
          <SelectTrigger>
            <SelectValue placeholder="All buckets" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All buckets</SelectItem>
            {Object.values(ListAnimalsApiAnimalsGetBucket).map((b) => (
              <SelectItem key={b} value={b}>
                {bucketLabel(b)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sex} onValueChange={setSex} items={SEX_FILTER_ITEMS}>
          <SelectTrigger>
            <SelectValue placeholder="Both sexes" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Both sexes</SelectItem>
            <SelectItem value={ListAnimalsApiAnimalsGetSex.F}>Female</SelectItem>
            <SelectItem value={ListAnimalsApiAnimalsGetSex.M}>Male</SelectItem>
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger>
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All statuses</SelectItem>
            {Object.values(ListAnimalsApiAnimalsGetStatus).map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search tag or name…"
            className="w-56 pl-8"
          />
        </div>
      </div>

      {query.isLoading ? (
        <p className="py-10 text-center text-muted-foreground">Loading…</p>
      ) : query.isError ? (
        <p className="text-sm text-destructive">
          {query.error instanceof ApiError ? query.error.detail : "Could not load animals."}
        </p>
      ) : !payload || payload.animals.length === 0 ? (
        <EmptyState
          icon={SearchX}
          title="No animals found"
          description="No animals match these filters."
        />
      ) : (
        <DataTableCard title="Herd" description={`${payload.total} animal(s)`}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tag</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Sex</TableHead>
                <TableHead>Breed</TableHead>
                <TableHead>Bucket</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Age (mo)</TableHead>
                <TableHead className="text-right">Weight</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payload.animals.map((a) => (
                <TableRow key={a.id}>
                  <TableCell>
                    <Link
                      href={`/animals/${a.id}`}
                      className="font-medium text-foreground hover:text-primary"
                    >
                      {a.tag_number}
                    </Link>
                  </TableCell>
                  <TableCell>{a.name ?? "—"}</TableCell>
                  <TableCell>{a.sex}</TableCell>
                  <TableCell>{a.breed}</TableCell>
                  <TableCell>{a.current_bucket.replace(/_/g, " ")}</TableCell>
                  <TableCell>
                    <StatusBadge status={a.status}>{a.status}</StatusBadge>
                  </TableCell>
                  <TableCell className="text-right">{a.age_months ?? "—"}</TableCell>
                  <TableCell className="text-right">
                    {a.latest_weight_kg != null ? `${a.latest_weight_kg.toFixed(1)} kg` : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DataTableCard>
      )}
    </div>
  );
}

/** Suspense boundary required because the content reads useSearchParams(). */
export default function AnimalsPage() {
  return (
    <Suspense fallback={<p className="py-10 text-center text-muted-foreground">Loading…</p>}>
      <AnimalsPageContent />
    </Suspense>
  );
}
