/**
 * Dashboard page: aggregate rendering from a mocked GET /api/dashboard —
 * stat cards, overdue/today's tasks (TaskLink Open vs View behaviour),
 * kiddings/ultrasounds due, move suggestions with cull warning, herd-by-
 * bucket tiles, recent weights formatting — plus the empty farm state,
 * loading/error states, and the dashboard.view permission gate.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AnimalOut,
  BreedingRecordOut,
  DashboardOut,
  TaskOut,
} from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, utcToday } from "@/lib/format";

import DashboardPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

/** UTC-relative fixture dates: the page compares due dates against
 * utcToday(), so fixtures built from browser-local dates drift
 * one day whenever the local and UTC dates differ. */
const TODAY = utcToday();
const THREE_DAYS_AGO = addDays(TODAY, -3);

function makeTask(overrides: Partial<TaskOut>): TaskOut {
  return {
    id: 1,
    title: "Task",
    due_date: TODAY,
    status: "PENDING",
    category: "OTHER",
    auto_generated: false,
    animal_id: null,
    purchase_batch_id: null,
    breeding_record_id: null,
    assigned_role_id: null,
    assigned_user_id: null,
    recur_days: null,
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
    skipped_by_id: null,
    action_url: null,
    ...overrides,
  };
}

function makeAnimal(overrides: Partial<AnimalOut>): AnimalOut {
  return {
    id: 1,
    tag_number: "G-001",
    name: null,
    breed: "Osmanabadi",
    sex: "F",
    date_of_birth: "2024-01-10",
    estimated_dob: null,
    birth_type: "twin",
    source: "BORN",
    dam_id: null,
    sire_id: null,
    birth_weight: 2.4,
    current_bucket: "LACT",
    status: "ACTIVE",
    status_date: null,
    sale_price: null,
    purchase_date: null,
    purchase_price: null,
    seller_name: null,
    cull_candidate: false,
    notes: null,
    created_at: "2024-01-10T00:00:00Z",
    ...overrides,
  };
}

function makeBreeding(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 11,
    buck_id: 12,
    breeding_date: "2026-03-01",
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: null,
    ultrasound_done: true,
    pregnant: true,
    kid_count_detected: 2,
    expected_kidding_date: "2026-08-10",
    outcome: "PREGNANT",
    doe_tag: "D-101",
    ...overrides,
  };
}

function makePayload(overrides: Partial<DashboardOut> = {}): DashboardOut {
  return {
    buckets: [],
    total_active: 0,
    sex_counts: {},
    status_totals: {},
    todays_tasks: [],
    overdue_tasks: [],
    ultrasounds_due: [],
    kiddings_due: [],
    cull_candidates: [],
    suggestions: [],
    recent_weights: [],
    ...overrides,
  };
}

const FORM_TASK = makeTask({
  id: 1,
  title: "Vaccinate kids",
  category: "VACCINE",
  action_url: "/health/new?task=1",
});
const PLAIN_TASK = makeTask({ id: 2, title: "Clean water troughs" });
const OVERDUE_TASK = makeTask({
  id: 3,
  title: "Deworm batch 4",
  due_date: THREE_DAYS_AGO,
});
const ULTRASOUND_TASK = makeTask({
  id: 4,
  title: "Ultrasound D-101",
  category: "ULTRASOUND",
  action_url: "/breeding/1/ultrasound",
});

const POPULATED = makePayload({
  buckets: [
    { code: "LACT", name: "Lactating does", count: 12 },
    { code: "KID 0-3", name: "Kids 0–3 months", count: 18 },
  ],
  total_active: 42,
  sex_counts: { F: 30, M: 12 },
  status_totals: { SOLD: 7, DEAD: 2 },
  todays_tasks: [FORM_TASK, PLAIN_TASK],
  overdue_tasks: [OVERDUE_TASK],
  ultrasounds_due: [ULTRASOUND_TASK],
  kiddings_due: [
    makeBreeding({ id: 5, doe_id: 11, doe_tag: "D-101", expected_kidding_date: "2026-08-10" }),
    makeBreeding({ id: 6, doe_id: 22, doe_tag: null, expected_kidding_date: "2026-08-15" }),
  ],
  cull_candidates: [makeAnimal({ id: 31, tag_number: "G-031", cull_candidate: true })],
  suggestions: [
    {
      animal: makeAnimal({ id: 41, tag_number: "G-077", name: "Lakshmi" }),
      to: "FINISHER",
      reason: "Weight 25.0 kg ≥ threshold",
    },
    {
      animal: makeAnimal({ id: 42, tag_number: "G-078" }),
      to: "YEARLING",
      reason: "Age 12 months ≥ threshold",
    },
  ],
  recent_weights: [
    { id: 1, date: "2026-08-01", weight_kg: 23.456, bcs: 3.5, notes: "Post-deworming" },
    { id: 2, date: "2026-07-28", weight_kg: 18.04, bcs: null, notes: null },
  ],
});

function dashboardHandler(payload: DashboardOut) {
  return http.get("/api/dashboard", () => HttpResponse.json(payload));
}

function rowOf(text: string): HTMLElement {
  const row = screen.getByText(text).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe("DashboardPage — populated aggregates", () => {
  beforeEach(() => {
    server.use(dashboardHandler(POPULATED));
  });

  it("titles the page with the active farm's name", async () => {
    renderWithProviders(<DashboardPage />);
    expect(
      await screen.findByRole("heading", { name: "Test Goat Farm — Dashboard" }),
    ).toBeInTheDocument();
  });

  it("shows the herd stat cards (active total, sex split)", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /dashboard/i });

    const statValue = (label: string) =>
      within(screen.getByText(label).parentElement as HTMLElement);

    expect(statValue("Active animals").getByText("42")).toBeInTheDocument();
    expect(statValue("Females").getByText("30")).toBeInTheDocument();
    expect(statValue("Males").getByText("12")).toBeInTheDocument();
  });

  it("shows the all-time sold count from status_totals", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /dashboard/i });

    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("Sold (all time)")).toBeInTheDocument();
  });

  it("counts tasks due + overdue together in the task stat", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /dashboard/i });

    // 2 due today + 1 overdue = 3.
    const card = screen.getByText("Tasks due + overdue").parentElement as HTMLElement;
    expect(within(card).getByText("3")).toBeInTheDocument();
  });

  it("renders the overdue card with formatted date and whole days late", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Deworm batch 4");

    expect(screen.getByText("Overdue tasks")).toBeInTheDocument();
    const row = rowOf("Deworm batch 4");
    expect(within(row).getByText(/3d late/)).toBeInTheDocument();
    expect(within(row).getByText(new RegExp(formatDateRe(THREE_DAYS_AGO)))).toBeInTheDocument();
  });

  it("links an overdue task without action_url to the overdue tasks tab", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Deworm batch 4");

    const row = rowOf("Deworm batch 4");
    expect(within(row).getByRole("link", { name: "View" })).toHaveAttribute(
      "href",
      "/tasks?tab=overdue",
    );
  });

  it("lists today's tasks", async () => {
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByText("Vaccinate kids")).toBeInTheDocument();
    expect(screen.getByText("Clean water troughs")).toBeInTheDocument();
  });

  it("form-linked task opens its action_url instead of a plain View link", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Vaccinate kids");

    const row = rowOf("Vaccinate kids");
    expect(within(row).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/health/new?task=1",
    );
    expect(within(row).queryByRole("link", { name: "View" })).not.toBeInTheDocument();
  });

  it("plain task falls back to a View link to the today tab", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Clean water troughs");

    const row = rowOf("Clean water troughs");
    expect(within(row).getByRole("link", { name: "View" })).toHaveAttribute(
      "href",
      "/tasks?tab=today",
    );
  });

  it("lists kiddings due with a doe link and a Record shortcut", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("D-101");

    const row = rowOf("D-101");
    expect(within(row).getByRole("link", { name: "D-101" })).toHaveAttribute(
      "href",
      "/animals/11",
    );
    expect(within(row).getByText("due 10 Aug 2026")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Record" })).toHaveAttribute(
      "href",
      "/kidding/new?breeding_id=5",
    );
  });

  it("hides the Record shortcut without kidding.manage", async () => {
    server.use(permissionsHandler(["dashboard.view"]), dashboardHandler(POPULATED));
    renderWithProviders(<DashboardPage />);

    // The row and doe link stay; only the Record action is gone.
    await screen.findByText("D-101");
    const row = rowOf("D-101");
    expect(within(row).queryByRole("link", { name: "Record" })).not.toBeInTheDocument();
  });

  it("falls back to 'Doe #<id>' when the breeding record has no doe tag", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Doe #22");

    expect(screen.getByRole("link", { name: "Doe #22" })).toHaveAttribute(
      "href",
      "/animals/22",
    );
  });

  it("lists ultrasounds due with a 'Record result' action link", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Ultrasound D-101");

    const row = rowOf("Ultrasound D-101");
    expect(within(row).getByRole("link", { name: "Record result" })).toHaveAttribute(
      "href",
      "/breeding/1/ultrasound",
    );
  });

  it("renders move suggestions with display name (tag · name), reason and target bucket", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Ready to move (2)");

    const row = rowOf("G-077 · Lakshmi");
    expect(within(row).getByRole("link", { name: "G-077 · Lakshmi" })).toHaveAttribute(
      "href",
      "/animals/41",
    );
    expect(within(row).getByText("Weight 25.0 kg ≥ threshold")).toBeInTheDocument();
    expect(within(row).getByText("→ FINISHER")).toBeInTheDocument();
  });

  it("renders a suggestion for an unnamed animal with just its tag", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Ready to move (2)");

    expect(screen.getByRole("link", { name: "G-078" })).toHaveAttribute(
      "href",
      "/animals/42",
    );
  });

  it("warns about cull candidates with a link to the breeding page", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText(/1 cull candidate\(s\)/);

    expect(screen.getByRole("link", { name: "see breeding page" })).toHaveAttribute(
      "href",
      "/breeding",
    );
  });

  it("renders herd-by-bucket tiles linking to the filtered animals list", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: "Herd by bucket" });

    const lact = screen.getByRole("link", { name: /Lactating does/ });
    expect(lact).toHaveAttribute("href", "/animals?bucket=LACT");
    expect(within(lact).getByText("12")).toBeInTheDocument();
    expect(within(lact).getByText("LACT")).toBeInTheDocument();
  });

  it("URL-encodes bucket codes containing spaces in the tile link", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: "Herd by bucket" });

    expect(screen.getByRole("link", { name: /Kids 0–3 months/ })).toHaveAttribute(
      "href",
      "/animals?bucket=KID%200-3",
    );
  });

  it("formats recent weights: 1-decimal kg, BCS, notes and formatted date", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: "Recent weight records" });

    const row = rowOf("Post-deworming");
    expect(within(row).getByText("1 Aug 2026")).toBeInTheDocument();
    // 23.456 → toFixed(1) rounds to 23.5.
    expect(within(row).getByText("23.5 kg")).toBeInTheDocument();
    expect(within(row).getByText("3.5")).toBeInTheDocument();
  });

  it("renders '—' for a missing BCS and an empty cell for missing notes", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: "Recent weight records" });

    const row = rowOf("18.0 kg");
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(within(row).getByText("28 Jul 2026")).toBeInTheDocument();
  });

  it("sends the bearer token and active farm id with the dashboard request", async () => {
    let authorization: string | null = null;
    let farmHeader: string | null = null;
    server.use(
      http.get("/api/dashboard", ({ request }) => {
        authorization = request.headers.get("Authorization");
        farmHeader = request.headers.get("X-Farm-Id");
        return HttpResponse.json(POPULATED);
      }),
    );

    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /dashboard/i });

    expect(authorization).toBe("Bearer test-access-token");
    expect(farmHeader).toBe("1");
  });
});

/** "5 Aug 2026"-style regex fragment for a YYYY-MM-DD input. */
function formatDateRe(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  return `${d} ${months[m - 1]} ${y}`;
}

describe("DashboardPage — empty farm", () => {
  beforeEach(() => {
    server.use(dashboardHandler(makePayload()));
  });

  it("shows zeroed stat cards with fallback 0s for missing counts", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /dashboard/i });

    for (const label of [
      "Active animals",
      "Females",
      "Males",
      "Sold (all time)",
      "Tasks due + overdue",
    ]) {
      const card = screen.getByText(label).parentElement as HTMLElement;
      expect(within(card).getByText("0")).toBeInTheDocument();
    }
  });

  it("shows 'Nothing due today.' and no overdue card", async () => {
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("Nothing due today.")).toBeInTheDocument();
    expect(screen.queryByText("Overdue tasks")).not.toBeInTheDocument();
  });

  it("shows 'None.' for both kiddings and ultrasounds due", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /dashboard/i });

    expect(screen.getAllByText("None.")).toHaveLength(2);
  });

  it("shows 'No suggestions.' and no cull warning", async () => {
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("No suggestions.")).toBeInTheDocument();
    expect(screen.getByText("Ready to move (0)")).toBeInTheDocument();
    expect(screen.queryByText(/cull candidate/)).not.toBeInTheDocument();
  });

  it("invites the user to add their first animal when there are no weights", async () => {
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText(/No weight records yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add your first animal" })).toHaveAttribute(
      "href",
      "/animals/new",
    );
  });

  it("renders the bucket and weights sections without any rows", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /dashboard/i });

    expect(screen.getByRole("heading", { name: "Herd by bucket" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Recent weight records" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("DashboardPage — loading, error and permission states", () => {
  it("shows 'Loading…' while the dashboard request is in flight", async () => {
    server.use(http.get("/api/dashboard", () => new Promise<Response>(() => {})));

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /dashboard/i })).not.toBeInTheDocument();
  });

  it("surfaces the API error detail when the dashboard request fails", async () => {
    server.use(
      http.get("/api/dashboard", () =>
        HttpResponse.json({ detail: "dashboard aggregate failed" }, { status: 500 }),
      ),
    );

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("dashboard aggregate failed")).toBeInTheDocument();
  });

  it("shows the generic message on a network failure", async () => {
    server.use(http.get("/api/dashboard", () => HttpResponse.error()));

    renderWithProviders(<DashboardPage />);

    expect(
      await screen.findByText("Could not load the dashboard."),
    ).toBeInTheDocument();
  });

  it("denies access without the dashboard.view permission and never fetches", async () => {
    let dashboardFetches = 0;
    server.use(
      permissionsHandler(["animals.view", "tasks.view"]),
      http.get("/api/dashboard", () => {
        dashboardFetches += 1;
        return HttpResponse.json(POPULATED);
      }),
    );

    renderWithProviders(<DashboardPage />);

    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
    expect(dashboardFetches).toBe(0);
    expect(screen.queryByRole("heading", { name: /dashboard/i })).not.toBeInTheDocument();
  });

  it("fetches the dashboard when dashboard.view is the only permission held", async () => {
    server.use(permissionsHandler(["dashboard.view"]), dashboardHandler(POPULATED));

    renderWithProviders(<DashboardPage />);

    expect(
      await screen.findByRole("heading", { name: "Test Goat Farm — Dashboard" }),
    ).toBeInTheDocument();
  });
});
