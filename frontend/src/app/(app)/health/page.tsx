"use client";

/** Health event log + record-event dialog — parity with v1's health/list.html + health/new.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Plus, SearchX, Syringe } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  Controller,
  useForm,
  useWatch,
  type FieldErrors,
  type FieldPath,
} from "react-hook-form";
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
  HealthEventInRoute,
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
import { enumLabel } from "@/lib/enum-labels";
import { translate, useLanguage, useT, type TFn } from "@/lib/i18n";
import { resolveTaskTitle } from "@/lib/task-title";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { StaleDataNotice } from "@/components/stale-data-notice";
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
import {
  ApiError,
  applyApiValidationToForm,
  } from "@/lib/api-client";
import { addDays, farmToday, formatDate, formatMoney } from "@/lib/format";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { isPersistableNonnegativeMoney } from "@/lib/persisted-numbers";
import { permittedAppPath, withReturnTo } from "@/lib/permission-navigation";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { MAX_PAGE_OFFSET, useUrlState } from "@/lib/use-url-state";

import { taskPrefill } from "./task-prefill";

const EVENT_TYPES = Object.values(HealthEventInType);
const BUCKETS = Object.values(HealthEventInBucket);
/** Bounded administration-route vocabulary (AdministrationRoute): the wire
 * accepts only these canonical upper-case codes now. */
const ROUTES = Object.values(HealthEventInRoute);
const MAX_HEALTH_EVENT_COST = 1_000_000_000;
const MAX_HEALTH_EVENT_NOTES = 4_000;
/** Mirrors backend/app/models/constants.py. An immutable event with a
 * mistyped withdrawal year must not hold an animal out of sale indefinitely. */
import { MAX_WITHDRAWAL_DAYS as MAX_WITHDRAWAL_DAYS_CAP } from "@/lib/backend-caps";
const MAX_WITHDRAWAL_DAYS = MAX_WITHDRAWAL_DAYS_CAP;
/** Sentinel for "no selection" in optional selects (empty string is not a valid item value). */
const NONE = "none";
/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
const ROUTE_ITEMS: Record<string, string> = {
  [NONE]: "—",
  ...Object.fromEntries(ROUTES.map((r) => [r, r])),
};
/** value → label map for the event-type select, in the active language. */
const eventTypeItems = (language: "en" | "te"): Record<string, string> =>
  Object.fromEntries(EVENT_TYPES.map((t) => [t, enumLabel("eventType", t, language)]));


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

/** Exported for direct schema-level testing: the dialog's native date inputs
 * sanitize malformed values in the browser, so several guard branches are
 * unreachable through the UI alone. The messages resolve through the i18n
 * catalog, so the factory takes the caller's `t`; the exported `eventSchema`
 * below is the English instance the mounted page replaces with the active
 * language's. */
export function buildEventSchema(t: TFn) {
  return z
  .object({
    scope: z.enum(["animal", "bucket", "batch"]),
    animal_id: z.string().optional(),
    bucket: z.string().optional(),
    purchase_batch_id: z.string().optional(),
    date: z.string().optional(),
    type: z.enum([
      "VACCINE",
      "DEWORMING",
      "TREATMENT",
      "FOOTBATH",
      "VITAMIN",
      "EXAM",
      "FECAL_EXAM",
    ]),
    product_name: z.string().max(120).optional(),
    disease_target: z.string().max(120).optional(),
    dose: z.string().max(60).optional(),
    route: z.enum([NONE, ...ROUTES] as [string, ...string[]]).optional(),
    vet_name: z.string().max(120).optional(),
    // Stryker disable ConditionalExpression, StringLiteral: every blank-cost arm is equivalent — Number("") === 0 is finite, ≥ 0, persistable and ≤ MAX, so a blank passes all three refines with or without the === "" fast path (the payload maps a blank cost to null separately)
    cost: z
      .string()
      .refine(
        (s) => s === "" || (Number.isFinite(Number(s)) && Number(s) >= 0),
        t("health.validation.costNonnegative"),
      )
      .refine(
        (s) => s === "" || isPersistableNonnegativeMoney(Number(s)),
        t("health.validation.moneyMin"),
      )
      .refine(
        (s) => s === "" || Number(s) <= MAX_HEALTH_EVENT_COST,
        t("health.validation.costTooLarge"),
      )
      .optional(),
    // Stryker restore ConditionalExpression, StringLiteral
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
      .max(MAX_HEALTH_EVENT_NOTES, t("health.validation.notesTooLong", { max: MAX_HEALTH_EVENT_NOTES }))
      .optional(),
    task_id: z.string().optional(),
  })
  .superRefine((v, ctx) => {
    // A blank event date has a defined API meaning: the active farm's today.
    // Validate every dependent date against that effective value rather than
    // letting blank-date submissions pass here and fail at the endpoint.
    const eventDate = v.date || farmToday();
    if (v.scope === "animal" && positiveIdString(v.animal_id) === null) {
      ctx.addIssue({ code: "custom", path: ["animal_id"], message: t("health.validation.pickAnimal") });
    }
    if (v.scope === "bucket" && !v.bucket) {
      ctx.addIssue({ code: "custom", path: ["bucket"], message: t("health.validation.pickBucket") });
    }
    if (v.scope === "batch" && positiveIdString(v.purchase_batch_id) === null) {
      ctx.addIssue({ code: "custom", path: ["purchase_batch_id"], message: t("health.validation.pickBatch") });
    }
    if (v.task_id && v.task_id !== NONE && positiveIdString(v.task_id) === null) {
      ctx.addIssue({ code: "custom", path: ["task_id"], message: t("health.validation.pickDuty") });
    }
    if (v.date && v.date > farmToday()) {
      ctx.addIssue({ code: "custom", path: ["date"], message: t("health.validation.dateFuture") });
    }
    if (v.next_due_date) {
      if (v.next_due_date <= eventDate) {
        ctx.addIssue({
          code: "custom",
          path: ["next_due_date"],
          message: t("health.validation.nextDueAfterEvent"),
        });
      }
      if (!v.schedule_template_name?.trim()) {
        ctx.addIssue({
          code: "custom",
          path: ["schedule_template_name"],
          message: t("health.validation.nameSchedule"),
        });
      }
      if (!v.next_due_authority?.trim()) {
        ctx.addIssue({
          code: "custom",
          path: ["next_due_authority"],
          message: t("health.validation.recordAuthority"),
        });
      }
    }
    if (v.product_manufactured_on && v.product_manufactured_on > farmToday()) {
      ctx.addIssue({
        code: "custom",
        path: ["product_manufactured_on"],
        message: t("health.validation.manufactureFuture"),
      });
    }
    if (v.product_manufactured_on && v.product_manufactured_on > eventDate) {
      ctx.addIssue({
        code: "custom",
        path: ["product_manufactured_on"],
        message: t("health.validation.manufactureAfterEvent"),
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
        message: t("health.validation.expiryBeforeManufacture"),
      });
    }
    if (v.product_expires_on && v.product_expires_on < eventDate) {
      ctx.addIssue({
        code: "custom",
        path: ["product_expires_on"],
        message: t("health.validation.productExpired"),
      });
    }
    if (v.vaccine_valid_until && v.vaccine_valid_until < eventDate) {
      ctx.addIssue({
        code: "custom",
        path: ["vaccine_valid_until"],
        message: t("health.validation.validityBeforeEvent"),
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
        message: t("health.validation.validityBeyondExpiry"),
      });
    }
    if (v.withdrawal_until) {
      if (v.withdrawal_until < eventDate) {
        ctx.addIssue({
          code: "custom",
          path: ["withdrawal_until"],
          message: t("health.validation.withdrawalBeforeEvent"),
        });
      } else if (v.withdrawal_until > addDays(eventDate, MAX_WITHDRAWAL_DAYS)) {
        ctx.addIssue({
          code: "custom",
          path: ["withdrawal_until"],
          message: t("health.validation.withdrawalTooLong", { days: MAX_WITHDRAWAL_DAYS }),
        });
      }
    }
    if (v.suspected_scheduled_disease && !v.disease_target?.trim()) {
      ctx.addIssue({
        code: "custom",
        path: ["disease_target"],
        message: t("health.validation.nameDisease"),
      });
    }
    for (const field of ["authority_notified_at", "isolation_started_at"] as const) {
      if (v[field] && v[field] > farmToday()) {
        ctx.addIssue({ code: "custom", path: [field], message: t("health.validation.dateFuture") });
      }
    }
  });
}

/** English instance kept for direct schema-level tests and for the 422 field
 *  mapper's key list (language-independent). The mounted page resolves the
 *  active language through buildEventSchema(t). */
export const eventSchema = buildEventSchema((key, vars) => translate("en", key, vars));
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
    date: farmToday(),
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

/** The record dialog's registered field names — the 422 mapper maps loc
 * tails against these and falls through anything else to the banner. */
const EVENT_FORM_FIELDS: readonly string[] = Object.keys(eventDefaults());

/** True when the field still holds exactly the prefilled hint (the operator
 *  did not edit it), so reverting the prefill may clear it. */
function holdsPrefill(current: string | undefined, prefilled: string | undefined): boolean {
  // Stryker disable next-line ConditionalExpression, StringLiteral: prefills only ever record defined hints, and even a hypothetical undefined hint fails the equality ((current ?? "") === undefined is always false), so both arms decline to clear; the ?? fallback exists only for TypeScript over the registered-string domain
  return prefilled !== undefined && (current ?? "") === prefilled;
}

function FieldError({ message, id }: { message?: string; id?: string }) {
  if (!message) return null;
  return <p id={id} role="alert" className="text-sm text-destructive">{message}</p>;
}

function HealthPageContent({ perms }: { perms: PermissionsState }) {
  const { can } = perms;
  const t = useT();
  const { language } = useLanguage();
  const allowed = can("health.view");
  const canManage = can("health.manage");
  const canViewAnimals = can("animals.view");
  const canViewTasks = can("tasks.view");
  /** value → label map for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value in the closed trigger. */
  const bucketItems: Record<string, string> = Object.fromEntries(
    BUCKETS.map((b) => [b, enumLabel("bucket", b, language)]),
  );
  const queryClient = useQueryClient();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchParamsKey = searchParams.toString();
  const returnTo = permittedAppPath(searchParams.get("returnTo"), can);
  const hasDeepLink = ["task_id", "animal_id", "purchase_batch_id"].some((key) =>
    searchParams.has(key),
  );
  // The event-log page lives in the URL (refresh and shared links keep the
  // page you were on); `offset` is dropped when it returns to the first page.
  const { getNumber, set: setUrlState } = useUrlState();
  // router.replace commits asynchronously, so a second page-turn click
  // before it lands would recompute "next" from the stale URL offset. The
  // pending value bridges that window; any other params change —
  // back/forward, a deep link — clears it so the URL wins again.
  const [pendingEventOffset, setPendingEventOffset] = useState<number | null>(null);
  const eventOffset = pendingEventOffset ?? getNumber("offset", 0, 0, MAX_PAGE_OFFSET);
  const setEventOffset = (next: number) => {
    setPendingEventOffset(next);
    setUrlState({ offset: next > 0 ? next : null });
  };
  const eventLimit = 50;
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPendingEventOffset(null);
  }, [searchParamsKey]);

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
  // A stale or hand-edited offset beyond the last page must not strand the
  // operator on a false "no events" screen — re-home it to the real last
  // page, exactly as the feeding ledger and the scenario pager do.
  useEffect(() => {
    const payload = eventsQuery.data?.status === 200 ? eventsQuery.data.data : undefined;
    // Stryker disable next-line ConditionalExpression: an offset of 0 always no-ops downstream anyway — total > 0 returns at the range guard, and total === 0 yields lastOffset 0 === eventOffset, blocked by the equality guard below
    if (!payload || eventOffset === 0) return;
    if (eventOffset < payload.total) return;
    const lastOffset =
      payload.total === 0
        ? 0
        : Math.floor((payload.total - 1) / eventLimit) * eventLimit;
    // Stryker disable next-line ConditionalExpression: the !== guard only skips a same-value setState that React bails out on without re-rendering or re-running this effect
    if (lastOffset !== eventOffset) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setEventOffset(lastOffset);
    }
    // setEventOffset is a stable closure over the two setters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventsQuery.data, eventOffset]);
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
          t.due_date <= farmToday(),
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
  // Stryker disable next-line BooleanLiteral: every path that opens the dialog (Add-event button, deep-link hydration) runs resetEventForm → setAdvancedOpen(false) before Radix mounts the <details>, so the initial state value never reaches the DOM
  const [advancedOpen, setAdvancedOpen] = useState(false);

  /** value → label map for the local task select. */
  const taskItems: Record<string, string> = {
    [NONE]: t("common.none"),
    ...Object.fromEntries(
      linkableHealthTasks.map((t) => [String(t.id), `${resolveTaskTitle(t, language)} (due ${formatDate(t.due_date)})`]),
    ),
  };

  // Rebuilt when the language changes so client-side validation messages
  // render in the active language; react-hook-form re-reads the resolver
  // option every render.
  const localizedSchema = useMemo(() => buildEventSchema(t), [t]);
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    setError,
    getValues,
    unregister,
    formState: { errors, isSubmitting },
  } = useForm<EventValues>({
    resolver: zodResolver(localizedSchema),
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
  // Stryker disable next-line BooleanLiteral: a permanently-true mounted flag only skips guards that React 18 already no-ops (setState after unmount)
  const mounted = useRef(true);

  // Stryker disable ArrayDeclaration: hook deps compare element-wise, and a constant junk list compares equal across renders — the effect still runs exactly once
  useEffect(() => {
    mounted.current = true;
    return () => {
      // Stryker disable next-line BooleanLiteral: same React 18 no-op argument — the flag's only consumers gate post-unmount writes
      mounted.current = false;
      // A late preview/write cannot own state or navigation after this page
      // has gone away. This also covers ordinary same-farm navigation; the
      // farm epoch below covers the smaller pre-unmount switch window.
      // Stryker disable next-line AssignmentOperator: a monotonically decreasing epoch counter mismatches a captured value exactly as reliably as an increasing one
    submissionEpoch.current += 1;
    };
  // Stryker restore ArrayDeclaration
  }, []);

  useEffect(() => {
    // The schedule picker is editable local state, so unrelated query changes
    // must not fight the user's selection. A real schedule-param navigation,
    // however (including Back/Forward while Next reuses this page), is a new
    // URL intent and must replace the old mount-time value.
    // Stryker disable next-line ConditionalExpression: requestedScheduleAnimalId derives from the same URL param (positiveIdString), so the effect can only re-run when that param changed — the compare-equal arm is unreachable
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
    // Stryker disable next-line AssignmentOperator: a monotonically decreasing epoch mismatches a captured value exactly as reliably as an increasing one
    submissionEpoch.current += 1;
    setAdvancedOpen(false);
    // A duty lookup still in flight belongs to the dialog session being torn
    // down. Left armed, it resolves into the NEXT, unrelated session and
    // silently rewrites scope/target/type over what the operator just entered.
    // Stryker disable next-line CallExpression: the resolver's task_id !== prefillTaskId arm consumes a late resolution in any fresh session (every reopen resets task_id to NONE), and a session that re-selects the same duty re-applies it through the select itself — where clearLinkedTaskPrefill first reverts, so user edits survive
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
    // Stryker disable next-line ConditionalExpression: with no applied prefill the call is a harmless no-op (prev is null, so it early-returns right after a task_id write that is overwritten three lines later)
    if (appliedPrefillRef.current) clearLinkedTaskPrefill();
    setValue("task_id", taskIdStr);
    const task = linkableHealthTasks.find((t) => String(t.id) === taskIdStr);
    // Stryker disable next-line ConditionalExpression: the local select only offers ids from this list and the deep-link resolver routes unknown ids to NONE, so the not-found arm is unreachable defense
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
    // Stryker disable next-line ConditionalExpression: pendingHealthTasks and exactTask are both pre-filtered to VACCINE/DEWORMING (the only categories that can appear in linkableHealthTasks), so this check is tautological here
    if (task.category === "VACCINE" || task.category === "DEWORMING") {
      applied.type = task.category;
      setValue("type", task.category);
      // Prefill product/disease from the duty title so the recorded event
      // matches the vaccination templates. Don't overwrite text
      // the user already typed.
      const hints = taskPrefill(task);
      // Stryker disable next-line StringLiteral: product_name is registered unconditionally, so getValues never yields undefined and the ?? "" fallback arm is dead
      if (hints.product_name && !(getValues("product_name") ?? "").trim()) {
        applied.product_name = hints.product_name;
        setValue("product_name", hints.product_name);
      }
      // Stryker disable next-line StringLiteral: disease_target is registered unconditionally, so getValues never yields undefined and the ?? "" fallback arm is dead
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
    // Stryker disable next-line ConditionalExpression: a manual scope change funnels through changeScope → this function, so by the time the operator can pick "— none —" any live prefill was already cleared — with prev intact the scope still equals prev.scope
    if (resetTaskScope && prev.scope && getValues("scope") === prev.scope) {
      // Stryker disable next-line BooleanLiteral: dropping the preserve flag would re-enter this function whose prev is already null — the same early return
      changeScope("animal", true);
    }
    if (prev.type && getValues("type") === prev.type) setValue("type", "VACCINE");
    if (holdsPrefill(getValues("product_name"), prev.product_name)) {
      setValue("product_name", "");
    }
    if (holdsPrefill(getValues("disease_target"), prev.disease_target)) {
      setValue("disease_target", "");
    }
  }

  function changeScope(nextScope: EventValues["scope"], preserveLinkedTask = false) {
    // Stryker disable next-line CallExpression: every path through this function reaches a second clear — the !preserveLinkedTask arm calls clearLinkedTaskPrefill (which clears first thing), and the preserve arm only runs from applyTask, which cleared before calling
    setBulkPreview(null);
    setRecordError(null);
    if (!preserveLinkedTask) {
      // A linked duty has an exact animal or purchase-batch scope. Keeping it
      // while the operator chooses another target makes the preview/write a
      // guaranteed 422 rather than a valid unlinked health event.
      clearLinkedTaskPrefill();
    }
    // Stryker disable next-line ObjectLiteral, BooleanLiteral: the scope control only ever sets valid enum values, so shouldValidate never surfaces a different error state
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
    // Stryker disable next-line CallExpression: every reopen path resets the scope to "animal", where the preview is never consulted, and any retarget clears it before a non-animal submit could read it
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
    // Stryker disable next-line StringLiteral: an internal dedupe signature only needs an injective separator; its exact text is never rendered or sent
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
    // Stryker disable next-line ConditionalExpression: with prefillTaskId null the remaining guards consume-and-return identically (task_id is never null so the mismatch arm fires once tabs load; before that the !tabs arm returns)
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
      // Stryker disable next-line ConditionalExpression: the guards above pin prefillTaskId === deepLinkedTaskIdValue (a non-null param string), and deepLinkedTaskId is its Number() — never null on this path
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
        // Stryker disable next-line ConditionalExpression: every retarget path (task select, scope radio, picker change) clears bulkPreview before the selection can differ from it, so a live preview never carries a different task than the selected one — the mismatch arm is unreachable defense
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
        // Stryker disable next-line UpdateOperator: a monotonically decreasing epoch mismatches a captured value exactly as reliably as an increasing one
    const epoch = ++submissionEpoch.current;
        const stillOwnsFarm = captureFarmScope();
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
            stillOwnsFarm() &&
            submissionEpoch.current === epoch &&
            // Stryker disable next-line ConditionalExpression: any scope retarget also unregisters/rewrites the target field, so the per-field comparison below rejects the stale preview on exactly the same inputs — the scope arm cannot differ alone
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
            !stillOwnsFarm() ||
            submissionEpoch.current !== epoch
          ) return;
          const message =
            err instanceof ApiError ? err.detail : t("health.toast.reviewFailed");
          setRecordError(message);
          toast.error(message);
        }
        return;
      }
      reviewedAnimalIds = bulkPreview.target_animal_ids;
    }
    // Stryker disable ConditionalExpression, LogicalOperator: the optional-id ternaries' swapped variants land on the null arm or produce NaN (which JSON serializes to null — the same wire value), and real selections are pinned by the payload campaign tests
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
      // Stryker disable OptionalChaining: every one of these inputs is registered unconditionally, so the values are strings, never undefined
      product_name: values.product_name?.trim() || null,
      disease_target: values.disease_target?.trim() || null,
      dose: values.dose?.trim() || null,
      route: values.route && values.route !== NONE ? (values.route as HealthEventInRoute) : null,
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
      // Stryker restore OptionalChaining
      task_id: values.task_id && values.task_id !== NONE ? Number(values.task_id) : null,
      expected_animal_ids: reviewedAnimalIds,
    };
    setRecordError(null);
    // Stryker disable next-line UpdateOperator: a monotonically decreasing epoch mismatches a captured value exactly as reliably as an increasing one
    const epoch = ++submissionEpoch.current;
    const stillOwnsFarm = captureFarmScope();
    try {
      const response = await recordMutation.mutateAsync({ data: payload });
      const recordedCount = response.status === 201 ? response.data.length : 0;
      // The request itself retained its original X-Farm-Id. If ownership has
      // since crossed a farm boundary, none of this old farm's completion may
      // affect the new farm's UI, cache, toast stream, or route.
      if (!stillOwnsFarm()) return;
      // The write happened, so confirm it and refresh the farm views
      // even if this dialog session is already over on the SAME farm.
      toast.success(
        values.scope === "animal"
          ? t("health.toast.recorded")
          : recordedCount === 1
            ? t("health.toast.recordedForOne", { count: recordedCount })
            : t("health.toast.recordedForMany", { count: recordedCount }),
      );
      invalidateFarmData(queryClient);
      // Beyond this point everything mutates dialog state. If the operator
      // dismissed this dialog and opened a new one, closing and resetting now
      // would wipe what they have since typed, and the navigation below would
      // be a second one on top of the dismissal's own.
      if (!mounted.current || submissionEpoch.current !== epoch) return;
      setOpen(false);
      // Stryker disable next-line CallExpression: every retarget path (scope radio, task select, picker change) clears bulkPreview before a new session could act on it, and a fresh session's scope is "animal" where the preview is never consulted
      setBulkPreview(null);
      // Stryker disable next-line CallExpression: both Add-event and the deep-link hydration call resetEventForm before reopening, so the post-success reset only tightens an invisible closed-dialog window
      resetEventForm();
      if (returnTo) router.push(returnTo);
      else if (hasDeepLink) router.replace("/health");
    } catch (err) {
      if (
        !mounted.current ||
        !stillOwnsFarm() ||
        submissionEpoch.current !== epoch
      ) return;
      // Stryker disable next-line StringLiteral: a live preview only exists for non-animal scopes (every retarget clears it before the scope can return to animal), so the "animal" comparison never decides anything on this line
      if (values.scope !== "animal") setBulkPreview(null);
      // A 422's per-field issues land inline on their inputs (the aria
      // wiring already renders those); only issues that match no form field
      // degrade to the banner + toast. Same collapsed-section rule as the
      // client-side invalid-submit path: an inline error hidden inside the
      // Advanced <details> must force it open or the save looks dead.
      const mappedFields: string[] = [];
      const unmapped = applyApiValidationToForm(
        err,
        (field, message) => {
          mappedFields.push(field);
          // Stryker disable next-line StringLiteral: nothing reads the RHF error type — the message drives every rendering (finance/breeding precedent)
          // Stryker disable next-line StringLiteral: nothing reads the RHF error type — the message drives every rendering (finance/breeding precedent)
          setError(field as FieldPath<EventValues>, { type: "server", message });
        },
        EVENT_FORM_FIELDS,
      );
      if (
        mappedFields.some((field) =>
          (ADVANCED_COMPLIANCE_FIELDS as readonly string[]).includes(field),
        )
      ) {
        setAdvancedOpen(true);
      }
      if (mappedFields.length === 0) {
        const message =
          err instanceof ApiError ? err.detail : t("health.toast.saveFailed");
        setRecordError(message);
        toast.error(message);
      } else if (unmapped.length > 0) {
        const message = unmapped.map((issue) => issue.msg).join("; ");
        setRecordError(message);
        toast.error(message);
      }
    }
  }

  async function onSubmit(values: EventValues) {
    await eventSubmission.run(() => submitEvent(values));
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
          title={t("health.page.title")}
          description={t("health.page.description")}
        />
        <div role="status" aria-live="polite">
          <span className="sr-only">{t("health.page.loading")}</span>
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

  // The confirm label only reads this when scope is non-animal AND a preview
  // is live; the hoist keeps that reachability explicit (an eager read with
  // optional chaining would mask a null dereference instead of pinning it).
  // Stryker disable next-line LogicalOperator, StringLiteral: the || variant only matters when scope is "animal" with a live preview — every scope change clears bulkPreview first, so that arm (and the "animal" spelling, which no other scope equals) is unreachable
  const previewCount = scope !== "animal" && bulkPreview !== null ? bulkPreview.target_count : 0;
  const events = eventPayload.events;

  return (
    <div className="space-y-6">
      {eventsQuery.isError && (
        <StaleDataNotice onRetry={() => void eventsQuery.refetch()} />
      )}
      <PageHeader
        title={t("health.page.title")}
        description={t("health.page.description")}
        actions={
          canManage && (
            <Button
              onClick={() => {
                resetEventForm();
                // Stryker disable next-line CallExpression: every dismissal path (onOpenChange → closeDeepLinkedDialog) already clears the banner, so the open-time clear is redundant
                setRecordError(null);
                setOpen(true);
              }}
            >
              <Plus aria-hidden="true" /> {t("health.addEvent")}
            </Button>
          )
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CalendarClock className="size-4 text-primary" />
            {t("health.schedule.cardTitle")}
          </CardTitle>
          <CardDescription>
            {t("health.schedule.cardDescription")}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-2">
          <div className="space-y-1.5">
            <Label htmlFor="schedule-animal">{t("health.schedule.viewFor")}</Label>
            <HealthAnimalPicker
              id="schedule-animal"
              value={scheduleAnimalId}
              onValueChange={setScheduleAnimalId}
              placeholder={t("health.form.animalPlaceholder")}
              dialogTitle={t("health.schedule.pickerTitle")}
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
            {t("health.schedule.view")}
          </Button>
        </CardContent>
      </Card>

      <DataTableCard
        title={t("health.log.title")}
        description={t("health.log.description")}
      >
        {events.length === 0 ? (
          <EmptyState
            icon={Syringe}
            title={t("health.log.emptyTitle")}
            description={t("health.log.emptyDescription")}
          >
            {canManage && (
              <Link href="/health/new" className={buttonVariants({ size: "sm" })}>
                {t("health.form.title")}
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
                    <StatusBadge status={e.type}>{enumLabel("eventType", e.type, language)}</StatusBadge>
                    <span className="text-xs text-muted-foreground">{formatDate(e.date)}</span>
                  </div>
                  <p className="mt-1 text-sm font-medium">
                    <EventAnimalLabel event={e} canViewAnimals={canViewAnimals} />
                  </p>
                  {e.product_name && (
                    <p className="mt-0.5 text-xs text-muted-foreground">{e.product_name}</p>
                  )}
                  {/* Food-safety fields must not be desktop-only: a sale
                   * withdrawal hold and the lot/expiry trail are what a phone
                   * user in the shed most needs to see. On a meat-goat farm
                   * the constraint is slaughter/sale, not milk. */}
                  {e.withdrawal_until && (
                    <Badge variant="destructive" className="mt-1">
                      {t("health.notForSaleUntil", { date: formatDate(e.withdrawal_until) })}
                    </Badge>
                  )}
                  {(e.product_lot || e.product_expires_on) && (
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {e.product_lot && <>{t("health.log.lot", { lot: e.product_lot })}</>}
                      {e.product_lot && e.product_expires_on && " · "}
                      {e.product_expires_on && (
                        <>{t("health.log.expires", { date: formatDate(e.product_expires_on) })}</>
                      )}
                    </p>
                  )}
                  {e.certificate_number && (
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {t("health.log.certificate", { number: e.certificate_number })}
                    </p>
                  )}
                  <p className="mt-1 text-xs text-muted-foreground">
                    {e.next_due_date ? (
                      <>
                        {t("health.table.nextDue")}: <NextDue date={e.next_due_date} />
                        {e.schedule_template_name && (
                          <span className="block">
                            {e.schedule_template_name}
                            {e.next_due_authority ? ` · ${e.next_due_authority}` : ""}
                          </span>
                        )}
                      </>
                    ) : (
                      t("health.log.noNextDue")
                    )}
                  </p>
                  {e.suspected_scheduled_disease && (
                    <Badge variant="destructive" className="mt-1">
                      {t("health.log.scheduledDiseaseSuspected")}
                    </Badge>
                  )}
                </div>
              ))}
            </div>
            <div className="hidden md:block">
            <Table className="min-w-[900px]">
            <TableHeader>
              <TableRow>
                <TableHead>{t("health.table.date")}</TableHead>
                <TableHead>{t("health.table.type")}</TableHead>
                <TableHead>{t("health.table.animal")}</TableHead>
                <TableHead>{t("health.table.product")}</TableHead>
                <TableHead>{t("health.table.target")}</TableHead>
                <TableHead>{t("health.table.dose")}</TableHead>
                <TableHead>{t("health.table.route")}</TableHead>
                <TableHead className="text-right">{t("health.table.cost")}</TableHead>
                <TableHead>{t("health.table.nextDue")}</TableHead>
                <TableHead>{t("health.table.notes")}</TableHead>
                <TableHead>{t("health.table.traceability")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.map((e) => (
                <TableRow key={e.id}>
                  <TableCell>{formatDate(e.date)}</TableCell>
                  <TableCell>
                    <StatusBadge status={e.type}>{enumLabel("eventType", e.type, language)}</StatusBadge>
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
                        <Badge variant="destructive">{t("health.log.scheduledDiseaseSuspected")}</Badge>
                      )}
                      {e.product_lot && <p>{t("health.log.lot", { lot: e.product_lot })}</p>}
                      {e.product_manufactured_on && (
                        <p>{t("health.log.manufactured", { date: formatDate(e.product_manufactured_on) })}</p>
                      )}
                      {e.product_expires_on && (
                        <p>{t("health.log.expires", { date: formatDate(e.product_expires_on) })}</p>
                      )}
                      {e.vaccine_valid_until && (
                        <p>{t("health.log.vaccineValidUntil", { date: formatDate(e.vaccine_valid_until) })}</p>
                      )}
                      {e.certificate_number && <p>{t("health.log.certificate", { number: e.certificate_number })}</p>}
                      {e.official_tag_number && <p>{t("health.log.officialTag", { tag: e.official_tag_number })}</p>}
                      {e.administered_by && <p>{t("health.log.administeredBy", { name: e.administered_by })}</p>}
                      {e.withdrawal_until && (
                        <p>{t("health.notForSaleUntil", { date: formatDate(e.withdrawal_until) })}</p>
                      )}
                      {e.authority_notified_at && (
                        <p>{t("health.log.authorityNotified", { date: formatDate(e.authority_notified_at) })}</p>
                      )}
                      {e.isolation_started_at && (
                        <p>{t("health.log.isolationStarted", { date: formatDate(e.isolation_started_at) })}</p>
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
            {t("health.log.updating")}
          </p>
        )}
        <PaginationControls
          total={eventPayload.total}
          limit={eventPayload.limit}
          offset={eventPayload.offset}
          onOffsetChange={setEventOffset}
          label={t("health.pagination.label")}
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
            <DialogTitle>{t("health.form.title")}</DialogTitle>
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
              <legend className="text-sm font-medium">{t("health.form.applyTo")}</legend>
              <Controller
                control={control}
                name="scope"
                render={({ field }) => (
                  <div className="flex flex-col gap-2 sm:flex-row sm:gap-4">
                    {(
                      [
                        ["animal", t("health.form.scopeAnimal")],
                        ["bucket", t("health.form.scopeBucket")],
                        ["batch", t("health.form.scopeBatch")],
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
                            // Stryker disable next-line CallExpression: changeScope's setValue("scope", value) writes the same form state one line later — the RHF onChange is redundant
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
                  <Label htmlFor="event-animal">{t("health.form.animalLabel")}</Label>
                  <HealthAnimalPicker
                    id="event-animal"
                    value={wAnimalId || ""}
                    onValueChange={(v) => {
                      // RemotePicker fires even when the already-selected row
                      // is re-clicked to confirm it; only an actual change may
                      // unlink the duty and its prefills.
                      // Stryker disable next-line StringLiteral: the picker rows carry animal ids only (no "none" option), so onValueChange never fires "" and the || "" arm is dead
                      if (v === (wAnimalId || "")) return;
                      clearLinkedTaskPrefill();
                      setRecordError(null);
                      setValue("animal_id", v, { shouldValidate: true });
                    }}
                    placeholder={t("health.form.animalPlaceholder")}
                    dialogTitle={t("health.form.animalPickerTitle")}
                    aria-invalid={Boolean(errors.animal_id) || undefined}
                    aria-describedby={errors.animal_id ? "event-animal-error" : undefined}
                  />
                  <FieldError id="event-animal-error" message={errors.animal_id?.message} />
                </div>
              )}
              {scope === "bucket" && (
                <div className="space-y-1.5">
                  <Label htmlFor="event-bucket">{t("health.form.bucketLabel")}</Label>
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
                      <SelectValue placeholder={t("health.form.bucketPlaceholder")} />
                    </SelectTrigger>
                    <SelectContent>
                      {BUCKETS.map((b) => (
                        <SelectItem key={b} value={b}>
                          {enumLabel("bucket", b, language)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FieldError id="event-bucket-error" message={errors.bucket?.message} />
                </div>
              )}
              {scope === "batch" && (
                <div className="space-y-1.5">
                  <Label htmlFor="event-batch">{t("health.form.batchLabel")}</Label>
                  <HealthPurchaseBatchPicker
                    id="event-batch"
                    value={wPurchaseBatchId || ""}
                    onValueChange={(v) => {
                      // Same-value confirmation must not unlink the duty.
                      // Stryker disable next-line StringLiteral: the picker rows carry batch ids only (no "none" option), so onValueChange never fires "" and the || "" arm is dead
                      if (v === (wPurchaseBatchId || "")) return;
                      clearLinkedTaskPrefill();
                      setRecordError(null);
                      setValue("purchase_batch_id", v, { shouldValidate: true });
                    }}
                    placeholder={t("health.form.batchPlaceholder")}
                    dialogTitle={t("health.form.batchPickerTitle")}
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
                  {bulkPreview.target_count === 1
                    ? t("health.form.reviewedSnapshotOne", { count: bulkPreview.target_count })
                    : t("health.form.reviewedSnapshotMany", { count: bulkPreview.target_count })}
                </p>
                <p className="mt-1 text-xs">
                  {t("health.form.reviewedExplainer")}
                </p>
                {bulkPreview.target_animals.length > 0 ? (
                  <ul
                    aria-label={t("health.form.reviewedListLabel")}
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
                    title={t("health.form.reviewedEmptyTitle")}
                    description={t("health.form.reviewedEmptyDescription")}
                    className="mt-2 py-8"
                  />
                )}
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-medium">
                    {t("health.form.reviewIds")}
                  </summary>
                  {bulkPreview.target_animal_ids.length > 0 ? (
                    <p className="mt-1 break-words font-mono text-xs">
                      {bulkPreview.target_animal_ids.join(", ")}
                    </p>
                  ) : (
                    <EmptyState
                      icon={SearchX}
                      title={t("health.form.reviewIdsEmptyTitle")}
                      description={t("health.form.reviewIdsEmptyDescription")}
                      className="mt-1 py-8"
                    />
                  )}
                </details>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="date">{t("health.form.dateLabel")}</Label>
                <Input
                  id="date"
                  type="date"
                  max={farmToday()}
                  aria-invalid={Boolean(errors.date) || undefined}
                  aria-describedby={errors.date ? "event-date-error" : undefined}
                  {...register("date")}
                />
                <FieldError id="event-date-error" message={errors.date?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="event-type">{t("health.form.typeLabel")}</Label>
                <Select
                  value={wType}
                  onValueChange={(v) => {
                    // Stryker disable next-line ObjectLiteral, BooleanLiteral: the select only ever sets valid enum values, so shouldValidate never surfaces a different error state (changeScope precedent)
                    setValue("type", v as EventValues["type"], { shouldValidate: true });
                  }}
                  items={eventTypeItems(language)}
                >
                  <SelectTrigger id="event-type" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {EVENT_TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {enumLabel("eventType", t, language)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="product_name">{t("health.form.productNameLabel")}</Label>
                <Input
                  id="product_name"
                  maxLength={120}
                  placeholder={t("health.form.productNamePlaceholder")}
                  aria-invalid={Boolean(errors.product_name) || undefined}
                  aria-describedby={errors.product_name ? "product-name-error" : undefined}
                  {...register("product_name")}
                />
                <FieldError id="product-name-error" message={errors.product_name?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="disease_target">{t("health.form.diseaseTargetLabel")}</Label>
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
                <Label htmlFor="dose">{t("health.form.doseLabel")}</Label>
                <Input
                  id="dose"
                  maxLength={60}
                  placeholder={t("health.form.dosePlaceholder")}
                  aria-invalid={Boolean(errors.dose) || undefined}
                  aria-describedby={errors.dose ? "event-dose-error" : undefined}
                  {...register("dose")}
                />
                <FieldError id="event-dose-error" message={errors.dose?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="event-route">{t("health.form.routeLabel")}</Label>
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
                <Label htmlFor="vet_name">{t("health.form.vetLabel")}</Label>
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
                <Label htmlFor="cost">{t("health.form.costLabel")}</Label>
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
                <Label htmlFor="next_due_date">{t("health.form.nextDueLabel")}</Label>
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
                <InlineLoading>{t("health.form.loadingDuties")}</InlineLoading>
              )}
              {canViewTasks && tasksQuery.isError && (
                <div
                  role="alert"
                  className="space-y-2 rounded-lg border border-destructive/40 p-3 text-sm"
                >
                  <p className="text-destructive">
                    {tasksQuery.error instanceof ApiError
                      ? tasksQuery.error.detail
                      : t("health.form.dutiesLoadFailed")}
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void tasksQuery.refetch()}
                  >
                    {t("health.form.retryDuties")}
                  </Button>
                </div>
              )}
              {unresolvedPrefillTask && (
                <p role="alert" className="text-sm text-destructive sm:col-span-2">
                  {t("health.form.unresolvedDuty", { id: unresolvedPrefillTask })}
                </p>
              )}
              {(() => {
                const linkedTask = linkableHealthTasks.find(
                  (t) => String(t.id) === wTaskId,
                );
                if (!linkedTask || linkedTask.due_date <= farmToday()) return null;
                return (
                  <p role="status" className="sm:col-span-2 text-sm text-muted-foreground">
                    {t("health.form.dutyNotDue", {
                      id: linkedTask.id,
                      date: formatDate(linkedTask.due_date),
                    })}
                  </p>
                );
              })()}
              {canViewTasks && linkableHealthTasks.length > 0 && (
                <div className="space-y-1.5">
                  <Label htmlFor="event-task">{t("health.form.linkedDutyLabel")}</Label>
                  <Select
                    value={wTaskId || NONE}
                    onValueChange={(v) => applyTask(v)}
                    items={taskItems}
                  >
                    <SelectTrigger id="event-task" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE}>{t("common.none")}</SelectItem>
                      {linkableHealthTasks.map((t) => (
                        <SelectItem key={t.id} value={String(t.id)}>
                          {resolveTaskTitle(t, language)} (due {formatDate(t.due_date)})
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
                {t("health.form.advancedTitle")}
              </summary>
              <p className="mt-2 text-xs text-muted-foreground">
                {t("health.form.advancedIntro")}
              </p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="schedule_template_name">{t("health.form.templateLabel")}</Label>
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
                          // Stryker disable next-line StringLiteral: hand-proven equivalent — Base UI normalizes a non-matching fallback value through its internal selection state (placeholder when unset, selected label when set), exactly like the create-dialog's birth-type select
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
                            <SelectValue placeholder={t("health.form.templatePlaceholder")} />
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
                    <InlineLoading>{t("health.form.loadingTemplates")}</InlineLoading>
                  )}
                  {errors.schedule_template_name && (
                    <p id="schedule-template-error" role="alert" className="text-sm text-destructive">
                      {errors.schedule_template_name.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="next_due_authority">{t("health.form.nextDueAuthorityLabel")}</Label>
                  <Input
                    id="next_due_authority"
                    maxLength={120}
                    placeholder={t("health.form.nextDueAuthorityPlaceholder")}
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
                  <Label htmlFor="product_lot">{t("health.form.lotLabel")}</Label>
                  <Input id="product_lot" maxLength={120} {...register("product_lot")} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="administered_by">{t("health.form.administeredByLabel")}</Label>
                  <Input id="administered_by" maxLength={120} {...register("administered_by")} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="product_manufactured_on">{t("health.form.manufacturedLabel")}</Label>
                  <Input
                    id="product_manufactured_on"
                    type="date"
                    max={farmToday()}
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
                  <Label htmlFor="product_expires_on">{t("health.form.expiresLabel")}</Label>
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
                  <Label htmlFor="vaccine_valid_until">{t("health.form.vaccineValidLabel")}</Label>
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
                  <Label htmlFor="withdrawal_until">{t("health.form.withdrawalLabel")}</Label>
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
                  <Label htmlFor="certificate_number">{t("health.form.certificateLabel")}</Label>
                  <Input id="certificate_number" maxLength={120} {...register("certificate_number")} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="official_tag_number">{t("health.form.officialTagLabel")}</Label>
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
                            // Stryker disable next-line StringLiteral, CallExpression: the field was unregistered one line above, so skipping the write or writing under a garbage key both leave it undefined — and the payload maps undefined and "" to null alike
                            setValue("authority_notified_at", "");
                            // Stryker disable next-line StringLiteral, CallExpression: same unregistered-field equivalence as the authority date
                            setValue("isolation_started_at", "");
                          }
                          field.onChange(selected);
                        }}
                      />
                    )}
                  />
                  <Label htmlFor="suspected_scheduled_disease" className="font-normal">
                    {t("health.form.suspectedDiseaseLabel")}
                  </Label>
                </div>
                {suspectedScheduledDisease && (
                  <>
                    <div className="space-y-1.5">
                      <Label htmlFor="authority_notified_at">{t("health.form.authorityNotifiedLabel")}</Label>
                      <Input
                        id="authority_notified_at"
                        type="date"
                        max={farmToday()}
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
                      <Label htmlFor="isolation_started_at">{t("health.form.isolationStartedLabel")}</Label>
                      <Input
                        id="isolation_started_at"
                        type="date"
                        max={farmToday()}
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
              <Label htmlFor="notes">{t("health.form.notesLabel")}</Label>
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
                {recordError} {t("health.form.errorSuffix")}
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
                  ? t("health.form.reviewing")
                  : isSubmitting || eventSubmission.pending
                    ? t("health.form.saving")
                    : scope !== "animal" && !bulkPreview
                      ? t("health.form.reviewTargets")
                      : scope !== "animal"
                        ? previewCount === 1
                          ? t("health.form.confirmForOne", { count: previewCount })
                          : t("health.form.confirmForMany", { count: previewCount })
                        : recordError
                          ? t("health.form.retrySave")
                          : t("health.form.save")}
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
  const perms = usePermissions();
  const t = useT();
  return (
    <Suspense
      fallback={
        <div className="space-y-6" role="status" aria-live="polite">
          <PageHeader
            title={t("health.page.title")}
            description={t("health.page.description")}
          />
          <span className="sr-only">{t("health.page.loading")}</span>
          <PageSkeleton cards={2} />
        </div>
      }
    >
      <PermissionGate
        perms={perms}
        perm="health.view"
        label={t("health.page.title")}
        description={t("health.page.description")}
        cards={2}
      >
        <HealthPageContent perms={perms} />
      </PermissionGate>
    </Suspense>
  );
}
