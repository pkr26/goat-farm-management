"use client";

/** Health event log + record-event dialog — parity with v1's health/list.html + health/new.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Plus, SearchX, Syringe } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Controller, useForm, useWatch, type FieldErrors } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  useGetTaskApiTasksTaskIdGet,
  useListEventsApiHealthEventsGet,
  useListTasksApiTasksGet,
  usePreviewBulkEventTargetsApiHealthEventsPreviewPost,
  useRecordEventApiHealthEventsPost,
  useScheduleTemplatesApiHealthScheduleTemplatesGet,
} from "@/api/generated/endpoints";
import {
  HealthEventInBucket,
  HealthEventInType,
  type HealthBulkTargetIn,
  type HealthBulkTargetPreviewOut,
  type HealthEventIn,
  type HealthEventOut,
  type TaskOut,
} from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import {
  HealthAnimalPicker,
  HealthPurchaseBatchPicker,
} from "@/components/health-target-pickers";
import { useFarmType } from "@/hooks/use-farm-type";
import { enumLabel } from "@/lib/enum-labels";
import { PageHeader } from "@/components/page-header";
import { PaginationControls } from "@/components/pagination-controls";
import {
  CardSkeleton,
  InlineLoading,
  PageSkeleton,
  TableSkeleton,
} from "@/components/skeletons";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
import { ApiError, farmScopeEpochValue } from "@/lib/api-client";
import { addDays, farmToday, formatDate, formatMoney } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import {
  isPersistableNonnegativeMoney,
  MIN_PERSISTED_MONEY_MESSAGE,
} from "@/lib/persisted-numbers";
import { permittedAppPath, withReturnTo } from "@/lib/permission-navigation";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { useUrlState } from "@/lib/use-url-state";

import { taskPrefill } from "./task-prefill";
import { PermissionsError } from "@/components/permissions-error";

const EVENT_TYPES = Object.values(HealthEventInType);
const BUCKETS = Object.values(HealthEventInBucket);
const ROUTES = ["SC", "Oral", "IM"];
const MAX_HEALTH_EVENT_COST = 1_000_000_000;
const MAX_HEALTH_EVENT_NOTES = 4_000;
/** Mirrors backend/app/models/constants.py. An immutable event with a
 * mistyped withdrawal year must not hold an animal out of sale indefinitely. */
const MAX_WITHDRAWAL_DAYS = 730;
/** Sentinel for "no selection" in optional selects (empty string is not a valid item value). */
const NONE = "none";
/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
const ROUTE_ITEMS: Record<string, string> = {
  [NONE]: "—",
  ...Object.fromEntries(ROUTES.map((r) => [r, r])),
};
const EVENT_TYPE_ITEMS: Record<string, string> = Object.fromEntries(
  EVENT_TYPES.map((t) => [t, enumLabel("eventType", t)]),
);

function localToday(): string {
  return farmToday();
}

/** Canonicalise URL/select ids without JavaScript's permissive Number()
 * syntax (for example, "1e2" must never silently target record 100). */
function positiveIdString(raw: string | null | undefined): string | null {
  if (!raw || !/^\d+$/.test(raw)) return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? String(parsed) : null;
}

/** Next-due date cell: destructive tint when overdue, warning tint when due
 *  within a week. Comparisons use the active farm's calendar day. */
function NextDue({ date }: { date: string }) {
  const overdue = date < farmToday();
  const dueSoon = !overdue && date <= addDays(farmToday(), 7);
  if (!overdue && !dueSoon) return <>{formatDate(date)}</>;
  return (
    <Badge variant={overdue ? "destructive" : "warning"}>
      <CalendarClock aria-hidden="true" className="size-3" />
      {formatDate(date)}
    </Badge>
  );
}

/** Animal/target label for one event row — shared by the desktop table cell
 *  and the mobile card so the two never drift. */
function EventAnimalLabel({
  event,
  canViewAnimals,
}: {
  event: HealthEventOut;
  canViewAnimals: boolean;
}) {
  if (event.animal_tag && canViewAnimals) {
    return (
      <Link
        href={withReturnTo(`/animals/${event.animal_id}`, "/health")}
        className="text-primary underline"
      >
        {event.animal_tag}
      </Link>
    );
  }
  if (event.animal_tag) return <>{event.animal_tag}</>;
  if (event.purchase_batch_id) return <>batch #{event.purchase_batch_id}</>;
  if (event.animal_id !== null) return <>#{event.animal_id}</>;
  // Animal-scoped rows keep #id; reaching this branch with every id null
  // means the event targeted a whole bucket.
  return <span className="text-muted-foreground">bucket-wide</span>;
}

const eventSchema = z
  .object({
    scope: z.enum(["animal", "bucket", "batch"]),
    animal_id: z.string().optional(),
    bucket: z.string().optional(),
    purchase_batch_id: z.string().optional(),
    date: z.string().optional(),
    type: z.enum(["VACCINE", "DEWORMING", "TREATMENT", "FOOTBATH", "VITAMIN"]),
    product_name: z.string().max(120).optional(),
    disease_target: z.string().max(120).optional(),
    dose: z.string().max(60).optional(),
    route: z.string().max(20).optional(),
    vet_name: z.string().max(120).optional(),
    cost: z
      .string()
      .refine(
        (s) => s === "" || (Number.isFinite(Number(s)) && Number(s) >= 0),
        "Cost must be a number ≥ 0",
      )
      .refine(
        (s) => s === "" || isPersistableNonnegativeMoney(Number(s)),
        MIN_PERSISTED_MONEY_MESSAGE,
      )
      .refine(
        (s) => s === "" || Number(s) <= MAX_HEALTH_EVENT_COST,
        "Cost cannot exceed ₹1,000,000,000",
      )
      .optional(),
    next_due_date: z.string().optional(),
    schedule_template_name: z.string().max(120).optional(),
    next_due_authority: z.string().max(120).optional(),
    product_lot: z.string().max(120).optional(),
    product_manufactured_on: z.string().optional(),
    product_expires_on: z.string().optional(),
    vaccine_valid_until: z.string().optional(),
    certificate_number: z.string().max(120).optional(),
    official_tag_number: z.string().max(80).optional(),
    administered_by: z.string().max(120).optional(),
    withdrawal_until: z.string().optional(),
    suspected_scheduled_disease: z.boolean(),
    authority_notified_at: z.string().optional(),
    isolation_started_at: z.string().optional(),
    notes: z
      .string()
      .max(MAX_HEALTH_EVENT_NOTES, "Notes cannot exceed 4000 characters")
      .optional(),
    task_id: z.string().optional(),
  })
  .superRefine((v, ctx) => {
    // A blank event date has a defined API meaning: the active farm's today.
    // Validate every dependent date against that effective value rather than
    // letting blank-date submissions pass here and fail at the endpoint.
    const eventDate = v.date || localToday();
    if (v.scope === "animal" && positiveIdString(v.animal_id) === null) {
      ctx.addIssue({ code: "custom", path: ["animal_id"], message: "Pick an animal" });
    }
    if (v.scope === "bucket" && !v.bucket) {
      ctx.addIssue({ code: "custom", path: ["bucket"], message: "Pick a bucket" });
    }
    if (v.scope === "batch" && positiveIdString(v.purchase_batch_id) === null) {
      ctx.addIssue({ code: "custom", path: ["purchase_batch_id"], message: "Pick a batch" });
    }
    if (v.task_id && v.task_id !== NONE && positiveIdString(v.task_id) === null) {
      ctx.addIssue({ code: "custom", path: ["task_id"], message: "Pick a valid duty" });
    }
    if (v.date && v.date > localToday()) {
      ctx.addIssue({ code: "custom", path: ["date"], message: "Date cannot be in the future" });
    }
    if (v.next_due_date) {
      if (v.next_due_date <= eventDate) {
        ctx.addIssue({
          code: "custom",
          path: ["next_due_date"],
          message: "Next due date must be after the event date",
        });
      }
      if (!v.schedule_template_name?.trim()) {
        ctx.addIssue({
          code: "custom",
          path: ["schedule_template_name"],
          message: "Name the schedule used for a next-due date",
        });
      }
      if (!v.next_due_authority?.trim()) {
        ctx.addIssue({
          code: "custom",
          path: ["next_due_authority"],
          message: "Record the authority for this next-due date",
        });
      }
    }
    if (v.product_manufactured_on && v.product_manufactured_on > localToday()) {
      ctx.addIssue({
        code: "custom",
        path: ["product_manufactured_on"],
        message: "Manufacture date cannot be in the future",
      });
    }
    if (v.product_manufactured_on && v.product_manufactured_on > eventDate) {
      ctx.addIssue({
        code: "custom",
        path: ["product_manufactured_on"],
        message: "Manufacture date cannot be after the event date",
      });
    }
    if (
      v.product_manufactured_on &&
      v.product_expires_on &&
      v.product_expires_on < v.product_manufactured_on
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["product_expires_on"],
        message: "Expiry cannot be before manufacture date",
      });
    }
    if (v.product_expires_on && v.product_expires_on < eventDate) {
      ctx.addIssue({
        code: "custom",
        path: ["product_expires_on"],
        message: "Product was expired on the event date",
      });
    }
    if (v.vaccine_valid_until && v.vaccine_valid_until < eventDate) {
      ctx.addIssue({
        code: "custom",
        path: ["vaccine_valid_until"],
        message: "Vaccine validity cannot be before the event date",
      });
    }
    if (
      v.vaccine_valid_until &&
      v.product_expires_on &&
      v.vaccine_valid_until > v.product_expires_on
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["vaccine_valid_until"],
        message: "Vaccine validity cannot extend beyond product expiry",
      });
    }
    if (v.withdrawal_until) {
      if (v.withdrawal_until < eventDate) {
        ctx.addIssue({
          code: "custom",
          path: ["withdrawal_until"],
          message: "Withdrawal date cannot be before the event date",
        });
      } else if (v.withdrawal_until > addDays(eventDate, MAX_WITHDRAWAL_DAYS)) {
        ctx.addIssue({
          code: "custom",
          path: ["withdrawal_until"],
          message: `Withdrawal date cannot be more than ${MAX_WITHDRAWAL_DAYS} days after the event`,
        });
      }
    }
    if (v.suspected_scheduled_disease && !v.disease_target?.trim()) {
      ctx.addIssue({
        code: "custom",
        path: ["disease_target"],
        message: "Name the suspected scheduled disease",
      });
    }
    for (const field of ["authority_notified_at", "isolation_started_at"] as const) {
      if (v[field] && v[field] > localToday()) {
        ctx.addIssue({ code: "custom", path: [field], message: "Date cannot be in the future" });
      }
    }
  });
type EventValues = z.infer<typeof eventSchema>;

/** Every field rendered inside the collapsed "Advanced traceability &
 * compliance" `<details>`. A visible field can make one of these mandatory
 * (setting next_due_date requires schedule_template_name and
 * next_due_authority), so a submit can fail purely on errors the closed
 * section hides — and react-hook-form cannot focus an input the browser is
 * not rendering, leaving the form silently stuck. The invalid-submit handler
 * forces the section open whenever one of these fields carries an error. */
const ADVANCED_COMPLIANCE_FIELDS = [
  "schedule_template_name",
  "next_due_authority",
  "product_lot",
  "administered_by",
  "product_manufactured_on",
  "product_expires_on",
  "vaccine_valid_until",
  "withdrawal_until",
  "certificate_number",
  "official_tag_number",
  "authority_notified_at",
  "isolation_started_at",
] as const satisfies readonly (keyof EventValues)[];

/** Rebuilt on every reset: a bare reset() restores react-hook-form's
 * mount-time snapshot, which dates events to the day the tab was opened. */
function eventDefaults(): EventValues {
  return {
    scope: "animal",
    animal_id: "",
    bucket: "",
    purchase_batch_id: "",
    date: localToday(),
    type: "VACCINE",
    product_name: "",
    disease_target: "",
    dose: "",
    route: NONE,
    vet_name: "",
    cost: "",
    next_due_date: "",
    schedule_template_name: "",
    next_due_authority: "",
    product_lot: "",
    product_manufactured_on: "",
    product_expires_on: "",
    vaccine_valid_until: "",
    certificate_number: "",
    official_tag_number: "",
    administered_by: "",
    withdrawal_until: "",
    suspected_scheduled_disease: false,
    authority_notified_at: "",
    isolation_started_at: "",
    notes: "",
    task_id: NONE,
  };
}

function FieldError({ message, id }: { message?: string; id?: string }) {
  if (!message) return null;
  return <p id={id} role="alert" className="text-sm text-destructive">{message}</p>;
}

function HealthPageContent() {
  const { can, loading: permsLoading, isError: permsError , refetch: permsRefetch } = usePermissions();
  const allowed = can("health.view");
  const canManage = can("health.manage");
  const canViewAnimals = can("animals.view");
  const canViewTasks = can("tasks.view");
  const farmType = useFarmType();
  /** value → label map for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const bucketItems: Record<string, string> = Object.fromEntries(
    BUCKETS.map((b) => [b, enumLabel("bucket", b, farmType)]),
  );
  const queryClient = useQueryClient();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchParamsKey = searchParams.toString();
  const returnTo = permittedAppPath(searchParams.get("returnTo"), can);
  const hasDeepLink = ["task_id", "animal_id", "purchase_batch_id"].some((key) =>
    searchParams.has(key),
  );
  // The event-log page lives in the URL (back/forward and refresh keep the
  // page you were on); `offset` is dropped when it returns to the first page.
  const { getNumber, set: setUrlState } = useUrlState();
  const eventOffset = getNumber("offset", 0, 0, 1_000_000);
  const setEventOffset = (next: number) =>
    setUrlState({ offset: next > 0 ? next : null });
  const eventLimit = 50;

  const eventsQuery = useListEventsApiHealthEventsGet(
    { limit: eventLimit, offset: eventOffset },
    {
      query: {
        enabled: allowed,
        // The offset lives in the query key; keep the previous page rendered
        // while a page turn settles instead of blanking the whole log (M-12).
        placeholderData: (previous) => previous,
      },
    },
  );
  const eventsSettling = eventsQuery.isPlaceholderData;
  const eventPayload = eventsQuery.data?.status === 200 ? eventsQuery.data.data : undefined;

  const [open, setOpen] = useState(false);
  const deepLinkedTaskIdParam = searchParams.get("task_id");
  const deepLinkedTaskIdValue = positiveIdString(deepLinkedTaskIdParam);
  const deepLinkedTaskId =
    deepLinkedTaskIdValue === null ? null : Number(deepLinkedTaskIdValue);
  const tasksQuery = useListTasksApiTasksGet(undefined, {
    query: { enabled: canManage && canViewTasks && open },
  });
  const exactTaskQuery = useGetTaskApiTasksTaskIdGet(deepLinkedTaskId ?? 0, {
    query: {
      enabled: canManage && canViewTasks && open && deepLinkedTaskId !== null,
      retry: false,
    },
  });
  const tabs = tasksQuery.data?.status === 200 ? tasksQuery.data.data : undefined;
  const pendingHealthTasks: TaskOut[] = tabs
    ? [...tabs.today, ...tabs.overdue, ...tabs.upcoming].filter(
        (t) =>
          t.status === "PENDING" &&
          (t.category === "VACCINE" || t.category === "DEWORMING") &&
          // api/health.py rejects an event dated before the duty's due date,
          // and the event date can never be in the future — so a duty that is
          // not due yet could only ever produce a 409.
          t.due_date <= localToday(),
      )
    : [];
  const exactTask =
    exactTaskQuery.data?.status === 200 &&
    exactTaskQuery.data.data.status === "PENDING" &&
    (exactTaskQuery.data.data.category === "VACCINE" ||
      exactTaskQuery.data.data.category === "DEWORMING")
      ? exactTaskQuery.data.data
      : undefined;
  // The tab response is intentionally bounded. Keep an eligible task fetched
  // by its exact deep-link id even when it is outside that window (or not due
  // yet); the writer will enforce due-date and assignment rules fail-closed.
  const linkableHealthTasks =
    exactTask && !pendingHealthTasks.some((task) => task.id === exactTask.id)
      ? [exactTask, ...pendingHealthTasks]
      : pendingHealthTasks;

  const recordMutation = useRecordEventApiHealthEventsPost();
  const previewMutation = usePreviewBulkEventTargetsApiHealthEventsPreviewPost();
  const eventSubmission = useSingleFlight();
  const [recordError, setRecordError] = useState<string | null>(null);
  const [bulkPreview, setBulkPreview] = useState<HealthBulkTargetPreviewOut | null>(null);
  const scheduleAnimalParam = searchParams.get("schedule_animal_id");
  const requestedScheduleAnimalId = positiveIdString(scheduleAnimalParam) ?? "";
  const [scheduleAnimalId, setScheduleAnimalId] = useState(
    () => requestedScheduleAnimalId,
  );
  const previousScheduleAnimalParam = useRef(scheduleAnimalParam);
  const [prefillTaskId, setPrefillTaskId] = useState<string | null>(null);
  /** A deep-linked duty id we could not resolve — surfaced so the operator
   *  knows the event will be recorded without completing that duty. */
  const [unresolvedPrefillTask, setUnresolvedPrefillTask] = useState<string | null>(null);
  /** Controlled open state of the Advanced `<details>`: user toggles update
   *  it (never fought), and a failed submit with an error hidden inside the
   *  collapsed section forces it open (see ADVANCED_COMPLIANCE_FIELDS). */
  const [advancedOpen, setAdvancedOpen] = useState(false);

  /** value → label map for the local task select. */
  const taskItems: Record<string, string> = {
    [NONE]: "— none —",
    ...Object.fromEntries(
      linkableHealthTasks.map((t) => [String(t.id), `${t.title} (due ${formatDate(t.due_date)})`]),
    ),
  };

  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    getValues,
    unregister,
    formState: { errors, isSubmitting },
  } = useForm<EventValues>({
    resolver: zodResolver(eventSchema),
    defaultValues: eventDefaults(),
  });
  const wAnimalId = useWatch({ control, name: "animal_id" });
  const wBucket = useWatch({ control, name: "bucket" });
  const wPurchaseBatchId = useWatch({ control, name: "purchase_batch_id" });
  const wRoute = useWatch({ control, name: "route" });
  const wTaskId = useWatch({ control, name: "task_id" });
  const wType = useWatch({ control, name: "type" });
  const scope = useWatch({ control, name: "scope" });
  // VACCINE/DEWORMING events accept ONLY an exact seeded programme name
  // (validated_template rejects anything else); the other types take free text.
  const templateIsSeeded = wType === "VACCINE" || wType === "DEWORMING";
  const scheduleTemplates = useScheduleTemplatesApiHealthScheduleTemplatesGet({
    query: { enabled: templateIsSeeded, staleTime: Infinity },
  });
  const templateOptions =
    scheduleTemplates.data?.status === 200
      ? scheduleTemplates.data.data.templates.filter((item) => item.event_type === wType)
      : [];
  const suspectedScheduledDisease = useWatch({
    control,
    name: "suspected_scheduled_disease",
  });

  /** The last URL deep link hydrated into the dialog. Next can reuse this
   *  page for a query-only navigation, so a lifetime boolean would discard a
   *  later task/animal link. The signature still prevents permission-query
   *  rerenders from reopening a link the operator just dismissed. */
  const deepLinkHydratedRef = useRef<string | null>(null);

  /** Identifies one dialog session's submission. The record dialog stays
   *  dismissible while its write is in flight — deliberately, because the
   *  request has no cancel and blocking dismissal would strand the operator
   *  behind a modal backdrop. So the continuation must instead check that the
   *  session it is about to close and reset is still its own. */
  const submissionEpoch = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      // A late preview/write cannot own state or navigation after this page
      // has gone away. This also covers ordinary same-farm navigation; the
      // farm epoch below covers the smaller pre-unmount switch window.
      submissionEpoch.current += 1;
    };
  }, []);

  useEffect(() => {
    // The schedule picker is editable local state, so unrelated query changes
    // must not fight the user's selection. A real schedule-param navigation,
    // however (including Back/Forward while Next reuses this page), is a new
    // URL intent and must replace the old mount-time value.
    if (previousScheduleAnimalParam.current === scheduleAnimalParam) return;
    previousScheduleAnimalParam.current = scheduleAnimalParam;
    setScheduleAnimalId(requestedScheduleAnimalId);
  }, [requestedScheduleAnimalId, scheduleAnimalParam]);

  /** Values the last linked duty prefilled — used to revert them when the
   *  user switches back to "— none —" without clobbering manual edits
   *. */
  const appliedPrefillRef = useRef<{
    scope?: EventValues["scope"];
    type?: EventValues["type"];
    product_name?: string;
    disease_target?: string;
  } | null>(null);

  function resetEventForm() {
    // The visible form and this metadata form one lifecycle. Leaving the
    // previous duty metadata behind can make a later, manually entered value
    // look like an unchanged prefill and be cleared incorrectly.
    appliedPrefillRef.current = null;
    // Any submission still in flight belongs to the session ending here.
    submissionEpoch.current += 1;
    setAdvancedOpen(false);
    // A duty lookup still in flight belongs to the dialog session being torn
    // down. Left armed, it resolves into the NEXT, unrelated session and
    // silently rewrites scope/target/type over what the operator just entered.
    setPrefillTaskId(null);
    // The unresolved-duty warning belongs to the deep link that opened the
    // previous dialog. Left standing it re-appears on every later event and
    // claims that one is failing to close a duty it never referenced.
    setUnresolvedPrefillTask(null);
    reset(eventDefaults());
  }

  /** handleSubmit error path: validation blocked the save. If any failing
   *  field lives inside the collapsed Advanced section, its message (and the
   *  input react-hook-form wants to focus) is not rendered by the browser, so
   *  the click would otherwise appear to do nothing. Open the section so the
   *  errors are visible; runs on every failed submit, so re-closing it and
   *  retrying cannot regress into the silent state. */
  function revealCollapsedErrors(submissionErrors: FieldErrors<EventValues>) {
    if (ADVANCED_COMPLIANCE_FIELDS.some((field) => submissionErrors[field] !== undefined)) {
      setAdvancedOpen(true);
    }
  }

  /** Prefill scope/target/type/product from a linked VACCINE/DEWORMING duty
   *  (v1 behaviour + product/disease hints). */
  function applyTask(taskIdStr: string) {
    setBulkPreview(null);
    if (taskIdStr === NONE) {
      clearLinkedTaskPrefill(true);
      return;
    }
    // Switching directly from one duty to another must not leave the first
    // duty's inferred product or disease attached to the second one.
    if (appliedPrefillRef.current) clearLinkedTaskPrefill();
    setValue("task_id", taskIdStr);
    const task = linkableHealthTasks.find((t) => String(t.id) === taskIdStr);
    if (!task) return;
    const applied: NonNullable<typeof appliedPrefillRef.current> = {};
    if (task.animal_id) {
      applied.scope = "animal";
      changeScope("animal", true);
      setValue("animal_id", String(task.animal_id));
    } else if (task.purchase_batch_id) {
      applied.scope = "batch";
      changeScope("batch", true);
      setValue("purchase_batch_id", String(task.purchase_batch_id));
    }
    if (task.category === "VACCINE" || task.category === "DEWORMING") {
      applied.type = task.category;
      setValue("type", task.category);
      // Prefill product/disease from the duty title so the recorded event
      // matches the vaccination templates. Don't overwrite text
      // the user already typed.
      const hints = taskPrefill(task);
      if (hints.product_name && !(getValues("product_name") ?? "").trim()) {
        applied.product_name = hints.product_name;
        setValue("product_name", hints.product_name);
      }
      if (hints.disease_target && !(getValues("disease_target") ?? "").trim()) {
        applied.disease_target = hints.disease_target;
        setValue("disease_target", hints.disease_target);
      }
    }
    appliedPrefillRef.current = applied;
  }

  function clearLinkedTaskPrefill(resetTaskScope = false) {
    setBulkPreview(null);
    setValue("task_id", NONE);
    const prev = appliedPrefillRef.current;
    // Clear before changing scope so changeScope cannot process this prefill a
    // second time. User-edited values remain untouched below.
    appliedPrefillRef.current = null;
    if (!prev) return;
    if (resetTaskScope && prev.scope && getValues("scope") === prev.scope) {
      changeScope("animal", true);
    }
    if (prev.type && getValues("type") === prev.type) setValue("type", "VACCINE");
    if (
      prev.product_name !== undefined &&
      (getValues("product_name") ?? "") === prev.product_name
    ) {
      setValue("product_name", "");
    }
    if (
      prev.disease_target !== undefined &&
      (getValues("disease_target") ?? "") === prev.disease_target
    ) {
      setValue("disease_target", "");
    }
  }

  function changeScope(nextScope: EventValues["scope"], preserveLinkedTask = false) {
    setBulkPreview(null);
    setRecordError(null);
    if (!preserveLinkedTask) {
      // A linked duty has an exact animal or purchase-batch scope. Keeping it
      // while the operator chooses another target makes the preview/write a
      // guaranteed 422 rather than a valid unlinked health event.
      clearLinkedTaskPrefill();
    }
    setValue("scope", nextScope, { shouldValidate: true });
    for (const [field, fieldScope] of [
      ["animal_id", "animal"],
      ["bucket", "bucket"],
      ["purchase_batch_id", "batch"],
    ] as const) {
      if (fieldScope !== nextScope) {
        unregister(field);
        setValue(field, "");
      }
    }
  }

  function closeDeepLinkedDialog() {
    setRecordError(null);
    setBulkPreview(null);
    if (returnTo) router.push(returnTo);
    else if (hasDeepLink) router.replace("/health");
  }

  // /health/new?... redirects here: auto-open the dialog and preserve any
  // animal/batch/task context supplied by the originating workflow.
  /* eslint-disable react-hooks/set-state-in-effect -- distinct URL intents initialize a controlled dialog session */
  useEffect(() => {
    const params = new URLSearchParams(searchParamsKey);
    const taskId = positiveIdString(params.get("task_id"));
    const animalId = positiveIdString(params.get("animal_id"));
    const batchId = positiveIdString(params.get("purchase_batch_id"));
    if (!taskId && !animalId && !batchId) {
      deepLinkHydratedRef.current = null;
      return;
    }
    const signature = `${taskId ?? ""}|${animalId ?? ""}|${batchId ?? ""}`;
    // Latch on the URL identity, not on `prefillTaskId`: that flag is cleared
    // both when resolution is consumed and when the dialog is reset. A
    // permission-query rerender must not reopen the same dismissed link, but
    // a later query-only navigation to a different link must be honored.
    if (!canManage || deepLinkHydratedRef.current === signature) return;
    deepLinkHydratedRef.current = signature;
    resetEventForm();
    // One-time mount initialization from URL params — cascading-render risk
    // doesn't apply here (runs once per distinct URL intent, not reactively to
    // the form state it writes).
    setOpen(true);
    if (animalId) {
      changeScope("animal");
      setValue("animal_id", animalId);
    }
    if (batchId) {
      changeScope("batch");
      setValue("purchase_batch_id", batchId);
    }
    if (taskId) {
      setPrefillTaskId(taskId);
      setValue("task_id", taskId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canManage, searchParamsKey]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Resolve the deep link through the exact task endpoint. The tab response is
  // only a fallback because it is a bounded window and cannot establish that
  // an absent id is inaccessible or stale.
  useEffect(() => {
    if (!prefillTaskId) return;
    // URL hydration and task resolution are separate effects. When navigation
    // changes task 1 -> task 2 while task 1 is unresolved, the hydration effect
    // writes task 2 first; this older effect must not then consume that newer
    // state (especially when task 2 is already cached and resolves in the same
    // effect flush).
    if (prefillTaskId !== deepLinkedTaskIdValue) return;
    const known = linkableHealthTasks.some((t) => String(t.id) === prefillTaskId);
    if (
      !known &&
      canViewTasks &&
      deepLinkedTaskId !== null &&
      !exactTaskQuery.isError &&
      !exactTaskQuery.data
    ) {
      return;
    }
    if (!known && canViewTasks && !tasksQuery.isError && !tabs) return;
    // The operator retargeted this event while the duty lookup was in flight
    // (changeScope -> clearLinkedTaskPrefill always writes task_id = NONE).
    // Consume the prefill without applying it, mirroring the `stillCurrent`
    // check the bulk-preview path below already makes. `unresolvedPrefillTask`
    // is deliberately left alone: the operator discarded this link themselves,
    // so warning that it could not be closed would be wrong.
    if (getValues("task_id") !== prefillTaskId) {
      // Intentional one-shot cleanup: consumes the resolution, nothing else.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPrefillTaskId(null);
      return;
    }
    // Intentional one-shot URL hydration after the exact task lookup settles.
    applyTask(known ? prefillTaskId : NONE);
    // Intentional one-shot cleanup after applying the prefill (see above).
    setPrefillTaskId(null);
    setUnresolvedPrefillTask(known ? null : prefillTaskId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    prefillTaskId,
    tasksQuery.data,
    tasksQuery.isError,
    exactTaskQuery.data,
    exactTaskQuery.isError,
    canViewTasks,
    deepLinkedTaskIdValue,
  ]);

  async function submitEvent(values: EventValues) {
    let reviewedAnimalIds: number[] | undefined;
    if (values.scope !== "animal") {
      const selectedBatchId = values.purchase_batch_id
        ? Number(values.purchase_batch_id)
        : null;
      const selectedTaskId =
        values.task_id && values.task_id !== NONE ? Number(values.task_id) : null;
      const previewMatchesSelection =
        bulkPreview?.scope === values.scope &&
        bulkPreview.task_id === selectedTaskId &&
        (values.scope === "bucket"
          ? bulkPreview.bucket === values.bucket
          : bulkPreview.purchase_batch_id === selectedBatchId);
      if (!previewMatchesSelection) {
        const target: HealthBulkTargetIn =
          values.scope === "bucket"
            ? {
                scope: "bucket",
                bucket: values.bucket as HealthBulkTargetIn["bucket"],
                ...(selectedTaskId !== null ? { task_id: selectedTaskId } : {}),
              }
            : {
                scope: "batch",
                purchase_batch_id: selectedBatchId,
                ...(selectedTaskId !== null ? { task_id: selectedTaskId } : {}),
        };
        setRecordError(null);
        const epoch = ++submissionEpoch.current;
        const requestFarmEpoch = farmScopeEpochValue();
        try {
          const response = await previewMutation.mutateAsync({ data: target });
          if (response.status !== 200) return;
          const currentScope = getValues("scope");
          const currentTaskIdValue = getValues("task_id");
          const currentTaskId =
            currentTaskIdValue && currentTaskIdValue !== NONE
              ? Number(currentTaskIdValue)
              : null;
          const stillCurrent =
            mounted.current &&
            farmScopeEpochValue() === requestFarmEpoch &&
            submissionEpoch.current === epoch &&
            currentScope === target.scope &&
            currentTaskId === selectedTaskId &&
            response.data.task_id === selectedTaskId &&
            (target.scope === "bucket"
              ? getValues("bucket") === target.bucket
              : Number(getValues("purchase_batch_id")) === target.purchase_batch_id);
          if (stillCurrent) setBulkPreview(response.data);
        } catch (err) {
          if (
            !mounted.current ||
            farmScopeEpochValue() !== requestFarmEpoch ||
            submissionEpoch.current !== epoch
          ) return;
          const message =
            err instanceof ApiError ? err.detail : "Could not review the bulk target set.";
          setRecordError(message);
          toast.error(message);
        }
        return;
      }
      reviewedAnimalIds = bulkPreview.target_animal_ids;
    }
    const payload: HealthEventIn = {
      scope: values.scope,
      animal_id:
        values.scope === "animal" && values.animal_id ? Number(values.animal_id) : null,
      bucket:
        values.scope === "bucket" && values.bucket
          ? (values.bucket as HealthEventIn["bucket"])
          : null,
      purchase_batch_id:
        values.scope === "batch" && values.purchase_batch_id
          ? Number(values.purchase_batch_id)
          : null,
      date: values.date || null,
      type: values.type,
      product_name: values.product_name?.trim() || null,
      disease_target: values.disease_target?.trim() || null,
      dose: values.dose?.trim() || null,
      route: values.route && values.route !== NONE ? values.route : null,
      vet_name: values.vet_name?.trim() || null,
      cost: values.cost ? Number(values.cost) : null,
      next_due_date: values.next_due_date || null,
      schedule_template_name: values.schedule_template_name?.trim() || null,
      next_due_authority: values.next_due_authority?.trim() || null,
      product_lot: values.product_lot?.trim() || null,
      product_manufactured_on: values.product_manufactured_on || null,
      product_expires_on: values.product_expires_on || null,
      vaccine_valid_until: values.vaccine_valid_until || null,
      certificate_number: values.certificate_number?.trim() || null,
      official_tag_number: values.official_tag_number?.trim() || null,
      administered_by: values.administered_by?.trim() || null,
      withdrawal_until: values.withdrawal_until || null,
      suspected_scheduled_disease: values.suspected_scheduled_disease,
      authority_notified_at: values.suspected_scheduled_disease
        ? values.authority_notified_at || null
        : null,
      isolation_started_at: values.suspected_scheduled_disease
        ? values.isolation_started_at || null
        : null,
      notes: values.notes?.trim() || null,
      task_id: values.task_id && values.task_id !== NONE ? Number(values.task_id) : null,
      expected_animal_ids: reviewedAnimalIds,
    };
    setRecordError(null);
    const epoch = ++submissionEpoch.current;
    const requestFarmEpoch = farmScopeEpochValue();
    try {
      const response = await recordMutation.mutateAsync({ data: payload });
      const recordedCount = response.status === 201 ? response.data.length : 0;
      // The request itself retained its original X-Farm-Id. If ownership has
      // since crossed a farm boundary, none of this old farm's completion may
      // affect the new farm's UI, cache, toast stream, or route.
      if (farmScopeEpochValue() !== requestFarmEpoch) return;
      // The write happened, so confirm it and refresh the farm views
      // even if this dialog session is already over on the SAME farm.
      toast.success(
        values.scope === "animal"
          ? "Health event recorded."
          : `Health event recorded for ${recordedCount} animal${recordedCount === 1 ? "" : "s"}.`,
      );
      invalidateFarmData(queryClient);
      // Beyond this point everything mutates dialog state. If the operator
      // dismissed this dialog and opened a new one, closing and resetting now
      // would wipe what they have since typed, and the navigation below would
      // be a second one on top of the dismissal's own.
      if (!mounted.current || submissionEpoch.current !== epoch) return;
      setOpen(false);
      setBulkPreview(null);
      resetEventForm();
      if (returnTo) router.push(returnTo);
      else if (hasDeepLink) router.replace("/health");
    } catch (err) {
      if (
        !mounted.current ||
        farmScopeEpochValue() !== requestFarmEpoch ||
        submissionEpoch.current !== epoch
      ) return;
      const message = err instanceof ApiError ? err.detail : "Could not save the health event.";
      if (values.scope !== "animal") setBulkPreview(null);
      setRecordError(message);
      toast.error(message);
    }
  }

  async function onSubmit(values: EventValues) {
    await eventSubmission.run(() => submitEvent(values));
  }

  // The header and page structure stay mounted while the permission set
  // settles — a page that collapses to a bare "Loading…" line reads as a
  // broken app on slow rural connections.
  if (permsLoading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Health"
          description="Vaccinations, deworming and treatments across the herd."
        />
        <PageSkeleton cards={2} />
      </div>
    );
  }
  if (permsError) {
    return (
      <PermissionsError onRetry={() => void permsRefetch()} />
    );
  }
  if (!allowed) {
    return <p className="text-muted-foreground">You don&apos;t have access to this page.</p>;
  }
  if (eventsQuery.isLoading || !eventPayload) {
    if (eventsQuery.isError) {
      return (
        <div role="alert" className="space-y-3 rounded-lg border border-destructive/40 p-4">
          <p className="text-sm text-destructive">
            {eventsQuery.error instanceof ApiError
              ? eventsQuery.error.detail
              : "Could not load health events."}
          </p>
          <Button type="button" variant="outline" onClick={() => void eventsQuery.refetch()}>
            Retry health events
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-6">
        <PageHeader
          title="Health"
          description="Vaccinations, deworming and treatments across the herd."
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading health events…</span>
          {/* Mirrors the mounted page: the schedule picker card above the
           * event-log table region. */}
          <div className="space-y-6">
            <CardSkeleton lines={2} />
            <TableSkeleton rows={6} columns={6} />
          </div>
        </div>
      </div>
    );
  }

  const events = eventPayload.events;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Health"
        description="Vaccinations, deworming and treatments across the herd."
        actions={
          canManage && (
            <Button
              onClick={() => {
                resetEventForm();
                setRecordError(null);
                setOpen(true);
              }}
            >
              <Plus aria-hidden="true" /> Add event
            </Button>
          )
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CalendarClock className="size-4 text-primary" />
            Vaccination schedule per animal
          </CardTitle>
          <CardDescription>
            Pick an animal to see due dates and boosters from the vaccination templates.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-2">
          <div className="space-y-1.5">
            <Label htmlFor="schedule-animal">View schedule for</Label>
            <HealthAnimalPicker
              id="schedule-animal"
              value={scheduleAnimalId}
              onValueChange={setScheduleAnimalId}
              placeholder="Pick an animal"
              dialogTitle="Choose an animal schedule"
              className="w-64"
            />
          </div>
          <Button
            variant="outline"
            disabled={!scheduleAnimalId}
            onClick={() =>
              router.push(
                withReturnTo(
                  `/health/schedule/${scheduleAnimalId}`,
                  `/health?schedule_animal_id=${encodeURIComponent(scheduleAnimalId)}`,
                ),
              )
            }
          >
            View
          </Button>
        </CardContent>
      </Card>

      <DataTableCard
        title="Event log"
        description="Every recorded health event, newest scope first."
      >
        {events.length === 0 ? (
          <EmptyState
            icon={Syringe}
            title="No health events recorded yet."
            description="Recorded vaccinations, deworming and treatments will appear here."
          >
            {canManage && (
              <Link href="/health/new" className={buttonVariants({ size: "sm" })}>
                Add health event
              </Link>
            )}
          </EmptyState>
        ) : (
          <>
            {/* Below md the 10-column log becomes a card per event — panning
             * a 900px table inside a 390px phone is not a log, it's a scroll
             * toy. Cards carry the key fields (date, type, animal, next due). */}
            <div className="space-y-2 md:hidden">
              {events.map((e) => (
                <div key={e.id} className="rounded-xl border bg-card p-3">
                  <div className="flex items-center justify-between gap-2">
                    <StatusBadge status={e.type}>{enumLabel("eventType", e.type)}</StatusBadge>
                    <span className="text-xs text-muted-foreground">{formatDate(e.date)}</span>
                  </div>
                  <p className="mt-1 text-sm font-medium">
                    <EventAnimalLabel event={e} canViewAnimals={canViewAnimals} />
                  </p>
                  {e.product_name && (
                    <p className="mt-0.5 text-xs text-muted-foreground">{e.product_name}</p>
                  )}
                  <p className="mt-1 text-xs text-muted-foreground">
                    {e.next_due_date ? (
                      <>
                        Next due: <NextDue date={e.next_due_date} />
                        {e.schedule_template_name && (
                          <span className="block">
                            {e.schedule_template_name}
                            {e.next_due_authority ? ` · ${e.next_due_authority}` : ""}
                          </span>
                        )}
                      </>
                    ) : (
                      "No next due date"
                    )}
                  </p>
                  {e.suspected_scheduled_disease && (
                    <Badge variant="destructive" className="mt-1">
                      Scheduled disease suspected
                    </Badge>
                  )}
                </div>
              ))}
            </div>
            <div className="hidden md:block">
            <Table className="min-w-[900px]">
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Animal</TableHead>
                <TableHead>Product</TableHead>
                <TableHead>Target</TableHead>
                <TableHead>Dose</TableHead>
                <TableHead>Route</TableHead>
                <TableHead className="text-right">Cost</TableHead>
                <TableHead>Next due</TableHead>
                <TableHead>Traceability & holds</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.map((e) => (
                <TableRow key={e.id}>
                  <TableCell>{formatDate(e.date)}</TableCell>
                  <TableCell>
                    <StatusBadge status={e.type}>{enumLabel("eventType", e.type)}</StatusBadge>
                  </TableCell>
                  <TableCell>
                    <EventAnimalLabel event={e} canViewAnimals={canViewAnimals} />
                  </TableCell>
                  <TableCell>{e.product_name ?? "—"}</TableCell>
                  <TableCell>{e.disease_target ?? "—"}</TableCell>
                  <TableCell>{e.dose ?? "—"}</TableCell>
                  <TableCell>{e.route ?? "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatMoney(e.cost)}</TableCell>
                  <TableCell>
                    {e.next_due_date ? (
                      <span>
                        <NextDue date={e.next_due_date} />
                        {e.schedule_template_name && (
                          <span className="mt-1 block text-xs text-muted-foreground">
                            {e.schedule_template_name}
                            {e.next_due_authority ? ` · ${e.next_due_authority}` : ""}
                          </span>
                        )}
                      </span>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="max-w-48">
                    {e.notes ? (
                      <span title={e.notes} className="block truncate">{e.notes}</span>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="min-w-48 space-y-1 text-xs">
                      {e.suspected_scheduled_disease && (
                        <Badge variant="destructive">Scheduled disease suspected</Badge>
                      )}
                      {e.product_lot && <p>Lot: {e.product_lot}</p>}
                      {e.product_manufactured_on && (
                        <p>Manufactured: {formatDate(e.product_manufactured_on)}</p>
                      )}
                      {e.product_expires_on && (
                        <p>Expires: {formatDate(e.product_expires_on)}</p>
                      )}
                      {e.vaccine_valid_until && (
                        <p>Vaccine valid until: {formatDate(e.vaccine_valid_until)}</p>
                      )}
                      {e.certificate_number && <p>Certificate: {e.certificate_number}</p>}
                      {e.official_tag_number && <p>Official tag: {e.official_tag_number}</p>}
                      {e.administered_by && <p>Administered by: {e.administered_by}</p>}
                      {e.withdrawal_until && (
                        <p>Withdrawal until: {formatDate(e.withdrawal_until)}</p>
                      )}
                      {e.authority_notified_at && (
                        <p>Authority notified: {formatDate(e.authority_notified_at)}</p>
                      )}
                      {e.isolation_started_at && (
                        <p>Isolation started: {formatDate(e.isolation_started_at)}</p>
                      )}
                      {!e.suspected_scheduled_disease &&
                        !e.product_lot &&
                        !e.certificate_number &&
                        !e.official_tag_number &&
                        !e.administered_by &&
                        !e.withdrawal_until &&
                        !e.product_manufactured_on &&
                        !e.product_expires_on &&
                        !e.vaccine_valid_until &&
                        !e.authority_notified_at &&
                        !e.isolation_started_at && <span>—</span>}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
            </div>
          </>
        )}
        {eventsSettling && (
          <p role="status" className="pt-3 text-sm text-muted-foreground">
            Updating health events…
          </p>
        )}
        <PaginationControls
          total={eventPayload.total}
          limit={eventPayload.limit}
          offset={eventPayload.offset}
          onOffsetChange={setEventOffset}
          label="health events"
          disabled={eventsSettling}
        />
      </DataTableCard>

      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) resetEventForm();
          setOpen(nextOpen);
          if (!nextOpen) closeDeepLinkedDialog();
        }}
      >
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Add health event</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={handleSubmit(onSubmit, revealCollapsedErrors)}
            noValidate
          >
            {/* Bulk preview is intentionally editable and guarded by an
                epoch/selection check. Freeze only the durable record write,
                whose payload must match the values the operator still sees. */}
            <fieldset disabled={recordMutation.isPending} className="space-y-4">
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Apply to</legend>
              <Controller
                control={control}
                name="scope"
                render={({ field }) => (
                  <div className="flex flex-col gap-2 sm:flex-row sm:gap-4">
                    {(
                      [
                        ["animal", "Single animal"],
                        ["bucket", "Whole bucket"],
                        ["batch", "Purchase batch"],
                      ] as const
                    ).map(([value, label]) => (
                      <Label key={value} className="flex items-center gap-1.5 font-normal">
                        <input
                          type="radio"
                          name={field.name}
                          value={value}
                          checked={field.value === value}
                          onBlur={field.onBlur}
                          onChange={() => {
                            field.onChange(value);
                            changeScope(value);
                          }}
                        />
                        {label}
                      </Label>
                    ))}
                  </div>
                )}
              />
              {scope === "animal" && (
                <div className="space-y-1.5">
                  <Label htmlFor="event-animal">Animal *</Label>
                  <HealthAnimalPicker
                    id="event-animal"
                    value={wAnimalId || ""}
                    onValueChange={(v) => {
                      // RemotePicker fires even when the already-selected row
                      // is re-clicked to confirm it; only an actual change may
                      // unlink the duty and its prefills.
                      if (v === (wAnimalId || "")) return;
                      clearLinkedTaskPrefill();
                      setRecordError(null);
                      setValue("animal_id", v, { shouldValidate: true });
                    }}
                    placeholder="Pick an animal"
                    dialogTitle="Choose an animal for this health event"
                    aria-invalid={Boolean(errors.animal_id) || undefined}
                    aria-describedby={errors.animal_id ? "event-animal-error" : undefined}
                  />
                  <FieldError id="event-animal-error" message={errors.animal_id?.message} />
                </div>
              )}
              {scope === "bucket" && (
                <div className="space-y-1.5">
                  <Label htmlFor="event-bucket">Bucket *</Label>
                  <Select
                    value={wBucket || ""}
                    items={bucketItems}
                    onValueChange={(v) => {
                      setBulkPreview(null);
                      setRecordError(null);
                      setValue("bucket", v, { shouldValidate: true });
                    }}
                  >
                    <SelectTrigger
                      id="event-bucket"
                      className="w-full"
                      aria-invalid={Boolean(errors.bucket) || undefined}
                      aria-describedby={errors.bucket ? "event-bucket-error" : undefined}
                    >
                      <SelectValue placeholder="Pick a bucket" />
                    </SelectTrigger>
                    <SelectContent>
                      {BUCKETS.map((b) => (
                        <SelectItem key={b} value={b}>
                          {enumLabel("bucket", b, farmType)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FieldError id="event-bucket-error" message={errors.bucket?.message} />
                </div>
              )}
              {scope === "batch" && (
                <div className="space-y-1.5">
                  <Label htmlFor="event-batch">Purchase batch *</Label>
                  <HealthPurchaseBatchPicker
                    id="event-batch"
                    value={wPurchaseBatchId || ""}
                    onValueChange={(v) => {
                      // Same-value confirmation must not unlink the duty.
                      if (v === (wPurchaseBatchId || "")) return;
                      clearLinkedTaskPrefill();
                      setRecordError(null);
                      setValue("purchase_batch_id", v, { shouldValidate: true });
                    }}
                    placeholder="Pick a batch"
                    dialogTitle="Choose a purchase batch for this health event"
                    aria-invalid={Boolean(errors.purchase_batch_id) || undefined}
                    aria-describedby={errors.purchase_batch_id ? "event-batch-error" : undefined}
                  />
                  <FieldError id="event-batch-error" message={errors.purchase_batch_id?.message} />
                </div>
              )}
            </fieldset>

            {scope !== "animal" && bulkPreview && (
              <div
                role="status"
                className="rounded-lg border border-warning/40 bg-warning-tint/50 p-3 text-sm text-warning-tint-foreground"
              >
                <p className="font-medium">
                  Reviewed target snapshot: {bulkPreview.target_count} active animal
                  {bulkPreview.target_count === 1 ? "" : "s"}
                </p>
                <p className="mt-1 text-xs">
                  Confirming records only the reviewed IDs. An animal that joins an unlinked scope
                  afterward is not silently added; if a reviewed animal leaves the scope (or a
                  linked batch no longer matches exactly), the server rejects the write and
                  requires a fresh review.
                </p>
                {bulkPreview.target_animals.length > 0 ? (
                  <ul
                    aria-label="Reviewed target animals"
                    className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded border border-warning/30 bg-background/60 px-2 py-1.5 text-xs"
                  >
                    {bulkPreview.target_animals.map((animal) => (
                      <li key={animal.id}>
                        <span className="font-medium">{animal.tag_number}</span>
                        {animal.name ? ` · ${animal.name}` : ""}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState
                    icon={SearchX}
                    title="No active animals are in this reviewed target."
                    description="Pick a different target above — recording this one would save nothing."
                    className="mt-2 py-8"
                  />
                )}
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-medium">
                    Review exact animal IDs
                  </summary>
                  {bulkPreview.target_animal_ids.length > 0 ? (
                    <p className="mt-1 break-words font-mono text-xs">
                      {bulkPreview.target_animal_ids.join(", ")}
                    </p>
                  ) : (
                    <EmptyState
                      icon={SearchX}
                      title="No active animal IDs were returned."
                      description="The reviewed snapshot is empty — choose a different target above."
                      className="mt-1 py-8"
                    />
                  )}
                </details>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="date">Date (defaults to today)</Label>
                <Input
                  id="date"
                  type="date"
                  max={localToday()}
                  aria-invalid={Boolean(errors.date) || undefined}
                  aria-describedby={errors.date ? "event-date-error" : undefined}
                  {...register("date")}
                />
                <FieldError id="event-date-error" message={errors.date?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="event-type">Type</Label>
                <Select
                  value={wType}
                  onValueChange={(v) =>
                    setValue("type", v as EventValues["type"], { shouldValidate: true })
                  }
                  items={EVENT_TYPE_ITEMS}
                >
                  <SelectTrigger id="event-type" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {EVENT_TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {enumLabel("eventType", t)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="product_name">Product name</Label>
                <Input
                  id="product_name"
                  maxLength={120}
                  placeholder="e.g. PPR vaccine"
                  aria-invalid={Boolean(errors.product_name) || undefined}
                  aria-describedby={errors.product_name ? "product-name-error" : undefined}
                  {...register("product_name")}
                />
                <FieldError id="product-name-error" message={errors.product_name?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="disease_target">Disease target</Label>
                <Input
                  id="disease_target"
                  maxLength={120}
                  aria-invalid={Boolean(errors.disease_target) || undefined}
                  aria-describedby={errors.disease_target ? "disease-target-error" : undefined}
                  {...register("disease_target")}
                />
                <FieldError id="disease-target-error" message={errors.disease_target?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="dose">Dose</Label>
                <Input
                  id="dose"
                  maxLength={60}
                  placeholder="e.g. 1 ml"
                  aria-invalid={Boolean(errors.dose) || undefined}
                  aria-describedby={errors.dose ? "event-dose-error" : undefined}
                  {...register("dose")}
                />
                <FieldError id="event-dose-error" message={errors.dose?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="event-route">Route</Label>
                <Select
                  value={wRoute || NONE}
                  onValueChange={(v) => setValue("route", v)}
                  items={ROUTE_ITEMS}
                >
                  <SelectTrigger id="event-route" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>—</SelectItem>
                    {ROUTES.map((r) => (
                      <SelectItem key={r} value={r}>
                        {r}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="vet_name">Vet</Label>
                <Input
                  id="vet_name"
                  maxLength={120}
                  aria-invalid={Boolean(errors.vet_name) || undefined}
                  aria-describedby={errors.vet_name ? "event-vet-error" : undefined}
                  {...register("vet_name")}
                />
                <FieldError id="event-vet-error" message={errors.vet_name?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="cost">Total cost (₹, split evenly)</Label>
                <Input
                  id="cost"
                  inputMode="decimal"
                  placeholder="0.00"
                  aria-invalid={Boolean(errors.cost) || undefined}
                  aria-describedby={errors.cost ? "event-cost-error" : undefined}
                  {...register("cost")}
                />
                <FieldError id="event-cost-error" message={errors.cost?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="next_due_date">Next due date</Label>
                <Input
                  id="next_due_date"
                  type="date"
                  aria-invalid={Boolean(errors.next_due_date) || undefined}
                  aria-describedby={errors.next_due_date ? "next-due-date-error" : undefined}
                  {...register("next_due_date")}
                />
                {errors.next_due_date && (
                  <p id="next-due-date-error" role="alert" className="text-sm text-destructive">
                    {errors.next_due_date.message}
                  </p>
                )}
              </div>
              {canViewTasks && tasksQuery.isLoading && (
                <InlineLoading>Loading linked duties…</InlineLoading>
              )}
              {canViewTasks && tasksQuery.isError && (
                <div
                  role="alert"
                  className="space-y-2 rounded-lg border border-destructive/40 p-3 text-sm"
                >
                  <p className="text-destructive">
                    {tasksQuery.error instanceof ApiError
                      ? tasksQuery.error.detail
                      : "Could not load linked duties."}
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void tasksQuery.refetch()}
                  >
                    Retry linked duties
                  </Button>
                </div>
              )}
              {unresolvedPrefillTask && (
                <p role="alert" className="text-sm text-destructive sm:col-span-2">
                  Could not link duty #{unresolvedPrefillTask} — it is unavailable or no
                  longer a pending health duty. Record the event without it.
                </p>
              )}
              {(() => {
                const linkedTask = linkableHealthTasks.find(
                  (t) => String(t.id) === wTaskId,
                );
                if (!linkedTask || linkedTask.due_date <= localToday()) return null;
                return (
                  <p role="status" className="sm:col-span-2 text-sm text-muted-foreground">
                    Duty #{linkedTask.id} is not due until {formatDate(linkedTask.due_date)} — the
                    server rejects an event dated before then.
                  </p>
                );
              })()}
              {canViewTasks && linkableHealthTasks.length > 0 && (
                <div className="space-y-1.5">
                  <Label htmlFor="event-task">Linked duty (completes it)</Label>
                  <Select
                    value={wTaskId || NONE}
                    onValueChange={(v) => applyTask(v)}
                    items={taskItems}
                  >
                    <SelectTrigger id="event-task" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE}>— none —</SelectItem>
                      {linkableHealthTasks.map((t) => (
                        <SelectItem key={t.id} value={String(t.id)}>
                          {t.title} (due {formatDate(t.due_date)})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>
            <details
              className="rounded-lg border p-3"
              open={advancedOpen}
              onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}
            >
              <summary className="cursor-pointer text-sm font-medium">
                Advanced traceability & compliance
              </summary>
              <p className="mt-2 text-xs text-muted-foreground">
                Record the product trail, authorised schedule, statutory notification and
                movement/withdrawal holds when they apply.
              </p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="schedule_template_name">Schedule/template name</Label>
                  {/* A vaccine/deworming event is only accepted with an exact
                      seeded programme name, and no other screen reveals those
                      names — free text here 422'd every time. Offer the list
                      the API will actually accept, and keep free text for the
                      types (treatment/footbath/vitamin) that allow it. */}
                  {templateIsSeeded ? (
                    <Controller
                      control={control}
                      name="schedule_template_name"
                      render={({ field }) => (
                        <Select
                          value={field.value ?? ""}
                          onValueChange={field.onChange}
                          disabled={templateOptions.length === 0}
                        >
                          <SelectTrigger
                            id="schedule_template_name"
                            aria-invalid={Boolean(errors.schedule_template_name) || undefined}
                            aria-describedby={
                              errors.schedule_template_name ? "schedule-template-error" : undefined
                            }
                          >
                            <SelectValue placeholder="Select a seeded programme" />
                          </SelectTrigger>
                          <SelectContent>
                            {templateOptions.map((template) => (
                              <SelectItem key={template.id} value={template.name}>
                                {template.timing_note
                                  ? `${template.name} — ${template.timing_note}`
                                  : template.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    />
                  ) : (
                    <Input
                      id="schedule_template_name"
                      maxLength={120}
                      aria-invalid={Boolean(errors.schedule_template_name) || undefined}
                      aria-describedby={errors.schedule_template_name ? "schedule-template-error" : undefined}
                      {...register("schedule_template_name")}
                    />
                  )}
                  {templateIsSeeded && scheduleTemplates.isLoading && (
                    <InlineLoading>Loading programmes…</InlineLoading>
                  )}
                  {errors.schedule_template_name && (
                    <p id="schedule-template-error" role="alert" className="text-sm text-destructive">
                      {errors.schedule_template_name.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="next_due_authority">Next-due authority</Label>
                  <Input
                    id="next_due_authority"
                    maxLength={120}
                    placeholder="e.g. veterinarian prescription / official schedule"
                    aria-invalid={Boolean(errors.next_due_authority) || undefined}
                    aria-describedby={errors.next_due_authority ? "next-due-authority-error" : undefined}
                    {...register("next_due_authority")}
                  />
                  {errors.next_due_authority && (
                    <p id="next-due-authority-error" role="alert" className="text-sm text-destructive">
                      {errors.next_due_authority.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="product_lot">Product lot/batch</Label>
                  <Input id="product_lot" maxLength={120} {...register("product_lot")} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="administered_by">Administered by</Label>
                  <Input id="administered_by" maxLength={120} {...register("administered_by")} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="product_manufactured_on">Product manufactured</Label>
                  <Input
                    id="product_manufactured_on"
                    type="date"
                    max={localToday()}
                    aria-invalid={Boolean(errors.product_manufactured_on) || undefined}
                    aria-describedby={errors.product_manufactured_on ? "product-manufactured-error" : undefined}
                    {...register("product_manufactured_on")}
                  />
                  {errors.product_manufactured_on && (
                    <p id="product-manufactured-error" role="alert" className="text-sm text-destructive">
                      {errors.product_manufactured_on.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="product_expires_on">Product expires</Label>
                  <Input
                    id="product_expires_on"
                    type="date"
                    aria-invalid={Boolean(errors.product_expires_on) || undefined}
                    aria-describedby={errors.product_expires_on ? "product-expires-error" : undefined}
                    {...register("product_expires_on")}
                  />
                  {errors.product_expires_on && (
                    <p id="product-expires-error" role="alert" className="text-sm text-destructive">
                      {errors.product_expires_on.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="vaccine_valid_until">Vaccine valid until</Label>
                  <Input
                    id="vaccine_valid_until"
                    type="date"
                    aria-invalid={Boolean(errors.vaccine_valid_until) || undefined}
                    aria-describedby={errors.vaccine_valid_until ? "vaccine-valid-error" : undefined}
                    {...register("vaccine_valid_until")}
                  />
                  {errors.vaccine_valid_until && (
                    <p id="vaccine-valid-error" role="alert" className="text-sm text-destructive">
                      {errors.vaccine_valid_until.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="withdrawal_until">Withdrawal until</Label>
                  <Input
                    id="withdrawal_until"
                    type="date"
                    aria-invalid={Boolean(errors.withdrawal_until) || undefined}
                    aria-describedby={errors.withdrawal_until ? "withdrawal-until-error" : undefined}
                    {...register("withdrawal_until")}
                  />
                  {errors.withdrawal_until && (
                    <p id="withdrawal-until-error" role="alert" className="text-sm text-destructive">
                      {errors.withdrawal_until.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="certificate_number">Certificate number</Label>
                  <Input id="certificate_number" maxLength={120} {...register("certificate_number")} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="official_tag_number">Official tag number</Label>
                  <Input id="official_tag_number" maxLength={80} {...register("official_tag_number")} />
                </div>
                <div className="flex items-center gap-2 sm:col-span-2">
                  <Controller
                    control={control}
                    name="suspected_scheduled_disease"
                    render={({ field }) => (
                      <Checkbox
                        id="suspected_scheduled_disease"
                        checked={field.value}
                        onCheckedChange={(checked) => {
                          const selected = checked === true;
                          if (!selected) {
                            unregister("authority_notified_at");
                            unregister("isolation_started_at");
                            setValue("authority_notified_at", "");
                            setValue("isolation_started_at", "");
                          }
                          field.onChange(selected);
                        }}
                      />
                    )}
                  />
                  <Label htmlFor="suspected_scheduled_disease" className="font-normal">
                    Suspected scheduled/notifiable disease — apply movement restriction
                  </Label>
                </div>
                {suspectedScheduledDisease && (
                  <>
                    <div className="space-y-1.5">
                      <Label htmlFor="authority_notified_at">Authority notified date</Label>
                      <Input
                        id="authority_notified_at"
                        type="date"
                        max={localToday()}
                        aria-invalid={Boolean(errors.authority_notified_at) || undefined}
                        aria-describedby={errors.authority_notified_at ? "authority-notified-error" : undefined}
                        {...register("authority_notified_at")}
                      />
                      {errors.authority_notified_at && (
                        <p id="authority-notified-error" role="alert" className="text-sm text-destructive">
                          {errors.authority_notified_at.message}
                        </p>
                      )}
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="isolation_started_at">Isolation started date</Label>
                      <Input
                        id="isolation_started_at"
                        type="date"
                        max={localToday()}
                        aria-invalid={Boolean(errors.isolation_started_at) || undefined}
                        aria-describedby={errors.isolation_started_at ? "isolation-started-error" : undefined}
                        {...register("isolation_started_at")}
                      />
                      {errors.isolation_started_at && (
                        <p id="isolation-started-error" role="alert" className="text-sm text-destructive">
                          {errors.isolation_started_at.message}
                        </p>
                      )}
                    </div>
                  </>
                )}
              </div>
            </details>
            <div className="space-y-1.5">
              <Label htmlFor="notes">Notes</Label>
              <Textarea
                id="notes"
                rows={2}
                maxLength={MAX_HEALTH_EVENT_NOTES}
                aria-invalid={Boolean(errors.notes) || undefined}
                aria-describedby={errors.notes ? "health-notes-error" : undefined}
                {...register("notes")}
              />
              {errors.notes && (
                <p id="health-notes-error" role="alert" className="text-sm text-destructive">
                  {errors.notes.message}
                </p>
              )}
            </div>
            {recordError && (
              <p role="alert" className="text-sm text-destructive">
                {recordError} Check the event details, then try again.
              </p>
            )}
            <DialogFooter>
              <Button
                type="submit"
                disabled={
                  isSubmitting || eventSubmission.pending || previewMutation.isPending
                }
              >
                {/* Branch on the mutation actually in flight: a single-animal
                    event never previews, so keying this off bulkPreview
                    announced "Reviewing targets…" while the immutable event
                    was being written. */}
                {previewMutation.isPending
                  ? "Reviewing targets…"
                  : isSubmitting || eventSubmission.pending
                    ? "Saving…"
                    : scope !== "animal" && !bulkPreview
                      ? "Review target animals"
                      : scope !== "animal"
                        ? `Confirm for ${bulkPreview?.target_count ?? 0} ${
                            (bulkPreview?.target_count ?? 0) === 1 ? "animal" : "animals"
                          }`
                        : recordError
                          ? "Retry save"
                          : "Save event"}
              </Button>
            </DialogFooter>
            </fieldset>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function HealthPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6" role="status" aria-live="polite">
          <PageHeader
            title="Health"
            description="Vaccinations, deworming and treatments across the herd."
          />
          <span className="sr-only">Loading health events…</span>
          <PageSkeleton cards={2} />
        </div>
      }
    >
      <HealthPageContent />
    </Suspense>
  );
}
