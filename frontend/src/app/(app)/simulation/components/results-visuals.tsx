"use client";

/** Result-side visuals for a finished simulation run: the NPV Monte-Carlo
 *  histogram, the annual percentile checkpoints and the optimization
 *  decision table. */

import { ShieldAlert, Target } from "lucide-react";

import type {
  OptimizationCandidate,
  PercentileBand,
  SimulationResult,
} from "@/api/generated/models";
import { DataTableCard } from "@/components/data-table-card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Histogram } from "@/components/charts";
import type { FarmVocabulary } from "@/lib/farm-vocabulary";
import { formatMoney } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";

import { formatHead, formatPercent, formatRatio, humanize } from "./format-helpers";

/** NPV distribution on the shared chart kit, with P5/P50/P95 markers. */
export function MonteCarloHistogram({
  counts,
  edges,
  p5,
  p50,
  p95,
}: {
  counts: number[];
  edges: number[];
  p5?: number;
  p50?: number;
  p95?: number;
}) {
  const t = useT();
  const totalRuns = counts.reduce((sum, count) => sum + count, 0);
  const max = Math.max(...counts, 1);
  const peakBin = counts.indexOf(max);
  const bins = counts.map((count, i) => ({
    label: `${formatMoney(edges[i])} – ${formatMoney(edges[i + 1])}`,
    value: count,
    unit: t("simulation.monteCarlo.runs"),
  }));
  return (
    <Histogram
      bins={bins}
      domain={[edges[0], edges[edges.length - 1]]}
      markers={[
        p50 !== undefined
          ? {
              label: t("simulation.monteCarlo.medianP50"),
              value: p50,
              display: formatMoney(p50),
              strong: true,
            }
          : null,
        p5 !== undefined ? { label: "P5", value: p5, display: formatMoney(p5) } : null,
        p95 !== undefined ? { label: "P95", value: p95, display: formatMoney(p95) } : null,
      ].filter(Boolean) as {
        label: string;
        value: number;
        display?: string;
        strong?: boolean;
      }[]}
      ariaLabel={t("simulation.monteCarlo.histogramAria", {
        runs: totalRuns,
        bins: counts.length,
        start: formatMoney(edges[peakBin]),
        end: formatMoney(edges[peakBin + 1]),
        count: max,
      })}
    />
  );
}

export function RiskBandTable({
  herd,
  liquidity,
}: {
  herd: PercentileBand;
  liquidity: PercentileBand;
}) {
  const t = useT();
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
      <h3 className="text-sm font-medium">{t("simulation.riskBands.title")}</h3>
      <div className="overflow-x-auto rounded-lg border">
        <Table className="min-w-[760px]">
          <TableHeader>
            <TableRow>
              <TableHead>{t("simulation.riskBands.month")}</TableHead>
              <TableHead className="text-right">{t("simulation.riskBands.herdP5")}</TableHead>
              <TableHead className="text-right">{t("simulation.riskBands.herdP50")}</TableHead>
              <TableHead className="text-right">{t("simulation.riskBands.herdP95")}</TableHead>
              <TableHead className="text-right">{t("simulation.riskBands.cashP5")}</TableHead>
              <TableHead className="text-right">{t("simulation.riskBands.cashP50")}</TableHead>
              <TableHead className="text-right">{t("simulation.riskBands.cashP95")}</TableHead>
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

function localizedObjective(objective: string, t: ReturnType<typeof useT>): string {
  if (objective === "balanced") return t("simulation.option.balanced");
  if (objective === "npv") return t("simulation.option.highestNpv");
  if (objective === "liquidity") return t("simulation.option.strongestLiquidity");
  return humanize(objective);
}


export function OptimizationResults({
  result,
  vocabulary,
}: {
  result: NonNullable<SimulationResult["optimization"]>;
  /** The page's species vocabulary — one screen, one set of nouns. */
  vocabulary: FarmVocabulary;
}) {
  const t = useT();
  const { language } = useLanguage();
  const baselineIdentity = optimizationCandidateIdentity(result.baseline);
  const recommendedIdentity = result.recommended
    ? optimizationCandidateIdentity(result.recommended)
    : null;
  const rows: { label: string; candidate: OptimizationCandidate }[] = [
    {
      label:
        recommendedIdentity === baselineIdentity
          ? t("simulation.optimization.baselineRecommended")
          : t("simulation.optimization.baseline"),
      candidate: result.baseline,
    },
  ];
  if (result.recommended && recommendedIdentity !== baselineIdentity) {
    rows.push({ label: t("simulation.optimization.recommended"), candidate: result.recommended });
  }
  const seen = new Set(rows.map((row) => optimizationCandidateIdentity(row.candidate)));
  for (const candidate of result.alternatives ?? []) {
    const identity = optimizationCandidateIdentity(candidate);
    if (seen.has(identity)) continue;
    seen.add(identity);
    rows.push({ label: t("simulation.optimization.alternative", { rank: candidate.rank }), candidate });
  }

  return (
    <DataTableCard
      title={
        <span className="flex items-center gap-2">
          <Target className="size-4" aria-hidden />
          {t("simulation.optimization.title")}
        </span>
      }
      description={t("simulation.optimization.description", {
        feasible: result.feasible_candidates,
        evaluated: result.evaluated_candidates,
        objective: localizedObjective(result.objective, t),
      })}
      contentClassName="space-y-4"
    >
      {result.recommended === null && (
        <div
          role="alert"
          className="flex gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          {t("simulation.optimization.noFeasible")}
        </div>
      )}
      <div className="overflow-x-auto">
        <Table className="min-w-[820px]">
          <TableHeader>
            <TableRow>
              <TableHead>{t("simulation.optimization.decisionSet")}</TableHead>
              <TableHead>{t("simulation.optimization.status")}</TableHead>
              <TableHead className="text-right">
                {t("simulation.optimization.herdColumns", {
                  female: language === "en" ? vocabulary.femaleAdult : t("simulation.token.doe"),
                  male: language === "en" ? vocabulary.maleAdult : t("simulation.token.buck"),
                })}
              </TableHead>
              <TableHead className="text-right">{t("simulation.optimization.saleAge")}</TableHead>
              <TableHead className="text-right">{t("simulation.optimization.retention")}</TableHead>
              <TableHead className="text-right">{t("simulation.optimization.debtShare")}</TableHead>
              <TableHead className="text-right">{t("simulation.optimization.projectCost")}</TableHead>
              <TableHead className="text-right">{t("simulation.optimization.capacityPeak")}</TableHead>
              <TableHead className="text-right">{t("simulation.metric.npv")}</TableHead>
              <TableHead className="text-right">{t("simulation.metric.irr")}</TableHead>
              <TableHead className="text-right">{t("simulation.optimization.minimumDscr")}</TableHead>
              <TableHead className="text-right">{t("simulation.optimization.minimumCash")}</TableHead>
              <TableHead className="text-right">{t("simulation.optimization.fundingGap")}</TableHead>
              <TableHead>{t("simulation.optimization.constraintFindings")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ label, candidate }) => (
              <TableRow key={`${label}:${optimizationCandidateIdentity(candidate)}`}>
                <TableCell className="font-medium">{label}</TableCell>
                <TableCell
                  className={candidate.feasible ? "text-success" : "text-destructive"}
                >
                  {candidate.feasible
                    ? t("simulation.optimization.feasible")
                    : t("simulation.optimization.infeasible")}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {candidate.starting_does} / {candidate.starting_bucks} /{" "}
                  {candidate.max_breeding_does}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {t("simulation.optimization.monthsShort", {
                    count: candidate.sale_age_months,
                  })}
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
                    : t("simulation.optimization.none")}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </DataTableCard>
  );
}
