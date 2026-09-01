"use client";

/** Milk yields: today's parlour log, herd trend and per-animal averages.
 *  Dairy module — hidden outright for meat farms, whose milk APIs 4xx. */

import { useQueryClient } from "@tanstack/react-query";
import { ChartColumn, Droplets, Milk, Plus, RefreshCw } from "lucide-react";
import { useState } from "react";

import {
  useAddMilkRecordApiMilkNewPost,
  useMilkListApiMilkGet,
  useMilkSummaryEndpointApiMilkSummaryGet,
} from "@/api/generated/endpoints";
import type {
  MilkAnimalSummaryOut,
  MilkDayTotalOut,
  MilkRecordIn,
} from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
import { LineChart, Sparkline } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { InlineLoading, PageSkeleton } from "@/components/skeletons";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatCard } from "@/components/stat-card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  SortableTableHead,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { enumLabel } from "@/lib/enum-labels";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { farmToday, formatDate, formatLitres } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { useFarmType } from "@/hooks/use-farm-type";
import { toast } from "sonner";
import { PermissionsError } from "@/components/permissions-error";

const SHIFTS = ["MORNING", "AFTERNOON", "NIGHT"] as const;
const SUMMARY_WINDOW_DAYS = 30;

function shiftTime(shift: string): string {
  if (shift === "MORNING") return "5:30 AM";
  if (shift === "AFTERNOON") return "1:30 PM";
  return "7:30 PM";
}

/** "2026-08-05" → "5 Aug" — the shared formatter's date without the year, for
 *  chart tick labels that must stay narrow. */
function shortDay(iso: string): string {
  return formatDate(iso).replace(/ \d{4}$/, "");
}

export default function MilkPage() {
  const { can, loading, isError , refetch } = usePermissions();
  // The permissions query is disabled until the auth bootstrap selects a
  // farm; `loading` alone is false during that window while the permission
  // set is still empty — deciding "no permission" then would flash a denial
  // at a signed-in operator. The skeleton stays up until the session (and
  // with it the permission fetch) is real.
  const { loading: authLoading } = useAuth();
  const vocabulary = farmVocabulary(useFarmType());
  const queryClient = useQueryClient();
  const [animalId, setAnimalId] = useState("");
  const [shift, setShift] = useState<(typeof SHIFTS)[number]>("MORNING");
  const [litres, setLitres] = useState("");
  const [fatPct, setFatPct] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  /** Client-side sort of the readings table — the API's newest-first order is
   *  the default; clicking a header sorts what you can see. */
  const [sortState, setSortState] = useState<{
    column: "date" | "litres";
    direction: "asc" | "desc";
  } | null>(null);
  const submit = useSingleFlight();

  const allowed = can("milk.view");
  // The parlour is a dairy module: a meat farm holds milk.view through the
  // owner role but has no milk data (and its milk APIs reject the farm type),
  // so neither the queries nor the copy below apply. Hide proactively rather
  // than waiting on the backend's 4xx.
  const parlourEnabled = allowed && vocabulary.dairy;
  const canRecord = can("milk.manage");
  // Fat is the number ₹/kg-fat procurement pricing pays on, so it is keyed by
  // the quality/manager roles — not by the attendant who records litres.
  const canTestFat = can("milk.quality");
  const today = farmToday();

  const summary = useMilkSummaryEndpointApiMilkSummaryGet(
    { days: SUMMARY_WINDOW_DAYS },
    { query: { enabled: parlourEnabled } },
  );
  const listing = useMilkListApiMilkGet(
    { limit: 50, date_from: today },
    { query: { enabled: parlourEnabled } },
  );
  const addRecord = useAddMilkRecordApiMilkNewPost();

  const summaryData = summary.data?.status === 200 ? summary.data.data : undefined;
  const records =
    listing.data?.status === 200 ? listing.data.data.records : undefined;

  const todayTotal = summaryData?.daily.find((day: MilkDayTotalOut) => day.date === today);
  /** Per-day herd litres across the summary window — feeds the sparkline and
   *  the trend card, both of which need at least two points to be a trend. */
  const dailySeries: MilkDayTotalOut[] = summaryData?.daily ?? [];
  const sparklineLitres = dailySeries.slice(-14).map((day) => day.litres);

  async function onSubmit() {
    setFormError(null);
    const parsedLitres = Number(litres);
    if (!animalId) {
      setFormError(`Pick the ${vocabulary.femaleAdult} this reading belongs to.`);
      return;
    }
    if (!Number.isFinite(parsedLitres) || parsedLitres <= 0 || parsedLitres > 100) {
      setFormError("Litres must be between 0 and 100 for one milking.");
      return;
    }
    // Fat is only ever submitted by the quality role. A stale value left in
    // the (disabled) field after a mid-session permission loss is dropped,
    // not fatal: null carries the tested fat forward server-side, and the
    // recorder could not clear the disabled input to recover otherwise.
    const parsedFat = canTestFat && fatPct.trim() !== "" ? Number(fatPct) : undefined;
    if (parsedFat !== undefined && (!Number.isFinite(parsedFat) || parsedFat < 3 || parsedFat > 12)) {
      setFormError(`Fat % runs 3–12 for ${vocabulary.species} milk.`);
      return;
    }
    const payload: MilkRecordIn = {
      animal_id: Number(animalId),
      date: today,
      shift,
      litres: parsedLitres,
      fat_pct: parsedFat ?? null,
    };
    await submit.run(async () => {
      try {
        await addRecord.mutateAsync({ data: payload });
        toast.success(`Recorded ${parsedLitres} L (${enumLabel("shift", shift).toLowerCase()})`);
        setLitres("");
        setFatPct("");
        await invalidateFarmData(queryClient);
        await Promise.all([summary.refetch(), listing.refetch()]);
      } catch (error) {
        toast.error(error instanceof ApiError ? error.detail : "Could not record the yield.");
      }
    });
  }

  const femaleAdultLabel =
    vocabulary.femaleAdult.charAt(0).toUpperCase() + vocabulary.femaleAdult.slice(1);

  if (loading || authLoading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Milk"
          description={`${femaleAdultLabel} yields by milking shift, herd daily totals and ${SUMMARY_WINDOW_DAYS}-day averages.`}
        />
        <PageSkeleton stats={3} cards={3} />
      </div>
    );
  }
  if (isError) {
    return <PermissionsError onRetry={() => void refetch()} />;
  }
  if (!allowed) {
    return (
      <p className="text-muted-foreground">
        You do not have permission to view milk records.
      </p>
    );
  }
  if (!vocabulary.dairy) {
    return (
      <EmptyState
        icon={Milk}
        title="Milk recording is for dairy farms."
        description="This farm is a meat herd, so there is no parlour log here. Milk yields are recorded on dairy farms."
      />
    );
  }

  const readingsSort = sortState;
  const toggleSort = (column: string) => {
    setSortState((prev) =>
      prev?.column === column
        ? prev.direction === "asc"
          ? { column: column as "date" | "litres", direction: "desc" }
          : null
        : { column: column as "date" | "litres", direction: "asc" },
    );
  };
  const sortedRecords = readingsSort
    ? [...(records ?? [])].sort((a, b) => {
        const dir = readingsSort.direction === "asc" ? 1 : -1;
        if (readingsSort.column === "date") return a.date.localeCompare(b.date) * dir;
        return (a.litres - b.litres) * dir;
      })
    : (records ?? []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Milk"
        description={`${femaleAdultLabel} yields by milking shift, herd daily totals and ${SUMMARY_WINDOW_DAYS}-day averages.`}
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => void Promise.all([summary.refetch(), listing.refetch()])}
            disabled={summary.isFetching || listing.isFetching}
          >
            <RefreshCw className="size-4" /> Refresh
          </Button>
        }
      >
      </PageHeader>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          icon={Milk}
          label="Today's herd total"
          value={todayTotal ? `${todayTotal.litres.toFixed(1)} L` : "0 L"}
          hint={
            todayTotal
              ? `${todayTotal.recorded_animals} ${vocabulary.species} milked${todayTotal.avg_fat_pct != null ? ` · ${todayTotal.avg_fat_pct.toFixed(1)}% fat` : ""}`
              : "No readings yet today"
          }
          footer={
            sparklineLitres.length >= 2 ? (
              <Sparkline data={sparklineLitres} ariaLabel="Herd litres, last 14 days" />
            ) : undefined
          }
        />
        <StatCard
          icon={ChartColumn}
          label={`${SUMMARY_WINDOW_DAYS}-day total`}
          value={summaryData ? `${formatLitres(summaryData.total_litres)} L` : "—"}
          hint={summaryData ? `${formatLitres(summaryData.avg_daily_litres)} L/day average` : undefined}
        />
        <StatCard
          icon={Droplets}
          label="Average fat"
          value={summaryData?.avg_fat_pct != null ? `${summaryData.avg_fat_pct.toFixed(1)}%` : "—"}
          hint={`${vocabulary.typeLabel} benchmark 6.5–7.5%`}
        />
      </div>

      {dailySeries.length >= 2 && (
        <Card>
          <CardHeader>
            <CardTitle>Herd milk trend</CardTitle>
            <CardDescription>
              Daily herd litres across the {SUMMARY_WINDOW_DAYS}-day window.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <LineChart
              ariaLabel={`Herd milk trend, daily litres over the last ${SUMMARY_WINDOW_DAYS} days`}
              yLabel="litres"
              points={dailySeries.map((day, index) => ({
                x: index,
                y: day.litres,
                xLabel: shortDay(day.date),
              }))}
            />
          </CardContent>
        </Card>
      )}

      {canRecord && (
        <Card>
          <CardHeader>
            <CardTitle>Record a milking</CardTitle>
            <CardDescription>
              Re-entering the same {vocabulary.femaleAdult}, date and shift replaces the reading.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
              <div className="space-y-1.5 lg:col-span-2">
                <Label htmlFor="milk-animal">{femaleAdultLabel}</Label>
                <AnimalPicker
                  id="milk-animal"
                  value={animalId}
                  onValueChange={setAnimalId}
                  placeholder={`Pick a ${vocabulary.femaleAdult}…`}
                  dialogTitle={`Pick the ${vocabulary.femaleAdult} milked`}
                  labelVariant="bucket"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="milk-shift">Shift</Label>
                <Select value={shift} onValueChange={(value) => setShift(value as (typeof SHIFTS)[number])}>
                  <SelectTrigger id="milk-shift" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SHIFTS.map((option) => (
                      <SelectItem key={option} value={option}>
                        {enumLabel("shift", option)} · {shiftTime(option)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="milk-litres">Litres</Label>
                <Input
                  id="milk-litres"
                  type="number"
                  step="0.1"
                  min="0"
                  max="100"
                  inputMode="decimal"
                  value={litres}
                  onChange={(event) => setLitres(event.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="milk-fat">
                  Fat % {canTestFat ? "(optional)" : "(quality role only)"}
                </Label>
                <Input
                  id="milk-fat"
                  type="number"
                  step="0.1"
                  min="3"
                  max="12"
                  inputMode="decimal"
                  value={fatPct}
                  onChange={(event) => setFatPct(event.target.value)}
                  disabled={!canTestFat}
                  aria-describedby={canTestFat ? undefined : "milk-fat-help"}
                />
                {!canTestFat && (
                  <p id="milk-fat-help" className="text-xs text-muted-foreground">
                    Only the milk quality / manager roles record fat tests. A
                    re-recorded yield keeps its tested fat as-is.
                  </p>
                )}
              </div>
            </div>
            {formError && (
              <p role="alert" className="mt-3 text-sm text-destructive">
                {formError}
              </p>
            )}
            <Button
              className="mt-4"
              onClick={() => void onSubmit()}
              disabled={submit.pending || addRecord.isPending}
            >
              <Plus className="size-4" /> {submit.pending ? "Saving…" : "Record yield"}
            </Button>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Today&apos;s readings</CardTitle>
          <CardDescription>Newest first; a {vocabulary.femaleAdult} appears once per shift.</CardDescription>
        </CardHeader>
        <CardContent>
          {listing.isError ? (
            <div role="alert" className="space-y-3">
              <p className="text-sm text-destructive">
                {listing.error instanceof ApiError
                  ? listing.error.detail
                  : "Could not load records."}
              </p>
              <Button
                type="button"
                variant="outline"
                onClick={() => void listing.refetch()}
              >
                Retry readings
              </Button>
            </div>
          ) : listing.isPending ? (
            <InlineLoading>Loading…</InlineLoading>
          ) : (records?.length ?? 0) === 0 ? (
            <EmptyState
              icon={Droplets}
              title="No readings today yet"
              description="Record the morning milking above."
              className="py-8"
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{femaleAdultLabel}</TableHead>
                  <SortableTableHead
                    column="date"
                    label="Date"
                    direction={readingsSort?.column === "date" ? readingsSort.direction : null}
                    onSort={toggleSort}
                  />
                  <TableHead>Shift</TableHead>
                  <SortableTableHead
                    column="litres"
                    label="Litres"
                    className="text-right"
                    direction={readingsSort?.column === "litres" ? readingsSort.direction : null}
                    onSort={toggleSort}
                  />
                  <TableHead className="text-right">Fat %</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedRecords.map((record) => (
                  <TableRow key={record.id}>
                    <TableCell className="font-medium">{record.animal_tag}</TableCell>
                    <TableCell>{formatDate(record.date)}</TableCell>
                    <TableCell>{enumLabel("shift", record.shift)}</TableCell>
                    <TableCell className="text-right">
                      {record.litres.toFixed(1)}
                    </TableCell>
                    <TableCell className="text-right">
                      {record.fat_pct != null ? record.fat_pct.toFixed(1) : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            Per-{vocabulary.femaleAdult} averages ({SUMMARY_WINDOW_DAYS} days)
          </CardTitle>
          <CardDescription>Top 200 by total yield — the cull-review league.</CardDescription>
        </CardHeader>
        <CardContent>
          {summary.isError ? (
            <div role="alert" className="space-y-3">
              <p className="text-sm text-destructive">
                {summary.error instanceof ApiError
                  ? summary.error.detail
                  : "Could not load summary."}
              </p>
              <Button
                type="button"
                variant="outline"
                onClick={() => void summary.refetch()}
              >
                Retry summary
              </Button>
            </div>
          ) : summary.isPending ? (
            <InlineLoading>Loading…</InlineLoading>
          ) : (summaryData?.animals?.length ?? 0) === 0 ? (
            <EmptyState
              icon={ChartColumn}
              title="Averages appear once readings exist"
              description="Below 6 L/day at peak lactation is a cull candidate; the daily-yield trend feeds the monthly cull review."
              className="py-8"
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{femaleAdultLabel}</TableHead>
                  <TableHead className="text-right">Days milked</TableHead>
                  <TableHead className="text-right">Total L</TableHead>
                  <TableHead className="text-right">Avg L/day</TableHead>
                  <TableHead className="text-right">Avg fat %</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(summaryData?.animals ?? []).map((animal: MilkAnimalSummaryOut) => (
                  <TableRow key={animal.animal_id}>
                    <TableCell className="font-medium">{animal.animal_tag}</TableCell>
                    <TableCell className="text-right">{animal.days_recorded}</TableCell>
                    <TableCell className="text-right">
                      {formatLitres(animal.total_litres)}
                    </TableCell>
                    <TableCell className="text-right">
                      {animal.avg_daily_litres.toFixed(1)}
                    </TableCell>
                    <TableCell className="text-right">
                      {animal.avg_fat_pct != null ? animal.avg_fat_pct.toFixed(1) : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
