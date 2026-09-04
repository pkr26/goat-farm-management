"use client";

/**
 * Ops Simulation (Business): the farm, day by day. The monthly Simulation
 * answers money questions in expected values; this page replays the farm's
 * operational reality one day at a time — one building per bucket, each with
 * its own vet area. Feed is mixed at the store and delivered per building in
 * the 40/20/40 shift split, pens are cleaned morning and night with
 * verification, the vet's due-duties round follows the protocol calendar,
 * and every animal's move between buildings is shown with the workflow
 * context that caused it. A deterministic seed makes a run replayable, and
 * the opt-in Markdown ledger exports the whole audit trail for manual
 * verification. Goat farms only in this version.
 */

import { useMemo, useState, type ComponentProps } from "react";
import {
  Baby,
  Banknote,
  Beef,
  CalendarClock,
  CalendarDays,
  ClipboardList,
  Download,
  Dumbbell,
  FlaskConical,
  Play,
  Plus,
  Wheat,
} from "lucide-react";
import { toast } from "sonner";

import { useRunDailyOpsSimulationApiOpsSimRunPost } from "@/api/generated/endpoints";
import type { DailyOpsResult, DayRecord } from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PageSkeleton } from "@/components/skeletons";
import { PermissionsError } from "@/components/permissions-error";
import { StatCard } from "@/components/stat-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
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
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { useFarmType } from "@/hooks/use-farm-type";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";

const BUCKETS = [
  "QUARANTINE",
  "FOUNDATION",
  "BREEDING",
  "PREGNANCY_EARLY",
  "PREGNANCY_LATE",
  "DELIVERY",
  "RECOVERY",
  "RESTING",
  "MALE_KIDS",
  "FEMALE_KIDS",
] as const;

type BucketCode = (typeof BUCKETS)[number];

/** One building per bucket, labelled with the seeded goat names. */
const BUILDING_NAMES: Record<string, string> = {
  QUARANTINE: "Quarantine Ward",
  FOUNDATION: "Foundation / Grow-out",
  BREEDING: "Breeding Bucket",
  PREGNANCY_EARLY: "Pregnancy A",
  PREGNANCY_LATE: "Pregnancy B",
  DELIVERY: "Delivery Ward",
  RECOVERY: "Recovery Ward",
  RESTING: "Resting / Dry-off + Flush",
  MALE_KIDS: "Male Kids Growing",
  FEMALE_KIDS: "Female Kids Growing",
  FEED_STORE: "Feed Store & Mixing Area",
};

function buildingName(building: string): string {
  return BUILDING_NAMES[building] ?? building;
}

const MIN_HORIZON_DAYS = 7;
const MAX_HORIZON_DAYS = 365;
const MAX_START_HEAD = 500;

type HerdRow = {
  key: string;
  tag: string;
  sex: "F" | "M";
  bucket: BucketCode;
  ageMonths: number;
  daysInBucket: number;
  /** Gestation days on day 1 for does; blank = not bred. */
  bredDaysAgo: string;
  dependentKid: boolean;
};

let herdRowSeq = 0;
function nextRowKey(): string {
  herdRowSeq += 1;
  return `row-${herdRowSeq}`;
}

function makeRow(partial: Partial<HerdRow> = {}): HerdRow {
  return {
    key: nextRowKey(),
    tag: "",
    sex: "F",
    bucket: "BREEDING",
    ageMonths: 18,
    daysInBucket: 0,
    bredDaysAgo: "",
    dependentKid: false,
    ...partial,
  };
}

/** Presets mirror the engine tests' toy herds so a first run is instantly
 * recognisable against the hand-derived golden traces. */
const PRESETS: { label: string; rows: () => HerdRow[] }[] = [
  {
    label: "Toy herd (10 does + 1 buck)",
    rows: () => [
      ...Array.from({ length: 10 }, (_, i) =>
        makeRow({ tag: `D${i + 1}`, sex: "F", bucket: "BREEDING", ageMonths: 12 + i + 1 }),
      ),
      makeRow({ tag: "B1", sex: "M", bucket: "BREEDING", ageMonths: 24 }),
    ],
  },
  {
    label: "Mixed herd (pregnancy + quarantine + kids)",
    rows: () => [
      ...Array.from({ length: 6 }, (_, i) =>
        makeRow({ tag: `D${i + 1}`, sex: "F", bucket: "BREEDING", ageMonths: 14 + i }),
      ),
      makeRow({ tag: "B1", sex: "M", bucket: "BREEDING", ageMonths: 24 }),
      makeRow({
        tag: "P1",
        sex: "F",
        bucket: "PREGNANCY_LATE",
        ageMonths: 30,
        bredDaysAgo: "120",
        daysInBucket: 20,
      }),
      makeRow({ tag: "Q1", sex: "F", bucket: "QUARANTINE", ageMonths: 14, daysInBucket: 10 }),
      makeRow({ tag: "MK1", sex: "M", bucket: "MALE_KIDS", ageMonths: 7 }),
      makeRow({ tag: "FK1", sex: "F", bucket: "FEMALE_KIDS", ageMonths: 9 }),
    ],
  },
];

function todayIsoDate(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
    now.getDate(),
  ).padStart(2, "0")}`;
}

function formatKg(value: number): string {
  return Number.isFinite(value) ? value.toFixed(value % 1 === 0 ? 0 : 1) : "—";
}

/** Minimal numeric input (planner pattern): commits only finite numbers. */
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

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail;
  return fallback;
}

/** Day chips show what kind of day it was at a glance. */
function dayBadges(day: DayRecord): string {
  const parts: string[] = [];
  if ((day.moves?.length ?? 0) > 0) parts.push(`${day.moves?.length} moves`);
  if ((day.births?.length ?? 0) > 0) parts.push(`${day.births?.length} births`);
  if ((day.exits?.length ?? 0) > 0) parts.push(`${day.exits?.length} exits`);
  return parts.join(" · ");
}

export default function OpsSimulationPage() {
  const { can, loading: permsLoading, isError: permsError, refetch: permsRefetch } =
    usePermissions();
  const allowed = can("simulation.view");

  const farmType = useFarmType();
  const vocabulary = farmVocabulary(farmType);
  const isDairyFarm = farmType === "BUFFALO_DAIRY";

  const runAction = useSingleFlight();
  const runMutation = useRunDailyOpsSimulationApiOpsSimRunPost();

  // ----- Run inputs
  const [startDate, setStartDate] = useState(todayIsoDate);
  const [horizonDays, setHorizonDays] = useState(90);
  const [seed, setSeed] = useState(2026);
  const [includeLedger, setIncludeLedger] = useState(false);
  const [rows, setRows] = useState<HerdRow[]>(() => PRESETS[0].rows());

  // ----- Results
  const [result, setResult] = useState<DailyOpsResult | null>(null);
  const [ledger, setLedger] = useState<string | null>(null);
  const [selectedDay, setSelectedDay] = useState(1);

  const rowErrors = useMemo(() => {
    const errors: string[] = [];
    const tags = rows.map((row) => row.tag.trim());
    if (tags.some((tag) => tag === "")) errors.push("Every animal needs a tag.");
    const duplicates = tags.filter((tag, index) => tag !== "" && tags.indexOf(tag) !== index);
    if (duplicates.length > 0) errors.push(`Duplicate tags: ${[...new Set(duplicates)].join(", ")}`);
    if (rows.length === 0) errors.push("Add at least one animal.");
    if (rows.length > MAX_START_HEAD) errors.push(`At most ${MAX_START_HEAD} head per run.`);
    return errors;
  }, [rows]);

  function updateRow(key: string, patch: Partial<HerdRow>) {
    setRows((current) =>
      current.map((row) => (row.key === key ? { ...row, ...patch } : row)),
    );
  }

  async function onRun() {
    if (rowErrors.length > 0) {
      toast.error(rowErrors[0]);
      return;
    }
    if (!Number.isInteger(horizonDays) || horizonDays < MIN_HORIZON_DAYS || horizonDays > MAX_HORIZON_DAYS) {
      toast.error(`Horizon must be ${MIN_HORIZON_DAYS}–${MAX_HORIZON_DAYS} days.`);
      return;
    }
    await runAction.run(async () => {
      try {
        const response = await runMutation.mutateAsync({
          data: {
            start_date: startDate,
            horizon_days: horizonDays,
            seed,
            include_ledger: includeLedger,
            animals: rows.map((row) => {
              const bred = row.bredDaysAgo.trim() === "" ? null : Number(row.bredDaysAgo);
              return {
                tag: row.tag.trim(),
                sex: row.sex,
                bucket: row.bucket,
                age_months: Math.max(0, Math.round(row.ageMonths)),
                days_in_bucket: Math.max(0, Math.round(row.daysInBucket)),
                ...(bred !== null && Number.isFinite(bred) ? { bred_days_ago: Math.round(bred) } : {}),
                dependent_kid: row.dependentKid,
              };
            }),
          },
        });
        if (response.status !== 200) throw new Error("Unexpected response");
        setResult(response.data.result);
        setLedger(response.data.ledger ?? null);
        setSelectedDay(1);
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch (err) {
        toast.error(errorMessage(err, "The simulation could not run."));
      }
    });
  }

  function downloadLedger() {
    if (!ledger) return;
    const blob = new Blob([ledger], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `ops-simulation-${result?.start_date ?? "run"}-seed-${result?.seed ?? 0}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }

  if (permsLoading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Ops Simulation"
          description="The farm day by day: buildings, duties, feed and every bucket move."
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
  if (isDairyFarm) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Ops Simulation"
          description="The farm day by day: buildings, duties, feed and every bucket move."
        />
        <EmptyState
          icon={CalendarClock}
          title="Goat farms only — for now"
          description="The daily operations simulation models the goat lifecycle: quarantine protocol, breeding to weaning, and the meat-kid sale window. Dairy milking duties arrive with the buffalo version."
        />
      </div>
    );
  }

  const selectedRecord = result ? (result.days[selectedDay - 1] ?? null) : null;
  const finalHead = result
    ? (result.days[result.days.length - 1]?.occupancy ?? []).reduce((sum, row) => sum + row.heads, 0)
    : 0;
  const totalFeedKg = result
    ? Object.values(result.totals.feed_kg_by_recipe ?? {}).reduce((sum, kg) => sum + kg, 0)
    : 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Ops Simulation"
        description={`Replay the farm one day at a time — one building per bucket, feed mixed and delivered in three shifts, pens cleaned twice daily, and every ${vocabulary.young} move verified against the legal lifecycle.`}
      />

      {result && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label="Herd (final day)"
            value={`${finalHead} head`}
            icon={Beef}
            hint={`from ${result.head_start} at the start`}
          />
          <StatCard
            label="Services → conceptions"
            value={`${result.totals.services} → ${result.totals.conceptions}`}
            icon={Baby}
            hint={`${result.totals.kids_born_alive} kids born alive`}
          />
          <StatCard
            label="Bucket moves"
            value={String(result.totals.moves)}
            icon={Dumbbell}
            hint={`${result.totals.deaths} died · ${result.totals.culls} culled · ${result.totals.sales} sold`}
          />
          <StatCard
            label="Feed delivered"
            value={`${formatKg(totalFeedKg)} kg`}
            icon={Wheat}
            hint={`${result.days.length} days · ${Object.keys(result.totals.feed_kg_by_recipe ?? {}).length} recipes`}
          />
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Run setup</CardTitle>
          <CardDescription>
            Where does the herd stand on day 1? Each row is one animal in one
            bucket (building). The same input and seed always replay the same
            farm.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <div className="space-y-2">
              <Label htmlFor="ops-sim-start">Start date</Label>
              <Input
                id="ops-sim-start"
                type="date"
                value={startDate}
                onChange={(event) => setStartDate(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="ops-sim-horizon">Horizon (days)</Label>
              <NumberField
                id="ops-sim-horizon"
                value={horizonDays}
                min={MIN_HORIZON_DAYS}
                max={MAX_HORIZON_DAYS}
                onCommitNumber={(n) => setHorizonDays(Math.round(n))}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="ops-sim-seed">Seed</Label>
              <NumberField
                id="ops-sim-seed"
                value={seed}
                onCommitNumber={(n) => setSeed(Math.round(n))}
              />
            </div>
            <div className="space-y-2">
              <Button
                id="ops-sim-ledger-toggle"
                variant={includeLedger ? "default" : "outline"}
                size="sm"
                aria-pressed={includeLedger}
                onClick={() => setIncludeLedger((value) => !value)}
              >
                <ClipboardList />
                Include Markdown ledger
              </Button>
              <p className="text-xs text-muted-foreground">
                A day-by-day audit document you can download and verify by hand.
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {PRESETS.map((preset) => (
              <Button
                key={preset.label}
                variant="outline"
                size="sm"
                onClick={() => setRows(preset.rows())}
              >
                <CalendarDays />
                {preset.label}
              </Button>
            ))}
            <Button variant="outline" size="sm" onClick={() => setRows((r) => [...r, makeRow()])}>
              <Plus />
              Add animal
            </Button>
          </div>

          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-28">Tag</TableHead>
                  <TableHead className="w-20">Sex</TableHead>
                  <TableHead className="w-48">Bucket (building)</TableHead>
                  <TableHead className="w-24">Age (mo)</TableHead>
                  <TableHead className="w-28">Days in bucket</TableHead>
                  <TableHead className="w-32">Bred days ago</TableHead>
                  <TableHead className="w-24">Creep kid</TableHead>
                  <TableHead className="w-16" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.key}>
                    <TableCell>
                      <Input
                        aria-label={`Tag for row ${row.key}`}
                        value={row.tag}
                        onChange={(event) => updateRow(row.key, { tag: event.target.value })}
                        className="w-28"
                        maxLength={50}
                      />
                    </TableCell>
                    <TableCell>
                      <Select
                        value={row.sex}
                        onValueChange={(value) =>
                          updateRow(row.key, { sex: value === "M" ? "M" : "F" })
                        }
                      >
                        <SelectTrigger aria-label={`Sex for ${row.tag || row.key}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="F">F</SelectItem>
                          <SelectItem value="M">M</SelectItem>
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell>
                      <Select
                        value={row.bucket}
                        onValueChange={(value) =>
                          updateRow(row.key, { bucket: value as BucketCode })
                        }
                      >
                        <SelectTrigger aria-label={`Bucket for ${row.tag || row.key}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {BUCKETS.map((bucket) => (
                            <SelectItem key={bucket} value={bucket}>
                              {buildingName(bucket)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell>
                      <NumberField
                        aria-label={`Age months for ${row.tag || row.key}`}
                        value={row.ageMonths}
                        min={0}
                        max={240}
                        onCommitNumber={(n) => updateRow(row.key, { ageMonths: n })}
                        className="w-24"
                      />
                    </TableCell>
                    <TableCell>
                      <NumberField
                        aria-label={`Days in bucket for ${row.tag || row.key}`}
                        value={row.daysInBucket}
                        min={0}
                        onCommitNumber={(n) => updateRow(row.key, { daysInBucket: n })}
                        className="w-28"
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        aria-label={`Bred days ago for ${row.tag || row.key}`}
                        value={row.bredDaysAgo}
                        onChange={(event) => updateRow(row.key, { bredDaysAgo: event.target.value })}
                        placeholder="—"
                        className="w-32"
                      />
                    </TableCell>
                    <TableCell>
                      <Checkbox
                        checked={row.dependentKid}
                        onCheckedChange={(checked) =>
                          updateRow(row.key, { dependentKid: checked === true })
                        }
                        aria-label={`Unweaned kid with dam for ${row.tag || row.key}`}
                      />
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Remove ${row.tag || "row"}`}
                        onClick={() => setRows((r) => r.filter((item) => item.key !== row.key))}
                      >
                        ×
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {rowErrors.length > 0 && (
            <p className="text-sm text-destructive">{rowErrors[0]}</p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => void onRun()} disabled={runMutation.isPending || runAction.pending}>
              <Play />
              {runMutation.isPending ? "Simulating…" : `Run ${horizonDays} days`}
            </Button>
            {ledger && (
              <Button variant="outline" onClick={downloadLedger}>
                <Download />
                Download ledger
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {!result && (
        <EmptyState
          icon={FlaskConical}
          title="No run yet"
          description="Pick a preset herd or enter your own, then run the simulation to see the farm's daily schedule, feeding manifest and every bucket move."
        />
      )}

      {result && selectedRecord && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Day timeline</CardTitle>
              <CardDescription>
                Pick a day. The schedule below is exactly what the crews do that
                day, in order: morning feed and cleaning, the 09:00 duties
                round, lifecycle events, then the afternoon and night rounds.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto rounded-lg border p-2">
                {result.days.map((day) => {
                  const badges = dayBadges(day);
                  return (
                    <Button
                      key={day.day}
                      variant={day.day === selectedDay ? "default" : "outline"}
                      size="sm"
                      className="h-8 px-2 text-xs"
                      onClick={() => setSelectedDay(day.day)}
                      aria-label={`Go to day ${day.day}${badges ? ` — ${badges}` : " — routine"}`}
                      title={badges || `Day ${day.day} — routine only`}
                    >
                      {day.day}
                      {badges ? " •" : ""}
                    </Button>
                  );
                })}
              </div>

              <h3 className="text-sm font-semibold">
                Day {selectedRecord.day} — {selectedRecord.date}
                {(() => {
                  const badges = dayBadges(selectedRecord);
                  return badges ? ` (${badges})` : " (routine day)";
                })()}
              </h3>

              <div className="grid gap-4 lg:grid-cols-2">
                <div className="overflow-x-auto rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-16">Time</TableHead>
                        <TableHead>Duty</TableHead>
                        <TableHead className="w-40">Building</TableHead>
                        <TableHead className="w-20">Crew</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {selectedRecord.tasks?.map((task, index) => (
                        <TableRow key={index}>
                          <TableCell className="whitespace-nowrap tabular-nums">{task.time}</TableCell>
                          <TableCell>
                            <span className="font-medium">{task.headline}</span>
                            {task.detail ? (
                              <span className="block text-xs text-muted-foreground">
                                {task.detail}
                              </span>
                            ) : null}
                          </TableCell>
                          <TableCell>{buildingName(task.building)}</TableCell>
                          <TableCell>{task.role}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>

                <div className="space-y-4">
                  <div className="overflow-x-auto rounded-lg border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Feeding manifest</TableHead>
                          <TableHead className="text-right">Heads</TableHead>
                          <TableHead className="text-right">kg/head</TableHead>
                          <TableHead className="text-right">AM</TableHead>
                          <TableHead className="text-right">Noon</TableHead>
                          <TableHead className="text-right">PM</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {selectedRecord.feeding?.map((line, index) => (
                          <TableRow key={index}>
                            <TableCell>
                              {buildingName(line.building)} — {line.recipe_display}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">{line.heads}</TableCell>
                            <TableCell className="text-right tabular-nums">
                              {line.kg_per_head}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatKg(line.morning_kg)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatKg(line.afternoon_kg)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatKg(line.night_kg)}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>

                  <div className="overflow-x-auto rounded-lg border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>End-of-day occupancy</TableHead>
                          <TableHead className="text-right">Head</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {selectedRecord.occupancy?.map((row) => (
                          <TableRow key={row.building}>
                            <TableCell>{buildingName(row.building)}</TableCell>
                            <TableCell className="text-right tabular-nums">{row.heads}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </div>
              </div>

              {(selectedRecord.moves?.length ?? 0) > 0 && (
                <div className="overflow-x-auto rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Animal</TableHead>
                        <TableHead>Move</TableHead>
                        <TableHead className="w-28">Context</TableHead>
                        <TableHead>Reason</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {selectedRecord.moves?.map((move, index) => (
                        <TableRow key={index}>
                          <TableCell className="font-medium">{move.tag}</TableCell>
                          <TableCell>
                            {buildingName(move.from_bucket ?? "")} → {buildingName(move.to_bucket)}
                          </TableCell>
                          <TableCell>{move.context}</TableCell>
                          <TableCell className="text-muted-foreground">{move.reason}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}

              {((selectedRecord.births?.length ?? 0) > 0 ||
                (selectedRecord.exits?.length ?? 0) > 0) && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {selectedRecord.births?.map((birth) => (
                    <p key={birth.dam_tag} className="text-sm">
                      <Baby className="mr-1 inline size-4 text-primary" />
                      <span className="font-medium">{birth.dam_tag}</span> kidded{" "}
                      {birth.live_kids} live of {birth.kids.length}:{" "}
                      {birth.kids
                        .map((kid) => `${kid.tag} (${kid.sex}${kid.status === "ALIVE" ? "" : `, ${kid.status}`})`)
                        .join(", ")}
                    </p>
                  ))}
                  {selectedRecord.exits?.map((exit) => (
                    <p key={exit.tag} className="text-sm">
                      <Banknote className="mr-1 inline size-4 text-muted-foreground" />
                      <span className="font-medium">{exit.tag}</span> — {exit.kind}: {exit.reason}
                    </p>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <DataTableCard
            title="Transition matrix"
            description="Every simulated move, validated against the same legal bucket graph the live app enforces."
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>From</TableHead>
                  <TableHead>To</TableHead>
                  <TableHead className="w-32">Context</TableHead>
                  <TableHead className="text-right">Count</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {result.transition_counts.map((row) => (
                  <TableRow key={`${row.from_bucket}-${row.to_bucket}-${row.context}`}>
                    <TableCell>{buildingName(row.from_bucket)}</TableCell>
                    <TableCell>{buildingName(row.to_bucket)}</TableCell>
                    <TableCell>{row.context}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </DataTableCard>

          <DataTableCard
            title="Animal journeys"
            description="One row per animal: where it started, every hop between buildings, and how it ended."
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-24">Tag</TableHead>
                  <TableHead className="w-14">Sex</TableHead>
                  <TableHead className="w-20">Born</TableHead>
                  <TableHead>Hops</TableHead>
                  <TableHead className="w-56">Final</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {result.journeys.map((journey) => (
                  <TableRow key={journey.tag}>
                    <TableCell className="font-medium">{journey.tag}</TableCell>
                    <TableCell>{journey.sex}</TableCell>
                    <TableCell>{journey.born_day ?? "start"}</TableCell>
                    <TableCell>
                      {(journey.hops ?? []).length === 0
                        ? "—"
                        : (journey.hops ?? [])
                            .map(
                              (hop) =>
                                `d${hop.day} ${buildingName(hop.from_bucket ?? "")}→${buildingName(
                                  hop.to_bucket,
                                )} (${hop.context})`,
                            )
                            .join(" → ")}
                    </TableCell>
                    <TableCell>
                      {journey.final_bucket ? (
                        `${buildingName(journey.final_bucket)} (active)`
                      ) : (
                        <span>
                          {journey.exit_kind} on day {journey.exit_day} — {journey.exit_reason}
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </DataTableCard>

          <Card>
            <CardHeader>
              <CardTitle>How this run works</CardTitle>
              <CardDescription>
                Every rule is echoed with the exact numbers it used — verify any
                day against the ledger.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <ol className="list-decimal space-y-3 pl-5 text-sm">
                {result.explanations.map((explanation) => (
                  <li key={explanation.key}>
                    <p className="font-medium">{explanation.title}</p>
                    <p className="text-muted-foreground">{explanation.explanation}</p>
                  </li>
                ))}
              </ol>
              <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                {result.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
              {ledger && (
                <div>
                  <Button variant="outline" size="sm" onClick={downloadLedger}>
                    <Download />
                    Download the day-by-day ledger
                  </Button>
                  <details className="mt-2">
                    <summary className="cursor-pointer text-sm text-muted-foreground">
                      Preview the ledger (Markdown)
                    </summary>
                    <pre className="mt-2 max-h-96 overflow-auto rounded-lg border bg-muted/40 p-3 text-xs">
                      {ledger}
                    </pre>
                  </details>
                </div>
              )}
            </CardContent>
          </Card>

          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <ClipboardList className="size-4" />
            Model {result.model_version} · seed {result.seed} ·{" "}
            {result.days.length} days · {result.head_start} head at the start.
          </p>
        </>
      )}
    </div>
  );
}
