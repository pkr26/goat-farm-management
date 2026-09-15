/**
 * Planner page — NLM subsidy toggle and the saved-plan DPR download:
 * the toggle rides the plan payload's finance.nlm_subsidy flag (on and off),
 * and the DPR button fetches /api/planner/plans/{id}/dpr (text/markdown) and
 * hands it to the browser as a file.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { server, TEST_FARMS } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import PlannerPage from "./page";

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/planner",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const GOAT_DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2 },
  reproduction: { gestation_months: 5, conception_rate: 0.85, litter_size: 1.6 },
  growth: { sale_age_months: 9 },
  mortality: { kid_pre_weaning: 0.15, kid_post_weaning: 0.05 },
  sales: { meat_price_per_kg: 400 },
};

function currentYearMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function addMonths(yearMonth: string, months: number): string {
  const [year, month] = yearMonth.split("-").map(Number);
  const total = year * 12 + (month - 1) + months;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

const SAVED_PLAN = {
  id: 7,
  farm_id: 1,
  name: "Festival plan",
  notes: "",
  start_year_month: currentYearMonth(),
  targets: [
    { year_month: addMonths(currentYearMonth(), 12), animal_class: "male_grower", count: 20 },
  ],
  assumptions: GOAT_DEFAULTS,
  valid: true,
  validation_error: null,
  revision: 1,
  created_at: "2026-09-02T10:00:00Z",
  updated_at: "2026-09-02T10:00:00Z",
};

interface PlanBody {
  assumptions: { finance?: { nlm_subsidy?: boolean } } & Record<string, unknown>;
}

function registerApiHandlers(options: {
  onPlan?: (body: PlanBody) => void;
  dprStatus?: number;
  savedPlans?: unknown[];
} = {}) {
  server.use(
    http.get("/api/auth/farms", () => HttpResponse.json(TEST_FARMS)),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi", "sirohi"], systems: ["stall_fed", "semi_intensive"] }),
    ),
    http.get("/api/simulation/defaults", () => HttpResponse.json(GOAT_DEFAULTS)),
    http.get("/api/planner/plans", () =>
      HttpResponse.json({
        items: options.savedPlans ?? [SAVED_PLAN],
        total: options.savedPlans?.length ?? 1,
        limit: 50,
        offset: 0,
      }),
    ),
    http.post("/api/planner/plan", async ({ request }) => {
      options.onPlan?.((await request.json()) as PlanBody);
      // A minimal report the page can render without crashing.
      return HttpResponse.json({
        start_year_month: currentYearMonth(),
        horizon_months: 60,
        targets_echo: [],
        plan: {
          before: { targets: [], npv: 0, total_shortfall: 0, all_met: true },
          after: null,
          recommended_purchases: [],
          gaps_closed: true,
          probabilities: [],
          notes: [],
        },
        stage_plan: [],
        actions: [],
        chains: [],
        notes: [],
      });
    }),
    http.get("/api/planner/plans/:id/dpr", () =>
      options.dprStatus && options.dprStatus !== 200
        ? HttpResponse.json({ detail: "DPR blew up" }, { status: options.dprStatus })
        : new HttpResponse("# DPR — Festival plan\n\nProject cost ₹6,00,000\n", {
            status: 200,
            headers: { "Content-Type": "text/markdown" },
          }),
    ),
  );
}

async function renderLoaded(options?: Parameters<typeof registerApiHandlers>[0]) {
  registerApiHandlers(options);
  renderWithProviders(<PlannerPage />, createTestQueryClient());
  await screen.findByText("Sale targets");
}

afterEach(() => {
  vi.restoreAllMocks();
  toastMocks.success.mockClear();
  toastMocks.error.mockClear();
});

describe("PlannerPage NLM subsidy toggle", () => {
  it("defaults to off and sends finance.nlm_subsidy=false in the plan payload", async () => {
    const captured: { body: PlanBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({ onPlan: (body) => (captured.body = body) });

    const toggle = screen.getByRole("checkbox", { name: "Apply NLM subsidy" });
    expect(toggle).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Add target" }));
    await user.click(screen.getByRole("button", { name: "Plan" }));

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body?.assumptions.finance?.nlm_subsidy).toBe(false);
  });

  it("sends finance.nlm_subsidy=true once the toggle is on", async () => {
    const captured: { body: PlanBody | null } = { body: null };
    const user = userEvent.setup();
    await renderLoaded({ onPlan: (body) => (captured.body = body) });

    await user.click(screen.getByRole("checkbox", { name: "Apply NLM subsidy" }));
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await user.click(screen.getByRole("button", { name: "Plan" }));

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body?.assumptions.finance?.nlm_subsidy).toBe(true);
  });

  it("adopts a saved plan's NLM setting when the plan is opened", async () => {
    const nlmPlan = {
      ...SAVED_PLAN,
      assumptions: { ...GOAT_DEFAULTS, finance: { nlm_subsidy: true } },
    };
    const user = userEvent.setup();
    await renderLoaded({ savedPlans: [nlmPlan] });

    await user.click((await screen.findAllByRole("button", { name: "Open" }))[0]!);

    expect(screen.getByRole("checkbox", { name: "Apply NLM subsidy" })).toBeChecked();
  });
});

describe("PlannerPage DPR download", () => {
  it("downloads the saved plan's DPR markdown as dpr-<id>.md", async () => {
    const createObjectURL = vi.fn((blob: Blob) => {
      void blob;
      return "blob:dpr";
    });
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(
      (await screen.findAllByRole("button", { name: "Download DPR for Festival plan" }))[0]!,
    );

    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("DPR downloaded."),
    );
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    const blob = createObjectURL.mock.calls[0]![0] as Blob;
    expect(blob.type).toBe("text/markdown");
    expect(await blob.text()).toContain("# DPR — Festival plan");
    const clickedAnchor = click.mock.instances[0] as HTMLAnchorElement;
    expect(clickedAnchor.download).toBe("dpr-7.md");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:dpr");
  });

  it("surfaces the API error detail when the DPR fetch fails", async () => {
    const user = userEvent.setup();
    await renderLoaded({ dprStatus: 500 });

    await user.click(
      (await screen.findAllByRole("button", { name: "Download DPR for Festival plan" }))[0]!,
    );

    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("DPR blew up"));
    expect(toastMocks.success).not.toHaveBeenCalled();
  });
});
