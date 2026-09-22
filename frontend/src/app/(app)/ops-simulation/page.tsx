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
  ArrowRight,
  Baby,
  Banknote,
  Beef,
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
import { PermissionGate } from "@/components/permission-gate";
import { StatCard } from "@/components/stat-card";
import { farmToday } from "@/lib/format";
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
import { captureFarmScope } from "@/lib/farm-scope-guard";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { useT, type MessageKey, type TFn } from "@/lib/i18n";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { cn } from "@/lib/utils";

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

/** One building per bucket, labelled with the seeded goat names. Mirrors the
 * backend's GOAT_BUILDING_NAMES (models/feed_rules.py); where the API sends
 * an authoritative building_name (task rows), that wins over this map. The
 * names themselves live in the i18n catalog (opsSim.building.*) so they
 * follow the UI language. */
const BUILDING_NAME_KEYS: Record<string, MessageKey> = {
  QUARANTINE: "opsSim.building.QUARANTINE",
  FOUNDATION: "opsSim.building.FOUNDATION",
  BREEDING: "opsSim.building.BREEDING",
  PREGNANCY_EARLY: "opsSim.building.PREGNANCY_EARLY",
  PREGNANCY_LATE: "opsSim.building.PREGNANCY_LATE",
  DELIVERY: "opsSim.building.DELIVERY",
  RECOVERY: "opsSim.building.RECOVERY",
  RESTING: "opsSim.building.RESTING",
  MALE_KIDS: "opsSim.building.MALE_KIDS",
  FEMALE_KIDS: "opsSim.building.FEMALE_KIDS",
  FEED_STORE: "opsSim.building.FEED_STORE",
};

function buildingName(building: string, t: TFn): string {
  const key = BUILDING_NAME_KEYS[building];
  return key ? t(key) : building;
}

/** One stable colour per bucket, used everywhere a building appears on this
 * page (moves, transition matrix, journeys, occupancy) so an animal's path
 * between buildings can be tracked by colour alone. */
// Token-system exemption: this page needs a 10-hue categorical palette to
// keep every bucket visually distinct, which the semantic tones (success /
// warning / info / destructive) and the 5-slot chart ramp cannot express.
// Raw palette classes with explicit dark: variants are the established
// fallback until a categorical token ramp lands in globals.css.
const BUCKET_COLORS: Record<string, { chip: string; dot: string }> = {
  QUARANTINE: {
    chip: "border-orange-500/40 bg-orange-500/15 text-orange-700 dark:text-orange-300",
    dot: "bg-orange-500",
  },
  FOUNDATION: {
    chip: "border-lime-500/40 bg-lime-500/15 text-lime-700 dark:text-lime-300",
    dot: "bg-lime-500",
  },
  BREEDING: {
    chip: "border-violet-500/40 bg-violet-500/15 text-violet-700 dark:text-violet-300",
    dot: "bg-violet-500",
  },
  PREGNANCY_EARLY: {
    chip: "border-fuchsia-500/40 bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-300",
    dot: "bg-fuchsia-500",
  },
  PREGNANCY_LATE: {
    chip: "border-pink-500/40 bg-pink-500/15 text-pink-700 dark:text-pink-300",
    dot: "bg-pink-500",
  },
  DELIVERY: {
    chip: "border-amber-500/40 bg-amber-500/15 text-amber-700 dark:text-amber-300",
    dot: "bg-amber-500",
  },
  RECOVERY: {
    chip: "border-emerald-500/40 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    dot: "bg-emerald-500",
  },
  RESTING: {
    chip: "border-teal-500/40 bg-teal-500/15 text-teal-700 dark:text-teal-300",
    dot: "bg-teal-500",
  },
  MALE_KIDS: {
    chip: "border-blue-500/40 bg-blue-500/15 text-blue-700 dark:text-blue-300",
    dot: "bg-blue-500",
  },
  FEMALE_KIDS: {
    chip: "border-indigo-500/40 bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
    dot: "bg-indigo-500",
  },
  FEED_STORE: {
    chip: "border-slate-500/40 bg-slate-500/15 text-slate-700 dark:text-slate-300",
    dot: "bg-slate-500",
  },
};

/** Compact chip labels (opsSim.buildingShort.*); the full building name rides
 * along in the tooltip. */
const SHORT_BUILDING_KEYS: Record<string, MessageKey> = {
  QUARANTINE: "opsSim.buildingShort.QUARANTINE",
  FOUNDATION: "opsSim.buildingShort.FOUNDATION",
  BREEDING: "opsSim.buildingShort.BREEDING",
  PREGNANCY_EARLY: "opsSim.buildingShort.PREGNANCY_EARLY",
  PREGNANCY_LATE: "opsSim.buildingShort.PREGNANCY_LATE",
  DELIVERY: "opsSim.buildingShort.DELIVERY",
  RECOVERY: "opsSim.buildingShort.RECOVERY",
  RESTING: "opsSim.buildingShort.RESTING",
  MALE_KIDS: "opsSim.buildingShort.MALE_KIDS",
  FEMALE_KIDS: "opsSim.buildingShort.FEMALE_KIDS",
  FEED_STORE: "opsSim.buildingShort.FEED_STORE",
};

/** A colour-coded building chip: the visual atom for "where an animal is". */
function BucketChip({ bucket, className }: { bucket: string; className?: string }) {
  const t = useT();
  const colors = BUCKET_COLORS[bucket] ?? BUCKET_COLORS.FEED_STORE;
  const shortKey = SHORT_BUILDING_KEYS[bucket];
  return (
    <span
      title={buildingName(bucket, t)}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
        colors.chip,
        className,
      )}
    >
      <span aria-hidden="true" className={cn("size-1.5 shrink-0 rounded-full", colors.dot)} />
      {shortKey ? t(shortKey) : bucket}
    </span>
  );
}

/** The arrow between two chips — an icon, never a text glyph, so it cannot be
 * confused with the building names around it. */
function BucketArrow({ className }: { className?: string }) {
  return (
    <ArrowRight
      aria-hidden="true"
      className={cn("size-3.5 shrink-0 text-muted-foreground", className)}
    />
  );
}

/** The workflow context that caused a move, shown as a quiet mono badge. */
function ContextBadge({ context }: { context: string }) {
  return (
    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
      {context}
    </span>
  );
}

/** One hop as a chip pair: [from] → [to]. Shared by the moves table, the
 * transition matrix and the journeys list so the three read identically. */
function BucketMoveLine({
  fromBucket,
  toBucket,
}: {
  fromBucket: string | null;
  toBucket: string;
}) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <BucketChip bucket={fromBucket ?? ""} />
      <BucketArrow />
      <BucketChip bucket={toBucket} />
    </span>
  );
}

const MIN_HORIZON_DAYS = 7;
const MAX_HORIZON_DAYS = 365;
const MAX_START_HEAD = 500;
// Per-animal bounds from AnimalStartSpec in shared/openapi.json; the backend
// re-validates (422), but blocking the run client-side avoids a lost round trip.
const MAX_AGE_MONTHS = 240;
const MAX_DAYS_IN_BUCKET = 3650;
const MAX_BRED_DAYS = 150;

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
 * recognisable against the hand-derived golden traces. Labels resolve
 * through the i18n catalog (opsSim.preset.*). */
const PRESETS: { labelKey: MessageKey; rows: () => HerdRow[] }[] = [
  {
    labelKey: "opsSim.preset.toyHerd",
    rows: () => [
      ...Array.from({ length: 10 }, (_, i) =>
        makeRow({ tag: `D${i + 1}`, sex: "F", bucket: "BREEDING", ageMonths: 12 + i + 1 }),
      ),
      makeRow({ tag: "B1", sex: "M", bucket: "BREEDING", ageMonths: 24 }),
    ],
  },
  {
    labelKey: "opsSim.preset.mixedHerd",
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
  // The farm's timezone, not the browser's — same invariant as farmToday()
  // elsewhere: 00:00–05:30 farm time must not resolve to the previous day.
  return farmToday();
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
function dayBadges(day: DayRecord, t: TFn): string {
  const parts: string[] = [];
  if ((day.moves?.length ?? 0) > 0) {
    parts.push(t("opsSim.badge.moves", { count: day.moves?.length ?? 0 }));
  }
  if ((day.births?.length ?? 0) > 0) {
    parts.push(t("opsSim.badge.births", { count: day.births?.length ?? 0 }));
  }
  if ((day.exits?.length ?? 0) > 0) {
    parts.push(t("opsSim.badge.exits", { count: day.exits?.length ?? 0 }));
  }
  return parts.join(" · ");
}

function OpsSimulationPageContent() {
  const vocabulary = farmVocabulary;
  const t = useT();

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
  // Inputs snapshot the CURRENT result was produced from; null with no
  // result. A mismatch means the rows on screen no longer describe the run
  // above them (P3, 2026-09-20 audit: no stale flag existed).
  const [resultInputsSignature, setResultInputsSignature] = useState<string | null>(null);
  const [ledger, setLedger] = useState<string | null>(null);
  const [selectedDay, setSelectedDay] = useState(1);
  // The ledger can be megabytes of text: it mounts only when opened.
  const [ledgerOpen, setLedgerOpen] = useState(false);

  const rowErrors = useMemo(() => {
    const errors: string[] = [];
    const tags = rows.map((row) => row.tag.trim());
    if (tags.some((tag) => tag === "")) errors.push(t("opsSim.validation.needTag"));
    const duplicates = tags.filter((tag, index) => tag !== "" && tags.indexOf(tag) !== index);
    if (duplicates.length > 0) {
      errors.push(
        t("opsSim.validation.duplicateTags", { tags: [...new Set(duplicates)].join(", ") }),
      );
    }
    if (rows.length === 0) errors.push(t("opsSim.validation.needAnimal"));
    if (rows.length > MAX_START_HEAD) {
      errors.push(t("opsSim.validation.maxHead", { max: MAX_START_HEAD }));
    }
    for (const row of rows) {
      const label = row.tag.trim() || t("opsSim.validation.unnamed");
      if (row.ageMonths < 0 || row.ageMonths > MAX_AGE_MONTHS) {
        errors.push(t("opsSim.validation.ageRange", { label, max: MAX_AGE_MONTHS }));
      }
      if (row.daysInBucket < 0 || row.daysInBucket > MAX_DAYS_IN_BUCKET) {
        errors.push(t("opsSim.validation.daysRange", { label, max: MAX_DAYS_IN_BUCKET }));
      }
      if (row.bredDaysAgo.trim() !== "") {
        const bred = Number(row.bredDaysAgo);
        if (!Number.isInteger(bred) || bred < 0 || bred > MAX_BRED_DAYS) {
          errors.push(t("opsSim.validation.bredRange", { label, max: MAX_BRED_DAYS }));
        }
      }
      // Coherence mirrors of the server's DailyOpsInput._check_herd (P3,
      // 2026-09-20 audit: the 422s arrived only after submit). Structural
      // rules only — the gestation-day windows stay server-side where the
      // species profile lives.
      if (row.bucket === "MALE_KIDS" && row.sex !== "M") {
        errors.push(t("opsSim.validation.maleKidsOnly", { label }));
      }
      if (
        (row.bucket === "PREGNANCY_EARLY" ||
          row.bucket === "PREGNANCY_LATE" ||
          row.bucket === "DELIVERY") &&
        row.sex !== "F"
      ) {
        errors.push(t("opsSim.validation.doeOnly", { label }));
      }
      if (
        (row.bucket === "PREGNANCY_EARLY" ||
          row.bucket === "PREGNANCY_LATE" ||
          row.bucket === "DELIVERY") &&
        row.bredDaysAgo.trim() === ""
      ) {
        errors.push(
          t("opsSim.validation.needsBred", { label, bucket: row.bucket.toLowerCase() }),
        );
      }
      if (row.bucket === "QUARANTINE" && row.daysInBucket > 44) {
        errors.push(t("opsSim.validation.quarantineLimit", { label }));
      }
      if (row.bredDaysAgo.trim() !== "" && row.sex !== "F") {
        errors.push(t("opsSim.validation.bredDoesOnly", { label }));
      }
    }
    return errors;
  }, [rows, t]);

  const inputsSignature = useMemo(
    () =>
      JSON.stringify([startDate, horizonDays, seed, includeLedger, rows]),
    [startDate, horizonDays, seed, includeLedger, rows],
  );
  const resultStale =
    result !== null && resultInputsSignature !== null && resultInputsSignature !== inputsSignature;

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
      toast.error(
        t("opsSim.validation.horizonRange", { min: MIN_HORIZON_DAYS, max: MAX_HORIZON_DAYS }),
      );
      return;
    }
    await runAction.run(async () => {
      // L-27 (2026-09-17 audit): the ops run continuation had no farm-scope
      // fence, unlike planner's onPlan — a farm switch while the run was in
      // flight painted the old farm's result/ledger into the new farm's page
      // (and scrolled it), or toasted its failure. Capture before the
      // mutation; fence both the result painting and the error toast.
      const farmScope = captureFarmScope();
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
        if (!farmScope()) return;
        setResultInputsSignature(inputsSignature);
        setResult(response.data.result);
        setLedger(response.data.ledger ?? null);
        setSelectedDay(1);
        setLedgerOpen(false);
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch (err) {
        if (!farmScope()) return;
        toast.error(errorMessage(err, t("opsSim.runFailed")));
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
        title={t("opsSim.title")}
        description={t("opsSim.description", { young: vocabulary.young })}
      />

      {resultStale && (
        <p
          role="status"
          className="rounded-lg border border-warning/40 bg-warning-tint/60 px-3 py-2 text-sm text-warning-tint-foreground"
        >
          {t("opsSim.staleNotice")}
        </p>
      )}

      {result && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label={t("opsSim.stat.herd")}
            value={t("opsSim.stat.headValue", { count: finalHead })}
            icon={Beef}
            hint={t("opsSim.stat.fromStart", { count: result.head_start })}
          />
          <StatCard
            label={t("opsSim.stat.services")}
            value={`${result.totals.services} → ${result.totals.conceptions}`}
            icon={Baby}
            hint={t("opsSim.stat.kidsBorn", { count: result.totals.kids_born_alive })}
          />
          <StatCard
            label={t("opsSim.stat.moves")}
            value={String(result.totals.moves)}
            icon={Dumbbell}
            hint={t("opsSim.stat.exitsHint", {
              deaths: result.totals.deaths,
              culled: result.totals.culls,
              sold: result.totals.sales,
            })}
          />
          <StatCard
            label={t("opsSim.stat.feed")}
            value={t("opsSim.stat.kgValue", { kg: formatKg(totalFeedKg) })}
            icon={Wheat}
            hint={t("opsSim.stat.feedHint", {
              days: result.days.length,
              recipes: Object.keys(result.totals.feed_kg_by_recipe ?? {}).length,
            })}
          />
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>{t("opsSim.setup.title")}</CardTitle>
          <CardDescription>{t("opsSim.setup.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <div className="space-y-2">
              <Label htmlFor="ops-sim-start">{t("opsSim.setup.startDate")}</Label>
              <Input
                id="ops-sim-start"
                type="date"
                value={startDate}
                onChange={(event) => setStartDate(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="ops-sim-horizon">{t("opsSim.setup.horizon")}</Label>
              <NumberField
                id="ops-sim-horizon"
                value={horizonDays}
                min={MIN_HORIZON_DAYS}
                max={MAX_HORIZON_DAYS}
                onCommitNumber={(n) => setHorizonDays(Math.round(n))}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="ops-sim-seed">{t("opsSim.setup.seed")}</Label>
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
                {t("opsSim.setup.includeLedger")}
              </Button>
              <p className="text-xs text-muted-foreground">{t("opsSim.setup.ledgerHint")}</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {PRESETS.map((preset) => (
              <Button
                key={preset.labelKey}
                variant="outline"
                size="sm"
                onClick={() => setRows(preset.rows())}
              >
                <CalendarDays />
                {t(preset.labelKey)}
              </Button>
            ))}
            <Button variant="outline" size="sm" onClick={() => setRows((r) => [...r, makeRow()])}>
              <Plus />
              {t("opsSim.addAnimal")}
            </Button>
          </div>

          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-28">{t("opsSim.col.tag")}</TableHead>
                  <TableHead className="w-20">{t("opsSim.col.sex")}</TableHead>
                  <TableHead className="w-48">{t("opsSim.col.bucketBuilding")}</TableHead>
                  <TableHead className="w-24">{t("opsSim.col.age")}</TableHead>
                  <TableHead className="w-28">{t("opsSim.col.daysInBucket")}</TableHead>
                  <TableHead className="w-32">{t("opsSim.col.bredDays")}</TableHead>
                  <TableHead className="w-24">{t("opsSim.col.creepKid")}</TableHead>
                  <TableHead className="w-16" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row, index) => (
                  <TableRow key={row.key}>
                    <TableCell>
                      <Input
                        aria-label={t("opsSim.aria.tagForRow", {
                          row: index + 1,
                          tag: row.tag.trim()
                            ? t("opsSim.aria.tagSuffix", { tag: row.tag.trim() })
                            : "",
                        })}
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
                        <SelectTrigger aria-label={t("opsSim.aria.sexFor", { tag: row.tag || row.key })}>
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
                        <SelectTrigger
                          aria-label={t("opsSim.aria.bucketFor", { tag: row.tag || row.key })}
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {BUCKETS.map((bucket) => (
                            <SelectItem key={bucket} value={bucket}>
                              {buildingName(bucket, t)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell>
                      <NumberField
                        aria-label={t("opsSim.aria.ageFor", { tag: row.tag || row.key })}
                        value={row.ageMonths}
                        min={0}
                        max={240}
                        onCommitNumber={(n) => updateRow(row.key, { ageMonths: n })}
                        className="w-24"
                      />
                    </TableCell>
                    <TableCell>
                      <NumberField
                        aria-label={t("opsSim.aria.daysFor", { tag: row.tag || row.key })}
                        value={row.daysInBucket}
                        min={0}
                        onCommitNumber={(n) => updateRow(row.key, { daysInBucket: n })}
                        className="w-28"
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        aria-label={t("opsSim.aria.bredFor", { tag: row.tag || row.key })}
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
                        aria-label={t("opsSim.aria.creepFor", { tag: row.tag || row.key })}
                      />
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={t("opsSim.aria.remove", {
                          tag: row.tag || t("opsSim.rowFallback"),
                        })}
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
              {runMutation.isPending
                ? t("opsSim.simulating")
                : t("opsSim.runDays", { days: horizonDays })}
            </Button>
            {ledger && (
              <Button variant="outline" onClick={downloadLedger}>
                <Download />
                {t("opsSim.downloadLedger")}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {!result && (
        <EmptyState
          icon={FlaskConical}
          title={t("opsSim.empty.title")}
          description={t("opsSim.empty.description")}
        />
      )}

      {result && selectedRecord && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>{t("opsSim.timeline.title")}</CardTitle>
              <CardDescription>{t("opsSim.timeline.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto rounded-lg border p-2">
                {result.days.map((day) => {
                  const badges = dayBadges(day, t);
                  return (
                    <Button
                      key={day.day}
                      variant={day.day === selectedDay ? "default" : "outline"}
                      size="sm"
                      className="h-8 px-2 text-xs"
                      onClick={() => setSelectedDay(day.day)}
                      aria-pressed={day.day === selectedDay}
                      aria-label={
                        badges
                          ? t("opsSim.dayButtonAria", { day: day.day, badges })
                          : t("opsSim.dayRoutineAria", { day: day.day })
                      }
                      title={badges || t("opsSim.dayRoutineTitle", { day: day.day })}
                    >
                      {day.day}
                      {badges ? " •" : ""}
                    </Button>
                  );
                })}
              </div>

              <h3 className="text-sm font-semibold">
                {t("opsSim.dayHeading", { day: selectedRecord.day, date: selectedRecord.date })}
                {(() => {
                  const badges = dayBadges(selectedRecord, t);
                  return badges
                    ? t("opsSim.dayBadgesSuffix", { badges })
                    : t("opsSim.routineDaySuffix");
                })()}
              </h3>

              <div className="grid gap-4 lg:grid-cols-2">
                <div className="overflow-x-auto rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-16">{t("opsSim.col.time")}</TableHead>
                        <TableHead>{t("opsSim.col.duty")}</TableHead>
                        <TableHead className="w-40">{t("opsSim.col.building")}</TableHead>
                        <TableHead className="w-20">{t("opsSim.col.crew")}</TableHead>
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
                          <TableCell>
                            {task.building_name ?? buildingName(task.building, t)}
                          </TableCell>
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
                          <TableHead>{t("opsSim.col.feedingManifest")}</TableHead>
                          <TableHead className="text-right">{t("opsSim.col.heads")}</TableHead>
                          <TableHead className="text-right">{t("opsSim.col.kgPerHead")}</TableHead>
                          <TableHead className="text-right">{t("opsSim.col.am")}</TableHead>
                          <TableHead className="text-right">{t("opsSim.col.noon")}</TableHead>
                          <TableHead className="text-right">{t("opsSim.col.pm")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {selectedRecord.feeding?.map((line, index) => (
                          <TableRow key={index}>
                            <TableCell>
                              {buildingName(line.building, t)} — {line.recipe_display}
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
                          <TableHead>{t("opsSim.col.occupancy")}</TableHead>
                          <TableHead className="text-right">{t("opsSim.col.head")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {selectedRecord.occupancy?.map((row) => (
                          <TableRow key={row.building}>
                            <TableCell>
                              <span className="flex items-center gap-2">
                                <span
                                  aria-hidden="true"
                                  className={cn(
                                    "size-2 shrink-0 rounded-full",
                                    (BUCKET_COLORS[row.building] ?? BUCKET_COLORS.FEED_STORE).dot,
                                  )}
                                />
                                {buildingName(row.building, t)}
                              </span>
                            </TableCell>
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
                        <TableHead>{t("opsSim.col.animal")}</TableHead>
                        <TableHead>{t("opsSim.col.move")}</TableHead>
                        <TableHead className="w-28">{t("opsSim.col.context")}</TableHead>
                        <TableHead>{t("opsSim.col.reason")}</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {selectedRecord.moves?.map((move, index) => (
                        <TableRow key={index}>
                          <TableCell className="font-medium">{move.tag}</TableCell>
                          <TableCell>
                            <BucketMoveLine
                              fromBucket={move.from_bucket}
                              toBucket={move.to_bucket}
                            />
                          </TableCell>
                          <TableCell>
                            <ContextBadge context={move.context} />
                          </TableCell>
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
                      <span className="font-medium">{birth.dam_tag}</span>
                      {t("opsSim.kiddedSummary", {
                        live: birth.live_kids,
                        total: birth.kids.length,
                      })}
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
            title={t("opsSim.matrix.title")}
            description={t("opsSim.matrix.description")}
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("opsSim.col.from")}</TableHead>
                  <TableHead>{t("opsSim.col.to")}</TableHead>
                  <TableHead className="w-32">{t("opsSim.col.context")}</TableHead>
                  <TableHead className="text-right">{t("opsSim.col.count")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {result.transition_counts.map((row) => (
                  <TableRow key={`${row.from_bucket}-${row.to_bucket}-${row.context}`}>
                    <TableCell>
                      <BucketChip bucket={row.from_bucket} />
                    </TableCell>
                    <TableCell>
                      <BucketChip bucket={row.to_bucket} />
                    </TableCell>
                    <TableCell>
                      <ContextBadge context={row.context} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{row.count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </DataTableCard>

          <DataTableCard
            title={t("opsSim.journeys.title")}
            description={t("opsSim.journeys.description")}
          >
            {/* One row per animal ever alive — scroll the body, not the page. */}
            <div className="max-h-96 overflow-y-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-24">{t("opsSim.col.tag")}</TableHead>
                    <TableHead className="w-14">{t("opsSim.col.sex")}</TableHead>
                    <TableHead className="w-20">{t("opsSim.col.born")}</TableHead>
                    <TableHead>{t("opsSim.col.hops")}</TableHead>
                    <TableHead className="w-56">{t("opsSim.col.final")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {result.journeys.map((journey) => (
                    <TableRow key={journey.tag}>
                      <TableCell className="font-medium">{journey.tag}</TableCell>
                      <TableCell>{journey.sex}</TableCell>
                      <TableCell>{journey.born_day ?? t("opsSim.startLabel")}</TableCell>
                      <TableCell>
                        {(journey.hops ?? []).length === 0 ? (
                          "—"
                        ) : (
                          // One chip-row per hop: the day, [from] → [to] and
                          // the context — a journey reads top-to-bottom, not
                          // as one long text run.
                          <ol className="space-y-1">
                            {(journey.hops ?? []).map((hop, index) => (
                              <li
                                key={index}
                                className="flex flex-wrap items-center gap-1"
                                aria-label={t("opsSim.hopAria", {
                                  day: hop.day,
                                  from: buildingName(hop.from_bucket ?? "", t),
                                  to: buildingName(hop.to_bucket, t),
                                  context: hop.context,
                                })}
                              >
                                <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                                  d{hop.day}
                                </span>
                                <BucketChip bucket={hop.from_bucket ?? ""} />
                                <BucketArrow />
                                <BucketChip bucket={hop.to_bucket} />
                                <ContextBadge context={hop.context} />
                              </li>
                            ))}
                          </ol>
                        )}
                      </TableCell>
                      <TableCell>
                        {journey.final_bucket ? (
                          <span className="flex items-center gap-1.5">
                            <span
                              aria-hidden="true"
                              className={cn(
                                "size-2 shrink-0 rounded-full",
                                (
                                  BUCKET_COLORS[journey.final_bucket] ?? BUCKET_COLORS.FEED_STORE
                                ).dot,
                              )}
                            />
                            {buildingName(journey.final_bucket, t)}
                            {t("opsSim.activeSuffix")}
                          </span>
                        ) : (
                          <span>
                            {t("opsSim.exitSummary", {
                              kind: journey.exit_kind ?? "—",
                              day: journey.exit_day ?? "—",
                              reason: journey.exit_reason,
                            })}
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </DataTableCard>

          <Card>
            <CardHeader>
              <CardTitle>{t("opsSim.howItWorks.title")}</CardTitle>
              <CardDescription>{t("opsSim.howItWorks.description")}</CardDescription>
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
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant="outline" size="sm" onClick={downloadLedger}>
                      <Download />
                      {t("opsSim.downloadLedgerFull")}
                    </Button>
                    <Button
                      id="ops-sim-ledger-preview-toggle"
                      variant="ghost"
                      size="sm"
                      aria-pressed={ledgerOpen}
                      onClick={() => setLedgerOpen((value) => !value)}
                    >
                      {ledgerOpen ? t("opsSim.hideLedger") : t("opsSim.previewLedger")}
                    </Button>
                  </div>
                  {ledgerOpen && (
                    <pre className="mt-2 max-h-96 overflow-auto rounded-lg border bg-muted/40 p-3 text-xs">
                      {ledger}
                    </pre>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <ClipboardList className="size-4" />
            {t("opsSim.modelFooter", {
              model: result.model_version,
              seed: result.seed,
              days: result.days.length,
              head: result.head_start,
            })}
          </p>
        </>
      )}
    </div>
  );
}

export default function OpsSimulationPage() {
  const perms = usePermissions();
  const t = useT();
  return (
    <PermissionGate
      perms={perms}
      perm="simulation.view"
      label={t("opsSim.title")}
      description={t("opsSim.gateDescription")}
      cards={2}
    >
      <OpsSimulationPageContent />
    </PermissionGate>
  );
}
