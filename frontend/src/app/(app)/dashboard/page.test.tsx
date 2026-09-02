/**
 * Dashboard page: aggregate rendering from a mocked GET /api/dashboard —
 * stat cards, overdue/today's tasks (TaskLink Open vs View behaviour),
 * kiddings/ultrasounds due, move suggestions with cull warning, herd-by-
 * bucket tiles, recent weights formatting — plus the empty farm state,
 * loading/error states, and the dashboard.view permission gate.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AnimalIdentityOut,
  AnimalOut,
  DashboardKiddingDueOut,
  DashboardOut,
  DashboardWeightOut,
  TaskOut,
} from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

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
const TODAY = farmToday();
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
    recurring_series_id: null,
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
    skipped_by_id: null,
    skipped_at: null,
    skip_reason: null,
    rejected_by_id: null,
    rejected_at: null,
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
    birth_type: "TWIN",
    source: "BORN",
    dam_id: null,
    sire_id: null,
    birth_weight: 2.4,
    current_bucket: "RESTING",
    status: "ACTIVE",
    status_date: null,
    sale_price: null,
    purchase_date: null,
    purchase_price: null,
    seller_name: null,
    cull_candidate: false,
    movement_restricted: false,
    restriction_reason: null,
    suspected_scheduled_disease: false,
    suspected_disease: null,
    authority_notified_at: null,
    restriction_cleared_at: null,
    restriction_cleared_by_id: null,
    restriction_clearance_reference: null,
    restriction_version: 0,
    mortality_cause: null,
    mortality_reported_at: null,
    notes: null,
    created_at: "2024-01-10T00:00:00Z",
    ...overrides,
  };
}

function makePayload(overrides: Partial<DashboardOut> = {}): DashboardOut {
  const payload = {
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
  return {
    ...payload,
    todays_tasks_total: overrides.todays_tasks_total ?? payload.todays_tasks.length,
    overdue_tasks_total: overrides.overdue_tasks_total ?? payload.overdue_tasks.length,
    ultrasounds_due_total:
      overrides.ultrasounds_due_total ?? payload.ultrasounds_due.length,
    kiddings_due_total: overrides.kiddings_due_total ?? payload.kiddings_due.length,
    cull_candidates_total:
      overrides.cull_candidates_total ?? payload.cull_candidates.length,
    suggestions_total: overrides.suggestions_total ?? payload.suggestions.length,
    recent_weights_total: overrides.recent_weights_total ?? payload.recent_weights.length,
    preview_limit: overrides.preview_limit ?? 20,
    recent_weights_limit: overrides.recent_weights_limit ?? 10,
  };
}

function makeKiddingDue(
  overrides: Partial<DashboardKiddingDueOut> = {},
): DashboardKiddingDueOut {
  return {
    id: 1,
    doe_id: 11,
    doe_tag: "D-101",
    expected_kidding_date: "2026-08-10",
    ...overrides,
  };
}

type RecentWeightFixture = DashboardWeightOut & {
  animal: AnimalIdentityOut;
  notes: string | null;
};

function makeRecentWeight(
  overrides: Partial<RecentWeightFixture> = {},
): RecentWeightFixture {
  return {
    id: 1,
    date: "2026-08-01",
    weight_kg: 23.456,
    bcs: 3,
    animal: { id: 51, tag_number: "W-051", name: "Malli" },
    notes: null,
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
    makeKiddingDue({ id: 5, doe_id: 11, doe_tag: "D-101", expected_kidding_date: "2026-08-10" }),
    makeKiddingDue({ id: 6, doe_id: 22, doe_tag: "D-022", expected_kidding_date: "2026-08-15" }),
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
    makeRecentWeight({ id: 1, notes: "Post-deworming" }),
    makeRecentWeight({
      id: 2,
      date: "2026-07-28",
      weight_kg: 18.04,
      bcs: null,
      animal: { id: 52, tag_number: "W-052", name: null },
    }),
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
    await screen.findByRole("heading", { name: /— Dashboard/ });

    const statValue = (label: string) =>
      within(screen.getByText(label).parentElement as HTMLElement);

    expect(statValue("Active animals").getByText("42")).toBeInTheDocument();
    expect(statValue("Females").getByText("30")).toBeInTheDocument();
    expect(statValue("Males").getByText("12")).toBeInTheDocument();
  });

  it("shows the all-time sold count from status_totals", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("Sold (all time)")).toBeInTheDocument();
  });

  it("counts tasks due + overdue together in the task stat", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    // 2 due today + 1 overdue = 3.
    const card = screen.getByText("Tasks due + overdue").parentElement as HTMLElement;
    expect(within(card).getByText("3")).toBeInTheDocument();
  });

  it("uses exact totals while clearly labeling bounded dashboard previews", async () => {
    server.use(
      dashboardHandler(
        makePayload({
          ...POPULATED,
          todays_tasks_total: 7,
          overdue_tasks_total: 5,
          ultrasounds_due_total: 6,
          kiddings_due_total: 8,
          suggestions_total: 9,
          cull_candidates_total: 4,
          recent_weights_total: 15,
        }),
      ),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Overdue tasks (5)");
    expect(screen.getByText(/operational previews are capped at 20 rows/)).toHaveTextContent(
      "recent weights are capped at 10",
    );

    const taskCard = screen.getByText("Tasks due + overdue").parentElement as HTMLElement;
    expect(within(taskCard).getByText("12")).toBeInTheDocument();
    expect(screen.getByText("Showing 2 of 7.")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 5.")).toBeInTheDocument();
    expect(screen.getByText("Kiddings due or overdue (8)")).toBeInTheDocument();
    expect(screen.getByText("Showing 2 of 8.")).toBeInTheDocument();
    expect(screen.getByText("Ultrasounds due in 7 days (6)")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 6.")).toBeInTheDocument();
    expect(screen.getByText("Showing 2 of 9 move suggestions.")).toBeInTheDocument();
    expect(screen.getByText(/4 cull candidate/)).toBeInTheDocument();
    expect(screen.getByText(/Showing 2 of 15 recent weight records/)).toBeInTheDocument();
  });

  it("renders the overdue card with formatted date and whole days late", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Deworm batch 4");

    expect(screen.getByText("Overdue tasks (1)")).toBeInTheDocument();
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
      "/health/new?task=1&returnTo=%2Fdashboard",
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
      "/animals/11?returnTo=%2Fdashboard",
    );
    expect(within(row).getByText("due 10 Aug 2026")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Record" })).toHaveAttribute(
      "href",
      "/kidding/new?breeding_id=5",
    );
  });

  it("hides the Record shortcut without kidding.manage", async () => {
    // breeding.view is required for the rows to be returned at all; the point
    // of this case is that kidding.manage alone gates the Record action.
    server.use(
      permissionsHandler(["dashboard.view", "breeding.view"]),
      dashboardHandler(POPULATED),
    );
    renderWithProviders(<DashboardPage />);

    // The row and doe link stay; only the Record action is gone.
    await screen.findByText("D-101");
    const row = rowOf("D-101");
    expect(within(row).queryByRole("link", { name: "Record" })).not.toBeInTheDocument();
  });

  it("renders the required bounded doe identity", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("D-022");

    expect(screen.getByRole("link", { name: "D-022" })).toHaveAttribute(
      "href",
      "/animals/22?returnTo=%2Fdashboard",
    );
  });

  it("lists ultrasounds due with a 'Record result' action link", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Ultrasound D-101");

    const row = rowOf("Ultrasound D-101");
    expect(within(row).getByRole("link", { name: "Record result" })).toHaveAttribute(
      "href",
      "/breeding/1/ultrasound?returnTo=%2Fdashboard",
    );
  });

  // REGRESSION — the card spans overdue through +7 days, but the "View"
  // fallback (shown when the viewer lacks breeding.manage) always pointed at
  // ?tab=today, a tab that cannot contain a past- or future-dated duty.
  it("sends the ultrasound fallback to the tab that holds the duty", async () => {
    server.use(
      permissionsHandler(["dashboard.view", "tasks.view"]),
      dashboardHandler(
        makePayload({
          ultrasounds_due: [
            ULTRASOUND_TASK,
            makeTask({
              id: 5,
              title: "Ultrasound D-102",
              category: "ULTRASOUND",
              action_url: "/breeding/2/ultrasound",
              due_date: addDays(TODAY, 5),
            }),
            makeTask({
              id: 6,
              title: "Ultrasound D-103",
              category: "ULTRASOUND",
              action_url: "/breeding/3/ultrasound",
              due_date: THREE_DAYS_AGO,
            }),
          ],
        }),
      ),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Ultrasound D-101");

    const hrefOf = (title: string) =>
      within(rowOf(title)).getByRole("link", { name: "View" }).getAttribute("href");
    expect(hrefOf("Ultrasound D-101")).toBe("/tasks?tab=today");
    expect(hrefOf("Ultrasound D-102")).toBe("/tasks?tab=upcoming");
    expect(hrefOf("Ultrasound D-103")).toBe("/tasks?tab=overdue");
  });

  it("renders move suggestions with display name (tag · name), reason and target bucket", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Ready to move (2)");

    const row = rowOf("G-077 · Lakshmi");
    expect(within(row).getByRole("link", { name: "G-077 · Lakshmi" })).toHaveAttribute(
      "href",
      "/animals/41?returnTo=%2Fdashboard",
    );
    expect(within(row).getByText("Weight 25.0 kg ≥ threshold")).toBeInTheDocument();
    expect(within(row).getByText("Finisher")).toBeInTheDocument();
  });

  it("renders a suggestion for an unnamed animal with just its tag", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Ready to move (2)");

    expect(screen.getByRole("link", { name: "G-078" })).toHaveAttribute(
      "href",
      "/animals/42?returnTo=%2Fdashboard",
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
    // REGRESSION — the tile counts ACTIVE animals only, so the link must scope
    // the destination list the same way; /animals defaults to every status.
    expect(lact).toHaveAttribute("href", "/animals?bucket=LACT&status=ACTIVE");
    expect(within(lact).getByText("12")).toBeInTheDocument();
    expect(within(lact).getByText("LACT")).toBeInTheDocument();
  });

  it("URL-encodes bucket codes containing spaces in the tile link", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: "Herd by bucket" });

    expect(screen.getByRole("link", { name: /Kids 0–3 months/ })).toHaveAttribute(
      "href",
      "/animals?bucket=KID%200-3&status=ACTIVE",
    );
  });

  it("identifies and links recent weights while formatting their facts", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: "Recent weight records" });

    const row = rowOf("Post-deworming");
    expect(within(row).getByText("1 Aug 2026")).toBeInTheDocument();
    // 23.456 → toFixed(1) rounds to 23.5.
    expect(within(row).getByText("23.5 kg")).toBeInTheDocument();
    expect(within(row).getByText("3")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "W-051 · Malli" })).toHaveAttribute(
      "href",
      "/animals/51?returnTo=%2Fdashboard",
    );
  });

  it("renders '—' for a missing BCS and note", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: "Recent weight records" });

    const row = rowOf("18.0 kg");
    expect(within(row).getAllByText("—")).toHaveLength(2);
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
    await screen.findByRole("heading", { name: /— Dashboard/ });

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
    await screen.findByRole("heading", { name: /— Dashboard/ });

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

  it("shows 'No kiddings due.' / 'No ultrasounds due.' for both kiddings and ultrasounds due", async () => {
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(screen.getByText("No kiddings due.")).toBeInTheDocument();
    expect(screen.getByText("No ultrasounds due.")).toBeInTheDocument();
  });

  it("shows 'No suggestions.' and no cull warning", async () => {
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("No move suggestions.")).toBeInTheDocument();
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
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(screen.getByRole("heading", { name: "Herd by bucket" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Recent weight records" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("DashboardPage — recent weights withheld vs. empty", () => {
  // REGRESSION — a caller without animals.view got recent_weights_total:
  // null (withheld), but the page rendered the same "No weight records yet"
  // copy a genuinely empty farm gets, misrepresenting a permission gate as
  // verified-empty data.
  it("shows a permission notice, not the empty-state copy, when recent_weights_total is withheld (null)", async () => {
    server.use(
      permissionsHandler(["dashboard.view"]),
      dashboardHandler({ ...makePayload(), recent_weights_total: null }),
    );
    renderWithProviders(<DashboardPage />);

    expect(
      await screen.findByText("Weight records require animal access."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No weight records yet/)).not.toBeInTheDocument();
  });

  it("shows the genuine empty-state copy when recent_weights_total is a real 0", async () => {
    server.use(dashboardHandler(makePayload({ recent_weights_total: 0 })));
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText(/No weight records yet/)).toBeInTheDocument();
    expect(
      screen.queryByText("Weight records require animal access."),
    ).not.toBeInTheDocument();
  });
});

describe("DashboardPage — loading, error and permission states", () => {
  it("shows 'Loading…' while the dashboard request is in flight", async () => {
    server.use(http.get("/api/dashboard", () => new Promise<Response>(() => {})));

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("Active animals")).not.toBeInTheDocument();
  });

  it("surfaces the API error detail when the dashboard request fails", async () => {
    let attempts = 0;
    server.use(
      http.get("/api/dashboard", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "dashboard aggregate failed" }, { status: 500 })
          : HttpResponse.json(POPULATED);
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("dashboard aggregate failed");
    await user.click(screen.getByRole("button", { name: "Retry dashboard" }));
    expect(
      await screen.findByRole("heading", { name: "Test Goat Farm — Dashboard" }),
    ).toBeInTheDocument();
    expect(attempts).toBe(2);
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

  it("shows a permission error instead of misreporting no access", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 }),
      ),
    );

    renderWithProviders(<DashboardPage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
  });

  it("fetches the dashboard when dashboard.view is the only permission held", async () => {
    server.use(permissionsHandler(["dashboard.view"]), dashboardHandler(POPULATED));

    renderWithProviders(<DashboardPage />);

    expect(
      await screen.findByRole("heading", { name: "Test Goat Farm — Dashboard" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Action unavailable").length).toBeGreaterThan(0);
    // Without breeding.view the API returns no kiddings; the card must say so
    // rather than assert an empty herd.
    expect(screen.getByText("Kiddings require breeding access.")).toBeInTheDocument();
    expect(screen.queryByText("D-101")).not.toBeInTheDocument();
    // Move suggestions carry animal identity and the exact latest weight, so
    // the API withholds them without animals.view too.
    expect(
      screen.getByText("Move suggestions require animal and breeding access."),
    ).toBeInTheDocument();
    expect(screen.queryByText("G-077 · Lakshmi")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
