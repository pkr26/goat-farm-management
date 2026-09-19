"use client";

/**
 * Simulation: bio-economic herd projection. Generic assumptions editor driven
 * by the backend defaults payload (rendered from whatever keys are present),
 * ad-hoc/scenario runs, viability results and saved-scenario management.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  Beef,
  Building2,
  CalendarClock,
  ChartColumn,
  Droplets,
  Database,
  FolderOpen,
  Gauge,
  GitCompareArrows,
  HandCoins,
  IndianRupee,
  Landmark,
  Percent,
  PiggyBank,
  Play,
  Plus,
  RefreshCw,
  Save,
  Scale,
  ShieldAlert,
  Sigma,
  TrendingUp,
  TriangleAlert,
  Wallet,
  Wheat,
  Repeat,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  getCompareScenariosApiSimulationScenariosCompareGetQueryKey,
  getCompareScenariosApiSimulationScenariosCompareGetQueryOptions,
  getGetScenarioApiSimulationScenariosScenarioIdGetQueryOptions,
  getListScenariosApiSimulationScenariosGetQueryKey,
  useBreedDefaultsApiSimulationDefaultsGet,
  useCompareScenariosApiSimulationScenariosCompareGet,
  useCreateScenarioApiSimulationScenariosPost,
  useDeleteScenarioApiSimulationScenariosScenarioIdDelete,
  useFarmCalibrationApiSimulationCalibrationGet,
  useHerdSnapshotApiSimulationHerdSnapshotGet,
  useListBreedsApiSimulationDefaultsBreedsGet,
  useListScenariosApiSimulationScenariosGet,
  useRunAdhocApiSimulationRunPost,
  useRunScenarioApiSimulationScenariosScenarioIdRunPost,
  useUpdateScenarioApiSimulationScenariosScenarioIdPatch,
} from "@/api/generated/endpoints";
import type {
  CalibrationEvidence,
  FarmCalibrationOut,
  HerdEventAssumptions,
  MetricExplanation,
  ScenarioOut,
  SimulationAssumptions,
  SimulationResult,
  ViabilityMetrics,
} from "@/api/generated/models";
import { BreedDefaultsApiSimulationDefaultsGetSystem } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PaginationControls } from "@/components/pagination-controls";
import { PageHeader } from "@/components/page-header";
import { PermissionGate } from "@/components/permission-gate";
import { TableSkeleton, InlineLoading } from "@/components/skeletons";
import { farmVocabulary, type FarmVocabulary } from "@/lib/farm-vocabulary";
import { MAX_PAGE_OFFSET, useUrlState } from "@/lib/use-url-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api-client";
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { FieldHelpButton, MetricCard, type FieldHelpState } from "./components/editor-widgets";
import { formatHead, formatPercent, formatRatio, humanize } from "./components/format-helpers";
import {
  NumberArrayInput,
  NumberInput,
  type NumberArrayRule,
  type NumericRule,
} from "./components/number-inputs";
import {
  MonteCarloHistogram,
  OptimizationResults,
  RiskBandTable,
} from "./components/results-visuals";
import { formatFarmDateTime, formatMoney } from "@/lib/format";
import { useLanguage, useT, type MessageKey, type TFn } from "@/lib/i18n";
import {
  SIMULATION_SECTION_HELP,
  simulationFieldHelp,
  speciesAwareLabel,
} from "@/lib/simulation-field-help";
import { usePermissions, type PermissionsState } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

const DEFAULT_SYSTEM = BreedDefaultsApiSimulationDefaultsGetSystem.stall_fed;
const MAX_COMPARE_SCENARIOS = 5;
const SCENARIO_PAGE_SIZE = 20;

/** Invalid legacy scenarios remain visible so the user can remove them. */
type ScenarioRow = ScenarioOut;

type BoundResult = {
  data: SimulationResult;
  /** Assumptions the run actually used. */
  fingerprint: string;
  /** Run options the run actually used. */
  options: string;
  /** Saved scenario the run came from; null for an ad-hoc editor run. */
  scenarioId: number | null;
  source: { kind: "editor" } | { kind: "scenario"; name: string };
};

const INTEGER_FIELDS = new Set([
  "meta.horizon_months",
  "herd.does",
  "herd.bucks",
  "herd.female_growers",
  "herd.male_growers",
  "herd.female_weaners",
  "herd.male_weaners",
  "herd.female_kids",
  "herd.male_kids",
  "herd.max_breeding_does",
  "reproduction.gestation_months",
  "reproduction.lactation_months",
  "reproduction.weaning_days",
  "reproduction.months_open_before_breeding",
  "reproduction.age_at_first_breeding_months",
  "reproduction.sexed_semen_services",
  "reproduction.max_services_before_cull",
  "culling.max_doe_age_months",
  "culling.buck_rotation_years",
  "culling.buck_doe_ratio",
  "growth.sale_age_months",
  "growth.adult_weight_age_months",
  "sales.eid_month",
  "sales.festival_hold_months",
  "costs.labour_per_head_threshold",
  "costs.planned_capacity_head",
  "costs.shed_useful_life_years",
  "costs.equipment_useful_life_years",
  "finance.loan_term_months",
  "finance.moratorium_months",
  "finance.working_capital_months",
  "risk.monte_carlo_runs",
  "risk.seed",
  "risk.disease_outbreak_duration_months",
  "risk.drought_duration_months",
  "risk.market_crash_duration_months",
  "optimization.max_candidates",
  "optimization.doe_scale_steps",
  "optimization.sale_age_radius_months",
]);

const FIELD_BOUNDS: Record<
  string,
  Pick<NumericRule, "min" | "max" | "exclusiveMin">
> = {
  "meta.horizon_months": { min: 12, max: 240 },
  // Wide enough for slow-maturing herds; goats sit at ~5 months.
  "reproduction.gestation_months": { min: 1, max: 12 },
  // Meat mode: the weaning-plus-rebreed interval, not a saleable-milk length.
  "reproduction.lactation_months": { min: 1, max: 12 },
  "reproduction.months_open_before_breeding": { min: 0, max: 12 },
  "reproduction.litter_size": { min: 0.5, max: 4 },
  "reproduction.age_at_first_breeding_months": { min: 6, max: 30 },
  "reproduction.stillbirth_rate": { min: 0, max: 0.5 },
  // Sexed-semen AI levers — PERMANENTLY retired from the API contract when the
  // model went goat-meat-only: the backend strips these keys from every
  // assumptions payload (_RETIRED_DAIRY_FIELDS), so they can never reach the
  // editor. The bounds below stay only as documentation of the old contract.
  "reproduction.sexed_semen_services": { min: 0, max: 6 },
  "reproduction.sexed_female_fraction": { min: 0.5, max: 1 },
  "reproduction.sexed_conception_multiplier": { exclusiveMin: 0, max: 1 },
  "reproduction.max_services_before_cull": { min: 0, max: 12 },
  "culling.max_doe_age_months": { min: 36, max: 180 },
  "culling.buck_rotation_years": { min: 1, max: 10 },
  "culling.buck_doe_ratio": { min: 1, max: 100 },
  "growth.birth_weight_kg": { exclusiveMin: 0, max: 1000 },
  "growth.adult_weight_doe_kg": { exclusiveMin: 0, max: 1000 },
  "growth.adult_weight_buck_kg": { exclusiveMin: 0, max: 1000 },
  "growth.adult_weight_age_months": { min: 13, max: 120 },
  "growth.young_male_weight_premium": { min: 0, max: 0.5 },
  "growth.sale_age_months": { min: 6, max: 24 },
  "sales.eid_month": { min: 0, max: 12 },
  "sales.festival_hold_months": { min: 0, max: 12 },
  "sales.eid_price_uplift": { min: 0, max: 2 },
  "sales.annual_livestock_price_growth_rate": { exclusiveMin: -1, max: 1 },
  "sales.selling_cost_fraction": { min: 0, max: 0.5 },
  // Surplus-milk side-line (meat mode): litres sold per lactating doe per day.
  "sales.milk_sale_litres_per_doe_day": { min: 0, max: 10 },
  "sales.milk_price_per_litre": { min: 0, max: 1e9 },
  "sales.male_calf_price_per_head": { min: 0, max: 1_000_000 },
  // The sales money heuristic below only matches "price"/"income", so this is
  // the one money field on the form that would otherwise reach the API with no
  // client-side floor and 422 on a negative "rebate".
  "sales.transport_cost_per_head": { min: 0, max: 1e9 },
  "feed.cultivated_fodder_acres": { min: 0, max: 1_000_000 },
  "feed.fodder_yield_t_dm_per_acre_year": { exclusiveMin: 0, max: 1000 },
  "feed.annual_feed_price_growth_rate": { exclusiveMin: -1, max: 1 },
  "feed.initial_fodder_stock_kg_dm": { min: 0, max: 1e9 },
  // Water demand planning (litres/head/day by class).
  "feed.water_litres_kid_per_day": { min: 0, max: 50 },
  "feed.water_litres_weaner_per_day": { min: 0, max: 50 },
  "feed.water_litres_grower_per_day": { min: 0, max: 50 },
  "feed.water_litres_doe_per_day": { min: 0, max: 50 },
  "feed.water_litres_lactating_doe_per_day": { min: 0, max: 50 },
  "feed.water_litres_buck_per_day": { min: 0, max: 50 },
  "feed.fodder_storage_capacity_kg_dm": { min: 0, max: 1e9 },
  "feed.fodder_storage_loss_fraction_monthly": { min: 0, max: 1 },
  // Mirrors MAX_LABOUR_PER_HEAD_THRESHOLD in the backend. It is deliberately
  // much larger than the herd-size ceiling ("effectively never scale" is a
  // legitimate policy), but still finite enough for downstream arithmetic.
  "costs.labour_per_head_threshold": { min: 1, max: 1_000_000_000_000_000 },
  "costs.insurance_pct_stock_value_annual": { min: 0, max: 0.25 },
  "costs.operating_cost_growth_rate_annual": { exclusiveMin: -1, max: 1 },
  "costs.planned_capacity_head": { min: 0, max: 100_000 },
  "costs.capacity_buffer_fraction": { min: 0, max: 1 },
  "costs.shed_useful_life_years": { min: 1, max: 100 },
  "costs.equipment_useful_life_years": { min: 1, max: 50 },
  "costs.shed_residual_fraction": { min: 0, max: 1 },
  "costs.equipment_residual_fraction": { min: 0, max: 1 },
  "finance.interest_rate_annual": { min: 0, max: 0.5 },
  "finance.loan_term_months": { min: 1, max: 180 },
  "finance.moratorium_months": { min: 0, max: 60 },
  "finance.subsidy_fraction": { min: 0, max: 0.9 },
  "finance.discount_rate_annual": { min: 0, max: 0.5 },
  "finance.working_capital_months": { min: 0, max: 24 },
  "finance.income_tax_rate": { min: 0, max: 0.6 },
  "finance.terminal_livestock_realization_fraction": { min: 0, max: 1 },
  "finance.terminal_asset_realization_fraction": { min: 0, max: 1 },
  "finance.terminal_working_capital_recovery_fraction": { min: 0, max: 1 },
  "finance.reinvestment_rate_annual": { min: 0, max: 0.5 },
  "risk.monte_carlo_runs": { min: 1, max: 2000 },
  // 2**31-1: the backend bounds the seed so it survives a browser round-trip.
  // No naming heuristic covers "seed", so without this entry the field carries
  // no bounds at all and a negative seed only fails at the API.
  "risk.seed": { min: 0, max: 2_147_483_647 },
  "risk.correlation_strength": { min: 0, max: 0.95 },
  "risk.disease_outbreak_probability_annual": { min: 0, max: 1 },
  "risk.disease_outbreak_duration_months": { min: 1, max: 24 },
  "risk.disease_adult_mortality_multiplier": { min: 1, max: 20 },
  "risk.disease_kid_mortality_multiplier": { min: 1, max: 20 },
  "risk.disease_conception_multiplier": { exclusiveMin: 0, max: 1 },
  "risk.drought_probability_annual": { min: 0, max: 1 },
  "risk.drought_duration_months": { min: 1, max: 24 },
  "risk.drought_fodder_yield_multiplier": { exclusiveMin: 0, max: 1 },
  "risk.drought_feed_price_multiplier": { min: 1, max: 20 },
  "risk.market_crash_probability_annual": { min: 0, max: 1 },
  "risk.market_crash_duration_months": { min: 1, max: 24 },
  "risk.market_crash_price_multiplier": { exclusiveMin: 0, max: 1 },
  "optimization.max_candidates": { min: 1, max: 300 },
  "optimization.minimum_dscr": { min: 0, max: 10 },
  // Optional ceilings: the backend takes any non-negative amount (null = no
  // ceiling). They must not be capped at MAX_MONEY here — that is a *per-input*
  // magnitude cap, while a project cost is a product of several such inputs and
  // legitimately exceeds it on a large farm.
  "optimization.maximum_project_cost": { min: 0 },
  "optimization.maximum_funding_gap": { min: 0 },
  "optimization.doe_scale_low": { exclusiveMin: 0, max: 5 },
  "optimization.doe_scale_high": { exclusiveMin: 0, max: 5 },
  "optimization.doe_scale_steps": { min: 1, max: 9 },
  "optimization.sale_age_radius_months": { min: 0, max: 9 },
  "optimization.retention_step": { min: 0, max: 1 },
  "optimization.loan_fraction_step": { min: 0, max: 1 },
};

/** Units the naming heuristics in numericRule cannot infer. Explicit paths
 * win, exactly as FIELD_BOUNDS does for limits. */
function fieldUnits(t: TFn): Record<string, string> {
  return {
    "costs.labour_per_head_threshold": t("simulation.unit.headPerLabourer"),
    "feed.fodder_yield_t_dm_per_acre_year": t("simulation.unit.tonnesDmPerAcreYear"),
    "feed.initial_fodder_stock_kg_dm": t("simulation.unit.kgDm"),
    "feed.fodder_storage_capacity_kg_dm": t("simulation.unit.kgDm"),
    "costs.planned_capacity_head": t("simulation.unit.head"),
    // An integer head-count ratio (1 buck per N does), not a 0-1 fraction: the
    // heuristic chain's trailing `ratio` test would otherwise caption it one.
    "culling.buck_doe_ratio": t("simulation.unit.femalesPerMale"),
    // Sexed-semen AI levers — permanently retired and stripped server-side
    // (see FIELD_BOUNDS above); these unit captions never render.
    "reproduction.sexed_semen_services": t("simulation.unit.services"),
    "reproduction.sexed_female_fraction": "%",
    "reproduction.sexed_conception_multiplier": "×",
    "reproduction.max_services_before_cull": t("simulation.unit.servicesZeroOff"),
    "growth.young_male_weight_premium": "%",
    "sales.festival_hold_months": t("simulation.unit.monthsZeroSellAtFinish"),
    "optimization.maximum_project_cost": "₹",
    "optimization.maximum_funding_gap": "₹",
    // Fractions and multipliers whose names also contain a money or duration
    // substring, which the heuristic chain settles first. Captioning a 0-0.6 tax
    // rate "₹" invites farmers to type 30 for 30%; the entry must stay explicit
    // because the money/duration tests cannot simply be moved (see numericRule).
    "sales.selling_cost_fraction": t("simulation.unit.fraction"),
    "sales.milk_sale_litres_per_doe_day": t("simulation.unit.litresDoeDay"),
    "sales.milk_price_per_litre": t("simulation.unit.currencyPerLitre"),
    "feed.water_litres_kid_per_day": t("simulation.unit.litresDay"),
    "feed.water_litres_weaner_per_day": t("simulation.unit.litresDay"),
    "feed.water_litres_grower_per_day": t("simulation.unit.litresDay"),
    "feed.water_litres_doe_per_day": t("simulation.unit.litresDay"),
    "feed.water_litres_lactating_doe_per_day": t("simulation.unit.litresDay"),
    "feed.water_litres_buck_per_day": t("simulation.unit.litresDay"),
    // Fields the heuristics would caption as a 0-1 fraction or as a plain
    // multiplier.
    "sales.male_calf_sell_at_birth_fraction": t("simulation.unit.fraction"),
    "sales.male_calf_price_per_head": t("simulation.unit.currencyPerHead"),
    "sales.annual_livestock_price_growth_rate": t("simulation.unit.fraction"),
    "feed.annual_feed_price_growth_rate": t("simulation.unit.fraction"),
    "feed.fodder_storage_loss_fraction_monthly": t("simulation.unit.fraction"),
    "costs.operating_cost_growth_rate_annual": t("simulation.unit.fraction"),
    "finance.income_tax_rate": t("simulation.unit.fraction"),
    "risk.correlation_strength": t("simulation.unit.fraction"),
    "risk.disease_outbreak_probability_annual": t("simulation.unit.fraction"),
    "risk.drought_probability_annual": t("simulation.unit.fraction"),
    "risk.market_crash_probability_annual": t("simulation.unit.fraction"),
    "risk.disease_adult_mortality_multiplier": t("simulation.unit.multiplier"),
    "risk.disease_kid_mortality_multiplier": t("simulation.unit.multiplier"),
    "risk.disease_conception_multiplier": t("simulation.unit.multiplier"),
    "risk.drought_fodder_yield_multiplier": t("simulation.unit.multiplier"),
    "risk.drought_feed_price_multiplier": t("simulation.unit.multiplier"),
    "risk.market_crash_price_multiplier": t("simulation.unit.multiplier"),
    "optimization.doe_scale_low": t("simulation.unit.multiplier"),
    "optimization.doe_scale_high": t("simulation.unit.multiplier"),
  };
}

/** Fields the backend models as "amount, or null for no ceiling". They arrive
 * as null by default, which every typed branch of renderField would drop —
 * taking the whole row off the form. Render them as blankable inputs instead. */
const NULLABLE_NUMBER_FIELDS = new Set([
  "optimization.maximum_project_cost",
  "optimization.maximum_funding_gap",
]);

/** Numeric fields constrained to a closed set of values — rendered as a
 * select so an out-of-set number can never reach the API. */
function numericFieldOptions(t: TFn): Record<string, Record<string, string>> {
  return {
    "reproduction.weaning_days": {
      "60": t("simulation.option.weaning60"),
      "90": t("simulation.option.weaning90"),
    },
  };
}

function stringFieldOptions(t: TFn): Record<string, Record<string, string>> {
  return {
    "herd.foundation_flock_state": {
      mixed: t("simulation.option.mixed"),
      open: t("simulation.option.open"),
    },
    "growth.growth_regime": {
      stall_fed: t("simulation.option.stallFed"),
      semi_intensive: t("simulation.option.semiIntensive"),
    },
    "costs.capacity_basis": {
      projected_peak: t("simulation.option.projectedPeak"),
      planned: t("simulation.option.plannedCapacity"),
      opening_herd: t("simulation.option.openingHerd"),
    },
    "optimization.objective": {
      balanced: t("simulation.option.balanced"),
      npv: t("simulation.option.highestNpv"),
      liquidity: t("simulation.option.strongestLiquidity"),
    },
  };
}

function numberArrayRule(
  section: string,
  key: string,
  horizonMonths: number,
  t: TFn,
): NumberArrayRule {
  const path = `${section}.${key}`;
  if (path === "growth.weight_by_age_months") {
    return {
      exactLength: 13,
      exclusiveMin: 0,
      max: 1000,
      nondecreasing: true,
      itemLabel: t("simulation.array.weightByAge"),
    };
  }
  if (path === "sales.festival_sale_months") {
    return {
      maxLength: 40,
      integer: true,
      min: 1,
      max: horizonMonths,
      allowEmpty: true,
      unique: true,
      itemLabel: t("simulation.array.simulationMonths"),
    };
  }
  if (key.startsWith("monthly_")) {
    return {
      exactLength: 12,
      exclusiveMin: 0,
      max: 10,
      itemLabel: t("simulation.array.monthlyMultipliers"),
    };
  }
  return { itemLabel: t("simulation.array.values") };
}

function numericRule(section: string, key: string, t: TFn): NumericRule {
  const path = `${section}.${key}`;
  const rule: NumericRule = {
    integer: INTEGER_FIELDS.has(path),
  };

  if (section === "herd") {
    if (INTEGER_FIELDS.has(path)) {
      Object.assign(rule, { min: 0, max: 100_000, integer: true });
    } else if (key.endsWith("_fraction")) Object.assign(rule, { min: 0, max: 1 });
    else if (key.endsWith("_price")) Object.assign(rule, { min: 0, max: 1e9 });
  } else if (section === "mortality") Object.assign(rule, { min: 0, max: 0.9 });
  else if (section === "reproduction" && /rate|ratio/.test(key))
    Object.assign(rule, { min: 0, max: 1 });
  else if (section === "culling" && key === "doe_cull_rate_annual")
    Object.assign(rule, { min: 0, max: 1 });
  else if (section === "sales" && /price|income/.test(key))
    Object.assign(rule, { min: 0, max: 1e9 });
  else if (section === "feed") {
    if (key.startsWith("dmi_") || key.endsWith("_dm_pct"))
      Object.assign(rule, {
        exclusiveMin: 0,
        max: key.startsWith("dmi_") ? 0.1 : 1,
      });
    else if (key.includes("share") || key === "grazing_dm_fraction")
      Object.assign(rule, { min: 0, max: 1 });
    else if (key.includes("price")) Object.assign(rule, { min: 0, max: 1e9 });
  } else if (
    section === "costs" &&
    key !== "labour_per_head_threshold" &&
    !key.includes("pct")
  )
    Object.assign(rule, { min: 0, max: 1e9 });
  else if (
    section === "finance" &&
    (key === "initial_stock_cost" || key === "loan_fraction_of_project_cost")
  )
    Object.assign(
      rule,
      key === "initial_stock_cost" ? { min: 0, max: 1e9 } : { min: 0, max: 1 },
    );

  // Explicit backend-derived limits replace the broad naming heuristics. A
  // plain Object.assign left stale bounds behind: annual price/cost growth
  // correctly supplied exclusiveMin=-1 but retained heuristic min=0, making
  // every legal decline rate impossible to enter in the browser.
  const explicitBounds = FIELD_BOUNDS[path];
  if (explicitBounds) {
    delete rule.min;
    delete rule.max;
    delete rule.exclusiveMin;
    Object.assign(rule, explicitBounds);
  }

  const units = fieldUnits(t);
  if (units[path]) rule.unit = units[path];
  else if (
    path === "sales.eid_price_uplift" ||
    path === "finance.loan_fraction_of_project_cost"
  )
    rule.unit = t("simulation.unit.fraction");
  // `_per_month` / `_per_year` are rates of the underlying metric, not
  // durations — settle them before the month/year duration patterns, which
  // otherwise caption ₹10,000/month of labour as "months".
  else if (key.endsWith("_per_month")) rule.unit = t("simulation.unit.currencyPerMonth");
  else if (key.endsWith("_per_year")) rule.unit = t("simulation.unit.currencyPerYear");
  else if (key.includes("month")) rule.unit = t("simulation.unit.months");
  else if (key.includes("year")) rule.unit = t("simulation.unit.years");
  else if (
    key.includes("price") ||
    key.includes("cost") ||
    key.includes("income") ||
    key.includes("labour") ||
    key.includes("overhead")
  )
    rule.unit = t("simulation.unit.currency");
  else if (key.includes("weight") || key.includes("_kg")) rule.unit = t("simulation.unit.kg");
  else if (key.includes("acre")) rule.unit = t("simulation.unit.acres");
  // This test stays last: hoisting it above the money/duration ones captions
  // concent*rate*_price_per_kg and *_duration_months as "fraction". Fields it
  // therefore cannot reach (income_tax_rate, selling_cost_fraction, …) belong
  // in FIELD_UNITS, not in a reordering of this chain.
  else if (/rate|ratio|fraction|pct|share|dmi_/.test(key))
    rule.unit = t("simulation.unit.fraction");
  return rule;
}

/** Identity of one assumption set. `events` is normalized because the editor
 * always carries a (possibly empty) event list while a stored scenario may
 * omit the key entirely — without this, a scenario run and the very same
 * scenario loaded in the editor never compared equal. */
function assumptionsFingerprint(assumptions: SimulationAssumptions): string {
  return JSON.stringify({ ...assumptions, events: assumptions.events ?? [] });
}

/** Run options belong to a result's identity: toggling Monte Carlo or
 * sensitivity after a run leaves the displayed figures incomplete. */
function runOptionsFingerprint(
  monteCarlo: boolean,
  sensitivity: boolean,
  optimization: boolean,
): string {
  return JSON.stringify({ monte_carlo: monteCarlo, sensitivity, optimization });
}

function scenarioUsable(scenario: ScenarioRow): scenario is ScenarioRow & {
  assumptions: SimulationAssumptions;
} {
  return scenario.valid !== false && scenario.assumptions !== null;
}

/** Quick-pick simulation horizons (meta.horizon_months stays editable). */
const HORIZON_PRESETS = [60, 120, 180, 240] as const;

/** value → label maps for the root `items` prop: without them, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
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

function eventKindItems(t: TFn): Record<string, string> {
  return {
    purchase: t("simulation.event.kind.purchase"),
    sale: t("simulation.event.kind.sale"),
  };
}

/** Inline validation for the herd events editor; horizon comes from meta. */
function validateEvents(
  events: HerdEventAssumptions[],
  horizonMonths: number,
  t: TFn,
): string[] {
  const errors: string[] = [];
  events.forEach((event, i) => {
    const label = t("simulation.event.number", { number: i + 1 });
    if (
      !Number.isInteger(event.month) ||
      event.month < 1 ||
      event.month > horizonMonths
    ) {
      errors.push(t("simulation.validation.eventMonth", { label, horizon: horizonMonths }));
    }
    if (!Number.isFinite(event.count) || event.count <= 0 || event.count > 100_000) {
      errors.push(t("simulation.validation.eventCount", { label }));
    }
    if (
      event.price_per_head !== null &&
      event.price_per_head !== undefined &&
      (Number.isNaN(event.price_per_head) || event.price_per_head < 0)
    ) {
      errors.push(t("simulation.validation.eventPrice", { label }));
    }
  });
  return errors;
}

/** One figure value in an explanation: currency for money-shaped keys,
 * percent for rate-shaped keys, otherwise a plain number. */
function formatFigure(key: string, value: number | string): string {
  if (typeof value === "string") return value;
  // Fraction- and duration-shaped keys are settled first: the money pattern
  // matches on substrings ("loan", "subsidy"), so loan_fraction (0.85),
  // subsidy_fraction (0.0) and loan_term_months (72) would render as rupees.
  // festival_uplift is the backend's 0-1 share of the same uplift its
  // narrative already spells as a percentage.
  if (/_fraction$|_margin$|_uplift$/.test(key) || /rate|irr|percent|pct|prob/i.test(key))
    return formatPercent(value);
  const isDuration = /_months?$|_years$|_runs$/.test(key);
  // terminal_value, tax_total and *_profit_total are rupee figures that share
  // no substring with the money vocabulary above; without them the explain
  // dialog printed a bare 150000 next to "Livestock ₹1,00,000". total_opex
  // and ebitda_total are the same kind of money total.
  if (
    !isDuration &&
    /cost|price|amount|npv|equity|loan|subsidy|capital|shed|equipment|stock|revenue|cash|terminal_value|tax_total|profit|opex|ebitda/i.test(
      key,
    )
  )
    return formatMoney(value);
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}


function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

function localizedVerdict(verdict: string, t: TFn, language: "en" | "te"): string {
  if (language === "en") return verdict;
  if (verdict === "VIABLE") return t("simulation.verdict.viable");
  if (verdict === "VIABLE WITH CAUTION") return t("simulation.verdict.viableWithCaution");
  if (verdict === "NOT VIABLE") return t("simulation.verdict.notViable");
  return verdict;
}

/** Heavy runs get a long transport budget (see api-client), but an abort is
 *  still possible: say what actually happened instead of a generic failure —
 *  the server may still be computing (M-6). */
function runErrorMessage(err: unknown, fallback: string, t: TFn): string {
  // AbortSignal.timeout() rejects with a TimeoutError DOMException — that is
  // our own budget expiring. A bare AbortError is a transport-level failure
  // (undici aborts dropped connections) and must keep the generic fallback.
  if (err instanceof Error && err.name === "TimeoutError") {
    return t("simulation.error.runTimeout");
  }
  return errorMessage(err, fallback);
}


function formatCalibrationValue(value: CalibrationEvidence["calibrated_value"]): string {
  if (Array.isArray(value)) return value.map((item) => formatRatio(item, 3)).join(", ");
  if (!Number.isFinite(value)) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(3);
}


/** Unit/range/current-value facts shown under a field's explanation. */
function fieldFacts(
  rule: NumericRule | NumberArrayRule | undefined,
  value: unknown,
  t: TFn,
  language: "en" | "te",
  options?: Record<string, string>,
): { term: string; value: string }[] {
  const facts: { term: string; value: string }[] = [];
  if (options)
    facts.push({ term: t("simulation.facts.choices"), value: Object.values(options).join(", ") });
  if (rule && "unit" in rule && rule.unit !== undefined)
    facts.push({ term: t("simulation.facts.unit"), value: rule.unit });
  if (rule) {
    const bounds: string[] = [];
    if (rule.exclusiveMin !== undefined)
      bounds.push(t("simulation.facts.greaterThan", { value: rule.exclusiveMin }));
    if (rule.min !== undefined) bounds.push(t("simulation.facts.atLeast", { value: rule.min }));
    if (rule.max !== undefined) bounds.push(t("simulation.facts.atMost", { value: rule.max }));
    if ("exactLength" in rule && rule.exactLength !== undefined)
      bounds.push(t("simulation.facts.exactlyValues", { count: rule.exactLength }));
    if ("maxLength" in rule && rule.maxLength !== undefined)
      bounds.push(t("simulation.facts.atMostValues", { count: rule.maxLength }));
    if (rule.integer) bounds.push(t("simulation.facts.wholeNumbersOnly"));
    if (bounds.length > 0)
      facts.push({ term: t("simulation.facts.allowedValues"), value: bounds.join(", ") });
  }
  if (typeof value === "boolean")
    facts.push({
      term: t("simulation.facts.currentValue"),
      value: value ? t("simulation.facts.on") : t("simulation.facts.off"),
    });
  else if (typeof value === "number")
    facts.push({ term: t("simulation.facts.currentValue"), value: String(value) });
  else if (typeof value === "string" && value.length > 0 && value.length <= 80)
    facts.push({
      term: t("simulation.facts.currentValue"),
      value: language === "te" ? (options?.[value] ?? value) : value,
    });
  else if (Array.isArray(value))
    facts.push({
      term: t("simulation.facts.currentValue"),
      value: value.length <= 12 ? value.join(", ") : t("simulation.facts.valueCount", { count: value.length }),
    });
  return facts;
}

/** Rows of the side-by-side scenario comparison table. */
function compareRows(t: TFn): {
  key: keyof Pick<
    ViabilityMetrics,
    | "npv"
    | "irr"
    | "mirr"
    | "bcr"
    | "avg_dscr"
    | "min_dscr"
    | "payback_month"
    | "minimum_cash_balance"
    | "additional_working_capital_required"
    | "operating_margin"
  >;
  label: string;
  format: (m: ViabilityMetrics) => string;
}[] {
  return [
    { key: "npv", label: t("simulation.metric.npv"), format: (m) => formatMoney(m.npv) },
    { key: "irr", label: t("simulation.metric.irr"), format: (m) => formatPercent(m.irr) },
    { key: "mirr", label: t("simulation.metric.mirr"), format: (m) => formatPercent(m.mirr) },
    { key: "bcr", label: t("simulation.metric.bcr"), format: (m) => formatRatio(m.bcr) },
    {
      key: "avg_dscr",
      label: t("simulation.metric.avgDscr"),
      format: (m) => formatRatio(m.avg_dscr),
    },
    {
      key: "min_dscr",
      label: t("simulation.metric.minimumDscr"),
      format: (m) => formatRatio(m.min_dscr),
    },
    {
      key: "operating_margin",
      label: t("simulation.metric.operatingMargin"),
      format: (m) => formatPercent(m.operating_margin),
    },
    {
      key: "minimum_cash_balance",
      label: t("simulation.metric.minimumCash"),
      format: (m) => formatMoney(m.minimum_cash_balance),
    },
    {
      key: "additional_working_capital_required",
      label: t("simulation.metric.additionalWorkingCapital"),
      format: (m) => formatMoney(m.additional_working_capital_required),
    },
    {
      key: "payback_month",
      label: t("simulation.metric.paybackMonth"),
      format: (m) => (m.payback_month === null ? "—" : String(m.payback_month)),
    },
  ];
}

type SectionValues = Record<string, unknown>;

/** Whole litres with Indian digit grouping for the water-demand figures. */
const litres = (value: number, language: "en" | "te"): string =>
  Math.round(value).toLocaleString(language === "te" ? "te-IN" : "en-IN");

/**
 * Assumption payloads are deliberately data-driven, so their labels cannot
 * be a closed JSX list. Translate the words that make up current and future
 * snake-case keys; an unknown machine field still degrades to its readable
 * humanized form rather than disappearing.
 */
const FIELD_TOKEN_KEYS: Record<string, MessageKey> = {
  active: "simulation.token.active",
  adult: "simulation.token.adult",
  age: "simulation.token.age",
  annual: "simulation.token.annual",
  asset: "simulation.token.asset",
  at: "simulation.token.at",
  auto: "simulation.token.auto",
  basis: "simulation.token.basis",
  birth: "simulation.token.birth",
  breed: "simulation.token.breed",
  breeding: "simulation.token.breeding",
  buck: "simulation.token.buck",
  buffer: "simulation.token.buffer",
  calf: "simulation.token.calf",
  candidates: "simulation.token.candidates",
  capacity: "simulation.token.capacity",
  carryforward: "simulation.token.carryforward",
  conception: "simulation.token.conception",
  concentrate: "simulation.token.concentrate",
  correlation: "simulation.token.correlation",
  cost: "simulation.token.cost",
  costs: "simulation.token.costs",
  cull: "simulation.token.cull",
  cultivated: "simulation.token.cultivated",
  days: "simulation.token.days",
  discount: "simulation.token.discount",
  disease: "simulation.token.disease",
  dm: "simulation.token.dm",
  dmi: "simulation.token.dmi",
  doe: "simulation.token.doe",
  does: "simulation.token.does",
  drought: "simulation.token.drought",
  dry: "simulation.token.dry",
  duration: "simulation.token.duration",
  eid: "simulation.token.eid",
  enabled: "simulation.token.enabled",
  equipment: "simulation.token.equipment",
  feed: "simulation.token.feed",
  female: "simulation.token.female",
  festival: "simulation.token.festival",
  flock: "simulation.token.flock",
  fodder: "simulation.token.fodder",
  fraction: "simulation.token.fraction",
  foundation: "simulation.token.foundation",
  gap: "simulation.token.gap",
  gestation: "simulation.token.gestation",
  grazing: "simulation.token.grazing",
  green: "simulation.token.green",
  growers: "simulation.token.growers",
  growth: "simulation.token.growth",
  head: "simulation.token.head",
  herd: "simulation.token.herd",
  high: "simulation.token.high",
  hold: "simulation.token.hold",
  horizon: "simulation.token.horizon",
  include: "simulation.token.include",
  income: "simulation.token.income",
  initial: "simulation.token.initial",
  insurance: "simulation.token.insurance",
  interest: "simulation.token.interest",
  kg: "simulation.token.kg",
  kid: "simulation.token.kid",
  kids: "simulation.token.kids",
  lactating: "simulation.token.lactating",
  lactation: "simulation.token.lactation",
  labour: "simulation.token.labour",
  life: "simulation.token.life",
  litter: "simulation.token.litter",
  livestock: "simulation.token.livestock",
  loan: "simulation.token.loan",
  loss: "simulation.token.loss",
  low: "simulation.token.low",
  male: "simulation.token.male",
  manure: "simulation.token.manure",
  market: "simulation.token.market",
  max: "simulation.token.max",
  maximum: "simulation.token.maximum",
  meat: "simulation.token.meat",
  minimum: "simulation.token.minimum",
  misc: "simulation.token.misc",
  month: "simulation.token.month",
  monthly: "simulation.token.monthly",
  months: "simulation.token.months",
  mortality: "simulation.token.mortality",
  multipliers: "simulation.token.multipliers",
  nlm: "simulation.token.nlm",
  open: "simulation.token.open",
  operating: "simulation.token.operating",
  overhead: "simulation.token.overhead",
  pct: "simulation.token.pct",
  per: "simulation.token.per",
  planned: "simulation.token.planned",
  price: "simulation.token.price",
  premium: "simulation.token.premium",
  probability: "simulation.token.probability",
  process: "simulation.token.process",
  project: "simulation.token.project",
  purchased: "simulation.token.purchased",
  parity: "simulation.token.parity",
  rate: "simulation.token.rate",
  radius: "simulation.token.radius",
  recovery: "simulation.token.recovery",
  regime: "simulation.token.regime",
  reinvestment: "simulation.token.reinvestment",
  residual: "simulation.token.residual",
  retention: "simulation.token.retention",
  rho: "simulation.token.rho",
  risk: "simulation.token.risk",
  runs: "simulation.token.runs",
  sale: "simulation.token.sale",
  scale: "simulation.token.scale",
  seed: "simulation.token.seed",
  sell: "simulation.token.sell",
  service: "simulation.token.service",
  services: "simulation.token.services",
  settling: "simulation.token.settling",
  sex: "simulation.token.sex",
  shed: "simulation.token.shed",
  share: "simulation.token.share",
  size: "simulation.token.size",
  sold: "simulation.token.sold",
  start: "simulation.token.start",
  state: "simulation.token.state",
  stillbirth: "simulation.token.stillbirth",
  stock: "simulation.token.stock",
  storage: "simulation.token.storage",
  subsidy: "simulation.token.subsidy",
  tax: "simulation.token.tax",
  term: "simulation.token.term",
  terminal: "simulation.token.terminal",
  threshold: "simulation.token.threshold",
  t: "simulation.token.tonnes",
  useful: "simulation.token.useful",
  value: "simulation.token.value",
  variation: "simulation.token.variation",
  vet: "simulation.token.vet",
  water: "simulation.token.water",
  weaner: "simulation.token.weaner",
  weaners: "simulation.token.weaners",
  weaning: "simulation.token.weaning",
  weight: "simulation.token.weight",
  within: "simulation.token.within",
  working: "simulation.token.working",
  year: "simulation.token.year",
  years: "simulation.token.years",
  yield: "simulation.token.yield",
  young: "simulation.token.young",
  acre: "simulation.token.acre",
  acres: "simulation.token.acres",
  animal: "simulation.token.animal",
  before: "simulation.token.before",
  bucks: "simulation.token.bucks",
  by: "simulation.token.by",
  capital: "simulation.token.capital",
  carlo: "simulation.token.carlo",
  class: "simulation.token.class",
  count: "simulation.token.count",
  crash: "simulation.token.crash",
  creep: "simulation.token.creep",
  day: "simulation.token.day",
  dscr: "simulation.token.dscr",
  family: "simulation.token.family",
  first: "simulation.token.first",
  funding: "simulation.token.funding",
  grower: "simulation.token.grower",
  kind: "simulation.token.kind",
  litre: "simulation.token.litre",
  litres: "simulation.token.litres",
  maintenance: "simulation.token.maintenance",
  milk: "simulation.token.milk",
  min: "simulation.token.min",
  monte: "simulation.token.monte",
  moratorium: "simulation.token.moratorium",
  multiplier: "simulation.token.multiplier",
  objective: "simulation.token.objective",
  of: "simulation.token.of",
  outbreak: "simulation.token.outbreak",
  place: "simulation.token.place",
  post: "simulation.token.post",
  pre: "simulation.token.pre",
  pregnant: "simulation.token.pregnant",
  purchase: "simulation.token.purchase",
  ratio: "simulation.token.ratio",
  realization: "simulation.token.realization",
  rotation: "simulation.token.rotation",
  run: "simulation.token.run",
  selling: "simulation.token.selling",
  step: "simulation.token.step",
  steps: "simulation.token.steps",
  strength: "simulation.token.strength",
  transport: "simulation.token.transport",
  uplift: "simulation.token.uplift",
};

function localizedFieldLabel(key: string, t: TFn, language: "en" | "te"): string {
  // Keep the default-language route byte-for-byte compatible with its
  // historic humanizer. Telugu needs token-aware labels because direct title
  // casing of a snake-case backend key would otherwise leak English.
  if (language === "en") return humanize(key);
  return key
    .split(/[._\s]+/)
    .filter(Boolean)
    .map((token) => FIELD_TOKEN_KEYS[token.toLowerCase()]
      ? t(FIELD_TOKEN_KEYS[token.toLowerCase()])
      : humanize(token))
    .join(" ");
}

/** Dairy-machinery assumption keys permanently retired for the goat-meat
 * profile (backend SimulationAssumptions._drop_retired_dairy_fields strips
 * them server-side). This filter remains as defense against stale cached
 * payloads so they never render (or validate) on screen. */
const DAIRY_HIDDEN_FIELDS = new Set([
  "sales.lactation_milk_litres",
  "sales.milk_price_per_kg_fat",
  "sales.milk_fat_pct",
  "sales.milk_persistency_monthly",
  "sales.milk_curve_shape",
  "sales.milk_peak_day",
  "sales.monthly_milk_yield_multipliers",
  "sales.monthly_milk_price_multipliers",
  "sales.annual_milk_price_growth_rate",
  "sales.calf_milk_litres_per_day_per_calf",
  "risk.disease_milk_yield_multiplier",
  "risk.milk_price",
]);

/** Top-level sections of the assumptions object that hold editable fields. */
function sectionEntries(assumptions: SimulationAssumptions): [string, SectionValues][] {
  return Object.entries(assumptions).filter(
    (entry): entry is [string, SectionValues] =>
      entry[1] !== null && typeof entry[1] === "object" && !Array.isArray(entry[1]),
  );
}

function SimulationPageContent({ perms }: { perms: PermissionsState }) {
  const t = useT();
  const { language } = useLanguage();
  const { can } = perms;
  const allowed = can("simulation.view");
  const canManage = can("simulation.manage");
  const canCalibrate = [
    "animals.view",
    "breeding.view",
    "kidding.view",
    "feeding.view",
    "finance.view",
  ].every((permission) => can(permission));
  const queryClient = useQueryClient();

  const defaultBreed = "osmanabadi";
  const [breed, setBreed] = useState(defaultBreed);
  const [system, setSystem] = useState<BreedDefaultsApiSimulationDefaultsGetSystem>(
    DEFAULT_SYSTEM,
  );
  // Auto-load defaults on first mount so the editor isn't empty.
  const [submittedParams, setSubmittedParams] = useState<{
    breed: string;
    system: BreedDefaultsApiSimulationDefaultsGetSystem;
  }>({
    breed: defaultBreed,
    system: DEFAULT_SYSTEM,
  });
  const [assumptions, setAssumptions] = useState<SimulationAssumptions | null>(null);
  const [loadedScenario, setLoadedScenario] = useState<ScenarioRow | null>(null);
  // Defaults requests run independently from the scenario list/editor. Only
  // apply a response while defaults are still the user's latest load intent.
  const acceptDefaultsRef = useRef(true);
  /** Bumped when every loader that REPLACES the whole assumption set is
   *  requested (defaults, scenario load, calibration, herd snapshot). An
   *  awaited loader compares it on resolve so an older completion can never
   *  write into an editor for a newer user intent. Deliberately NOT bumped by field
   *  edits: NumberInput commits on each valid keystroke, so doing that would
   *  discard a herd import the moment the operator nudged a number. */
  const editorEpochRef = useRef(0);
  /** Counts in-editor changes separately from whole-editor loader intents.
   * Conflict recovery fetches a fresh saved revision asynchronously; it may
   * replace the editor only if the operator has not continued editing since
   * the rejected PATCH was submitted. */
  const editorContentEpochRef = useRef(0);
  const [invalidFields, setInvalidFields] = useState<Set<string>>(() => new Set());
  const [editorVersion, setEditorVersion] = useState(0);
  const [horizonInputVersion, setHorizonInputVersion] = useState(0);
  // Scheduled herd events live outside the reflected sections editor.
  const [events, setEvents] = useState<HerdEventAssumptions[]>([]);
  const eventKeyCounter = useRef(0);
  const [eventKeys, setEventKeys] = useState<string[]>([]);
  const [recurrenceOpen, setRecurrenceOpen] = useState(false);
  const [recurrence, setRecurrence] = useState({
    month: 1,
    kind: "purchase" as HerdEventAssumptions["kind"],
    animal_class: "doe" as HerdEventAssumptions["animal_class"],
    count: 10,
    every: 2,
    repeat: 6,
  });
  // 2026-09-17 audit (L-23): the repeat-plan dialog's NumberInputs reported no
  // validity, so "Generate rows" committed the stale last-valid drafts while
  // the boxes displayed invalid ones. Dialog-local twin of invalidFields —
  // deliberately separate, because the shared set gates Run/Save and counts
  // "highlighted" editor fields, which dialog drafts are not.
  const [recurrenceInvalid, setRecurrenceInvalid] = useState<Set<string>>(() => new Set());
  const [explanation, setExplanation] = useState<MetricExplanation | null>(null);
  /** The "?" dialog: one assumption term's explanation + unit/range/value. */
  const [fieldExplain, setFieldExplain] = useState<FieldHelpState | null>(null);
  const [calibration, setCalibration] = useState<FarmCalibrationOut | null>(null);
  const [calibrationLookback, setCalibrationLookback] = useState(24);
  // Updated synchronously by every live calibration-parameter control. A
  // response for parameters the operator changed while it was loading must
  // not replace the editor under the newly displayed selection.
  const calibrationParamsGeneration = useRef(0);

  const [monteCarlo, setMonteCarlo] = useState(false);
  const [sensitivity, setSensitivity] = useState(false);
  const [optimization, setOptimization] = useState(false);
  const [result, setResult] = useState<BoundResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [runningScenarioId, setRunningScenarioId] = useState<number | null>(null);
  // Runs, comparisons, and scenario writes can read or mutate the same saved
  // revision. One synchronous flight prevents run/delete, compare/delete,
  // update/run, and same-render duplicate actions from crossing each other.
  const simulationAction = useSingleFlight();

  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [compareIds, setCompareIds] = useState<string | null>(null);
  // Scenario paging survives refresh and shared links (it replaces rather
  // than pushes, so Back returns to the page, not to a previous page number).
  const { getNumber: scenarioOffsetParam, set: setScenarioParams, searchParams } =
    useUrlState();
  const [scenarioOffset, setScenarioOffsetState] = useState(() =>
    scenarioOffsetParam("scenarios", 0, 0, MAX_PAGE_OFFSET),
  );
  // Params this page did not itself write (back/forward, an edited link)
  // re-seed the local offset, mirroring the feeding ledger and purchases.
  const scenarioParamsKey = searchParams.toString();
  const lastWrittenScenarioParamsRef = useRef(scenarioParamsKey);
  // Latest-ref the reader: adoption keys on the params string changing,
  // not on reader identity churn from per-render params objects.
  const scenarioOffsetParamRef = useRef(scenarioOffsetParam);
  useEffect(() => {
    scenarioOffsetParamRef.current = scenarioOffsetParam;
  });
  useEffect(() => {
    if (lastWrittenScenarioParamsRef.current === scenarioParamsKey) return;
    lastWrittenScenarioParamsRef.current = scenarioParamsKey;
    setScenarioOffsetState(
      scenarioOffsetParamRef.current("scenarios", 0, 0, MAX_PAGE_OFFSET),
    );
  }, [scenarioParamsKey]);
  const setScenarioOffset = useCallback(
    (offset: number) => {
      setScenarioOffsetState(offset);
      const qs = setScenarioParams({ scenarios: offset > 0 ? offset : null });
      if (qs !== null) lastWrittenScenarioParamsRef.current = qs;
    },
    [setScenarioParams],
  );

  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveNotes, setSaveNotes] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);

  const breedsQuery = useListBreedsApiSimulationDefaultsBreedsGet({
    query: { enabled: allowed },
  });
  const breeds = breedsQuery.data?.status === 200 ? breedsQuery.data.data : undefined;

  const defaultsQuery = useBreedDefaultsApiSimulationDefaultsGet(submittedParams, {
    query: { enabled: allowed },
  });

  type DefaultsPayload = Extract<
    (typeof defaultsQuery)["data"],
    { status: 200 }
  >["data"];

  const applyDefaultsToEditor = useCallback((payload: DefaultsPayload) => {
    setAssumptions(payload);
    const nextEvents = payload.events ?? [];
    setEvents(nextEvents);
    setEventKeys(nextEvents.map(() => `event-${eventKeyCounter.current++}`));
    setLoadedScenario(null);
    setCalibration(null);
    setInvalidFields(new Set());
    setEditorVersion((version) => version + 1);
    // Setters and refs are stable; nothing else is captured.
  }, []);

  useEffect(() => {
    if (defaultsQuery.data?.status === 200 && acceptDefaultsRef.current) {
      acceptDefaultsRef.current = false;
      // react-query v5 has no onSuccess: mirror the explicitly requested
      // defaults payload into editable state.
      applyDefaultsToEditor(defaultsQuery.data.data);
    }
  }, [defaultsQuery.data, applyDefaultsToEditor]);

  const snapshotQuery = useHerdSnapshotApiSimulationHerdSnapshotGet(
    // The snapshot buckets females by the breed's age-at-first-breeding, so
    // it must be taken for the breed whose defaults are actually loaded in
    // the editor — not the live dropdown, which the user may have changed
    // without clicking "Load defaults". Keying on the dropdown silently mixed
    // breed A economics with head counts bucketed by breed B's thresholds.
    { breed: submittedParams.breed },
    {
      query: { enabled: false },
    },
  );

  const calibrationQuery = useFarmCalibrationApiSimulationCalibrationGet(
    { breed, system, lookback_months: calibrationLookback },
    { query: { enabled: false } },
  );

  const scenariosQuery = useListScenariosApiSimulationScenariosGet(
    { limit: SCENARIO_PAGE_SIZE, offset: scenarioOffset },
    { query: { enabled: allowed } },
  );
  const scenarioPage =
    scenariosQuery.data?.status === 200 ? scenariosQuery.data.data : undefined;
  const scenarios = scenarioPage?.items ?? [];
  const scenarioTotal = scenarioPage?.total ?? 0;
  // Only usable rows can enter this collection, but a refetch re-validates
  // stored assumptions, so an already-selected row can turn invalid — and its
  // checkbox is then disabled, leaving no way to untick it. Count such rows
  // out here so the "(N selected)" label, the 2–5 compare gate and the ids
  // sent to compare all describe what would actually run. Off-page ids are
  // kept (their rows are unknown here) so farmers can compare scenarios
  // selected from different pages; the compare endpoint re-checks tenant
  // scope and stored validity before running anything.
  const selectedUsableIds = selectedIds.filter((id) => {
    const row = scenarios.find((scenario) => scenario.id === id);
    return row === undefined || scenarioUsable(row);
  });

  useEffect(() => {
    if (scenarioPage === undefined || scenarioOffset === 0) return;
    if (scenarioOffset < scenarioPage.total) return;
    const lastOffset =
      scenarioPage.total === 0
        ? 0
        : Math.floor((scenarioPage.total - 1) / SCENARIO_PAGE_SIZE) *
          SCENARIO_PAGE_SIZE;
    // A concurrent deletion can make the requested page disappear. Re-home
    // it to the real last page instead of displaying a false empty-farm state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setScenarioOffset(lastOffset);
    setCompareIds(null);
  }, [scenarioOffset, scenarioPage, setScenarioOffset]);

  const compareQuery = useCompareScenariosApiSimulationScenariosCompareGet(
    { ids: compareIds ?? "" },
    // onCompare fetches this query inside simulationAction. Auto-fetching
    // from the state change would escape that shared flight.
    { query: { enabled: false } },
  );
  const comparePayload =
    compareIds !== null && compareQuery.data?.status === 200
      ? compareQuery.data.data
      : undefined;

  const runMutation = useRunAdhocApiSimulationRunPost();
  const runScenarioMutation = useRunScenarioApiSimulationScenariosScenarioIdRunPost();
  const createMutation = useCreateScenarioApiSimulationScenariosPost();
  const updateMutation = useUpdateScenarioApiSimulationScenariosScenarioIdPatch();
  const deleteMutation = useDeleteScenarioApiSimulationScenariosScenarioIdDelete();
  // Pending-delete confirmation replaces window.confirm: the native dialog
  // bypasses the app's dialog language, focus management and theming.
  const [pendingDelete, setPendingDelete] = useState<ScenarioRow | null>(null);

  function invalidateScenarios() {
    queryClient.invalidateQueries({
      queryKey: getListScenariosApiSimulationScenariosGetQueryKey(),
    });
    queryClient.invalidateQueries({
      queryKey: getCompareScenariosApiSimulationScenariosCompareGetQueryKey(),
    });
    setCompareIds(null);
  }

  function onScenarioOffsetChange(offset: number) {
    setScenarioOffset(offset);
    // A rendered comparison describes the prior selection/page snapshot.
    // Keep the selections themselves, but require an explicit fresh compare.
    setCompareIds(null);
  }

  function loadScenarioIntoEditor(scenario: ScenarioRow) {
    if (!scenarioUsable(scenario)) return;
    acceptDefaultsRef.current = false;
    editorEpochRef.current += 1;
    setAssumptions(scenario.assumptions);
    const scenarioEvents = scenario.assumptions.events ?? [];
    setEvents(scenarioEvents);
    setEventKeys(
      scenarioEvents.map(() => `event-${eventKeyCounter.current++}`),
    );
    setLoadedScenario(scenario);
    setCalibration(null);
    setInvalidFields(new Set());
    setEditorVersion((version) => version + 1);
  }

  function setFieldValidity(key: string, valid: boolean) {
    // Number inputs call this synchronously for every raw keystroke. Keep the
    // conflict-recovery fence ahead of React state batching even when validity
    // remains unchanged (for example, continued typing in an invalid draft).
    editorContentEpochRef.current += 1;
    setInvalidFields((previous) => {
      const alreadyValid = !previous.has(key);
      if (alreadyValid === valid) return previous;
      const next = new Set(previous);
      if (valid) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  /** Same bookkeeping for the repeat-plan dialog's local set (L-23): no
   * editorContentEpochRef bump — the dialog edits no editor content until
   * rows are actually generated. */
  function setRecurrenceFieldValidity(key: string, valid: boolean) {
    setRecurrenceInvalid((previous) => {
      const alreadyValid = !previous.has(key);
      if (alreadyValid === valid) return previous;
      const next = new Set(previous);
      if (valid) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function updateField(section: string, key: string, value: unknown) {
    acceptDefaultsRef.current = false;
    editorContentEpochRef.current += 1;
    setAssumptions((prev) => {
      if (!prev) return prev;
      const current = (prev as Record<string, SectionValues>)[section] ?? {};
      if (section === "growth" && key === "birth_weight_kg" && typeof value === "number") {
        const curve = Array.isArray(current.weight_by_age_months)
          ? [...(current.weight_by_age_months as number[])]
          : [];
        if (curve.length > 0) curve[0] = value;
        return {
          ...prev,
          growth: { ...current, birth_weight_kg: value, weight_by_age_months: curve },
        };
      }
      if (section === "growth" && key === "weight_by_age_months" && Array.isArray(value)) {
        return {
          ...prev,
          growth: { ...current, weight_by_age_months: value, birth_weight_kg: value[0] },
        };
      }
      return { ...prev, [section]: { ...current, [key]: value } };
    });
  }

  function updateNestedField(section: string, key: string, subKey: string, value: unknown) {
    acceptDefaultsRef.current = false;
    editorContentEpochRef.current += 1;
    setAssumptions((prev) => {
      if (!prev) return prev;
      const current = (prev as Record<string, SectionValues>)[section] ?? {};
      const nested = (current[key] as SectionValues | undefined) ?? {};
      return {
        ...prev,
        [section]: { ...current, [key]: { ...nested, [subKey]: value } },
      };
    });
  }

  /** Horizon from the meta section; gates event-month validation. */
  const horizonMonths = assumptions?.meta?.horizon_months ?? 240;
  const simVocabulary = farmVocabulary;
  const eventErrors = validateEvents(events, horizonMonths, t);
  const assumptionErrors: string[] = [];
  if (assumptions) {
    const start = assumptions.meta?.start_year_month;
    const match = start?.match(/^(\d{4})-(\d{2})$/);
    if (
      !match ||
      Number(match[1]) < 1900 ||
      Number(match[1]) > 2200 ||
      Number(match[2]) < 1 ||
      Number(match[2]) > 12
    )
      assumptionErrors.push(t("simulation.validation.startYearMonth"));

    const finance = assumptions.finance;
    if (
      finance &&
      typeof finance.loan_fraction_of_project_cost === "number" &&
      typeof finance.subsidy_fraction === "number" &&
      finance.loan_fraction_of_project_cost + finance.subsidy_fraction > 1
    )
      assumptionErrors.push(t("simulation.validation.loanPlusSubsidy"));
    if (
      finance &&
      typeof finance.moratorium_months === "number" &&
      typeof finance.loan_term_months === "number" &&
      finance.moratorium_months >= finance.loan_term_months
    )
      assumptionErrors.push(t("simulation.validation.moratorium"));

    const feed = assumptions.feed;
    if (
      feed &&
      typeof feed.initial_fodder_stock_kg_dm === "number" &&
      typeof feed.fodder_storage_capacity_kg_dm === "number" &&
      feed.initial_fodder_stock_kg_dm > feed.fodder_storage_capacity_kg_dm
    )
      assumptionErrors.push(t("simulation.validation.fodderStock"));

    const costs = assumptions.costs;
    if (
      costs?.capacity_basis === "planned" &&
      (costs.planned_capacity_head ?? 0) <= 0
    )
      assumptionErrors.push(t("simulation.validation.plannedCapacity"));

    const festivalMonths = assumptions.sales?.festival_sale_months ?? [];
    if (new Set(festivalMonths).size !== festivalMonths.length)
      assumptionErrors.push(t("simulation.validation.festivalDuplicates"));
    if (
      festivalMonths.some(
        (month) => !Number.isInteger(month) || month < 1 || month > horizonMonths,
      )
    )
      assumptionErrors.push(t("simulation.validation.festivalMonths", { horizon: horizonMonths }));

    const optimizationAssumptions = assumptions.optimization;
    if (
      optimizationAssumptions &&
      typeof optimizationAssumptions.doe_scale_low === "number" &&
      typeof optimizationAssumptions.doe_scale_high === "number" &&
      optimizationAssumptions.doe_scale_low > optimizationAssumptions.doe_scale_high
    )
      assumptionErrors.push(
        t("simulation.validation.herdScale", {
          herd:
            language === "en"
              ? simVocabulary.femaleAdultPlural
              : t("simulation.token.does"),
        }),
      );

    const growth = assumptions.growth;
    const weightCurve = growth?.weight_by_age_months;
    // updateField mirrors birth_weight_kg into weight_by_age_months[0], and
    // that path never runs NumberArrayInput's `nondecreasing` rule — so raising
    // the birth weight past month 1 must be caught here or the run only fails
    // at the API with a message that never names the birth weight field.
    if (weightCurve?.some((weight, i) => i > 0 && weight < weightCurve[i - 1]))
      assumptionErrors.push(t("simulation.validation.weightCurve"));
    const yearling = weightCurve
      ? Math.max(...weightCurve.slice(0, 13))
      : null;
    if (
      growth &&
      yearling !== null &&
      typeof growth.adult_weight_doe_kg === "number" &&
      typeof growth.adult_weight_buck_kg === "number" &&
      (growth.adult_weight_doe_kg < yearling || growth.adult_weight_buck_kg < yearling)
    )
      assumptionErrors.push(
        t("simulation.validation.adultWeight", {
          female:
            language === "en" ? simVocabulary.femaleAdult : t("simulation.token.doe"),
          male:
            language === "en" ? simVocabulary.maleAdult : t("simulation.token.buck"),
        }),
      );

    const risk = assumptions.risk;
    if (risk) {
      for (const [key, value] of Object.entries(risk)) {
        if (
          value &&
          typeof value === "object" &&
          "low" in value &&
          "high" in value &&
          !(
            Number((value as SectionValues).low) <= 1 &&
            Number((value as SectionValues).high) >= 1
          )
        )
          assumptionErrors.push(
            t("simulation.validation.riskBracket", {
              label: localizedFieldLabel(key, t, language),
            }),
          );
      }
    }
  }
  if (events.length > 500)
    assumptionErrors.push(t("simulation.validation.maxEvents"));
  const hasEditorErrors =
    invalidFields.size > 0 || assumptionErrors.length > 0 || eventErrors.length > 0;
  const currentPayload = assumptions ? { ...assumptions, events } : null;
  const currentFingerprint = currentPayload ? assumptionsFingerprint(currentPayload) : null;
  const currentOptions = runOptionsFingerprint(monteCarlo, sensitivity, optimization);

  /** The basis a result claims to describe, as it stands right now: the editor
   * for an ad-hoc run — and for a scenario run while the editor is showing
   * that same scenario — otherwise the saved scenario the run came from.
   * `null` when the basis is off-screen and cannot be compared. Measuring a
   * scenario run against unrelated editor state left the staleness banner
   * permanently on, which drowned out the real signal. */
  function liveFingerprint(bound: BoundResult): string | null {
    if (bound.scenarioId === null || bound.scenarioId === loadedScenario?.id)
      return currentFingerprint;
    const scenario = scenarios.find((row) => row.id === bound.scenarioId);
    return scenario && scenarioUsable(scenario)
      ? assumptionsFingerprint(scenario.assumptions)
      : null;
  }

  const resultIsStale =
    result !== null &&
    (result.options !== currentOptions ||
      (liveFingerprint(result) ?? result.fingerprint) !== result.fingerprint);

  function addEvent() {
    acceptDefaultsRef.current = false;
    editorContentEpochRef.current += 1;
    setEventKeys((previous) => [...previous, `event-${eventKeyCounter.current++}`]);
    setEvents((prev) => [
      ...prev,
      {
        month: Math.min(12, horizonMonths),
        kind: "purchase",
        animal_class: "doe",
        count: 10,
        price_per_head: null,
      },
    ]);
  }

  function updateEvent(index: number, patch: Partial<HerdEventAssumptions>) {
    acceptDefaultsRef.current = false;
    editorContentEpochRef.current += 1;
    setEvents((prev) =>
      prev.map((event, i) => (i === index ? { ...event, ...patch } : event)),
    );
  }

  function removeEvent(index: number) {
    acceptDefaultsRef.current = false;
    editorContentEpochRef.current += 1;
    const removedKey = eventKeys[index];
    setEvents((prev) => prev.filter((_, i) => i !== index));
    setEventKeys((previous) => previous.filter((_, i) => i !== index));
    setInvalidFields((previous) => {
      const next = new Set(
        [...previous].filter((key) => !removedKey || !key.startsWith(`event:${removedKey}:`)),
      );
      return next;
    });
  }

  /** Assumptions plus the scheduled events, as sent to run/save endpoints. */
  function assumptionsWithEvents(): SimulationAssumptions | null {
    return assumptions ? { ...assumptions, events } : null;
  }

  /** Expand "N animals every M months" into concrete event rows.
   *
   * The server stays the single source of truth for one event's schema; this
   * only multiplies rows the same way a user would have clicked them, with
   * the 500-event ceiling enforced before a single row lands.
   */
  function applyRecurrence() {
    const { month, kind, animal_class, count, every, repeat } = recurrence;
    const safeRepeat = Math.max(1, Math.min(120, Math.floor(repeat)));
    const safeEvery = Math.max(1, Math.min(120, Math.floor(every)));
    const rows: HerdEventAssumptions[] = [];
    for (let i = 0; i < safeRepeat; i++) {
      const m = month + i * safeEvery;
      if (m > horizonMonths) break;
      rows.push({ month: m, kind, animal_class, count, price_per_head: null });
    }
    if (rows.length === 0) {
      toast.error(t("simulation.error.recurrenceBeyondHorizon"));
      return;
    }
    if (events.length + rows.length > 500) {
      toast.error(
        t("simulation.error.recurrenceCapacity", {
          rows: rows.length,
          remaining: 500 - events.length,
        }),
      );
      return;
    }
    acceptDefaultsRef.current = false;
    editorContentEpochRef.current += 1;
    setEventKeys((previous) => [
      ...previous,
      ...rows.map(() => `event-${eventKeyCounter.current++}`),
    ]);
    setEvents((prev) => [...prev, ...rows]);
    setRecurrenceOpen(false);
    toast.success(
      t("simulation.toast.recurrenceAdded", {
        count: rows.length,
        kind: language === "en" ? kind : eventKindItems(t)[kind],
      }),
    );
  }

  async function onUseCurrentHerd() {
    // Do NOT clear the latch up front. Clearing it here made a "Load defaults"
    // that was already in flight land on a `false` latch and be dropped
    // silently — while its button flipped back as though it had applied — so
    // the new breed's head counts got merged into the OLD breed's economics.
    const epoch = ++editorEpochRef.current;
    const farmScope = captureFarmScope();
    try {
      const res = await snapshotQuery.refetch();
      if (res.isError || res.data?.status !== 200) {
        if (!farmScope()) return;
        toast.error(errorMessage(res.error, t("simulation.error.herdSnapshot")));
        return;
      }
      if (editorEpochRef.current !== epoch) {
        // A different loader replaced the editor while this snapshot was in
        // flight; applying it now would overwrite that scenario's saved head
        // counts and silently detach the scenario binding. Never fail
        // silently — say so, so the operator can click again.
        toast.error(t("simulation.error.herdSnapshotStale"));
        return;
      }
      acceptDefaultsRef.current = false;
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
      setLoadedScenario(null);
      setInvalidFields((previous) =>
        new Set([...previous].filter((key) => key.startsWith("event:"))),
      );
      setEditorVersion((version) => version + 1);
      if (!farmScope()) return;
      toast.success(t("simulation.toast.currentHerdLoaded", { count: snap.total_head }));
    } catch (err) {
      if (!farmScope()) return;
      toast.error(errorMessage(err, t("simulation.error.herdSnapshot")));
    }
  }

  async function onCalibrateFromFarm() {
    const farmScope = captureFarmScope();
    // Claim an ordered editor intent, but do not clear a pending defaults
    // latch. If calibration fails, that older request is still a valid
    // fallback; if a NEW defaults click happens, it increments this epoch and
    // makes the calibration continuation stale.
    const epoch = ++editorEpochRef.current;
    const paramsGeneration = calibrationParamsGeneration.current;
    try {
      const res = await calibrationQuery.refetch();
      if (res.isError || res.data?.status !== 200) {
        if (!farmScope()) return;
        toast.error(errorMessage(res.error, t("simulation.error.calibration")));
        return;
      }
      if (
        editorEpochRef.current !== epoch ||
        calibrationParamsGeneration.current !== paramsGeneration
      ) {
        toast.error(t("simulation.error.calibrationStale"));
        return;
      }
      acceptDefaultsRef.current = false;
      const calibrated = res.data.data;
      setAssumptions(calibrated.assumptions);
      const nextEvents = calibrated.assumptions.events ?? [];
      setEvents(nextEvents);
      setEventKeys(nextEvents.map(() => `event-${eventKeyCounter.current++}`));
      setSubmittedParams({ breed, system });
      setLoadedScenario(null);
      setCalibration(calibrated);
      setInvalidFields(new Set());
      setEditorVersion((version) => version + 1);
      if (!farmScope()) return;
      toast.success(t("simulation.toast.calibrated", { count: calibrated.evidence.length }));
    } catch (err) {
      if (!farmScope()) return;
      toast.error(errorMessage(err, t("simulation.error.calibration")));
    }
  }

  async function onRun() {
    await simulationAction.run(async () => {
      const farmScope = captureFarmScope();
      const payload = assumptionsWithEvents();
      if (!payload || hasEditorErrors) return;
      setRunError(null);
      try {
        const res = await runMutation.mutateAsync({
          data: {
            assumptions: payload,
            monte_carlo: monteCarlo,
            sensitivity,
            optimization,
          },
        });
        if (res.status === 200 && farmScope())
          setResult({
            data: res.data,
            fingerprint: assumptionsFingerprint(payload),
            options: runOptionsFingerprint(monteCarlo, sensitivity, optimization),
            scenarioId: null,
            source: { kind: "editor" },
          });
      } catch (err) {
        if (!farmScope()) return;
        const message = runErrorMessage(err, t("simulation.error.run"), t);
        setRunError(message);
        toast.error(message);
      }
    });
  }

  async function onRunScenario(scenario: ScenarioRow) {
    if (!scenarioUsable(scenario)) return;
    await simulationAction.run(async () => {
      const farmScope = captureFarmScope();
      setRunError(null);
      setRunningScenarioId(scenario.id);
      try {
        const res = await runScenarioMutation.mutateAsync({
          scenarioId: scenario.id,
          params: { monte_carlo: monteCarlo, sensitivity, optimization },
        });
        if (res.status === 200 && farmScope())
          setResult({
            data: res.data,
            fingerprint: assumptionsFingerprint(scenario.assumptions),
            options: runOptionsFingerprint(monteCarlo, sensitivity, optimization),
            scenarioId: scenario.id,
            source: { kind: "scenario", name: scenario.name },
          });
      } catch (err) {
        if (!farmScope()) return;
        const message = runErrorMessage(err, t("simulation.error.scenarioRun"), t);
        setRunError(message);
        toast.error(message);
      } finally {
        setRunningScenarioId(null);
      }
    });
  }

  async function onDeleteScenario(scenario: ScenarioRow) {
    await simulationAction.run(async () => {
      const farmScope = captureFarmScope();
      setPendingDelete(null);
      try {
        await deleteMutation.mutateAsync({
          scenarioId: scenario.id,
          params: { expected_revision: scenario.revision },
        });
        if (!farmScope()) return;
        toast.success(t("simulation.toast.scenarioDeleted"));
        // The deleted row owned focus; the section heading is the nearest
        // sensible home once it unmounts.
        document.getElementById("sim-scenarios")?.focus({ preventScroll: true });
        // Read current state at completion: the operator may have loaded this
        // row while the DELETE was in flight. Never leave the editor/result
        // bound to a scenario that no longer exists, while preserving a
        // different scenario loaded in the meantime.
        setLoadedScenario((current) =>
          current?.id === scenario.id ? null : current,
        );
        setResult((current) =>
          current?.scenarioId === scenario.id ? null : current,
        );
        setSelectedIds((prev) => prev.filter((id) => id !== scenario.id));
        const remainingTotal = Math.max(0, scenarioTotal - 1);
        if (scenarioOffset > 0 && scenarioOffset >= remainingTotal) {
          setScenarioOffset(
            remainingTotal === 0
              ? 0
              : Math.floor((remainingTotal - 1) / SCENARIO_PAGE_SIZE) *
                  SCENARIO_PAGE_SIZE,
          );
        }
        invalidateScenarios();
      } catch (err) {
        // Same farm-scope fence as every sibling continuation: a farm switch
        // during a failing DELETE must not surface in the new farm's UI
        // (RT-P2-5).
        if (!farmScope()) return;
        toast.error(errorMessage(err, t("simulation.error.deleteScenario")));
      }
    });
  }

  async function onCompare() {
    if (
      selectedUsableIds.length < 2 ||
      selectedUsableIds.length > MAX_COMPARE_SCENARIOS
    )
      return;
    const ids = selectedUsableIds.join(",");
    await simulationAction.run(async () => {
      setCompareIds(ids);
      try {
        await queryClient.fetchQuery(
          getCompareScenariosApiSimulationScenariosCompareGetQueryOptions(
            { ids },
            // This is an explicit user-requested execution, not a passive
            // cache read. Preserve the former refetch-on-repeat behavior.
            { query: { staleTime: 0 } },
          ),
        );
      } catch {
        // The disabled observer above still receives and renders the cached
        // query error; contain the rejection because this is a click handler.
      }
    });
  }

  async function onSaveScenario() {
    const payload = assumptionsWithEvents();
    if (!payload || !saveName.trim()) return;
    if (hasEditorErrors) return;
    await simulationAction.run(async () => {
      const farmScope = captureFarmScope();
      setSaveError(null);
      try {
        const created = await createMutation.mutateAsync({
          data: {
            name: saveName.trim(),
            notes: saveNotes.trim(),
            assumptions: payload,
          },
        });
        if (!farmScope()) return;
        toast.success(t("simulation.toast.scenarioSaved"));
        if (created.status === 201) {
          // The API intentionally preserves oldest-first ordering, so the new
          // row belongs on the final page rather than page zero.
          const nextTotal = scenarioTotal + 1;
          setScenarioOffset(
            Math.floor((nextTotal - 1) / SCENARIO_PAGE_SIZE) * SCENARIO_PAGE_SIZE,
          );
        }
        invalidateScenarios();
        setSaveOpen(false);
        setSaveName("");
        setSaveNotes("");
      } catch (err) {
        if (!farmScope()) return;
        const message = errorMessage(err, t("simulation.error.saveScenario"));
        setSaveError(message);
        toast.error(message);
      }
    });
  }

  async function onUpdateScenario() {
    const payload = assumptionsWithEvents();
    if (!payload || !loadedScenario) return;
    if (hasEditorErrors || !scenarioUsable(loadedScenario)) return;
    const epoch = editorEpochRef.current;
    const contentEpoch = editorContentEpochRef.current;
    await simulationAction.run(async () => {
      const farmScope = captureFarmScope();
      try {
        const updated = await updateMutation.mutateAsync({
          scenarioId: loadedScenario.id,
          data: { assumptions: payload, expected_revision: loadedScenario.revision },
        });
        // Loading defaults, calibration, a herd snapshot, or another saved
        // scenario while this PATCH is in flight makes its binding stale. The
        // write still succeeded; only its late UI continuation is discarded.
        if (updated.status === 200 && editorEpochRef.current === epoch) {
          setLoadedScenario(updated.data);
        }
        if (!farmScope()) return;
        toast.success(t("simulation.toast.scenarioUpdated"));
        invalidateScenarios();
      } catch (err) {
        if (!farmScope()) return;
        if (err instanceof ApiError && err.status === 409) {
          invalidateScenarios();
          try {
            const fresh = await queryClient.fetchQuery(
              getGetScenarioApiSimulationScenariosScenarioIdGetQueryOptions(
                loadedScenario.id,
                { query: { staleTime: 0 } },
              ),
            );
            if (
              fresh.status === 200 &&
              editorEpochRef.current === epoch &&
              editorContentEpochRef.current === contentEpoch &&
              scenarioUsable(fresh.data)
            ) {
              // Replace the stale editor wholesale. Merely advancing the
              // revision while preserving old assumptions would turn the
              // retry into a lost update against the other operator.
              loadScenarioIntoEditor(fresh.data);
              toast.error(t("simulation.error.scenarioConflict"));
              return;
            }
          } catch {
            // Keep the original conflict detail below when refresh fails.
          }
        }
        toast.error(errorMessage(err, t("simulation.error.updateScenario")));
      }
    });
  }

  /** Species-aware display label for one assumption field. */
  function fieldLabelFor(key: string): string {
    return speciesAwareLabel(localizedFieldLabel(key, t, language));
  }

  /** Opens the "?" dialog for one field: explanation plus unit/range/value. */
  function openFieldHelp(
    section: string,
    key: string,
    value: unknown,
    rule?: NumericRule | NumberArrayRule,
    options?: Record<string, string>,
    subKey?: string,
  ): void {
    const path = subKey ? `${section}.${key}.${subKey}` : `${section}.${key}`;
    const entry = simulationFieldHelp(path, simVocabulary);
    // The established English help entries use carefully written sentence
    // casing (for example, “Meat price”). Preserve that default wording;
    // Telugu instead uses the token-localized field name rather than leaking
    // the English entry label.
    const label =
      language === "en"
        ? (entry?.label ?? fieldLabelFor(subKey ?? key))
        : fieldLabelFor(subKey ?? key);
    setFieldExplain({
      label,
      body: entry?.help.body ?? null,
      facts: fieldFacts(rule, value, t, language, options),
    });
  }

  /** One editor row; the input type follows the value type. */
  function renderField(section: string, key: string, value: unknown) {
    const id = `sim-${section}-${key}`;
    const numericOptions = numericFieldOptions(t)[`${section}.${key}`];
    if (numericOptions && typeof value === "number") {
      return (
        <div key={id} className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={id}>{fieldLabelFor(key)}</Label>
            <FieldHelpButton
              label={fieldLabelFor(key)}
              onClick={() => openFieldHelp(section, key, value)}
            />
          </div>
          <Select
            value={String(value)}
            onValueChange={(v) => updateField(section, key, Number(v))}
            items={numericOptions}
          >
            <SelectTrigger id={id} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(numericOptions).map(([option, label]) => (
                <SelectItem key={option} value={option}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      );
    }
    const stringOptions = stringFieldOptions(t)[`${section}.${key}`];
    if (stringOptions) {
      return (
        <div key={id} className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={id}>{fieldLabelFor(key)}</Label>
            <FieldHelpButton
              label={fieldLabelFor(key)}
              onClick={() => openFieldHelp(section, key, value, undefined, stringOptions)}
            />
          </div>
          <Select
            value={String(value)}
            onValueChange={(v) => updateField(section, key, v)}
            items={stringOptions}
          >
            <SelectTrigger id={id} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(stringOptions).map(([option, label]) => (
                <SelectItem key={option} value={option}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      );
    }
    if (
      NULLABLE_NUMBER_FIELDS.has(`${section}.${key}`) &&
      (value === null || typeof value === "number")
    ) {
      const rule = numericRule(section, key, t);
      return (
        <div key={id} className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={id}>{fieldLabelFor(key)}</Label>
            <FieldHelpButton
              label={fieldLabelFor(key)}
              onClick={() => openFieldHelp(section, key, value, rule)}
            />
          </div>
          <NumberInput
            id={id}
            value={value}
            nullable
            {...rule}
            onValidityChange={(valid) => setFieldValidity(`field:${id}`, valid)}
            onCommit={(n) => updateField(section, key, n)}
          />
          <p className="text-xs text-muted-foreground">
            {rule.unit
              ? t("simulation.field.blankNoLimitWithUnit", { unit: rule.unit })
              : t("simulation.field.blankNoLimit")}
          </p>
        </div>
      );
    }
    if (typeof value === "number") {
      const rule = numericRule(section, key, t);
      return (
        <div key={id} className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={id}>{fieldLabelFor(key)}</Label>
            <FieldHelpButton
              label={fieldLabelFor(key)}
              onClick={() => openFieldHelp(section, key, value, rule)}
            />
          </div>
          <NumberInput
            key={
              section === "meta" && key === "horizon_months"
                ? `${id}:${horizonInputVersion}`
                : id
            }
            id={id}
            value={value}
            {...rule}
            onValidityChange={(valid) => setFieldValidity(`field:${id}`, valid)}
            onCommit={(n) => updateField(section, key, n)}
          />
          {rule.unit && (
            <p className="text-xs text-muted-foreground">
              {t("simulation.field.unit", { unit: rule.unit })}
            </p>
          )}
        </div>
      );
    }
    if (typeof value === "boolean") {
      return (
        <div key={id} className="flex items-center gap-2">
          <Checkbox
            id={id}
            checked={value}
            onCheckedChange={(checked) => updateField(section, key, checked === true)}
          />
          <Label htmlFor={id} className="font-normal">
            {fieldLabelFor(key)}
          </Label>
          <FieldHelpButton
            label={fieldLabelFor(key)}
            onClick={() => openFieldHelp(section, key, value)}
          />
        </div>
      );
    }
    if (typeof value === "string") {
      const isMonth = section === "meta" && key === "start_year_month";
      const monthIsInvalid =
        isMonth &&
        !/^(?:19\d{2}|20\d{2}|21\d{2}|2200)-(?:0[1-9]|1[0-2])$/.test(value);
      const errorId = `${id}-error`;
      return (
        <div key={id} className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={id}>{fieldLabelFor(key)}</Label>
            <FieldHelpButton
              label={fieldLabelFor(key)}
              onClick={() => openFieldHelp(section, key, value)}
            />
          </div>
          <Input
            id={id}
            type={isMonth ? "month" : "text"}
            value={value}
            min={isMonth ? "1900-01" : undefined}
            max={isMonth ? "2200-12" : undefined}
            aria-invalid={monthIsInvalid || undefined}
            aria-describedby={monthIsInvalid ? errorId : undefined}
            onChange={(e) => updateField(section, key, e.target.value)}
          />
          {monthIsInvalid && (
            <p id={errorId} role="alert" className="text-sm text-destructive">
              {t("simulation.validation.startYearMonthInput")}
            </p>
          )}
        </div>
      );
    }
    if (Array.isArray(value)) {
      const rule = numberArrayRule(section, key, horizonMonths, t);
      return (
        <div key={id} className="space-y-1.5 sm:col-span-2 lg:col-span-3">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={id}>
              {t("simulation.field.arrayLabel", {
                label: fieldLabelFor(key),
                items: rule.itemLabel,
              })}
            </Label>
            <FieldHelpButton
              label={fieldLabelFor(key)}
              onClick={() => openFieldHelp(section, key, value, rule)}
            />
          </div>
          <NumberArrayInput
            id={id}
            value={value as number[]}
            rule={rule}
            onValidityChange={(valid) => setFieldValidity(`field:${id}`, valid)}
            onCommit={(numbers) => updateField(section, key, numbers)}
          />
        </div>
      );
    }
    if (value && typeof value === "object") {
      // Nested parameter object (e.g. risk.meat_price = {enabled, low, high}).
      const parentLabel = fieldLabelFor(key);
      return (
        <div
          key={id}
          className="space-y-2 rounded-lg border p-3 sm:col-span-2 lg:col-span-3"
        >
          <div className="flex items-center gap-1.5">
            <p className="text-sm font-medium">{parentLabel}</p>
            <FieldHelpButton
              label={parentLabel}
              onClick={() => openFieldHelp(section, key, value)}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(value as SectionValues).map(([subKey, subValue]) =>
              renderNestedField(section, key, subKey, subValue),
            )}
          </div>
        </div>
      );
    }
    return null;
  }

  function renderNestedField(section: string, key: string, subKey: string, value: unknown) {
    const id = `sim-${section}-${key}-${subKey}`;
    const subLabel = fieldLabelFor(subKey);
    if (typeof value === "number") {
      const rule: NumericRule =
        section === "risk" && (subKey === "low" || subKey === "high")
          ? { exclusiveMin: 0, max: 100, unit: t("simulation.unit.multiplier") }
          : numericRule(section, subKey, t);
      return (
        <div key={id} className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={id}>{subLabel}</Label>
            <FieldHelpButton
              label={subLabel}
              onClick={() => openFieldHelp(section, key, value, rule, undefined, subKey)}
            />
          </div>
          <NumberInput
            id={id}
            value={value}
            {...rule}
            onValidityChange={(valid) => setFieldValidity(`field:${id}`, valid)}
            onCommit={(n) => updateNestedField(section, key, subKey, n)}
          />
          {rule.unit && (
            <p className="text-xs text-muted-foreground">
              {t("simulation.field.unit", { unit: rule.unit })}
            </p>
          )}
        </div>
      );
    }
    if (typeof value === "boolean") {
      return (
        <div key={id} className="flex items-center gap-2">
          <Checkbox
            id={id}
            checked={value}
            onCheckedChange={(checked) =>
              updateNestedField(section, key, subKey, checked === true)
            }
          />
          <Label htmlFor={id} className="font-normal">
            {subLabel}
          </Label>
          <FieldHelpButton
            label={subLabel}
            onClick={() => openFieldHelp(section, key, value, undefined, undefined, subKey)}
          />
        </div>
      );
    }
    if (typeof value === "string") {
      return (
        <div key={id} className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={id}>{subLabel}</Label>
            <FieldHelpButton
              label={subLabel}
              onClick={() => openFieldHelp(section, key, value, undefined, undefined, subKey)}
            />
          </div>
          <Input
            id={id}
            type="text"
            value={value}
            onChange={(e) => updateNestedField(section, key, subKey, e.target.value)}
          />
        </div>
      );
    }
    return null;
  }

  function renderResults(r: SimulationResult) {
    const m = r.metrics;
    // Water results are an optional contract addition (generated client lags).
    const peakWater = (r as { peak_water_litres_per_day?: number | null })
      .peak_water_litres_per_day;
    const annualWater =
      (r as { annual_water_litres?: number[] | null }).annual_water_litres ?? [];
    const capacityShortfall = Math.max(
      0,
      r.project_cost_breakdown.projected_peak_head -
        r.project_cost_breakdown.capacity_places,
    );
    const explanationsByKey = new Map(
      (r.metric_explanations ?? []).map((entry) => [entry.key, entry]),
    );
    const infoFor = (key: string) => {
      const entry = explanationsByKey.get(key);
      return entry ? () => setExplanation(entry) : undefined;
    };
    const sortedSensitivity = r.sensitivity
      ? [...r.sensitivity].sort(
          (a, b) =>
            Math.max(Math.abs(b.delta_npv_low), Math.abs(b.delta_npv_high)) -
            Math.max(Math.abs(a.delta_npv_low), Math.abs(a.delta_npv_high)),
        )
      : null;
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          <MetricCard
            value={formatMoney(m.npv)}
            label={t("simulation.metric.npv")}
            icon={IndianRupee}
            tint={m.npv >= 0 ? "success" : "destructive"}
            onInfo={infoFor("npv")}
          />
          <MetricCard
            value={formatPercent(m.irr)}
            label={t("simulation.metric.irr")}
            icon={Percent}
            onInfo={infoFor("irr")}
          />
          <MetricCard
            value={formatPercent(m.mirr)}
            label={t("simulation.metric.mirr")}
            icon={TrendingUp}
            onInfo={infoFor("mirr")}
          />
          <MetricCard
            value={formatRatio(m.bcr)}
            label={t("simulation.metric.bcr")}
            icon={Scale}
            tint={m.bcr === null ? "default" : m.bcr >= 1 ? "success" : "destructive"}
            onInfo={infoFor("bcr")}
          />
          <MetricCard
            value={formatRatio(m.avg_dscr)}
            label={t("simulation.metric.avgDscr")}
            icon={Gauge}
            tint={
              m.avg_dscr === null
                ? "default"
                : m.avg_dscr >= 1.2
                  ? "success"
                  : m.avg_dscr >= 1
                    ? "warning"
                    : "destructive"
            }
            onInfo={infoFor("avg_dscr")}
          />
          <MetricCard
            value={formatRatio(m.min_dscr)}
            label={t("simulation.metric.minimumDscr")}
            icon={ShieldAlert}
            tint={
              m.min_dscr === null
                ? "default"
                : m.min_dscr >= 1.2
                  ? "success"
                  : m.min_dscr >= 1
                    ? "warning"
                    : "destructive"
            }
            onInfo={infoFor("min_dscr")}
          />
          <MetricCard
            value={formatPercent(m.operating_margin)}
            label={t("simulation.metric.operatingMargin")}
            icon={TrendingUp}
            tint={
              m.operating_margin === null
                ? "default"
                : m.operating_margin >= 0
                  ? "success"
                  : "destructive"
            }
            onInfo={infoFor("operating_margin")}
          />
          <MetricCard
            value={m.payback_month === null ? "—" : String(m.payback_month)}
            label={t("simulation.metric.paybackMonth")}
            icon={CalendarClock}
            tint="warning"
            onInfo={infoFor("payback_month")}
          />
          <MetricCard
            value={
              m.break_even_meat_price_per_kg === null
                ? "—"
                : formatMoney(m.break_even_meat_price_per_kg)
            }
            label={t("simulation.metric.breakEvenMeat")}
            icon={Beef}
            tint="warning"
            onInfo={infoFor("break_even_meat_price_per_kg")}
          />
          <MetricCard
            value={formatMoney(m.project_cost)}
            label={t("simulation.metric.projectCost")}
            icon={Wallet}
            onInfo={infoFor("project_cost")}
          />
          <MetricCard
            value={formatMoney(m.loan_amount)}
            label={t("simulation.metric.loan")}
            icon={Landmark}
            onInfo={infoFor("loan_amount")}
          />
          <MetricCard
            value={formatMoney(m.subsidy_amount)}
            label={t("simulation.metric.subsidy")}
            icon={HandCoins}
            tint="success"
            onInfo={infoFor("subsidy_amount")}
          />
          <MetricCard
            value={formatMoney(m.equity)}
            label={t("simulation.metric.equity")}
            icon={PiggyBank}
            onInfo={infoFor("equity")}
          />
          <MetricCard
            value={formatHead(m.peak_capacity_head)}
            label={t("simulation.metric.fundedCapacity")}
            icon={Building2}
            onInfo={infoFor("peak_capacity_head")}
          />
          <MetricCard
            value={formatMoney(m.terminal_value)}
            label={t("simulation.metric.terminalValue")}
            icon={Wallet}
            onInfo={infoFor("terminal_value")}
          />
          <MetricCard
            value={formatMoney(m.minimum_cash_balance)}
            label={t("simulation.metric.minimumCashMonth", { month: m.minimum_cash_month })}
            icon={Wallet}
            tint={m.minimum_cash_balance < 0 ? "destructive" : "success"}
            onInfo={infoFor("minimum_cash_balance")}
          />
          <MetricCard
            value={formatMoney(m.additional_working_capital_required)}
            label={t("simulation.metric.additionalWorkingCapital")}
            icon={HandCoins}
            tint={m.additional_working_capital_required > 0 ? "destructive" : "success"}
            onInfo={infoFor("additional_working_capital_required")}
          />
          <MetricCard
              value={t("simulation.metric.acresValue", {
                value: formatRatio(r.feed_summary.land_requirement_acres),
              })}
              label={t("simulation.metric.fodderLandRequired")}
            icon={Wheat}
          />
          <MetricCard
            value={String(r.feed_summary.fodder_deficit_months)}
            label={t("simulation.metric.fodderDeficitMonths")}
            icon={Wheat}
            tint={r.feed_summary.fodder_deficit_months > 0 ? "warning" : "success"}
          />
          {/* Water results are an optional contract addition; the generated
           * client types lag it, so read defensively. */}
          {typeof peakWater === "number" && (
            <MetricCard
              value={t("simulation.metric.litresPerDay", {
                value: litres(peakWater, language),
              })}
              label={t("simulation.metric.peakWaterDemand")}
              icon={Droplets}
              tint="warning"
            />
          )}
        </div>
        {annualWater.length > 0 && (
          <p className="text-sm text-muted-foreground">
            {t("simulation.result.annualWaterDemand", {
              values: annualWater
                .map((v, i) =>
                  t("simulation.result.annualWaterYear", {
                    year: i + 1,
                    value: litres(v, language),
                  }),
                )
                .join(" · "),
            })}
          </p>
        )}

        {r.narrative_report && r.narrative_report.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>{t("simulation.result.report")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              {r.narrative_report.map((section) => {
                const verdict =
                  section.key === "viability_verdict" &&
                  typeof section.figures?.verdict === "string"
                    ? section.figures.verdict
                    : null;
                const verdictLabel =
                  verdict === null ? null : localizedVerdict(verdict, t, language);
                return (
                  <section key={section.key} className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-medium">{section.title}</h3>
                      {verdictLabel && (
                        <Badge
                          variant={
                            verdict === "VIABLE"
                              ? "success"
                              : verdict === "VIABLE WITH CAUTION"
                                ? "warning"
                                : "destructive"
                          }
                        >
                          {verdictLabel}
                        </Badge>
                      )}
                    </div>
                    {section.paragraphs.map((paragraph, i) => (
                      <p key={i} className="text-sm text-muted-foreground">
                        {paragraph}
                      </p>
                    ))}
                  </section>
                );
              })}
            </CardContent>
          </Card>
        )}

        {capacityShortfall > 0 && (
          <div
            role="alert"
            className="flex gap-3 border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <p>
              {t("simulation.result.capacityShortfall", {
                count: formatHead(capacityShortfall),
              })}
            </p>
          </div>
        )}

        <DataTableCard
          title={t("simulation.table.capitalBridge")}
          description={t("simulation.table.capitalBridgeDescription", {
            basis: localizedFieldLabel(r.project_cost_breakdown.capacity_basis, t, language),
            places: formatHead(r.project_cost_breakdown.capacity_places),
            peak: formatHead(r.project_cost_breakdown.projected_peak_head),
          })}
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("simulation.table.component")}</TableHead>
                <TableHead className="text-right">{t("simulation.table.openingProjectCost")}</TableHead>
                <TableHead className="text-right">{t("simulation.table.closingRecovery")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[
                {
                  label: t("simulation.table.shed"),
                  opening: r.project_cost_breakdown.shed_cost,
                  closing: r.terminal_value_breakdown.shed,
                },
                {
                  label: t("simulation.table.equipment"),
                  opening: r.project_cost_breakdown.equipment_cost,
                  closing: r.terminal_value_breakdown.equipment,
                },
                {
                  label: t("simulation.table.livestock"),
                  opening: r.project_cost_breakdown.stock_cost,
                  closing: r.terminal_value_breakdown.livestock,
                },
                {
                  label: t("simulation.table.workingCapital"),
                  opening: r.project_cost_breakdown.working_capital,
                  closing: r.terminal_value_breakdown.working_capital,
                },
              ].map((row) => (
                <TableRow key={row.label}>
                  <TableCell className="font-medium">{row.label}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(row.opening)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(row.closing)}
                  </TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell className="font-semibold">{t("simulation.table.total")}</TableCell>
                <TableCell className="text-right font-semibold tabular-nums">
                  {formatMoney(m.project_cost)}
                </TableCell>
                <TableCell className="text-right font-semibold tabular-nums">
                  {formatMoney(r.terminal_value_breakdown.total)}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </DataTableCard>

        <DataTableCard
          title={t("simulation.table.annualPl")}
          description={t("simulation.table.annualPlDescription")}
        >
          <div className="overflow-x-auto">
            <Table className="min-w-[1700px]">
              <TableHeader>
                <TableRow>
                  <TableHead>{t("simulation.table.year")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.revenue")}</TableHead>
                  <TableHead className="text-right" title={t("simulation.table.meatTitle")}>
                    {t("simulation.table.meatCurrency")}
                  </TableHead>
                  <TableHead className="text-right" title={t("simulation.table.cullTitle")}>
                    {t("simulation.table.cullCurrency")}
                  </TableHead>
                  <TableHead className="text-right" title={t("simulation.table.manureTitle")}>
                    {t("simulation.table.manureCurrency")}
                  </TableHead>
                  <TableHead className="text-right">{t("simulation.table.opex")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.ebitda")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.depreciation")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.ebit")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.interest")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.tax")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.pat")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.debtService")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.terminalValue")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.netCashFlow")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {r.annual_pl.map((row) => (
                  <TableRow key={row.year}>
                    <TableCell>{row.year}</TableCell>
                    {[
                      row.total_revenue,
                      row.meat_revenue,
                      row.cull_revenue,
                      row.manure_revenue,
                      row.total_opex,
                      row.ebitda,
                      row.depreciation,
                      row.ebit,
                      row.interest,
                      row.tax,
                      row.profit_after_tax,
                      row.debt_service,
                      row.terminal_value,
                    ].map((value, index) => (
                      <TableCell key={index} className="text-right tabular-nums">
                        {formatMoney(value)}
                      </TableCell>
                    ))}
                    <TableCell
                      className={`text-right tabular-nums ${row.net_cash_flow < 0 ? "text-destructive" : ""}`}
                    >
                      {formatMoney(row.net_cash_flow)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </DataTableCard>

        <DataTableCard
          title={t("simulation.table.monthlyProjection")}
          description={t("simulation.table.monthlyProjectionDescription", {
            count: r.months.length,
          })}
        >
          <div className="max-h-96 overflow-auto rounded-lg border">
            <Table className="min-w-[1400px]">
              <TableHeader>
                <TableRow>
                  <TableHead>{t("simulation.table.month")}</TableHead>
                  <TableHead>{t("simulation.table.totalHerd")}</TableHead>
                  <TableHead>{t("simulation.table.births")}</TableHead>
                  <TableHead>{t("simulation.table.deaths")}</TableHead>
                  <TableHead>{t("simulation.table.salesHead")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.meatPerKg")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.salesRevenue")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.cullHead")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.cullRevenue")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.purchasedGreenKg")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.feedCost")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.sellingCost")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.tax")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.debtService")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.terminalValue")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.netCashFlow")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.cashBalance")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.cumulativeCashFlow")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.fodderStock")}</TableHead>
                  <TableHead className="text-right">{t("simulation.table.water")}</TableHead>
                  <TableHead>{t("simulation.table.events")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {r.months.map((row) => (
                  <TableRow
                    key={row.month}
                    className={
                      row.events && row.events.length > 0
                        ? "bg-warning-tint/50"
                        : undefined
                    }
                  >
                    <TableCell>{row.month}</TableCell>
                    <TableCell>{formatHead(row.total_herd)}</TableCell>
                    <TableCell>{formatHead(row.births)}</TableCell>
                    <TableCell>{formatHead(row.deaths)}</TableCell>
                    <TableCell>{formatHead(row.sales_head)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMoney(row.meat_price_per_kg)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMoney(row.sales_revenue)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatHead(row.culls_head)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMoney(row.cull_revenue)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatHead(row.feed_purchased_green_kg)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMoney(row.feed_cost)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMoney(row.selling_cost)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMoney(row.tax)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMoney(row.debt_service)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMoney(row.terminal_value)}
                    </TableCell>
                    <TableCell
                      className={`text-right tabular-nums ${row.net_cash_flow < 0 ? "text-destructive" : ""}`}
                    >
                      {formatMoney(row.net_cash_flow)}
                    </TableCell>
                    <TableCell
                      className={`text-right tabular-nums ${row.cash_balance < 0 ? "text-destructive" : ""}`}
                    >
                      {formatMoney(row.cash_balance)}
                    </TableCell>
                    <TableCell
                      className={
                        `text-right tabular-nums ${row.cumulative_cash_flow < 0 ? "text-destructive" : ""}`
                      }
                    >
                      {formatMoney(row.cumulative_cash_flow)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatHead(row.fodder_stock_kg_dm)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {/* Optional water-demand column (contract addition). */}
                      {typeof (row as { water_litres?: number }).water_litres === "number"
                        ? litres(
                            (row as { water_litres?: number }).water_litres as number,
                            language,
                          )
                        : "—"}
                    </TableCell>
                    <TableCell>
                      {row.events && row.events.length > 0 ? (
                        <div className="space-y-0.5 text-xs whitespace-nowrap">
                          {row.events.map((line, i) => (
                            <div key={i}>{line}</div>
                          ))}
                        </div>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </DataTableCard>

        {r.monte_carlo && (
          <Card>
            <CardHeader>
              <CardTitle>
                {t("simulation.result.monteCarloTitle", {
                  runs: r.monte_carlo.runs,
                  seed: r.monte_carlo.seed,
                })}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {/* Tints follow the sign, as every other NPV/cash card does:
                    a hard-coded emerald painted a loss-making mean as success. */}
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_mean)}
                  label={t("simulation.metric.npvMean")}
                  icon={IndianRupee}
                  tint={r.monte_carlo.npv_mean >= 0 ? "success" : "destructive"}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_std)}
                  label={t("simulation.metric.npvStd")}
                  icon={Sigma}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_p5)}
                  label={t("simulation.metric.npvP5")}
                  icon={ChartColumn}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_p50)}
                  label={t("simulation.metric.npvP50")}
                  icon={ChartColumn}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_p95)}
                  label={t("simulation.metric.npvP95")}
                  icon={ChartColumn}
                />
                <MetricCard
                  value={`${(r.monte_carlo.prob_npv_negative * 100).toFixed(1)}%`}
                  label={t("simulation.metric.probabilityNegativeNpv")}
                  icon={TriangleAlert}
                  tint={r.monte_carlo.prob_npv_negative > 0 ? "destructive" : "success"}
                />
                <MetricCard
                  value={formatPercent(r.monte_carlo.prob_liquidity_shortfall)}
                  label={t("simulation.metric.probabilityCashShortfall")}
                  icon={Wallet}
                  tint={
                    r.monte_carlo.prob_liquidity_shortfall > 0 ? "destructive" : "success"
                  }
                />
                <MetricCard
                  value={formatPercent(r.monte_carlo.prob_dscr_below_one)}
                  label={t("simulation.metric.probabilityDscrBelowOne")}
                  icon={ShieldAlert}
                  tint={r.monte_carlo.prob_dscr_below_one > 0 ? "destructive" : "success"}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.minimum_cash_p5)}
                  label={t("simulation.metric.minimumCashP5")}
                  icon={Wallet}
                  tint={r.monte_carlo.minimum_cash_p5 < 0 ? "destructive" : "success"}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.minimum_cash_p50)}
                  label={t("simulation.metric.minimumCashP50")}
                  icon={Wallet}
                  tint={r.monte_carlo.minimum_cash_p50 < 0 ? "destructive" : "success"}
                />
              </div>
              <MonteCarloHistogram
                counts={r.monte_carlo.npv_histogram_counts}
                edges={r.monte_carlo.npv_histogram_edges}
                p5={r.monte_carlo.npv_p5}
                p50={r.monte_carlo.npv_p50}
                p95={r.monte_carlo.npv_p95}
              />
              <p className="text-sm text-muted-foreground">
                {t("simulation.result.meanPathEvents", {
                  disease: formatRatio(r.monte_carlo.mean_disease_outbreaks, 2),
                  drought: formatRatio(r.monte_carlo.mean_drought_events, 2),
                  market: formatRatio(r.monte_carlo.mean_market_crashes, 2),
                })}
              </p>
              <RiskBandTable
                herd={r.monte_carlo.herd_percentiles}
                liquidity={r.monte_carlo.liquidity_percentiles}
              />
            </CardContent>
          </Card>
        )}

        {r.optimization && (
          <OptimizationResults result={r.optimization} vocabulary={simVocabulary} />
        )}

        {sortedSensitivity && sortedSensitivity.length > 0 && (
          <DataTableCard
            title={t("simulation.table.sensitivity")}
            description={t("simulation.table.sensitivityDescription")}
          >
            <Table className="min-w-[680px]">
              <TableHeader>
                <TableRow>
                  <TableHead>{t("simulation.table.parameter")}</TableHead>
                  <TableHead>{t("simulation.table.deltaNpvLow")}</TableHead>
                  <TableHead>{t("simulation.table.deltaNpvHigh")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedSensitivity.map((item) => (
                  <TableRow key={item.parameter}>
                    <TableCell>{localizedFieldLabel(item.parameter, t, language)}</TableCell>
                    <TableCell
                      className={
                        item.delta_npv_low < 0 ? "text-destructive" : undefined
                      }
                    >
                      {formatMoney(item.delta_npv_low)}
                    </TableCell>
                    <TableCell
                      className={
                        item.delta_npv_high < 0 ? "text-destructive" : undefined
                      }
                    >
                      {formatMoney(item.delta_npv_high)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </DataTableCard>
        )}

        <Dialog
          open={explanation !== null}
          onOpenChange={(open) => {
            if (!open) setExplanation(null);
          }}
        >
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>{explanation?.title}</DialogTitle>
            </DialogHeader>
            {explanation && (
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground">
                  {explanation.explanation}
                </p>
                {explanation.figures && (
                  <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                    {Object.entries(explanation.figures)
                      .filter(
                        (entry): entry is [string, number | string] =>
                          entry[1] !== null,
                      )
                      .map(([key, value]) => (
                        <div key={key} className="contents">
                          <dt className="text-muted-foreground">
                            {localizedFieldLabel(key, t, language)}
                          </dt>
                          <dd>{formatFigure(key, value)}</dd>
                        </div>
                      ))}
                  </dl>
                )}
              </div>
            )}
          </DialogContent>
        </Dialog>

      </div>
    );
  }


  return (
    <div className="space-y-6">
      <PageHeader
        title={t("simulation.page.title")}
        description={t("simulation.page.description")}
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => void onCompare()}
              disabled={
                selectedUsableIds.length < 2 ||
                selectedUsableIds.length > MAX_COMPARE_SCENARIOS ||
                simulationAction.pending
              }
            >
              <GitCompareArrows />


              {compareQuery.isFetching
                ? t("simulation.action.comparing")
                : t("simulation.action.compareSelected")}
            </Button>
            {canManage && (
              <>
                <Button
                  variant="outline"
                  onClick={() => {
                    setSaveError(null);
                    setSaveOpen(true);
                  }}
                  disabled={!assumptions || hasEditorErrors || simulationAction.pending}
                >
                  <Save />
                  {t("simulation.action.saveAsScenario")}
                </Button>
                {loadedScenario && (
                  <Button
                    variant="outline"
                    onClick={() => void onUpdateScenario()}
                    disabled={
                      !assumptions ||
                      hasEditorErrors ||
                      !scenarioUsable(loadedScenario) ||
                      simulationAction.pending
                    }
                  >
                    {updateMutation.isPending
                      ? t("simulation.action.updating")
                      : t("simulation.action.updateScenario", { name: loadedScenario.name })}
                  </Button>
                )}
              </>
            )}
            <Button
              onClick={() => void onRun()}
              disabled={!assumptions || hasEditorErrors || simulationAction.pending}
            >
              <Play />
              {runMutation.isPending
                ? t("simulation.action.running")
                : t("simulation.action.runSimulation")}
            </Button>
          </>
        }
      />

      {/* Sticky in-page navigator — the simulation is one long scroll;
          this keeps every section one click away. */}
      <nav
        aria-label={t("simulation.nav.label")}
        className="sticky top-14 z-20 -mx-4 flex items-center gap-2 border-b bg-background/90 px-4 py-2 backdrop-blur-md md:-mx-6 md:px-6"
      >
        {/* Links first, run cluster last: the cluster's ml-auto pushes it to
            the right edge, which only reads correctly when it follows the
            links in DOM order. */}
        <ul className="flex min-w-0 gap-1 overflow-x-auto text-sm">
          {([
            ["sim-setup", t("simulation.nav.setup")],
            calibration ? (["sim-calibration", t("simulation.nav.calibration")]) : null,
            ["sim-assumptions", t("simulation.nav.assumptions")],
            ["sim-events", t("simulation.nav.herdEvents")],
            result ? (["sim-results", t("simulation.nav.results")]) : null,
            ["sim-scenarios", t("simulation.nav.scenarios")],
          ].filter(Boolean) as [string, string][]).map(([href, label]) => (
            <li key={href}>
              <a
                href={`#${href}`}
                // The App Router suppresses the browser's native fragment
                // scroll, so drive it explicitly; scroll-mt-28 on each
                // target clears the sticky topbar and this nav.
                onClick={(event) => {
                  event.preventDefault();
                  const target = document.getElementById(href);
                  target?.scrollIntoView({ behavior: "smooth", block: "start" });
                  // Fragment navigation also moves focus; do the same since
                  // the default scroll was suppressed.
                  target?.focus({ preventScroll: true });
                  window.history.replaceState(null, "", `#${href}`);
                }}
                className="inline-flex rounded-md px-2.5 py-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                {label}
              </a>
            </li>
          ))}
        </ul>
        {/* Run stays reachable from anywhere on this long page, with the
            last run's headline numbers beside it. Chips dim and retitle when
            the editor has moved on since that run. */}
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {result && (
            <>
              {/* Value-only chips: the metric names live in title/aria
                  labels so they never collide with the results section's
                  metric labels in text queries or screen readers. */}
              <span
                aria-label={t("simulation.accessibility.lastRunNpv", {
                  value: formatMoney(result.data.metrics.npv),
                  changed: resultIsStale ? t("simulation.accessibility.inputsChanged") : "",
                })}
                title={t("simulation.accessibility.lastRunNpvTitle", {
                  changed: resultIsStale ? t("simulation.accessibility.inputsChanged") : "",
                })}
                className={`hidden items-center rounded-md bg-muted px-2 py-1 text-xs sm:inline-flex${resultIsStale ? " opacity-60" : ""}`}
              >
                <span className="table-numeric font-medium">{formatMoney(result.data.metrics.npv)}</span>
              </span>
              <span
                aria-label={t("simulation.accessibility.lastRunIrr", {
                  value: formatPercent(result.data.metrics.irr),
                  changed: resultIsStale ? t("simulation.accessibility.inputsChanged") : "",
                })}
                title={t("simulation.accessibility.lastRunIrrTitle", {
                  changed: resultIsStale ? t("simulation.accessibility.inputsChanged") : "",
                })}
                className={`hidden items-center rounded-md bg-muted px-2 py-1 text-xs md:inline-flex${resultIsStale ? " opacity-60" : ""}`}
              >
                <span className="table-numeric font-medium">{formatPercent(result.data.metrics.irr)}</span>
              </span>
            </>
          )}
          <Button
            size="sm"
            onClick={() => void onRun()}
            disabled={!assumptions || hasEditorErrors || simulationAction.pending}
          >
            <Play aria-hidden="true" />
            {runMutation.isPending ? t("simulation.action.running") : t("simulation.action.run")}
          </Button>
        </div>
      </nav>


      <Card id="sim-setup" tabIndex={-1} className="scroll-mt-28 focus:outline-none">
        <CardHeader>
          <CardTitle>{t("simulation.setup.title")}</CardTitle>
          <CardDescription>
            {t("simulation.setup.description")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="sim-breed">{t("simulation.setup.breed")}</Label>
              <Select
                value={breed}
                onValueChange={(value) => {
                  calibrationParamsGeneration.current += 1;
                  setBreed(value);
                }}
              >
                <SelectTrigger id="sim-breed">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(breeds?.breeds ?? [breed]).map((b) => (
                    <SelectItem key={b} value={b}>
                      {b}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sim-system">{t("simulation.setup.system")}</Label>
              <Select
                value={system}
                onValueChange={(v) => {
                  calibrationParamsGeneration.current += 1;
                  setSystem(v as BreedDefaultsApiSimulationDefaultsGetSystem);
                }}
                items={Object.fromEntries(
                  (breeds?.systems ?? [system]).map((s) => [s, localizedFieldLabel(s, t, language)]),
                )}
              >
                <SelectTrigger id="sim-system">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(breeds?.systems ?? [system]).map((s) => (
                    <SelectItem key={s} value={s}>
                      {localizedFieldLabel(s, t, language)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              variant="outline"
              onClick={() => {
                // Claim the editor intent before starting/refetching. Any
                // older calibration or snapshot completion now observes a
                // different epoch and cannot overwrite these defaults.
                editorEpochRef.current += 1;
                acceptDefaultsRef.current = true;
                if (breed === submittedParams.breed && system === submittedParams.system) {
                  // Apply the refetched payload directly from the result.
                  // With unchanged breed+system the response is deep-equal
                  // to the cached one and react-query's structural sharing
                  // preserves the old object reference, so the data-keyed
                  // effect above would never re-run and the click would
                  // silently keep the user's edits.
                  void defaultsQuery.refetch().then((result) => {
                    if (result.data?.status === 200 && acceptDefaultsRef.current) {
                      acceptDefaultsRef.current = false;
                      applyDefaultsToEditor(result.data.data);
                    }
                  });
                } else {
                  setSubmittedParams({ breed, system });
                }
              }}
              disabled={defaultsQuery.isFetching}
            >
              <RefreshCw />
              {defaultsQuery.isFetching
                ? t("common.loading")
                : t("simulation.action.loadDefaults")}
            </Button>
            <Button
              variant="outline"
              onClick={() => void onUseCurrentHerd()}
              disabled={!assumptions || snapshotQuery.isFetching || defaultsQuery.isFetching}
            >
              <Building2 />
              {snapshotQuery.isFetching
                ? t("common.loading")
                : t("simulation.action.useCurrentHerd")}
            </Button>
            {canCalibrate && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="sim-calibration-lookback">
                    {t("simulation.setup.calibrationHistory")}
                  </Label>
                  <Select
                    value={String(calibrationLookback)}
                    onValueChange={(value) => {
                      calibrationParamsGeneration.current += 1;
                      setCalibrationLookback(Number(value));
                    }}
                    items={{
                      "12": t("simulation.setup.months", { count: 12 }),
                      "24": t("simulation.setup.months", { count: 24 }),
                      "36": t("simulation.setup.months", { count: 36 }),
                      "60": t("simulation.setup.months", { count: 60 }),
                    }}
                  >
                    <SelectTrigger id="sim-calibration-lookback">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {[12, 24, 36, 60].map((months) => (
                        <SelectItem key={months} value={String(months)}>
                          {t("simulation.setup.months", { count: months })}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <Button
                  variant="outline"
                  onClick={() => void onCalibrateFromFarm()}
                  disabled={!assumptions || calibrationQuery.isFetching}
                >
                  <Database />
                  {calibrationQuery.isFetching
                    ? t("simulation.action.calibrating")
                    : t("simulation.action.calibrateFromFarm")}
                </Button>
              </>
            )}
          </div>
          {defaultsQuery.isError && (
            <p role="alert" className="text-sm text-destructive">
              {defaultsQuery.error instanceof ApiError
                ? defaultsQuery.error.detail
                : t("simulation.error.defaults")}
            </p>
          )}
          {breedsQuery.isError && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(breedsQuery.error, t("simulation.error.breeds"))}
            </p>
          )}
        </CardContent>
      </Card>

      {calibration && (
        <DataTableCard
          title={t("simulation.calibration.title")}
          id="sim-calibration"
          tabIndex={-1}
          className="scroll-mt-28 focus:outline-none"
          description={t("simulation.calibration.description", {
            date: calibration.reference_date,
            months: calibration.lookback_months,
          })}
          actions={
            <span className="text-sm font-medium tabular-nums">
              {t("simulation.calibration.coverage", {
                value: formatPercent(calibration.coverage_score),
              })}
            </span>
          }
          contentClassName="space-y-4"
        >
          {calibration.warnings.length > 0 && (
            <div
              role="status"
              className="flex gap-2 rounded-lg border border-warning/40 bg-warning-tint/60 px-3 py-2 text-sm text-warning-tint-foreground"
            >
              <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              <div className="space-y-1">
                {calibration.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </div>
            </div>
          )}
          {calibration.evidence.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("simulation.calibration.empty")}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table className="min-w-[720px]">
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("simulation.calibration.assumption")}</TableHead>
                    <TableHead>{t("simulation.calibration.baseline")}</TableHead>
                    <TableHead>{t("simulation.calibration.calibrated")}</TableHead>
                    <TableHead>{t("simulation.calibration.sample")}</TableHead>
                    <TableHead>{t("simulation.calibration.confidence")}</TableHead>
                    <TableHead>{t("simulation.calibration.evidence")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {calibration.evidence.map((item) => (
                    <TableRow key={item.path}>
                      <TableCell className="font-medium">
                        {item.path.split(".").map((part) => localizedFieldLabel(part, t, language)).join(" / ")}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {formatCalibrationValue(item.previous_value)}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {formatCalibrationValue(item.calibrated_value)}
                      </TableCell>
                      <TableCell className="tabular-nums">{item.sample_size}</TableCell>
                      <TableCell>{localizedFieldLabel(item.confidence, t, language)}</TableCell>
                      <TableCell>
                        <div>{item.source}</div>
                        <p className="text-xs text-muted-foreground">{item.method}</p>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </DataTableCard>
      )}

      <fieldset disabled={defaultsQuery.isFetching} className="contents">
      <Card id="sim-assumptions" tabIndex={-1} className="scroll-mt-28 focus:outline-none">
        <CardHeader>
          <CardTitle>{t("simulation.assumptions.title")}</CardTitle>
          <CardDescription>
            {t("simulation.assumptions.description")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!assumptions && !defaultsQuery.isError && (
            <InlineLoading>{t("simulation.assumptions.loading")}</InlineLoading>
          )}
          {assumptions?.meta && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-muted-foreground">
                {t("simulation.assumptions.horizonPresets")}
              </span>
              {HORIZON_PRESETS.map((preset) => (
                <Button
                  key={preset}
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    updateField("meta", "horizon_months", preset);
                    setFieldValidity("field:sim-meta-horizon_months", true);
                    setHorizonInputVersion((version) => version + 1);
                  }}
                >
                  {t("simulation.assumptions.horizonPreset", { years: preset / 12 })}
                </Button>
              ))}
              <span className="text-xs text-muted-foreground">
                {t("simulation.assumptions.horizonExample")}
              </span>
            </div>
          )}
          {assumptions &&
            sectionEntries(assumptions).map(([section, values]) => (
              <details
                key={`${section}:${editorVersion}`}
                open={section === "meta" || section === "herd"}
                className="rounded-lg border"
              >
                <summary className="flex cursor-pointer items-center gap-1.5 px-4 py-2.5 text-sm font-medium hover:bg-muted/50">
                  {localizedFieldLabel(section, t, language)}
                  <FieldHelpButton
                    label={localizedFieldLabel(section, t, language)}
                    onClick={() =>
                      setFieldExplain({
                        label: t("simulation.assumptions.sectionTitle", {
                          section: localizedFieldLabel(section, t, language),
                        }),
                        body: SIMULATION_SECTION_HELP[section]?.(simVocabulary) ?? null,
                        facts: [],
                      })
                    }
                  />
                </summary>
                <div className="grid gap-3 border-t px-4 py-3 sm:grid-cols-2 lg:grid-cols-3">
                  {Object.entries(values)
                    .filter(([key]) => !DAIRY_HIDDEN_FIELDS.has(`${section}.${key}`))
                    .map(([key, value]) => renderField(section, key, value))}
                </div>
              </details>
            ))}
          {invalidFields.size > 0 && (
            <p role="alert" className="text-sm text-destructive">
              {t(
                invalidFields.size === 1
                  ? "simulation.validation.fixHighlightedOne"
                  : "simulation.validation.fixHighlightedMany",
                { count: invalidFields.size },
              )}
            </p>
          )}
          {assumptionErrors.map((error) => (
            <p key={error} role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ))}
        </CardContent>
      </Card>

      <DataTableCard
        title={t("simulation.events.title")}
        id="sim-events"
        tabIndex={-1}
        className="scroll-mt-28 focus:outline-none"
        description={t("simulation.events.description")}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setRecurrenceOpen(true)}
              disabled={!assumptions || events.length >= 500}
            >
              <Repeat />
              {t("simulation.action.repeatPlan")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={addEvent}
              // Events belong to a loaded assumption set. Before the initial
              // defaults land, adding one would flip acceptDefaultsRef and
              // silently cancel the pending auto-load, leaving the editor stuck
              // on "Loading defaults…" — so stay disabled until assumptions
              // exist, like Run and "Use current herd" already do.
              disabled={!assumptions || events.length >= 500}
            >
              <Plus />
              {t("simulation.action.addEvent")}
            </Button>
          </div>
        }
        contentClassName="space-y-3"
      >
        {events.length === 0 ? (
          <EmptyState
            icon={CalendarClock}
            title={t("simulation.events.emptyTitle")}
            description={t("simulation.events.emptyDescription")}
          />
        ) : (
          <Table className="min-w-[760px]">
            <TableHeader>
              <TableRow>
                <TableHead>{t("simulation.events.month")}</TableHead>
                <TableHead>{t("simulation.events.kind")}</TableHead>
                <TableHead>{t("simulation.events.class")}</TableHead>
                <TableHead>{t("simulation.events.count")}</TableHead>
                <TableHead>{t("simulation.events.pricePerHeadColumn")}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.map((event, index) => {
                const eventKey = eventKeys[index] ?? `event-fallback-${index}`;
                return (
                <TableRow key={eventKey}>
                  <TableCell>
                    <NumberInput
                      id={`simulation-${eventKey}-month`}
                      aria-label={t("simulation.events.month")}
                      min={1}
                      max={horizonMonths}
                      integer
                      className="w-20"
                      value={event.month}
                      onValidityChange={(valid) =>
                        setFieldValidity(`event:${eventKey}:month`, valid)
                      }
                      onCommit={(n) => updateEvent(index, { month: n })}
                    />
                  </TableCell>
                  <TableCell>
                    <Select
                      value={event.kind}
                      onValueChange={(v) =>
                        updateEvent(index, {
                          kind: v as HerdEventAssumptions["kind"],
                        })
                      }
                      items={eventKindItems(t)}
                    >
                      <SelectTrigger aria-label={t("simulation.events.kind")} size="sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(eventKindItems(t)).map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    <Select
                      value={event.animal_class}
                      onValueChange={(v) =>
                        updateEvent(index, {
                          animal_class: v as HerdEventAssumptions["animal_class"],
                        })
                      }
                      items={eventClassItems(simVocabulary, t, language)}
                    >
                      <SelectTrigger aria-label={t("simulation.events.class")} size="sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(eventClassItems(simVocabulary, t, language)).map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    <NumberInput
                      id={`simulation-${eventKey}-count`}
                      aria-label={t("simulation.events.count")}
                      exclusiveMin={0}
                      max={100_000}
                      className="w-20"
                      value={event.count}
                      onValidityChange={(valid) =>
                        setFieldValidity(`event:${eventKey}:count`, valid)
                      }
                      onCommit={(n) => updateEvent(index, { count: n })}
                    />
                  </TableCell>
                  <TableCell>
                    <NumberInput
                      id={`simulation-${eventKey}-price`}
                      aria-label={t("simulation.events.pricePerHead")}
                      min={0}
                      max={1_000_000_000}
                      placeholder={t("simulation.events.auto")}
                      className="w-24"
                      nullable
                      value={event.price_per_head ?? null}
                      onValidityChange={(valid) =>
                        setFieldValidity(`event:${eventKey}:price`, valid)
                      }
                      onCommit={(n) => updateEvent(index, { price_per_head: n })}
                    />
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => removeEvent(index)}
                    >
                      {t("simulation.action.remove")}
                    </Button>
                  </TableCell>
                </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
        {eventErrors.map((error) => (
          <p key={error} role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ))}
      </DataTableCard>

      <Dialog open={recurrenceOpen} onOpenChange={setRecurrenceOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("simulation.recurrence.title")}</DialogTitle>
            <DialogDescription>
              {t("simulation.recurrence.description")}
            </DialogDescription>
          </DialogHeader>
          {/* Phones stack single-column like every other create dialog. */}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="recurrence-month">{t("simulation.recurrence.firstMonth")}</Label>
              <NumberInput
                id="recurrence-month"
                min={1}
                max={horizonMonths}
                integer
                value={recurrence.month}
                onValidityChange={(valid) =>
                  setRecurrenceFieldValidity("month", valid)
                }
                onCommit={(n) => setRecurrence((r) => ({ ...r, month: n }))}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="recurrence-count">{t("simulation.events.count")}</Label>
              <NumberInput
                id="recurrence-count"
                exclusiveMin={0}
                max={100_000}
                value={recurrence.count}
                onValidityChange={(valid) =>
                  setRecurrenceFieldValidity("count", valid)
                }
                onCommit={(n) => setRecurrence((r) => ({ ...r, count: n }))}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="recurrence-every">{t("simulation.recurrence.everyMonths")}</Label>
              <NumberInput
                id="recurrence-every"
                min={1}
                max={120}
                integer
                value={recurrence.every}
                onValidityChange={(valid) =>
                  setRecurrenceFieldValidity("every", valid)
                }
                onCommit={(n) => setRecurrence((r) => ({ ...r, every: n }))}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="recurrence-repeat">{t("simulation.recurrence.repeats")}</Label>
              <NumberInput
                id="recurrence-repeat"
                min={1}
                max={120}
                integer
                value={recurrence.repeat}
                onValidityChange={(valid) =>
                  setRecurrenceFieldValidity("repeat", valid)
                }
                onCommit={(n) => setRecurrence((r) => ({ ...r, repeat: n }))}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="recurrence-kind">{t("simulation.events.kind")}</Label>
              <Select
                value={recurrence.kind}
                onValueChange={(v) =>
                  setRecurrence((r) => ({
                    ...r,
                    kind: v as HerdEventAssumptions["kind"],
                  }))
                }
                items={eventKindItems(t)}
              >
                <SelectTrigger id="recurrence-kind" size="sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(eventKindItems(t)).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="recurrence-class">{t("simulation.events.class")}</Label>
              <Select
                value={recurrence.animal_class}
                onValueChange={(v) =>
                  setRecurrence((r) => ({
                    ...r,
                    animal_class: v as HerdEventAssumptions["animal_class"],
                  }))
                }
                items={eventClassItems(simVocabulary, t, language)}
              >
                <SelectTrigger id="recurrence-class" size="sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(eventClassItems(simVocabulary, t, language)).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRecurrenceOpen(false)}>
              {t("common.cancel")}
            </Button>
            {/* L-23 (2026-09-17 audit): NumberInput commits only valid drafts,
             * so clicking Generate with an invalid box would commit the stale
             * last-valid value instead — hold the button until every dialog
             * input reports valid. */}
            <Button onClick={applyRecurrence} disabled={recurrenceInvalid.size > 0}>
              {t("simulation.action.generateRows")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      </fieldset>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl bg-card px-4 py-3 ring-1 ring-foreground/10">
          <span className="text-sm font-medium">{t("simulation.runOptions.title")}</span>
          <div className="flex items-center gap-2">
            <Checkbox
              id="sim-monte-carlo"
              checked={monteCarlo}
              onCheckedChange={(checked) => setMonteCarlo(checked === true)}
            />
            <Label htmlFor="sim-monte-carlo" className="font-normal">
              {t("simulation.runOptions.monteCarlo")}
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="sim-sensitivity"
              checked={sensitivity}
              onCheckedChange={(checked) => setSensitivity(checked === true)}
            />
            <Label htmlFor="sim-sensitivity" className="font-normal">
              {t("simulation.runOptions.sensitivity")}
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="sim-optimization"
              checked={optimization}
              onCheckedChange={(checked) => setOptimization(checked === true)}
            />
            <Label htmlFor="sim-optimization" className="font-normal">
              {t("simulation.runOptions.optimization")}
            </Label>
          </div>
        </div>
        {loadedScenario && (
          <p className="text-sm text-muted-foreground">
            {t("simulation.runOptions.editingScenario", { name: loadedScenario.name })}
          </p>
        )}
        {runError && (
          <p role="alert" className="text-sm text-destructive">
            {runError}
          </p>
        )}
      </section>

      {result && (
        <section className="space-y-3">
          <h2 id="sim-results" tabIndex={-1} className="scroll-mt-28 font-heading text-lg font-semibold focus:outline-none">
            {t("simulation.results.title")}
          </h2>
          <p className="text-sm text-muted-foreground">
            {t("simulation.results.source", {
              source:
                result.source.kind === "editor"
                  ? t("simulation.result.currentEditor")
                  : t("simulation.result.savedScenario", { name: result.source.name }),
            })}
          </p>
          <p className="text-xs text-muted-foreground">
            {t("simulation.results.modelFingerprint", { model: result.data.model_version })}{" "}
            <span className="font-mono" title={result.data.assumptions_fingerprint}>
              {result.data.assumptions_fingerprint.slice(0, 12)}
            </span>
          </p>
          {resultIsStale && (
            <p
              role="status"
              className="rounded-lg border border-warning/40 bg-warning-tint/60 px-3 py-2 text-sm text-warning-tint-foreground"
            >
              {t("simulation.results.stale", {
                source:
                  result.scenarioId === null
                    ? t("simulation.results.editorAssumptions")
                    : t("simulation.results.savedScenario"),
              })}
            </p>
          )}
          {renderResults(result.data)}
        </section>
      )}

      <DataTableCard
        title={t("simulation.scenarios.title")}
        id="sim-scenarios"
        tabIndex={-1}
        className="scroll-mt-28 focus:outline-none"
        description={t("simulation.scenarios.description", {
          max: MAX_COMPARE_SCENARIOS,
          selected: selectedUsableIds.length,
        })}
        contentClassName="space-y-4"
      >
        {scenariosQuery.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(scenariosQuery.error, t("simulation.error.savedScenarios"))}
          </p>
        ) : scenariosQuery.isLoading ? (
          <TableSkeleton rows={4} columns={4} />
        ) : scenarioTotal === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title={t("simulation.scenarios.emptyTitle")}
            description={t("simulation.scenarios.emptyDescription")}
          />
        ) : scenarios.length === 0 ? (
          <p role="status" className="text-sm text-muted-foreground">
            {t("simulation.scenarios.pageMoved")}
          </p>
        ) : (
          <div className="space-y-3">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead>{t("simulation.scenarios.name")}</TableHead>
                  <TableHead>{t("simulation.scenarios.notes")}</TableHead>
                  <TableHead>{t("simulation.scenarios.updated")}</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {scenarios.map((scenario) => (
                  <TableRow key={scenario.id}>
                    <TableCell>
                      <Checkbox
                        aria-label={t("simulation.scenarios.compareScenario", {
                          name: scenario.name,
                        })}
                        checked={
                          scenarioUsable(scenario) && selectedIds.includes(scenario.id)
                        }
                        disabled={
                          !scenarioUsable(scenario) ||
                          (!selectedIds.includes(scenario.id) &&
                            selectedUsableIds.length >= MAX_COMPARE_SCENARIOS)
                        }
                        onCheckedChange={(checked) => {
                          if (!scenarioUsable(scenario)) return;
                          setCompareIds(null);
                          setSelectedIds((prev) => {
                            if (checked !== true) {
                              return prev.filter((id) => id !== scenario.id);
                            }
                            const usableCount = prev.filter((id) => {
                              const row = scenarios.find((candidate) => candidate.id === id);
                              return row === undefined || scenarioUsable(row);
                            }).length;
                            return prev.includes(scenario.id) ||
                              usableCount >= MAX_COMPARE_SCENARIOS
                              ? prev
                              : [...prev, scenario.id];
                          });
                        }}
                      />
                    </TableCell>
                    <TableCell className="font-medium">
                      <div>{scenario.name}</div>
                      {!scenarioUsable(scenario) && (
                        <span className="text-xs font-normal text-destructive">
                          {t("simulation.scenarios.invalidAssumptions")}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <div>{scenario.notes}</div>
                      {!scenarioUsable(scenario) && scenario.validation_error && (
                        <p className="max-w-md text-xs text-destructive">
                          {scenario.validation_error}
                        </p>
                      )}
                    </TableCell>
                    <TableCell>{formatFarmDateTime(scenario.updated_at)}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            loadScenarioIntoEditor(scenario);
                          }}
                          disabled={!scenarioUsable(scenario)}
                        >
                          {t("simulation.action.load")}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => void onRunScenario(scenario)}
                          disabled={
                            !scenarioUsable(scenario) || simulationAction.pending
                          }
                        >
                          {runningScenarioId === scenario.id
                            ? t("simulation.action.running")
                            : t("simulation.action.run")}
                        </Button>
                        {canManage && (
                          <Button
                            variant="destructive"
                            size="sm"
                            disabled={simulationAction.pending}
                            onClick={() => setPendingDelete(scenario)}
                          >
                            {t("simulation.action.delete")}
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <PaginationControls
              total={scenarioTotal}
              limit={scenarioPage?.limit ?? SCENARIO_PAGE_SIZE}
              offset={scenarioPage?.offset ?? scenarioOffset}
              onOffsetChange={onScenarioOffsetChange}
              label={t("simulation.scenarios.paginationLabel")}
            />
          </div>
        )}
        {compareQuery.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(compareQuery.error, t("simulation.error.compareScenarios"))}
          </p>
        )}
        {comparePayload && comparePayload.results.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">{t("simulation.scenarios.comparison")}</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("simulation.scenarios.metric")}</TableHead>
                  {comparePayload.scenarios.map((scenario, index) => (
                    <TableHead key={`${scenario.id}-${index}`}>{scenario.name}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {compareRows(t).map((row) => (
                  <TableRow key={row.key}>
                    <TableCell className="font-medium">{row.label}</TableCell>
                    {comparePayload.results.map((r, i) => (
                      <TableCell key={`${comparePayload.scenarios[i]?.id ?? "result"}-${i}`}>
                        {row.format(r.metrics)}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </DataTableCard>

      <Dialog
        open={saveOpen}
        onOpenChange={(nextOpen) => {
          // A late create continuation closes and resets this controlled
          // form. Keep the current session mounted until that write settles.
          if (!nextOpen && simulationAction.pending) return;
          if (!nextOpen) setSaveError(null);
          setSaveOpen(nextOpen);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("simulation.action.saveAsScenario")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {saveError && (
              <p role="alert" className="text-sm text-destructive">
                {saveError}
              </p>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="scenario-name">{t("simulation.scenarios.nameRequired")}</Label>
              <Input
                id="scenario-name"
                maxLength={120}
                disabled={simulationAction.pending}
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="scenario-notes">{t("simulation.scenarios.notes")}</Label>
              <Input
                id="scenario-notes"
                maxLength={2000}
                disabled={simulationAction.pending}
                value={saveNotes}
                onChange={(e) => setSaveNotes(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button
                onClick={() => void onSaveScenario()}
                disabled={
                  !saveName.trim() || hasEditorErrors || simulationAction.pending
                }
              >
                {simulationAction.pending
                  ? t("simulation.action.saving")
                  : t("simulation.action.saveScenario")}
              </Button>
            </DialogFooter>
          </div>
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
              <DialogTitle>{t("simulation.delete.title")}</DialogTitle>
              <DialogDescription>
                {pendingDelete !== null && (
                  <>
                    {t("simulation.delete.descriptionBefore")}{" "}
                    <span className="font-medium text-foreground">
                      {pendingDelete.name}
                    </span>{" "}
                    {t("simulation.delete.descriptionAfter")}
                  </>
                )}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={simulationAction.pending}
                onClick={() => setPendingDelete(null)}
              >
                {t("common.cancel")}
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={simulationAction.pending}
                onClick={() => {
                  if (pendingDelete) void onDeleteScenario(pendingDelete);
                }}
              >
                {simulationAction.pending
                  ? t("simulation.action.deleting")
                  : t("simulation.action.deleteScenario")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

      {/* The "?" explanation dialog: one assumption term, its meaning, and the
          unit/allowed range/current value the editor derives for it. */}
      <Dialog
        open={fieldExplain !== null}
        onOpenChange={(open) => {
          if (!open) setFieldExplain(null);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{fieldExplain?.label}</DialogTitle>
          </DialogHeader>
          {fieldExplain && (
            <div className="space-y-4">
              {fieldExplain.body ? (
                <p className="text-sm text-muted-foreground">{fieldExplain.body}</p>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {t("simulation.field.noExplanation")}
                </p>
              )}
              {fieldExplain.facts.length > 0 && (
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                  {fieldExplain.facts.map((fact) => (
                    <div key={fact.term} className="contents">
                      <dt className="text-muted-foreground">{fact.term}</dt>
                      <dd>{fact.value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}


export default function SimulationPage() {
  const t = useT();
  const perms = usePermissions();
  return (
    <PermissionGate
      perms={perms}
      perm="simulation.view"
      label={t("simulation.page.title")}
      description={t("simulation.page.description")}
      noAccessMessage={t("simulation.noAccess")}
      cards={2}
      announce
    >
      <SimulationPageContent perms={perms} />
    </PermissionGate>
  );
}
