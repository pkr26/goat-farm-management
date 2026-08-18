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
  Database,
  FolderOpen,
  Gauge,
  GitCompareArrows,
  HandCoins,
  IndianRupee,
  Info,
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
  Target,
  TrendingUp,
  TriangleAlert,
  Wallet,
  Wheat,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type ComponentProps } from "react";
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
  OptimizationCandidate,
  PercentileBand,
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
import { StatCard } from "@/components/stat-card";
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
import { formatFarmDateTime, formatMoney } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

const DEFAULT_BREED = "osmanabadi";
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
  source: string;
};

type NumericRule = {
  integer?: boolean;
  min?: number;
  max?: number;
  exclusiveMin?: number;
  unit?: string;
};

type NumberArrayRule = {
  exactLength?: number;
  maxLength?: number;
  integer?: boolean;
  min?: number;
  max?: number;
  exclusiveMin?: number;
  allowEmpty?: boolean;
  unique?: boolean;
  nondecreasing?: boolean;
  itemLabel: string;
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
  "reproduction.months_open_before_breeding",
  "reproduction.age_at_first_breeding_months",
  "culling.max_doe_age_months",
  "culling.buck_rotation_years",
  "culling.buck_doe_ratio",
  "growth.sale_age_months",
  "sales.eid_month",
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
  "reproduction.gestation_months": { min: 1, max: 7 },
  "reproduction.lactation_months": { min: 1, max: 8 },
  "reproduction.months_open_before_breeding": { min: 0, max: 12 },
  "reproduction.litter_size": { min: 0.5, max: 4 },
  "reproduction.age_at_first_breeding_months": { min: 6, max: 30 },
  "reproduction.stillbirth_rate": { min: 0, max: 0.5 },
  "culling.max_doe_age_months": { min: 36, max: 180 },
  "culling.buck_rotation_years": { min: 1, max: 10 },
  "culling.buck_doe_ratio": { min: 1, max: 100 },
  "growth.birth_weight_kg": { exclusiveMin: 0, max: 1000 },
  "growth.adult_weight_doe_kg": { exclusiveMin: 0, max: 1000 },
  "growth.adult_weight_buck_kg": { exclusiveMin: 0, max: 1000 },
  "growth.sale_age_months": { min: 6, max: 24 },
  "sales.eid_month": { min: 0, max: 12 },
  "sales.eid_price_uplift": { min: 0, max: 2 },
  "sales.annual_livestock_price_growth_rate": { exclusiveMin: -1, max: 1 },
  "sales.selling_cost_fraction": { min: 0, max: 0.5 },
  "sales.lactation_milk_litres": { min: 0, max: 100_000 },
  // The sales money heuristic below only matches "price"/"income", so this is
  // the one money field on the form that would otherwise reach the API with no
  // client-side floor and 422 on a negative "rebate".
  "sales.transport_cost_per_head": { min: 0, max: 1e9 },
  "feed.cultivated_fodder_acres": { min: 0, max: 1_000_000 },
  "feed.fodder_yield_t_dm_per_acre_year": { exclusiveMin: 0, max: 1000 },
  "feed.annual_feed_price_growth_rate": { exclusiveMin: -1, max: 1 },
  "feed.initial_fodder_stock_kg_dm": { min: 0, max: 1e9 },
  "feed.fodder_storage_capacity_kg_dm": { min: 0, max: 1e9 },
  "feed.fodder_storage_loss_fraction_monthly": { min: 0, max: 1 },
  "costs.labour_per_head_threshold": { min: 1 },
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
const FIELD_UNITS: Record<string, string> = {
  "costs.labour_per_head_threshold": "head per labourer",
  "feed.fodder_yield_t_dm_per_acre_year": "t DM/acre/yr",
  "feed.initial_fodder_stock_kg_dm": "kg DM",
  "feed.fodder_storage_capacity_kg_dm": "kg DM",
  "costs.planned_capacity_head": "head",
  // An integer head-count ratio (1 buck per N does), not a 0-1 fraction: the
  // heuristic chain's trailing `ratio` test would otherwise caption it one.
  "culling.buck_doe_ratio": "does per buck",
  "optimization.maximum_project_cost": "₹",
  "optimization.maximum_funding_gap": "₹",
  // Fractions and multipliers whose names also contain a money or duration
  // substring, which the heuristic chain settles first. Captioning a 0-0.6 tax
  // rate "₹" invites farmers to type 30 for 30%; the entry must stay explicit
  // because the money/duration tests cannot simply be moved (see numericRule).
  "sales.selling_cost_fraction": "fraction",
  "sales.annual_livestock_price_growth_rate": "fraction",
  "feed.annual_feed_price_growth_rate": "fraction",
  "feed.fodder_storage_loss_fraction_monthly": "fraction",
  "costs.operating_cost_growth_rate_annual": "fraction",
  "finance.income_tax_rate": "fraction",
  "risk.drought_feed_price_multiplier": "multiplier",
  "risk.market_crash_price_multiplier": "multiplier",
};

/** Fields the backend models as "amount, or null for no ceiling". They arrive
 * as null by default, which every typed branch of renderField would drop —
 * taking the whole row off the form. Render them as blankable inputs instead. */
const NULLABLE_NUMBER_FIELDS = new Set([
  "optimization.maximum_project_cost",
  "optimization.maximum_funding_gap",
]);

const STRING_FIELD_OPTIONS: Record<string, Record<string, string>> = {
  "herd.foundation_flock_state": { mixed: "Mixed", open: "Open" },
  "costs.capacity_basis": {
    projected_peak: "Projected peak",
    planned: "Planned capacity",
    opening_herd: "Opening herd",
  },
  "optimization.objective": {
    balanced: "Balanced",
    npv: "Highest NPV",
    liquidity: "Strongest liquidity",
  },
};

function numberArrayRule(
  section: string,
  key: string,
  horizonMonths: number,
): NumberArrayRule {
  const path = `${section}.${key}`;
  if (path === "growth.weight_by_age_months") {
    return {
      exactLength: 13,
      exclusiveMin: 0,
      max: 1000,
      nondecreasing: true,
      itemLabel: "weights (ages 0-12)",
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
      itemLabel: "simulation months",
    };
  }
  if (key.startsWith("monthly_")) {
    return {
      exactLength: 12,
      exclusiveMin: 0,
      max: 10,
      itemLabel: "monthly multipliers (Jan-Dec)",
    };
  }
  return { itemLabel: "values" };
}

function numericRule(section: string, key: string): NumericRule {
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

  if (FIELD_UNITS[path]) rule.unit = FIELD_UNITS[path];
  else if (
    path === "sales.eid_price_uplift" ||
    path === "finance.loan_fraction_of_project_cost"
  )
    rule.unit = "fraction";
  // `_per_month` / `_per_year` are rates of the underlying metric, not
  // durations — settle them before the month/year duration patterns, which
  // otherwise caption ₹10,000/month of labour as "months".
  else if (key.endsWith("_per_month")) rule.unit = "₹/month";
  else if (key.endsWith("_per_year")) rule.unit = "₹/yr";
  else if (key.includes("month")) rule.unit = "months";
  else if (key.includes("year")) rule.unit = "years";
  else if (
    key.includes("price") ||
    key.includes("cost") ||
    key.includes("income") ||
    key.includes("labour") ||
    key.includes("overhead")
  )
    rule.unit = "₹";
  else if (key.includes("weight") || key.includes("_kg")) rule.unit = "kg";
  else if (key.includes("litre")) rule.unit = "litres";
  else if (key.includes("acre")) rule.unit = "acres";
  // This test stays last: hoisting it above the money/duration ones captions
  // concent*rate*_price_per_kg and *_duration_months as "fraction". Fields it
  // therefore cannot reach (income_tax_rate, selling_cost_fraction, …) belong
  // in FIELD_UNITS, not in a reordering of this chain.
  else if (/rate|ratio|fraction|pct|share|dmi_/.test(key)) rule.unit = "fraction";
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
const HORIZON_PRESETS = [
  { months: 60, label: "5 yr" },
  { months: 120, label: "10 yr" },
  { months: 180, label: "15 yr" },
  { months: 240, label: "20 yr" },
] as const;

/** value → label maps for the root `items` prop: without them, Base UI's
 * Select.Value renders the raw value in the closed trigger. */
const EVENT_KIND_ITEMS: Record<string, string> = {
  purchase: "Purchase",
  sale: "Sale",
};
const EVENT_CLASS_ITEMS: Record<string, string> = {
  doe: "Doe",
  buck: "Buck",
  female_kid: "Female kid",
  male_kid: "Male kid",
  female_weaner: "Female weaner",
  male_weaner: "Male weaner",
  female_grower: "Female grower",
  male_grower: "Male grower",
};

/** Inline validation for the herd events editor; horizon comes from meta. */
function validateEvents(events: HerdEventAssumptions[], horizonMonths: number): string[] {
  const errors: string[] = [];
  events.forEach((event, i) => {
    const label = `Event ${i + 1}`;
    if (
      !Number.isInteger(event.month) ||
      event.month < 1 ||
      event.month > horizonMonths
    ) {
      errors.push(`${label}: month must be a whole number between 1 and ${horizonMonths}.`);
    }
    if (!Number.isFinite(event.count) || event.count <= 0 || event.count > 100_000) {
      errors.push(`${label}: count must be greater than 0 and at most 100,000.`);
    }
    if (
      event.price_per_head !== null &&
      event.price_per_head !== undefined &&
      (Number.isNaN(event.price_per_head) || event.price_per_head < 0)
    ) {
      errors.push(`${label}: price per head must be zero or more (or left blank).`);
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
  if (/_fraction$|_margin$/.test(key) || /rate|irr|percent|pct|prob/i.test(key))
    return formatPercent(value);
  const isDuration = /_months?$|_years$|_runs$/.test(key);
  // terminal_value, tax_total and *_profit_total are rupee figures that share
  // no substring with the money vocabulary above; without them the explain
  // dialog printed a bare 150000 next to "Livestock ₹1,00,000".
  if (
    !isDuration &&
    /cost|price|amount|npv|equity|loan|subsidy|capital|shed|equipment|stock|revenue|cash|terminal_value|tax_total|profit/i.test(
      key,
    )
  )
    return formatMoney(value);
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/** snake_case → Title Case ("horizon_months" → "Horizon Months"). */
function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

/** Ratio with a fixed precision; non-finite (e.g. BCR = inf) or null → "—". */
function formatRatio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

/** IRR is a fraction (0.18 → "18.0%"); null → "—". */
function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(1)}%`;
}

/** Cohort head counts are expected values (float64), so they are almost never
 * integral. Render them at the precision the backend narrative uses. */
function formatHead(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : value.toFixed(1);
}

function formatCalibrationValue(value: CalibrationEvidence["calibrated_value"]): string {
  if (Array.isArray(value)) return value.map((item) => formatRatio(item, 3)).join(", ");
  if (!Number.isFinite(value)) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(3);
}

/** Headline metric: shared StatCard with tabular numerals, plus an optional
 * "explain" button in the hint slot that opens the metric's dialog. */
function MetricCard({
  value,
  label,
  icon,
  tint = "default",
  onInfo,
}: {
  value: string;
  label: string;
  icon: LucideIcon;
  tint?: "default" | "emerald" | "amber" | "red";
  onInfo?: () => void;
}) {
  return (
    <StatCard
      label={label}
      value={<span className="tabular-nums">{value}</span>}
      icon={icon}
      tint={tint}
      hint={
        onInfo ? (
          <button
            type="button"
            aria-label={`Explain ${label}`}
            onClick={onInfo}
            className="inline-flex size-4 items-center justify-center rounded-full border border-muted-foreground/40 text-muted-foreground hover:bg-accent"
          >
            <Info className="size-2.5" aria-hidden />
          </button>
        ) : undefined
      }
    />
  );
}

type NumberInputProps = Omit<
  ComponentProps<typeof Input>,
  "type" | "value" | "onChange" | "onBlur"
> &
  NumericRule &
  {
    onValidityChange?: (valid: boolean) => void;
  } &
  (
    | {
        value: number;
        nullable?: false;
        onCommit: (value: number) => void;
      }
    | {
        value: number | null;
        nullable: true;
        onCommit: (value: number | null) => void;
      }
  );

/** Numeric input for the assumptions/events editors: commits only finite
 *  numbers, so NaN (or a silent 0) can never reach the assumptions object.
 *  Blank leaves the stored value unchanged — or commits null when `nullable`
 *  (e.g. price per head = auto); unparseable text shows an inline error. The
 *  draft is local while the field is being edited, so external updates
 *  (defaults / scenario loads) still flow through otherwise. */
function NumberInput(props: NumberInputProps) {
  const {
    value,
    onCommit,
    nullable = false,
    integer,
    min,
    max,
    exclusiveMin,
    unit,
    onValidityChange,
    id,
    "aria-describedby": describedBy,
    ...inputProps
  } = props;
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const errorId = id ? `${id}-error` : undefined;

  function validate(raw: string): { value?: number | null; error?: string } {
    if (raw === "") {
      return nullable ? { value: null } : { error: "A value is required." };
    }
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return { error: "Enter a valid number." };
    if (integer && !Number.isInteger(parsed)) return { error: "Enter a whole number." };
    if (exclusiveMin !== undefined && parsed <= exclusiveMin)
      return { error: `Must be greater than ${exclusiveMin}.` };
    if (min !== undefined && parsed < min) return { error: `Must be at least ${min}.` };
    if (max !== undefined && parsed > max) return { error: `Must be at most ${max}.` };
    return { value: parsed };
  }

  function update(raw: string) {
    setDraft(raw);
    const next = validate(raw);
    const message = next.error ?? null;
    setError(message);
    onValidityChange?.(!message);
    if (!message) {
      if (next.value === null) (onCommit as (v: number | null) => void)(null);
      else (onCommit as (v: number) => void)(next.value as number);
    }
  }

  // Bounds can come from live state (the herd-event Month field takes
  // `max={horizonMonths}`), and validation otherwise ran only on keystroke —
  // so a bound change stranded both the error text and this field's entry in
  // the parent's `invalidFields` set, which gates Run and Save. Re-derive
  // while rendering (React's documented pattern for state that depends on
  // props) rather than in an effect.
  const boundsKey = `${min}|${max}|${exclusiveMin}|${integer}|${nullable}`;
  const [seenBounds, setSeenBounds] = useState(boundsKey);
  if (boundsKey !== seenBounds) {
    setSeenBounds(boundsKey);
    setError(validate(draft ?? (value === null ? "" : String(value))).error ?? null);
  }

  // Keep the parent's invalidFields in step with `error`, however it changed.
  const notifyValidity = useRef(onValidityChange);
  useEffect(() => {
    notifyValidity.current = onValidityChange;
  });
  useEffect(() => {
    notifyValidity.current?.(!error);
  }, [error]);

  return (
    <>
      <Input
        id={id}
        data-unit={unit}
        type="number"
        step={integer ? 1 : "any"}
        min={exclusiveMin === undefined ? min : undefined}
        max={max}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={[describedBy, error ? errorId : null]
          .filter(Boolean)
          .join(" ") || undefined}
        {...inputProps}
        value={draft ?? (value === null ? "" : String(value))}
        onChange={(e) => update(e.target.value)}
        onBlur={() => {
          if (!error) setDraft(null);
        }}
      />
      {error && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </>
  );
}

function NumberArrayInput({
  id,
  value,
  rule,
  onCommit,
  onValidityChange,
}: {
  id: string;
  value: number[];
  rule: NumberArrayRule;
  onCommit: (value: number[]) => void;
  onValidityChange: (valid: boolean) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const errorId = `${id}-error`;

  function validate(raw: string): { parsed: number[]; message: string | null } {
    if (raw.trim() === "" && rule.allowEmpty) return { parsed: [], message: null };
    const tokens = raw.split(",").map((token) => token.trim());
    let message: string | null = null;
    const parsed = tokens.map(Number);
    if (tokens.some((token) => token === "") || parsed.some((n) => !Number.isFinite(n)))
      message = "Enter only comma-separated numbers.";
    else if (rule.exactLength !== undefined && parsed.length !== rule.exactLength)
      message = `Enter exactly ${rule.exactLength} ${rule.itemLabel}.`;
    else if (rule.maxLength !== undefined && parsed.length > rule.maxLength)
      message = `Enter at most ${rule.maxLength} ${rule.itemLabel}.`;
    else if (rule.integer && parsed.some((n) => !Number.isInteger(n)))
      message = `Every ${rule.itemLabel} entry must be a whole number.`;
    else if (
      rule.exclusiveMin !== undefined &&
      parsed.some((n) => n <= rule.exclusiveMin!)
    )
      message = `Every entry must be greater than ${rule.exclusiveMin}.`;
    else if (rule.min !== undefined && parsed.some((n) => n < rule.min!))
      message = `Every entry must be at least ${rule.min}.`;
    else if (rule.max !== undefined && parsed.some((n) => n > rule.max!))
      message = `Every entry must be at most ${rule.max}.`;
    else if (rule.unique && new Set(parsed).size !== parsed.length)
      message = `${humanize(rule.itemLabel)} must not contain duplicates.`;
    else if (
      rule.nondecreasing &&
      parsed.some((n, index) => index > 0 && n < parsed[index - 1])
    )
      message = `${humanize(rule.itemLabel)} must not decrease.`;
    return { parsed, message };
  }

  function update(raw: string) {
    setDraft(raw);
    const { parsed, message } = validate(raw);
    setError(message);
    if (!message) onCommit(parsed);
  }

  // Some bounds are derived from live state rather than constants — notably
  // `max: horizonMonths` for sales.festival_sale_months. Validation otherwise
  // ran only on keystroke, so raising the horizon left the old error rendered
  // AND left this field in the parent's `invalidFields` set, which gates Run
  // and Save; lowering it did the reverse, letting an out-of-range payload
  // through. `rule` is rebuilt every render, so key on its primitive fields.
  const boundsKey = [
    rule.min,
    rule.max,
    rule.exclusiveMin,
    rule.exactLength,
    rule.maxLength,
    rule.integer,
    rule.unique,
    rule.nondecreasing,
    rule.allowEmpty,
  ].join("|");
  const [seenBounds, setSeenBounds] = useState(boundsKey);
  if (boundsKey !== seenBounds) {
    setSeenBounds(boundsKey);
    setError(validate(draft ?? value.join(", ")).message);
  }

  // Keep the parent's invalidFields in step with `error`, however it changed.
  const notifyValidity = useRef(onValidityChange);
  useEffect(() => {
    notifyValidity.current = onValidityChange;
  });
  useEffect(() => {
    notifyValidity.current(!error);
  }, [error]);

  return (
    <>
      <Input
        id={id}
        type="text"
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={error ? errorId : undefined}
        value={draft ?? value.join(", ")}
        onChange={(event) => update(event.target.value)}
        onBlur={() => {
          if (!error) setDraft(null);
        }}
      />
      {error && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </>
  );
}

/** Rows of the side-by-side scenario comparison table. */
const COMPARE_ROWS: {
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
}[] = [
  { key: "npv", label: "NPV", format: (m) => formatMoney(m.npv) },
  { key: "irr", label: "IRR", format: (m) => formatPercent(m.irr) },
  { key: "mirr", label: "MIRR", format: (m) => formatPercent(m.mirr) },
  { key: "bcr", label: "BCR", format: (m) => formatRatio(m.bcr) },
  { key: "avg_dscr", label: "Avg DSCR", format: (m) => formatRatio(m.avg_dscr) },
  { key: "min_dscr", label: "Minimum DSCR", format: (m) => formatRatio(m.min_dscr) },
  {
    key: "operating_margin",
    label: "Operating margin",
    format: (m) => formatPercent(m.operating_margin),
  },
  {
    key: "minimum_cash_balance",
    label: "Minimum cash",
    format: (m) => formatMoney(m.minimum_cash_balance),
  },
  {
    key: "additional_working_capital_required",
    label: "Additional working capital",
    format: (m) => formatMoney(m.additional_working_capital_required),
  },
  {
    key: "payback_month",
    label: "Payback month",
    format: (m) => (m.payback_month === null ? "—" : String(m.payback_month)),
  },
];

type SectionValues = Record<string, unknown>;

/** Top-level sections of the assumptions object that hold editable fields. */
function sectionEntries(assumptions: SimulationAssumptions): [string, SectionValues][] {
  return Object.entries(assumptions).filter(
    (entry): entry is [string, SectionValues] =>
      entry[1] !== null && typeof entry[1] === "object" && !Array.isArray(entry[1]),
  );
}

export default function SimulationPage() {
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
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

  const [breed, setBreed] = useState(DEFAULT_BREED);
  const [system, setSystem] = useState<BreedDefaultsApiSimulationDefaultsGetSystem>(
    DEFAULT_SYSTEM,
  );
  // Auto-load defaults on first mount so the editor isn't empty.
  const [submittedParams, setSubmittedParams] = useState<{
    breed: string;
    system: BreedDefaultsApiSimulationDefaultsGetSystem;
  }>({
    breed: DEFAULT_BREED,
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
  const [invalidFields, setInvalidFields] = useState<Set<string>>(() => new Set());
  const [editorVersion, setEditorVersion] = useState(0);
  const [horizonInputVersion, setHorizonInputVersion] = useState(0);
  // Scheduled herd events live outside the reflected sections editor.
  const [events, setEvents] = useState<HerdEventAssumptions[]>([]);
  const eventKeyCounter = useRef(0);
  const [eventKeys, setEventKeys] = useState<string[]>([]);
  const [explanation, setExplanation] = useState<MetricExplanation | null>(null);
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
  const [scenarioOffset, setScenarioOffset] = useState(0);

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

  useEffect(() => {
    if (defaultsQuery.data?.status === 200 && acceptDefaultsRef.current) {
      acceptDefaultsRef.current = false;
      // react-query v5 has no onSuccess: mirror the explicitly requested
      // defaults payload into editable state.
      setAssumptions(defaultsQuery.data.data);
      const nextEvents = defaultsQuery.data.data.events ?? [];
      setEvents(nextEvents);
      setEventKeys(nextEvents.map(() => `event-${eventKeyCounter.current++}`));
      setLoadedScenario(null);
      setCalibration(null);
      setInvalidFields(new Set());
      setEditorVersion((version) => version + 1);
    }
  }, [defaultsQuery.data]);

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
  }, [scenarioOffset, scenarioPage]);

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
    setInvalidFields((previous) => {
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
  const eventErrors = validateEvents(events, horizonMonths);
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
      assumptionErrors.push("Start year month must be a real month from 1900-01 to 2200-12.");

    const finance = assumptions.finance;
    if (
      finance &&
      typeof finance.loan_fraction_of_project_cost === "number" &&
      typeof finance.subsidy_fraction === "number" &&
      finance.loan_fraction_of_project_cost + finance.subsidy_fraction > 1
    )
      assumptionErrors.push("Loan fraction plus subsidy fraction must not exceed 1.");
    if (
      finance &&
      typeof finance.moratorium_months === "number" &&
      typeof finance.loan_term_months === "number" &&
      finance.moratorium_months >= finance.loan_term_months
    )
      assumptionErrors.push("Moratorium must be shorter than the loan term.");

    const feed = assumptions.feed;
    if (
      feed &&
      typeof feed.initial_fodder_stock_kg_dm === "number" &&
      typeof feed.fodder_storage_capacity_kg_dm === "number" &&
      feed.initial_fodder_stock_kg_dm > feed.fodder_storage_capacity_kg_dm
    )
      assumptionErrors.push("Initial fodder stock must fit within fodder storage capacity.");

    const costs = assumptions.costs;
    if (
      costs?.capacity_basis === "planned" &&
      (costs.planned_capacity_head ?? 0) <= 0
    )
      assumptionErrors.push(
        "Planned capacity must be greater than 0 when the capacity basis is planned.",
      );

    const festivalMonths = assumptions.sales?.festival_sale_months ?? [];
    if (new Set(festivalMonths).size !== festivalMonths.length)
      assumptionErrors.push("Festival sale months must not contain duplicates.");
    if (
      festivalMonths.some(
        (month) => !Number.isInteger(month) || month < 1 || month > horizonMonths,
      )
    )
      assumptionErrors.push(
        `Festival sale months must be whole numbers between 1 and ${horizonMonths}.`,
      );

    const optimizationAssumptions = assumptions.optimization;
    if (
      optimizationAssumptions &&
      typeof optimizationAssumptions.doe_scale_low === "number" &&
      typeof optimizationAssumptions.doe_scale_high === "number" &&
      optimizationAssumptions.doe_scale_low > optimizationAssumptions.doe_scale_high
    )
      assumptionErrors.push("Doe scale low must be less than or equal to doe scale high.");

    const growth = assumptions.growth;
    const weightCurve = growth?.weight_by_age_months;
    // updateField mirrors birth_weight_kg into weight_by_age_months[0], and
    // that path never runs NumberArrayInput's `nondecreasing` rule — so raising
    // the birth weight past month 1 must be caught here or the run only fails
    // at the API with a message that never names the birth weight field.
    if (weightCurve?.some((weight, i) => i > 0 && weight < weightCurve[i - 1]))
      assumptionErrors.push(
        "Weight by age months must not decrease from birth to month 12 — lower the birth weight or raise the curve.",
      );
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
        "Adult doe and buck weights must be at least the highest yearling weight.",
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
          assumptionErrors.push(`${humanize(key)} low and high must bracket 1.`);
      }
    }
  }
  if (events.length > 500)
    assumptionErrors.push("A simulation can contain at most 500 herd events.");
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
    setEvents((prev) =>
      prev.map((event, i) => (i === index ? { ...event, ...patch } : event)),
    );
  }

  function removeEvent(index: number) {
    acceptDefaultsRef.current = false;
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

  async function onUseCurrentHerd() {
    // Do NOT clear the latch up front. Clearing it here made a "Load defaults"
    // that was already in flight land on a `false` latch and be dropped
    // silently — while its button flipped back as though it had applied — so
    // the new breed's head counts got merged into the OLD breed's economics.
    const epoch = ++editorEpochRef.current;
    try {
      const res = await snapshotQuery.refetch();
      if (res.isError || res.data?.status !== 200) {
        toast.error(errorMessage(res.error, "Could not load the herd snapshot."));
        return;
      }
      if (editorEpochRef.current !== epoch) {
        // A different loader replaced the editor while this snapshot was in
        // flight; applying it now would overwrite that scenario's saved head
        // counts and silently detach the scenario binding. Never fail
        // silently — say so, so the operator can click again.
        toast.error(
          "The editor was reloaded while the herd snapshot was loading. Click “Use current herd” again to apply it.",
        );
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
      toast.success(`Loaded current herd (${snap.total_head} head).`);
    } catch (err) {
      toast.error(errorMessage(err, "Could not load the herd snapshot."));
    }
  }

  async function onCalibrateFromFarm() {
    // Claim an ordered editor intent, but do not clear a pending defaults
    // latch. If calibration fails, that older request is still a valid
    // fallback; if a NEW defaults click happens, it increments this epoch and
    // makes the calibration continuation stale.
    const epoch = ++editorEpochRef.current;
    const paramsGeneration = calibrationParamsGeneration.current;
    try {
      const res = await calibrationQuery.refetch();
      if (res.isError || res.data?.status !== 200) {
        toast.error(errorMessage(res.error, "Could not calibrate from farm records."));
        return;
      }
      if (
        editorEpochRef.current !== epoch ||
        calibrationParamsGeneration.current !== paramsGeneration
      ) {
        toast.error(
          "The editor or calibration settings changed while calibration was running. Calibrate again to apply fresh farm evidence.",
        );
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
      toast.success(
        `Calibrated ${calibrated.evidence.length} assumptions from farm records.`,
      );
    } catch (err) {
      toast.error(errorMessage(err, "Could not calibrate from farm records."));
    }
  }

  async function onRun() {
    await simulationAction.run(async () => {
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
        if (res.status === 200)
          setResult({
            data: res.data,
            fingerprint: assumptionsFingerprint(payload),
            options: runOptionsFingerprint(monteCarlo, sensitivity, optimization),
            scenarioId: null,
            source: "Current editor assumptions",
          });
      } catch (err) {
        const message = errorMessage(err, "Simulation failed");
        setRunError(message);
        toast.error(message);
      }
    });
  }

  async function onRunScenario(scenario: ScenarioRow) {
    if (!scenarioUsable(scenario)) return;
    await simulationAction.run(async () => {
      setRunError(null);
      setRunningScenarioId(scenario.id);
      try {
        const res = await runScenarioMutation.mutateAsync({
          scenarioId: scenario.id,
          params: { monte_carlo: monteCarlo, sensitivity, optimization },
        });
        if (res.status === 200)
          setResult({
            data: res.data,
            fingerprint: assumptionsFingerprint(scenario.assumptions),
            options: runOptionsFingerprint(monteCarlo, sensitivity, optimization),
            scenarioId: scenario.id,
            source: `Saved scenario “${scenario.name}”`,
          });
      } catch (err) {
        const message = errorMessage(err, "Scenario run failed");
        setRunError(message);
        toast.error(message);
      } finally {
        setRunningScenarioId(null);
      }
    });
  }

  async function onDeleteScenario(scenario: ScenarioRow) {
    await simulationAction.run(async () => {
      if (!window.confirm(`Delete scenario "${scenario.name}"?`)) return;
      try {
        await deleteMutation.mutateAsync({ scenarioId: scenario.id });
        toast.success("Scenario deleted.");
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
        toast.error(errorMessage(err, "Could not delete the scenario."));
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
      setSaveError(null);
      try {
        const created = await createMutation.mutateAsync({
          data: {
            name: saveName.trim(),
            notes: saveNotes.trim(),
            assumptions: payload,
          },
        });
        toast.success("Scenario saved.");
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
        const message = errorMessage(err, "Could not save the scenario.");
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
    await simulationAction.run(async () => {
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
        toast.success("Scenario updated.");
        invalidateScenarios();
      } catch (err) {
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
              scenarioUsable(fresh.data)
            ) {
              // Replace the stale editor wholesale. Merely advancing the
              // revision while preserving old assumptions would turn the
              // retry into a lost update against the other operator.
              loadScenarioIntoEditor(fresh.data);
              toast.error(
                "This scenario changed elsewhere. The editor was refreshed to the latest revision; review it before saving again.",
              );
              return;
            }
          } catch {
            // Keep the original conflict detail below when refresh fails.
          }
        }
        toast.error(errorMessage(err, "Could not update the scenario."));
      }
    });
  }

  /** One editor row; the input type follows the value type. */
  function renderField(section: string, key: string, value: unknown) {
    const id = `sim-${section}-${key}`;
    const stringOptions = STRING_FIELD_OPTIONS[`${section}.${key}`];
    if (stringOptions) {
      return (
        <div key={id} className="space-y-1.5">
          <Label htmlFor={id}>{humanize(key)}</Label>
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
      const rule = numericRule(section, key);
      return (
        <div key={id} className="space-y-1.5">
          <Label htmlFor={id}>{humanize(key)}</Label>
          <NumberInput
            id={id}
            value={value}
            nullable
            {...rule}
            onValidityChange={(valid) => setFieldValidity(`field:${id}`, valid)}
            onCommit={(n) => updateField(section, key, n)}
          />
          <p className="text-xs text-muted-foreground">
            {rule.unit ? `Unit: ${rule.unit} — ` : ""}blank means no limit
          </p>
        </div>
      );
    }
    if (typeof value === "number") {
      const rule = numericRule(section, key);
      return (
        <div key={id} className="space-y-1.5">
          <Label htmlFor={id}>{humanize(key)}</Label>
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
          {rule.unit && <p className="text-xs text-muted-foreground">Unit: {rule.unit}</p>}
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
            {humanize(key)}
          </Label>
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
          <Label htmlFor={id}>{humanize(key)}</Label>
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
              Enter a real month from 1900-01 to 2200-12.
            </p>
          )}
        </div>
      );
    }
    if (Array.isArray(value)) {
      const rule = numberArrayRule(section, key, horizonMonths);
      return (
        <div key={id} className="space-y-1.5 sm:col-span-2 lg:col-span-3">
          <Label htmlFor={id}>
            {humanize(key)} ({rule.itemLabel}, comma-separated)
          </Label>
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
      return (
        <div
          key={id}
          className="space-y-2 rounded-lg border p-3 sm:col-span-2 lg:col-span-3"
        >
          <p className="text-sm font-medium">{humanize(key)}</p>
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
    if (typeof value === "number") {
      const rule: NumericRule =
        section === "risk" && (subKey === "low" || subKey === "high")
          ? { exclusiveMin: 0, max: 100, unit: "multiplier" }
          : numericRule(section, subKey);
      return (
        <div key={id} className="space-y-1.5">
          <Label htmlFor={id}>{humanize(subKey)}</Label>
          <NumberInput
            id={id}
            value={value}
            {...rule}
            onValidityChange={(valid) => setFieldValidity(`field:${id}`, valid)}
            onCommit={(n) => updateNestedField(section, key, subKey, n)}
          />
          {rule.unit && <p className="text-xs text-muted-foreground">Unit: {rule.unit}</p>}
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
            {humanize(subKey)}
          </Label>
        </div>
      );
    }
    if (typeof value === "string") {
      return (
        <div key={id} className="space-y-1.5">
          <Label htmlFor={id}>{humanize(subKey)}</Label>
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
            label="NPV"
            icon={IndianRupee}
            tint={m.npv >= 0 ? "emerald" : "red"}
            onInfo={infoFor("npv")}
          />
          <MetricCard
            value={formatPercent(m.irr)}
            label="IRR"
            icon={Percent}
            onInfo={infoFor("irr")}
          />
          <MetricCard
            value={formatPercent(m.mirr)}
            label="MIRR"
            icon={TrendingUp}
            onInfo={infoFor("mirr")}
          />
          <MetricCard
            value={formatRatio(m.bcr)}
            label="BCR"
            icon={Scale}
            tint={m.bcr === null ? "default" : m.bcr >= 1 ? "emerald" : "red"}
            onInfo={infoFor("bcr")}
          />
          <MetricCard
            value={formatRatio(m.avg_dscr)}
            label="Avg DSCR"
            icon={Gauge}
            tint={
              m.avg_dscr === null
                ? "default"
                : m.avg_dscr >= 1.2
                  ? "emerald"
                  : m.avg_dscr >= 1
                    ? "amber"
                    : "red"
            }
            onInfo={infoFor("avg_dscr")}
          />
          <MetricCard
            value={formatRatio(m.min_dscr)}
            label="Minimum DSCR"
            icon={ShieldAlert}
            tint={
              m.min_dscr === null
                ? "default"
                : m.min_dscr >= 1.2
                  ? "emerald"
                  : m.min_dscr >= 1
                    ? "amber"
                    : "red"
            }
            onInfo={infoFor("min_dscr")}
          />
          <MetricCard
            value={formatPercent(m.operating_margin)}
            label="Operating margin"
            icon={TrendingUp}
            tint={
              m.operating_margin === null
                ? "default"
                : m.operating_margin >= 0
                  ? "emerald"
                  : "red"
            }
            onInfo={infoFor("operating_margin")}
          />
          <MetricCard
            value={m.payback_month === null ? "—" : String(m.payback_month)}
            label="Payback month"
            icon={CalendarClock}
            tint="amber"
            onInfo={infoFor("payback_month")}
          />
          <MetricCard
            value={
              m.break_even_meat_price_per_kg === null
                ? "—"
                : formatMoney(m.break_even_meat_price_per_kg)
            }
            label="Break-even meat (₹/kg)"
            icon={Beef}
            tint="amber"
            onInfo={infoFor("break_even_meat_price_per_kg")}
          />
          <MetricCard
            value={formatMoney(m.project_cost)}
            label="Project cost"
            icon={Wallet}
            onInfo={infoFor("project_cost")}
          />
          <MetricCard
            value={formatMoney(m.loan_amount)}
            label="Loan"
            icon={Landmark}
            onInfo={infoFor("loan_amount")}
          />
          <MetricCard
            value={formatMoney(m.subsidy_amount)}
            label="Subsidy"
            icon={HandCoins}
            tint="emerald"
            onInfo={infoFor("subsidy_amount")}
          />
          <MetricCard
            value={formatMoney(m.equity)}
            label="Equity"
            icon={PiggyBank}
            onInfo={infoFor("equity")}
          />
          <MetricCard
            value={formatHead(m.peak_capacity_head)}
            label="Funded capacity (head)"
            icon={Building2}
            onInfo={infoFor("peak_capacity_head")}
          />
          <MetricCard
            value={formatMoney(m.terminal_value)}
            label="Terminal value"
            icon={Wallet}
            onInfo={infoFor("terminal_value")}
          />
          <MetricCard
            value={formatMoney(m.minimum_cash_balance)}
            label={`Minimum cash (month ${m.minimum_cash_month})`}
            icon={Wallet}
            tint={m.minimum_cash_balance < 0 ? "red" : "emerald"}
            onInfo={infoFor("minimum_cash_balance")}
          />
          <MetricCard
            value={formatMoney(m.additional_working_capital_required)}
            label="Additional working capital"
            icon={HandCoins}
            tint={m.additional_working_capital_required > 0 ? "red" : "emerald"}
            onInfo={infoFor("additional_working_capital_required")}
          />
          <MetricCard
            value={`${formatRatio(r.feed_summary.land_requirement_acres)} acres`}
            label="Fodder land required"
            icon={Wheat}
          />
          <MetricCard
            value={String(r.feed_summary.fodder_deficit_months)}
            label="Fodder deficit months"
            icon={Wheat}
            tint={r.feed_summary.fodder_deficit_months > 0 ? "amber" : "emerald"}
          />
        </div>

        {r.narrative_report && r.narrative_report.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Report</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              {r.narrative_report.map((section) => {
                const verdict =
                  section.key === "viability_verdict" &&
                  typeof section.figures?.verdict === "string"
                    ? section.figures.verdict
                    : null;
                return (
                  <section key={section.key} className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-medium">{section.title}</h3>
                      {verdict && (
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                            verdict === "VIABLE"
                              ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                              : verdict === "VIABLE WITH CAUTION"
                                ? "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
                                : "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                          }`}
                        >
                          {verdict}
                        </span>
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
            className="flex gap-3 border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <p>
              Projected peak herd exceeds funded housing and equipment capacity by{" "}
              {formatHead(capacityShortfall)} head. This plan is not physically feasible at
              the configured capacity.
            </p>
          </div>
        )}

        <DataTableCard
          title="Capital and terminal-value bridge"
          description={`${humanize(r.project_cost_breakdown.capacity_basis)} basis funds ${formatHead(r.project_cost_breakdown.capacity_places)} places against a projected peak of ${formatHead(r.project_cost_breakdown.projected_peak_head)} head.`}
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Component</TableHead>
                <TableHead className="text-right">Opening project cost</TableHead>
                <TableHead className="text-right">Closing recovery</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[
                {
                  label: "Shed",
                  opening: r.project_cost_breakdown.shed_cost,
                  closing: r.terminal_value_breakdown.shed,
                },
                {
                  label: "Equipment",
                  opening: r.project_cost_breakdown.equipment_cost,
                  closing: r.terminal_value_breakdown.equipment,
                },
                {
                  label: "Livestock",
                  opening: r.project_cost_breakdown.stock_cost,
                  closing: r.terminal_value_breakdown.livestock,
                },
                {
                  label: "Working capital",
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
                <TableCell className="font-semibold">Total</TableCell>
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
          title="Annual P&amp;L"
          description="Accrual profit, tax, debt service and project cash flow by year."
        >
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Year</TableHead>
                  <TableHead className="text-right">Revenue</TableHead>
                  <TableHead className="text-right">Opex</TableHead>
                  <TableHead className="text-right">EBITDA</TableHead>
                  <TableHead className="text-right">Depreciation</TableHead>
                  <TableHead className="text-right">EBIT</TableHead>
                  <TableHead className="text-right">Interest</TableHead>
                  <TableHead className="text-right">Tax</TableHead>
                  <TableHead className="text-right">PAT</TableHead>
                  <TableHead className="text-right">Debt service</TableHead>
                  <TableHead className="text-right">Terminal value</TableHead>
                  <TableHead className="text-right">Net cash flow</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {r.annual_pl.map((row) => (
                  <TableRow key={row.year}>
                    <TableCell>{row.year}</TableCell>
                    {[
                      row.total_revenue,
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
          title="Monthly projection"
          description={`Herd and cash-flow detail over ${r.months.length} months.`}
        >
          <div className="max-h-96 overflow-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Month</TableHead>
                  <TableHead>Total herd</TableHead>
                  <TableHead>Births</TableHead>
                  <TableHead>Deaths</TableHead>
                  <TableHead>Sales head</TableHead>
                  <TableHead className="text-right">Meat ₹/kg</TableHead>
                  <TableHead className="text-right">Sales revenue</TableHead>
                  <TableHead className="text-right">Purchased green kg</TableHead>
                  <TableHead className="text-right">Feed cost</TableHead>
                  <TableHead className="text-right">Selling cost</TableHead>
                  <TableHead className="text-right">Tax</TableHead>
                  <TableHead className="text-right">Debt service</TableHead>
                  <TableHead className="text-right">Terminal value</TableHead>
                  <TableHead className="text-right">Net cash flow</TableHead>
                  <TableHead className="text-right">Cash balance</TableHead>
                  <TableHead className="text-right">Cumulative cash flow</TableHead>
                  <TableHead className="text-right">Fodder stock (kg DM)</TableHead>
                  <TableHead>Events</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {r.months.map((row) => (
                  <TableRow
                    key={row.month}
                    className={
                      row.events && row.events.length > 0
                        ? "bg-amber-50 dark:bg-amber-950/40"
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
                Monte Carlo ({r.monte_carlo.runs} runs, seed {r.monte_carlo.seed})
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {/* Tints follow the sign, as every other NPV/cash card does:
                    a hard-coded emerald painted a loss-making mean as success. */}
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_mean)}
                  label="NPV mean"
                  icon={IndianRupee}
                  tint={r.monte_carlo.npv_mean >= 0 ? "emerald" : "red"}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_std)}
                  label="NPV std"
                  icon={Sigma}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_p5)}
                  label="P5"
                  icon={ChartColumn}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_p50)}
                  label="P50"
                  icon={ChartColumn}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_p95)}
                  label="P95"
                  icon={ChartColumn}
                />
                <MetricCard
                  value={`${(r.monte_carlo.prob_npv_negative * 100).toFixed(1)}%`}
                  label="P(NPV < 0)"
                  icon={TriangleAlert}
                  tint={r.monte_carlo.prob_npv_negative > 0 ? "red" : "emerald"}
                />
                <MetricCard
                  value={formatPercent(r.monte_carlo.prob_liquidity_shortfall)}
                  label="P(cash shortfall)"
                  icon={Wallet}
                  tint={
                    r.monte_carlo.prob_liquidity_shortfall > 0 ? "red" : "emerald"
                  }
                />
                <MetricCard
                  value={formatPercent(r.monte_carlo.prob_dscr_below_one)}
                  label="P(DSCR < 1)"
                  icon={ShieldAlert}
                  tint={r.monte_carlo.prob_dscr_below_one > 0 ? "red" : "emerald"}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.minimum_cash_p5)}
                  label="Minimum cash P5"
                  icon={Wallet}
                  tint={r.monte_carlo.minimum_cash_p5 < 0 ? "red" : "emerald"}
                />
                <MetricCard
                  value={formatMoney(r.monte_carlo.minimum_cash_p50)}
                  label="Minimum cash P50"
                  icon={Wallet}
                  tint={r.monte_carlo.minimum_cash_p50 < 0 ? "red" : "emerald"}
                />
              </div>
              <MonteCarloHistogram
                counts={r.monte_carlo.npv_histogram_counts}
                edges={r.monte_carlo.npv_histogram_edges}
              />
              <p className="text-sm text-muted-foreground">
                Mean path events: {formatRatio(r.monte_carlo.mean_disease_outbreaks, 2)}
                {" disease, "}
                {formatRatio(r.monte_carlo.mean_drought_events, 2)} drought, and{" "}
                {formatRatio(r.monte_carlo.mean_market_crashes, 2)} market crash.
              </p>
              <RiskBandTable
                herd={r.monte_carlo.herd_percentiles}
                liquidity={r.monte_carlo.liquidity_percentiles}
              />
            </CardContent>
          </Card>
        )}

        {r.optimization && <OptimizationResults result={r.optimization} />}

        {sortedSensitivity && sortedSensitivity.length > 0 && (
          <DataTableCard
            title="Sensitivity (ΔNPV)"
            description="Parameters ranked by their largest absolute NPV swing."
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Parameter</TableHead>
                  <TableHead>ΔNPV low</TableHead>
                  <TableHead>ΔNPV high</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedSensitivity.map((item) => (
                  <TableRow key={item.parameter}>
                    <TableCell>{humanize(item.parameter)}</TableCell>
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
                          <dt className="text-muted-foreground">{humanize(key)}</dt>
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
        title="Simulation"
        description="Project herd growth, cash flow and viability from editable bio-economic assumptions."
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
              {compareQuery.isFetching ? "Comparing…" : "Compare selected"}
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
                  Save as scenario
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
                      ? "Updating…"
                      : `Update ${loadedScenario.name}`}
                  </Button>
                )}
              </>
            )}
            <Button
              onClick={() => void onRun()}
              disabled={!assumptions || hasEditorErrors || simulationAction.pending}
            >
              <Play />
              {runMutation.isPending ? "Running…" : "Run simulation"}
            </Button>
          </>
        }
      />

      <Card>
        <CardHeader>
          <CardTitle>Setup</CardTitle>
          <CardDescription>
            Pick a breed and rearing system, then load the baseline assumptions.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="sim-breed">Breed</Label>
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
              <Label htmlFor="sim-system">System</Label>
              <Select
                value={system}
                onValueChange={(v) => {
                  calibrationParamsGeneration.current += 1;
                  setSystem(v as BreedDefaultsApiSimulationDefaultsGetSystem);
                }}
                items={Object.fromEntries(
                  (breeds?.systems ?? [system]).map((s) => [s, humanize(s)]),
                )}
              >
                <SelectTrigger id="sim-system">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(breeds?.systems ?? [system]).map((s) => (
                    <SelectItem key={s} value={s}>
                      {humanize(s)}
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
                  void defaultsQuery.refetch();
                } else {
                  setSubmittedParams({ breed, system });
                }
              }}
              disabled={defaultsQuery.isFetching}
            >
              <RefreshCw />
              {defaultsQuery.isFetching ? "Loading…" : "Load defaults"}
            </Button>
            <Button
              variant="outline"
              onClick={() => void onUseCurrentHerd()}
              disabled={!assumptions || snapshotQuery.isFetching || defaultsQuery.isFetching}
            >
              <Building2 />
              {snapshotQuery.isFetching ? "Loading…" : "Use current herd"}
            </Button>
            {canCalibrate && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="sim-calibration-lookback">Calibration history</Label>
                  <Select
                    value={String(calibrationLookback)}
                    onValueChange={(value) => {
                      calibrationParamsGeneration.current += 1;
                      setCalibrationLookback(Number(value));
                    }}
                    items={{
                      "12": "12 months",
                      "24": "24 months",
                      "36": "36 months",
                      "60": "60 months",
                    }}
                  >
                    <SelectTrigger id="sim-calibration-lookback">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {[12, 24, 36, 60].map((months) => (
                        <SelectItem key={months} value={String(months)}>
                          {months} months
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
                  {calibrationQuery.isFetching ? "Calibrating…" : "Calibrate from farm"}
                </Button>
              </>
            )}
          </div>
          {defaultsQuery.isError && (
            <p role="alert" className="text-sm text-destructive">
              {defaultsQuery.error instanceof ApiError
                ? defaultsQuery.error.detail
                : "Could not load the defaults."}
            </p>
          )}
          {breedsQuery.isError && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(breedsQuery.error, "Could not load available breeds and systems.")}
            </p>
          )}
        </CardContent>
      </Card>

      {calibration && (
        <DataTableCard
          title="Farm calibration evidence"
          description={`Records through ${calibration.reference_date}, using a ${calibration.lookback_months}-month lookback.`}
          actions={
            <span className="text-sm font-medium tabular-nums">
              {formatPercent(calibration.coverage_score)} coverage
            </span>
          }
          contentClassName="space-y-4"
        >
          {calibration.warnings.length > 0 && (
            <div
              role="status"
              className="flex gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
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
              No farm observations met the evidence thresholds; breed-system defaults remain
              in use.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Assumption</TableHead>
                    <TableHead>Baseline</TableHead>
                    <TableHead>Calibrated</TableHead>
                    <TableHead>Sample</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead>Evidence</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {calibration.evidence.map((item) => (
                    <TableRow key={item.path}>
                      <TableCell className="font-medium">
                        {item.path.split(".").map(humanize).join(" / ")}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {formatCalibrationValue(item.previous_value)}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {formatCalibrationValue(item.calibrated_value)}
                      </TableCell>
                      <TableCell className="tabular-nums">{item.sample_size}</TableCell>
                      <TableCell>{humanize(item.confidence)}</TableCell>
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
      <Card>
        <CardHeader>
          <CardTitle>Assumptions</CardTitle>
          <CardDescription>
            Model inputs grouped by section, mirroring the backend defaults payload.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!assumptions && !defaultsQuery.isError && (
            <p className="text-muted-foreground">Loading defaults…</p>
          )}
          {assumptions?.meta && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-muted-foreground">Horizon presets:</span>
              {HORIZON_PRESETS.map((preset) => (
                <Button
                  key={preset.months}
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    updateField("meta", "horizon_months", preset.months);
                    setFieldValidity("field:sim-meta-horizon_months", true);
                    setHorizonInputVersion((version) => version + 1);
                  }}
                >
                  {preset.label}
                </Button>
              ))}
              <span className="text-xs text-muted-foreground">
                120 months = 10 years
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
                <summary className="cursor-pointer px-4 py-2.5 text-sm font-medium hover:bg-muted/50">
                  {humanize(section)}
                </summary>
                <div className="grid gap-3 border-t px-4 py-3 sm:grid-cols-2 lg:grid-cols-3">
                  {Object.entries(values).map(([key, value]) =>
                    renderField(section, key, value),
                  )}
                </div>
              </details>
            ))}
          {invalidFields.size > 0 && (
            <p role="alert" className="text-sm text-destructive">
              Fix {invalidFields.size} highlighted numeric field
              {invalidFields.size === 1 ? "" : "s"} before running or saving.
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
        title="Herd events"
        description="Purchases or sales that fire at a given simulation month."
        actions={
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
            Add event
          </Button>
        }
        contentClassName="space-y-3"
      >
        {events.length === 0 ? (
          <EmptyState
            icon={CalendarClock}
            title="No scheduled events"
            description="Add purchases or sales that fire at a given simulation month."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Month</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Class</TableHead>
                <TableHead>Count</TableHead>
                <TableHead>Price/head</TableHead>
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
                      aria-label="Month"
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
                      items={EVENT_KIND_ITEMS}
                    >
                      <SelectTrigger aria-label="Kind" size="sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(EVENT_KIND_ITEMS).map(([value, label]) => (
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
                      items={EVENT_CLASS_ITEMS}
                    >
                      <SelectTrigger aria-label="Class" size="sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(EVENT_CLASS_ITEMS).map(([value, label]) => (
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
                      aria-label="Count"
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
                      aria-label="Price per head"
                      min={0}
                      max={1_000_000_000}
                      placeholder="auto"
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
                      Remove
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
      </fieldset>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl bg-card px-4 py-3 ring-1 ring-foreground/10">
          <span className="text-sm font-medium">Run options</span>
          <div className="flex items-center gap-2">
            <Checkbox
              id="sim-monte-carlo"
              checked={monteCarlo}
              onCheckedChange={(checked) => setMonteCarlo(checked === true)}
            />
            <Label htmlFor="sim-monte-carlo" className="font-normal">
              Monte Carlo
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="sim-sensitivity"
              checked={sensitivity}
              onCheckedChange={(checked) => setSensitivity(checked === true)}
            />
            <Label htmlFor="sim-sensitivity" className="font-normal">
              Sensitivity
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="sim-optimization"
              checked={optimization}
              onCheckedChange={(checked) => setOptimization(checked === true)}
            />
            <Label htmlFor="sim-optimization" className="font-normal">
              Optimization
            </Label>
          </div>
        </div>
        {loadedScenario && (
          <p className="text-sm text-muted-foreground">
            Editing scenario: {loadedScenario.name}
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
          <h2 className="text-lg font-semibold">Results</h2>
          <p className="text-sm text-muted-foreground">Source: {result.source}</p>
          <p className="text-xs text-muted-foreground">
            Model {result.data.model_version} · assumptions fingerprint{" "}
            <span className="font-mono" title={result.data.assumptions_fingerprint}>
              {result.data.assumptions_fingerprint.slice(0, 12)}
            </span>
          </p>
          {resultIsStale && (
            <p
              role="status"
              className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
            >
              These results do not match the current{" "}
              {result.scenarioId === null ? "editor assumptions" : "saved scenario"} or run
              options. Run the simulation again before using them for a decision.
            </p>
          )}
          {renderResults(result.data)}
        </section>
      )}

      <DataTableCard
        title="Scenarios"
        description={`Saved assumption sets to load, run, update or compare. Select 2–${MAX_COMPARE_SCENARIOS} valid scenarios (${selectedUsableIds.length} selected).`}
        contentClassName="space-y-4"
      >
        {scenariosQuery.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(scenariosQuery.error, "Could not load saved scenarios.")}
          </p>
        ) : scenariosQuery.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading scenarios…</p>
        ) : scenarioTotal === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title="No saved scenarios yet."
            description="Save the current assumptions as a scenario to rerun or compare later."
          />
        ) : scenarios.length === 0 ? (
          <p role="status" className="text-sm text-muted-foreground">
            This scenario page no longer exists. Returning to the last available page…
          </p>
        ) : (
          <div className="space-y-3">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead>Name</TableHead>
                  <TableHead>Notes</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {scenarios.map((scenario) => (
                  <TableRow key={scenario.id}>
                    <TableCell>
                      <Checkbox
                        aria-label={`Compare ${scenario.name}`}
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
                          Invalid saved assumptions
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
                          Load
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => void onRunScenario(scenario)}
                          disabled={
                            !scenarioUsable(scenario) || simulationAction.pending
                          }
                        >
                          {runningScenarioId === scenario.id ? "Running…" : "Run"}
                        </Button>
                        {canManage && (
                          <Button
                            variant="destructive"
                            size="sm"
                            disabled={simulationAction.pending}
                            onClick={() => void onDeleteScenario(scenario)}
                          >
                            Delete
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
              label="saved scenarios"
            />
          </div>
        )}
        {compareQuery.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(compareQuery.error, "Could not compare the selected scenarios.")}
          </p>
        )}
        {comparePayload && comparePayload.results.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Comparison</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Metric</TableHead>
                  {comparePayload.scenarios.map((scenario) => (
                    <TableHead key={scenario.id}>{scenario.name}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {COMPARE_ROWS.map((row) => (
                  <TableRow key={row.key}>
                    <TableCell className="font-medium">{row.label}</TableCell>
                    {comparePayload.results.map((r, i) => (
                      <TableCell key={comparePayload.scenarios[i]?.id ?? i}>
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
            <DialogTitle>Save as scenario</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {saveError && (
              <p role="alert" className="text-sm text-destructive">
                {saveError}
              </p>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="scenario-name">Name *</Label>
              <Input
                id="scenario-name"
                maxLength={120}
                disabled={simulationAction.pending}
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="scenario-notes">Notes</Label>
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
                {simulationAction.pending ? "Saving…" : "Save scenario"}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** NPV histogram: flex bars, height proportional to the max bin count. */
function MonteCarloHistogram({ counts, edges }: { counts: number[]; edges: number[] }) {
  const max = Math.max(...counts, 1);
  return (
    <div className="flex h-24 items-end gap-px" aria-label="NPV histogram">
      {counts.map((count, i) => (
        <div
          key={i}
          className="flex-1 rounded-t bg-primary/70"
          style={{ height: `${Math.max((count / max) * 100, count > 0 ? 4 : 0)}%` }}
          title={`${formatMoney(edges[i])} – ${formatMoney(edges[i + 1])}: ${count} runs`}
        />
      ))}
    </div>
  );
}

function RiskBandTable({
  herd,
  liquidity,
}: {
  herd: PercentileBand;
  liquidity: PercentileBand;
}) {
  const monthCount = Math.min(herd.p50.length, liquidity.p50.length);
  const indices = Array.from(
    new Set([
      0,
      ...Array.from({ length: monthCount }, (_, index) => index).filter(
        (index) => (index + 1) % 12 === 0,
      ),
      monthCount - 1,
    ]),
  ).filter((index) => index >= 0 && index < monthCount);

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">Annual uncertainty checkpoints</h3>
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Month</TableHead>
              <TableHead className="text-right">Herd P5</TableHead>
              <TableHead className="text-right">Herd P50</TableHead>
              <TableHead className="text-right">Herd P95</TableHead>
              <TableHead className="text-right">Cash P5</TableHead>
              <TableHead className="text-right">Cash P50</TableHead>
              <TableHead className="text-right">Cash P95</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {indices.map((index) => (
              <TableRow key={index}>
                <TableCell>{index + 1}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatHead(herd.p5[index])}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatHead(herd.p50[index])}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatHead(herd.p95[index])}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatMoney(liquidity.p5[index])}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatMoney(liquidity.p50[index])}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatMoney(liquidity.p95[index])}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function optimizationCandidateIdentity(candidate: OptimizationCandidate): string {
  return [
    candidate.starting_does,
    candidate.starting_bucks,
    candidate.max_breeding_does,
    candidate.sale_age_months,
    candidate.female_retention_fraction,
    candidate.loan_fraction,
  ].join(":");
}

function OptimizationResults({
  result,
}: {
  result: NonNullable<SimulationResult["optimization"]>;
}) {
  const baselineIdentity = optimizationCandidateIdentity(result.baseline);
  const recommendedIdentity = result.recommended
    ? optimizationCandidateIdentity(result.recommended)
    : null;
  const rows: { label: string; candidate: OptimizationCandidate }[] = [
    {
      label:
        recommendedIdentity === baselineIdentity ? "Baseline / recommended" : "Baseline",
      candidate: result.baseline,
    },
  ];
  if (result.recommended && recommendedIdentity !== baselineIdentity) {
    rows.push({ label: "Recommended", candidate: result.recommended });
  }
  const seen = new Set(rows.map((row) => optimizationCandidateIdentity(row.candidate)));
  for (const candidate of result.alternatives ?? []) {
    const identity = optimizationCandidateIdentity(candidate);
    if (seen.has(identity)) continue;
    seen.add(identity);
    rows.push({ label: `Alternative ${candidate.rank}`, candidate });
  }

  return (
    <DataTableCard
      title={
        <span className="flex items-center gap-2">
          <Target className="size-4" aria-hidden />
          Optimization
        </span>
      }
      description={`${result.feasible_candidates} of ${result.evaluated_candidates} evaluated candidates satisfy the ${humanize(result.objective)} objective constraints.`}
      contentClassName="space-y-4"
    >
      {result.recommended === null && (
        <div
          role="alert"
          className="flex gap-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
        >
          <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          No evaluated candidate satisfies every financing and capacity constraint. The
          baseline below is diagnostic, not a recommendation.
        </div>
      )}
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Decision set</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Does / bucks / ceiling</TableHead>
              <TableHead className="text-right">Sale age</TableHead>
              <TableHead className="text-right">Retention</TableHead>
              <TableHead className="text-right">Debt share</TableHead>
              <TableHead className="text-right">Project cost</TableHead>
              <TableHead className="text-right">Capacity / peak</TableHead>
              <TableHead className="text-right">NPV</TableHead>
              <TableHead className="text-right">IRR</TableHead>
              <TableHead className="text-right">Min DSCR</TableHead>
              <TableHead className="text-right">Minimum cash</TableHead>
              <TableHead className="text-right">Funding gap</TableHead>
              <TableHead>Constraint findings</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ label, candidate }) => (
              <TableRow key={`${label}:${optimizationCandidateIdentity(candidate)}`}>
                <TableCell className="font-medium">{label}</TableCell>
                <TableCell
                  className={candidate.feasible ? "text-emerald-700" : "text-destructive"}
                >
                  {candidate.feasible ? "Feasible" : "Infeasible"}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {candidate.starting_does} / {candidate.starting_bucks} /{" "}
                  {candidate.max_breeding_does}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {candidate.sale_age_months} mo
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatPercent(candidate.female_retention_fraction)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatPercent(candidate.loan_fraction)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatMoney(candidate.project_cost)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatHead(candidate.capacity_places)} /{" "}
                  {formatHead(candidate.projected_peak_head)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatMoney(candidate.npv)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatPercent(candidate.irr)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatRatio(candidate.min_dscr)}
                </TableCell>
                <TableCell
                  className={`text-right tabular-nums ${candidate.minimum_cash_balance < 0 ? "text-destructive" : ""}`}
                >
                  {formatMoney(candidate.minimum_cash_balance)}
                </TableCell>
                <TableCell
                  className={`text-right tabular-nums ${candidate.funding_gap > 0 ? "text-destructive" : ""}`}
                >
                  {formatMoney(candidate.funding_gap)}
                </TableCell>
                <TableCell className="min-w-56 text-xs">
                  {(candidate.constraint_violations ?? []).length > 0
                    ? candidate.constraint_violations?.join("; ")
                    : "None"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </DataTableCard>
  );
}
