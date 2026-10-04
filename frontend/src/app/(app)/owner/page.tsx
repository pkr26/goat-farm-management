"use client";

/**
 * Cross-farm owner console: every farm the
 * caller owns on one screen — attention headlines ranked worst-first, and a
 * benchmark table for comparing farms against each other. A per-farm "Open"
 * switches the active farm context and lands on that farm's dashboard, so
 * the 20-farm owner stops re-selecting farms from the picker between checks.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowUpRight, BarChart3 } from "lucide-react";

import {
  useOwnerBenchmarksApiOwnerBenchmarksGet,
  useOwnerOverviewApiOwnerOverviewGet,
} from "@/api/generated/endpoints";
import { DataTableCard } from "@/components/data-table-card";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { TableSkeleton } from "@/components/skeletons";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { formatMoney, formatMoneyDecimal } from "@/lib/format";
import { useT } from "@/lib/i18n";
import { useAuth } from "@/lib/auth-context";

const BENCHMARK_WINDOWS = [30, 90, 365] as const;

export default function OwnerPage() {
  const t = useT();
  const router = useRouter();
  const { selectFarm, farms } = useAuth();
  const [windowDays, setWindowDays] = useState<number>(90);

  // The backend serves owners of >= 1 farm — not just the current farm's
  // owner — so both the queries and the no-access gate key on the farms
  // list (role === null marks ownership), never perms.isOwner (which is
  // current-farm only). Gating the queries also keeps a worker's visit
  // from firing a doomed 403 request.
  const ownsAnyFarm = farms.some((f) => f.role === null);

  const overviewQuery = useOwnerOverviewApiOwnerOverviewGet({
    query: { enabled: ownsAnyFarm },
  });
  const benchmarksQuery = useOwnerBenchmarksApiOwnerBenchmarksGet(
    { days: windowDays },
    { query: { enabled: ownsAnyFarm } },
  );

  if (!ownsAnyFarm) {
    return (
      <EmptyState
        icon={BarChart3}
        title={t("owner.noAccess.title")}
        description={t("owner.noAccess.description")}
      />
    );
  }

  const overview =
    overviewQuery.data?.status === 200 ? overviewQuery.data.data : undefined;
  const benchmarks =
    benchmarksQuery.data?.status === 200 ? benchmarksQuery.data.data : undefined;

  // Attention ranking: the farm with the most overdue duties first, then
  // open screening flags, then pending-today pressure. Idle farms sink.
  const farmsRanked = [...(overview?.farms ?? [])].sort((a, b) => {
    const score = (f: (typeof farmsRanked)[number]) =>
      f.overdue_duties * 1000 + f.open_screening_flags * 100 + f.todays_duties_pending;
    return score(b) - score(a);
  });

  const openFarm = (farmId: number, timezone: string) => {
    selectFarm(farmId, timezone);
    router.push("/dashboard");
  };

  return (
    <div className="space-y-6">
      <PageHeader title={t("owner.title")} description={t("owner.description")} />

      <DataTableCard
        title={t("owner.overview.title")}
        description={t("owner.overview.description")}
        ariaBusy={overviewQuery.isFetching}
      >
        {overviewQuery.isError && overview && <p role="alert" className="mb-3 text-sm text-destructive">
          {t("owner.refreshFailed")}{" "}
          <Button size="sm" variant="outline" onClick={() => void overviewQuery.refetch()}>{t("common.retry")}</Button>
        </p>}
        {overviewQuery.isPending ? (
          <div role="status" aria-live="polite">
            <TableSkeleton rows={4} columns={8} />
          </div>
        ) : !overview ? (
          <EmptyState
            icon={BarChart3}
            title={t("common.somethingWentWrong")}
            description={t("owner.overview.loadFailed")}
          >
            <Button
              type="button"
              variant="outline"
              onClick={() => void overviewQuery.refetch()}
            >
              {t("common.retry")}
            </Button>
          </EmptyState>
        ) : farmsRanked.length === 0 ? (
          <EmptyState
            icon={BarChart3}
            title={t("owner.overview.empty")}
            description={t("owner.overview.emptyDescription")}
          />
        ) : (
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>{t("owner.overview.farm")}</th>
                  <th className="text-right">{t("owner.overview.active")}</th>
                  <th className="text-right">{t("owner.overview.overdue")}</th>
                  <th className="text-right">{t("owner.overview.today")}</th>
                  <th className="text-right">{t("owner.overview.watch")}</th>
                  <th className="text-right">{t("owner.overview.holds")}</th>
                  <th className="text-right">{t("owner.overview.flags")}</th>
                  <th className="text-right">{t("owner.overview.monthNet")}</th>
                  <th className="sr-only">{t("owner.overview.open")}</th>
                </tr>
              </thead>
              <tbody>
                {farmsRanked.map((farm) => (
                  <tr key={farm.farm_id}>
                    <td className="font-medium">{farm.farm_name}</td>
                    <td className="table-numeric text-right">{farm.active_animals}</td>
                    <td className="table-numeric text-right">
                      {farm.overdue_duties > 0 ? (
                        <StatusBadge status="ERROR">{farm.overdue_duties}</StatusBadge>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="table-numeric text-right">
                      {farm.todays_duties_done}/{farm.todays_duties_done + farm.todays_duties_pending}
                    </td>
                    <td className="table-numeric text-right">{farm.kidding_watch > 0 ? farm.kidding_watch : "—"}</td>
                    <td className="table-numeric text-right">{farm.movement_restricted > 0 ? farm.movement_restricted : "—"}</td>
                    <td className="table-numeric text-right">
                      {farm.open_screening_flags > 0 ? (
                        <StatusBadge status="PENDING_REVIEW">
                          {farm.open_screening_flags}
                        </StatusBadge>
                      ) : (
                        "—"
                      )}
                    </td>
                    {/* month_net is a Decimal-pattern string on the wire —
                     * route it through formatMoneyDecimal, never Number(). */}
                    <td className="table-numeric text-right">
                      {formatMoneyDecimal(farm.month_net)}
                    </td>
                    <td>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => openFarm(farm.farm_id, farm.timezone)}
                      >
                        <ArrowUpRight aria-hidden /> {t("owner.overview.open")}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </DataTableCard>

      <DataTableCard
        title={t("owner.benchmarks.title")}
        description={t("owner.benchmarks.description", { days: windowDays })}
        actions={
          <div className="flex flex-wrap gap-1" role="group" aria-label={t("owner.benchmarks.window")}>
            {BENCHMARK_WINDOWS.map((days) => (
              <Button
                key={days}
                size="sm"
                variant={windowDays === days ? "default" : "outline"}
                aria-pressed={windowDays === days}
                onClick={() => setWindowDays(days)}
              >
                {t("owner.benchmarks.days", { days })}
              </Button>
            ))}
          </div>
        }
        ariaBusy={benchmarksQuery.isFetching}
      >
        {benchmarksQuery.isError && benchmarks && <p role="alert" className="mb-3 text-sm text-destructive">
          {t("owner.refreshFailed")}{" "}
          <Button size="sm" variant="outline" onClick={() => void benchmarksQuery.refetch()}>{t("common.retry")}</Button>
        </p>}
        {benchmarksQuery.isPending ? (
          <div role="status" aria-live="polite">
            <TableSkeleton rows={3} columns={7} />
          </div>
        ) : !benchmarks ? (
          <EmptyState icon={BarChart3} title={t("common.somethingWentWrong")}
            description={t("owner.benchmarks.loadFailed")}>
            <Button variant="outline" onClick={() => void benchmarksQuery.refetch()}>{t("common.retry")}</Button>
          </EmptyState>
        ) : benchmarks.farms.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("owner.benchmarks.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>{t("owner.overview.farm")}</th>
                  <th className="text-right">{t("owner.benchmarks.conception")}</th>
                  <th className="text-right">{t("owner.benchmarks.kidMortality")}</th>
                  <th className="text-right">{t("owner.benchmarks.dailyGain")}</th>
                  <th className="text-right">{t("owner.benchmarks.feedPerKg")}</th>
                  <th className="text-right">{t("owner.benchmarks.profitPerSold")}</th>
                  <th className="text-right">{t("owner.benchmarks.sold")}</th>
                </tr>
              </thead>
              <tbody>
                {benchmarks.farms.map((farm) => (
                  <tr key={farm.farm_id}>
                    <td className="font-medium">{farm.farm_name}</td>
                    <td className="table-numeric text-right">{farm.conception_rate === null ? "—" : `${farm.conception_rate}%`}</td>
                    <td className="table-numeric text-right">
                      {farm.kid_mortality_rate === null ? "—" : `${farm.kid_mortality_rate}%`}
                    </td>
                    <td className="table-numeric text-right">
                      {farm.avg_daily_gain_kg === null
                        ? "—"
                        : `${farm.avg_daily_gain_kg.toFixed(2)} kg`}
                    </td>
                    <td className="table-numeric text-right">
                      {farm.feed_cost_per_kg_gain === null
                        ? "—"
                        : formatMoney(farm.feed_cost_per_kg_gain)}
                    </td>
                    <td className="table-numeric text-right">
                      {farm.profit_per_animal_sold === null
                        ? "—"
                        : formatMoney(farm.profit_per_animal_sold)}
                    </td>
                    <td className="table-numeric text-right">{farm.animals_sold}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </DataTableCard>
    </div>
  );
}
