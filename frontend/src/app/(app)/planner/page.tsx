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
  Download,
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
import { NlmFundingEditor, validNlmFunding } from "../simulation/components/nlm-funding-editor";
import { PaginationControls } from "@/components/pagination-controls";
import { MAX_PAGE_OFFSET, useUrlState } from "@/lib/use-url-state";

import {
  getPlanApiPlannerPlansPlanIdGet,
  getListPlansApiPlannerPlansGetQueryKey,
  getPlanDprApiPlannerPlansPlanIdDprGetUrl,
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
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError, apiFetchText } from "@/lib/api-client";
import { getActiveLanguage } from "@/lib/active-language";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { farmVocabulary, type FarmVocabulary } from "@/lib/farm-vocabulary";
import { farmToday, formatFarmDateTime, formatMoney } from "@/lib/format";
import { useLanguage, useT, type TFn } from "@/lib/i18n";
import { mapServerError } from "@/lib/server-error-phrases";
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

/** English month abbreviations for formatYearMonth (te-IN goes through Intl). */
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

/** "2027-01" → "Jan 2027" (the label farmers plan in). Telugu renders through
 * Intl te-IN like formatDate; English keeps the hand-built table so existing
 * output stays byte-identical. Invalid values pass through verbatim. */
function formatYearMonth(value: string): string {
  if (!isValidYearMonth(value)) return value;
  const [year, month] = value.split("-").map(Number);
  if (getActiveLanguage() === "te") {
    return new Intl.DateTimeFormat("te-IN", {
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(Date.UTC(year, month - 1, 1)));
  }
  return `${MONTH_NAMES[month - 1]} ${year}`;
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

/** value → label maps for the class select and report cells. English resolves
 * through the farm vocabulary (Doe, Buck, Female kid…); Telugu resolves
 * through the shared simulation.* class keys (simulation page precedent). */
function eventClassItems(
  vocabulary: FarmVocabulary,
  t: TFn,
  language: "en" | "te",
): Record<string, string> {
  const young = language === "en" ? vocabulary.young : t("simulation.token.kid");
  return {
    doe:
      language === "en"
        ? vocabulary.femaleAdult.charAt(0).toUpperCase() + vocabulary.femaleAdult.slice(1)
        : t("simulation.token.doe"),
    buck:
      language === "en"
        ? vocabulary.maleAdult.charAt(0).toUpperCase() + vocabulary.maleAdult.slice(1)
        : t("simulation.token.buck"),
    female_kid: t("simulation.event.class.femaleYoung", { young }),
    male_kid: t("simulation.event.class.maleYoung", { young }),
    female_weaner: t("simulation.event.class.femaleWeaner"),
    male_weaner: t("simulation.event.class.maleWeaner"),
    female_grower: t("simulation.event.class.femaleGrower"),
    male_grower: t("simulation.event.class.maleGrower"),
  };
}

function formatPlanClass(
  animalClass: string,
  vocabulary: FarmVocabulary,
  t: TFn,
  language: "en" | "te",
): string {
  return eventClassItems(vocabulary, t, language)[animalClass] ?? animalClass;
}

function errorMessage(t: TFn, err: unknown, fallback: string): string {
  return err instanceof ApiError ? mapServerError(t, err.detail, err.status, err.code) : fallback;
}

const ACTION_ICONS: Record<PlannerActionKind, LucideIcon> = {
  purchase: ShoppingCart,
  breed: HeartPulse,
  expect_births: Baby,
  retain: FolderOpen,
  sell: Banknote,
};

// t() is keyed by MessageKey literals, so the wire action kinds resolve
// through this table (screening page precedent).
const ACTION_KIND_KEYS: Record<PlannerActionKind, Parameters<TFn>[0]> = {
  purchase: "planner.action.purchase",
  breed: "planner.action.breed",
  expect_births: "planner.action.expectBirths",
  retain: "planner.action.retain",
  sell: "planner.action.sell",
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
  const t = useT();
  const { language } = useLanguage();
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
  // NLM capital-subsidy toggle: part of the assumptions document the plan
  // runs and saves against (finance.nlm_subsidy), kept as page state like the
  // start month so a run and a save always agree.
  const [nlmSubsidy, setNlmSubsidy] = useState(false);
  const [invalidNlmFields, setInvalidNlmFields] = useState<Set<string>>(new Set());
  const [nlmBasisVersion, setNlmBasisVersion] = useState(0);
  // A defaults response may only land while it is still the latest intent.
  const acceptDefaultsRef = useRef(true);

  const breedsQuery = useListBreedsApiSimulationDefaultsBreedsGet(
    { query: { enabled: allowed } },
  );
  const defaultsQuery = useBreedDefaultsApiSimulationDefaultsGet(
    submittedParams,
    { query: { enabled: allowed } },
  );
  useEffect(() => {
    if (defaultsQuery.data?.status === 200 && acceptDefaultsRef.current) {
      acceptDefaultsRef.current = false;
      setAssumptions(defaultsQuery.data.data);
      setInvalidNlmFields(new Set());
      setNlmBasisVersion((version) => version + 1);
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
      toast.error(t("planner.toast.presetLoading"));
      return;
    }
    // L-28: disarm the auto-defaults latch like onOpenPlan. A breed-defaults response
    // still in flight would otherwise land after this handler applied the herd snapshot
    // and silently wipe it back to presets — after the success toast. Placed after the
    // guard above: that early return fires while the initial defaults are still
    // pending, and those must keep their armed delivery.
    acceptDefaultsRef.current = false;
    const farmScope = captureFarmScope();
    try {
      const res = await snapshotQuery.refetch();
      if (res.isError || res.data?.status !== 200) {
        if (!farmScope()) return;
        toast.error(errorMessage(t, res.error, t("planner.toast.herdSnapshotFailed")));
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
      toast.success(t("planner.toast.herdApplied", { count: snap.total_head }));
    } catch (err) {
      if (!farmScope()) return;
      toast.error(errorMessage(t, err, t("planner.toast.herdSnapshotFailed")));
    }
  }

  async function onCalibrateFromFarm() {
    // L-28: disarm the auto-defaults latch like onOpenPlan, or a late breed-defaults
    // response wipes the calibration this handler just applied — after the success
    // toast.
    acceptDefaultsRef.current = false;
    const farmScope = captureFarmScope();
    try {
      const res = await calibrationQuery.refetch();
      if (res.isError || res.data?.status !== 200) {
        if (!farmScope()) return;
        toast.error(errorMessage(t, res.error, t("planner.toast.calibrateFailed")));
        return;
      }
      const calibrated = res.data.data;
      setAssumptions(calibrated.assumptions);
      setInvalidNlmFields(new Set());
      setNlmBasisVersion((version) => version + 1);
      setBasisSource("calibration");
      if (!farmScope()) return;
      toast.success(
        t("planner.toast.calibrated", { count: calibrated.evidence.length }),
      );
    } catch (err) {
      if (!farmScope()) return;
      toast.error(errorMessage(t, err, t("planner.toast.calibrateFailed")));
    }
  }

  // ----- Targets.
  const targetKeyCounter = useRef(0);
  const nextDefaultTargetMonth = () => addMonths(startMonth, 12);
  const [targets, setTargets] = useState<TargetRow[]>([]);

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
      const label = t("planner.validation.targetLabel", { index: index + 1 });
      if (!isValidYearMonth(target.year_month))
        return t("planner.validation.monthInvalid", { label });
      if (!isBefore(startMonth, target.year_month))
        return t("planner.validation.monthAfterStart", {
          label,
          start: formatYearMonth(startMonth),
        });
      if (isBefore(targetCeiling, target.year_month))
        return t("planner.validation.beyondCeiling", { label });
      if (!Number.isFinite(target.count) || target.count <= 0 || target.count > 100_000)
        return t("planner.validation.countRange", { label });
      return null;
    })
    .filter((error): error is string => error !== null);
  if (invalidNlmFields.size > 0 || (assumptions?.finance && !validNlmFunding(
      { ...assumptions.finance, nlm_subsidy: nlmSubsidy }, assumptions.herd?.does, assumptions.herd?.bucks)))
    targetErrors.push(t("simulation.nlm.invalid"));

  // ----- Backward plan run.
  const planMutation = usePlanSalesApiPlannerPlanPost();
  const [report, setReport] = useState<BackwardPlanReport | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planInputsSnapshot, setPlanInputsSnapshot] = useState<string | null>(null);

  const planInputsKey = `${startMonth}|${JSON.stringify(targets)}|${JSON.stringify(assumptions)}|${nlmSubsidy}`;
  const reportIsStale = report !== null && planInputsSnapshot !== planInputsKey;

  /** The anchored assumption document a run/save actually uses. */
  function anchoredAssumptions(): SimulationAssumptions | null {
    if (!assumptions) return null;
    return {
      ...assumptions,
      meta: { ...assumptions.meta, start_year_month: startMonth },
      finance: { ...assumptions.finance, nlm_subsidy: nlmSubsidy },
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
        const message = errorMessage(t, err, t("planner.toast.planFailed"));
        setPlanError(message);
        toast.error(message);
      }
    });
  }

  // ----- Saved plans.
  const { searchParams: plansSearchParams, getNumber: plansGetNumber, set: setPlansUrl } = useUrlState();
  const [plansOffset, setPlansOffset] = useState(() => plansGetNumber("plans_offset", 0, 0, MAX_PAGE_OFFSET));
  const [plansParamsKey, setPlansParamsKey] = useState(plansSearchParams.toString());
  if (plansSearchParams.toString() !== plansParamsKey) {
    setPlansParamsKey(plansSearchParams.toString());
    setPlansOffset(plansGetNumber("plans_offset", 0, 0, MAX_PAGE_OFFSET));
  }
  function turnPlansPage(offset: number) {
    setPlansOffset(offset);
    setPlansUrl({ plans_offset: offset || null });
  }
  const plansQuery = useListPlansApiPlannerPlansGet(
    { limit: 50, offset: plansOffset },
    { query: { enabled: allowed } },
  );
  const createPlanMutation = useCreatePlanApiPlannerPlansPost();
  const updatePlanMutation = useUpdatePlanApiPlannerPlansPlanIdPatch();
  const deletePlanMutation = useDeletePlanApiPlannerPlansPlanIdDelete();
  const [planName, setPlanName] = useState("");
  const [openPlan, setOpenPlanState] = useState<PlannerPlanOut | null>(null);
  const openPlanRef = useRef<PlannerPlanOut | null>(null);
  const openPlanGeneration = useRef(0);
  // All changes occur in event/async continuations; the ref fences older
  // conflict responses immediately, before React paints another open plan.
  function setOpenPlan(plan: PlannerPlanOut | null) {
    openPlanGeneration.current += 1;
    openPlanRef.current = plan;
    setOpenPlanState(plan);
  }
  const [conflictingPlan, setConflictingPlan] = useState<PlannerPlanOut | null>(null);
  // DELETE is permanent with no undo; the row button only stages the plan
  // here and the confirm dialog below performs it (same contract as the
  // simulation page's scenario delete).
  const [pendingDelete, setPendingDelete] = useState<PlannerPlanOut | null>(null);
  /** Plan id whose DPR download is in flight (null = idle) — disables every
   *  DPR button so two downloads never race. */
  const [dprPendingId, setDprPendingId] = useState<number | null>(null);

  const plansPage = plansQuery.data?.status === 200 ? plansQuery.data.data : null;
  const savedPlans = plansPage ? plansPage.items : [];
  useEffect(() => {
    if (plansPage && plansOffset > 0 && plansOffset >= plansPage.total) {
      const next = Math.max(0, Math.floor((plansPage.total - 1) / 50) * 50);
      // A server count can shrink after deletion; repair the URL and local
      // page only after that authoritative response arrives.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPlansOffset(next);
      setPlansUrl({ plans_offset: next || null });
    }
  }, [plansPage, plansOffset, setPlansUrl]);

  function invalidatePlans() {
    void queryClient.invalidateQueries({ queryKey: getListPlansApiPlannerPlansGetQueryKey() });
  }

  async function onSavePlan() {
    await saveAction.run(async () => {
      const farmScope = captureFarmScope();
      const generation = openPlanGeneration.current;
      const payload = anchoredAssumptions();
      const name = planName.trim();
      if (!payload || targets.length === 0 || targetErrors.length > 0) {
        toast.error(t("planner.toast.fixTargetsSave"));
        return;
      }
      if (!name) {
        toast.error(t("planner.toast.nameRequiredSave"));
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
          if (openPlanGeneration.current === generation) setOpenPlan(res.data);
          invalidatePlans();
          toast.success(t("planner.toast.saved", { name }));
        }
      } catch (err) {
        if (!farmScope()) return;
        toast.error(errorMessage(t, err, t("planner.toast.saveFailed")));
      }
    });
  }

  async function onUpdatePlan() {
    await saveAction.run(async () => {
      const farmScope = captureFarmScope();
      const generation = openPlanGeneration.current;
      if (!openPlan) return;
      const payload = anchoredAssumptions();
      if (!payload || targets.length === 0 || targetErrors.length > 0) {
        toast.error(t("planner.toast.fixTargetsUpdate"));
        return;
      }
      const name = planName.trim();
      if (!name) {
        toast.error(t("planner.toast.nameRequiredUpdate"));
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
          if (openPlanGeneration.current === generation) setOpenPlan(res.data);
          invalidatePlans();
          toast.success(t("planner.toast.updated", { name: res.data.name }));
        }
      } catch (err) {
        if (!farmScope()) return;
        if (err instanceof ApiError && err.status === 409) {
          toast.error(t("planner.toast.revisionConflict"));
          // Keep the original revision and draft until the operator chooses
          // whether to load the complete authoritative contents.
          const refreshed = await client_get_plan(openPlan.id);
          if (refreshed && farmScope() && openPlanGeneration.current === generation && openPlanRef.current?.id === openPlan.id)
            setConflictingPlan(refreshed);
          return;
        }
        toast.error(errorMessage(t, err, t("planner.toast.updateFailed")));
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
      const res = await deletePlanMutation.mutateAsync({
        planId: plan.id,
        params: { expected_revision: plan.revision },
      });
      if (res.status === 204 && farmScope()) {
        if (openPlanRef.current?.id === plan.id) {
          setOpenPlan(null);
          setPlanName("");
        }
        invalidatePlans();
        toast.success(t("planner.toast.deleted", { name: plan.name }));
      }
    } catch (err) {
      if (!farmScope()) return;
      if (err instanceof ApiError && err.status === 409) {
        // Optimistic-concurrency conflict: the row moved under us. Refresh the list so
        // the operator sees (and can retry against) the current revision instead of an
        // unretryable stale one until reload.
        toast.error(t("planner.toast.deleteConflict"));
        await invalidatePlans();
        return;
      }
      toast.error(errorMessage(t, err, t("planner.toast.deleteFailed")));
    }
  }

  /** Fetch the DPR markdown for a saved plan and hand it to the browser as a
   *  file download (the account-export pattern: object URL + anchor click). */
  async function onDownloadDpr(plan: PlannerPlanOut) {
    const farmScope = captureFarmScope();
    setDprPendingId(plan.id);
    try {
      const markdown = await apiFetchText(getPlanDprApiPlannerPlansPlanIdDprGetUrl(plan.id));
      if (!farmScope()) return;
      const blob = new Blob([markdown], { type: "text/markdown" });
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = `dpr-${plan.id}.md`;
      try {
        document.body.appendChild(anchor);
        anchor.click();
      } finally {
        anchor.remove();
        URL.revokeObjectURL(href);
      }
      toast.success(t("planner.dprDownloaded"));
    } catch (err) {
      if (!farmScope()) return;
      toast.error(errorMessage(t, err, t("planner.dprFailed")));
    } finally {
      setDprPendingId(null);
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
    setInvalidNlmFields(new Set());
    setNlmBasisVersion((version) => version + 1);
    setConflictingPlan(null);
    setNlmSubsidy(plan.assumptions.finance?.nlm_subsidy === true);
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
    toast.success(t("planner.toast.opened", { name: plan.name }));
  }


  const evaluation = report ? (report.plan.after ?? report.plan.before) : null;
  const breeds = breedsQuery.data?.status === 200 ? breedsQuery.data.data.breeds : [];
  const breedSelectItems = Object.fromEntries(
    breeds.map((b) => [b, b.replace(/_/g, " ")]),
  ) as Record<string, string>;

  const showEvaluationReport = report && evaluation;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("planner.page.title")}
        description={t("planner.page.description", { month: formatYearMonth(startMonth) })}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="planner-plan-name"
              aria-label={t("planner.form.planName")}
              placeholder={t("planner.form.planNamePlaceholder")}
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
              {t("planner.form.savePlan")}
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
                {t("planner.form.updateNamed", { name: openPlan.name })}
              </Button>
            )}
          </div>
        }
      />

      <DataTableCard
        title={t("planner.targets.title")}
        description={t("planner.targets.description")}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={addTarget}
              disabled={!assumptions || targets.length >= MAX_TARGETS}
            >
              <Plus />
              {t("planner.targets.add")}
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
              {planMutation.isPending ? t("planner.run.busy") : t("planner.run.button")}
              <Play />
            </Button>
          </div>
        }
        contentClassName="space-y-3"
      >
        {targets.length === 0 ? (
          <EmptyState
            icon={Target}
            title={t("planner.targets.emptyTitle")}
            description={t("planner.targets.emptyDescription", {
              count: 200,
              species: t("planner.speciesPlural"),
              month: formatYearMonth(addMonths(startMonth, 16)),
            })}
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
                  <Label>{t("planner.targets.saleMonth")}</Label>
                  <Input
                    type="month"
                    aria-label={t("planner.targets.saleMonth")}
                    className="w-full"
                    min={addMonths(startMonth, 1)}
                    value={target.year_month}
                    onChange={(event) => updateTarget(target.key, { year_month: event.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>{t("planner.targets.class")}</Label>
                  <Select
                    value={target.animal_class}
                    onValueChange={(v) =>
                      updateTarget(target.key, {
                        animal_class: v as PlannerTarget["animal_class"],
                      })
                    }
                    items={eventClassItems(vocabulary, t, language)}
                  >
                    <SelectTrigger aria-label={t("planner.targets.class")} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(eventClassItems(vocabulary, t, language)).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label>{t("planner.targets.count")}</Label>
                  <NumberField
                    aria-label={t("planner.targets.count")}
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
                  aria-label={t("planner.targets.removeAria", {
                    month: formatYearMonth(target.year_month),
                  })}
                >
                  <Trash2 />
                  {t("planner.targets.remove")}
                </Button>
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow>
                <TableHead>{t("planner.targets.month")}</TableHead>
                <TableHead>{t("planner.targets.class")}</TableHead>
                <TableHead>{t("planner.targets.count")}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {targets.map((target) => (
                <TableRow key={target.key}>
                  <TableCell>
                    <Input
                      type="month"
                      aria-label={t("planner.targets.saleMonth")}
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
                      items={eventClassItems(vocabulary, t, language)}
                    >
                      <SelectTrigger aria-label={t("planner.targets.class")} size="sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(eventClassItems(vocabulary, t, language)).map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    <NumberField
                      aria-label={t("planner.targets.count")}
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
                      aria-label={t("planner.targets.removeAria", {
                        month: formatYearMonth(target.year_month),
                      })}
                    >
                      <Trash2 />
                      {t("planner.targets.remove")}
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
          <CardTitle>{t("planner.basis.title")}</CardTitle>
          <CardDescription>
            {t("planner.basis.description")}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-1">
            <Label htmlFor="planner-start">{t("planner.basis.start")}</Label>
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
            <Label htmlFor="planner-breed">{t("planner.basis.breedPreset")}</Label>
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
            <Label htmlFor="planner-system">{t("planner.basis.system")}</Label>
            <Select
              value={system}
              onValueChange={(v) => {
                const next = v as typeof system;
                setSystem(next);
                loadBreedDefaults({ breed: submittedParams.breed, system: next });
              }}
              items={{
                stall_fed: t("planner.system.stallFed"),
                semi_intensive: t("planner.system.semiIntensive"),
              }}
            >
              <SelectTrigger id="planner-system" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="stall_fed">{t("planner.system.stallFed")}</SelectItem>
                <SelectItem value="semi_intensive">{t("planner.system.semiIntensive")}</SelectItem>
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
              {snapshotQuery.isFetching ? t("common.loading") : t("planner.useMyHerd")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void onCalibrateFromFarm()}
              disabled={!canCalibrate || calibrationQuery.isFetching}
              title={
                canCalibrate
                  ? undefined
                  : t("planner.basis.calibrateDenied")
              }
            >
              {calibrationQuery.isFetching
                ? t("planner.basis.calibrating")
                : t("planner.basis.useFarmRecords")}
            </Button>
          </div>
          <div className="flex items-start gap-2 sm:col-span-2 lg:col-span-4">
            <Checkbox
              id="planner-nlm-subsidy"
              checked={nlmSubsidy}
              onCheckedChange={(checked) => {
                setNlmSubsidy(checked === true);
                if (checked !== true) {
                  setInvalidNlmFields(new Set());
                  setAssumptions((previous) => previous ? { ...previous, finance: {
                    ...previous.finance, nlm_approved_subsidy_amount: null, nlm_subsidy_receipts: [],
                  }} : previous);
                }
              }}
            />
            <div className="space-y-1">
              <Label htmlFor="planner-nlm-subsidy" className="font-normal">
                {t("planner.nlmSubsidy")}
              </Label>
              <p className="text-xs text-muted-foreground">{t("planner.nlmSubsidyHelp")}</p>
            </div>
          </div>
          {nlmSubsidy && assumptions?.finance && <NlmFundingEditor
            key={`${basisSource}:${submittedParams.breed}:${submittedParams.system}:${nlmBasisVersion}`}
            prefix="planner" value={{ ...assumptions.finance, nlm_subsidy: true }}
            onChange={(patch) => {
              acceptDefaultsRef.current = false;
              setAssumptions((previous) => previous ? { ...previous, finance: { ...previous.finance, ...patch } } : previous);
            }}
            onValidityChange={(key, valid) => setInvalidNlmFields((previous) => {
              if (previous.has(key) === !valid) return previous;
              const next = new Set(previous);
              if (valid) next.delete(key); else next.add(key);
              return next;
            })} />}
          <p className="text-sm text-muted-foreground sm:col-span-2 lg:col-span-4" role="note">
            {basisSource === "preset" && t("planner.basis.note.preset")}
            {basisSource === "herd" &&
              t("planner.basis.note.herd", { breed: submittedParams.breed.replace(/_/g, " ") })}
            {basisSource === "calibration" && t("planner.basis.note.calibration")}
            {basisSource === "saved" && t("planner.basis.note.saved")}{" "}
            {t("planner.basis.note.moreControlPrefix")}{" "}
            <Link className="underline" href="/simulation">
              {t("planner.basis.note.simulationLink")}
            </Link>{" "}
            {t("planner.basis.note.moreControlSuffix")}
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
                {report.plan.gaps_closed
                  ? t("planner.eval.feasible")
                  : t("planner.eval.cannotClose")}
                {reportIsStale ? t("planner.eval.staleSuffix") : ""}
              </CardTitle>
              <CardDescription>
                {t("planner.eval.description", {
                  shortfall: formatPlanCount(evaluation.total_shortfall),
                  purchases: report.plan.recommended_purchases.length,
                  npvBefore: formatMoney(report.plan.before.npv),
                  npvAfterSuffix: report.plan.after
                    ? t("planner.eval.npvAfterSuffix", { npv: formatMoney(report.plan.after.npv) })
                    : "",
                })}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {/* Cash-floor figures the evaluation already carries. For a
               * loan-taking farmer the minimum-cash month is the most
               * decision-relevant number in the payload — the simulation page
               * shows the same trio for its runs, while the planner used to
               * print only NPV/shortfall/purchases. */}
              <dl className="mb-4 grid gap-2 sm:grid-cols-2">
                <div className="flex items-baseline justify-between gap-3 rounded-lg border p-3">
                  <dt className="text-sm text-muted-foreground">
                    {t("planner.eval.minimumCash", { month: evaluation.minimum_cash_month })}
                  </dt>
                  <dd
                    className={`text-sm font-medium tabular-nums ${
                      evaluation.minimum_cash_balance < 0 ? "text-destructive" : ""
                    }`}
                  >
                    {formatMoney(evaluation.minimum_cash_balance)}
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-3 rounded-lg border p-3">
                  <dt className="text-sm text-muted-foreground">
                    {t("planner.eval.workingCapital")}
                  </dt>
                  <dd
                    className={`text-sm font-medium tabular-nums ${
                      evaluation.additional_working_capital_required > 0 ? "text-destructive" : ""
                    }`}
                  >
                    {formatMoney(evaluation.additional_working_capital_required)}
                  </dd>
                </div>
              </dl>
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
                        {formatPlanClass(fill.animal_class, vocabulary, t, language)}
                      </p>
                      <dl className="space-y-1 text-sm">
                        <div className="flex items-baseline justify-between gap-3">
                          <dt className="text-muted-foreground">{t("planner.eval.target")}</dt>
                          <dd className="tabular-nums">{formatPlanCount(fill.requested)}</dd>
                        </div>
                        <div className="flex items-baseline justify-between gap-3">
                          <dt className="text-muted-foreground">{t("planner.eval.filledNow")}</dt>
                          <dd className="tabular-nums">
                            {fill.met ? "✓" : "⚠"} {formatPlanCount(fill.filled)}
                          </dd>
                        </div>
                        <div className="flex items-baseline justify-between gap-3">
                          <dt className="text-muted-foreground">{t("planner.eval.withPurchases")}</dt>
                          <dd className="tabular-nums">
                            {after ? `${after.met ? "✓" : "⚠"} ${formatPlanCount(after.filled)}` : "—"}
                          </dd>
                        </div>
                        <div className="flex items-baseline justify-between gap-3">
                          <dt className="text-muted-foreground">₹/head</dt>
                          <dd className="tabular-nums">{formatMoney(fill.price_per_head)}</dd>
                        </div>
                        <div className="flex items-baseline justify-between gap-3">
                          <dt className="text-muted-foreground">{t("planner.eval.pFull")}</dt>
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
                    <TableHead>{t("planner.eval.month")}</TableHead>
                    <TableHead>{t("planner.eval.class")}</TableHead>
                    <TableHead>{t("planner.eval.target")}</TableHead>
                    <TableHead>{t("planner.eval.filledNow")}</TableHead>
                    <TableHead>{t("planner.eval.withPurchases")}</TableHead>
                    <TableHead>₹/head</TableHead>
                    <TableHead>{t("planner.eval.pFull")}</TableHead>
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
                        <TableCell>{formatPlanClass(fill.animal_class, vocabulary, t, language)}</TableCell>
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
              <CardTitle>{t("planner.actions.title")}</CardTitle>
              <CardDescription>
                {t("planner.actions.description")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {report.actions.map((action, index) => {
                const Icon = ACTION_ICONS[action.kind];
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
                          {formatYearMonth(action.year_month)} · {t(ACTION_KIND_KEYS[action.kind])}:
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
              <CardTitle>{t("planner.chain.title")}</CardTitle>
              <CardDescription>
                {t("planner.chain.description")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {report.chains.map((chain, index) => (
                <div
                  key={index}
                  className="space-y-2 rounded-lg border border-border p-3"
                >
                  <p className="text-sm font-medium">
                    {t("planner.chain.summary", {
                      count: formatPlanCount(chain.count),
                      cls: formatPlanClass(chain.animal_class, vocabulary, t, language),
                      month: formatYearMonth(chain.year_month),
                    })}{" "}
                    {chain.achievable ? (
                      <span className="text-muted-foreground">{t("planner.chain.achievable")}</span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-warning-tint-foreground">
                        <TriangleAlert className="size-3.5" aria-hidden />
                        {t("planner.chain.notAchievable")}
                      </span>
                    )}
                  </p>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{t("planner.chain.when")}</TableHead>
                        <TableHead>{t("planner.chain.howMany")}</TableHead>
                        <TableHead>{t("planner.chain.what")}</TableHead>
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
            title={t("planner.stage.title")}
            description={t("planner.stage.description")}
            contentClassName="space-y-3"
          >
            {/* Below md the 16-column stage plan becomes a card per month —
             * a 1,100px matrix cannot pan on a phone; the month's herd shape
             * and flows read as label/value pairs instead. */}
            <div className="space-y-2 md:hidden">
              {report.stage_plan.map((row) => (
                <div key={row.year_month} className="space-y-2 rounded-xl border bg-card p-3 shadow-xs">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium">{formatYearMonth(row.year_month)}</span>
                    <span className="tabular-nums font-medium">
                      {t("planner.stage.headCount", { count: formatHead(row.total_head) })}
                    </span>
                  </div>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                    {[
                      [t("planner.stage.femaleKids"), row.female_kids],
                      [t("planner.stage.maleKids"), row.male_kids],
                      [t("planner.stage.femaleWeaners"), row.female_weaners],
                      [t("planner.stage.maleWeaners"), row.male_weaners],
                      [t("planner.stage.femaleGrowers"), row.female_growers],
                      [t("planner.stage.maleGrowers"), row.male_growers],
                      [t("planner.stage.openDoes"), row.open_does],
                      [t("planner.stage.pregnant"), row.pregnant_does],
                      [t("planner.stage.lactating"), row.lactating_does],
                      [t("planner.stage.bucks"), row.bucks],
                    ].map(([label, value]) => (
                      <div key={label as string} className="flex items-baseline justify-between gap-2">
                        <dt className="text-muted-foreground">{label}</dt>
                        <dd className="tabular-nums">{formatHead(value as number)}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="text-xs text-muted-foreground tabular-nums">
                    {t("planner.stage.flows", {
                      births: formatHead(row.births),
                      deaths: formatHead(row.deaths),
                      culls: formatHead(row.culls_head),
                      sales: formatHead(row.sales_head),
                      purchases: formatHead(row.purchases_head),
                    })}
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
                  {t("planner.stage.caption")}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("planner.stage.month")}</TableHead>
                    <TableHead>{t("planner.stage.femaleKids")}</TableHead>
                    <TableHead>{t("planner.stage.maleKids")}</TableHead>
                    <TableHead>{t("planner.stage.femaleWeaners")}</TableHead>
                    <TableHead>{t("planner.stage.maleWeaners")}</TableHead>
                    <TableHead>{t("planner.stage.femaleGrowers")}</TableHead>
                    <TableHead>{t("planner.stage.maleGrowers")}</TableHead>
                    <TableHead>{t("planner.stage.openDoes")}</TableHead>
                    <TableHead>{t("planner.stage.pregnant")}</TableHead>
                    <TableHead>{t("planner.stage.lactating")}</TableHead>
                    <TableHead>{t("planner.stage.bucks")}</TableHead>
                    <TableHead>{t("planner.stage.total")}</TableHead>
                    <TableHead>{t("planner.stage.born")}</TableHead>
                    <TableHead>{t("planner.stage.died")}</TableHead>
                    <TableHead>{t("planner.stage.culled")}</TableHead>
                    <TableHead>{t("planner.stage.sold")}</TableHead>
                    <TableHead>{t("planner.stage.bought")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.stage_plan.map((row) => (
                    <TableRow key={row.year_month}>
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
                  <CardTitle>{t("planner.notes.title")}</CardTitle>
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
        title={t("planner.saved.title")}
        description={t("planner.saved.description")}
        contentClassName="space-y-3"
      >
        {plansQuery.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {t("planner.saved.loadFailed")}
          </p>
        ) : savedPlans.length === 0 ? (
          <EmptyState
            icon={CalendarCheck}
            title={t("planner.saved.emptyTitle")}
            description={t("planner.saved.emptyDescription")}
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
                    {/* L-29: updated_at is a UTC datetime;
                     * render it in the farm timezone and active locale, not the
                     * browser's. */}
                    {t("planner.saved.updatedAt", { datetime: formatFarmDateTime(plan.updated_at) })}
                  </span>
                </div>
                {plan.valid === false && <p role="note" className="text-sm text-destructive">{plan.validation_error || t("planner.saved.invalid")}</p>}
                <p className="text-xs text-muted-foreground">
                  {t("planner.saved.startsAt", { month: formatYearMonth(plan.start_year_month) })}
                </p>
                <p className="text-xs text-muted-foreground">
                  {(plan.targets ?? [])
                    .map(
                      (target) =>
                        `${formatPlanCount(target.count)} ${formatPlanClass(target.animal_class, vocabulary, t, language)} ${formatYearMonth(target.year_month)}`,
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
                    disabled={plan.valid === false}
                  >
                    <FolderOpen />
                    {t("planner.saved.open")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-11 px-4"
                    onClick={() => void onDownloadDpr(plan)}
                    disabled={dprPendingId !== null || plan.valid === false}
                    aria-label={t("planner.downloadDprFor", { name: plan.name })}
                  >
                    <Download />
                    {dprPendingId === plan.id
                      ? t("planner.downloadingDpr")
                      : t("planner.downloadDpr")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-11 px-4"
                    onClick={() => setPendingDelete(plan)}
                    disabled={!canManage || deletePlanMutation.isPending}
                    aria-label={t("planner.saved.deleteAria", { name: plan.name })}
                  >
                    <Trash2 />
                    {t("planner.saved.delete")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
          <div className="hidden md:block">
          <Table className="min-w-[720px]">
            <TableHeader>
              <TableRow>
                <TableHead>{t("planner.saved.colName")}</TableHead>
                <TableHead>{t("planner.saved.colStart")}</TableHead>
                <TableHead>{t("planner.saved.colTargets")}</TableHead>
                <TableHead>{t("planner.saved.colNotes")}</TableHead>
                <TableHead>{t("planner.saved.colUpdated")}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {savedPlans.map((plan) => (
                <TableRow key={plan.id}>
                  <TableCell className="font-medium">{plan.name}
                    {plan.valid === false && <p className="text-xs text-destructive">{plan.validation_error || t("planner.saved.invalid")}</p>}
                  </TableCell>
                  <TableCell>{formatYearMonth(plan.start_year_month)}</TableCell>
                  <TableCell>
                    {(plan.targets ?? [])
                      .map(
                        (target) =>
                          `${formatPlanCount(target.count)} ${formatPlanClass(target.animal_class, vocabulary, t, language)} ${formatYearMonth(target.year_month)}`,
                      )
                      .join("; ") || "—"}
                  </TableCell>
                  <TableCell className="max-w-64 truncate">{plan.notes || "—"}</TableCell>
                  {/* L-29: farm timezone + active locale
                   * instead of the browser locale. */}
                  <TableCell>{formatFarmDateTime(plan.updated_at)}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Button variant="outline" size="sm" disabled={plan.valid === false} onClick={() => onOpenPlan(plan)}>
                        <FolderOpen />
                        {t("planner.saved.open")}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void onDownloadDpr(plan)}
                        disabled={dprPendingId !== null || plan.valid === false}
                        aria-label={t("planner.downloadDprFor", { name: plan.name })}
                      >
                        <Download />
                        {dprPendingId === plan.id
                          ? t("planner.downloadingDpr")
                          : t("planner.downloadDpr")}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setPendingDelete(plan)}
                        disabled={!canManage || deletePlanMutation.isPending}
                        aria-label={t("planner.saved.deleteAria", { name: plan.name })}
                      >
                        <Trash2 />
                        {t("planner.saved.delete")}
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
        {plansPage && <PaginationControls total={plansPage.total} limit={plansPage.limit}
          offset={plansOffset} onOffsetChange={turnPlansPage} disabled={plansQuery.isFetching} />}
      </DataTableCard>

      <Dialog open={conflictingPlan !== null} onOpenChange={(open) => !open && setConflictingPlan(null)}>
        <DialogContent><DialogHeader>
          <DialogTitle>{t("planner.conflict.title")}</DialogTitle>
          <DialogDescription>{t("planner.conflict.description")}</DialogDescription>
        </DialogHeader>
          <p>{t("planner.conflict.latest", { name: conflictingPlan?.name ?? "", revision: conflictingPlan?.revision ?? 0 })}</p>
          <p className="text-sm text-muted-foreground">{(conflictingPlan?.targets ?? []).map((target) =>
            `${formatPlanCount(target.count)} ${formatPlanClass(target.animal_class, vocabulary, t, language)} ${formatYearMonth(target.year_month)}`).join("; ")}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConflictingPlan(null)}>{t("planner.conflict.keepDraft")}</Button>
            <Button disabled={conflictingPlan?.valid === false} onClick={() => conflictingPlan && onOpenPlan(conflictingPlan)}>{t("planner.conflict.loadLatest")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(next) => {
          if (!next) setPendingDelete(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("planner.delete.title")}</DialogTitle>
            <DialogDescription>
              {pendingDelete !== null && (
                <span>
                  {t("planner.delete.intro")}
                  <span className="font-medium text-foreground">{pendingDelete.name}</span>
                  {t("planner.delete.outro")}
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
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={deletePlanMutation.isPending}
              onClick={() => {
                if (pendingDelete) void onDeletePlan(pendingDelete);
              }}
            >
              {deletePlanMutation.isPending ? t("planner.delete.busy") : t("planner.delete.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function PlannerPage() {
  const perms = usePermissions();
  const t = useT();
  return (
    <PermissionGate
      perms={perms}
      perm="simulation.view"
      label={t("planner.page.title")}
      description={t("planner.gate.description")}
      cards={2}
    >
      <PlannerPageContent perms={perms} />
    </PermissionGate>
  );
}
