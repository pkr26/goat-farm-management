/**
 * Simulation page: the milk planner card. Dairy scenarios get the
 * litres-per-day reverse planner; meat scenarios never see it. The plan
 * request carries the editor's assumptions plus the target/ramp/sizing
 * inputs, and the report renders the herd design, procurement, projection
 * and the breeding-calendar notes. Request failures surface as an alert.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import SimulationPage from "./page";

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const MANAGE_PERMS = ["simulation.view", "simulation.manage"];

const DAIRY_DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 60, bucks: 0, foundation_flock_state: "mixed" },
  reproduction: { lactation_months: 10, gestation_months: 10 },
  sales: {
    lactation_milk_litres: 2400,
    milk_curve_shape: "wood",
    milk_peak_day: 65,
  },
};

const MEAT_DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2 },
  sales: { meat_price_per_kg: 400 },
};

const MILK_REPORT = {
  target_daily_litres: 1000,
  ramp_months: 1,
  hold_year_round: false,
  curve: {
    shape: "wood",
    lactation_litres: 2400,
    lactation_months: 10,
    peak_day: 65,
    peak_month_of_lactation: 3,
    peak_daily_litres: 11.6,
    avg_daily_litres_per_milking_doe: 7.9,
    monthly_litres: [218, 343, 354, 328, 288, 246, 205, 169, 138, 111],
  },
  herd: {
    daily_target_litres: 1000,
    calving_interval_months: 14.2,
    expected_services_per_conception: 2.2,
    breeding_does: 204,
    milking_does: 126,
    dry_does: 78,
    calvings_per_month: 14,
    ai_services_per_month: 37,
    replacement_does_per_month: 3.8,
    heifer_calves_available_per_month: 7.3,
    heifer_surplus_per_month: 3.5,
    starting_does_credited: 60,
    purchases_total: 147,
    replacement_purchases_total: 42,
    seasonal_low_daily_litres: 860,
    seasonal_high_daily_litres: 1080,
    herd_for_year_round_target: 233,
  },
  purchases: [
    { month: 1, count: 147, profile: "in-milk buffalo at mixed lactation stages" },
  ],
  projection: [
    {
      month: 1,
      calendar_month: 1,
      breeding_does: 207,
      milking_does: 143,
      dry_does: 64,
      freshenings: 15.9,
      ai_services: 31.9,
      heifer_graduates: 0,
      projected_daily_litres: 1027,
      target_daily_litres: 1000,
      gap_daily_litres: -27,
      projected_monthly_litres: 31270,
      projected_monthly_revenue: 2019482,
      meets_target: true,
    },
    {
      month: 2,
      calendar_month: 2,
      breeding_does: 206,
      milking_does: 143,
      dry_does: 63,
      freshenings: 15.9,
      ai_services: 33.5,
      heifer_graduates: 0,
      projected_daily_litres: 873,
      target_daily_litres: 1000,
      gap_daily_litres: 127,
      projected_monthly_litres: 26583,
      projected_monthly_revenue: 1717251,
      meets_target: false,
    },
  ],
  steady_from_month: 1,
  steady_average_daily_litres: 1000,
  achievable: true,
  notes: [
    "To land calvings in month T, start AI about 11 months earlier (gestation 10 + ~1 month(s) to conceive at 45% per service); with a 2-month voluntary waiting period after each calving.",
    "Yield seasonality moves the tank between 0.86x and 1.08x the annual mean.",
  ],
};

interface MilkPlanBody {
  daily_target_litres: number;
  ramp_months: number;
  projection_months: number;
  hold_year_round: boolean;
  assumptions: Record<string, unknown>;
}

function registerApiHandlers(options: {
  defaults?: unknown;
  onMilkPlan?: (body: MilkPlanBody) => void;
  milkPlanResult?: unknown;
  milkPlanStatus?: number;
} = {}) {
  server.use(
    permissionsHandler(MANAGE_PERMS),
      http.get("/api/simulation/defaults/breeds", () =>
        HttpResponse.json({ breeds: ["murrah_dairy"], systems: ["stall_fed"] }),
      ),
      http.get("/api/simulation/defaults", () =>
        HttpResponse.json(options.defaults ?? DAIRY_DEFAULTS),
      ),
    http.get("/api/simulation/scenarios", () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
    http.get("/api/simulation/herd-snapshot", () =>
      HttpResponse.json({
        does: 60,
        bucks: 0,
        f_kids: 0,
        f_weaners: 0,
        f_growers: 0,
        m_kids: 0,
        m_weaners: 0,
        m_growers: 0,
        total_head: 60,
      }),
    ),
    http.get("/api/simulation/calibration", () => HttpResponse.json({})),
    http.post("/api/simulation/milk-planner/plan", async ({ request }) => {
      options.onMilkPlan?.((await request.json()) as MilkPlanBody);
      return HttpResponse.json(options.milkPlanResult ?? MILK_REPORT, {
        status: options.milkPlanStatus ?? 200,
      });
    }),
  );
}

async function renderLoaded(
  options?: Parameters<typeof registerApiHandlers>[0],
) {
  registerApiHandlers(options);
  const view = renderWithProviders(<SimulationPage />, createTestQueryClient());
  expect(await screen.findByText("Horizon Months")).toBeInTheDocument();
  return view;
}

describe("SimulationPage milk planner", () => {
  it("shows the milk planner for dairy assumptions and hides it for meat", async () => {
    const dairy = await renderLoaded();
    expect(screen.getByText("Milk planner", { selector: "[data-slot=\'card-title\'], h2" })).toBeInTheDocument();
    dairy.unmount();

    await renderLoaded({ defaults: MEAT_DEFAULTS });
    await waitFor(() => {
      expect(screen.queryByText("Milk planner", { selector: "[data-slot=\'card-title\'], h2" })).not.toBeInTheDocument();
    });
  });

  it("sends the target and options, and renders the designed herd", async () => {
    const captured: { body: MilkPlanBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({
      onMilkPlan: (body) => {
        captured.body = body;
      },
    });

    const target = screen.getByLabelText("Daily target (L)");
    await user.clear(target);
    await user.type(target, "1000");

    await user.click(screen.getByRole("button", { name: "Plan milk" }));

    expect(captured.body).not.toBeNull();
    expect(captured.body?.daily_target_litres).toBe(1000);
    expect(captured.body?.ramp_months).toBe(1);
    expect(captured.body?.projection_months).toBe(36);
    expect(captured.body?.hold_year_round).toBe(false);
    expect(captured.body?.assumptions).toBeTruthy();

    expect(await screen.findByText(/Target is achievable/)).toBeInTheDocument();
    // Herd design headline: breeding/milking split and the monthly calendar.
    expect(screen.getByText(/204\.0 breeding/)).toBeInTheDocument();
    expect(screen.getByText(/14\.0 calvings/)).toBeInTheDocument();
    // Procurement line, including the replacement bridge.
    expect(screen.getAllByText(/in-milk animal/).length).toBeGreaterThan(0);
    expect(screen.getByText(/replacement bridge of 42\.0 head/)).toBeInTheDocument();
    // Projection rows render with litres and revenue.
    expect(screen.getByText("1,027")).toBeInTheDocument();
    expect(screen.getAllByText("₹20,19,482")[0]).toBeInTheDocument();
    // Breeding-calendar note.
    expect(screen.getByText(/start AI about 11 months earlier/)).toBeInTheDocument();
  });

  it("surfaces a failed plan request as an alert, not a silent empty card", async () => {
    const user = userEvent.setup();
    await renderLoaded({
      milkPlanStatus: 422,
      milkPlanResult: { detail: "The milk planner needs a dairy scenario" },
    });

    await user.click(screen.getByRole("button", { name: "Plan milk" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/dairy scenario/);
    // The empty state stays; no half-rendered report.
    expect(screen.getByText("No milk plan yet")).toBeInTheDocument();
  });
});
