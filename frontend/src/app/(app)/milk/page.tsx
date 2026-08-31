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
import type { MilkRecordIn } from "@/api/generated/models";
import { AnimalPicker } from "@/components/animal-picker";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
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
} from "@/components/ui/table";
import { ApiError } from "@/lib/api-client";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { farmToday, formatLitres } from "@/lib/format";
import { invalidateFarmData } from "@/lib/query-invalidation";
import { usePermissions } from "@/lib/use-permissions";
import { useSingleFlight } from "@/lib/use-single-flight";
import { useFarmType } from "@/hooks/use-farm-type";
import { toast } from "sonner";

const SHIFTS = ["MORNING", "AFTERNOON", "NIGHT"] as const;
const SUMMARY_WINDOW_DAYS = 30;

function shiftLabel(shift: string): string {
  if (shift === "MORNING") return "Morning";
  if (shift === "AFTERNOON") return "Afternoon";
  return "Night";
}

function shiftTime(shift: string): string {
  if (shift === "MORNING") return "5:30 AM";
  if (shift === "AFTERNOON") return "1:30 PM";
  return "7:30 PM";
}

export default function MilkPage() {
  const { can, loading, isError } = usePermissions();
  const vocabulary = farmVocabulary(useFarmType());
  const queryClient = useQueryClient();
  const [animalId, setAnimalId] = useState("");
  const [shift, setShift] = useState<(typeof SHIFTS)[number]>("MORNING");
  const [litres, setLitres] = useState("");
  const [fatPct, setFatPct] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const submit = useSingleFlight();

  const allowed = can("milk.view");
  // The parlour is a dairy module: a meat farm holds milk.view through the
  // owner role but has no milk data (and its milk APIs reject the farm type),
  // so neither the queries nor the copy below apply. Hide proactively rather
  // than waiting on the backend's 4xx.
  const parlourEnabled = allowed && vocabulary.dairy;
  const canRecord = can("milk.manage");
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

  const todayTotal = summaryData?.daily?.find((day: { date: string }) => day.date === today);

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
    const parsedFat = fatPct.trim() === "" ? undefined : Number(fatPct);
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
        toast.success(`Recorded ${parsedLitres} L (${shiftLabel(shift).toLowerCase()})`);
        setLitres("");
        setFatPct("");
        await invalidateFarmData(queryClient);
        await Promise.all([summary.refetch(), listing.refetch()]);
      } catch (error) {
        toast.error(error instanceof ApiError ? error.detail : "Could not record the yield.");
      }
    });
  }

  if (loading) {
    return <p className="p-6 text-muted-foreground">Loading permissions…</p>;
  }
  if (isError) {
    return <p className="p-6 text-destructive">Could not load permissions. Try again later.</p>;
  }
  if (!allowed) {
    return <p className="p-6 text-muted-foreground">You do not have permission to view milk records.</p>;
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

  const femaleAdultLabel =
    vocabulary.femaleAdult.charAt(0).toUpperCase() + vocabulary.femaleAdult.slice(1);

  return (
    <div className="space-y-6 p-4 sm:p-6">
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
                        {shiftLabel(option)} · {shiftTime(option)}
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
                <Label htmlFor="milk-fat">Fat % (optional)</Label>
                <Input
                  id="milk-fat"
                  type="number"
                  step="0.1"
                  min="3"
                  max="12"
                  inputMode="decimal"
                  value={fatPct}
                  onChange={(event) => setFatPct(event.target.value)}
                />
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
            <p className="text-sm text-destructive">
              {listing.error instanceof ApiError ? listing.error.detail : "Could not load records."}
            </p>
          ) : listing.isPending ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (records?.length ?? 0) === 0 ? (
            <p className="text-sm text-muted-foreground">
              No readings today yet — record the morning milking above.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{femaleAdultLabel}</TableHead>
                  <TableHead>Shift</TableHead>
                  <TableHead className="text-right tabular-nums">Litres</TableHead>
                  <TableHead className="text-right tabular-nums">Fat %</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(records ?? []).map((record) => (
                  <TableRow key={record.id}>
                    <TableCell className="font-medium">{record.animal_tag}</TableCell>
                    <TableCell>{shiftLabel(record.shift)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {record.litres.toFixed(1)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
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
            <p className="text-sm text-destructive">
              {summary.error instanceof ApiError ? summary.error.detail : "Could not load summary."}
            </p>
          ) : summary.isPending ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (summaryData?.animals?.length ?? 0) === 0 ? (
            <p className="text-sm text-muted-foreground">
              Averages appear once readings exist. Below 6 L/day at peak lactation is a cull
              candidate; the daily-yield trend feeds the monthly cull review.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{femaleAdultLabel}</TableHead>
                  <TableHead className="text-right tabular-nums">Days milked</TableHead>
                  <TableHead className="text-right tabular-nums">Total L</TableHead>
                  <TableHead className="text-right tabular-nums">Avg L/day</TableHead>
                  <TableHead className="text-right tabular-nums">Avg fat %</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(summaryData?.animals ?? []).map((animal: { animal_id: number; animal_tag: string; total_litres: number; avg_daily_litres: number; days_recorded: number; avg_fat_pct: number | null }) => (
                  <TableRow key={animal.animal_id}>
                    <TableCell className="font-medium">{animal.animal_tag}</TableCell>
                    <TableCell className="text-right tabular-nums">{animal.days_recorded}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatLitres(animal.total_litres)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {animal.avg_daily_litres.toFixed(1)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
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
