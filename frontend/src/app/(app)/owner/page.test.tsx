/**
 * Cross-farm owner console (ITEM 3, 2026-09-21 playbook): worst-first
 * attention ranking, benchmark window switching, ownership gating (a worker
 * sees the no-access state and never fires the API calls), and the
 * drill-through that switches the active farm context.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server, permissionsHandler } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import OwnerPage from "./page";

const { pushMock, selectFarm, farmsRef } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  selectFarm: vi.fn(),
  farmsRef: {
    current: [
      { id: 1, name: "Calm Farm", timezone: "Asia/Kolkata", role: null },
      { id: 2, name: "Trouble Farm", timezone: "Asia/Kolkata", role: null },
    ] as Array<{ id: number; name: string; timezone: string; role: string | null }>,
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/owner",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("@/lib/auth-context", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useAuth: () => ({ selectFarm, farms: farmsRef.current }),
}));

const OVERVIEW = {
  farms: [
    {
      farm_id: 1,
      farm_name: "Calm Farm",
      timezone: "Asia/Kolkata",
      active_animals: 40,
      overdue_duties: 0,
      todays_duties_pending: 0,
      todays_duties_done: 5,
      kidding_watch: 0,
      movement_restricted: 0,
      open_screening_flags: 0,
      month_income: "1000.00",
      month_expense: "400.00",
      month_net: "600.00",
    },
    {
      farm_id: 2,
      farm_name: "Trouble Farm",
      timezone: "Asia/Kolkata",
      active_animals: 12,
      overdue_duties: 7,
      todays_duties_pending: 3,
      todays_duties_done: 1,
      kidding_watch: 2,
      movement_restricted: 1,
      open_screening_flags: 4,
      month_income: "0.00",
      month_expense: "250.00",
      month_net: "-250.00",
    },
  ],
};

const BENCHMARKS = {
  days: 90,
  farms: [
    {
      farm_id: 1,
      farm_name: "Calm Farm",
      conception_rate: 85.0,
      kid_mortality_rate: 5.0,
      avg_daily_gain_kg: 0.12,
      feed_cost_per_kg_gain: 40.5,
      profit_per_animal_sold: 3000.0,
      animals_sold: 2,
    },
    {
      farm_id: 2,
      farm_name: "Trouble Farm",
      conception_rate: null,
      kid_mortality_rate: null,
      avg_daily_gain_kg: null,
      feed_cost_per_kg_gain: null,
      profit_per_animal_sold: null,
      animals_sold: 0,
    },
  ],
};

let ownerApiHits = 0;

function renderPage() {
  return renderWithProviders(<OwnerPage />, createTestQueryClient());
}

beforeEach(() => {
  pushMock.mockClear();
  selectFarm.mockClear();
  farmsRef.current = [
    { id: 1, name: "Calm Farm", timezone: "Asia/Kolkata", role: null },
    { id: 2, name: "Trouble Farm", timezone: "Asia/Kolkata", role: null },
  ];
  ownerApiHits = 0;
  server.use(
    http.get("/api/owner/overview", () => {
      ownerApiHits += 1;
      return HttpResponse.json(OVERVIEW);
    }),
    http.get("/api/owner/benchmarks", () => {
      ownerApiHits += 1;
      return HttpResponse.json(BENCHMARKS);
    }),
  );
});

describe("OwnerPage", () => {
  it("ranks farms worst-first on the attention board", async () => {
    renderPage();

    const rows = await screen.findAllByRole("row");
    // Header + two farm rows; Trouble Farm (7 overdue) must lead.
    expect(rows[1]).toHaveTextContent("Trouble Farm");
    expect(rows[2]).toHaveTextContent("Calm Farm");
    expect(within(rows[1]).getByText("7")).toBeInTheDocument();
  });

  it("renders benchmark figures with withheld-not-zero dashes", async () => {
    renderPage();

    expect(await screen.findByText("85%")).toBeInTheDocument();
    expect(screen.getByText("5%")).toBeInTheDocument();
    expect(screen.getByText("0.12 kg")).toBeInTheDocument();
    // The empty farm shows dashes, never fabricated zeros. Its benchmark row
    // is the SECOND "Trouble Farm" occurrence (the attention board leads).
    const rows = screen
      .getAllByText("Trouble Farm")
      .map((el) => el.closest<HTMLElement>("tr"))
      .filter((row) => row !== null);
    expect(rows.length).toBe(2);
    expect(
      within(rows[1]).getAllByText((_, element) => element?.textContent === "—").length,
    ).toBeGreaterThanOrEqual(4);
  });

  it("switches the benchmark window", async () => {
    renderPage();
    const user = userEvent.setup();

    await screen.findByText("85%");
    await user.click(screen.getByRole("button", { name: "365d" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "365d" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
  });

  it("drills through by switching the farm context and landing on the dashboard", async () => {
    renderPage();
    const user = userEvent.setup();

    const calmRow = (await screen.findAllByText("Calm Farm"))[0].closest<HTMLElement>("tr");
    expect(calmRow).not.toBeNull();
    await user.click(
      within(calmRow as HTMLElement).getByRole("button", { name: /open farm/i }),
    );

    expect(selectFarm).toHaveBeenCalledWith(1, "Asia/Kolkata");
    expect(pushMock).toHaveBeenCalledWith("/dashboard");
  });

  it("shows owners-only state to a worker and never queries the API", async () => {
    farmsRef.current = [{ id: 3, name: "Employed Farm", timezone: "Asia/Kolkata", role: "Worker" }];
    server.use(permissionsHandler(["dashboard.view", "animals.view"]));
    renderPage();

    expect(
      await screen.findByText("The cross-farm console is available to farm owners only."),
    ).toBeInTheDocument();
    // Both queries are disabled for non-owners: no request may leave the
    // page, not even one that would 403.
    expect(ownerApiHits).toBe(0);
    expect(screen.queryByText("Trouble Farm")).not.toBeInTheDocument();
  });

  it("serves an owner who is currently switched into a farm they do not own", async () => {
    // owns-any-farm (role === null on ANY list entry), not the current
    // farm's is_owner: an owner acting as a manager of another farm keeps
    // the console.
    farmsRef.current = [
      { id: 9, name: "Managed Farm", timezone: "Asia/Kolkata", role: "Manager" },
      { id: 1, name: "Calm Farm", timezone: "Asia/Kolkata", role: null },
    ];
    server.use(permissionsHandler(["dashboard.view"]));
    renderPage();

    expect((await screen.findAllByText("Trouble Farm")).length).toBeGreaterThan(0);
    expect(ownerApiHits).toBeGreaterThan(0);
  });
});
