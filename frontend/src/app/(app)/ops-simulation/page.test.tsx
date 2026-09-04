/**
 * Ops Simulation page: the daily Buckets & Tasks simulator. The setup card
 * edits the starting herd (one row per animal in a bucket) and posts to
 * /api/ops-sim/run; the results render the day timeline, the selected day's
 * duty schedule, feeding manifest, occupancy, the transition matrix, animal
 * journeys, the explanations and the opt-in Markdown ledger. Dairy farms see
 * the goat-only gate; permission failures keep the page out of reach.
 */

import { screen, waitFor } from "@testing-library/react";
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

const FEED_LINE = {
  day: 1,
  building: "BREEDING",
  recipe: "MAINTENANCE_75_25",
  recipe_display: "Maintenance 75:25",
  heads: 11,
  kg_per_head: 1.2,
  daily_kg: 13.2,
  morning_kg: 5.28,
  afternoon_kg: 2.64,
  night_kg: 5.28,
};

const DAY_1 = {
  day: 1,
  date: "2026-09-03",
  tasks: [
    {
      day: 1,
      date: "2026-09-03",
      time: "06:30",
      category: "FEED",
      building: "FEED_STORE",
      building_name: "Feed Store & Mixing Area",
      role: "FEEDER",
      animals: [],
      headline: "Mix 13.200 kg — Maintenance 75:25",
      detail: "One batch covers all buildings today.",
    },
    {
      day: 1,
      date: "2026-09-03",
      time: "07:15",
      category: "CLEANING",
      building: "BREEDING",
      building_name: "Breeding Bucket",
      role: "CLEANER",
      animals: [],
      headline: "Clean Breeding Bucket — morning (after feeding)",
      detail: "Remove soiled bedding.",
    },
  ],
  moves: [],
  births: [],
  exits: [],
  feeding: [FEED_LINE],
  occupancy: [{ building: "BREEDING", heads: 11 }],
};

const DAY_2 = {
  day: 2,
  date: "2026-09-04",
  tasks: [
    {
      day: 2,
      date: "2026-09-04",
      time: "09:00",
      category: "KIDDING_DUE",
      building: "DELIVERY",
      building_name: "Delivery Ward",
      role: "VET",
      animals: ["P1"],
      headline: "Kidding due: P1",
      detail: "Expected today.",
    },
  ],
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
      live_kids: 1,
      kids: [{ tag: "P1-1", sex: "F", status: "ALIVE" }],
    },
  ],
  exits: [
    { day: 2, date: "2026-09-04", tag: "MK1", kind: "SOLD", reason: "Meat sale" },
  ],
  feeding: [FEED_LINE],
  occupancy: [{ building: "BREEDING", heads: 11 }],
};

const RUN_RESULT = {
  model_version: "1.0.0",
  species: "GOAT",
  start_date: "2026-09-03",
  horizon_days: 3,
  seed: 7,
  head_start: 13,
  days: [DAY_1, DAY_2, DAY_1],
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
      final_status: "ACTIVE",
      final_bucket: "RECOVERY",
      exit_day: null,
      exit_kind: null,
      exit_reason: "",
      hops: [{ day: 2, from_bucket: "DELIVERY", to_bucket: "RECOVERY", context: "kidding" }],
    },
  ],
  transition_counts: [
    { from_bucket: "DELIVERY", to_bucket: "RECOVERY", context: "kidding", count: 1 },
  ],
  totals: {
    feed_kg_by_recipe: { MAINTENANCE_75_25: 39.6 },
    tasks_by_category: { FEED: 9, CLEANING: 8 },
    tasks_by_role: { FEEDER: 9, CLEANER: 8 },
    vet_tasks_by_building: { DELIVERY: 1 },
    building_days: { BREEDING: 3 },
    services: 10,
    conceptions: 9,
    failed_services: 1,
    kids_born_alive: 1,
    kids_born_dead: 0,
    deaths: 0,
    culls: 0,
    sales: 1,
    moves: 1,
  },
  explanations: [
    {
      key: "routine",
      title: "The daily routine",
      explanation: "Seven phases, every day.",
      figures: { horizon_days: 3 },
    },
  ],
  notes: ["Goat farms only in this version (species=GOAT)."],
};

function runHandler(
  body: typeof RUN_RESULT & { ledger?: string | null } = RUN_RESULT,
  requests: Request[] = [],
) {
  return http.post("/api/ops-sim/run", async ({ request }) => {
    requests.push(request);
    return HttpResponse.json({ result: body, ledger: body.ledger ?? null });
  });
}

async function runSimulation(user: ReturnType<typeof userEvent.setup>) {
  const runButton = await screen.findByRole("button", { name: /^Run 90 days/ });
  await user.click(runButton);
  await screen.findByText("Day timeline");
}

describe("OpsSimulationPage — setup and run", () => {
  it("renders the preset toy herd and posts it to the run endpoint", async () => {
    const user = userEvent.setup();
    const requests: Request[] = [];
    server.use(runHandler(RUN_RESULT, requests));
    renderWithProviders(<OpsSimulationPage />);

    // Preset rows are editable in the herd table.
    expect(await screen.findByDisplayValue("D1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("B1")).toBeInTheDocument();

    await runSimulation(user);
    expect(requests).toHaveLength(1);
    const payload = await requests[0].json();
    expect(payload.start_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(payload.horizon_days).toBe(90);
    expect(payload.seed).toBe(2026);
    expect(payload.animals).toHaveLength(11);
    expect(payload.animals[0]).toMatchObject({ tag: "D1", sex: "F", bucket: "BREEDING" });
    expect(payload.include_ledger).toBe(false);
  });

  it("renders the results: stats, day schedule, feeding manifest, occupancy", async () => {
    const user = userEvent.setup();
    server.use(runHandler());
    renderWithProviders(<OpsSimulationPage />);
    await runSimulation(user);

    // Stat cards echo the totals.
    expect(screen.getByText("11 head")).toBeInTheDocument(); // final-day occupancy
    expect(screen.getByText("10 → 9")).toBeInTheDocument(); // services → conceptions

    // Day 1: the schedule table and the feeding manifest.
    expect(screen.getByText("Mix 13.200 kg — Maintenance 75:25")).toBeInTheDocument();
    expect(screen.getByText("Clean Breeding Bucket — morning (after feeding)")).toBeInTheDocument();
    expect(screen.getByText(/Breeding Bucket — Maintenance 75:25/)).toBeInTheDocument();
    expect(screen.getByText("2.6")).toBeInTheDocument(); // afternoon share (1 dp)

    // Journeys, transitions, explanations, notes.
    expect(screen.getByText("Transition matrix")).toBeInTheDocument();
    expect(screen.getByText("Animal journeys")).toBeInTheDocument();
    expect(screen.getByText("How this run works")).toBeInTheDocument();
    expect(screen.getByText(/Goat farms only in this version/)).toBeInTheDocument();
  });

  it("switches the selected day and shows its moves, births and exits", async () => {
    const user = userEvent.setup();
    server.use(runHandler());
    renderWithProviders(<OpsSimulationPage />);
    await runSimulation(user);

    // Day 2 carries the kidding, the move and the sale.
    await user.click(screen.getByRole("button", { name: /^Go to day 2/ }));
    expect(await screen.findByText("Kidding due: P1")).toBeInTheDocument();
    expect(screen.getByText("Delivery Ward → Recovery Ward")).toBeInTheDocument();
    const paragraph = (fragment: string) =>
      screen.getByText(
        (_, element) =>
          element?.tagName === "P" && (element.textContent ?? "").includes(fragment),
      );
    expect(paragraph("kidded 1 live of 1")).toBeInTheDocument();
    expect(paragraph("MK1 — SOLD: Meat sale")).toBeInTheDocument();
  });

  it("downloads the ledger when the run included one", async () => {
    const user = userEvent.setup();
    // jsdom ships no URL.createObjectURL; define it on the real URL object
    // (stubbing the whole global breaks `new URL` for fetch itself).
    const createObjectURL = vi.fn(() => "blob:ledger");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      value: createObjectURL,
      configurable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
    try {
      server.use(
        runHandler({ ...RUN_RESULT, ledger: "# Buckets & Tasks — daily operations ledger\n" }),
      );
      renderWithProviders(<OpsSimulationPage />);

      await user.click(
        await screen.findByRole("button", { name: "Include Markdown ledger" }),
      );
      await runSimulation(user);

      expect(await screen.findByText("Download ledger")).toBeInTheDocument();
      expect(screen.getByText(/Preview the ledger/)).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: /Download the day-by-day ledger/ }));
      expect(createObjectURL).toHaveBeenCalledTimes(1);
    } finally {
      delete (URL as { createObjectURL?: unknown }).createObjectURL;
      delete (URL as { revokeObjectURL?: unknown }).revokeObjectURL;
    }
  });

  it("surfaces a rejected run as a toast and keeps the empty state", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/ops-sim/run", () =>
        HttpResponse.json({ detail: "These inputs produce non-finite results." }, { status: 422 }),
      ),
    );
    renderWithProviders(<OpsSimulationPage />);

    await user.click(await screen.findByRole("button", { name: /^Run 90 days/ }));
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalled());
    expect(toastMocks.error).toHaveBeenCalledWith("These inputs produce non-finite results.");
    expect(screen.getByText("No run yet")).toBeInTheDocument();
  });

  it("refuses to post a herd with duplicate or blank tags", async () => {
    const user = userEvent.setup();
    const requests: Request[] = [];
    server.use(runHandler(RUN_RESULT, requests));
    renderWithProviders(<OpsSimulationPage />);

    const firstTag = await screen.findByDisplayValue("D1");
    await user.clear(firstTag);
    await user.type(firstTag, "B1"); // duplicates the buck

    await user.click(screen.getByRole("button", { name: /^Run 90 days/ }));
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Duplicate tags: B1"));
    expect(requests).toHaveLength(0);
  });

  it("refuses to post a row with a non-numeric bred-days value", async () => {
    const user = userEvent.setup();
    const requests: Request[] = [];
    server.use(runHandler(RUN_RESULT, requests));
    renderWithProviders(<OpsSimulationPage />);

    const bred = await screen.findByLabelText(/^Bred days ago for D1$/);
    await user.clear(bred);
    await user.type(bred, "3 weeks");

    await user.click(screen.getByRole("button", { name: /^Run 90 days/ }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        "D1: bred days ago must be a whole number 0–150.",
      ),
    );
    expect(requests).toHaveLength(0);
  });

  it("refuses to post out-of-bounds per-animal values", async () => {
    const user = userEvent.setup();
    const requests: Request[] = [];
    server.use(runHandler(RUN_RESULT, requests));
    renderWithProviders(<OpsSimulationPage />);

    const age = await screen.findByLabelText(/^Age months for D1$/);
    await user.clear(age);
    await user.type(age, "300");

    await user.click(screen.getByRole("button", { name: /^Run 90 days/ }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("D1: age must be 0–240 months."),
    );
    expect(requests).toHaveLength(0);
  });

  it("mounts the ledger preview only when opened", async () => {
    const user = userEvent.setup();
    server.use(runHandler({ ...RUN_RESULT, ledger: "# LEDGER-BODY-MARKER\n" }));
    renderWithProviders(<OpsSimulationPage />);

    await user.click(await screen.findByRole("button", { name: "Include Markdown ledger" }));
    await runSimulation(user);

    const toggle = await screen.findByRole("button", {
      name: "Preview the ledger (Markdown)",
    });
    // Megabytes of ledger text never enter the DOM while collapsed.
    expect(screen.queryByText(/LEDGER-BODY-MARKER/)).not.toBeInTheDocument();
    await user.click(toggle);
    expect(await screen.findByText(/LEDGER-BODY-MARKER/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Hide the ledger preview" }));
    expect(screen.queryByText(/LEDGER-BODY-MARKER/)).not.toBeInTheDocument();
  });

  it("prefers the server-provided building name on duty rows", async () => {
    const user = userEvent.setup();
    server.use(
      runHandler({
        ...RUN_RESULT,
        days: [
          {
            ...DAY_1,
            tasks: [{ ...DAY_1.tasks[0], building_name: "The Server-Side Mix Station" }],
          },
          DAY_1,
          DAY_1,
        ],
      }),
    );
    renderWithProviders(<OpsSimulationPage />);
    await runSimulation(user);

    expect(await screen.findByText("The Server-Side Mix Station")).toBeInTheDocument();
    expect(screen.queryByText("Feed Store & Mixing Area")).not.toBeInTheDocument();
  });
});

describe("OpsSimulationPage — gating", () => {
  it("shows the goat-only gate on a buffalo dairy farm", async () => {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([{ ...TEST_FARMS[0], farm_type: "BUFFALO_DAIRY" }]),
      ),
    );
    renderWithProviders(<OpsSimulationPage />);
    expect(await screen.findByText("Goat farms only — for now")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Run 90 days/ })).not.toBeInTheDocument();
  });

  it("denies the page without simulation.view", async () => {
    server.use(permissionsHandler(["dashboard.view"]));
    renderWithProviders(<OpsSimulationPage />);
    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
  });
});
