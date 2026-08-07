"use client";

/**
 * Simulation: bio-economic herd projection. Generic assumptions editor driven
 * by the backend defaults payload (rendered from whatever keys are present),
 * ad-hoc/scenario runs, viability results and saved-scenario management.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  Beef,
  CalendarClock,
  ChartColumn,
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
  Save,
  Scale,
  Sigma,
  TriangleAlert,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ComponentProps } from "react";
import { toast } from "sonner";

import {
  getListScenariosApiSimulationScenariosGetQueryKey,
  useBreedDefaultsApiSimulationDefaultsGet,
  useCompareScenariosApiSimulationScenariosCompareGet,
  useCreateScenarioApiSimulationScenariosPost,
  useDeleteScenarioApiSimulationScenariosScenarioIdDelete,
  useHerdSnapshotApiSimulationHerdSnapshotGet,
  useListBreedsApiSimulationDefaultsBreedsGet,
  useListScenariosApiSimulationScenariosGet,
  useRunAdhocApiSimulationRunPost,
  useRunScenarioApiSimulationScenariosScenarioIdRunPost,
  useUpdateScenarioApiSimulationScenariosScenarioIdPatch,
} from "@/api/generated/endpoints";
import type {
  HerdEventAssumptions,
  MetricExplanation,
  ScenarioOut,
  SimulationAssumptions,
  SimulationResult,
  ViabilityMetrics,
} from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
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
import { formatDate, formatMoney } from "@/lib/format";
import { usePermissions } from "@/lib/use-permissions";

const DEFAULT_BREED = "osmanabadi";
const DEFAULT_SYSTEM = "stall_fed";

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
    if (!Number.isInteger(event.count) || event.count <= 0) {
      errors.push(`${label}: count must be a positive whole number.`);
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
  if (/rate|irr|percent|pct|prob/i.test(key)) return formatPercent(value);
  if (/cost|price|amount|npv|equity|loan|subsidy|capital|shed|equipment|stock|revenue|cash/i.test(key))
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
function formatPercent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
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
  const { value, onCommit, nullable = false, ...inputProps } = props;
  const [draft, setDraft] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  return (
    <>
      <Input
        type="number"
        step="any"
        aria-invalid={invalid || undefined}
        {...inputProps}
        value={draft ?? (value === null ? "" : String(value))}
        onChange={(e) => {
          const raw = e.target.value;
          setDraft(raw);
          if (raw === "") {
            setInvalid(false);
            if (nullable) (onCommit as (v: number | null) => void)(null);
            return;
          }
          const n = Number(raw);
          if (!Number.isFinite(n)) {
            setInvalid(true);
            return;
          }
          setInvalid(false);
          (onCommit as (v: number) => void)(n);
        }}
        onBlur={() => {
          setDraft(null);
          setInvalid(false);
        }}
      />
      {invalid && <p className="text-sm text-destructive">Enter a valid number.</p>}
    </>
  );
}

/** Rows of the side-by-side scenario comparison table. */
const COMPARE_ROWS: {
  key: keyof Pick<
    ViabilityMetrics,
    "npv" | "irr" | "bcr" | "avg_dscr" | "payback_month"
  >;
  label: string;
  format: (m: ViabilityMetrics) => string;
}[] = [
  { key: "npv", label: "NPV", format: (m) => formatMoney(m.npv) },
  { key: "irr", label: "IRR", format: (m) => formatPercent(m.irr) },
  { key: "bcr", label: "BCR", format: (m) => formatRatio(m.bcr) },
  { key: "avg_dscr", label: "Avg DSCR", format: (m) => formatRatio(m.avg_dscr) },
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
  const { can, loading: permsLoading } = usePermissions();
  const allowed = can("simulation.view");
  const canManage = can("simulation.manage");
  const queryClient = useQueryClient();

  const [breed, setBreed] = useState(DEFAULT_BREED);
  const [system, setSystem] = useState(DEFAULT_SYSTEM);
  // Auto-load defaults on first mount so the editor isn't empty.
  const [submittedParams, setSubmittedParams] = useState({
    breed: DEFAULT_BREED,
    system: DEFAULT_SYSTEM,
  });
  const [assumptions, setAssumptions] = useState<SimulationAssumptions | null>(null);
  const [loadedScenario, setLoadedScenario] = useState<ScenarioOut | null>(null);
  // Scheduled herd events live outside the reflected sections editor.
  const [events, setEvents] = useState<HerdEventAssumptions[]>([]);
  const [explanation, setExplanation] = useState<MetricExplanation | null>(null);

  const [monteCarlo, setMonteCarlo] = useState(false);
  const [sensitivity, setSensitivity] = useState(false);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [runningScenarioId, setRunningScenarioId] = useState<number | null>(null);

  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [compareIds, setCompareIds] = useState<string | null>(null);

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
    if (defaultsQuery.data?.status === 200) {
      // react-query v5 has no onSuccess: mirror each fetched defaults payload
      // into editable state (one-shot per new payload identity).
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAssumptions(defaultsQuery.data.data);
      setEvents(defaultsQuery.data.data.events ?? []);
      setLoadedScenario(null);
    }
  }, [defaultsQuery.data]);

  const snapshotQuery = useHerdSnapshotApiSimulationHerdSnapshotGet(
    { breed },
    {
      query: { enabled: false },
    },
  );

  const scenariosQuery = useListScenariosApiSimulationScenariosGet({
    query: { enabled: allowed },
  });
  const scenarios =
    scenariosQuery.data?.status === 200 ? scenariosQuery.data.data : [];

  const compareQuery = useCompareScenariosApiSimulationScenariosCompareGet(
    { ids: compareIds ?? "" },
    { query: { enabled: allowed && compareIds !== null } },
  );
  const comparePayload =
    compareQuery.data?.status === 200 ? compareQuery.data.data : undefined;

  const runMutation = useRunAdhocApiSimulationRunPost();
  const runScenarioMutation = useRunScenarioApiSimulationScenariosScenarioIdRunPost();
  const createMutation = useCreateScenarioApiSimulationScenariosPost();
  const updateMutation = useUpdateScenarioApiSimulationScenariosScenarioIdPatch();
  const deleteMutation = useDeleteScenarioApiSimulationScenariosScenarioIdDelete();

  function invalidateScenarios() {
    queryClient.invalidateQueries({
      queryKey: getListScenariosApiSimulationScenariosGetQueryKey(),
    });
  }

  function updateField(section: string, key: string, value: unknown) {
    setAssumptions((prev) => {
      if (!prev) return prev;
      const current = (prev as Record<string, SectionValues>)[section] ?? {};
      return { ...prev, [section]: { ...current, [key]: value } };
    });
  }

  function updateNestedField(section: string, key: string, subKey: string, value: unknown) {
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

  function addEvent() {
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
    setEvents((prev) =>
      prev.map((event, i) => (i === index ? { ...event, ...patch } : event)),
    );
  }

  function removeEvent(index: number) {
    setEvents((prev) => prev.filter((_, i) => i !== index));
  }

  /** Assumptions plus the scheduled events, as sent to run/save endpoints. */
  function assumptionsWithEvents(): SimulationAssumptions | null {
    return assumptions ? { ...assumptions, events } : null;
  }

  async function onUseCurrentHerd() {
    try {
      const res = await snapshotQuery.refetch();
      if (res.data?.status !== 200) return;
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
      toast.success(`Loaded current herd (${snap.total_head} head).`);
    } catch (err) {
      toast.error(errorMessage(err, "Could not load the herd snapshot."));
    }
  }

  async function onRun() {
    const payload = assumptionsWithEvents();
    if (!payload) return;
    if (eventErrors.length > 0) return; // inline messages already shown
    setRunError(null);
    try {
      const res = await runMutation.mutateAsync({
        data: { assumptions: payload, monte_carlo: monteCarlo, sensitivity },
      });
      if (res.status === 200) setResult(res.data);
    } catch (err) {
      const message = errorMessage(err, "Simulation failed");
      setRunError(message);
      toast.error(message);
    }
  }

  async function onRunScenario(scenario: ScenarioOut) {
    setRunError(null);
    setRunningScenarioId(scenario.id);
    try {
      const res = await runScenarioMutation.mutateAsync({
        scenarioId: scenario.id,
        params: { monte_carlo: monteCarlo, sensitivity },
      });
      if (res.status === 200) setResult(res.data);
    } catch (err) {
      const message = errorMessage(err, "Scenario run failed");
      setRunError(message);
      toast.error(message);
    } finally {
      setRunningScenarioId(null);
    }
  }

  async function onDeleteScenario(scenario: ScenarioOut) {
    if (!window.confirm(`Delete scenario "${scenario.name}"?`)) return;
    try {
      await deleteMutation.mutateAsync({ scenarioId: scenario.id });
      toast.success("Scenario deleted.");
      if (loadedScenario?.id === scenario.id) setLoadedScenario(null);
      setSelectedIds((prev) => prev.filter((id) => id !== scenario.id));
      invalidateScenarios();
    } catch (err) {
      toast.error(errorMessage(err, "Could not delete the scenario."));
    }
  }

  function onCompare() {
    const ids = selectedIds.join(",");
    if (ids === compareIds) {
      void compareQuery.refetch();
    } else {
      setCompareIds(ids);
    }
  }

  async function onSaveScenario() {
    const payload = assumptionsWithEvents();
    if (!payload || !saveName.trim()) return;
    if (eventErrors.length > 0) return;
    setSaveError(null);
    try {
      await createMutation.mutateAsync({
        data: {
          name: saveName.trim(),
          notes: saveNotes.trim(),
          assumptions: payload,
        },
      });
      toast.success("Scenario saved.");
      invalidateScenarios();
      setSaveOpen(false);
      setSaveName("");
      setSaveNotes("");
    } catch (err) {
      const message = errorMessage(err, "Could not save the scenario.");
      setSaveError(message);
      toast.error(message);
    }
  }

  async function onUpdateScenario() {
    const payload = assumptionsWithEvents();
    if (!payload || !loadedScenario) return;
    if (eventErrors.length > 0) return;
    try {
      await updateMutation.mutateAsync({
        scenarioId: loadedScenario.id,
        data: { assumptions: payload },
      });
      toast.success("Scenario updated.");
      invalidateScenarios();
    } catch (err) {
      toast.error(errorMessage(err, "Could not update the scenario."));
    }
  }

  /** One editor row; the input type follows the value type. */
  function renderField(section: string, key: string, value: unknown) {
    const id = `sim-${section}-${key}`;
    if (section === "herd" && key === "foundation_flock_state") {
      return (
        <div key={id} className="space-y-1.5">
          <Label htmlFor={id}>Foundation Flock State</Label>
          <Select
            value={String(value)}
            onValueChange={(v) => updateField(section, key, v)}
          >
            <SelectTrigger id={id} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="open">open</SelectItem>
              <SelectItem value="mixed">mixed</SelectItem>
            </SelectContent>
          </Select>
        </div>
      );
    }
    if (typeof value === "number") {
      return (
        <div key={id} className="space-y-1.5">
          <Label htmlFor={id}>{humanize(key)}</Label>
          <NumberInput
            id={id}
            value={value}
            onCommit={(n) => updateField(section, key, n)}
          />
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
      return (
        <div key={id} className="space-y-1.5">
          <Label htmlFor={id}>{humanize(key)}</Label>
          <Input
            id={id}
            type={isMonth ? "month" : "text"}
            value={value}
            onChange={(e) => updateField(section, key, e.target.value)}
          />
        </div>
      );
    }
    if (Array.isArray(value)) {
      return (
        <div key={id} className="space-y-1.5 sm:col-span-2 lg:col-span-3">
          <Label htmlFor={id}>{humanize(key)} (comma-separated)</Label>
          <Input
            id={id}
            type="text"
            value={value.join(", ")}
            onChange={(e) =>
              updateField(
                section,
                key,
                e.target.value
                  .split(",")
                  .map((token) => Number(token.trim()))
                  .filter((n) => !Number.isNaN(n)),
              )
            }
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
      return (
        <div key={id} className="space-y-1.5">
          <Label htmlFor={id}>{humanize(subKey)}</Label>
          <NumberInput
            id={id}
            value={value}
            onCommit={(n) => updateNestedField(section, key, subKey, n)}
          />
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
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <MetricCard
            value={formatMoney(m.npv)}
            label="NPV"
            icon={IndianRupee}
            tint="emerald"
            onInfo={infoFor("npv")}
          />
          <MetricCard
            value={formatPercent(m.irr)}
            label="IRR"
            icon={Percent}
            tint="emerald"
            onInfo={infoFor("irr")}
          />
          <MetricCard
            value={formatRatio(m.bcr)}
            label="BCR"
            icon={Scale}
            onInfo={infoFor("bcr")}
          />
          <MetricCard
            value={formatRatio(m.avg_dscr)}
            label="Avg DSCR"
            icon={Gauge}
            onInfo={infoFor("avg_dscr")}
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

        <DataTableCard
          title="Annual P&amp;L"
          description="Yearly revenue, operating costs and cash flow."
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Year</TableHead>
                <TableHead>Revenue</TableHead>
                <TableHead>Opex</TableHead>
                <TableHead>EBITDA</TableHead>
                <TableHead>Debt service</TableHead>
                <TableHead>Net cash flow</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {r.annual_pl.map((row) => (
                <TableRow key={row.year}>
                  <TableCell>{row.year}</TableCell>
                  <TableCell>{formatMoney(row.total_revenue)}</TableCell>
                  <TableCell>{formatMoney(row.total_opex)}</TableCell>
                  <TableCell>{formatMoney(row.ebitda)}</TableCell>
                  <TableCell>{formatMoney(row.debt_service)}</TableCell>
                  <TableCell
                    className={row.net_cash_flow < 0 ? "text-destructive" : undefined}
                  >
                    {formatMoney(row.net_cash_flow)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
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
                  <TableHead>Sales revenue</TableHead>
                  <TableHead>Feed cost</TableHead>
                  <TableHead>Debt service</TableHead>
                  <TableHead>Net cash flow</TableHead>
                  <TableHead>Cumulative cash flow</TableHead>
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
                    <TableCell>{row.total_herd}</TableCell>
                    <TableCell>{row.births}</TableCell>
                    <TableCell>{row.deaths}</TableCell>
                    <TableCell>{row.sales_head}</TableCell>
                    <TableCell>{formatMoney(row.sales_revenue)}</TableCell>
                    <TableCell>{formatMoney(row.feed_cost)}</TableCell>
                    <TableCell>{formatMoney(row.debt_service)}</TableCell>
                    <TableCell
                      className={row.net_cash_flow < 0 ? "text-destructive" : undefined}
                    >
                      {formatMoney(row.net_cash_flow)}
                    </TableCell>
                    <TableCell
                      className={
                        row.cumulative_cash_flow < 0 ? "text-destructive" : undefined
                      }
                    >
                      {formatMoney(row.cumulative_cash_flow)}
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
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <MetricCard
                  value={formatMoney(r.monte_carlo.npv_mean)}
                  label="NPV mean"
                  icon={IndianRupee}
                  tint="emerald"
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
                  tint="red"
                />
              </div>
              <MonteCarloHistogram
                counts={r.monte_carlo.npv_histogram_counts}
                edges={r.monte_carlo.npv_histogram_edges}
              />
            </CardContent>
          </Card>
        )}

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
              onClick={onCompare}
              disabled={selectedIds.length < 2 || compareQuery.isFetching}
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
                  disabled={!assumptions}
                >
                  <Save />
                  Save as scenario
                </Button>
                {loadedScenario && (
                  <Button
                    variant="outline"
                    onClick={() => void onUpdateScenario()}
                    disabled={!assumptions || updateMutation.isPending}
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
              disabled={!assumptions || runMutation.isPending}
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
              <Select value={breed} onValueChange={setBreed}>
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
              <Select value={system} onValueChange={setSystem}>
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
                if (breed === submittedParams.breed && system === submittedParams.system) {
                  void defaultsQuery.refetch();
                } else {
                  setSubmittedParams({ breed, system });
                }
              }}
              disabled={defaultsQuery.isFetching}
            >
              {defaultsQuery.isFetching ? "Loading…" : "Load defaults"}
            </Button>
            <Button
              variant="outline"
              onClick={() => void onUseCurrentHerd()}
              disabled={!assumptions || snapshotQuery.isFetching}
            >
              {snapshotQuery.isFetching ? "Loading…" : "Use current herd"}
            </Button>
          </div>
          {defaultsQuery.isError && (
            <p className="text-sm text-destructive">
              {defaultsQuery.error instanceof ApiError
                ? defaultsQuery.error.detail
                : "Could not load the defaults."}
            </p>
          )}
        </CardContent>
      </Card>

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
                  onClick={() =>
                    updateField("meta", "horizon_months", preset.months)
                  }
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
                key={section}
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
        </CardContent>
      </Card>

      <DataTableCard
        title="Herd events"
        description="Purchases or sales that fire at a given simulation month."
        actions={
          <Button variant="outline" size="sm" onClick={addEvent}>
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
              {events.map((event, index) => (
                <TableRow key={index}>
                  <TableCell>
                    <NumberInput
                      aria-label="Month"
                      min={1}
                      max={horizonMonths}
                      className="w-20"
                      value={event.month}
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
                      aria-label="Count"
                      min={1}
                      className="w-20"
                      value={event.count}
                      onCommit={(n) => updateEvent(index, { count: n })}
                    />
                  </TableCell>
                  <TableCell>
                    <NumberInput
                      aria-label="Price per head"
                      min={0}
                      placeholder="auto"
                      className="w-24"
                      nullable
                      value={event.price_per_head ?? null}
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
              ))}
            </TableBody>
          </Table>
        )}
        {eventErrors.map((error) => (
          <p key={error} className="text-sm text-destructive">
            {error}
          </p>
        ))}
      </DataTableCard>

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
        </div>
        {loadedScenario && (
          <p className="text-sm text-muted-foreground">
            Editing scenario: {loadedScenario.name}
          </p>
        )}
        {runError && <p className="text-sm text-destructive">{runError}</p>}
      </section>

      {result && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">Results</h2>
          {renderResults(result)}
        </section>
      )}

      <DataTableCard
        title="Scenarios"
        description="Saved assumption sets to load, run, update or compare."
        contentClassName="space-y-4"
      >
        {scenarios.length === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title="No saved scenarios yet."
            description="Save the current assumptions as a scenario to rerun or compare later."
          />
        ) : (
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
                      checked={selectedIds.includes(scenario.id)}
                      onCheckedChange={(checked) =>
                        setSelectedIds((prev) =>
                          checked === true
                            ? [...prev, scenario.id]
                            : prev.filter((id) => id !== scenario.id),
                        )
                      }
                    />
                  </TableCell>
                  <TableCell className="font-medium">{scenario.name}</TableCell>
                  <TableCell>{scenario.notes}</TableCell>
                  <TableCell>{formatDate(scenario.updated_at)}</TableCell>
                  <TableCell>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setAssumptions(scenario.assumptions);
                          setEvents(scenario.assumptions.events ?? []);
                          setLoadedScenario(scenario);
                        }}
                      >
                        Load
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void onRunScenario(scenario)}
                        disabled={runningScenarioId !== null}
                      >
                        {runningScenarioId === scenario.id ? "Running…" : "Run"}
                      </Button>
                      {canManage && (
                        <Button
                          variant="destructive"
                          size="sm"
                          disabled={deleteMutation.isPending}
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

      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Save as scenario</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {saveError && <p className="text-sm text-destructive">{saveError}</p>}
            <div className="space-y-1.5">
              <Label htmlFor="scenario-name">Name *</Label>
              <Input
                id="scenario-name"
                maxLength={120}
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="scenario-notes">Notes</Label>
              <Input
                id="scenario-notes"
                maxLength={2000}
                value={saveNotes}
                onChange={(e) => setSaveNotes(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button
                onClick={() => void onSaveScenario()}
                disabled={!saveName.trim() || createMutation.isPending}
              >
                {createMutation.isPending ? "Saving…" : "Save scenario"}
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
