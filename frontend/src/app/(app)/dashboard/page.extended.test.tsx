/**
 * Dashboard page — a second pass over the claims the aggregate itself makes:
 * the urgency tint of the task stat, the truncation notices (and their
 * absence once a section is complete), the withheld sentinels the payload
 * carries alongside the permission cache, the late marker on overdue
 * kiddings, the proportions of the herd-by-bucket bars, and the styling and
 * permission gating of the shortcuts each card offers.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AnimalIdentityOut,
  DashboardKiddingDueOut,
  DashboardOut,
  DashboardWeightOut,
  FarmOut,
  MoveSuggestionOut,
  TaskOut,
} from "@/api/generated/models";
import { TEST_FARMS, permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday, formatDate } from "@/lib/format";

import DashboardPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

/** The bare "Dashboard" title covers an active farm the membership list does
 * not contain. AuthProvider's own invariant (the selection always comes from
 * the list it just applied) never produces that pairing, so the one test that
 * exercises the fallback substitutes the list the page reads. */
const authOverride = vi.hoisted(() => ({ farms: null as FarmOut[] | null }));

vi.mock("@/lib/auth-context", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth-context")>();
  return {
    ...actual,
    useAuth() {
      const value = actual.useAuth();
      return authOverride.farms === null ? value : { ...value, farms: authOverride.farms };
    },
  };
});

afterEach(() => {
  authOverride.farms = null;
});

/** Farm-calendar fixture dates: every due date is compared against the
 * selected farm's today, so fixtures are built relative to it. */
const TODAY = farmToday();
const THREE_DAYS_AGO = addDays(TODAY, -3);
const FIVE_DAYS_AGO = addDays(TODAY, -5);
const NEXT_WEEK = addDays(TODAY, 7);

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

function identity(overrides: Partial<AnimalIdentityOut> = {}): AnimalIdentityOut {
  return { id: 1, tag_number: "G-001", name: null, ...overrides };
}

function makeKiddingDue(
  overrides: Partial<DashboardKiddingDueOut> = {},
): DashboardKiddingDueOut {
  return {
    id: 1,
    doe_id: 11,
    doe_tag: "D-101",
    expected_kidding_date: NEXT_WEEK,
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
    weight_kg: 23.4,
    bcs: 3,
    animal: identity({ id: 51, tag_number: "W-051", name: "Malli" }),
    notes: null,
    ...overrides,
  };
}

function makeSuggestion(overrides: Partial<MoveSuggestionOut> = {}): MoveSuggestionOut {
  return {
    animal: identity({ id: 41, tag_number: "G-077", name: "Lakshmi" }),
    to: "FINISHER",
    reason: "Weight 25.0 kg ≥ threshold",
    ...overrides,
  };
}

/** Totals default to the preview length, i.e. a section that is fully listed;
 * a test that wants a capped preview raises the total it cares about. */
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

/** Every section fully listed: each preview holds all of its total. */
const COMPLETE = makePayload({
  buckets: [
    { code: "LACT", name: "Lactating does", count: 12 },
    { code: "DRY", name: "Dry does", count: 18 },
  ],
  total_active: 30,
  sex_counts: { F: 20, M: 10 },
  status_totals: { SOLD: 3 },
  todays_tasks: [FORM_TASK, PLAIN_TASK],
  overdue_tasks: [OVERDUE_TASK],
  ultrasounds_due: [ULTRASOUND_TASK],
  kiddings_due: [
    makeKiddingDue({ id: 5, doe_id: 11, doe_tag: "D-101" }),
    makeKiddingDue({
      id: 6,
      doe_id: 22,
      doe_tag: "D-022",
      expected_kidding_date: addDays(TODAY, 3),
    }),
  ],
  cull_candidates: [identity({ id: 31, tag_number: "G-031" })],
  suggestions: [
    makeSuggestion(),
    makeSuggestion({
      animal: identity({ id: 42, tag_number: "G-078" }),
      to: "YEARLING",
      reason: "Age 12 months ≥ threshold",
    }),
  ],
  recent_weights: [
    makeRecentWeight({ id: 1, notes: "Post-deworming" }),
    makeRecentWeight({
      id: 2,
      animal: identity({ id: 52, tag_number: "W-052" }),
      bcs: null,
    }),
  ],
});

/** The dashboard's row DTO types doe_tag as required, but the breeding and
 * kidding DTOs it is assembled from both allow a null tag, and the card falls
 * back to the doe id rather than rendering an unidentifiable row. */
function untaggedKiddingDue(
  overrides: Partial<DashboardKiddingDueOut> = {},
): DashboardKiddingDueOut {
  return { ...makeKiddingDue(overrides), doe_tag: null as unknown as string };
}

function dashboardHandler(payload: DashboardOut) {
  return http.get("/api/dashboard", () => HttpResponse.json(payload));
}

function rowOf(text: string): HTMLElement {
  const row = screen.getByText(text).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

/** The tinted icon badge sitting beside a stat card's label. */
function statIcon(label: string): HTMLElement {
  const icon = screen.getByText(label).parentElement?.previousElementSibling;
  expect(icon).not.toBeNull();
  return icon as HTMLElement;
}

/** The proportional fill inside a herd-by-bucket tile. */
function bucketBar(name: string | RegExp): HTMLElement {
  const bar = screen
    .getByRole("link", { name })
    .querySelector<HTMLElement>("span.bg-primary");
  expect(bar).not.toBeNull();
  return bar as HTMLElement;
}

describe("DashboardPage — first-run onboarding", () => {
  it("shows the welcome card with linked steps for a truly empty farm", async () => {
    server.use(dashboardHandler(makePayload()));
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(
      screen.getByText("Welcome to your new farm"),
    ).toBeInTheDocument();
    const addFirst = screen.getByRole("link", { name: "Add animal" });
    expect(addFirst).toHaveAttribute("href", "/animals/new");
    expect(screen.getByRole("link", { name: "Open purchases" })).toHaveAttribute(
      "href",
      "/purchases",
    );
    expect(screen.getByRole("link", { name: "Open tasks" })).toHaveAttribute(
      "href",
      "/tasks",
    );
  });

  it("hides the welcome card once any animal exists, even an inactive one", async () => {
    // A wound-down herd (all sold) must not be greeted as "new".
    server.use(
      dashboardHandler(
        makePayload({ total_active: 0, status_totals: { SOLD: 4 } }),
      ),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Sold (all time)");
    expect(screen.queryByText("Welcome to your new farm")).not.toBeInTheDocument();
  });

  it("hides the welcome card without animals.view", async () => {
    server.use(
      permissionsHandler(["dashboard.view"]),
      dashboardHandler(makePayload()),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });
    expect(screen.queryByText("Welcome to your new farm")).not.toBeInTheDocument();
  });
});

describe("DashboardPage — task stat urgency", () => {
  it("tints the task stat red while anything is overdue", async () => {
    server.use(dashboardHandler(COMPLETE));
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    const icon = statIcon("Tasks due + overdue");
    expect(icon).toHaveClass("bg-destructive/10", "text-destructive");
    expect(icon).not.toHaveClass("bg-warning-tint");
    expect(icon).not.toHaveClass("bg-muted");
  });

  it("tints the task stat amber when work is due today but nothing is overdue", async () => {
    server.use(
      dashboardHandler(
        makePayload({ ...COMPLETE, overdue_tasks: [], overdue_tasks_total: 0 }),
      ),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    const icon = statIcon("Tasks due + overdue");
    expect(icon).toHaveClass("bg-warning-tint", "text-warning-tint-foreground");
    expect(icon).not.toHaveClass("bg-red-100");
    expect(icon).not.toHaveClass("bg-muted");
  });

  it("leaves the task stat untinted when nothing is due or overdue", async () => {
    server.use(
      dashboardHandler(
        makePayload({
          ...COMPLETE,
          todays_tasks: [],
          todays_tasks_total: 0,
          overdue_tasks: [],
          overdue_tasks_total: 0,
        }),
      ),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    const icon = statIcon("Tasks due + overdue");
    expect(icon).toHaveClass("bg-muted", "text-muted-foreground");
    expect(icon).not.toHaveClass("bg-red-100");
    expect(icon).not.toHaveClass("bg-amber-100");
  });
});

describe("DashboardPage — fully listed sections", () => {
  it("states each section's exact total and claims no truncation", async () => {
    server.use(dashboardHandler(COMPLETE));
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(screen.getByText("Overdue tasks (1)")).toBeInTheDocument();
    expect(screen.getByText("Today's tasks (2)")).toBeInTheDocument();
    expect(screen.getByText("Kiddings due or overdue (2)")).toBeInTheDocument();
    expect(screen.getByText("Ultrasounds due in 7 days (1)")).toBeInTheDocument();
    expect(screen.getByText("Ready to move (2)")).toBeInTheDocument();
    // Every preview holds its whole total, so no section may claim to be
    // showing a subset, and the capped-preview banner must stay away.
    expect(screen.queryAllByText(/Showing/)).toHaveLength(0);
    expect(
      screen.queryByText(/operational previews are capped/),
    ).not.toBeInTheDocument();
  });
});

describe("DashboardPage — capped preview banner", () => {
  it.each([
    ["today's tasks", { todays_tasks_total: 5 }],
    ["overdue tasks", { overdue_tasks_total: 4 }],
    ["ultrasounds due", { ultrasounds_due_total: 3 }],
    ["kiddings due", { kiddings_due_total: 6 }],
    ["cull candidates", { cull_candidates_total: 4 }],
    ["move suggestions", { suggestions_total: 7 }],
    ["recent weights", { recent_weights_total: 9 }],
  ] as const)(
    "warns that previews are capped when only %s is truncated",
    async (_section, totals) => {
      server.use(dashboardHandler(makePayload({ ...COMPLETE, ...totals })));
      renderWithProviders(<DashboardPage />);
      await screen.findByRole("heading", { name: /— Dashboard/ });

      expect(
        screen.getByText(/operational previews are capped at 20 rows/),
      ).toHaveTextContent("recent weights are capped at 10");
    },
  );
});

describe("DashboardPage — truncated previews", () => {
  it("links the overdue notice to the full overdue list", async () => {
    server.use(
      dashboardHandler(makePayload({ ...COMPLETE, overdue_tasks_total: 5 })),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    const notice = screen.getByText(/Showing 1 of 5\./);
    expect(notice).toHaveTextContent("Showing 1 of 5. View all overdue tasks");
    expect(
      within(notice).getByRole("link", { name: "View all overdue tasks" }),
    ).toHaveAttribute("href", "/tasks?tab=overdue");
  });

  it("keeps the overdue count but drops its link without tasks.view", async () => {
    server.use(
      permissionsHandler(["dashboard.view"]),
      dashboardHandler(makePayload({ ...COMPLETE, overdue_tasks_total: 5 })),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(screen.getByText(/Showing 1 of 5\./)).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "View all overdue tasks" }),
    ).not.toBeInTheDocument();
  });

  it("links the kidding notice to the kidding register", async () => {
    server.use(
      dashboardHandler(makePayload({ ...COMPLETE, kiddings_due_total: 6 })),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    const notice = screen.getByText(/Showing 2 of 6\./);
    expect(notice).toHaveTextContent("Showing 2 of 6. View the kidding register");
    expect(
      within(notice).getByRole("link", { name: "View the kidding register" }),
    ).toHaveAttribute("href", "/kidding");
  });

  it("keeps the kidding count but drops its link without kidding.view", async () => {
    server.use(
      permissionsHandler(["dashboard.view", "breeding.view", "animals.view"]),
      dashboardHandler(makePayload({ ...COMPLETE, kiddings_due_total: 6 })),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(screen.getByText(/Showing 2 of 6\./)).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "View the kidding register" }),
    ).not.toBeInTheDocument();
  });
});

describe("DashboardPage — withheld sections vs. real counts", () => {
  // The permission cache and the dashboard payload are separate queries and
  // drift by design; either side saying "withheld" has to win.
  it("treats a null cull total as withheld even while breeding.view is cached", async () => {
    // Spread the finished payload: makePayload's `??` defaults would read a
    // withheld null as "the preview holds everything".
    server.use(dashboardHandler({ ...COMPLETE, cull_candidates_total: null }));
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(screen.getByText("Kiddings require breeding access.")).toBeInTheDocument();
    // No count may be asserted for a section the payload withheld.
    expect(screen.getByText("Kiddings due or overdue")).toBeInTheDocument();
    expect(screen.queryByText("Kiddings due or overdue (2)")).not.toBeInTheDocument();
    expect(screen.queryByText("D-101")).not.toBeInTheDocument();
  });

  it("treats a null recent-weights total as withholding the move suggestions too", async () => {
    server.use(dashboardHandler({ ...COMPLETE, recent_weights_total: null }));
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(
      screen.getByText("Move suggestions require animal and breeding access."),
    ).toBeInTheDocument();
    expect(screen.getByText("Ready to move")).toBeInTheDocument();
    expect(screen.queryByText("Ready to move (2)")).not.toBeInTheDocument();
    expect(screen.queryByText("G-077 · Lakshmi")).not.toBeInTheDocument();
    expect(screen.getByText("Weight records require animal access.")).toBeInTheDocument();
  });

  it("never leaks a bounded suggestion count into the withheld notice", async () => {
    server.use(
      permissionsHandler(["dashboard.view", "tasks.view"]),
      dashboardHandler({ ...COMPLETE, suggestions_total: 9, recent_weights_total: null }),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(
      screen.getByText("Move suggestions require animal and breeding access."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Showing 2 of 9 move suggestions/)).not.toBeInTheDocument();
  });

  it("names the breeding register in the cull banner only with breeding.view", async () => {
    server.use(
      permissionsHandler(["dashboard.view", "animals.view"]),
      dashboardHandler(
        makePayload({
          ...COMPLETE,
          cull_candidates: [identity({ id: 31, tag_number: "G-031" }), identity({ id: 32, tag_number: "G-032" })],
          cull_candidates_total: 2,
        }),
      ),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(
      screen.getByText("2 cull candidate(s) — flagged in breeding records"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "see breeding page" }),
    ).not.toBeInTheDocument();
  });
});

describe("DashboardPage — due dates and late markers", () => {
  it("reads the overdue task's date and days-late marker as one line", async () => {
    server.use(dashboardHandler(COMPLETE));
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Deworm batch 4");

    const cell = within(rowOf("Deworm batch 4")).getByText(formatDate(THREE_DAYS_AGO));
    expect(cell).toHaveTextContent(`${formatDate(THREE_DAYS_AGO)} (3d late)`);
  });

  it("marks an overdue kidding with the whole days it is late", async () => {
    server.use(
      dashboardHandler(
        makePayload({
          ...COMPLETE,
          kiddings_due: [makeKiddingDue({ expected_kidding_date: FIVE_DAYS_AGO })],
          kiddings_due_total: 1,
        }),
      ),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByText("D-101");

    const cell = within(rowOf("D-101")).getByText(`due ${formatDate(FIVE_DAYS_AGO)}`);
    expect(cell).toHaveTextContent(`due ${formatDate(FIVE_DAYS_AGO)} (5d late)`);
  });

  it("leaves kiddings due today or later unmarked", async () => {
    server.use(
      dashboardHandler(
        makePayload({
          ...COMPLETE,
          overdue_tasks: [],
          overdue_tasks_total: 0,
          kiddings_due: [
            makeKiddingDue({ id: 5, doe_id: 11, doe_tag: "D-101", expected_kidding_date: TODAY }),
            makeKiddingDue({ id: 6, doe_id: 22, doe_tag: "D-022" }),
          ],
          kiddings_due_total: 2,
        }),
      ),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByText("D-101");

    // A kidding due today is not yet late, and neither is one a week out.
    expect(within(rowOf("D-101")).getByText(`due ${formatDate(TODAY)}`).textContent).toBe(
      `due ${formatDate(TODAY)}`,
    );
    expect(screen.queryByText(/d late/)).not.toBeInTheDocument();
  });
});

describe("DashboardPage — untagged doe rows", () => {
  const untagged = (overrides: Partial<DashboardOut> = {}) => ({
    ...COMPLETE,
    kiddings_due: [untaggedKiddingDue({ id: 5, doe_id: 11 })],
    kiddings_due_total: 1,
    ...overrides,
  });

  it("links an untagged kidding row by its doe id", async () => {
    server.use(dashboardHandler(untagged()));
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(screen.getByRole("link", { name: "Doe #11" })).toHaveAttribute(
      "href",
      "/animals/11?returnTo=%2Fdashboard",
    );
  });

  it("still names an untagged doe when animals.view is missing", async () => {
    server.use(
      permissionsHandler(["dashboard.view", "breeding.view", "kidding.view"]),
      dashboardHandler(untagged()),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    expect(screen.getByText("Doe #11")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Doe #11" })).not.toBeInTheDocument();
  });
});

describe("DashboardPage — herd by bucket", () => {
  it("scales every bucket bar against the largest bucket", async () => {
    server.use(dashboardHandler(COMPLETE));
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: "Herd by bucket" });

    // 12 of the largest 18 → 67%; the largest bucket fills its track.
    expect(bucketBar(/Lactating does/).style.width).toBe("67%");
    expect(bucketBar(/Dry does/).style.width).toBe("100%");
  });

  it("keeps the bars measurable when every bucket is empty", async () => {
    server.use(
      dashboardHandler(
        makePayload({
          ...COMPLETE,
          buckets: [
            { code: "LACT", name: "Lactating does", count: 0 },
            { code: "DRY", name: "Dry does", count: 0 },
          ],
        }),
      ),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: "Herd by bucket" });

    expect(bucketBar(/Lactating does/).style.width).toBe("0%");
    expect(bucketBar(/Dry does/).style.width).toBe("0%");
  });
});

describe("DashboardPage — shortcut styling and gating", () => {
  it("renders every row action as a small outline button", async () => {
    server.use(dashboardHandler(COMPLETE));
    renderWithProviders(<DashboardPage />);
    await screen.findByText("Vaccinate kids");

    const actions = [
      ...screen.getAllByRole("link", { name: "Open" }),
      ...screen.getAllByRole("link", { name: "View" }),
      ...screen.getAllByRole("link", { name: "Record" }),
      ...screen.getAllByRole("link", { name: "Record result" }),
    ];
    expect(actions).toHaveLength(6);
    for (const action of actions) {
      expect(action).toHaveClass("border-border", "bg-background", "h-9");
      expect(action).not.toHaveClass("bg-primary");
      expect(action).toHaveClass("text-[0.8rem]");
    }
  });

  it("renders every card shortcut as a small ghost button", async () => {
    server.use(dashboardHandler(COMPLETE));
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("heading", { name: /— Dashboard/ });

    const shortcuts = [
      screen.getByRole("link", { name: "View all" }),
      ...screen.getAllByRole("link", { name: "Breeding" }),
      screen.getByRole("link", { name: "View herd" }),
    ];
    expect(shortcuts).toHaveLength(4);
    for (const shortcut of shortcuts) {
      expect(shortcut).toHaveClass("hover:bg-muted", "h-9");
      expect(shortcut).not.toHaveClass("bg-primary");
      expect(shortcut).not.toHaveClass("bg-background");
      expect(shortcut).toHaveClass("text-[0.8rem]");
    }
  });

  it("renders the first-animal invite as a small outline button", async () => {
    server.use(
      dashboardHandler(
        makePayload({ ...COMPLETE, recent_weights: [], recent_weights_total: 0 }),
      ),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findByText(/No weight records yet/);

    const invite = screen.getByRole("link", { name: "Add your first animal" });
    expect(invite).toHaveClass("border-border", "bg-background", "h-9");
    expect(invite).not.toHaveClass("bg-primary");
    expect(invite).toHaveClass("text-[0.8rem]");
  });

  it("hides the first-animal invite without animals.create", async () => {
    server.use(
      permissionsHandler(["dashboard.view", "animals.view"]),
      dashboardHandler(
        makePayload({ ...COMPLETE, recent_weights: [], recent_weights_total: 0 }),
      ),
    );
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText(/No weight records yet/)).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Add your first animal" }),
    ).not.toBeInTheDocument();
  });
});

describe("DashboardPage — active farm and permission loading", () => {
  it("holds the loading state until permissions resolve", async () => {
    server.use(http.get("/api/auth/permissions", () => new Promise<Response>(() => {})));
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();
  });

  it("titles the page with the farm the operator actually selected", async () => {
    localStorage.setItem("goatfarm.farmId", "2");
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          TEST_FARMS[0],
          { ...TEST_FARMS[0], id: 2, name: "Hill Side Farm" },
        ]),
      ),
      dashboardHandler(COMPLETE),
    );
    renderWithProviders(<DashboardPage />);

    expect(
      await screen.findByRole("heading", { name: "Hill Side Farm — Dashboard" }),
    ).toBeInTheDocument();
  });

  it("falls back to a bare Dashboard title when the active farm is not listed", async () => {
    authOverride.farms = [];
    server.use(dashboardHandler(COMPLETE));
    renderWithProviders(<DashboardPage />);

    expect(
      await screen.findByRole("heading", { name: "Dashboard" }),
    ).toBeInTheDocument();
  });
});
