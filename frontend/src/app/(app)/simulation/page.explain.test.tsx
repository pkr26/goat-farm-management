/**
 * Simulation page: metric explanation dialogs from the stat-card info
 * buttons, and the narrative report card with the viability verdict badge.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import SimulationPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const MANAGE_PERMS = ["simulation.view", "simulation.manage"];

/** Sparse but valid full assumptions object (only present keys render). */
const DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2, foundation_flock_state: "open" },
  finance: { interest_rate_annual: 0.12 },
};

const RESULT = {
  months: [],
  annual_pl: [],
  metrics: {
    project_cost: 500000,
    loan_amount: 250000,
    subsidy_amount: 100000,
    equity: 150000,
    npv: 234567,
    irr: 0.18,
    mirr: 0.16,
    bcr: 1.42,
    dscr_per_year: [1.8],
    avg_dscr: 1.8,
    min_dscr: 1.8,
    payback_month: null,
    break_even_meat_price_per_kg: 320,
    peak_capacity_head: 58,
    terminal_value: 150000,
    tax_total: 0,
    accounting_profit_total: 25000,
    minimum_cash_balance: 43000,
    minimum_cash_month: 1,
    additional_working_capital_required: 0,
    operating_margin: 0.25,
  },
  amortization: [],
  feed_summary: {
    annual_green_kg: [],
    annual_homegrown_green_kg: [],
    annual_purchased_green_kg: [],
    annual_dry_kg: [],
    annual_concentrate_kg: [],
    annual_feed_cost: [],
    annual_fodder_waste_kg_dm: [],
    land_requirement_acres: 0,
    fodder_deficit_months: 0,
    peak_fodder_stock_kg_dm: 0,
  },
  project_cost_breakdown: {
    shed_cost: 200000,
    equipment_cost: 50000,
    stock_cost: 200000,
    working_capital: 50000,
    capacity_places: 58,
    capacity_basis: "projected_peak",
    projected_peak_head: 52.4,
  },
  terminal_value_breakdown: {
    livestock: 100000,
    shed: 30000,
    equipment: 5000,
    working_capital: 15000,
    total: 150000,
  },
  model_version: "3.0.0",
  assumptions_fingerprint: "0123456789abcdef0123456789abcdef",
  metric_explanations: [
    {
      key: "npv",
      title: "Net Present Value",
      explanation: "NPV discounts every month's net cash flow back to month 0.",
      figures: { npv: 234567, discount_rate: 0.12, ignored: null },
    },
    {
      key: "loan_amount",
      title: "Loan",
      explanation:
        "85.0% of the project cost is repaid over 72 months at 11.0% annual interest.",
      // Real backend figure keys (explain.py): a 0-1 fraction and a month
      // count sitting next to genuine money.
      figures: { loan_amount: 250000, loan_fraction: 0.85, loan_term_months: 72 },
    },
    {
      key: "break_even_meat_price_per_kg",
      title: "Break-even",
      explanation: "The unit clears break-even with a safety margin.",
      figures: { break_even_meat_price_per_kg: 320, safety_margin: 0.426 },
    },
    {
      key: "project_cost",
      title: "Project Cost",
      explanation: "Total project cost is ₹5,00,000 at month 0.",
      figures: {
        shed_cost: 200000,
        equipment_cost: 50000,
        stock_cost: 200000,
        working_capital: 50000,
      },
    },
  ],
  narrative_report: [
    {
      key: "overview",
      title: "Overview",
      paragraphs: ["A 50-doe stall-fed Osmanabadi unit over 60 months."],
      figures: {},
    },
    {
      key: "herd_trajectory",
      title: "Herd Trajectory",
      paragraphs: ["The herd grows from 52 to 90 head."],
      figures: {},
    },
    {
      key: "revenue_mix",
      title: "Revenue Mix",
      paragraphs: ["Meat sales dominate revenue."],
      figures: {},
    },
    {
      key: "cost_mix",
      title: "Cost Mix",
      paragraphs: ["Feed is the largest cost."],
      figures: {},
    },
    {
      key: "viability_verdict",
      title: "Viability Verdict",
      paragraphs: ["The unit clears the viability bar."],
      figures: { verdict: "VIABLE" },
    },
    {
      key: "risks",
      title: "Risks",
      paragraphs: ["Meat price swings are the main risk."],
      figures: {},
    },
  ],
  monte_carlo: null,
  sensitivity: null,
  optimization: null,
};

async function renderWithResult(result = RESULT) {
  server.use(
    permissionsHandler(MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
    ),
    http.get("/api/simulation/defaults", () => HttpResponse.json(DEFAULTS)),
    http.get("/api/simulation/scenarios", () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
    http.post("/api/simulation/run", () => HttpResponse.json(result)),
  );
  const user = userEvent.setup();
  renderWithProviders(<SimulationPage />);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Run simulation" }));
  expect((await screen.findAllByText("₹2,34,567"))[0]).toBeInTheDocument();
  return user;
}

describe("SimulationPage explainability", () => {
  it("opens the matching explanation from a stat card's info button", async () => {
    const user = await renderWithResult();

    await user.click(screen.getByRole("button", { name: "Explain NPV" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Net Present Value")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "NPV discounts every month's net cash flow back to month 0.",
      ),
    ).toBeInTheDocument();
    // Figures: currency formatting for money keys, percent for rate keys,
    // and null values skipped.
    expect(within(dialog).getAllByText("₹2,34,567")[0]).toBeInTheDocument();
    expect(within(dialog).getByText("Discount Rate")).toBeInTheDocument();
    expect(within(dialog).getByText("12.0%")).toBeInTheDocument();
    expect(within(dialog).queryByText("Ignored")).not.toBeInTheDocument();
  });

  it("shows the project cost breakdown in the project cost explanation", async () => {
    const user = await renderWithResult();

    await user.click(screen.getByRole("button", { name: "Explain Project cost" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Shed Cost")).toBeInTheDocument();
    // shed_cost and stock_cost are both 200000 in the fixture.
    expect(within(dialog).getAllByText("₹2,00,000")).toHaveLength(2);
    expect(within(dialog).getByText("Working Capital")).toBeInTheDocument();
    // equipment_cost and working_capital are both 50000 in the fixture.
    expect(within(dialog).getAllByText("₹50,000")).toHaveLength(2);
  });

  // REGRESSION — formatFigure inferred units from substrings, so the money
  // pattern claimed `loan_fraction` and `loan_term_months` ("loan") and
  // `subsidy_fraction` ("subsidy"), rendering "₹0.85" and "₹72"; `safety_margin`
  // matched nothing and printed the raw 0.43.
  it("formats fractions as percentages and month counts as plain numbers", async () => {
    const user = await renderWithResult();

    await user.click(screen.getByRole("button", { name: "Explain Loan" }));
    const loanDialog = await screen.findByRole("dialog");
    expect(within(loanDialog).getAllByText("₹2,50,000")[0]).toBeInTheDocument();
    expect(within(loanDialog).getByText("85.0%")).toBeInTheDocument();
    expect(within(loanDialog).getByText("72")).toBeInTheDocument();
    expect(within(loanDialog).queryByText("₹0.85")).not.toBeInTheDocument();
    expect(within(loanDialog).queryByText("₹72")).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "Explain Break-even meat (₹/kg)" }));
    const breakEvenDialog = await screen.findByRole("dialog");
    expect(within(breakEvenDialog).getByText("42.6%")).toBeInTheDocument();
    expect(within(breakEvenDialog).queryByText("0.43")).not.toBeInTheDocument();
  });

  it("renders all six narrative report sections with the verdict badge", async () => {
    await renderWithResult();

    expect(screen.getByText("Report")).toBeInTheDocument();
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByText("Herd Trajectory")).toBeInTheDocument();
    expect(screen.getByText("Revenue Mix")).toBeInTheDocument();
    expect(screen.getByText("Cost Mix")).toBeInTheDocument();
    expect(screen.getByText("Viability Verdict")).toBeInTheDocument();
    expect(screen.getByText("Risks")).toBeInTheDocument();
    expect(screen.getByText("VIABLE")).toBeInTheDocument();
    expect(
      screen.getByText("The unit clears the viability bar."),
    ).toBeInTheDocument();
  });

  it("renders payback month as a dash when null", async () => {
    await renderWithResult();

    const paybackLabel = screen.getByText("Payback month");
    const card = paybackLabel.closest("div.rounded-xl") as HTMLElement;
    expect(within(card).getByText("—")).toBeInTheDocument();
  });

  it("shows a hard warning when the projected herd exceeds funded capacity", async () => {
    await renderWithResult({
      ...RESULT,
      project_cost_breakdown: {
        ...RESULT.project_cost_breakdown,
        capacity_places: 40,
        projected_peak_head: 52.4,
      },
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Projected peak herd exceeds funded housing and equipment capacity by 12.4 head",
    );
  });
});
