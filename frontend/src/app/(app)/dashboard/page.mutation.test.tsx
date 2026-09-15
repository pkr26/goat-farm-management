/**
 * Dashboard page — mutation-hardening suite for the branches Stryker flagged:
 * the permissions-error retry, the overdue preview cap, the first-run
 * welcome card (content, permission filtering, all-zero status gate), the
 * bounded-preview banner limits, empty/overdue kiddings and ultrasound
 * markers, suggestion bucket labels and the herd-by-bucket donut.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday, formatDate } from "@/lib/format";

import DashboardPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const TODAY = farmToday();
const THREE_DAYS_AGO = addDays(TODAY, -3);

function makeTask(overrides: Record<string, unknown>) {
  return {
    id: 1,
    title: "Task",
    title_key: null,
    title_args: {},
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
    created_at: "2026-01-01T00:00:00Z",
    created_by_id: null,
    action_url: null,
    ...overrides,
  };
}

function makePayload(overrides: Record<string, unknown> = {}) {
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
    todays_tasks_total: payload.todays_tasks.length,
    overdue_tasks_total: payload.overdue_tasks.length,
    ultrasounds_due_total: payload.ultrasounds_due.length,
    kiddings_due_total: payload.kiddings_due.length,
    cull_candidates_total: payload.cull_candidates.length,
    suggestions_total: payload.suggestions.length,
    recent_weights_total: payload.recent_weights.length,
    preview_limit: 20,
    recent_weights_limit: 10,
    ...overrides,
  };
}

function dashboardHandler(payload: Record<string, unknown>) {
  return http.get("/api/dashboard", () => HttpResponse.json(payload));
}

async function renderLoaded() {
  renderWithProviders(<DashboardPage />);
  // The loading branches also render the "Dashboard" heading, so wait for the
  // loading copy to clear before asserting on data.
  await waitFor(() => expect(screen.queryByText(/Loading/)).not.toBeInTheDocument());
  return screen.findByRole("heading", { name: /— Dashboard|^Dashboard$/ });
}

describe("DashboardPage — permissions failure", () => {
  it("offers a retry that reloads the permissions probe", async () => {
    let permsCalls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        permsCalls += 1;
        return permsCalls === 1
          ? HttpResponse.json({ detail: "nope" }, { status: 500 })
          : HttpResponse.json({ is_owner: true, permissions: ["dashboard.view"] });
      }),
      dashboardHandler(makePayload()),
    );
    const user = userEvent.setup();
    renderWithProviders(<DashboardPage />);

    const retry = await screen.findByRole("button", { name: /retry/i });
    expect(screen.queryByRole("heading", { name: /Dashboard/ })).not.toBeInTheDocument();
    await user.click(retry);
    expect(await screen.findByRole("heading", { name: /Dashboard/ })).toBeInTheDocument();
  });
});

describe("DashboardPage — overdue preview cap", () => {
  beforeEach(() => {
    const overdue = Array.from({ length: 7 }, (_, i) =>
      makeTask({ id: i + 1, title: `Overdue ${i + 1}`, due_date: THREE_DAYS_AGO }),
    );
    server.use(dashboardHandler(makePayload({ overdue_tasks: overdue })));
  });

  it("shows five rows and names the exact remainder", async () => {
    await renderLoaded();

    // 5 overdue body rows (+1 for the table's own header-less row per card).
    expect(screen.getByText("Overdue tasks (7)")).toBeInTheDocument();
    expect(screen.getAllByText(/3d late/)).toHaveLength(5);
    expect(screen.getByText("Showing 5 of 7.")).toBeInTheDocument();
    expect(screen.queryByText("Overdue 6")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View all overdue tasks" })).toHaveAttribute(
      "href",
      "/tasks?tab=overdue",
    );
  });
});

describe("DashboardPage — first-run welcome card", () => {
  it("teaches the three-step workflow with linked CTAs", async () => {
    server.use(dashboardHandler(makePayload()));
    await renderLoaded();

    expect(screen.getByText("Welcome to your new farm")).toBeInTheDocument();
    expect(
      screen.getByText("Set up in three steps — everything else on this page fills in as you go."),
    ).toBeInTheDocument();
    expect(screen.getByText("Add your first animals")).toBeInTheDocument();
    expect(screen.getByText("Record a purchase batch")).toBeInTheDocument();
    expect(screen.getByText("Log today's work")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add animal" })).toHaveAttribute("href", "/animals/new");
    expect(screen.getByRole("link", { name: "Open purchases" })).toHaveAttribute(
      "href",
      "/purchases",
    );
    expect(screen.getByRole("link", { name: "Open tasks" })).toHaveAttribute("href", "/tasks");
  });

  it("hides the card once any status register entry is non-zero", async () => {
    server.use(
      dashboardHandler(
        makePayload({ total_active: 0, status_totals: { SOLD: 3 } }),
      ),
    );
    await renderLoaded();

    expect(screen.queryByText("Welcome to your new farm")).not.toBeInTheDocument();
  });

  it("hides the card without animals.view even on an all-zero herd", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: false, permissions: ["dashboard.view", "tasks.view"] }),
      ),
      dashboardHandler(makePayload()),
    );
    await renderLoaded();

    expect(screen.queryByText("Welcome to your new farm")).not.toBeInTheDocument();
  });

  it("drops the add-animal step when animals.create is missing", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({
          is_owner: false,
          permissions: ["dashboard.view", "animals.view", "purchases.view", "tasks.view"],
        }),
      ),
      dashboardHandler(makePayload()),
    );
    await renderLoaded();

    expect(screen.getByText("Welcome to your new farm")).toBeInTheDocument();
    expect(screen.queryByText("Add your first animals")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Add animal" })).not.toBeInTheDocument();
    expect(screen.getByText("Record a purchase batch")).toBeInTheDocument();
  });

  it("shows the card — with all three step descriptions — when the status register is present but all zero", async () => {
    server.use(dashboardHandler(makePayload({ status_totals: { ACTIVE: 0, SOLD: 0 } })));
    await renderLoaded();

    expect(screen.getByText("Welcome to your new farm")).toBeInTheDocument();
    expect(
      screen.getByText("Tag every goat you own — tags are how the whole farm connects."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Buying animals? A batch auto-creates their 45-day quarantine plan."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Feeding, health events and tasks live here — check in each morning."),
    ).toBeInTheDocument();
  });

  it("never treats a herd with active animals as new", async () => {
    server.use(dashboardHandler(makePayload({ total_active: 5 })));
    await renderLoaded();

    expect(screen.queryByText("Welcome to your new farm")).not.toBeInTheDocument();
  });
});

describe("DashboardPage — bounded-preview banner", () => {
  it("states both server limits when any list is truncated", async () => {
    server.use(
      dashboardHandler(
        makePayload({
          total_active: 9,
          todays_tasks: [makeTask({ id: 1 })],
          todays_tasks_total: 4,
          preview_limit: 5,
          recent_weights_limit: 3,
        }),
      ),
    );
    await renderLoaded();

    expect(
      screen.getByText(
        "Dashboard operational previews are capped at 5 rows; recent weights are capped at 3. Exact totals are shown, and the operational links open the full registers.",
      ),
    ).toBeInTheDocument();
  });

  it("stays silent when nothing was truncated", async () => {
    server.use(
      dashboardHandler(
        makePayload({
          total_active: 9,
          todays_tasks: [makeTask({ id: 1 })],
        }),
      ),
    );
    await renderLoaded();

    expect(screen.queryByText(/previews are capped/)).not.toBeInTheDocument();
  });
});

describe("DashboardPage — parturition and ultrasound lists", () => {
  it("renders the factual empty state for a goat farm with nothing due", async () => {
    server.use(dashboardHandler(makePayload()));
    await renderLoaded();

    expect(screen.getByText("Kiddings due or overdue (0)")).toBeInTheDocument();
    expect(screen.getByText("No kiddings due.")).toBeInTheDocument();
    expect(
      screen.getByText("Confirmed pregnancies appear here as their due dates approach."),
    ).toBeInTheDocument();
  });

  it("marks an overdue expected kidding and skips a null due date", async () => {
    server.use(
      dashboardHandler(
        makePayload({
          kiddings_due: [
            { id: 1, doe_id: 11, doe_tag: "D-101", expected_kidding_date: THREE_DAYS_AGO },
            { id: 2, doe_id: 12, doe_tag: "D-102", expected_kidding_date: null },
          ],
        }),
      ),
    );
    await renderLoaded();

    expect(screen.getAllByText(/3d late/)[0]).toBeInTheDocument();
    const nullRow = screen.getByText("D-102").closest("tr") as HTMLElement;
    expect(within(nullRow).queryByText(/late/)).not.toBeInTheDocument();
  });

  it("marks an overdue ultrasound with its whole days late", async () => {
    server.use(
      dashboardHandler(
        makePayload({
          ultrasounds_due: [makeTask({ id: 9, title: "Ultrasound D-101", due_date: THREE_DAYS_AGO })],
        }),
      ),
    );
    await renderLoaded();

    expect(screen.getByText("(3d late)")).toBeInTheDocument();
    // The marker is separated from the due date by a real space.
    const cell = screen.getByText("Ultrasound D-101").closest("td")
      ?.previousElementSibling as HTMLElement;
    expect(cell.textContent).toBe(`${formatDate(THREE_DAYS_AGO)} (3d late)`);
  });
});

describe("DashboardPage — task stat tint ladder", () => {
  function tasksStatIcon() {
    const card = screen.getByText("Tasks due + overdue").closest(
      '[data-slot="card"]',
    ) as HTMLElement;
    return card.querySelector('[data-slot="card-content"] > span') as HTMLElement;
  }

  it("paints the tasks stat warning when work is due but nothing is overdue", async () => {
    server.use(
      dashboardHandler(makePayload({ total_active: 4, todays_tasks: [makeTask({ id: 1 })] })),
    );
    await renderLoaded();

    expect(tasksStatIcon().className).toContain("bg-warning-tint");
    expect(tasksStatIcon().className).not.toContain("bg-destructive");
  });

  it("keeps the neutral tint when the task register is empty", async () => {
    server.use(dashboardHandler(makePayload({ total_active: 4 })));
    await renderLoaded();

    expect(tasksStatIcon().className).toContain("bg-muted");
    expect(tasksStatIcon().className).not.toContain("bg-warning-tint");
    expect(tasksStatIcon().className).not.toContain("bg-destructive");
  });
});

describe("DashboardPage — farm-less heading and withheld breeding copy", () => {
  it("explains the missing breeding permission instead of an empty list", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: false, permissions: ["dashboard.view", "animals.view"] }),
      ),
      dashboardHandler(makePayload()),
    );
    await renderLoaded();

    expect(screen.getByText("Kiddings require breeding access.")).toBeInTheDocument();
    expect(
      screen.getByText("Ask an admin to grant breeding.view to see kiddings due here."),
    ).toBeInTheDocument();
  });
});

describe("DashboardPage — suggestions and herd-by-bucket", () => {
  beforeEach(() => {
    server.use(
      dashboardHandler(
        makePayload({
          total_active: 30,
          suggestions: [
            {
              animal: { id: 41, tag_number: "G-077", name: "Lakshmi" },
              to: "PREGNANCY_EARLY",
              reason: "Pregnancy confirmed",
            },
          ],
          buckets: [
            { code: "PREGNANCY_EARLY", name: "Pregnancy A", count: 12 },
            { code: "KID_0_3", name: "Kids 0–3 mo", count: 6 },
          ],
        }),
      ),
    );
  });

  it("resolves the suggestion's target bucket to its species label", async () => {
    await renderLoaded();

    const row = screen.getByText("G-077 · Lakshmi").closest("tr") as HTMLElement;
    expect(within(row).getByText("Pregnancy A")).toBeInTheDocument();
    expect(within(row).queryByText("PREGNANCY_EARLY")).not.toBeInTheDocument();
  });

  it("renders the donut centred on the active total with no legend", async () => {
    await renderLoaded();

    expect(screen.getByText("active animals")).toBeInTheDocument();
    // The ring keeps an accessible name built from the bucket slices.
    expect(
      screen.getByRole("img", { name: "Distribution: Pregnancy A 12, Kids 0–3 mo 6" }),
    ).toBeInTheDocument();
    // showLegend is false: the donut itself must not repeat bucket names.
    const donut = screen
      .getByText("active animals")
      .closest('[data-slot="card-content"]')?.querySelector("svg");
    expect(donut).not.toBeNull();
    expect(donut?.textContent).toBe("");
    // The side list still shows both buckets with counts.
    expect(screen.getAllByText("Pregnancy A").length).toBeGreaterThan(0);
    expect(screen.getByText("Kids 0–3 mo")).toBeInTheDocument();
  });
});
