/**
 * Simulation assumptions editor: the numeric contract the editor projects onto
 * the fields outside the representative sweep in page.test.tsx — whole-number
 * steps for head counts and durations, the explicit backend limits that
 * replace the naming heuristics, the unit captions those heuristics get wrong,
 * and the blankable "no ceiling" optimisation limits.
 */

import { screen } from "@testing-library/react";
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

/** Valid assumptions carrying every field whose contract this file pins.
 * Cross-field rules stay satisfied (moratorium < loan term, opening fodder
 * stock within storage capacity) so nothing but the edit under test can
 * block a run. */
const DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: {
    does: 50,
    bucks: 2,
    female_growers: 6,
    male_growers: 5,
    female_weaners: 4,
    male_weaners: 3,
    female_kids: 2,
    male_kids: 1,
    max_breeding_does: 200,
  },
  reproduction: {
    lactation_months: 7,
    months_open_before_breeding: 2,
    litter_size: 1.6,
    age_at_first_breeding_months: 10,
    stillbirth_rate: 0.05,
  },
  culling: { max_doe_age_months: 84, buck_rotation_years: 3 },
  growth: { adult_weight_doe_kg: 35, adult_weight_buck_kg: 45 },
  sales: {
    eid_month: 6,
    annual_livestock_price_growth_rate: 0.05,
    lactation_milk_litres: 120,
    transport_cost_per_head: 250,
  },
  feed: {
    cultivated_fodder_acres: 2,
    fodder_yield_t_dm_per_acre_year: 6,
    annual_feed_price_growth_rate: 0.06,
    initial_fodder_stock_kg_dm: 1000,
    fodder_storage_capacity_kg_dm: 5000,
    fodder_storage_loss_fraction_monthly: 0.02,
  },
  costs: {
    labour_per_head_threshold: 75,
    operating_cost_growth_rate_annual: 0.05,
    planned_capacity_head: 120,
    capacity_buffer_fraction: 0.1,
    shed_useful_life_years: 25,
    equipment_useful_life_years: 10,
    shed_residual_fraction: 0.1,
    equipment_residual_fraction: 0.05,
  },
  finance: {
    loan_term_months: 84,
    moratorium_months: 6,
    subsidy_fraction: 0.35,
    discount_rate_annual: 0.12,
    working_capital_months: 3,
    income_tax_rate: 0.25,
    terminal_livestock_realization_fraction: 0.8,
    terminal_asset_realization_fraction: 0.6,
    terminal_working_capital_recovery_fraction: 1,
    reinvestment_rate_annual: 0.08,
  },
  risk: {
    disease_outbreak_duration_months: 3,
    disease_adult_mortality_multiplier: 3,
    disease_kid_mortality_multiplier: 4,
    disease_conception_multiplier: 0.6,
    drought_probability_annual: 0.15,
    drought_duration_months: 4,
    drought_fodder_yield_multiplier: 0.5,
    drought_feed_price_multiplier: 1.5,
    market_crash_probability_annual: 0.1,
    market_crash_duration_months: 6,
    market_crash_price_multiplier: 0.7,
  },
  optimization: {
    minimum_dscr: 1.2,
    maximum_project_cost: null,
    maximum_funding_gap: null,
    doe_scale_high: 1.5,
    doe_scale_steps: 5,
    sale_age_radius_months: 3,
    retention_step: 0.1,
    loan_fraction_step: 0.05,
  },
};

/** Breeds + defaults + an empty scenario list; no run handler, because every
 * test here stays inside the editor. */
function registerApiHandlers() {
  server.use(
    permissionsHandler(MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
    ),
    http.get("/api/simulation/defaults", () => HttpResponse.json(DEFAULTS)),
    http.get("/api/simulation/scenarios", () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
  );
}

/** Render the page and wait for the auto-loaded defaults to reach the editor. */
async function renderLoaded() {
  registerApiHandlers();
  renderWithProviders(<SimulationPage />);
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
}

/** The visible caption under a field ("Unit: months"), or null when the field
 * declares no unit. */
function captionOf(label: string): string | null {
  return screen.getByLabelText(label).closest("div")?.querySelector("p")?.textContent ?? null;
}

describe("SimulationPage assumption field contracts", () => {
  it("projects the explicit backend limits, whole-number steps and units onto every field", async () => {
    await renderLoaded();

    const contract = (
      label: string,
      expected: { min?: string; max?: string; step: string; unit?: string },
    ) => {
      const input = screen.getByLabelText(label);
      if (expected.min === undefined) expect(input).not.toHaveAttribute("min");
      else expect(input).toHaveAttribute("min", expected.min);
      if (expected.max === undefined) expect(input).not.toHaveAttribute("max");
      else expect(input).toHaveAttribute("max", expected.max);
      expect(input).toHaveAttribute("step", expected.step);
      if (expected.unit === undefined) expect(input).not.toHaveAttribute("data-unit");
      else expect(input).toHaveAttribute("data-unit", expected.unit);
    };

    // Opening cohorts are whole head, capped at the herd-size ceiling.
    contract("Bucks", { min: "0", max: "100000", step: "1" });
    contract("Female Growers", { min: "0", max: "100000", step: "1" });
    contract("Male Growers", { min: "0", max: "100000", step: "1" });
    contract("Female Weaners", { min: "0", max: "100000", step: "1" });
    contract("Male Weaners", { min: "0", max: "100000", step: "1" });
    contract("Female Kids", { min: "0", max: "100000", step: "1" });
    contract("Male Kids", { min: "0", max: "100000", step: "1" });
    contract("Max Breeding Does", { min: "0", max: "100000", step: "1" });

    contract("Lactation Months", { min: "1", max: "12", step: "1", unit: "months" });
    contract("Months Open Before Breeding", {
      min: "0",
      max: "12",
      step: "1",
      unit: "months",
    });
    contract("Litter Size", { min: "0.5", max: "4", step: "any" });
    contract("Age At First Breeding Months", {
      min: "6",
      max: "30",
      step: "1",
      unit: "months",
    });
    contract("Stillbirth Rate", { min: "0", max: "0.5", step: "any", unit: "fraction" });

    contract("Max Doe Age Months", { min: "36", max: "180", step: "1", unit: "months" });
    contract("Buck Rotation Years", { min: "1", max: "10", step: "1", unit: "years" });

    // Weights are exclusive-minimum, so the browser gets a max but no min.
    contract("Adult Weight Doe Kg", { max: "1000", step: "any", unit: "kg" });
    contract("Adult Weight Buck Kg", { max: "1000", step: "any", unit: "kg" });

    contract("Eid Month", { min: "0", max: "12", step: "1", unit: "months" });
    contract("Annual Livestock Price Growth Rate", {
      max: "1",
      step: "any",
      unit: "fraction",
    });
    contract("Lactation Milk Litres", {
      min: "0",
      max: "100000",
      step: "any",
      unit: "litres",
    });
    contract("Transport Cost Per Head", {
      min: "0",
      max: "1000000000",
      step: "any",
      unit: "₹",
    });

    contract("Cultivated Fodder Acres", {
      min: "0",
      max: "1000000",
      step: "any",
      unit: "acres",
    });
    contract("Fodder Yield T Dm Per Acre Year", {
      max: "1000",
      step: "any",
      unit: "t DM/acre/yr",
    });
    contract("Annual Feed Price Growth Rate", { max: "1", step: "any", unit: "fraction" });
    contract("Initial Fodder Stock Kg Dm", {
      min: "0",
      max: "1000000000",
      step: "any",
      unit: "kg DM",
    });
    contract("Fodder Storage Capacity Kg Dm", {
      min: "0",
      max: "1000000000",
      step: "any",
      unit: "kg DM",
    });
    contract("Fodder Storage Loss Fraction Monthly", {
      min: "0",
      max: "1",
      step: "any",
      unit: "fraction",
    });

    contract("Labour Per Head Threshold", {
      min: "1",
      max: "1000000000000000",
      step: "1",
      unit: "head per labourer",
    });
    contract("Operating Cost Growth Rate Annual", {
      max: "1",
      step: "any",
      unit: "fraction",
    });
    contract("Planned Capacity Head", { min: "0", max: "100000", step: "1", unit: "head" });
    contract("Capacity Buffer Fraction", {
      min: "0",
      max: "1",
      step: "any",
      unit: "fraction",
    });
    contract("Shed Useful Life Years", { min: "1", max: "100", step: "1", unit: "years" });
    contract("Equipment Useful Life Years", {
      min: "1",
      max: "50",
      step: "1",
      unit: "years",
    });
    contract("Shed Residual Fraction", { min: "0", max: "1", step: "any", unit: "fraction" });
    contract("Equipment Residual Fraction", {
      min: "0",
      max: "1",
      step: "any",
      unit: "fraction",
    });

    contract("Loan Term Months", { min: "1", max: "180", step: "1", unit: "months" });
    contract("Moratorium Months", { min: "0", max: "60", step: "1", unit: "months" });
    contract("Subsidy Fraction", { min: "0", max: "0.9", step: "any", unit: "fraction" });
    contract("Discount Rate Annual", { min: "0", max: "0.5", step: "any", unit: "fraction" });
    contract("Working Capital Months", { min: "0", max: "24", step: "1", unit: "months" });
    contract("Income Tax Rate", { min: "0", max: "0.6", step: "any", unit: "fraction" });
    contract("Terminal Livestock Realization Fraction", {
      min: "0",
      max: "1",
      step: "any",
      unit: "fraction",
    });
    contract("Terminal Asset Realization Fraction", {
      min: "0",
      max: "1",
      step: "any",
      unit: "fraction",
    });
    contract("Terminal Working Capital Recovery Fraction", {
      min: "0",
      max: "1",
      step: "any",
      unit: "fraction",
    });
    contract("Reinvestment Rate Annual", {
      min: "0",
      max: "0.5",
      step: "any",
      unit: "fraction",
    });

    contract("Disease Outbreak Duration Months", {
      min: "1",
      max: "24",
      step: "1",
      unit: "months",
    });
    contract("Disease Adult Mortality Multiplier", {
      min: "1",
      max: "20",
      step: "any",
      unit: "multiplier",
    });
    contract("Disease Kid Mortality Multiplier", {
      min: "1",
      max: "20",
      step: "any",
      unit: "multiplier",
    });
    contract("Disease Conception Multiplier", { max: "1", step: "any", unit: "multiplier" });
    contract("Drought Probability Annual", {
      min: "0",
      max: "1",
      step: "any",
      unit: "fraction",
    });
    contract("Drought Duration Months", { min: "1", max: "24", step: "1", unit: "months" });
    contract("Drought Fodder Yield Multiplier", {
      max: "1",
      step: "any",
      unit: "multiplier",
    });
    contract("Drought Feed Price Multiplier", {
      min: "1",
      max: "20",
      step: "any",
      unit: "multiplier",
    });
    contract("Market Crash Probability Annual", {
      min: "0",
      max: "1",
      step: "any",
      unit: "fraction",
    });
    contract("Market Crash Duration Months", {
      min: "1",
      max: "24",
      step: "1",
      unit: "months",
    });
    contract("Market Crash Price Multiplier", { max: "1", step: "any", unit: "multiplier" });

    contract("Minimum Dscr", { min: "0", max: "10", step: "any" });
    // An optional ceiling: a floor of 0 and deliberately no maximum, because a
    // funding gap is a product of several capped inputs.
    contract("Maximum Funding Gap", { min: "0", step: "any", unit: "₹" });
    contract("Doe Scale High", { max: "5", step: "any", unit: "multiplier" });
    contract("Doe Scale Steps", { min: "1", max: "9", step: "1" });
    contract("Sale Age Radius Months", { min: "0", max: "9", step: "1", unit: "months" });
    contract("Retention Step", { min: "0", max: "1", step: "any" });
    contract("Loan Fraction Step", { min: "0", max: "1", step: "any", unit: "fraction" });
  });

  // The substring heuristics would caption these "kg", "months", "₹" or
  // nothing at all — a farmer reading "₹" over a 0-0.6 tax rate types 30.
  it("captions the fields the naming heuristics get wrong", async () => {
    await renderLoaded();

    expect(captionOf("Initial Fodder Stock Kg Dm")).toBe("Unit: kg DM");
    expect(captionOf("Fodder Storage Capacity Kg Dm")).toBe("Unit: kg DM");
    expect(captionOf("Planned Capacity Head")).toBe("Unit: head");
    expect(captionOf("Income Tax Rate")).toBe("Unit: fraction");
    expect(captionOf("Fodder Storage Loss Fraction Monthly")).toBe("Unit: fraction");
    expect(captionOf("Annual Livestock Price Growth Rate")).toBe("Unit: fraction");
    expect(captionOf("Annual Feed Price Growth Rate")).toBe("Unit: fraction");
    expect(captionOf("Operating Cost Growth Rate Annual")).toBe("Unit: fraction");
    expect(captionOf("Drought Probability Annual")).toBe("Unit: fraction");
    expect(captionOf("Market Crash Probability Annual")).toBe("Unit: fraction");
    expect(captionOf("Disease Adult Mortality Multiplier")).toBe("Unit: multiplier");
    expect(captionOf("Disease Kid Mortality Multiplier")).toBe("Unit: multiplier");
    expect(captionOf("Disease Conception Multiplier")).toBe("Unit: multiplier");
    expect(captionOf("Drought Fodder Yield Multiplier")).toBe("Unit: multiplier");
    expect(captionOf("Drought Feed Price Multiplier")).toBe("Unit: multiplier");
    expect(captionOf("Market Crash Price Multiplier")).toBe("Unit: multiplier");
    expect(captionOf("Doe Scale High")).toBe("Unit: multiplier");
    expect(captionOf("Maximum Funding Gap")).toBe("Unit: ₹ — blank means no limit");
  });

  it("names the ceiling and blocks the run when a bounded field leaves its range", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    const lactation = screen.getByLabelText("Lactation Months");
    await user.clear(lactation);
    await user.type(lactation, "13");

    expect(screen.getByText("Must be at most 12.")).toBeInTheDocument();
    expect(lactation).toHaveAttribute("aria-invalid", "true");
    expect(
      screen.getByText("Fix 1 highlighted numeric field before running or saving."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save as scenario" })).toBeDisabled();

    await user.clear(lactation);
    await user.type(lactation, "12");

    expect(screen.queryByText("Must be at most 12.")).not.toBeInTheDocument();
    expect(lactation).not.toHaveAttribute("aria-invalid");
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });

  it("names the floor of a field the naming heuristics leave unbounded", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    const maxDoeAge = screen.getByLabelText("Max Doe Age Months");
    await user.clear(maxDoeAge);
    await user.type(maxDoeAge, "35");
    expect(screen.getByText("Must be at least 36.")).toBeInTheDocument();

    const disease = screen.getByLabelText("Disease Adult Mortality Multiplier");
    await user.clear(disease);
    await user.type(disease, "0.5");
    // A multiplier below 1 would make a disease outbreak *reduce* mortality.
    expect(screen.getByText("Must be at least 1.")).toBeInTheDocument();
    expect(
      screen.getByText("Fix 2 highlighted numeric fields before running or saving."),
    ).toBeInTheDocument();
  });

  it("rejects fractional durations and zero on the exclusive-minimum fields", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    const rotation = screen.getByLabelText("Buck Rotation Years");
    await user.clear(rotation);
    await user.type(rotation, "2.5");
    expect(screen.getByText("Enter a whole number.")).toBeInTheDocument();
    expect(rotation).toHaveAttribute("aria-invalid", "true");

    await user.clear(rotation);
    await user.type(rotation, "2");
    expect(screen.queryByText("Enter a whole number.")).not.toBeInTheDocument();

    const scaleHigh = screen.getByLabelText("Doe Scale High");
    await user.clear(scaleHigh);
    await user.type(scaleHigh, "0");
    expect(screen.getByText("Must be greater than 0.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  // A null ceiling means "no limit"; rendered as a plain number field the row
  // would disappear from the form entirely.
  it("renders the optional funding-gap ceiling as a blankable input", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    const gap = screen.getByLabelText("Maximum Funding Gap");
    expect(gap).toHaveValue(null);

    await user.type(gap, "250000");
    expect(gap).toHaveValue(250000);
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();

    await user.clear(gap);
    // Blank is a legal value here, unlike every required numeric field.
    expect(gap).toHaveValue(null);
    expect(screen.queryByText("A value is required.")).not.toBeInTheDocument();
    expect(gap).not.toHaveAttribute("aria-invalid");
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
  });
});
