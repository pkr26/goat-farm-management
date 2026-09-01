"use client";

/** Herd list with bucket/sex/status filters + tag search — parity with v1's animals/list.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { PawPrint, Search, SearchX } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
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
import { PaginationControls } from "@/components/pagination-controls";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
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
import { useFarmType } from "@/hooks/use-farm-type";
import { ApiError } from "@/lib/api-client";
import { enumLabel } from "@/lib/enum-labels";
import { farmToday } from "@/lib/format";
import {
  isPersistableNonnegativeMoney,
  isPersistableNonnegativeWeight,
  MIN_PERSISTED_KG,
  MIN_PERSISTED_KG_MESSAGE,
  MIN_PERSISTED_MONEY_MESSAGE,
} from "@/lib/persisted-numbers";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { PermissionsError } from "@/components/permissions-error";

const ALL = "ALL";
const PAGE_SIZE = 50;
/** Mirrors backend/app/api/animals.py `q: Query(max_length=60)`. */
const MAX_TAG_SEARCH = 60;
const clampSearch = (value: string) => value.slice(0, MAX_TAG_SEARCH);
/** Mirrors backend/app/schemas/common.py MAX_PAGE_OFFSET (`offset: Query(le=…)`).
 * A larger offset is a 422, and an error response never reaches the
 * out-of-range self-healing below — the list would just dead-end. */
const MAX_PAGE_OFFSET = 1_000_000;
const MAX_PAGE = Math.floor(MAX_PAGE_OFFSET / PAGE_SIZE) + 1;
const BUCKETS = Object.values(AnimalCreateInCurrentBucket);
const BIRTH_TYPES = Object.values(AnimalCreateInBirthType);
const WORKFLOW_ONLY_INITIAL_BUCKETS = new Set<string>([
  AnimalCreateInCurrentBucket.PREGNANCY_EARLY,
  AnimalCreateInCurrentBucket.PREGNANCY_LATE,
  AnimalCreateInCurrentBucket.DELIVERY,
  AnimalCreateInCurrentBucket.RECOVERY,
]);
const HISTORICAL_IMPORT_BUCKETS = BUCKETS.filter(
  (bucket) => !WORKFLOW_ONLY_INITIAL_BUCKETS.has(bucket),
);
/** Sex each bucket is reserved for, mirroring backend/app/schemas/animals.py
 * (and the ck_animals_bucket_sex CHECK). Buckets absent here take both. */
const BUCKET_REQUIRED_SEX: Record<string, string> = {
  [AnimalCreateInCurrentBucket.MALE_KIDS]: AnimalCreateInSex.M,
  [AnimalCreateInCurrentBucket.FEMALE_KIDS]: AnimalCreateInSex.F,
  [AnimalCreateInCurrentBucket.RESTING]: AnimalCreateInSex.F,
};
const bucketAllowsSex = (bucket: string, sex: string) =>
  (BUCKET_REQUIRED_SEX[bucket] ?? sex) === sex;

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
  [AnimalCreateInSource.BORN]: "Historical born-on-farm import",
  [AnimalCreateInSource.PURCHASED]: "Purchased",
};
const SEX_FILTER_ITEMS: Record<string, string> = { [ALL]: "Both sexes", ...SEX_ITEMS };
const STATUS_FILTER_ITEMS: Record<string, string> = {
  [ALL]: "All statuses",
  ...Object.fromEntries(Object.values(ListAnimalsApiAnimalsGetStatus).map((s) => [s, s])),
};

/** Zero is meaningful for optional weights; anything smaller rounds away. */
const MIN_PERSISTED_WEIGHT_MESSAGE = "Weight must be 0 kg or at least 0.0005 kg";

const optNum = (schema: z.ZodNumber) =>
  z.preprocess(
    (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)),
    schema.optional(),
  );

function localToday(): string {
  return farmToday();
}

function completedMonths(dateOfBirth: string, referenceDate: string): number | null {
  const birth = dateOfBirth.split("-").map(Number);
  const reference = referenceDate.split("-").map(Number);
  if (birth.length !== 3 || reference.length !== 3 || [...birth, ...reference].some(Number.isNaN)) {
    return null;
  }
  const [birthYear, birthMonth, birthDay] = birth;
  const [referenceYear, referenceMonth, referenceDay] = reference;
  let months = (referenceYear - birthYear) * 12 + referenceMonth - birthMonth;
  if (referenceDay < birthDay) months -= 1;
  return months;
}

const createSchema = z
  .object({
    tag_number: z.string().max(50).optional().or(z.literal("")),
    name: z.string().max(80).optional(),
    sex: z.enum([AnimalCreateInSex.M, AnimalCreateInSex.F]),
    source: z.enum([AnimalCreateInSource.BORN, AnimalCreateInSource.PURCHASED]),
    current_bucket: z.enum(BUCKETS as [string, ...string[]]),
    breed: z.string().max(60).optional(),
    date_of_birth: z
      .string()
      .optional()
      .refine((value) => !value || value <= localToday(), "Date can't be in the future"),
    estimated_dob: z
      .string()
      .optional()
      .refine((value) => !value || value <= localToday(), "Date can't be in the future"),
    birth_type: z.enum([...BIRTH_TYPES] as [string, ...string[]]).optional(),
    birth_weight: optNum(
      z
        .number()
        .nonnegative()
        .max(1000, "At most 1000 kg")
        .refine(isPersistableNonnegativeWeight, MIN_PERSISTED_WEIGHT_MESSAGE),
    ),
    purchase_date: z
      .string()
      .optional()
      .refine((value) => !value || value <= localToday(), "Date can't be in the future"),
    purchase_price: optNum(
      z
        .number()
        .nonnegative()
        .max(1_000_000_000, "Purchase price cannot exceed ₹1,000,000,000")
        .refine(isPersistableNonnegativeMoney, MIN_PERSISTED_MONEY_MESSAGE),
    ),
    seller_name: z.string().max(120).optional(),
    weight_kg: optNum(
      z
        .number()
        .positive()
        .min(MIN_PERSISTED_KG, MIN_PERSISTED_KG_MESSAGE)
        .max(1000, "At most 1000 kg"),
    ),
    weight_date: z
      .string()
      .optional()
      .refine((value) => !value || value <= localToday(), "Date can't be in the future"),
    // Keep the resolver output total before the BORN-only audit requirement
    // below. Programmatic/legacy form submissions may omit this optional
    // input, but validation should still follow the normal issue path rather
    // than relying on an undefined-safe string operation.
    historical_import_reason: z.string().max(255).optional().default(""),
    notes: z.string().max(4000, "Max 4000 characters").optional(),
  })
  .superRefine((values, ctx) => {
    if (values.weight_date && values.weight_kg === undefined) {
      ctx.addIssue({
        code: "custom",
        path: ["weight_kg"],
        message: "Entry weight date requires an entry weight",
      });
    }
    if (values.source !== AnimalCreateInSource.BORN) return;

    if (!bucketAllowsSex(values.current_bucket, values.sex)) {
      const requiredSex = BUCKET_REQUIRED_SEX[values.current_bucket];
      ctx.addIssue({
        code: "custom",
        path: ["current_bucket"],
        message: `Only ${requiredSex === AnimalCreateInSex.M ? "male" : "female"} animals may enter ${values.current_bucket}`,
      });
    }
    if (!values.historical_import_reason.trim()) {
      ctx.addIssue({
        code: "custom",
        path: ["historical_import_reason"],
        message: "Explain why this historical animal is being imported",
      });
    }
    if (WORKFLOW_ONLY_INITIAL_BUCKETS.has(values.current_bucket)) {
      ctx.addIssue({
        code: "custom",
        path: ["current_bucket"],
        message: "Pregnancy, delivery and recovery buckets require their linked workflow records",
      });
    }
    if (values.current_bucket !== AnimalCreateInCurrentBucket.BREEDING) return;

    const minimumAge = values.sex === AnimalCreateInSex.M ? 12 : 10;
    const minimumWeight = values.sex === AnimalCreateInSex.M ? 25 : 22;
    const recordedDob = values.date_of_birth || values.estimated_dob;
    if (!recordedDob) {
      ctx.addIssue({
        code: "custom",
        path: ["date_of_birth"],
        message: "A breeding import requires a date of birth or estimated DOB",
      });
    } else {
      const ageMonths = completedMonths(recordedDob, localToday());
      if (ageMonths === null || ageMonths < minimumAge) {
        ctx.addIssue({
          code: "custom",
          path: ["date_of_birth"],
          message: `A ${values.sex === AnimalCreateInSex.M ? "buck" : "doe"} must be at least ${minimumAge} months old to enter BREEDING`,
        });
      }
    }
    if (values.weight_kg === undefined || values.weight_kg < minimumWeight) {
      ctx.addIssue({
        code: "custom",
        path: ["weight_kg"],
        message: `Entry weight must be at least ${minimumWeight} kg to enter BREEDING`,
      });
    }
  });
type CreateInput = z.input<typeof createSchema>;
type CreateValues = z.output<typeof createSchema>;

const emptyToNull = (v: string | undefined) => (v ? v : null);

function pageFromSearchParams(searchParams: URLSearchParams): number {
  const parsed = Number(searchParams.get("page"));
  return Number.isSafeInteger(parsed) && parsed >= 1 ? Math.min(parsed, MAX_PAGE) : 1;
}

function animalListUrl({
  pathname,
  paramsKey,
  bucket,
  sex,
  status,
  q,
  page,
}: {
  pathname: string;
  paramsKey: string;
  bucket: string;
  sex: string;
  status: string;
  q: string;
  page: number;
}): string {
  const params = new URLSearchParams(paramsKey);
  const setFilter = (name: string, value: string, emptyValue: string) => {
    if (value === emptyValue) params.delete(name);
    else params.set(name, value);
  };
  setFilter("bucket", bucket, ALL);
  setFilter("sex", sex, ALL);
  setFilter("status", status, ALL);
  setFilter("q", q.trim(), "");
  // `page` is the canonical browser state; the API offset is derived from it.
  params.delete("offset");
  if (page > 1) params.set("page", String(page));
  else params.delete("page");
  const rest = params.toString();
  return rest ? `${pathname}?${rest}` : pathname;
}

function paramsKeyFromUrl(url: string): string {
  const questionMark = url.indexOf("?");
  return questionMark === -1 ? "" : url.slice(questionMark + 1);
}

function CreateAnimalDialog({
  onCreated,
  isOwner,
  startOpen = false,
}: {
  onCreated: () => void;
  isOwner: boolean;
  startOpen?: boolean;
}) {
  const [open, setOpen] = useState(startOpen);
  const createMut = useCreateAnimalApiAnimalsPost();
  const createFlight = useSingleFlight();
  const {
    register,
    handleSubmit,
    control,
    reset,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<CreateInput, unknown, CreateValues>({
    resolver: zodResolver(createSchema),
    // Provenance fields are mutually exclusive. Unregistering conditional
    // inputs prevents a value entered under one source from being submitted
    // after the operator switches to the other source.
    shouldUnregister: true,
    defaultValues: {
      sex: AnimalCreateInSex.F,
      source: AnimalCreateInSource.PURCHASED,
      current_bucket: AnimalCreateInCurrentBucket.QUARANTINE,
      breed: "Osmanabadi",
    },
  });
  const source = useWatch({ control, name: "source" });
  const sex = useWatch({ control, name: "sex" });
  const currentBucket = useWatch({ control, name: "current_bucket" });

  useEffect(() => {
    if (source === AnimalCreateInSource.PURCHASED) {
      setValue("current_bucket", AnimalCreateInCurrentBucket.QUARANTINE, {
        shouldValidate: true,
      });
    }
  }, [setValue, source]);

  useEffect(() => {
    if (!isOwner && source === AnimalCreateInSource.BORN) {
      setValue("source", AnimalCreateInSource.PURCHASED, { shouldValidate: true });
    }
  }, [isOwner, setValue, source]);

  async function onSubmit(values: CreateValues) {
    await createFlight.run(async () => {
      try {
        await createMut.mutateAsync({
          data: {
            tag_number: values.tag_number?.trim() || undefined,
            name: values.name?.trim() || null,
            sex: values.sex,
            source: values.source,
            current_bucket:
              values.source === AnimalCreateInSource.PURCHASED
                ? AnimalCreateInCurrentBucket.QUARANTINE
                : (values.current_bucket as AnimalCreateInCurrentBucket),
            breed: values.breed?.trim() || "Osmanabadi",
            date_of_birth: emptyToNull(values.date_of_birth),
            estimated_dob: emptyToNull(values.estimated_dob),
            birth_type: (values.birth_type || null) as AnimalCreateInBirthType,
            birth_weight: values.birth_weight ?? null,
            purchase_date: emptyToNull(values.purchase_date),
            purchase_price: values.purchase_price ?? null,
            seller_name: values.seller_name?.trim() || null,
            weight_kg: values.weight_kg ?? null,
            weight_date: emptyToNull(values.weight_date),
            notes: values.notes?.trim() || null,
            historical_import_reason:
              values.source === AnimalCreateInSource.BORN
                ? values.historical_import_reason.trim() || null
                : null,
          },
        });
        toast.success("Animal added.");
        reset();
        setOpen(false);
        onCreated();
      } catch (err) {
        toast.error(err instanceof ApiError ? err.detail : "Something went wrong");
      }
    });
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button disabled={createFlight.pending} onClick={() => setOpen(true)}>
        Add animal
      </Button>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add animal</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3" noValidate>
          <fieldset disabled={isSubmitting || createFlight.pending} className="contents">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="tag_number">Tag number</Label>
              <Input
                id="tag_number"
                placeholder="Auto-generated if left blank (e.g. G-7KP2D)"
                maxLength={50}
                aria-invalid={Boolean(errors.tag_number) || undefined}
                aria-describedby={errors.tag_number ? "create-tag-error" : undefined}
                {...register("tag_number")}
              />
              {errors.tag_number && (
                <p id="create-tag-error" role="alert" className="text-sm text-destructive">
                  {errors.tag_number.message}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                maxLength={80}
                aria-invalid={Boolean(errors.name) || undefined}
                aria-describedby={errors.name ? "create-name-error" : undefined}
                {...register("name")}
              />
              {errors.name && (
                <p id="create-name-error" role="alert" className="text-sm text-destructive">
                  {errors.name.message}
                </p>
              )}
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
                      <SelectItem value={AnimalCreateInSource.PURCHASED}>Purchased</SelectItem>
                      {isOwner && (
                        <SelectItem value={AnimalCreateInSource.BORN}>
                          Historical born-on-farm import
                        </SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="animal-bucket">Bucket *</Label>
              {source === AnimalCreateInSource.PURCHASED ? (
                <>
                  <Input
                    id="animal-bucket"
                    value="QUARANTINE"
                    readOnly
                    aria-describedby="purchased-quarantine-note"
                  />
                  <p id="purchased-quarantine-note" className="text-xs text-muted-foreground">
                    Purchased animals must enter QUARANTINE. Complete the quarantine protocol
                    before moving this animal into the production herd.
                  </p>
                </>
              ) : (
                <Controller
                  control={control}
                  name="current_bucket"
                  render={({ field }) => (
                    <Select value={field.value} onValueChange={field.onChange} items={BUCKET_ITEMS}>
                      <SelectTrigger id="animal-bucket" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {HISTORICAL_IMPORT_BUCKETS.filter((b) => bucketAllowsSex(b, sex)).map(
                          (b) => (
                            <SelectItem key={b} value={b}>
                              {bucketLabel(b)}
                            </SelectItem>
                          ),
                        )}
                      </SelectContent>
                    </Select>
                  )}
                />
              )}
              {errors.current_bucket && (
                <p role="alert" className="text-sm text-destructive">
                  {errors.current_bucket.message}
                </p>
              )}
              {source === AnimalCreateInSource.BORN &&
                currentBucket === AnimalCreateInCurrentBucket.BREEDING && (
                  <p className="text-xs text-muted-foreground">
                    BREEDING imports require a {sex === AnimalCreateInSex.M ? "buck" : "doe"} age
                    of at least {sex === AnimalCreateInSex.M ? 12 : 10} months and an entry weight
                    of at least {sex === AnimalCreateInSex.M ? 25 : 22} kg.
                  </p>
                )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="breed">Breed</Label>
              <Input
                id="breed"
                maxLength={60}
                aria-invalid={Boolean(errors.breed) || undefined}
                aria-describedby={errors.breed ? "create-breed-error" : undefined}
                {...register("breed")}
              />
              {errors.breed && (
                <p id="create-breed-error" role="alert" className="text-sm text-destructive">
                  {errors.breed.message}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="date_of_birth">Date of birth</Label>
              <Input
                id="date_of_birth"
                type="date"
                max={localToday()}
                {...register("date_of_birth")}
              />
              {errors.date_of_birth && (
                <p role="alert" className="text-sm text-destructive">{errors.date_of_birth.message}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="estimated_dob">Estimated DOB</Label>
              <Input
                id="estimated_dob"
                type="date"
                max={localToday()}
                {...register("estimated_dob")}
              />
              {errors.estimated_dob && (
                <p role="alert" className="text-sm text-destructive">{errors.estimated_dob.message}</p>
              )}
            </div>
            {source === AnimalCreateInSource.BORN && (
              <>
                <div className="col-span-2 space-y-1.5 rounded-lg border p-3">
                  <p className="text-sm text-muted-foreground">
                    Historical import only. Normal births must be recorded through Kidding so the
                    dam, sire and kidding record remain linked.
                  </p>
                  <Label htmlFor="historical_import_reason">Historical import reason *</Label>
                  <Textarea
                    id="historical_import_reason"
                    rows={2}
                    maxLength={255}
                    aria-invalid={Boolean(errors.historical_import_reason) || undefined}
                    {...register("historical_import_reason")}
                  />
                  {errors.historical_import_reason && (
                    <p className="text-sm text-destructive">
                      {errors.historical_import_reason.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="animal-birth-type">Birth type</Label>
                  <Controller
                    control={control}
                    name="birth_type"
                    render={({ field }) => (
                      <Select value={field.value ?? ""} onValueChange={field.onChange}>
                        <SelectTrigger id="animal-birth-type" className="w-full">
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
                  <Input
                    id="birth_weight"
                    type="number"
                    step="0.01"
                    min="0.0005"
                    {...register("birth_weight")}
                  />
                  {errors.birth_weight && (
                    <p role="alert" className="text-sm text-destructive">{errors.birth_weight.message}</p>
                  )}
                </div>
              </>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="weight_kg">Entry weight (kg)</Label>
              <Input
                id="weight_kg"
                type="number"
                step="0.01"
                min="0.0005"
                aria-invalid={Boolean(errors.weight_kg) || undefined}
                aria-describedby={errors.weight_kg ? "create-weight-error" : undefined}
                {...register("weight_kg")}
              />
              {errors.weight_kg && (
                <p role="alert" className="text-sm text-destructive">{errors.weight_kg.message}</p>
              )}
            </div>
            {source === AnimalCreateInSource.BORN && (
              <div className="space-y-1.5">
                <Label htmlFor="weight_date">Entry weight date</Label>
                <Input
                  id="weight_date"
                  type="date"
                  max={localToday()}
                  {...register("weight_date")}
                />
                {errors.weight_date && (
                  <p role="alert" className="text-sm text-destructive">{errors.weight_date.message}</p>
                )}
              </div>
            )}
          </div>

          {source === AnimalCreateInSource.PURCHASED && (
            <div className="grid grid-cols-2 gap-3 rounded-lg border p-3">
              <div className="space-y-1.5">
                <Label htmlFor="purchase_date">Purchase date</Label>
                <Input
                  id="purchase_date"
                  type="date"
                  max={localToday()}
                  {...register("purchase_date")}
                />
                {errors.purchase_date && (
                  <p role="alert" className="text-sm text-destructive">{errors.purchase_date.message}</p>
                )}
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
                  <p role="alert" className="text-sm text-destructive">{errors.purchase_price.message}</p>
                )}
              </div>
              <div className="col-span-2 space-y-1.5">
                <Label htmlFor="seller_name">Seller name</Label>
                <Input
                  id="seller_name"
                  maxLength={120}
                  aria-invalid={Boolean(errors.seller_name) || undefined}
                  aria-describedby={errors.seller_name ? "create-seller-error" : undefined}
                  {...register("seller_name")}
                />
                {errors.seller_name && (
                  <p id="create-seller-error" role="alert" className="text-sm text-destructive">
                    {errors.seller_name.message}
                  </p>
                )}
              </div>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="notes">Notes</Label>
            <Textarea
              id="notes"
              rows={2}
              maxLength={4000}
              aria-invalid={Boolean(errors.notes) || undefined}
              {...register("notes")}
            />
            {errors.notes && <p className="text-sm text-destructive">{errors.notes.message}</p>}
          </div>

          <DialogFooter>
            <Button type="submit" disabled={isSubmitting || createFlight.pending}>
              {isSubmitting || createFlight.pending ? "Saving…" : "Save animal"}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AnimalsPageContent() {
  const queryClient = useQueryClient();
  const { can, isOwner, loading: permsLoading, isError: permsError , refetch: permsRefetch } = usePermissions();
  const allowed = can("animals.view");
  const farmType = useFarmType();
  /** value → label map for the bucket filter Select root: farm-type aware
   * labels (dairy pens vs goat wards) with the raw code as the value. */
  const bucketFilterItems = useMemo(() => {
    const entries = Object.values(ListAnimalsApiAnimalsGetBucket).map((b) => [
      b,
      enumLabel("bucket", b, farmType),
    ]);
    return { [ALL]: "All buckets", ...Object.fromEntries(entries) };
  }, [farmType]);
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  // Key effects and navigation de-duplication by the serialized params; the
  // useSearchParams object identity itself is not stable.
  const paramsKey = searchParams.toString();

  const [bucket, setBucket] = useState(searchParams.get("bucket") ?? ALL);
  const [sex, setSex] = useState(searchParams.get("sex") ?? ALL);
  const [status, setStatus] = useState(searchParams.get("status") ?? ALL);
  const [q, setQ] = useState(clampSearch(searchParams.get("q") ?? ""));
  const [debouncedQ, setDebouncedQ] = useState(q.trim());
  const [searchNavigationPending, setSearchNavigationPending] = useState(false);
  const navigationSeq = useRef(0);
  /** Dispatched-but-uncommitted list navigations, keyed by a per-dispatch
   *  sequence number so two dispatches to the SAME destination stay two
   *  entries. Each records the `q` its URL actually carries. */
  const pendingComponentNavigations = useRef<Map<number, { key: string; urlQ: string }>>(
    new Map(),
  );
  const [page, setPage] = useState(() => pageFromSearchParams(searchParams));
  const pageNavigationPending = useRef(false);
  const recordComponentNavigation = useCallback((url: string) => {
    // Next 16 gives a newly dispatched navigation priority over the currently
    // pending one — but on a slow device the older navigation's commit can
    // still land first. Keep EVERY dispatch, in dispatch order, so an early
    // commit of an older navigation is not mistaken for a browser/history
    // navigation (which would reset the search input mid-typing).
    //
    // Keying by sequence rather than by destination is load-bearing: keying by
    // destination forced a re-dispatch of an already-pending URL to be deleted
    // and re-inserted, which moved it to the end and broke the very insertion
    // order the consume loop below relies on — so committing an older
    // duplicate destination deleted entries for navigations still in flight.
    //
    // Recording the URL's own `q` (rather than a live edit counter) is the
    // other half: the counter folded in keystrokes the URL does not describe,
    // so an edit made BEFORE the dispatch looked already-represented.
    const key = paramsKeyFromUrl(url);
    pendingComponentNavigations.current.set(++navigationSeq.current, {
      key,
      urlQ: (new URLSearchParams(key).get("q") ?? "").trim(),
    });
  }, []);
  const replaceListUrl = useCallback(
    (url: string) => {
      // Next will not commit a same-URL replace. Recording one would leave a
      // phantom pending entry that keeps the list in "Updating" forever.
      if (paramsKeyFromUrl(url) === paramsKey) {
        if (pendingComponentNavigations.current.size === 0) return;
        // A same-URL navigation is still load-bearing when it supersedes an
        // older transition away from this URL. Next's navigation queue uses
        // the newer dispatch to discard the older action, but it produces no
        // new search-param commit for us to consume. Clear our superseded
        // entries without recording a phantom, then dispatch the cancellation.
        pendingComponentNavigations.current.clear();
        setSearchNavigationPending(false);
        router.replace(url);
        return;
      }
      recordComponentNavigation(url);
      // The destination query can already be fresh in React Query's cache,
      // so `isFetching` is not a reliable navigation guard. Keep rows hidden
      // until Next commits this URL; otherwise a row click can race the
      // outstanding replace and be pulled back from the profile page.
      setSearchNavigationPending(true);
      router.replace(url);
    },
    [paramsKey, recordComponentNavigation, router],
  );
  const pushListUrl = useCallback(
    (url: string) => {
      if (paramsKeyFromUrl(url) === paramsKey) {
        if (pendingComponentNavigations.current.size === 0) return;
        pendingComponentNavigations.current.clear();
        setSearchNavigationPending(false);
        // Cancelling back to the current URL must not create another history
        // entry even though the transition being superseded was a push.
        router.replace(url);
        return;
      }
      recordComponentNavigation(url);
      setSearchNavigationPending(true);
      router.push(url);
    },
    [paramsKey, recordComponentNavigation, router],
  );
  // Same-route client navigations (e.g. a dashboard bucket link while already
  // on /animals) change the params — re-sync the filters. Keyed
  // off the param STRING: useSearchParams' object identity isn't stable.
  useEffect(() => {
    const params = new URLSearchParams(paramsKey);
    const pending = pendingComponentNavigations.current;
    // A single commit to a destination satisfies every earlier dispatch up to
    // the NEWEST matching one. This matters for A -> B -> C -> B: Next applies
    // only the final B navigation, so consuming the oldest B would strand C
    // and the final B as phantom pending entries forever.
    let matchedSeq: number | undefined;
    for (const [seq, entry] of pending) {
      if (entry.key === paramsKey) {
        matchedSeq = seq;
      }
    }
    const matched = matchedSeq === undefined ? undefined : pending.get(matchedSeq);
    // The input holds text this committing URL does not describe, so the
    // operator has typed since it was dispatched. Preserve their edit and let
    // its debounce issue the next URL; external/history navigations (no match)
    // still rehydrate the input normally.
    const hasNewerSearchEdit = matched !== undefined && q.trim() !== matched.urlQ;
    if (matchedSeq !== undefined) {
      // Consume the matched dispatch plus everything dispatched before it:
      // Next has discarded those, and a lingering entry would make a later
      // browser/history navigation to the same params look component-owned.
      // Genuinely later dispatches survive — they may still commit.
      for (const seq of [...pending.keys()]) {
        if (seq > matchedSeq) break;
        pending.delete(seq);
      }
    } else if (pending.size > 0) {
      // A URL that was not initiated by this list is browser/app
      // navigation and remains authoritative.
      pending.clear();
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setBucket(params.get("bucket") ?? ALL);
    setSex(params.get("sex") ?? ALL);
    setStatus(params.get("status") ?? ALL);
    const nextQ = clampSearch(params.get("q") ?? "");
    if (!hasNewerSearchEdit) {
      setQ(nextQ);
      setDebouncedQ(nextQ.trim());
    }
    // A matching older navigation may commit while a newer filter/page
    // navigation is still in flight. That newer URL remains authoritative,
    // so do not briefly expose rows described by the older commit.
    setSearchNavigationPending(hasNewerSearchEdit || pending.size > 0);
    setPage(pageFromSearchParams(params));
    // `q` is read but deliberately NOT a dependency: this effect must run only
    // when the URL commits. Adding it would re-run the whole filter re-sync on
    // every keystroke, fighting the debounce. The closure here belongs to the
    // render in which `paramsKey` changed, so the `q` it reads is current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramsKey]);
  // ?new=1 opens the create dialog once; strip it so a reload doesn't reopen
  // the dialog. Latch the flag on the first render (like /breeding and
  // /kidding do): the strip effect runs while permissions are still loading,
  // i.e. before CreateAnimalDialog is ever mounted, so reading the live
  // params at render time loses the /animals/new deep link on a cold load.
  const [openFromUrl] = useState(() => searchParams.get("new") === "1");
  const newParamStripPending = useRef(false);
  useEffect(() => {
    const params = new URLSearchParams(paramsKey);
    if (params.get("new") !== "1") {
      newParamStripPending.current = false;
      return;
    }
    // Strict Mode replays mount effects. A second identical replacement can
    // never produce a distinct params commit, so its pending-navigation entry
    // would otherwise survive forever and misclassify a later history visit.
    if (newParamStripPending.current) return;
    newParamStripPending.current = true;
    params.delete("new");
    const rest = params.toString();
    replaceListUrl(rest ? `${pathname}?${rest}` : pathname);
  }, [paramsKey, pathname, replaceListUrl]);
  // Debounce the search box (~300ms) so typing doesn't fire a request per
  // keystroke; the input itself stays instant.
  useEffect(() => {
    const handle = setTimeout(() => {
      const normalizedQ = q.trim();
      setDebouncedQ(normalizedQ);
      const urlQ = (new URLSearchParams(paramsKey).get("q") ?? "").trim();
      if (normalizedQ === urlQ) {
        // This effect also re-runs after filter changes. An unchanged search
        // term must not clear the guard owned by an uncommitted filter/page
        // navigation.
        setSearchNavigationPending(pendingComponentNavigations.current.size > 0);
        return;
      }
      // Keep stale list rows non-interactive until Next has committed the URL
      // replacement. Otherwise a quick row click can race this replacement
      // and be sent back from /animals/:id to the list query.
      setSearchNavigationPending(true);
      setPage(1);
      const url = animalListUrl({
        pathname,
        paramsKey,
        bucket,
        sex,
        status,
        q: normalizedQ,
        page: 1,
      });
      replaceListUrl(url);
    }, 300);
    return () => clearTimeout(handle);
  }, [bucket, paramsKey, pathname, q, replaceListUrl, sex, status]);

  const params: ListAnimalsApiAnimalsGetParams = {
    ...(bucket !== ALL && { bucket: bucket as ListAnimalsApiAnimalsGetBucket }),
    ...(sex !== ALL && { sex: sex as ListAnimalsApiAnimalsGetSex }),
    ...(status !== ALL && { status: status as ListAnimalsApiAnimalsGetStatus }),
    ...(status === ALL && { include_all_statuses: true }),
    ...(debouncedQ && { q: debouncedQ }),
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  };
  const query = useListAnimalsApiAnimalsGet(params, { query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const searchPending = q.trim() !== debouncedQ || searchNavigationPending;
  const total = Math.max(0, payload?.total ?? 0);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageOutOfRange = payload !== undefined && page > totalPages;
  /** Distinguishes "herd is empty" (offer the first entry) from "filters
   * exclude everything" (offer to clear them). */
  const filtersActive =
    bucket !== ALL || sex !== ALL || status !== ALL || debouncedQ.trim() !== "";

  useEffect(() => {
    if (!query.isFetching) pageNavigationPending.current = false;
  }, [page, query.isFetching]);

  useEffect(() => {
    if (!pageOutOfRange) return;
    const handle = window.setTimeout(() => setPage(totalPages), 0);
    // The replacement is the external synchronization performed by this
    // effect; its helper also raises the interaction guard in the same render
    // so cached rows cannot flash before the URL commit.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    replaceListUrl(
      animalListUrl({
        pathname,
        paramsKey,
        bucket,
        sex,
        status,
        q: debouncedQ,
        page: totalPages,
      }),
    );
    return () => window.clearTimeout(handle);
  }, [
    bucket,
    debouncedQ,
    pageOutOfRange,
    paramsKey,
    pathname,
    replaceListUrl,
    sex,
    status,
    totalPages,
  ]);

  function changeFilter(
    field: "bucket" | "sex" | "status",
    value: string,
  ) {
    const next = { bucket, sex, status, q: q.trim(), page: 1, [field]: value };
    if (field === "bucket") setBucket(value);
    else if (field === "sex") setSex(value);
    else setStatus(value);
    setDebouncedQ(next.q);
    setPage(1);
    replaceListUrl(animalListUrl({ pathname, paramsKey, ...next }));
  }

  function changePage(nextPage: number) {
    if (
      pageNavigationPending.current ||
      query.isFetching ||
      nextPage < 1 ||
      nextPage > totalPages
    ) {
      return;
    }
    pageNavigationPending.current = true;
    setPage(nextPage);
    pushListUrl(
      animalListUrl({
        pathname,
        paramsKey,
        bucket,
        sex,
        status,
        q: debouncedQ,
        page: nextPage,
      }),
    );
  }

  function refresh() {
    if (page !== 1) {
      setPage(1);
      replaceListUrl(
        animalListUrl({
          pathname,
          paramsKey,
          bucket,
          sex,
          status,
          q: debouncedQ,
          page: 1,
        }),
      );
    }
    invalidateFarmData(queryClient);
  }

  if (permsLoading) {
    return <p role="status" aria-live="polite" className="py-10 text-center text-muted-foreground">Loading…</p>;
  }
  if (permsError) {
    return (
      <PermissionsError onRetry={() => void permsRefetch()} />
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
              isOwner={isOwner}
              startOpen={openFromUrl}
            />
          )
        }
      />

      <div className="flex flex-wrap items-center gap-3">
        <Select
          value={bucket}
          onValueChange={(value) => changeFilter("bucket", value)}
          items={bucketFilterItems}
        >
          <SelectTrigger aria-label="Filter animals by bucket">
            <SelectValue placeholder="All buckets" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All buckets</SelectItem>
            {Object.values(ListAnimalsApiAnimalsGetBucket).map((b) => (
              <SelectItem key={b} value={b}>
                {enumLabel("bucket", b, farmType)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={sex}
          onValueChange={(value) => changeFilter("sex", value)}
          items={SEX_FILTER_ITEMS}
        >
          <SelectTrigger aria-label="Filter animals by sex">
            <SelectValue placeholder="Both sexes" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Both sexes</SelectItem>
            <SelectItem value={ListAnimalsApiAnimalsGetSex.F}>Female</SelectItem>
            <SelectItem value={ListAnimalsApiAnimalsGetSex.M}>Male</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={status}
          onValueChange={(value) => changeFilter("status", value)}
          items={STATUS_FILTER_ITEMS}
        >
          <SelectTrigger aria-label="Filter animals by status">
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
        <div className="relative w-full sm:w-auto">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search by tag…"
            aria-label="Search animals by tag"
            maxLength={60}
            className="w-full pl-8 sm:w-56"
          />
        </div>
      </div>

      {searchPending ? (
        <p role="status" className="py-10 text-center text-muted-foreground">
          Updating animals…
        </p>
      ) : query.isLoading ? (
        <p role="status" aria-live="polite" className="py-10 text-center text-muted-foreground">Loading…</p>
      ) : query.isFetching ? (
        <p role="status" className="py-10 text-center text-muted-foreground">
          Updating animals…
        </p>
      ) : query.isError ? (
        <div role="alert" className="space-y-3">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : "Could not load animals."}
          </p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            Retry animals
          </Button>
        </div>
      ) : pageOutOfRange ? (
        <p role="status" className="py-10 text-center text-muted-foreground">
          Returning to the last available page…
        </p>
      ) : !payload || payload.animals.length === 0 ? (
        filtersActive ? (
          <EmptyState
            icon={SearchX}
            title="No animals match these filters."
            description="Try clearing the filters."
          />
        ) : (
          <EmptyState
            icon={PawPrint}
            title="No animals yet"
            description="Add your first animal to start the herd register."
          >
            {can("animals.create") && (
              <Link
                href="/animals/new"
                className={buttonVariants({ variant: "default", size: "sm" })}
              >
                Add animal
              </Link>
            )}
          </EmptyState>
        )
      ) : (
        <DataTableCard title="Herd" description={`${payload.total} animal(s)`}>
          <Table className="min-w-[760px]">
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
                  <TableCell>{enumLabel("sex", a.sex)}</TableCell>
                  <TableCell>{a.breed}</TableCell>
                  <TableCell>{enumLabel("bucket", a.current_bucket, farmType)}</TableCell>
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

      {payload && !searchPending && !query.isFetching && !query.isError && !pageOutOfRange && (
        <PaginationControls
          total={total}
          limit={PAGE_SIZE}
          offset={(page - 1) * PAGE_SIZE}
          onOffsetChange={(nextOffset) => changePage(Math.floor(nextOffset / PAGE_SIZE) + 1)}
          label="animals"
        />
      )}
    </div>
  );
}

/** Suspense boundary required because the content reads useSearchParams(). */
export default function AnimalsPage() {
  return (
    <Suspense fallback={<p role="status" aria-live="polite" className="py-10 text-center text-muted-foreground">Loading…</p>}>
      <AnimalsPageContent />
    </Suspense>
  );
}
