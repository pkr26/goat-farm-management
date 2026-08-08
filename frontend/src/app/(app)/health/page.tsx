"use client";

/** Health event log + record-event dialog — parity with v1's health/list.html + health/new.html. */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Syringe } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useForm , useWatch} from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  getListEventsApiHealthEventsGetQueryKey,
  getListTasksApiTasksGetQueryKey,
  useListAnimalsApiAnimalsGet,
  useListBatchesApiPurchasesGet,
  useListEventsApiHealthEventsGet,
  useListTasksApiTasksGet,
  useRecordEventApiHealthEventsPost,
} from "@/api/generated/endpoints";
import {
  HealthEventInBucket,
  HealthEventInType,
  type HealthEventIn,
  type TaskOut,
} from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
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
import { addDays, formatDate, formatMoney, utcToday } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";

const EVENT_TYPES = Object.values(HealthEventInType);
const BUCKETS = Object.values(HealthEventInBucket);
const ROUTES = ["SC", "Oral", "IM"];
/** Sentinel for "no selection" in optional selects (empty string is not a valid item value). */
const NONE = "none";
/** value → label map for the root `items` prop: without it, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
const ROUTE_ITEMS: Record<string, string> = {
  [NONE]: "—",
  ...Object.fromEntries(ROUTES.map((r) => [r, r])),
};

function localToday(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

/** Product/disease hints parsed from a linked duty's title, so recorded
 *  events match the vaccination templates instead of leaving both blank
 *  (audit 3-1: a blank-target deworming event never matched the template). */
export function taskPrefill(task: TaskOut): {
  product_name?: string;
  disease_target?: string;
} {
  // Strip auto-task scaffolding: "[Supplier #3] Day 4: …" → "…", and a
  // trailing ": <animal tag>" ("Pre-kidding ET+TT vaccine: G-ABC12").
  let title = task.title.replace(/^\[[^\]]*\]\s*/, "");
  title = title.replace(/^Days?\s*\d+(?:[–—-]\d+)?:\s*/i, "");
  if (task.animal_tag && title.endsWith(`: ${task.animal_tag}`)) {
    title = title.slice(0, -`: ${task.animal_tag}`.length);
  }
  if (task.category === "DEWORMING") {
    // "deworm — Albendazole/Closantel oral + Ivermectin SC": product is the
    // drug part; the target is always the Deworming template.
    const drug = title.split("—")[1]?.trim();
    return { product_name: drug || undefined, disease_target: "Deworming" };
  }
  if (task.category === "VACCINE") {
    // "vaccinate PPR (live viral, SC)" → "PPR".
    const vaccinated = /vaccinate\s+(.+?)\s*(?:\(|$)/i.exec(title);
    if (vaccinated) return { disease_target: vaccinated[1].trim() };
    // "Pre-kidding ET+TT vaccine" → the "ET + TT pre-kidding" template.
    if (/et\s*\+\s*tt/i.test(title)) return { disease_target: "ET + TT pre-kidding" };
    if (title.trim()) return { disease_target: title.trim() };
  }
  return {};
}

/** Next-due date cell: red tint when overdue, amber when due within a week.
 *  Comparisons use the backend's UTC today (audit 7-5). */
function NextDue({ date }: { date: string }) {
  const overdue = date < utcToday();
  const dueSoon = !overdue && date <= addDays(utcToday(), 7);
  if (!overdue && !dueSoon) return <>{formatDate(date)}</>;
  return (
    <span
      className={
        overdue
          ? "inline-flex items-center gap-1 rounded-md bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-800 dark:bg-red-950 dark:text-red-300"
          : "inline-flex items-center gap-1 rounded-md bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300"
      }
    >
      <CalendarClock className="size-3" />
      {formatDate(date)}
    </span>
  );
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
      .optional(),
    next_due_date: z.string().optional(),
    notes: z.string().optional(),
    task_id: z.string().optional(),
  })
  .superRefine((v, ctx) => {
    if (v.scope === "animal" && !v.animal_id) {
      ctx.addIssue({ code: "custom", path: ["animal_id"], message: "Pick an animal" });
    }
    if (v.scope === "bucket" && !v.bucket) {
      ctx.addIssue({ code: "custom", path: ["bucket"], message: "Pick a bucket" });
    }
    if (v.scope === "batch" && !v.purchase_batch_id) {
      ctx.addIssue({ code: "custom", path: ["purchase_batch_id"], message: "Pick a batch" });
    }
    if (v.date && v.date > localToday()) {
      ctx.addIssue({ code: "custom", path: ["date"], message: "Date cannot be in the future" });
    }
  });
type EventValues = z.infer<typeof eventSchema>;

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="text-sm text-destructive">{message}</p>;
}

export default function HealthPage() {
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const allowed = can("health.view");
  const canManage = can("health.manage");
  const canViewTasks = can("tasks.view");
  const queryClient = useQueryClient();
  const router = useRouter();

  const eventsQuery = useListEventsApiHealthEventsGet({ query: { enabled: allowed } });
  const events = eventsQuery.data?.status === 200 ? eventsQuery.data.data : undefined;

  const animalsQuery = useListAnimalsApiAnimalsGet(undefined, {
    query: { enabled: allowed },
  });
  const animals =
    animalsQuery.data?.status === 200 ? animalsQuery.data.data.animals : undefined;

  const [open, setOpen] = useState(false);
  const batchesQuery = useListBatchesApiPurchasesGet({
    query: { enabled: canManage && open },
  });
  const batches = batchesQuery.data?.status === 200 ? batchesQuery.data.data : undefined;

  const tasksQuery = useListTasksApiTasksGet({
    query: { enabled: canManage && canViewTasks && open },
  });
  const tabs = tasksQuery.data?.status === 200 ? tasksQuery.data.data : undefined;
  const pendingHealthTasks: TaskOut[] = tabs
    ? [...tabs.today, ...tabs.overdue, ...tabs.upcoming].filter(
        (t) =>
          t.status === "PENDING" &&
          (t.category === "VACCINE" || t.category === "DEWORMING"),
      )
    : [];

  const recordMutation = useRecordEventApiHealthEventsPost();
  const [scheduleAnimalId, setScheduleAnimalId] = useState("");
  const [prefillTaskId, setPrefillTaskId] = useState<string | null>(null);

  /** value → label maps for the root `items` prop: without it, Base UI's
   * Select.Value renders the raw value (e.g. an animal id) in the closed
   * trigger, including for programmatically prefilled values. */
  const scheduleAnimalItems: Record<string, string> = Object.fromEntries(
    (animals ?? []).map((a) => [String(a.id), `${a.tag_number}${a.name ? ` · ${a.name}` : ""}`]),
  );
  const animalItems: Record<string, string> = Object.fromEntries(
    (animals ?? []).map((a) => [
      String(a.id),
      `${a.tag_number}${a.name ? ` · ${a.name}` : ""} — ${a.current_bucket}`,
    ]),
  );
  const batchItems: Record<string, string> = Object.fromEntries(
    (batches ?? []).map((b) => [
      String(b.id),
      `#${b.id} — ${formatDate(b.date)} ${b.supplier ?? ""} (${b.count})`,
    ]),
  );
  const taskItems: Record<string, string> = {
    [NONE]: "— none —",
    ...Object.fromEntries(
      pendingHealthTasks.map((t) => [String(t.id), `${t.title} (due ${formatDate(t.due_date)})`]),
    ),
  };

  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    getValues,
    formState: { errors, isSubmitting },
  } = useForm<EventValues>({
    resolver: zodResolver(eventSchema),
    defaultValues: {
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
      notes: "",
      task_id: NONE,
    },
  });
  const wAnimalId = useWatch({ control, name: "animal_id" });
  const wBucket = useWatch({ control, name: "bucket" });
  const wPurchaseBatchId = useWatch({ control, name: "purchase_batch_id" });
  const wRoute = useWatch({ control, name: "route" });
  const wTaskId = useWatch({ control, name: "task_id" });
  const wType = useWatch({ control, name: "type" });
  const scope = useWatch({ control, name: "scope" });

  /** Values the last linked duty prefilled — used to revert them when the
   *  user switches back to "— none —" without clobbering manual edits
   *  (audit 7-11). */
  const appliedPrefillRef = useRef<{
    scope?: EventValues["scope"];
    type?: EventValues["type"];
    product_name?: string;
    disease_target?: string;
  } | null>(null);

  /** Prefill scope/target/type/product from a linked VACCINE/DEWORMING duty
   *  (v1 behaviour + audit 3-1 product/disease hints). */
  function applyTask(taskIdStr: string) {
    setValue("task_id", taskIdStr);
    if (taskIdStr === NONE) {
      const prev = appliedPrefillRef.current;
      if (prev) {
        if (prev.scope && getValues("scope") === prev.scope) setValue("scope", "animal");
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
      appliedPrefillRef.current = null;
      return;
    }
    const task = pendingHealthTasks.find((t) => String(t.id) === taskIdStr);
    if (!task) return;
    const applied: NonNullable<typeof appliedPrefillRef.current> = {};
    if (task.animal_id) {
      applied.scope = "animal";
      setValue("scope", "animal");
      setValue("animal_id", String(task.animal_id));
    } else if (task.purchase_batch_id) {
      applied.scope = "batch";
      setValue("scope", "batch");
      setValue("purchase_batch_id", String(task.purchase_batch_id));
    }
    if (task.category === "VACCINE" || task.category === "DEWORMING") {
      applied.type = task.category;
      setValue("type", task.category);
      // Prefill product/disease from the duty title so the recorded event
      // matches the vaccination templates (audit 3-1). Don't overwrite text
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

  // /health/new?task_id=… redirects here: auto-open the dialog prefilled.
  useEffect(() => {
    if (!canManage || prefillTaskId !== null) return;
    const params = new URLSearchParams(window.location.search);
    const taskId = params.get("task_id");
    if (!taskId) return;
    // One-time mount initialization from URL params — cascading-render risk
    // doesn't apply here (runs once, not reactive to props/state).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOpen(true);
    const animalId = params.get("animal_id");
    const batchId = params.get("purchase_batch_id");
    if (animalId) {
      setValue("scope", "animal");
      setValue("animal_id", animalId);
    }
    if (batchId) {
      setValue("scope", "batch");
      setValue("purchase_batch_id", batchId);
    }
    setPrefillTaskId(taskId);
    setValue("task_id", taskId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canManage]);

  // Once the linked tasks load, finish prefilling from the task itself.
  useEffect(() => {
    if (!prefillTaskId || pendingHealthTasks.length === 0) return;
    applyTask(prefillTaskId);
    // Intentional one-shot cleanup after applying the prefill (see above).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPrefillTaskId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillTaskId, tasksQuery.data]);

  async function onSubmit(values: EventValues) {
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
      notes: values.notes?.trim() || null,
      task_id: values.task_id && values.task_id !== NONE ? Number(values.task_id) : null,
    };
    try {
      await recordMutation.mutateAsync({ data: payload });
      toast.success("Health event recorded.");
      queryClient.invalidateQueries({ queryKey: getListEventsApiHealthEventsGetQueryKey() });
      queryClient.invalidateQueries({ queryKey: getListTasksApiTasksGetQueryKey() });
      setOpen(false);
      reset();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.detail : "Something went wrong");
    }
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
  if (eventsQuery.isLoading || !events) {
    if (eventsQuery.isError) {
      return (
        <p className="text-sm text-destructive">
          {eventsQuery.error instanceof ApiError
            ? eventsQuery.error.detail
            : "Could not load health events."}
        </p>
      );
    }
    return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Health"
        description="Vaccinations, deworming and treatments across the herd."
        actions={
          canManage && (
            <Button
              onClick={() => {
                reset();
                setOpen(true);
              }}
            >
              + Add event
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
            <Select value={scheduleAnimalId} onValueChange={(v) => setScheduleAnimalId(v)} items={scheduleAnimalItems}>
              <SelectTrigger id="schedule-animal" className="w-64">
                <SelectValue placeholder="Pick an animal" />
              </SelectTrigger>
              <SelectContent>
                {(animals ?? []).map((a) => (
                  <SelectItem key={a.id} value={String(a.id)}>
                    {a.tag_number}
                    {a.name ? ` · ${a.name}` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            variant="outline"
            disabled={!scheduleAnimalId}
            onClick={() => router.push(`/health/schedule/${scheduleAnimalId}`)}
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
          />
        ) : (
          <Table>
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
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.map((e) => (
                <TableRow key={e.id}>
                  <TableCell>{formatDate(e.date)}</TableCell>
                  <TableCell>
                    <StatusBadge status={e.type}>{e.type}</StatusBadge>
                  </TableCell>
                  <TableCell>
                    {e.animal_tag ? (
                      <Link href={`/animals/${e.animal_id}`} className="text-primary underline">
                        {e.animal_tag}
                      </Link>
                    ) : e.purchase_batch_id ? (
                      `batch #${e.purchase_batch_id}`
                    ) : (
                      `#${e.animal_id}`
                    )}
                  </TableCell>
                  <TableCell>{e.product_name ?? "—"}</TableCell>
                  <TableCell>{e.disease_target ?? "—"}</TableCell>
                  <TableCell>{e.dose ?? "—"}</TableCell>
                  <TableCell>{e.route ?? "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatMoney(e.cost)}</TableCell>
                  <TableCell>
                    {e.next_due_date ? <NextDue date={e.next_due_date} /> : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DataTableCard>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Add health event</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Apply to</legend>
              <div className="flex gap-4">
                {(
                  [
                    ["animal", "Single animal"],
                    ["bucket", "Whole bucket"],
                    ["batch", "Purchase batch"],
                  ] as const
                ).map(([value, label]) => (
                  <Label key={value} className="flex items-center gap-1.5 font-normal">
                    <input type="radio" value={value} {...register("scope")} />
                    {label}
                  </Label>
                ))}
              </div>
              {scope === "animal" && (
                <div className="space-y-1.5">
                  <Label htmlFor="event-animal">Animal *</Label>
                  <Select
                    value={wAnimalId || ""}
                    onValueChange={(v) => setValue("animal_id", v, { shouldValidate: true })}
                    items={animalItems}
                  >
                    <SelectTrigger id="event-animal" className="w-full">
                      <SelectValue placeholder="Pick an animal" />
                    </SelectTrigger>
                    <SelectContent>
                      {(animals ?? []).map((a) => (
                        <SelectItem key={a.id} value={String(a.id)}>
                          {a.tag_number}
                          {a.name ? ` · ${a.name}` : ""} — {a.current_bucket}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FieldError message={errors.animal_id?.message} />
                </div>
              )}
              {scope === "bucket" && (
                <div className="space-y-1.5">
                  <Label htmlFor="event-bucket">Bucket *</Label>
                  <Select
                    value={wBucket || ""}
                    onValueChange={(v) => setValue("bucket", v, { shouldValidate: true })}
                  >
                    <SelectTrigger id="event-bucket" className="w-full">
                      <SelectValue placeholder="Pick a bucket" />
                    </SelectTrigger>
                    <SelectContent>
                      {BUCKETS.map((b) => (
                        <SelectItem key={b} value={b}>
                          {b}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FieldError message={errors.bucket?.message} />
                </div>
              )}
              {scope === "batch" && (
                <div className="space-y-1.5">
                  <Label htmlFor="event-batch">Purchase batch *</Label>
                  <Select
                    value={wPurchaseBatchId || ""}
                    onValueChange={(v) =>
                      setValue("purchase_batch_id", v, { shouldValidate: true })
                    }
                    items={batchItems}
                  >
                    <SelectTrigger id="event-batch" className="w-full">
                      <SelectValue placeholder="Pick a batch" />
                    </SelectTrigger>
                    <SelectContent>
                      {(batches ?? []).map((b) => (
                        <SelectItem key={b.id} value={String(b.id)}>
                          #{b.id} — {formatDate(b.date)} {b.supplier ?? ""} ({b.count})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FieldError message={errors.purchase_batch_id?.message} />
                </div>
              )}
            </fieldset>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="date">Date (defaults to today)</Label>
                <Input id="date" type="date" max={localToday()} {...register("date")} />
                <FieldError message={errors.date?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="event-type">Type</Label>
                <Select
                  value={wType}
                  onValueChange={(v) =>
                    setValue("type", v as EventValues["type"], { shouldValidate: true })
                  }
                >
                  <SelectTrigger id="event-type" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {EVENT_TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {t}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="product_name">Product name</Label>
                <Input id="product_name" placeholder="e.g. PPR vaccine" {...register("product_name")} />
                <FieldError message={errors.product_name?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="disease_target">Disease target</Label>
                <Input id="disease_target" {...register("disease_target")} />
                <FieldError message={errors.disease_target?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="dose">Dose</Label>
                <Input id="dose" placeholder="e.g. 1 ml" {...register("dose")} />
                <FieldError message={errors.dose?.message} />
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
                <Input id="vet_name" {...register("vet_name")} />
                <FieldError message={errors.vet_name?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="cost">Total cost (₹, split evenly)</Label>
                <Input id="cost" inputMode="decimal" placeholder="0.00" {...register("cost")} />
                <FieldError message={errors.cost?.message} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="next_due_date">Next due date</Label>
                <Input id="next_due_date" type="date" {...register("next_due_date")} />
              </div>
              {canViewTasks && pendingHealthTasks.length > 0 && (
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
                      {pendingHealthTasks.map((t) => (
                        <SelectItem key={t.id} value={String(t.id)}>
                          {t.title} (due {formatDate(t.due_date)})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="notes">Notes</Label>
              <Input id="notes" {...register("notes")} />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Saving…" : "Save event"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
