"use client";

/**
 * Simulation: bio-economic herd projection. Generic assumptions editor driven
 * by the backend defaults payload (rendered from whatever keys are present),
 * ad-hoc/scenario runs, viability results and saved-scenario management.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
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
  ScenarioOut,
  SimulationAssumptions,
  SimulationResult,
  ViabilityMetrics,
} from "@/api/generated/models";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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

/** snake_case → Title Case ("horizon_months" → "Horizon Months"). */
function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

/** Ratio with a fixed precision; non-finite (e.g. BCR = inf) → "—". */
function formatRatio(value: number, digits = 2): string {
  return Number.isFinite(value) ? value.toFixed(digits) : "—";
}

/** IRR is a fraction (0.18 → "18.0%"); null → "—". */
function formatPercent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function StatCard({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-xl bg-card p-4 ring-1 ring-foreground/10">
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-sm text-muted-foreground">{label}</div>
    </div>
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
      setLoadedScenario(null);
    }
  }, [defaultsQuery.data]);

  const snapshotQuery = useHerdSnapshotApiSimulationHerdSnapshotGet({
    query: { enabled: false },
  });

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
    if (!assumptions) return;
    setRunError(null);
    try {
      const res = await runMutation.mutateAsync({
        data: { assumptions, monte_carlo: monteCarlo, sensitivity },
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
    if (!assumptions || !saveName.trim()) return;
    setSaveError(null);
    try {
      await createMutation.mutateAsync({
        data: { name: saveName.trim(), notes: saveNotes.trim(), assumptions },
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
    if (!assumptions || !loadedScenario) return;
    try {
      await updateMutation.mutateAsync({
        scenarioId: loadedScenario.id,
        data: { assumptions },
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
          <Input
            id={id}
            type="number"
            step="any"
            value={value}
            onChange={(e) => {
              const n = Number(e.target.value);
              updateField(section, key, Number.isNaN(n) ? 0 : n);
            }}
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
          <Input
            id={id}
            type="number"
            step="any"
            value={value}
            onChange={(e) => {
              const n = Number(e.target.value);
              updateNestedField(section, key, subKey, Number.isNaN(n) ? 0 : n);
            }}
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
          <StatCard value={formatMoney(m.npv)} label="NPV" />
          <StatCard value={formatPercent(m.irr)} label="IRR" />
          <StatCard value={formatRatio(m.bcr)} label="BCR" />
          <StatCard value={formatRatio(m.avg_dscr)} label="Avg DSCR" />
          <StatCard
            value={m.payback_month === null ? "—" : String(m.payback_month)}
            label="Payback month"
          />
          <StatCard
            value={
              m.break_even_meat_price_per_kg === null
                ? "—"
                : formatMoney(m.break_even_meat_price_per_kg)
            }
            label="Break-even meat (₹/kg)"
          />
          <StatCard value={formatMoney(m.project_cost)} label="Project cost" />
          <StatCard value={formatMoney(m.loan_amount)} label="Loan" />
          <StatCard value={formatMoney(m.subsidy_amount)} label="Subsidy" />
          <StatCard value={formatMoney(m.equity)} label="Equity" />
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Annual P&amp;L</CardTitle>
          </CardHeader>
          <CardContent>
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
          </CardContent>
        </Card>

        <details className="rounded-xl bg-card ring-1 ring-foreground/10">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
            Monthly projection ({r.months.length} months)
          </summary>
          <div className="max-h-96 overflow-auto border-t">
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
                </TableRow>
              </TableHeader>
              <TableBody>
                {r.months.map((row) => (
                  <TableRow key={row.month}>
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
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </details>

        {r.monte_carlo && (
          <Card>
            <CardHeader>
              <CardTitle>
                Monte Carlo ({r.monte_carlo.runs} runs, seed {r.monte_carlo.seed})
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <StatCard value={formatMoney(r.monte_carlo.npv_mean)} label="NPV mean" />
                <StatCard value={formatMoney(r.monte_carlo.npv_std)} label="NPV std" />
                <StatCard value={formatMoney(r.monte_carlo.npv_p5)} label="P5" />
                <StatCard value={formatMoney(r.monte_carlo.npv_p50)} label="P50" />
                <StatCard value={formatMoney(r.monte_carlo.npv_p95)} label="P95" />
                <StatCard
                  value={`${(r.monte_carlo.prob_npv_negative * 100).toFixed(1)}%`}
                  label="P(NPV < 0)"
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
          <Card>
            <CardHeader>
              <CardTitle>Sensitivity (ΔNPV)</CardTitle>
            </CardHeader>
            <CardContent>
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
            </CardContent>
          </Card>
        )}
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
      <h1 className="text-2xl font-semibold">Simulation</h1>

      <Card>
        <CardHeader>
          <CardTitle>Setup</CardTitle>
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

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Assumptions</h2>
        {!assumptions && !defaultsQuery.isError && (
          <p className="text-muted-foreground">Loading defaults…</p>
        )}
        {assumptions &&
          sectionEntries(assumptions).map(([section, values]) => (
            <details
              key={section}
              open={section === "meta" || section === "herd"}
              className="rounded-xl bg-card ring-1 ring-foreground/10"
            >
              <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
                {humanize(section)}
              </summary>
              <div className="grid gap-3 border-t px-4 py-3 sm:grid-cols-2 lg:grid-cols-3">
                {Object.entries(values).map(([key, value]) =>
                  renderField(section, key, value),
                )}
              </div>
            </details>
          ))}
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-4">
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
          <Button
            onClick={() => void onRun()}
            disabled={!assumptions || runMutation.isPending}
          >
            {runMutation.isPending ? "Running…" : "Run simulation"}
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

      <Card>
        <CardHeader>
          <CardTitle>Scenarios</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {scenarios.length === 0 ? (
            <p className="text-muted-foreground">No saved scenarios yet.</p>
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
          <Button
            variant="outline"
            onClick={onCompare}
            disabled={selectedIds.length < 2 || compareQuery.isFetching}
          >
            {compareQuery.isFetching ? "Comparing…" : "Compare selected"}
          </Button>

          {comparePayload && comparePayload.results.length > 0 && (
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
          )}
        </CardContent>
      </Card>

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
