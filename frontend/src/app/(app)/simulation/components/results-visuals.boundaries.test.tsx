import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { OptimizationCandidate } from "@/api/generated/models";
import { farmVocabulary } from "@/lib/farm-vocabulary";

import { MonteCarloHistogram, OptimizationResults, RiskBandTable } from "./results-visuals";

function candidate(overrides: Partial<OptimizationCandidate> = {}): OptimizationCandidate {
  return {
    rank: 0, starting_does: 100, starting_bucks: 5, max_breeding_does: 100,
    sale_age_months: 12, female_retention_fraction: 0.1, loan_fraction: 0.1,
    festival_hold_months: 1, max_services_before_cull: 3, project_cost: 1000,
    capacity_places: 105, projected_peak_head: 105, npv: 1000, irr: 0.2,
    min_dscr: 1, minimum_cash_balance: 0, funding_gap: 0, feasible: true,
    ...overrides,
  };
}

function band(months: number, scale: number) {
  const values = Array.from({ length: months }, (_, index) => (index + 1) * scale);
  return { p5: values, p25: values, p50: values, p75: values, p95: values };
}

function decisionRows() {
  return within(screen.getByRole("table")).getAllByRole("row").slice(1);
}

describe("exported simulation result boundaries", () => {
  it("announces the actual peak for finite fractional bin frequencies", () => {
    render(<MonteCarloHistogram counts={[0.25, 0.5]} edges={[0, 100, 200]} />);
    const svg = screen.getByRole("img");
    expect(svg).toHaveAccessibleName(
      "NPV histogram: 0.75 runs across 2 bins, most frequent ₹100 – ₹200 with 0.5 runs",
    );
    expect(Array.from(svg.querySelectorAll("rect title"), (title) => title.textContent)).toEqual([
      "₹0 – ₹100: 0.25 runs",
      "₹100 – ₹200: 0.5 runs",
    ]);
  });

  it.each([
    { months: 0, expected: [] },
    { months: 1, expected: [1] },
    { months: 12, expected: [1, 12] },
    { months: 13, expected: [1, 12, 13] },
    { months: 25, expected: [1, 12, 24, 25] },
  ])("shows first, complete-year and final checkpoints for $months months", ({ months, expected }) => {
    render(<RiskBandTable herd={band(months, 10)} liquidity={band(months, 100)} />);
    const rows = decisionRows();
    expect(rows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual(expected.map(String));
    rows.forEach((row, index) => {
      const cells = within(row).getAllByRole("cell");
      expect(cells).toHaveLength(7);
      expect(cells[1]).toHaveTextContent(String(expected[index] * 10));
      expect(cells[4]).toHaveTextContent(`₹${(expected[index] * 100).toLocaleString("en-IN")}`);
    });
  });

  it("limits risk checkpoints to months present in both result bands", () => {
    render(<RiskBandTable herd={band(25, 10)} liquidity={band(13, 100)} />);
    expect(decisionRows().map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual(["1", "12", "13"]);
  });

  it("keeps distinct recommendations and later alternatives after duplicates", () => {
    const baseline = candidate();
    const recommended = candidate({ rank: 1, starting_does: 110 });
    const alternative = candidate({ rank: 2, starting_bucks: 6 });
    render(<OptimizationResults vocabulary={farmVocabulary} result={{
      objective: "balanced", evaluated_candidates: 3, feasible_candidates: 3,
      baseline, recommended, alternatives: [baseline, recommended, alternative, alternative],
    }} />);
    expect(decisionRows().map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual([
      "Baseline", "Recommended", "Alternative 2",
    ]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("identifies a recommendation of the baseline decision without duplicate rows", () => {
    const baseline = candidate();
    render(<OptimizationResults vocabulary={farmVocabulary} result={{
      objective: "balanced", evaluated_candidates: 1, feasible_candidates: 1,
      baseline, recommended: { ...baseline, rank: 1 }, alternatives: [baseline],
    }} />);
    expect(decisionRows()).toHaveLength(1);
    expect(within(decisionRows()[0]).getAllByRole("cell")[0]).toHaveTextContent("Baseline / recommended");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a diagnostic infeasible baseline and an explicit warning when there is no recommendation", () => {
    render(<OptimizationResults vocabulary={farmVocabulary} result={{
      objective: "balanced", evaluated_candidates: 1, feasible_candidates: 0,
      baseline: candidate({ feasible: false, constraint_violations: ["Capacity shortfall"] }),
      recommended: null,
    }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The baseline below is diagnostic, not a recommendation.");
    const cells = within(decisionRows()[0]).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("Baseline");
    expect(cells[1]).toHaveTextContent("Infeasible");
    expect(cells[1]).toHaveAttribute("data-tone", "warning");
    expect(cells[13]).toHaveTextContent("Capacity shortfall");
  });

  it("uses the exported consumer's English goat vocabulary in its result headers", () => {
    const baseline = candidate();
    render(<OptimizationResults vocabulary={{ ...farmVocabulary, femaleAdult: "nanny goat", maleAdult: "billy goat" }} result={{
      objective: "balanced", evaluated_candidates: 1, feasible_candidates: 1, baseline, recommended: baseline,
    }} />);
    expect(screen.getByRole("columnheader", { name: "nanny goats / billy goats / ceiling" })).toBeInTheDocument();
  });

  it.each([
    { cash: -1, gap: 1, cashWarning: true, gapWarning: true },
    { cash: 0, gap: 0, cashWarning: false, gapWarning: false },
    { cash: 1, gap: -1, cashWarning: false, gapWarning: false },
  ])("marks financial shortfalls without flagging the break-even boundary ($cash, $gap)", ({ cash, gap, cashWarning, gapWarning }) => {
    const baseline = candidate({ minimum_cash_balance: cash, funding_gap: gap });
    render(<OptimizationResults vocabulary={farmVocabulary} result={{
      objective: "balanced", evaluated_candidates: 1, feasible_candidates: 1, baseline, recommended: baseline,
    }} />);
    const cells = within(decisionRows()[0]).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("Feasible");
    expect(cells[1]).toHaveAttribute("data-tone", "success");
    expect(cells[11]).toHaveAttribute("data-tone", cashWarning ? "warning" : "neutral");
    expect(cells[12]).toHaveAttribute("data-tone", gapWarning ? "warning" : "neutral");
    expect(cells[11]).toHaveTextContent(cash < 0 ? "-₹1" : `₹${cash}`);
    expect(cells[12]).toHaveTextContent(gap < 0 ? "-₹1" : `₹${gap}`);
    expect(cells[13]).toHaveTextContent("None");
  });
});
