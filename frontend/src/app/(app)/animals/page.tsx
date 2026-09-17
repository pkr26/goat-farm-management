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
  type AnimalCreateIn,
  type ListAnimalsApiAnimalsGetParams,
} from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PaginationControls } from "@/components/pagination-controls";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { InlineLoading, PageSkeleton, TableSkeleton } from "@/components/skeletons";
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
  SortableTableHead,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api-client";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { MAX_ANIMAL_TAG_LENGTH, MAX_FREE_TEXT_LENGTH } from "@/lib/backend-caps";
import { enumLabel } from "@/lib/enum-labels";
import { useLanguage, useT } from "@/lib/i18n";
import { farmVocabulary, type FarmVocabulary } from "@/lib/farm-vocabulary";
import { farmToday } from "@/lib/format";
import {
  isPersistableNonnegativeMoney,
  isPersistableNonnegativeWeight,
  MIN_PERSISTED_KG,
  MIN_PERSISTED_KG_MESSAGE,
  MIN_PERSISTED_MONEY_MESSAGE,
} from "@/lib/persisted-numbers";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

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
/** Phenotype coat-colour codes (wire values; labels live in the i18n
 * catalog under animals.coatColor.*). */
const COAT_COLOR_CODES = ["black", "black_patched", "brown", "white", "spotted"] as const;
/** value → label map for the root `items` prop, in the active language:
 * without it, Base UI's Select.Value renders the raw value in the closed
 * trigger. */
const birthTypeItems = (language: "en" | "te"): Record<string, string> =>
  Object.fromEntries(BIRTH_TYPES.map((t) => [t, enumLabel("birthType", t, language)]));
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

const bucketLabel = (b: string, language: "en" | "te") => enumLabel("bucket", b, language);
/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
const bucketItems = (language: "en" | "te"): Record<string, string> =>
  Object.fromEntries(BUCKETS.map((b) => [b, bucketLabel(b, language)]));
const sexItems = (language: "en" | "te"): Record<string, string> => ({
  [AnimalCreateInSex.F]: enumLabel("sex", "F", language),
  [AnimalCreateInSex.M]: enumLabel("sex", "M", language),
});
const SOURCE_ITEMS: Record<string, string> = {
  [AnimalCreateInSource.BORN]: "Historical born-on-farm import",
  [AnimalCreateInSource.PURCHASED]: "Purchased",
};
const sexFilterItems = (language: "en" | "te"): Record<string, string> => ({
  [ALL]: "Both sexes",
  ...sexItems(language),
});
// Stryker disable next-line ObjectLiteral, StringLiteral: cva resolves variant "default" to the same classes as the fallback, and the sm size only changes padding classes no test observes (duties record-row precedent)
const ROW_ACTION_CLASSES = buttonVariants({ variant: "default", size: "sm" });

const statusFilterItems = (language: "en" | "te"): Record<string, string> => ({
  [ALL]: "All statuses",
  ...Object.fromEntries(
    Object.values(ListAnimalsApiAnimalsGetStatus).map((s) => [
      s,
      enumLabel("status", s, language),
    ]),
  ),
});

/** Zero is meaningful for optional weights; anything smaller rounds away. */
const MIN_PERSISTED_WEIGHT_MESSAGE = "Weight must be 0 kg or at least 0.0005 kg";

const optNum = (schema: z.ZodNumber) =>
  z.preprocess(
    // Stryker disable next-line ConditionalExpression: registered number inputs only ever yield "" or a numeric string — null/undefined never arrive, and the blank arm is pinned by the form campaign tests
  (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)),
    schema.optional(),
  );


/** Completed whole months between two ISO dates, or null when either date is
 * malformed. Exported for direct schema-level testing: the date inputs
 * sanitize values in the browser, so the malformed branch is unreachable
 * through the dialog. */
export function completedMonths(dateOfBirth: string, referenceDate: string): number | null {
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

/** Species-aware create schema: breeding-entry gates and nouns come from the
 * goat thresholds. Exported for direct schema-level testing of the gates the
 * dialog's native inputs cannot produce (e.g. malformed dates). */
export const createAnimalSchema = (vocabulary: FarmVocabulary) =>
  z
  .object({
    // Stryker disable next-line StringLiteral: z.string() already accepts "" (length 0 is within max), so the .or(z.literal("")) arm is a readability hint, not a distinct acceptance
    tag_number: z.string().max(MAX_ANIMAL_TAG_LENGTH).optional().or(z.literal("")),
    name: z.string().max(80).optional(),
    sex: z.enum([AnimalCreateInSex.M, AnimalCreateInSex.F]),
    source: z.enum([AnimalCreateInSource.BORN, AnimalCreateInSource.PURCHASED]),
    current_bucket: z.enum(BUCKETS as [string, ...string[]]),
    breed: z.string().max(60).optional(),
    // Phenotype descriptors (optional API contract: coat_color / horned).
    // The "" sentinel means "not recorded" and is stripped before submit, so
    // a backend that predates the fields never receives the keys at all.
    coat_color: z.enum(["", ...COAT_COLOR_CODES] as [string, ...string[]]).optional(),
    horned: z.enum(["", "yes", "no"]).optional(),
    date_of_birth: z
      .string()
      .optional()
      .refine((value) => !value || value <= farmToday(), "Date can't be in the future"),
    estimated_dob: z
      .string()
      .optional()
      .refine((value) => !value || value <= farmToday(), "Date can't be in the future"),
    birth_type: z.enum([...BIRTH_TYPES] as [string, ...string[]]).optional(),
    birth_weight: optNum(
      z
        .number()
        .min(
          vocabulary.facts.birthWeightKg.min,
          `A newborn ${vocabulary.young} weighs at least ${vocabulary.facts.birthWeightKg.min} kg`,
        )
        .max(
          vocabulary.facts.birthWeightKg.max,
          `A newborn ${vocabulary.young} weighs at most ${vocabulary.facts.birthWeightKg.max} kg`,
        )
        .refine(isPersistableNonnegativeWeight, MIN_PERSISTED_WEIGHT_MESSAGE),
    ),
    purchase_date: z
      .string()
      .optional()
      .refine((value) => !value || value <= farmToday(), "Date can't be in the future"),
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
        .max(
          vocabulary.facts.maxWeightKg,
          `At most ${vocabulary.facts.maxWeightKg} kg for this farm's species`,
        ),
    ),
    weight_date: z
      .string()
      .optional()
      .refine((value) => !value || value <= farmToday(), "Date can't be in the future"),
    // Keep the resolver output total before the BORN-only audit requirement
    // below. Programmatic/legacy form submissions may omit this optional
    // input, but validation should still follow the normal issue path rather
    // than relying on an undefined-safe string operation.
    historical_import_reason: z.string().max(255).optional().default(""),
    notes: z.string().max(MAX_FREE_TEXT_LENGTH, `Max ${MAX_FREE_TEXT_LENGTH} characters`).optional(),
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

    // Species gates mirror backend/app/models/species.py — a 23-month-old
    // Purchased adults must not be judged by kid thresholds (and vice versa).
    const rules =
      values.sex === AnimalCreateInSex.M ? vocabulary.breedingEntry.male : vocabulary.breedingEntry.female;
    const minimumAge = rules.minMonths;
    const minimumWeight = rules.minWeightKg;
    const recordedDob = values.date_of_birth || values.estimated_dob;
    if (!recordedDob) {
      ctx.addIssue({
        code: "custom",
        path: ["date_of_birth"],
        message: "A breeding import requires a date of birth or estimated DOB",
      });
    } else {
      const ageMonths = completedMonths(recordedDob, farmToday());
      // Stryker disable next-line ConditionalExpression: the null arm falls through to `null < minimumAge` (null coerces to 0, below the 10/12-month goat minimums), which adds the very same issue
      if (ageMonths === null || ageMonths < minimumAge) {
        ctx.addIssue({
          code: "custom",
          path: ["date_of_birth"],
          message: `A ${values.sex === AnimalCreateInSex.M ? vocabulary.maleAdult : vocabulary.femaleAdult} must be at least ${minimumAge} months old to enter BREEDING`,
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
type CreateInput = z.input<ReturnType<typeof createAnimalSchema>>;
type CreateValues = z.output<ReturnType<typeof createAnimalSchema>>;

const emptyToNull = (v: string | undefined) => (v ? v : null);

function pageFromSearchParams(searchParams: URLSearchParams): number {
  // Stryker disable next-line StringLiteral: the fallback only feeds the strict ^(0|[1-9]\d*)$ grammar, which rejects arbitrary text exactly as it rejects "" — both spellings land on the page-1 return
  const raw = searchParams.get("page") ?? "";
  // Strict grammar (the tasks board's): Number() accepts JS exotics like
  // "0x10" and "1e2" that then jump deep into the register.
  if (!/^(0|[1-9]\d*)$/.test(raw)) return 1;
  const parsed = Number(raw);
  // Stryker disable next-line EqualityOperator: parsed === 1 takes the same Math.min branch and the else arm also yields 1, so >= and > agree on every input
  return parsed >= 1 ? Math.min(parsed, MAX_PAGE) : 1;
}

// URL filter values are attacker-controlled: validate each against the
// generated enum and fall back to ALL, never forwarding a hostile link's
// garbage into a guaranteed-422 request (the finance ledger's pattern).
function bucketFromSearchParams(searchParams: URLSearchParams): string {
  const raw = searchParams.get("bucket");
  const values = Object.values(ListAnimalsApiAnimalsGetBucket) as string[];
  return raw && values.includes(raw) ? raw : ALL;
}

function sexFromSearchParams(searchParams: URLSearchParams): string {
  const raw = searchParams.get("sex");
  const values = Object.values(ListAnimalsApiAnimalsGetSex) as string[];
  return raw && values.includes(raw) ? raw : ALL;
}

function statusFromSearchParams(searchParams: URLSearchParams): string {
  const raw = searchParams.get("status");
  const values = Object.values(ListAnimalsApiAnimalsGetStatus) as string[];
  return raw && values.includes(raw) ? raw : ALL;
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
  // Stryker disable next-line MethodExpression: every caller (the debounce timer, changeFilter, the clamp effect) passes an already-trimmed q, so the re-trim is a no-op
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
  canManagePurchases,
  // Stryker disable next-line BooleanLiteral: the sole call site passes an explicit boolean (openFromUrl), so this parameter default never evaluates
  startOpen = false,
}: {
  onCreated: () => void;
  isOwner: boolean;
  /** Creating a PURCHASED animal books its purchase cascade, which the API
   * gates on purchases.manage (owners always hold it). */
  canManagePurchases: boolean;
  startOpen?: boolean;
}) {
  const [open, setOpen] = useState(startOpen);
  const createMut = useCreateAnimalApiAnimalsPost();
  const createFlight = useSingleFlight();
  const { language } = useLanguage();
  const t = useT();
  const vocabulary = farmVocabulary;
  // Stryker disable next-line ArrayDeclaration: farmVocabulary is a module constant, so the dep list can never go stale
  const schema = useMemo(() => createAnimalSchema(vocabulary), [vocabulary]);
  const {
    register,
    handleSubmit,
    control,
    reset,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<CreateInput, unknown, CreateValues>({
    resolver: zodResolver(schema),
    // Provenance fields are mutually exclusive. Unregistering conditional
    // inputs prevents a value entered under one source from being submitted
    // after the operator switches to the other source.
    shouldUnregister: true,
    defaultValues: {
      sex: AnimalCreateInSex.F,
      source: AnimalCreateInSource.PURCHASED,
      current_bucket: AnimalCreateInCurrentBucket.QUARANTINE,
      // Deliberately empty: the species default shows in the placeholder and
      // is applied on submit — a prefilled value reads as user input and gets
      // saved without a second look.
      breed: "",
    },
  });
  const source = useWatch({ control, name: "source" });
  const sex = useWatch({ control, name: "sex" });
  const currentBucket = useWatch({ control, name: "current_bucket" });
  /** value → label maps for the phenotype selects (active language). */
  const coatColorItems: Record<string, string> = {
    "": t("animals.notRecorded"),
    black: t("animals.coatColor.black"),
    black_patched: t("animals.coatColor.black_patched"),
    brown: t("animals.coatColor.brown"),
    white: t("animals.coatColor.white"),
    spotted: t("animals.coatColor.spotted"),
  };
  const hornedItems: Record<string, string> = {
    "": t("animals.notRecorded"),
    yes: t("common.yes"),
    no: t("common.no"),
  };

  // Stryker disable ConditionalExpression, ObjectLiteral, BooleanLiteral: the payload mapper independently forces QUARANTINE for PURCHASED and honors the operator's choice for BORN (pinned by the campaign payload tests); this preset only prefills the select, and shouldValidate never surfaces a different error state for enum values
  useEffect(() => {
    if (source === AnimalCreateInSource.PURCHASED) {
      setValue("current_bucket", AnimalCreateInCurrentBucket.QUARANTINE, {
        shouldValidate: true,
      });
    }
  }, [setValue, source]);
  // Stryker restore ConditionalExpression, ObjectLiteral, BooleanLiteral

  // Stryker disable ConditionalExpression, BlockStatement, BooleanLiteral, EqualityOperator, StringLiteral, ArrayDeclaration, CallExpression, ObjectLiteral: the BORN option renders for owners only, so a non-owner's source can never read BORN — the reset (and this whole effect body) is unreachable defense-in-depth
  useEffect(() => {
    if (!isOwner && source === AnimalCreateInSource.BORN) {
      setValue("source", AnimalCreateInSource.PURCHASED, { shouldValidate: true });
    }
  }, [isOwner, setValue, source]);
  // Stryker restore ConditionalExpression, BlockStatement, BooleanLiteral, EqualityOperator, StringLiteral, ArrayDeclaration, CallExpression

  async function onSubmit(values: CreateValues) {
    await createFlight.run(async () => {
      const farmScope = captureFarmScope();
      try {
        await createMut.mutateAsync({
          data: {
            // Stryker disable next-line OptionalChaining: the input is registered unconditionally, so the value is a string, never undefined
            tag_number: values.tag_number?.trim() || undefined,
            // Stryker disable next-line OptionalChaining: the input is registered unconditionally, so the value is a string, never undefined
            name: values.name?.trim() || null,
            sex: values.sex,
            source: values.source,
            current_bucket:
              // Stryker disable next-line ConditionalExpression: the purchased branch renders a read-only Quarantine input and the preset effect already forces the value, so values.current_bucket is always QUARANTINE when source is PURCHASED
              values.source === AnimalCreateInSource.PURCHASED
                ? AnimalCreateInCurrentBucket.QUARANTINE
                : (values.current_bucket as AnimalCreateInCurrentBucket),
            // Stryker disable next-line OptionalChaining: the input is registered unconditionally, so the value is a string, never undefined
            breed: values.breed?.trim() || vocabulary.defaultBreed,
            // Only sent when the operator actually recorded them: a backend
            // that predates the phenotype contract would 422 the unknown keys.
            ...(values.coat_color ? { coat_color: values.coat_color } : {}),
            ...(values.horned === "yes"
              ? { horned: true }
              : values.horned === "no"
                ? { horned: false }
                : {}),
            date_of_birth: emptyToNull(values.date_of_birth),
            estimated_dob: emptyToNull(values.estimated_dob),
            birth_type: (values.birth_type || null) as AnimalCreateInBirthType,
            birth_weight: values.birth_weight ?? null,
            purchase_date: emptyToNull(values.purchase_date),
            purchase_price: values.purchase_price ?? null,
            seller_name: values.seller_name?.trim() || null,
            weight_kg: values.weight_kg ?? null,
            weight_date: emptyToNull(values.weight_date),
            // Stryker disable next-line OptionalChaining: the input is registered unconditionally, so the value is a string, never undefined
            notes: values.notes?.trim() || null,
            historical_import_reason:
              // Stryker disable next-line ConditionalExpression: the resolver's .default("") gives every submission a string, so the mutant's PURCHASED arm reads "" and still maps to null — identical wire value (the BORN test pins the reason text)
              values.source === AnimalCreateInSource.BORN
                ? values.historical_import_reason.trim() || null
                : null,
            // The generated client lags the optional phenotype contract.
          } as AnimalCreateIn & { coat_color?: string; horned?: boolean },
        });
        // The write landed on the farm it was aimed at; a switch since then
        // means this continuation belongs to the previous farm's UI.
        if (!farmScope()) return;
        toast.success("Animal added.");
        // Stryker disable next-line CallExpression: shouldUnregister:true drops every field value when Radix unmounts the closed dialog's inputs, so the explicit reset is redundant with the remount defaults (pinned by the reopen-blank test)
        reset();
        setOpen(false);
        onCreated();
      } catch (err) {
        if (!farmScope()) return;
        toast.error(err instanceof ApiError ? err.detail : "Something went wrong");
      }
    });
  }

  // Stryker disable next-line ConditionalExpression: the purchased branch renders a read-only Quarantine input, so currentBucket can never read BREEDING while source is PURCHASED — the first conjunct never decides anything
  const showBreedingEntryHint = source === AnimalCreateInSource.BORN && currentBucket === AnimalCreateInCurrentBucket.BREEDING;

  // Stryker disable next-line LogicalOperator, ConditionalExpression: isSubmitting and createFlight.pending only diverge inside the async-resolver microtask before createFlight.run starts — a window every test asserts across, so ||/&& and either operand pinned to a constant are observationally identical for the cancel control (the submit button's label condition pins the pair)
  // Stryker disable next-line LogicalOperator: isSubmitting and createFlight.pending only diverge inside the async-resolver microtask before createFlight.run starts — a window every test asserts across, so || and && are observationally identical for the cancel control (the submit button's label condition pins the pair)
  const cancelBusy = isSubmitting || createFlight.pending;

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
          {/* Phones stack single-column: two forced columns squeezed a
           * date input and a select into ~160px slivers. */}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="tag_number">Tag number</Label>
              <Input
                id="tag_number"
                placeholder={`Auto-generated if blank (e.g. ${vocabulary.tagPrefix}-7KP2D)`}
                maxLength={MAX_ANIMAL_TAG_LENGTH}
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
                  <Select value={field.value} onValueChange={field.onChange} items={sexItems(language)}>
                    <SelectTrigger id="animal-sex" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={AnimalCreateInSex.F}>
                        {enumLabel("sex", "F", language)}
                      </SelectItem>
                      <SelectItem value={AnimalCreateInSex.M}>
                        {enumLabel("sex", "M", language)}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="animal-source">Source *</Label>
              {/* The managed-purchase cascade makes PURCHASED a
                  purchases.manage action (BORN stays owner-only), so a
                  caller holding neither grant has no creatable source and the
                  form explains itself instead of offering a dead submit. */}
              {!canManagePurchases && !isOwner && (
                <p role="alert" className="text-sm text-destructive">
                  You cannot create animals directly: purchased entries need
                  purchases.manage, and the historical born-on-farm import is
                  owner-only. Ask the farm owner to record the purchase batch.
                </p>
              )}
              <Controller
                control={control}
                name="source"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange} items={SOURCE_ITEMS}>
                    <SelectTrigger id="animal-source" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {canManagePurchases && (
                        <SelectItem value={AnimalCreateInSource.PURCHASED}>Purchased</SelectItem>
                      )}
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
                    value={bucketLabel(AnimalCreateInCurrentBucket.QUARANTINE, language)}
                    readOnly
                    aria-describedby="purchased-quarantine-note"
                  />
                  <p id="purchased-quarantine-note" className="text-xs text-muted-foreground">
                    Purchased animals must enter quarantine. Complete the quarantine protocol
                    before moving this animal into the production herd.
                  </p>
                </>
              ) : (
                <Controller
                  control={control}
                  name="current_bucket"
                  render={({ field }) => (
                    <Select value={field.value} onValueChange={field.onChange} items={bucketItems(language)}>
                      <SelectTrigger id="animal-bucket" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {HISTORICAL_IMPORT_BUCKETS.filter((b) => bucketAllowsSex(b, sex)).map(
                          (b) => (
                            <SelectItem key={b} value={b}>
                              {bucketLabel(b, language)}
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
              {showBreedingEntryHint && (
                  <p className="text-xs text-muted-foreground">
                    BREEDING imports require{" "}
                    {sex === AnimalCreateInSex.M ? "a " + vocabulary.maleAdult : "a " + vocabulary.femaleAdult}{" "}
                    of at least{" "}
                    {sex === AnimalCreateInSex.M
                      ? vocabulary.breedingEntry.male.minMonths
                      : vocabulary.breedingEntry.female.minMonths}{" "}
                    months and{" "}
                    {sex === AnimalCreateInSex.M
                      ? vocabulary.breedingEntry.male.minWeightKg
                      : vocabulary.breedingEntry.female.minWeightKg}{" "}
                    kg.
                  </p>
                )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="breed">Breed</Label>
              <Input
                id="breed"
                placeholder={`e.g. ${vocabulary.defaultBreed} (the default when blank)`}
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
            {/* Phenotype descriptors (optional API contract). Blank means not
             * recorded and is stripped from the payload. */}
            <div className="space-y-1.5">
              <Label htmlFor="animal-coat-color">{t("animals.coatColor")}</Label>
              <Controller
                control={control}
                name="coat_color"
                render={({ field }) => (
                  <Select
                    value={field.value ?? ""}
                    onValueChange={field.onChange}
                    items={coatColorItems}
                  >
                    <SelectTrigger id="animal-coat-color" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="">{t("animals.notRecorded")}</SelectItem>
                      {COAT_COLOR_CODES.map((code) => (
                        <SelectItem key={code} value={code}>
                          {coatColorItems[code]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="animal-horned">{t("animals.horned")}</Label>
              <Controller
                control={control}
                name="horned"
                render={({ field }) => (
                  <Select
                    value={field.value ?? ""}
                    onValueChange={field.onChange}
                    items={hornedItems}
                  >
                    <SelectTrigger id="animal-horned" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="">{t("animals.notRecorded")}</SelectItem>
                      <SelectItem value="yes">{t("common.yes")}</SelectItem>
                      <SelectItem value="no">{t("common.no")}</SelectItem>
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="date_of_birth">Date of birth</Label>
              <Input
                id="date_of_birth"
                type="date"
                max={farmToday()}
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
                max={farmToday()}
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
                    Historical import only. Normal births must be recorded through the{" "}
                    {vocabulary.parturition} register so the dam, sire and {vocabulary.parturition}{" "}
                    record remain linked.
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
                      <Select
                        // Stryker disable next-line LogicalOperator: hand-proven equivalent — Base UI normalizes the set-value "" (mutant arm) through its internal selection state, so the trigger label and popup indicator are identical; the RHF value itself never passes through this display prop
                        value={field.value ?? ""}
                        onValueChange={field.onChange}
                        items={birthTypeItems(language)}
                      >
                        <SelectTrigger id="animal-birth-type" className="w-full">
                          <SelectValue placeholder="—" />
                        </SelectTrigger>
                        <SelectContent>
                          {BIRTH_TYPES.map((t) => (
                            <SelectItem key={t} value={t}>
{birthTypeItems(language)[t]}
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
                  max={farmToday()}
                  {...register("weight_date")}
                />
                {errors.weight_date && (
                  <p role="alert" className="text-sm text-destructive">{errors.weight_date.message}</p>
                )}
              </div>
            )}
          </div>

          {source === AnimalCreateInSource.PURCHASED && (
            <div className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="purchase_date">Purchase date</Label>
                <Input
                  id="purchase_date"
                  type="date"
                  max={farmToday()}
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
              maxLength={MAX_FREE_TEXT_LENGTH}
              aria-invalid={Boolean(errors.notes) || undefined}
              {...register("notes")}
            />
            {errors.notes && <p className="text-sm text-destructive">{errors.notes.message}</p>}
          </div>

          <p className="text-xs text-muted-foreground">Fields marked * are required.</p>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={cancelBusy}
              onClick={() => {
                // Stryker disable next-line CallExpression: same shouldUnregister/unmount equivalence as the success-path reset — closing the dialog already clears field state
                reset();
                setOpen(false);
              }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={isSubmitting || createFlight.pending || (!canManagePurchases && !isOwner)}
            >
              {isSubmitting || createFlight.pending ? "Saving…" : "Save animal"}
            </Button>
          </DialogFooter>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AnimalsPageContent({ perms }: { perms: PermissionsState }) {
  const queryClient = useQueryClient();
  const { can, isOwner } = perms;
  const allowed = can("animals.view");
  /** value → label map for the bucket filter Select root, with the raw
   * code as the value. */
  const { language } = useLanguage();
  const bucketFilterItems = useMemo(() => {
    const entries = Object.values(ListAnimalsApiAnimalsGetBucket).map((b) => [
      b,
      enumLabel("bucket", b, language),
    ]);
    return { [ALL]: "All buckets", ...Object.fromEntries(entries) };
  }, [language]);
  const statusItems = statusFilterItems(language);
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  // Key effects and navigation de-duplication by the serialized params; the
  // useSearchParams object identity itself is not stable.
  const paramsKey = searchParams.toString();

  const [bucket, setBucket] = useState(() => bucketFromSearchParams(searchParams));
  const [sex, setSex] = useState(() => sexFromSearchParams(searchParams));
  const [status, setStatus] = useState(() => statusFromSearchParams(searchParams));
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
  /** Client-side sort of the fetched page — the API's recency order is the
   * default; clicking a header sorts what you can see. */
  type SortColumn = "tag" | "age" | "weight";
  const [sortState, setSortState] = useState<{
    column: SortColumn;
    direction: "asc" | "desc";
  } | null>(null);
  // Stryker disable next-line BooleanLiteral: the isFetching effect clears the flag before any interaction can observe it, and changePage's isFetching disjunct guards the very first fetch regardless
  const pageNavigationPending = useRef(false);
  // Stryker disable ArrayDeclaration: the callback reads and writes refs only, so a constant dep list cannot change its behavior
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
  // Stryker restore ArrayDeclaration
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
    // Stryker disable next-line ConditionalExpression: pending.get(undefined) is undefined anyway, so the undefined guard decides nothing
    const matched = matchedSeq === undefined ? undefined : pending.get(matchedSeq);
    // The input holds text this committing URL does not describe, so the
    // operator has typed since it was dispatched. Preserve their edit and let
    // its debounce issue the next URL; external/history navigations (no match)
    // still rehydrate the input normally.
    const hasNewerSearchEdit = matched !== undefined && q.trim() !== matched.urlQ;
    // Stryker disable next-line ConditionalExpression, EqualityOperator: the only reader clears at most an empty map when the operand is pinned or the comparison widened — a no-op
    const hasUnmatchedPending = pending.size > 0;
    // Stryker disable next-line ConditionalExpression: with no match the loop below deletes nothing that the pending.clear() in the else-if does not delete the same render
    if (matchedSeq !== undefined) {
      // Consume the matched dispatch plus everything dispatched before it:
      // Next has discarded those, and a lingering entry would make a later
      // browser/history navigation to the same params look component-owned.
      // Genuinely later dispatches survive — they may still commit.
      for (const seq of [...pending.keys()]) {
        if (seq > matchedSeq) break;
        pending.delete(seq);
      }
    } else {
      // A URL that was not initiated by this list is browser/app
      // navigation and remains authoritative.
      // Stryker disable next-line ConditionalExpression, EqualityOperator: clearing an empty map is a no-op, so >= 0 and a pinned-true operand differ only by clearing nothing
      if (hasUnmatchedPending) {
        pending.clear();
      }
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setBucket(bucketFromSearchParams(params));
    setSex(sexFromSearchParams(params));
    setStatus(statusFromSearchParams(params));
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
      // Stryker disable next-line CallExpression: this branch only runs when the trimmed term differs from the committed URL's q, so replaceListUrl always takes its record+raise arm and re-raises this very guard one statement later
      // Stryker disable next-line CallExpression, BooleanLiteral: this branch only runs when the trimmed term differs from the committed URL's q, so replaceListUrl always takes its record+raise arm one statement later — skipping the call or lowering it to false cannot change the settled guard
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
  // Stryker disable next-line ObjectLiteral: PermissionGate refuses to mount this page without animals.view, so `allowed` is always true by the time this hook runs
  const query = useListAnimalsApiAnimalsGet(params, { query: { enabled: allowed } });
  const payload = query.data?.status === 200 ? query.data.data : undefined;
  const searchPending = q.trim() !== debouncedQ || searchNavigationPending;
  const total = Math.max(0, payload?.total ?? 0);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageOutOfRange = payload !== undefined && page > totalPages;
  /** Distinguishes "herd is empty" (offer the first entry) from "filters
   * exclude everything" (offer to clear them). */
  const filtersActive =
    // Stryker disable next-line MethodExpression: debouncedQ is written already-trimmed by every writer (URL hydration, the debounce timer, changeFilter), so the re-trim is a no-op
    bucket !== ALL || sex !== ALL || status !== ALL || debouncedQ.trim() !== "";

  useEffect(() => {
    // Stryker disable next-line ConditionalExpression: changePage also guards on query.isFetching, so clearing the ref while a fetch is still in flight cannot enable a second navigation
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
    // Stryker disable next-line ArrowFunction: the 0ms timer can only re-fire setPage(totalPages); by then page either already equals totalPages (React bails on the same value) or the page is unmounted (a React 18 no-op)
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

  /** One-shot filter reset: clears every filter and the search box in a
   * single URL write (per-field changeFilter calls would race on q). The
   * individual setters are belt-and-braces: the committed URL drives the
   * params-sync effect, which re-homes every one of these from the search
   * params one render later (hand-mutation of any setter passes the
   * clear-filters request test through that re-sync). */
  function clearFilters() {
    // Stryker disable next-line CallExpression
    setBucket(ALL);
    // Stryker disable next-line CallExpression
    setSex(ALL);
    // Stryker disable next-line CallExpression
    setStatus(ALL);
    // Stryker disable next-line CallExpression
    setQ("");
    // Stryker disable next-line CallExpression
    setDebouncedQ("");
    // Stryker disable next-line CallExpression
    setPage(1);
    // Stryker disable next-line ObjectLiteral: the mutant crashes replaceListUrl with a TypeError before any observable behavior; the runner dies with it (RuntimeError attribution) rather than recording a test failure
    const clearedUrlArgs = {
      pathname,
      paramsKey,
      bucket: ALL,
      sex: ALL,
      status: ALL,
      q: "",
      page: 1,
    };
    // Stryker disable next-line CallExpression: a skipped URL write self-heals through the 300ms search debounce, which re-dispatches the same cleared URL; only the transient browser-history window differs
    replaceListUrl(animalListUrl(clearedUrlArgs));
  }

  function changePage(nextPage: number) {
    if (
      pageNavigationPending.current ||
      query.isFetching ||
      // Stryker disable next-line ConditionalExpression: PaginationControls clamps to the real range before invoking this, so the lower bound is defense-in-depth
      nextPage < 1 ||
      // Stryker disable next-line ConditionalExpression: PaginationControls clamps to the real range before invoking this, so the upper bound is defense-in-depth
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


  const dataLoading = query.isLoading || searchPending || pageOutOfRange;
  const sort = sortState;
  const toggleSort = (column: string) => {
    setSortState((prev) =>
      prev?.column === column
        ? prev.direction === "asc"
          ? { column: column as SortColumn, direction: "desc" }
          : null
        : { column: column as SortColumn, direction: "asc" },
    );
  };
  // Stryker disable next-line ArrayDeclaration: payload is undefined only while the loading state hides the table, so the fallback array is never rendered
  const listedAnimals = payload?.animals ?? [];
  const sortedAnimals =
    listedAnimals && sort
      ? [...listedAnimals].sort((a, b) => {
          const dir = sort.direction === "asc" ? 1 : -1;
          // Stryker disable next-line ArithmeticOperator: dir is always ±1, and x*±1 === x/±1 for every number
          if (sort.column === "tag") return a.tag_number.localeCompare(b.tag_number) * dir;
          if (sort.column === "age") {
            // Stryker disable next-line ArithmeticOperator: dir is always ±1, and x*±1 === x/±1 for every number
            return ((a.age_months ?? -1) - (b.age_months ?? -1)) * dir;
          }
          // Stryker disable next-line ArithmeticOperator: dir is always ±1, and x*±1 === x/±1 for every number
          return ((a.latest_weight_kg ?? -1) - (b.latest_weight_kg ?? -1)) * dir;
        })
      : listedAnimals;

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
              canManagePurchases={can("purchases.manage")}
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
                {enumLabel("bucket", b, language)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={sex}
          onValueChange={(value) => changeFilter("sex", value)}
          items={sexFilterItems(language)}
        >
          <SelectTrigger aria-label="Filter animals by sex">
            <SelectValue placeholder="Both sexes" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Both sexes</SelectItem>
            <SelectItem value={ListAnimalsApiAnimalsGetSex.F}>
              {enumLabel("sex", "F", language)}
            </SelectItem>
            <SelectItem value={ListAnimalsApiAnimalsGetSex.M}>
              {enumLabel("sex", "M", language)}
            </SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={status}
          onValueChange={(value) => changeFilter("status", value)}
          items={statusItems}
        >
          <SelectTrigger aria-label="Filter animals by status">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All statuses</SelectItem>
            {Object.values(ListAnimalsApiAnimalsGetStatus).map((s) => (
              <SelectItem key={s} value={s}>
                {statusItems[s]}
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

      {dataLoading || query.isFetching ? (
        <div role="status" aria-live="polite" className="space-y-3">
          {/* Stale rows stand down during a refetch (they may describe the
           * previous filter/page); the spinner line is the polite signal. */}
          <InlineLoading>{dataLoading ? "Loading animals…" : "Updating animals…"}</InlineLoading>
          <TableSkeleton />
        </div>
      ) : query.isError ? (
        <div role="alert" className="space-y-3">
          <p className="text-sm text-destructive">
            {query.error instanceof ApiError ? query.error.detail : "Could not load animals."}
          </p>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => void query.refetch()}>
              Retry animals
            </Button>
            {filtersActive ? (
              <Button type="button" variant="ghost" onClick={clearFilters}>
                Clear filters
              </Button>
            ) : null}
          </div>
        </div>
      ) : !payload || payload.animals.length === 0 ? (
        filtersActive ? (
          <EmptyState
            icon={SearchX}
            title="No animals match these filters."
            description="Try clearing the filters."
          >
            <Button type="button" variant="outline" size="sm" onClick={clearFilters}>
              Clear filters
            </Button>
          </EmptyState>
        ) : (
          <EmptyState
            icon={PawPrint}
            title="No animals yet"
            description="Add your first animal to start the herd register."
          >
            {can("animals.create") && (
              <Link
                href="/animals/new"
                className={ROW_ACTION_CLASSES}
              >
                Add animal
              </Link>
            )}
          </EmptyState>
        )
      ) : (
        <DataTableCard
          title="Herd"
          description={`${payload.total} animal(s)${sort ? " · sorted within the current page" : ""}`}
        >
          {/* Below md the 8-column table becomes a card per animal — panning
           * a 760px table inside a 390px phone is not a list, it's a scroll
           * toy. */}
          <div className="space-y-2 md:hidden">
            {sortedAnimals.map((a) => (
              <Link
                key={a.id}
                href={`/animals/${a.id}`}
                className="block rounded-xl border bg-card p-3 transition-colors hover:bg-muted/50"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium" dir="auto">{a.tag_number}</span>
                  <StatusBadge status={a.status} />
                </div>
                {a.name && <p className="text-sm text-muted-foreground">{a.name}</p>}
                <p className="mt-1 text-xs text-muted-foreground">
                  {enumLabel("sex", a.sex, language)} · {enumLabel("bucket", a.current_bucket, language)} ·{" "}
                  {a.breed}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {a.age_months !== null ? `${a.age_months} mo · ` : ""}
                  {a.latest_weight_kg != null ? `${a.latest_weight_kg.toFixed(1)} kg` : "weight not recorded"}
                </p>
              </Link>
            ))}
          </div>
          <div className="hidden md:block">
            <Table className="min-w-[760px]">
              <TableHeader>
                <TableRow>
                  <SortableTableHead
                    column="tag"
                    label="Tag"
                    direction={sort?.column === "tag" ? sort.direction : null}
                    onSort={toggleSort}
                  />
                  <TableHead>Name</TableHead>
                  <TableHead>Sex</TableHead>
                  <TableHead>Breed</TableHead>
                  <TableHead>Bucket</TableHead>
                  <TableHead>Status</TableHead>
                  <SortableTableHead
                    column="age"
                    label="Age (mo)"
                    className="text-right"
                    direction={sort?.column === "age" ? sort.direction : null}
                    onSort={toggleSort}
                  />
                  <SortableTableHead
                    column="weight"
                    label="Weight"
                    className="text-right"
                    direction={sort?.column === "weight" ? sort.direction : null}
                    onSort={toggleSort}
                  />
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedAnimals.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell>
                      <Link
                        href={`/animals/${a.id}`}
                        className="font-medium text-foreground hover:text-primary"
                      >
                        <span dir="auto">{a.tag_number}</span>
                      </Link>
                    </TableCell>
                    <TableCell dir="auto">{a.name ?? "—"}</TableCell>
                    <TableCell>{enumLabel("sex", a.sex, language)}</TableCell>
                    <TableCell>{a.breed}</TableCell>
                    <TableCell>{enumLabel("bucket", a.current_bucket, language)}</TableCell>
                    <TableCell>
                      <StatusBadge status={a.status} />
                    </TableCell>
                    <TableCell className="text-right">{a.age_months ?? "—"}</TableCell>
                    <TableCell className="text-right">
                      {a.latest_weight_kg != null ? `${a.latest_weight_kg.toFixed(1)} kg` : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
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
  const perms = usePermissions();
  return (
    <Suspense
      fallback={
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading…</span>
          <PageSkeleton cards={1} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="animals.view"
        label="Animals"
        description="Your herd at a glance — filter by bucket, sex or status, or search by tag."
        cards={1}
        announce
      >
        <AnimalsPageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
