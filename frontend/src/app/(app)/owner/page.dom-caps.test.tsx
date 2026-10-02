/**
 * Owner page attention ranking + benchmark windows (2026-10-01 campaign
 * follow-up): the ranking weights are 1 000 : 100 : 1 (overdue duties,
 * open screening flags, pending-today) — each boundary fixture flips the
 * order under exactly one weight mutation — and the window selector offers
 * exactly 30d / 90d / 365d.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import OwnerPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/owner",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const { pushMock, selectFarm, farmsRef } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  selectFarm: vi.fn(),
  farmsRef: { current: [] as { id: number; name: string; timezone: string; role: string | null }[] },
}));

vi.mock("@/lib/auth-context", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useAuth: () => ({ selectFarm, farms: farmsRef.current }),
}));

function farm(
  farmId: number,
  farmName: string,
  counts: { overdue?: number; flags?: number; pending?: number },
) {
  return {
    farm_id: farmId,
    farm_name: farmName,
    timezone: "Asia/Kolkata",
    active_animals: 10,
    overdue_duties: counts.overdue ?? 0,
    todays_duties_pending: counts.pending ?? 0,
    todays_duties_done: 0,
    kidding_watch: 0,
    movement_restricted: 0,
    open_screening_flags: counts.flags ?? 0,
    month_income: "0.00",
    month_expense: "0.00",
    month_net: "0.00",
  };
}

const BENCHMARKS = {
  days: 90,
  farms: [
    {
      farm_id: 1,
      farm_name: "Alpha Farm",
      conception_rate: 85.0,
      kid_mortality_rate: 5.0,
      avg_daily_gain_kg: 0.12,
      feed_cost_per_kg_gain: 40.5,
      profit_per_animal_sold: 3000.0,
      animals_sold: 2,
    },
  ],
};

describe("OwnerPage ranking and benchmark windows", () => {
  beforeEach(() => {
    pushMock.mockClear();
    selectFarm.mockClear();
    farmsRef.current = [
      { id: 1, name: "Alpha Farm", timezone: "Asia/Kolkata", role: null },
      { id: 2, name: "Beta Farm", timezone: "Asia/Kolkata", role: null },
    ];
    server.use(
      http.get("/api/owner/benchmarks", () => HttpResponse.json(BENCHMARKS)),
    );
  });

  /** Renders with the given farms (listed order = API order) and returns the
   *  attention table's farm-name column, top first. */
  async function rankedNames(farms: Record<string, unknown>[]): Promise<string[]> {
    server.use(http.get("/api/owner/overview", () => HttpResponse.json({ farms })));
    renderWithProviders(<OwnerPage />, createTestQueryClient());
    const table = (await screen.findAllByRole("table")).find((t) =>
      within(t).queryByText("Farm"),
    );
    expect(table).toBeDefined();
    const rows = within(table!).getAllByRole("row").slice(1);
    expect(rows.length).toBe(farms.length);
    return rows.map((row) => row.querySelector("td")!.textContent!);
  }

  it("breaks a 1 000-point tie in favour of the listed-first farm (weight +1 flips it)", async () => {
    // Alpha = 0·1000 + 9·100 + 100 = 1 000; Beta = 1·1000 = 1 000 (listed 2nd).
    expect(
      await rankedNames([
        farm(1, "Alpha Farm", { flags: 9, pending: 100 }),
        farm(2, "Beta Farm", { overdue: 1 }),
      ]),
    ).toEqual(["Alpha Farm", "Beta Farm"]);
  });

  it("one overdue duty outranks 9 flags and 99 pending (weight −1 or ÷ flips it)", async () => {
    expect(
      await rankedNames([
        farm(1, "Alpha Farm", { flags: 9, pending: 99 }),
        farm(2, "Beta Farm", { overdue: 1 }),
      ]),
    ).toEqual(["Beta Farm", "Alpha Farm"]);
  });

  it("breaks a 100-point tie in favour of the listed-first farm (weight +1 flips it)", async () => {
    expect(
      await rankedNames([
        farm(1, "Alpha Farm", { pending: 100 }),
        farm(2, "Beta Farm", { flags: 1 }),
      ]),
    ).toEqual(["Alpha Farm", "Beta Farm"]);
  });

  it("one flag outranks 99 pending (weight −1 or ÷ flips it)", async () => {
    expect(
      await rankedNames([
        farm(1, "Alpha Farm", { pending: 99 }),
        farm(2, "Beta Farm", { flags: 1 }),
      ]),
    ).toEqual(["Beta Farm", "Alpha Farm"]);
  });

  it("9 pending plus one flag outranks 100 pending (÷ on the 100 weight flips it)", async () => {
    expect(
      await rankedNames([
        farm(1, "Alpha Farm", { pending: 100 }),
        farm(2, "Beta Farm", { flags: 1, pending: 9 }),
      ]),
    ).toEqual(["Beta Farm", "Alpha Farm"]);
  });

  it("one pending duty still outranks a fully idle farm (− on the pending arm flips it)", async () => {
    expect(
      await rankedNames([
        farm(1, "Alpha Farm", {}),
        farm(2, "Beta Farm", { pending: 1 }),
      ]),
    ).toEqual(["Beta Farm", "Alpha Farm"]);
  });

  it("offers exactly the 30/90/365-day benchmark windows", async () => {
    server.use(http.get("/api/owner/overview", () => HttpResponse.json({ farms: [] })));
    renderWithProviders(<OwnerPage />, createTestQueryClient());
    const group = await screen.findByRole("group");
    const buttons = within(group).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["30d", "90d", "365d"]);
    expect(within(group).getByRole("button", { name: "90d" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
