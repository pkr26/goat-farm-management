/**
 * Ops Simulation page — mutation-hunting suite. Each test pins a specific
 * derived behaviour of the page (arithmetic the UI computes client-side,
 * payload shaping, gating, formatting, day-badge logic) so Stryker mutants
 * in those paths cannot survive.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server, TEST_FARMS, permissionsHandler } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import OpsSimulationPage from "./page";

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/ops-simulation",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function feedLine(day: number, kgPerHead: number, heads: number) {
  const daily = heads * kgPerHead;
  return {
    day,
    building: "BREEDING",
    recipe: "MAINTENANCE_75_25",
    recipe_display: "Maintenance 75:25",
    heads,
    kg_per_head: kgPerHead,
    daily_kg: daily,
    morning_kg: daily * 0.4,
    afternoon_kg: daily * 0.2,
    night_kg: daily * 0.4,
  };
}

/** Three days: routine / busy (move+birth+exit) / routine. */
function makeResult() {
  const routineTasks = (day: number) => [
    {
      day,
      date: `2026-09-0${day + 2}`,
      time: "06:30",
      category: "FEED",
      building: "BREEDING",
      building_name: "Breeding Bucket",
      role: "FEEDER",
      animals: [],
      headline: `Feed round ${day}`,
      detail: "",
    },
  ];
  return {
    model_version: "1.0.0",
    species: "GOAT",
    start_date: "2026-09-03",
    horizon_days: 3,
    seed: 11,
    head_start: 4,
    days: [
      {
        day: 1,
        date: "2026-09-03",
        tasks: routineTasks(1),
        moves: [],
        births: [],
        exits: [],
        feeding: [feedLine(1, 1.2, 2)],
        occupancy: [{ building: "BREEDING", heads: 2 }],
      },
      {
        day: 2,
        date: "2026-09-04",
        tasks: routineTasks(2),
        moves: [
          {
            day: 2,
            date: "2026-09-04",
            tag: "P1",
            from_bucket: "DELIVERY",
            to_bucket: "RECOVERY",
            context: "kidding",
            reason: "Kidding recorded",
          },
        ],
        births: [
          {
            day: 2,
            date: "2026-09-04",
            dam_tag: "P1",
            live_kids: 2,
            kids: [
              { tag: "P1-1", sex: "F", status: "ALIVE" },
              { tag: "P1-2", sex: "M", status: "ALIVE" },
            ],
          },
        ],
        exits: [{ day: 2, date: "2026-09-04", tag: "MK1", kind: "SOLD", reason: "Meat sale" }],
        feeding: [feedLine(2, 1.2, 2)],
        occupancy: [{ building: "BREEDING", heads: 2 }],
      },
      {
        day: 3,
        date: "2026-09-05",
        tasks: routineTasks(3),
        moves: [],
        births: [],
        exits: [
          { day: 3, date: "2026-09-05", tag: "D9", kind: "DEAD", reason: "Adult mortality" },
        ],
        feeding: [feedLine(3, 1.5, 3)],
        occupancy: [
          { building: "BREEDING", heads: 2 },
          { building: "RECOVERY", heads: 3 },
        ],
      },
    ],
    journeys: [
      {
        tag: "D1",
        sex: "F",
        born_day: null,
        dam_tag: null,
        start_bucket: "BREEDING",
        final_status: "ACTIVE",
        final_bucket: "BREEDING",
        exit_day: null,
        exit_kind: null,
        exit_reason: "",
        hops: [],
      },
      {
        tag: "P1",
        sex: "F",
        born_day: null,
        dam_tag: null,
        start_bucket: "DELIVERY",
        final_status: "SOLD",
        final_bucket: null,
        exit_day: 9,
        exit_kind: "SOLD",
        exit_reason: "Cull",
        hops: [{ day: 2, from_bucket: "DELIVERY", to_bucket: "RECOVERY", context: "kidding" }],
      },
    ],
    transition_counts: [
      { from_bucket: "DELIVERY", to_bucket: "RECOVERY", context: "kidding", count: 1 },
    ],
    totals: {
      feed_kg_by_recipe: { MAINTENANCE_75_25: 2.4 + 2.4, CREEP: 0.9 },
      tasks_by_category: { FEED: 3 },
      tasks_by_role: { FEEDER: 3 },
      vet_tasks_by_building: {},
      building_days: { BREEDING: 3 },
      services: 2,
      conceptions: 1,
      failed_services: 1,
      kids_born_alive: 2,
      kids_born_dead: 0,
      deaths: 1,
      culls: 0,
      sales: 1,
      moves: 1,
    },
    explanations: [
      { key: "routine", title: "The daily routine", explanation: "Seven phases.", figures: {} },
    ],
    notes: ["Goat farms only in this version (species=GOAT)."],
  };
}

function runHandler(requests: Request[] = [], result = makeResult(), ledger: string | null = null) {
  return http.post("/api/ops-sim/run", async ({ request }) => {
    requests.push(request);
    return HttpResponse.json({ result, ledger });
  });
}

async function run(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /^Run 90 days/ }));
  await screen.findByText("Day timeline");
}

describe("OpsSimulationPage — derived figures", () => {
  it("sums the final-day occupancy for the herd stat", async () => {
    const user = userEvent.setup();
    server.use(runHandler());
    renderWithProviders(<OpsSimulationPage />);
    await run(user);
    // Final day occupancy: 2 + 3 = 5 head.
    expect(screen.getByText("5 head")).toBeInTheDocument();
    expect(screen.getByText("from 4 at the start")).toBeInTheDocument();
  });

  it("sums every recipe for the delivered-feed stat", async () => {
    const user = userEvent.setup();
    server.use(runHandler());
    renderWithProviders(<OpsSimulationPage />);
    await run(user);
    // 4.8 + 0.9 = 5.7 kg; integer-valued totals print without decimals.
    expect(screen.getByText("5.7 kg")).toBeInTheDocument();
    expect(screen.getByText("3 days · 2 recipes")).toBeInTheDocument();
  });

  it("prints services → conceptions and the exit summary exactly", async () => {
    const user = userEvent.setup();
    server.use(runHandler());
    renderWithProviders(<OpsSimulationPage />);
    await run(user);
    expect(screen.getByText("2 → 1")).toBeInTheDocument();
    expect(screen.getByText("2 kids born alive")).toBeInTheDocument();
    expect(screen.getByText("1 died · 0 culled · 1 sold")).toBeInTheDocument();
  });

  it("labels day chips by what happened and routine days as routine", async () => {
    const user = userEvent.setup();
    server.use(runHandler());
    renderWithProviders(<OpsSimulationPage />);
    await run(user);
    expect(
      screen.getByRole("button", { name: "Go to day 1 — routine" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Go to day 2 — 1 moves · 1 births · 1 exits",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Go to day 3 — 1 exits" }),
    ).toBeInTheDocument();
  });

  it("formats kilogram cells at one decimal", async () => {
    const user = userEvent.setup();
    server.use(runHandler());
    renderWithProviders(<OpsSimulationPage />);
    await run(user);
    // Day 1 ration: 2 head × 1.2 kg → shares 0.96 / 0.48 / 0.96 print 1 / 0.5 / 1.
    expect(await screen.findByText("0.5")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Go to day 3 — 1 exits" }));
    // Day 3: 3 head × 1.5 kg → 1.8 / 0.9 / 1.8; the per-head rate prints exact.
    expect(screen.getAllByText("1.8").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("0.9")).toBeInTheDocument();
    expect(screen.getByText("1.5")).toBeInTheDocument();
  });
});

describe("OpsSimulationPage — payload shaping", () => {
  it("rounds ages and days, and sends bred_days_ago only when typed", async () => {
    const user = userEvent.setup();
    const requests: Request[] = [];
    server.use(runHandler(requests));
    renderWithProviders(<OpsSimulationPage />);

    // Mixed-herd preset carries a pregnant doe with bred days and defaults.
    await user.click(
      await screen.findByRole("button", { name: /Mixed herd \(pregnancy \+ quarantine \+ kids\)/ }),
    );
    await user.click(await screen.findByRole("button", { name: /^Run 90 days/ }));
    await screen.findByText("Day timeline");

    const payload = await requests[0].json();
    const p1 = payload.animals.find((animal: { tag: string }) => animal.tag === "P1");
    expect(p1).toMatchObject({
      bucket: "PREGNANCY_LATE",
      bred_days_ago: 120,
      days_in_bucket: 20,
      age_months: 30,
    });
    const d1 = payload.animals.find((animal: { tag: string }) => animal.tag === "D1");
    expect(d1.bred_days_ago).toBeUndefined();
    expect(d1.dependent_kid).toBe(false);
  });

  it("commits only finite horizon edits and clamps the run label", async () => {
    const user = userEvent.setup();
    server.use(runHandler());
    renderWithProviders(<OpsSimulationPage />);

    const horizon = await screen.findByLabelText("Horizon (days)");
    await user.clear(horizon);
    await user.type(horizon, "45");
    expect(await screen.findByRole("button", { name: /^Run 45 days/ })).toBeInTheDocument();

    // A non-finite edit never commits: the field is blank while drafting.
    await user.clear(horizon);
    expect(screen.getByRole("button", { name: /^Run 45 days/ })).toBeInTheDocument();
  });

  it("rejects out-of-bounds horizons client-side without a request", async () => {
    const user = userEvent.setup();
    const requests: Request[] = [];
    server.use(runHandler(requests));
    renderWithProviders(<OpsSimulationPage />);

    const horizon = await screen.findByLabelText("Horizon (days)");
    await user.clear(horizon);
    await user.type(horizon, "500");
    await user.click(screen.getByRole("button", { name: /^Run 500 days/ }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Horizon must be 7–365 days."),
    );
    expect(requests).toHaveLength(0);
  });

  it("adds and removes herd rows", async () => {
    const user = userEvent.setup();
    server.use(runHandler());
    renderWithProviders(<OpsSimulationPage />);

    await screen.findByDisplayValue("D1");
    const tagInputs = () => screen.getAllByLabelText(/^Tag for row /);
    expect(tagInputs()).toHaveLength(11);

    await user.click(screen.getByRole("button", { name: "Add animal" }));
    await waitFor(() => expect(tagInputs()).toHaveLength(12));

    await user.click(screen.getByRole("button", { name: "Remove D10" }));
    await waitFor(() => expect(screen.queryByDisplayValue("D10")).not.toBeInTheDocument());
    expect(tagInputs()).toHaveLength(11);
  });

  it("sends the ledger flag only when the toggle is pressed", async () => {
    const user = userEvent.setup();
    const requests: Request[] = [];
    server.use(runHandler(requests));
    renderWithProviders(<OpsSimulationPage />);

    const toggle = await screen.findByRole("button", { name: "Include Markdown ledger" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    await run(user);
    expect((await requests[0].json()).include_ledger).toBe(false);

    await user.click(screen.getByRole("button", { name: "Include Markdown ledger" }));
    expect(screen.getByRole("button", { name: "Include Markdown ledger" }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("OpsSimulationPage — journeys and gating", () => {
  it("renders hop chains and terminal exits per animal", async () => {
    const user = userEvent.setup();
    server.use(runHandler());
    renderWithProviders(<OpsSimulationPage />);
    await run(user);

    // The journey hop renders as one chip-row: day, [from] → [to], context.
    const journeyRow = screen.getByRole("row", { name: /SOLD on day 9/ });
    const hop = within(journeyRow).getByRole("listitem");
    expect(within(hop).getByText("d2")).toBeInTheDocument();
    expect(within(hop).getByTitle("Delivery Ward")).toBeInTheDocument();
    expect(within(hop).getByTitle("Recovery Ward")).toBeInTheDocument();
    expect(within(hop).getByText("kidding")).toBeInTheDocument();
    expect(screen.getByText(/SOLD on day 9 — Cull/)).toBeInTheDocument();
    expect(screen.getByText(/Breeding Bucket \(active\)/)).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument(); // D1 never moved
  });

  it("keeps the run button disabled while a run is in flight", async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    server.use(
      http.post("/api/ops-sim/run", async () => {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return HttpResponse.json({ result: makeResult(), ledger: null });
      }),
    );
    renderWithProviders(<OpsSimulationPage />);

    const runButton = await screen.findByRole("button", { name: /^Run 90 days/ });
    await user.click(runButton);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^Simulating/ })).toBeDisabled(),
    );
    release?.();
    await screen.findByText("Day timeline");
  });

  it("shows the dairy gate with no run controls", async () => {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([{ ...TEST_FARMS[0], farm_type: "BUFFALO_DAIRY" }]),
      ),
    );
    renderWithProviders(<OpsSimulationPage />);
    expect(await screen.findByText("Goat farms only — for now")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add animal" })).not.toBeInTheDocument();
  });

  it("denies access without simulation.view", async () => {
    server.use(permissionsHandler(["dashboard.view"]));
    renderWithProviders(<OpsSimulationPage />);
    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
  });
});
