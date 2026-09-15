"use client";

/**
 * Planner (Business): target-based backward planning. The farmer states what
 * must be sold and when ("200 goats in Jan 2027"), and the backend works the
 * biology backward — breeding, gestation, mortality, culling, growth stages —
 * to answer with feasibility, a month-by-month stage plan, dated actions
 * (buy / breed / expect births / sell) and each target's requirement chain.
 * Plans can be saved per farm and re-run any time.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  Baby,
  Banknote,
  CalendarCheck,
  Database,
  FolderOpen,
  HeartPulse,
  Play,
  Plus,
  Save,
  ShoppingCart,
  Target,
  Trash2,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
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
  usePlanSalesApiPlannerPlanPost,
  useUpdatePlanApiPlannerPlansPlanIdPatch,
} from "@/api/generated/endpoints";
import type {
  BackwardPlanReport,
  PlannerActionKind,
  PlannerPlanOut,
  PlannerTarget,
  SimulationAssumptions,
} from "@/api/generated/models";
import { BreedDefaultsApiSimulationDefaultsGetSystem } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
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
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api-client";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { farmVocabulary, type FarmVocabulary } from "@/lib/farm-vocabulary";
import { farmToday, formatMoney } from "@/lib/format";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
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
  // Stryker disable LogicalOperator, ConditionalExpression: for null/undefined the Number.isFinite arm is also true (it does not coerce), so the null/undefined operand variants never decide anything the isFinite arm does not
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : value.toFixed(1);
  // Stryker restore LogicalOperator, ConditionalExpression
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
 * back to the stored value mid-edit. Exported for direct mutation testing. */
export function NumberField(
  props: ComponentProps<typeof Input> & { onCommitNumber: (n: number) => void },
) {
  const { onCommitNumber, ...inputProps } = props;
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <Input
      type="number"
      {...inputProps}
      // Stryker disable next-line StringLiteral: the page's only NumberField usage passes the numeric target count, so the nullish arm is unreachable from app surfaces (the exported unit test still pins the intent)
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

function PlannerPageContent({ perms }: { perms: PermissionsState }) {
  const { can } = perms;
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

  const vocabulary = farmVocabulary;
  const defaultBreed = "osmanabadi";

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

  const breedsQuery = useListBreedsApiSimulationDefaultsBreedsGet(
    // Stryker disable next-line ObjectLiteral: PermissionGate refuses to mount this page without simulation.view, so `allowed` is always true by the time this hook runs
    { query: { enabled: allowed } },
  );
  const defaultsQuery = useBreedDefaultsApiSimulationDefaultsGet(
    submittedParams,
    // Stryker disable next-line ObjectLiteral: PermissionGate refuses to mount this page without simulation.view, so `allowed` is always true by the time this hook runs
    { query: { enabled: allowed } },
  );
  useEffect(() => {
    if (defaultsQuery.data?.status === 200 && acceptDefaultsRef.current) {
      // Stryker disable next-line BooleanLiteral: same-key redelivery only happens via remount (a fresh ref re-accepts anyway) or loadBreedDefaults (which re-arms first); refetchOnWindowFocus is off app-wide, so no path re-delivers defaults without re-arming
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
    // Stryker disable BlockStatement, BooleanLiteral, ConditionalExpression, StringLiteral, CallExpression: react-query v5 refetch() resolves with an error result instead of rejecting (throwOnError stays false), so this catch is unreachable defense-in-depth; emptying the try falls through to the same suppressed continuation
    try {
      const res = await snapshotQuery.refetch();
      // Stryker disable next-line OptionalChaining: the isError arm short-circuits whenever data is undefined, so the optional chain is only evaluated with data present
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
      // Stryker disable BlockStatement, BooleanLiteral, ConditionalExpression, StringLiteral: react-query v5 refetch() resolves with an error result instead of rejecting (throwOnError stays false), so this catch is unreachable defense-in-depth
    } catch (err) {
      if (!farmScope()) return;
      toast.error(errorMessage(err, "Could not load the herd snapshot."));
    }
    // Stryker restore BlockStatement, BooleanLiteral, ConditionalExpression, StringLiteral, CallExpression
  }

  async function onCalibrateFromFarm() {
    const farmScope = captureFarmScope();
    // Stryker disable BlockStatement, BooleanLiteral, ConditionalExpression, StringLiteral, CallExpression: react-query v5 refetch() resolves with an error result instead of rejecting (throwOnError stays false), so this catch is unreachable defense-in-depth; emptying the try falls through to the same suppressed continuation
    try {
      const res = await calibrationQuery.refetch();
      // Stryker disable next-line OptionalChaining: the isError arm short-circuits whenever data is undefined, so the optional chain is only evaluated with data present
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
      // Stryker disable BlockStatement, BooleanLiteral, ConditionalExpression, StringLiteral: react-query v5 refetch() resolves with an error result instead of rejecting (throwOnError stays false), so this catch is unreachable defense-in-depth
    } catch (err) {
      if (!farmScope()) return;
      toast.error(errorMessage(err, "Could not calibrate from farm records."));
    }
    // Stryker restore BlockStatement, BooleanLiteral, ConditionalExpression, StringLiteral, CallExpression
  }

  // ----- Targets.
  const targetKeyCounter = useRef(0);
  const nextDefaultTargetMonth = () => addMonths(startMonth, 12);
  const [targets, setTargets] = useState<TargetRow[]>([]);

  // Stryker disable next-line UpdateOperator: React list keys only need uniqueness, and a monotonically decreasing counter is exactly as unique as an increasing one
  const nextTargetKey = () => `target-${targetKeyCounter.current++}`;

  function addTarget() {
    setTargets((prev) => [
      ...prev,
      {
        key: nextTargetKey(),
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
    // Stryker disable next-line ConditionalExpression: Plan/Save/Update are reachable only through buttons that stay disabled until the preset lands, and assumptions is never reset to null afterwards — the null arm is unreachable defense-in-depth
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
      // Stryker disable ConditionalExpression, LogicalOperator: the Plan button is disabled for exactly these states on the same render, so the guard is unreachable defense-in-depth
      if (!payload || targets.length === 0 || targetErrors.length > 0) return;
      // Stryker restore ConditionalExpression, LogicalOperator
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

  // ----- Saved plans.
  const plansQuery = useListPlansApiPlannerPlansGet(
    { limit: 50, offset: 0 },
    // Stryker disable next-line ObjectLiteral: PermissionGate refuses to mount this page without simulation.view, so `allowed` is always true by the time this hook runs
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
      // Stryker disable ConditionalExpression, LogicalOperator, BlockStatement, CallExpression, StringLiteral: the Save button is disabled for exactly these states (including the trimmed-empty name), so both branches are unreachable defense-in-depth
      if (!payload || targets.length === 0 || targetErrors.length > 0) {
        toast.error("Fix the targets before saving the plan.");
        return;
      }
      if (!name) {
        toast.error("Give the plan a name before saving.");
        return;
      }
      // Stryker restore ConditionalExpression, LogicalOperator, BlockStatement, CallExpression, StringLiteral
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
      // Stryker disable next-line ConditionalExpression: the Update button only renders while openPlan is set, so the guard is unreachable defense-in-depth
      if (!openPlan) return;
      const payload = anchoredAssumptions();
      // Stryker disable ConditionalExpression, LogicalOperator, BlockStatement, CallExpression, StringLiteral: the Update button is disabled for exactly these states on the same render, so the branch is unreachable defense-in-depth
      if (!payload || targets.length === 0 || targetErrors.length > 0) {
        toast.error("Fix the targets before updating the saved plan.");
        return;
      }
      // Stryker restore ConditionalExpression, LogicalOperator, BlockStatement, CallExpression, StringLiteral
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
    // Stryker disable BlockStatement: emptying either block returns undefined instead of null, and the caller's truthiness check treats both identically
    try {
      const res = await getPlanApiPlannerPlansPlanIdGet(planId);
      return res.status === 200 ? res.data : null;
    } catch {
      return null;
    }
    // Stryker restore BlockStatement
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
        key: nextTargetKey(),
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
    toast.success(`Opened “${plan.name}” — press Plan to re-run it against today's biology.`);
  }


  const evaluation = report ? (report.plan.after ?? report.plan.before) : null;
  const breeds = breedsQuery.data?.status === 200 ? breedsQuery.data.data.breeds : [];
  const breedSelectItems = Object.fromEntries(
    breeds.map((b) => [b, b.replace(/_/g, " ")]),
  ) as Record<string, string>;

  // Stryker disable next-line LogicalOperator: the mutant dereferences whichever half is missing and crashes the render with a TypeError that escapes into the test runner itself (Stryker records RuntimeError, not a kill); the both-null test pins the real guard
  const showEvaluationReport = report && evaluation;

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
                  targetErrors.length > 0 ||
                  // Mirrors the Save condition: an empty plan name is caught
                  // at submit with a toast — don't offer the dead click
                  // (RT-P2-6).
                  !planName.trim()
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
          <>
          {/* Below md each target becomes a card with stacked fields — the
           * editor must stay fully usable on the phone, not pan inside a
           * 720px table. */}
          <div className="space-y-2 md:hidden">
            {targets.map((target) => (
              <div key={target.key} className="space-y-3 rounded-xl border bg-card p-3 shadow-xs">
                <div className="space-y-1.5">
                  <Label>Sale month</Label>
                  <Input
                    type="month"
                    aria-label="Sale month"
                    className="w-full"
                    min={addMonths(startMonth, 1)}
                    value={target.year_month}
                    onChange={(event) => updateTarget(target.key, { year_month: event.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Class</Label>
                  <Select
                    value={target.animal_class}
                    onValueChange={(v) =>
                      updateTarget(target.key, {
                        animal_class: v as PlannerTarget["animal_class"],
                      })
                    }
                    items={eventClassItems(vocabulary)}
                  >
                    <SelectTrigger aria-label="Class" className="w-full">
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
                </div>
                <div className="space-y-1.5">
                  <Label>Count</Label>
                  <NumberField
                    aria-label="Count"
                    className="w-full"
                    min={1}
                    max={100_000}
                    step={1}
                    value={target.count}
                    onCommitNumber={(n) => updateTarget(target.key, { count: n })}
                  />
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-11 px-4"
                  onClick={() => removeTarget(target.key)}
                  aria-label={`Remove target ${formatYearMonth(target.year_month)}`}
                >
                  <Trash2 />
                  Remove
                </Button>
              </div>
            ))}
          </div>
          <div className="hidden md:block">
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
          </div>
          </>
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
            <Link className="underline" href="/simulation">
              Simulation
            </Link>{" "}
            and calibrate there first.
          </p>
        </CardContent>
      </Card>

      {planError && (
        <p role="alert" className="text-sm text-destructive">
          {planError}
        </p>
      )}

      {showEvaluationReport && (
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
              {/* Below md the 7-column evaluation becomes a card per target. */}
              <div className="space-y-2 md:hidden">
                {report.plan.before.targets.map((fill, index) => {
                  const after = report.plan.after?.targets[index];
                  const risk = report.plan.probabilities?.[index];
                  const echo = report.targets_echo[index];
                  return (
                    <div key={index} className="space-y-1 rounded-xl border bg-card p-3 shadow-xs">
                      <p className="font-medium">
                        {echo ? formatYearMonth(echo.year_month) : fill.month}
                        {" · "}
                        {formatPlanClass(fill.animal_class, vocabulary)}
                      </p>
                      <dl className="space-y-1 text-sm">
                        <div className="flex items-baseline justify-between gap-3">
                          <dt className="text-muted-foreground">Target</dt>
                          <dd className="tabular-nums">{formatPlanCount(fill.requested)}</dd>
                        </div>
                        <div className="flex items-baseline justify-between gap-3">
                          <dt className="text-muted-foreground">Filled now</dt>
                          <dd className="tabular-nums">
                            {fill.met ? "✓" : "⚠"} {formatPlanCount(fill.filled)}
                          </dd>
                        </div>
                        <div className="flex items-baseline justify-between gap-3">
                          <dt className="text-muted-foreground">With purchases</dt>
                          <dd className="tabular-nums">
                            {after ? `${after.met ? "✓" : "⚠"} ${formatPlanCount(after.filled)}` : "—"}
                          </dd>
                        </div>
                        <div className="flex items-baseline justify-between gap-3">
                          <dt className="text-muted-foreground">₹/head</dt>
                          <dd className="tabular-nums">{formatMoney(fill.price_per_head)}</dd>
                        </div>
                        <div className="flex items-baseline justify-between gap-3">
                          <dt className="text-muted-foreground">P(full)</dt>
                          <dd className="tabular-nums">
                            {risk ? `${Math.round(risk.p_full * 100)}%` : "—"}
                          </dd>
                        </div>
                      </dl>
                    </div>
                  );
                })}
              </div>
              <div className="hidden md:block">
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
                      <TableRow key={index}>
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
              </div>
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
                // Stryker disable next-line OptionalChaining: inside the report!==null render branch, report cannot be null at this node
                const missed = action.year_month < (report?.start_year_month ?? startMonth);
                return (
                  <div
                    key={index}
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
                  key={index}
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
                        <TableRow key={stepIndex}>
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
            {/* Below md the 16-column stage plan becomes a card per month —
             * a 1,100px matrix cannot pan on a phone; the month's herd shape
             * and flows read as label/value pairs instead. */}
            <div className="space-y-2 md:hidden">
              {report.stage_plan.map((row) => (
                <div key={row.month} className="space-y-2 rounded-xl border bg-card p-3 shadow-xs">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium">{formatYearMonth(row.year_month)}</span>
                    <span className="tabular-nums font-medium">
                      {formatHead(row.total_head)} head
                    </span>
                  </div>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                    {[
                      [`F ${vocabulary.young}s`, row.female_kids],
                      [`M ${vocabulary.young}s`, row.male_kids],
                      ["F weaners", row.female_weaners],
                      ["M weaners", row.male_weaners],
                      ["F growers", row.female_growers],
                      ["M growers", row.male_growers],
                      [`Open ${vocabulary.femaleAdultPlural}`, row.open_does],
                      ["Pregnant", row.pregnant_does],
                      ["Lactating", row.lactating_does],
                      [vocabulary.maleAdult + "s", row.bucks],
                    ].map(([label, value]) => (
                      <div key={label as string} className="flex items-baseline justify-between gap-2">
                        <dt className="text-muted-foreground">{label}</dt>
                        <dd className="tabular-nums">{formatHead(value as number)}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="text-xs text-muted-foreground tabular-nums">
                    Born {formatHead(row.births)} · Died {formatHead(row.deaths)} · Culled{" "}
                    {formatHead(row.culls_head)} · Sold {formatHead(row.sales_head)} · Bought{" "}
                    {formatHead(row.purchases_head)}
                  </p>
                </div>
              ))}
            </div>
            <div className="max-h-96 overflow-auto hidden md:block">
              <Table className="min-w-[1100px]">
                {/* Sixteen columns of heads and flows need a summary for
                 * screen readers — the card title alone does not explain the
                 * matrix. sr-only: the card description already says it
                 * visually. */}
                <TableCaption className="sr-only">
                  End-of-month herd head by stage for every plan month, followed by that
                  month&apos;s births, deaths, culls, sales and purchases.
                </TableCaption>
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
                    <TableRow key={row.month}>
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

          {((): React.ReactNode => {
            const notes: string[] = report.notes ?? [];
            if (notes.length === 0) return null;
            return (
              <Card>
                <CardHeader>
                  <CardTitle>Notes</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {notes.map((note, index) => (
                    <p key={index} className="text-sm text-muted-foreground" role="note">
                      {note}
                    </p>
                  ))}
                </CardContent>
              </Card>
            );
          })()}
        </>
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
          <>
          {/* Below md the 6-column plans table becomes a card per plan —
           * Open/Delete stay 44px touch targets. */}
          <div className="space-y-2 md:hidden">
            {savedPlans.map((plan) => (
              <div key={plan.id} className="space-y-1.5 rounded-xl border bg-card p-3 shadow-xs">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="font-medium">{plan.name}</span>
                  <span className="text-xs text-muted-foreground">
                    Updated {new Date(plan.updated_at).toLocaleDateString()}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground">
                  Starts {formatYearMonth(plan.start_year_month)}
                </p>
                <p className="text-xs text-muted-foreground">
                  {(plan.targets ?? [])
                    .map(
                      (target) =>
                        `${formatPlanCount(target.count)} ${formatPlanClass(target.animal_class, vocabulary)} ${formatYearMonth(target.year_month)}`,
                    )
                    .join("; ") || "—"}
                </p>
                {plan.notes && (
                  <p className="text-xs text-muted-foreground">{plan.notes}</p>
                )}
                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-11 px-4"
                    onClick={() => onOpenPlan(plan)}
                  >
                    <FolderOpen />
                    Open
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-11 px-4"
                    onClick={() => setPendingDelete(plan)}
                    disabled={!canManage || deletePlanMutation.isPending}
                    aria-label={`Delete plan ${plan.name}`}
                  >
                    <Trash2 />
                    Delete
                  </Button>
                </div>
              </div>
            ))}
          </div>
          <div className="hidden md:block">
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
                <TableRow key={plan.id}>
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
          </div>
          </>
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
          // Stryker disable next-line BooleanLiteral, ConditionalExpression: the open state is driven programmatically via pendingDelete, so onOpenChange only ever reports dismissal (next=false)
          if (!next) setPendingDelete(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete plan?</DialogTitle>
            <DialogDescription>
              {pendingDelete !== null && (
                <span>
                  {`This permanently deletes `}
                  <span className="font-medium text-foreground">{pendingDelete.name}</span>
                  {` and its saved assumptions. Runs already exported are not affected.`}
                </span>
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
              disabled={deletePlanMutation.isPending}
              onClick={() => {
                // Stryker disable next-line ConditionalExpression: the confirm button renders only inside the dialog, which is open exactly when pendingDelete is non-null
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

export default function PlannerPage() {
  const perms = usePermissions();
  return (
    <PermissionGate
      perms={perms}
      perm="simulation.view"
      label="Planner"
      description="Set sale targets by month; the planner works backward to today's to-do list."
      cards={2}
    >
      <PlannerPageContent perms={perms} />
    </PermissionGate>
  );
}
