"use client";

/**
 * Planner (Business): target-based backward planning. The farmer states what
 * must be sold and when ("200 goats in Jan 2027"), and the backend works the
 * biology backward — breeding, gestation, mortality, culling, growth stages —
 * to answer with feasibility, a month-by-month stage plan, dated actions
 * (buy / breed / expect births / sell) and each target's requirement chain.
 * Dairy farms also get the milk-target planner that designs the herd behind a
 * daily-litres contract. Plans can be saved per farm and re-run any time.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  Baby,
  Banknote,
  CalendarCheck,
  Database,
  FolderOpen,
  HeartPulse,
  Milk,
  Play,
  Plus,
  Save,
  ShoppingCart,
  Target,
  Trash2,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type ComponentProps } from "react";
import { toast } from "sonner";

import {
  getPlanApiPlannerPlansPlanIdGet,
  getListPlansApiPlannerPlansGetQueryKey,
  useBreedDefaultsApiSimulationDefaultsGet,
  useCreatePlanApiPlannerPlansPost,
  useDeletePlanApiPlannerPlansPlanIdDelete,
  useFarmCalibrationApiSimulationCalibrationGet,
  useHerdSnapshotApiSimulationHerdSnapshotGet,
  useListBreedsApiSimulationDefaultsBreedsGet,
  useListPlansApiPlannerPlansGet,
  usePlanMilkApiPlannerMilkPlanPost,
  usePlanSalesApiPlannerPlanPost,
  useUpdatePlanApiPlannerPlansPlanIdPatch,
} from "@/api/generated/endpoints";
import type {
  BackwardPlanReport,
  MilkPlanReport,
  PlannerActionKind,
  PlannerPlanOut,
  PlannerTarget,
  SimulationAssumptions,
} from "@/api/generated/models";
import { BreedDefaultsApiSimulationDefaultsGetSystem } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PageSkeleton } from "@/components/skeletons";
import { PermissionsError } from "@/components/permissions-error";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { farmVocabulary, type FarmVocabulary } from "@/lib/farm-vocabulary";
import { farmToday, formatLitres, formatMoney } from "@/lib/format";
import { useFarmType } from "@/hooks/use-farm-type";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

const DEFAULT_SYSTEM = BreedDefaultsApiSimulationDefaultsGetSystem.stall_fed;
const MAX_TARGETS = 50;
const MAX_HORIZON_MONTHS = 240;

type TargetRow = {
  key: string;
  year_month: string;
  animal_class: PlannerTarget["animal_class"];
  count: number;
};

/** "2027-01" → "Jan 2027" (the label farmers plan in). */
const MONTH_NAMES = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

function isValidYearMonth(value: string): boolean {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(value);
}

function formatYearMonth(value: string): string {
  if (!isValidYearMonth(value)) return value;
  const [year, month] = value.split("-");
  return `${MONTH_NAMES[Number(month) - 1]} ${year}`;
}

function currentYearMonth(): string {
  // The farm's timezone, not the browser's: an operator west of IST opening
  // the planner between 00:00 and 05:30 farm time must not land in (or be
  // capped at) the previous month.
  return farmToday().slice(0, 7);
}

function addMonths(yearMonth: string, months: number): string {
  const [year, month] = yearMonth.split("-").map(Number);
  const total = year * 12 + (month - 1) + months;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

/** YYYY-MM strings compare lexicographically once validated. */
function isBefore(a: string, b: string): boolean {
  return a < b;
}

/** Head counts print as whole numbers: plans are stated in whole animals. */
function formatPlanCount(value: number): string {
  return Number.isFinite(value)
    ? Number.isInteger(value)
      ? String(value)
      : value.toFixed(1)
    : "—";
}

/** Cohort head counts are expected values (float64); one decimal like the
 * simulation tables. */
function formatHead(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : value.toFixed(1);
}

function eventClassItems(vocabulary: FarmVocabulary): Record<string, string> {
  const young = vocabulary.young;
  return {
    doe: vocabulary.femaleAdult.charAt(0).toUpperCase() + vocabulary.femaleAdult.slice(1),
    buck: vocabulary.maleAdult.charAt(0).toUpperCase() + vocabulary.maleAdult.slice(1),
    female_kid: `Female ${young}`,
    male_kid: `Male ${young}`,
    female_weaner: "Female weaner",
    male_weaner: "Male weaner",
    female_grower: "Female grower",
    male_grower: "Male grower",
  };
}

function formatPlanClass(animalClass: string, vocabulary: FarmVocabulary): string {
  return eventClassItems(vocabulary)[animalClass] ?? animalClass;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

const ACTION_ICONS: Record<PlannerActionKind, LucideIcon> = {
  purchase: ShoppingCart,
  breed: HeartPulse,
  expect_births: Baby,
  retain: FolderOpen,
  sell: Banknote,
};

const ACTION_KIND_LABELS: Record<PlannerActionKind, string> = {
  purchase: "Buy",
  breed: "Breed",
  expect_births: "Expect births",
  retain: "Retain",
  sell: "Sell",
};

/** Minimal numeric input for this page's small number fields: commits only
 * finite numbers so NaN can never reach a payload. The draft is local while
 * the field is edited (blank never commits), so a cleared field does not snap
 * back to the stored value mid-edit. */
function NumberField(
  props: ComponentProps<typeof Input> & { onCommitNumber: (n: number) => void },
) {
  const { onCommitNumber, ...inputProps } = props;
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <Input
      type="number"
      {...inputProps}
      value={draft ?? String(inputProps.value ?? "")}
      onChange={(event) => {
        const raw = event.target.value;
        setDraft(raw);
        const parsed = Number(raw);
        if (raw !== "" && Number.isFinite(parsed)) onCommitNumber(parsed);
      }}
      onBlur={() => setDraft(null)}
    />
  );
}

export default function PlannerPage() {
  const { can, loading: permsLoading, isError: permsError, refetch: permsRefetch } =
    usePermissions();
  const allowed = can("simulation.view");
  const canManage = can("simulation.manage");
  const canUseHerd = can("animals.view");
  const canCalibrate = [
    "animals.view",
    "breeding.view",
    "kidding.view",
    "feeding.view",
    "finance.view",
  ].every((permission) => can(permission));
  const queryClient = useQueryClient();
  const plannerAction = useSingleFlight();
  const saveAction = useSingleFlight();

  const farmType = useFarmType();
  const vocabulary = farmVocabulary(farmType);
  const isDairyFarm = farmType === "BUFFALO_DAIRY";
  const defaultBreed = isDairyFarm ? "murrah_dairy" : "osmanabadi";

  // ----- Plan basis: anchor month + the assumptions the plan runs against.
  const [startMonth, setStartMonth] = useState(currentYearMonth);
  const [system, setSystem] = useState<BreedDefaultsApiSimulationDefaultsGetSystem>(DEFAULT_SYSTEM);
  const [submittedParams, setSubmittedParams] = useState<{
    breed: string;
    system: BreedDefaultsApiSimulationDefaultsGetSystem;
  }>({ breed: defaultBreed, system: DEFAULT_SYSTEM });
  const [assumptions, setAssumptions] = useState<SimulationAssumptions | null>(null);
  const [basisSource, setBasisSource] = useState<"preset" | "herd" | "calibration" | "saved">(
    "preset",
  );
  // A defaults response may only land while it is still the latest intent.
  const acceptDefaultsRef = useRef(true);

  const breedsQuery = useListBreedsApiSimulationDefaultsBreedsGet({
    query: { enabled: allowed },
  });
  const defaultsQuery = useBreedDefaultsApiSimulationDefaultsGet(submittedParams, {
    query: { enabled: allowed },
  });
  useEffect(() => {
    if (defaultsQuery.data?.status === 200 && acceptDefaultsRef.current) {
      acceptDefaultsRef.current = false;
      setAssumptions(defaultsQuery.data.data);
      setBasisSource("preset");
    }
  }, [defaultsQuery.data]);

  function loadBreedDefaults(next: { breed: string; system: typeof system }) {
    acceptDefaultsRef.current = true;
    setSubmittedParams(next);
  }

  const snapshotQuery = useHerdSnapshotApiSimulationHerdSnapshotGet(
    // Bucket by the breed actually loaded, matching the simulation page's rule.
    { breed: submittedParams.breed },
    { query: { enabled: false } },
  );
  const calibrationQuery = useFarmCalibrationApiSimulationCalibrationGet(
    { breed: submittedParams.breed, system, lookback_months: 24 },
    { query: { enabled: false } },
  );

  async function onUseCurrentHerd() {
    if (!assumptions) {
      toast.error("The breed preset is still loading — try again in a moment.");
      return;
    }
    const farmScope = captureFarmScope();
    try {
      const res = await snapshotQuery.refetch();
      if (res.isError || res.data?.status !== 200) {
        if (!farmScope()) return;
        toast.error(errorMessage(res.error, "Could not load the herd snapshot."));
        return;
      }
      const snap = res.data.data;
      setAssumptions((prev) =>
        prev
          ? {
              ...prev,
              herd: {
                ...prev.herd,
                does: snap.does,
                bucks: snap.bucks,
                female_kids: snap.f_kids,
                female_weaners: snap.f_weaners,
                female_growers: snap.f_growers,
                male_kids: snap.m_kids,
                male_weaners: snap.m_weaners,
                male_growers: snap.m_growers,
              },
            }
          : prev,
      );
      setBasisSource("herd");
      if (!farmScope()) return;
      toast.success(`Starting stock set to your current herd (${snap.total_head} head).`);
    } catch (err) {
      if (!farmScope()) return;
      toast.error(errorMessage(err, "Could not load the herd snapshot."));
    }
  }

  async function onCalibrateFromFarm() {
    const farmScope = captureFarmScope();
    try {
      const res = await calibrationQuery.refetch();
      if (res.isError || res.data?.status !== 200) {
        if (!farmScope()) return;
        toast.error(errorMessage(res.error, "Could not calibrate from farm records."));
        return;
      }
      const calibrated = res.data.data;
      setAssumptions(calibrated.assumptions);
      setBasisSource("calibration");
      if (!farmScope()) return;
      toast.success(
        `Calibrated ${calibrated.evidence.length} assumptions from your farm records.`,
      );
    } catch (err) {
      if (!farmScope()) return;
      toast.error(errorMessage(err, "Could not calibrate from farm records."));
    }
  }

  // ----- Targets.
  const targetKeyCounter = useRef(0);
  const nextDefaultTargetMonth = () => addMonths(startMonth, 12);
  const [targets, setTargets] = useState<TargetRow[]>([]);

  function addTarget() {
    setTargets((prev) => [
      ...prev,
      {
        key: `target-${targetKeyCounter.current++}`,
        year_month: nextDefaultTargetMonth(),
        animal_class: "male_grower",
        count: 20,
      },
    ]);
  }

  function updateTarget(key: string, patch: Partial<TargetRow>) {
    setTargets((prev) => prev.map((t) => (t.key === key ? { ...t, ...patch } : t)));
  }

  function removeTarget(key: string) {
    setTargets((prev) => prev.filter((t) => t.key !== key));
  }

  // A sale must land at least one month after the plan starts (biology needs
  // lead time) and within the engine's 20-year ceiling (240 months out).
  const targetCeiling = addMonths(startMonth, MAX_HORIZON_MONTHS - 1);
  const targetErrors = targets
    .map((target, index) => {
      const label = `Target ${index + 1}`;
      if (!isValidYearMonth(target.year_month))
        return `${label}: pick a real month (YYYY-MM).`;
      if (!isBefore(startMonth, target.year_month))
        return `${label}: the month must come after the plan start (${formatYearMonth(startMonth)}).`;
      if (isBefore(targetCeiling, target.year_month))
        return `${label}: beyond the 20-year planning ceiling.`;
      if (!Number.isFinite(target.count) || target.count <= 0 || target.count > 100_000)
        return `${label}: count must be greater than 0 and at most 100,000.`;
      return null;
    })
    .filter((error): error is string => error !== null);

  // ----- Backward plan run.
  const planMutation = usePlanSalesApiPlannerPlanPost();
  const [report, setReport] = useState<BackwardPlanReport | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planInputsSnapshot, setPlanInputsSnapshot] = useState<string | null>(null);

  const planInputsKey = `${startMonth}|${JSON.stringify(targets)}|${JSON.stringify(assumptions)}`;
  const reportIsStale = report !== null && planInputsSnapshot !== planInputsKey;

  /** The anchored assumption document a run/save actually uses. */
  function anchoredAssumptions(): SimulationAssumptions | null {
    if (!assumptions) return null;
    return {
      ...assumptions,
      meta: { ...assumptions.meta, start_year_month: startMonth },
    } as SimulationAssumptions;
  }

  async function onPlan() {
    await plannerAction.run(async () => {
      const farmScope = captureFarmScope();
      const payload = anchoredAssumptions();
      if (!payload || targets.length === 0 || targetErrors.length > 0) return;
      setPlanError(null);
      try {
        const res = await planMutation.mutateAsync({
          data: {
            assumptions: payload,
            targets: targets.map((t) => ({
              year_month: t.year_month,
              animal_class: t.animal_class,
              count: t.count,
            })),
            close_gaps: true,
            risk_runs: 100,
          },
        });
        if (res.status === 200 && farmScope()) {
          setReport(res.data);
          setPlanInputsSnapshot(planInputsKey);
        }
      } catch (err) {
        if (!farmScope()) return;
        const message = errorMessage(err, "Plan failed");
        setPlanError(message);
        toast.error(message);
      }
    });
  }

  // ----- Milk planner (dairy farms).
  const milkPlanMutation = usePlanMilkApiPlannerMilkPlanPost();
  const [milkTarget, setMilkTarget] = useState(1000);
  const [milkRampMonths, setMilkRampMonths] = useState(1);
  const [milkProjectionMonths, setMilkProjectionMonths] = useState(36);
  const [milkHoldYearRound, setMilkHoldYearRound] = useState(false);
  const [milkReport, setMilkReport] = useState<MilkPlanReport | null>(null);
  // The start month the milk plan actually ran with (labels stay truthful
  // after the user moves the start while the report is only stale-flagged).
  const [milkRanStart, setMilkRanStart] = useState(currentYearMonth);
  const [milkError, setMilkError] = useState<string | null>(null);
  const [milkInputsSnapshot, setMilkInputsSnapshot] = useState<string | null>(null);

  const horizonMonths = assumptions?.meta?.horizon_months ?? 120;
  // The milk plan depends on the milk inputs, the basis and the start month —
  // NOT on the sale targets, which must not flag it stale.
  const milkInputsKey = `${milkTarget}|${milkRampMonths}|${milkProjectionMonths}|${milkHoldYearRound}|${startMonth}|${JSON.stringify(assumptions)}`;
  const milkReportIsStale = milkReport !== null && milkInputsSnapshot !== milkInputsKey;

  async function onMilkPlan() {
    await plannerAction.run(async () => {
      const farmScope = captureFarmScope();
      const payload = anchoredAssumptions();
      if (!payload) return;
      setMilkError(null);
      try {
        const res = await milkPlanMutation.mutateAsync({
          data: {
            assumptions: payload,
            daily_target_litres: milkTarget,
            ramp_months: milkRampMonths,
            projection_months: milkProjectionMonths,
            hold_year_round: milkHoldYearRound,
          },
        });
        if (res.status === 200 && farmScope()) {
          setMilkReport(res.data);
          setMilkRanStart(startMonth);
          setMilkInputsSnapshot(milkInputsKey);
        }
      } catch (err) {
        if (!farmScope()) return;
        const message = errorMessage(err, "Milk plan failed");
        setMilkError(message);
        toast.error(message);
      }
    });
  }

  // ----- Saved plans.
  const plansQuery = useListPlansApiPlannerPlansGet(
    { limit: 50, offset: 0 },
    { query: { enabled: allowed } },
  );
  const createPlanMutation = useCreatePlanApiPlannerPlansPost();
  const updatePlanMutation = useUpdatePlanApiPlannerPlansPlanIdPatch();
  const deletePlanMutation = useDeletePlanApiPlannerPlansPlanIdDelete();
  const [planName, setPlanName] = useState("");
  const [openPlan, setOpenPlan] = useState<PlannerPlanOut | null>(null);
  // DELETE is permanent with no undo; the row button only stages the plan
  // here and the confirm dialog below performs it (same contract as the
  // simulation page's scenario delete).
  const [pendingDelete, setPendingDelete] = useState<PlannerPlanOut | null>(null);

  const plansPage = plansQuery.data?.status === 200 ? plansQuery.data.data : null;
  const savedPlans = plansPage ? plansPage.items.filter((item) => item.valid) : [];

  function invalidatePlans() {
    void queryClient.invalidateQueries({ queryKey: getListPlansApiPlannerPlansGetQueryKey() });
  }

  async function onSavePlan() {
    await saveAction.run(async () => {
      const farmScope = captureFarmScope();
      const payload = anchoredAssumptions();
      const name = planName.trim();
      if (!payload || targets.length === 0 || targetErrors.length > 0) {
        toast.error("Fix the targets before saving the plan.");
        return;
      }
      if (!name) {
        toast.error("Give the plan a name before saving.");
        return;
      }
      try {
        const res = await createPlanMutation.mutateAsync({
          data: {
            name,
            start_year_month: startMonth,
            targets: targets.map((t) => ({
              year_month: t.year_month,
              animal_class: t.animal_class,
              count: t.count,
            })),
            assumptions: payload,
          },
        });
        if (res.status === 201 && farmScope()) {
          setOpenPlan(res.data);
          invalidatePlans();
          toast.success(`Saved plan “${name}”.`);
        }
      } catch (err) {
        if (!farmScope()) return;
        toast.error(errorMessage(err, "Could not save the plan."));
      }
    });
  }

  async function onUpdatePlan() {
    await saveAction.run(async () => {
      const farmScope = captureFarmScope();
      if (!openPlan) return;
      const payload = anchoredAssumptions();
      if (!payload || targets.length === 0 || targetErrors.length > 0) {
        toast.error("Fix the targets before updating the saved plan.");
        return;
      }
      const name = planName.trim();
      if (!name) {
        toast.error("Give the plan a name before updating it.");
        return;
      }
      try {
        const res = await updatePlanMutation.mutateAsync({
          planId: openPlan.id,
          data: {
            expected_revision: openPlan.revision,
            name,
            start_year_month: startMonth,
            targets: targets.map((t) => ({
              year_month: t.year_month,
              animal_class: t.animal_class,
              count: t.count,
            })),
            assumptions: payload,
          },
        });
        if (res.status === 200 && farmScope()) {
          setOpenPlan(res.data);
          invalidatePlans();
          toast.success(`Updated “${res.data.name}”.`);
        }
      } catch (err) {
        if (!farmScope()) return;
        if (err instanceof ApiError && err.status === 409) {
          toast.error(
            "This plan changed in another tab. The latest revision was loaded — press Update again.",
          );
          // Adopt the current row so a retry uses the fresh revision instead
          // of failing forever against the stale one.
          const refreshed = await client_get_plan(openPlan.id);
          if (refreshed) setOpenPlan(refreshed);
          return;
        }
        toast.error(errorMessage(err, "Could not update the saved plan."));
      }
    });
  }

  async function client_get_plan(planId: number): Promise<PlannerPlanOut | null> {
    try {
      const res = await getPlanApiPlannerPlansPlanIdGet(planId);
      return res.status === 200 ? res.data : null;
    } catch {
      return null;
    }
  }

  async function onDeletePlan(plan: PlannerPlanOut) {
    setPendingDelete(null);
    const farmScope = captureFarmScope();
    try {
      const res = await deletePlanMutation.mutateAsync({ planId: plan.id });
      if (res.status === 204 && farmScope()) {
        if (openPlan?.id === plan.id) {
          setOpenPlan(null);
          setPlanName("");
        }
        invalidatePlans();
        toast.success(`Deleted “${plan.name}”.`);
      }
    } catch (err) {
      if (!farmScope()) return;
      toast.error(errorMessage(err, "Could not delete the plan."));
    }
  }

  function onOpenPlan(plan: PlannerPlanOut) {
    if (!plan.targets || !plan.assumptions) return;
    setTargets(
      plan.targets.map((target) => ({
        key: `target-${targetKeyCounter.current++}`,
        year_month: target.year_month,
        animal_class: target.animal_class,
        count: target.count,
      })),
    );
    setStartMonth(plan.start_year_month);
    setAssumptions(plan.assumptions);
    acceptDefaultsRef.current = false;
    // The plan carries its own assumptions; re-anchor the preset dropdowns to
    // the farm's species default so "Use my herd" / "Use farm records" bucket
    // and calibrate by the right species' thresholds, visibly.
    setSubmittedParams({ breed: defaultBreed, system: DEFAULT_SYSTEM });
    setSystem(DEFAULT_SYSTEM);
    setBasisSource("saved");
    setOpenPlan(plan);
    setPlanName(plan.name);
    setReport(null);
    setMilkReport(null);
    toast.success(`Opened “${plan.name}” — press Plan to re-run it against today's biology.`);
  }

  // ----- Render.
  if (permsLoading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Planner"
          description="Set sale targets by month; the planner works backward to today's to-do list."
        />
        <PageSkeleton cards={2} />
      </div>
    );
  }
  if (permsError) {
    return <PermissionsError onRetry={() => void permsRefetch()} />;
  }
  if (!allowed) {
    return <p className="text-muted-foreground">You don&apos;t have access to this page.</p>;
  }

  const evaluation = report ? (report.plan.after ?? report.plan.before) : null;
  const breeds = breedsQuery.data?.status === 200 ? breedsQuery.data.data.breeds : [];
  const breedSelectItems = Object.fromEntries(
    breeds.map((b) => [b, b.replace(/_/g, " ")]),
  ) as Record<string, string>;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Planner"
        description={`State what must be sold and when — the planner works backward through breeding, gestation, mortality and culling to tell you what to do starting ${formatYearMonth(startMonth)}.`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="planner-plan-name"
              aria-label="Plan name"
              placeholder="Plan name (e.g. Festival 2028)"
              className="w-56"
              value={planName}
              onChange={(event) => setPlanName(event.target.value)}
              maxLength={120}
            />
            <Button
              variant="outline"
              size="sm"
              onClick={onSavePlan}
              disabled={
                !canManage ||
                createPlanMutation.isPending ||
                targets.length === 0 ||
                targetErrors.length > 0 ||
                !planName.trim()
              }
            >
              <Save />
              Save plan
            </Button>
            {openPlan && (
              <Button
                variant="outline"
                size="sm"
                onClick={onUpdatePlan}
                disabled={
                  !canManage ||
                  updatePlanMutation.isPending ||
                  targets.length === 0 ||
                  targetErrors.length > 0
                }
              >
                Update “{openPlan.name}”
              </Button>
            )}
          </div>
        }
      />

      <DataTableCard
        title="Sale targets"
        description="What you must sell, and when. Any month up to 20 years out; any class of animal."
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={addTarget}
              disabled={!assumptions || targets.length >= MAX_TARGETS}
            >
              <Plus />
              Add target
            </Button>
            <Button
              size="sm"
              onClick={onPlan}
              disabled={
                !assumptions ||
                targets.length === 0 ||
                targetErrors.length > 0 ||
                planMutation.isPending
              }
            >
              {planMutation.isPending ? "Planning…" : "Plan"}
              <Play />
            </Button>
          </div>
        }
        contentClassName="space-y-3"
      >
        {targets.length === 0 ? (
          <EmptyState
            icon={Target}
            title="No sale targets yet"
            description={`Add your first target — e.g. “200 ${vocabulary.speciesPlural} in ${formatYearMonth(addMonths(startMonth, 16))}”. The planner checks it against breeding, gestation, mortality and culling, and tells you what to do.`}
          />
        ) : (
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow>
                <TableHead>Month</TableHead>
                <TableHead>Class</TableHead>
                <TableHead>Count</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {targets.map((target) => (
                <TableRow key={target.key}>
                  <TableCell>
                    <Input
                      type="month"
                      aria-label="Sale month"
                      className="w-40"
                      min={addMonths(startMonth, 1)}
                      value={target.year_month}
                      onChange={(event) => updateTarget(target.key, { year_month: event.target.value })}
                    />
                  </TableCell>
                  <TableCell>
                    <Select
                      value={target.animal_class}
                      onValueChange={(v) =>
                        updateTarget(target.key, {
                          animal_class: v as PlannerTarget["animal_class"],
                        })
                      }
                      items={eventClassItems(vocabulary)}
                    >
                      <SelectTrigger aria-label="Class" size="sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(eventClassItems(vocabulary)).map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    <NumberField
                      aria-label="Count"
                      className="w-24"
                      min={1}
                      max={100_000}
                      step={1}
                      value={target.count}
                      onCommitNumber={(n) => updateTarget(target.key, { count: n })}
                    />
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => removeTarget(target.key)}
                      aria-label={`Remove target ${formatYearMonth(target.year_month)}`}
                    >
                      <Trash2 />
                      Remove
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {targetErrors.map((error) => (
          <p key={error} role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ))}
      </DataTableCard>

      <Card>
        <CardHeader>
          <CardTitle>Plan basis</CardTitle>
          <CardDescription>
            The herd and biology the plan starts from. Month 1 of the plan is the start month;
            every date in the plan is counted from it.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-1">
            <Label htmlFor="planner-start">Plan start</Label>
            <Input
              id="planner-start"
              type="month"
              className="w-40"
              value={startMonth}
              max={currentYearMonth()}
              onChange={(event) => {
                if (isValidYearMonth(event.target.value)) setStartMonth(event.target.value);
              }}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="planner-breed">Breed preset</Label>
            <Select
              value={submittedParams.breed}
              onValueChange={(v) => loadBreedDefaults({ breed: v, system })}
              items={breedSelectItems}
            >
              <SelectTrigger id="planner-breed" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(breedSelectItems).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="planner-system">Production system</Label>
            <Select
              value={system}
              onValueChange={(v) => {
                const next = v as typeof system;
                setSystem(next);
                loadBreedDefaults({ breed: submittedParams.breed, system: next });
              }}
              items={{ stall_fed: "Stall-fed", semi_intensive: "Semi-intensive" }}
            >
              <SelectTrigger id="planner-system" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="stall_fed">Stall-fed</SelectItem>
                <SelectItem value="semi_intensive">Semi-intensive</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void onUseCurrentHerd()}
              disabled={!canUseHerd || snapshotQuery.isFetching}
            >
              <Database />
              {snapshotQuery.isFetching ? "Loading…" : "Use my herd"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void onCalibrateFromFarm()}
              disabled={!canCalibrate || calibrationQuery.isFetching}
              title={
                canCalibrate
                  ? undefined
                  : "Needs animals, breeding, births, feeding and finance read access."
              }
            >
              {calibrationQuery.isFetching ? "Calibrating…" : "Use farm records"}
            </Button>
          </div>
          <p className="text-sm text-muted-foreground sm:col-span-2 lg:col-span-4" role="note">
            {basisSource === "preset" && "Starting from breed-preset defaults."}
            {basisSource === "herd" &&
              `Starting from your live herd's head counts on top of the ${submittedParams.breed.replace(/_/g, " ")} preset.`}
            {basisSource === "calibration" &&
              "Starting from assumptions calibrated against this farm's own records."}
            {basisSource === "saved" && "Starting from a saved plan's assumptions."}{" "}
            For full control of every assumption (feed, prices, finance), build them in{" "}
            <a className="underline" href="/simulation">
              Simulation
            </a>{" "}
            and calibrate there first.
          </p>
        </CardContent>
      </Card>

      {planError && (
        <p role="alert" className="text-sm text-destructive">
          {planError}
        </p>
      )}

      {report && evaluation && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>
                {report.plan.gaps_closed ? "The plan is feasible" : "The plan cannot fully close"}
                {reportIsStale ? " · stale — re-run after edits" : ""}
              </CardTitle>
              <CardDescription>
                Shortfall {formatPlanCount(evaluation.total_shortfall)} head after
                recommendations · {report.plan.recommended_purchases.length} recommended purchase
                event(s) · NPV {formatMoney(report.plan.before.npv)} before purchases
                {report.plan.after ? `, ${formatMoney(report.plan.after.npv)} after` : ""}.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table className="min-w-[820px]">
                <TableHeader>
                  <TableRow>
                    <TableHead>Month</TableHead>
                    <TableHead>Class</TableHead>
                    <TableHead>Target</TableHead>
                    <TableHead>Filled now</TableHead>
                    <TableHead>With purchases</TableHead>
                    <TableHead>₹/head</TableHead>
                    <TableHead>P(full)</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.plan.before.targets.map((fill, index) => {
                    const after = report.plan.after?.targets[index];
                    const risk = report.plan.probabilities?.[index];
                    const echo = report.targets_echo[index];
                    return (
                      <TableRow key={`plan-fill-${index}`}>
                        <TableCell>{echo ? formatYearMonth(echo.year_month) : fill.month}</TableCell>
                        <TableCell>{formatPlanClass(fill.animal_class, vocabulary)}</TableCell>
                        <TableCell>{formatPlanCount(fill.requested)}</TableCell>
                        <TableCell>
                          {fill.met ? "✓" : "⚠"} {formatPlanCount(fill.filled)}
                        </TableCell>
                        <TableCell>
                          {after ? `${after.met ? "✓" : "⚠"} ${formatPlanCount(after.filled)}` : "—"}
                        </TableCell>
                        <TableCell>{formatMoney(fill.price_per_head)}</TableCell>
                        <TableCell>{risk ? `${Math.round(risk.p_full * 100)}%` : "—"}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>What to do and when</CardTitle>
              <CardDescription>
                The dated to-do list that delivers the targets. Actions dated before the plan
                start are missed deadlines — the reason a target cannot fill.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {report.actions.map((action, index) => {
                const Icon = ACTION_ICONS[action.kind];
                const missed = action.year_month < (report?.start_year_month ?? startMonth);
                return (
                  <div
                    key={`action-${index}`}
                    className={`flex items-start gap-3 rounded-lg border p-3 ${
                      missed ? "border-warning-tint-border bg-warning-tint/30" : "border-border"
                    }`}
                  >
                    <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                    <div className="min-w-0">
                      <p className="text-sm font-medium">
                        <span className="text-muted-foreground">
                          {formatYearMonth(action.year_month)} · {ACTION_KIND_LABELS[action.kind]}:
                        </span>{" "}
                        {action.headline}
                      </p>
                      <p className="text-sm text-muted-foreground">{action.detail}</p>
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Why these numbers — each target worked backward</CardTitle>
              <CardDescription>
                The chain from the sale date back through survival, litter size, sex share and
                conception to the does that must be bred.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {report.chains.map((chain, index) => (
                <div
                  key={`chain-${index}`}
                  className="space-y-2 rounded-lg border border-border p-3"
                >
                  <p className="text-sm font-medium">
                    {formatPlanCount(chain.count)}{" "}
                    {formatPlanClass(chain.animal_class, vocabulary)} in{" "}
                    {formatYearMonth(chain.year_month)}{" "}
                    {chain.achievable ? (
                      <span className="text-muted-foreground">· achievable</span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-warning-tint-foreground">
                        <TriangleAlert className="size-3.5" aria-hidden />
                        not achievable as planned
                      </span>
                    )}
                  </p>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>When</TableHead>
                        <TableHead>How many</TableHead>
                        <TableHead>What</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {chain.steps.map((step, stepIndex) => (
                        <TableRow key={`chain-step-${index}-${stepIndex}`}>
                          <TableCell>{formatYearMonth(step.year_month)}</TableCell>
                          <TableCell>{step.quantity}</TableCell>
                          <TableCell>{step.label}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  <p className="text-sm text-muted-foreground" role="note">
                    {chain.explanation}
                  </p>
                </div>
              ))}
            </CardContent>
          </Card>

          <DataTableCard
            title="Stage plan — the herd shape the targets require"
            description="How many animals must stand in each stage every month (end-of-month head), plus that month's flows. This is the herd you are managing toward."
            contentClassName="space-y-3"
          >
            <div className="max-h-96 overflow-auto">
              <Table className="min-w-[1100px]">
                <TableHeader>
                  <TableRow>
                    <TableHead>Month</TableHead>
                    <TableHead>F {vocabulary.young}s</TableHead>
                    <TableHead>M {vocabulary.young}s</TableHead>
                    <TableHead>F weaners</TableHead>
                    <TableHead>M weaners</TableHead>
                    <TableHead>F growers</TableHead>
                    <TableHead>M growers</TableHead>
                    <TableHead>Open {vocabulary.femaleAdultPlural}</TableHead>
                    <TableHead>Pregnant</TableHead>
                    <TableHead>Lactating</TableHead>
                    <TableHead>{vocabulary.maleAdult}s</TableHead>
                    <TableHead>Total</TableHead>
                    <TableHead>Born</TableHead>
                    <TableHead>Died</TableHead>
                    <TableHead>Culled</TableHead>
                    <TableHead>Sold</TableHead>
                    <TableHead>Bought</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.stage_plan.map((row) => (
                    <TableRow key={`stage-${row.month}`}>
                      <TableCell>{formatYearMonth(row.year_month)}</TableCell>
                      <TableCell>{formatHead(row.female_kids)}</TableCell>
                      <TableCell>{formatHead(row.male_kids)}</TableCell>
                      <TableCell>{formatHead(row.female_weaners)}</TableCell>
                      <TableCell>{formatHead(row.male_weaners)}</TableCell>
                      <TableCell>{formatHead(row.female_growers)}</TableCell>
                      <TableCell>{formatHead(row.male_growers)}</TableCell>
                      <TableCell>{formatHead(row.open_does)}</TableCell>
                      <TableCell>{formatHead(row.pregnant_does)}</TableCell>
                      <TableCell>{formatHead(row.lactating_does)}</TableCell>
                      <TableCell>{formatHead(row.bucks)}</TableCell>
                      <TableCell className="font-medium">{formatHead(row.total_head)}</TableCell>
                      <TableCell>{formatHead(row.births)}</TableCell>
                      <TableCell>{formatHead(row.deaths)}</TableCell>
                      <TableCell>{formatHead(row.culls_head)}</TableCell>
                      <TableCell>{formatHead(row.sales_head)}</TableCell>
                      <TableCell>{formatHead(row.purchases_head)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </DataTableCard>

          {(report.notes ?? []).length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Notes</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {(report.notes ?? []).map((note, index) => (
                  <p key={`plan-note-${index}`} className="text-sm text-muted-foreground" role="note">
                    {note}
                  </p>
                ))}
              </CardContent>
            </Card>
          )}
        </>
      )}

      {isDairyFarm && (
        <DataTableCard
          title="Milk target"
          description="State the litres per day the dairy must ship (a procurement contract or bulk buyer). The planner designs the herd that delivers it: animals by lactation stage, the calving/AI calendar that keeps the tank flat, and the in-milk purchases that build it."
          actions={
            <Button
              size="sm"
              onClick={onMilkPlan}
              disabled={!assumptions || milkPlanMutation.isPending || milkTarget <= 0}
            >
              {milkPlanMutation.isPending ? "Planning…" : "Plan milk"}
            </Button>
          }
          contentClassName="space-y-3"
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1">
              <Label htmlFor="milk-target">Daily target (L)</Label>
              <NumberField
                id="milk-target"
                min={1}
                max={1_000_000}
                value={milkTarget}
                onCommitNumber={(n) => setMilkTarget(n)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="milk-ramp">Ramp (months)</Label>
              <NumberField
                id="milk-ramp"
                min={1}
                max={Math.max(1, Math.min(60, horizonMonths - 1))}
                step={1}
                className="w-24"
                value={milkRampMonths}
                onCommitNumber={(n) => setMilkRampMonths(n)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="milk-projection">Projection (months)</Label>
              <NumberField
                id="milk-projection"
                min={12}
                max={horizonMonths}
                step={1}
                className="w-24"
                value={milkProjectionMonths}
                onCommitNumber={(n) => setMilkProjectionMonths(n)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="milk-year-round">Sizing</Label>
              <Select
                value={milkHoldYearRound ? "year_round" : "average"}
                onValueChange={(v) => setMilkHoldYearRound(v === "year_round")}
                items={{ average: "12-month average", year_round: "Hold year-round" }}
              >
                <SelectTrigger id="milk-year-round" size="sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="average">12-month average</SelectItem>
                  <SelectItem value="year_round">Hold year-round</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {milkError && (
            <p role="alert" className="text-sm text-destructive">
              {milkError}
            </p>
          )}

          {!milkReport ? (
            <EmptyState
              icon={Milk}
              title="No milk plan yet"
              description="Enter the daily litres you must ship — the planner sizes the herd, the monthly calving calendar and the AI schedule behind it."
            />
          ) : (
            <div className="space-y-3 rounded-lg border border-border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">
                  {milkReport.achievable ? "Target is achievable" : "Target falls short"}
                  {" · "}
                  <span className="text-muted-foreground">
                    steady {formatLitres(milkReport.steady_average_daily_litres)} L/day
                    {milkReport.steady_from_month !== null
                      ? ` from month ${milkReport.steady_from_month}`
                      : ""}
                    {milkReportIsStale ? " · stale — re-plan after edits" : ""}
                  </span>
                </span>
                <span className="text-sm text-muted-foreground">
                  {formatHead(milkReport.herd.breeding_does)} breeding (
                  {formatHead(milkReport.herd.milking_does)} milking) ·{" "}
                  {formatHead(milkReport.herd.calvings_per_month)} calvings and{" "}
                  {formatHead(milkReport.herd.ai_services_per_month)} AI/month ·{" "}
                  {milkReport.herd.dry_months_per_cycle.toFixed(1)} dry months per{" "}
                  {milkReport.herd.calving_interval_months.toFixed(1)}-month cycle (
                  {Math.round(milkReport.herd.milking_share_of_herd * 100)}% in milk)
                </span>
              </div>

              {milkReport.purchases.length > 0 && (
                <p className="text-sm text-muted-foreground" role="note">
                  Buy{" "}
                  {milkReport.purchases
                    .map((purchase) => `${formatHead(purchase.count)} in-milk animal(s) in month ${purchase.month}`)
                    .join(", ")}
                  {". "}
                  {milkReport.herd.replacement_purchases_total > 0 && (
                    <>
                      Plus a replacement bridge of{" "}
                      {formatHead(milkReport.herd.replacement_purchases_total)} head over the
                      plan — culling and mortality the heifer pipeline cannot cover yet.
                    </>
                  )}
                </p>
              )}

              {(milkReport.explanations ?? []).length > 0 && (
                <div className="space-y-1.5 rounded-lg border border-border bg-muted/30 p-3">
                  <p className="text-sm font-medium">
                    Why this herd — the math behind {formatLitres(milkReport.target_daily_litres)}
                    /day
                  </p>
                  {(milkReport.explanations ?? []).map((line, index) => (
                    <p
                      key={`milk-explanation-${index}`}
                      className="text-sm text-muted-foreground"
                    >
                      {line}
                    </p>
                  ))}
                </div>
              )}

              <details className="rounded-lg border border-border p-3">
                <summary className="cursor-pointer text-sm font-medium">
                  The lactation curve the plan runs on — milk rises to a peak, then
                  falls to dry-off
                </summary>
                <div className="mt-3 overflow-x-auto">
                  <Table className="min-w-[560px]">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Month of lactation</TableHead>
                        <TableHead>L/day per animal</TableHead>
                        <TableHead>Share of lactation</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {milkReport.curve.monthly_litres.map((litres, index) => (
                        <TableRow key={`curve-${index}`}>
                          <TableCell>
                            {index + 1}
                            {index + 1 === milkReport.curve.peak_month_of_lactation
                              ? " (peak)"
                              : ""}
                          </TableCell>
                          <TableCell>{formatLitres(litres / 30.44)}</TableCell>
                          <TableCell>
                            {((litres / milkReport.curve.lactation_litres) * 100).toFixed(0)}%
                          </TableCell>
                        </TableRow>
                      ))}
                      <TableRow className="bg-muted/40">
                        <TableCell>
                          {milkReport.curve.lactation_months + 1}–
                          {Math.max(
                            milkReport.curve.lactation_months + 1,
                            Math.round(milkReport.herd.calving_interval_months),
                          )}
                        </TableCell>
                        <TableCell>0 (dry)</TableCell>
                        <TableCell>
                          late pregnancy — eating, not milking, until the next calving
                        </TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </div>
              </details>

              <div className="max-h-80 overflow-auto">
                <Table className="min-w-[860px]">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Month</TableHead>
                      <TableHead>Cal</TableHead>
                      <TableHead>Milking</TableHead>
                      <TableHead>Dry</TableHead>
                      <TableHead>Calvings</TableHead>
                      <TableHead>AI</TableHead>
                      <TableHead>L/day</TableHead>
                      <TableHead>Gap</TableHead>
                      <TableHead>₹ milk</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {milkReport.projection.map((row, index) => (
                      <TableRow key={`milk-month-${index}`}>
                        <TableCell>
                          {formatYearMonth(addMonths(milkRanStart, row.month - 1))}
                        </TableCell>
                        <TableCell>{row.calendar_month}</TableCell>
                        <TableCell>{formatHead(row.milking_does)}</TableCell>
                        <TableCell>{formatHead(row.dry_does)}</TableCell>
                        <TableCell>{formatHead(row.freshenings)}</TableCell>
                        <TableCell>{formatHead(row.ai_services)}</TableCell>
                        <TableCell className={row.meets_target ? "" : "text-warning-tint-foreground"}>
                          <span className="inline-flex items-center gap-1.5">
                            {formatLitres(row.projected_daily_litres)}
                            {!row.meets_target && (
                              <>
                                <TriangleAlert className="size-3.5 shrink-0" aria-hidden />
                                <span className="text-xs font-medium">below target</span>
                              </>
                            )}
                          </span>
                        </TableCell>
                        <TableCell>{formatLitres(row.gap_daily_litres)}</TableCell>
                        <TableCell>{formatMoney(row.projected_monthly_revenue)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              {(milkReport.notes ?? []).map((note, index) => (
                <p key={`milk-note-${index}`} className="text-sm text-muted-foreground" role="note">
                  {note}
                </p>
              ))}
            </div>
          )}
        </DataTableCard>
      )}

      <DataTableCard
        title="Saved plans"
        description="Named target lists with the assumptions they run against. Opening a plan never runs it — biology and prices change, so press Plan to re-check it against today."
        contentClassName="space-y-3"
      >
        {plansQuery.isError ? (
          <p role="alert" className="text-sm text-destructive">
            Saved plans could not be loaded. Refresh the page to retry.
          </p>
        ) : savedPlans.length === 0 ? (
          <EmptyState
            icon={CalendarCheck}
            title="No saved plans"
            description="Enter targets above, name the plan, and save it to revisit the plan each season."
          />
        ) : (
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Start</TableHead>
                <TableHead>Targets</TableHead>
                <TableHead>Notes</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {savedPlans.map((plan) => (
                <TableRow key={`saved-plan-${plan.id}`}>
                  <TableCell className="font-medium">{plan.name}</TableCell>
                  <TableCell>{formatYearMonth(plan.start_year_month)}</TableCell>
                  <TableCell>
                    {(plan.targets ?? [])
                      .map(
                        (target) =>
                          `${formatPlanCount(target.count)} ${formatPlanClass(target.animal_class, vocabulary)} ${formatYearMonth(target.year_month)}`,
                      )
                      .join("; ") || "—"}
                  </TableCell>
                  <TableCell className="max-w-64 truncate">{plan.notes || "—"}</TableCell>
                  <TableCell>{new Date(plan.updated_at).toLocaleDateString()}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Button variant="outline" size="sm" onClick={() => onOpenPlan(plan)}>
                        <FolderOpen />
                        Open
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setPendingDelete(plan)}
                        disabled={!canManage || deletePlanMutation.isPending}
                        aria-label={`Delete plan ${plan.name}`}
                      >
                        <Trash2 />
                        Delete
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {plansPage && plansPage.total > plansPage.items.length && (
          <p className="text-sm text-muted-foreground" role="note">
            Showing the first {plansPage.items.length} of {plansPage.total} saved plans.
          </p>
        )}
      </DataTableCard>

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(next) => {
          if (!next) setPendingDelete(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete plan?</DialogTitle>
            <DialogDescription>
              {pendingDelete !== null && (
                <>
                  This permanently deletes{" "}
                  <span className="font-medium text-foreground">{pendingDelete.name}</span>{" "}
                  and its saved assumptions. Runs already exported are not affected.
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={deletePlanMutation.isPending}
              onClick={() => setPendingDelete(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={deletePlanMutation.isPending || pendingDelete === null}
              onClick={() => {
                if (pendingDelete) void onDeletePlan(pendingDelete);
              }}
            >
              {deletePlanMutation.isPending ? "Deleting…" : "Delete plan"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
