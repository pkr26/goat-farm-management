import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskOut } from "@/api/generated/models";
import { addDays, farmToday } from "@/lib/format";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import TasksPage from "./page";

const nav = vi.hoisted(() => {
  const state = { search: "" };
  const applyUrl = (url: string) => {
    state.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
  };
  const push = vi.fn((url: string) => applyUrl(url));
  const replace = vi.fn((url: string) => applyUrl(url));
  return {
    state,
    push,
    replace,
    router: { push, replace, prefetch: vi.fn() },
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(nav.state.search),
  useParams: () => ({}),
}));

const TODAY = farmToday();

type Totals = {
  today: number;
  overdue: number;
  upcoming: number;
  awaiting: number;
  completed: number;
};

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

function hrefParams(mock: ReturnType<typeof vi.fn>): URLSearchParams {
  const href = String(mock.mock.calls.at(-1)?.[0] ?? "");
  return new URL(href, "https://goatfarm.invalid").searchParams;
}

describe("TasksPage independent pagination", () => {
  let totals: Totals;
  let seenParams: URLSearchParams[];

  beforeEach(() => {
    nav.state.search = "";
    nav.push.mockClear();
    nav.replace.mockClear();
    totals = { today: 137, overdue: 127, upcoming: 222, awaiting: 88, completed: 143 };
    seenParams = [];
    server.use(
      permissionsHandler([
        "tasks.view",
        "tasks.complete",
        "tasks.verify",
        "animals.view",
        "health.manage",
      ]),
      http.get("/api/tasks", ({ request }) => {
        const params = new URL(request.url).searchParams;
        seenParams.push(params);
        const offset = (tab: keyof Totals) => Number(params.get(`${tab}_offset`) ?? 0);
        const page = (tab: keyof Totals, task: TaskOut) =>
          offset(tab) < totals[tab] ? [task] : [];
        const activeLimit = Number(params.get("active_limit") ?? 50);
        const completedLimit = Number(params.get("completed_limit") ?? 50);
        return HttpResponse.json({
          today: page("today", makeTask({ id: 1, title: "Today row" })),
          overdue: page(
            "overdue",
            makeTask({
              id: 2,
              title: "Overdue row",
              due_date: addDays(TODAY, -2),
              animal_id: 7,
              animal_tag: "G-007",
              action_url: "/health/new?task=2",
            }),
          ),
          upcoming: page(
            "upcoming",
            makeTask({ id: 3, title: "Upcoming row", due_date: addDays(TODAY, 7) }),
          ),
          awaiting: page(
            "awaiting",
            makeTask({
              id: 4,
              title: "Awaiting row",
              status: "DONE",
              needs_verification: true,
              completed_by_id: 999,
              completed_at: "2026-08-01T12:00:00Z",
            }),
          ),
          completed: page(
            "completed",
            makeTask({
              id: 5,
              title: "Completed row",
              status: "VERIFIED",
              completed_at: "2026-08-01T12:00:00Z",
              verified_at: "2026-08-01T13:00:00Z",
            }),
          ),
          today_total: totals.today,
          today_offset: offset("today"),
          overdue_total: totals.overdue,
          overdue_offset: offset("overdue"),
          upcoming_total: totals.upcoming,
          upcoming_offset: offset("upcoming"),
          awaiting_total: totals.awaiting,
          awaiting_offset: offset("awaiting"),
          active_limit: activeLimit,
          completed_total: totals.completed,
          completed_limit: completedLimit,
          completed_offset: offset("completed"),
        });
      }),
    );
  });

  it("uses exact totals and preserves every tab offset while navigating independently", async () => {
    nav.state.search =
      "tab=overdue&today_offset=50&overdue_offset=50&upcoming_offset=100&awaiting_offset=50&completed_offset=50&from=dashboard";
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);

    await screen.findByText("Overdue row");
    expect(screen.getByRole("tab", { name: "Today (137)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Overdue (127)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Upcoming (222)" })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "Awaiting verification (88)" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Completed (143)" })).toBeInTheDocument();

    const initial = seenParams[0];
    expect(initial.get("active_limit")).toBe("50");
    expect(initial.get("completed_limit")).toBe("50");
    expect(initial.get("today_offset")).toBe("50");
    expect(initial.get("overdue_offset")).toBe("50");
    expect(initial.get("upcoming_offset")).toBe("100");
    expect(initial.get("awaiting_offset")).toBe("50");
    expect(initial.get("completed_offset")).toBe("50");

    const overduePager = screen.getByRole("navigation", {
      name: "overdue tasks pagination",
    });
    expect(overduePager).toHaveTextContent("Showing 51–100 of 127 overdue tasks");

    const expectedReturnTo =
      "/tasks?tab=overdue&today_offset=50&overdue_offset=50&upcoming_offset=100&awaiting_offset=50&completed_offset=50&from=dashboard";
    const openFormHref = within(screen.getByText("Overdue row").closest("tr")!).getByRole(
      "link",
      { name: "Open form" },
    ).getAttribute("href");
    const animalHref = screen.getByRole("link", { name: "G-007" }).getAttribute("href");
    expect(new URL(openFormHref!, "https://goatfarm.invalid").searchParams.get("returnTo")).toBe(
      expectedReturnTo,
    );
    expect(new URL(animalHref!, "https://goatfarm.invalid").searchParams.get("returnTo")).toBe(
      expectedReturnTo,
    );

    await user.click(within(overduePager).getByRole("button", { name: "Next" }));
    await waitFor(() => expect(seenParams.at(-1)?.get("overdue_offset")).toBe("100"));
    expect(seenParams.at(-1)?.get("today_offset")).toBe("50");
    expect(seenParams.at(-1)?.get("upcoming_offset")).toBe("100");
    expect(seenParams.at(-1)?.get("awaiting_offset")).toBe("50");
    expect(seenParams.at(-1)?.get("completed_offset")).toBe("50");
    expect(hrefParams(nav.push).get("from")).toBe("dashboard");
    expect(await screen.findByText("Showing 101–127 of 127 overdue tasks")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Today (137)" }));
    const todayPager = await screen.findByRole("navigation", { name: "today tasks pagination" });
    await user.click(within(todayPager).getByRole("button", { name: "Previous" }));
    await waitFor(() => expect(seenParams.at(-1)?.get("today_offset")).toBe("0"));
    expect(seenParams.at(-1)?.get("overdue_offset")).toBe("100");
    expect(seenParams.at(-1)?.get("upcoming_offset")).toBe("100");
    expect(seenParams.at(-1)?.get("awaiting_offset")).toBe("50");
    expect(seenParams.at(-1)?.get("completed_offset")).toBe("50");
  });

  it("normalizes malformed and over-limit offsets before they reach the API", async () => {
    totals = { today: 0, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 };
    nav.state.search =
      "tab=overdue&today_offset=-1&overdue_offset=abc&upcoming_offset=1.5&awaiting_offset=1000001&completed_offset=9007199254740992&from=dashboard";
    renderWithProviders(<TasksPage />);

    await screen.findByText("No overdue tasks.");
    expect(seenParams[0].get("today_offset")).toBe("0");
    expect(seenParams[0].get("overdue_offset")).toBe("0");
    expect(seenParams[0].get("upcoming_offset")).toBe("0");
    expect(seenParams[0].get("awaiting_offset")).toBe("0");
    expect(seenParams[0].get("completed_offset")).toBe("0");
    await waitFor(() =>
      expect(nav.replace).toHaveBeenLastCalledWith("/tasks?tab=overdue&from=dashboard"),
    );
  });

  it("accepts the exact maximum offset while canonicalizing one malformed sibling", async () => {
    totals = {
      today: 1_000_001,
      overdue: 1,
      upcoming: 1,
      awaiting: 1,
      completed: 1,
    };
    nav.state.search =
      "tab=today&today_offset=1000000&overdue_offset=oops&from=dashboard";
    renderWithProviders(<TasksPage />);

    await waitFor(() => expect(seenParams.length).toBeGreaterThan(0));
    expect(seenParams[0].get("today_offset")).toBe("1000000");
    expect(seenParams[0].get("overdue_offset")).toBe("0");
    await waitFor(() =>
      expect(nav.replace).toHaveBeenLastCalledWith(
        "/tasks?tab=today&today_offset=1000000&from=dashboard",
      ),
    );
  });

  it("accepts non-page-aligned integer offsets allowed by the API contract", async () => {
    nav.state.search = "tab=today&today_offset=17";
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);

    const todayPager = await screen.findByRole("navigation", {
      name: "today tasks pagination",
    });
    expect(seenParams[0].get("today_offset")).toBe("17");
    expect(todayPager).toHaveTextContent("Showing 18–67 of 137 today tasks");

    await user.click(within(todayPager).getByRole("button", { name: "Next" }));
    await waitFor(() => expect(seenParams.at(-1)?.get("today_offset")).toBe("67"));
    expect(hrefParams(nav.push).get("today_offset")).toBe("67");
  });

  it("clamps all stale bucket offsets in one replacement after totals shrink", async () => {
    totals = { today: 101, overdue: 151, upcoming: 51, awaiting: 51, completed: 101 };
    nav.state.search =
      "tab=overdue&today_offset=100&overdue_offset=150&upcoming_offset=50&awaiting_offset=50&completed_offset=100&from=dashboard";
    const { queryClient } = renderWithProviders(<TasksPage />);
    await screen.findByText("Overdue row");
    nav.replace.mockClear();

    totals = { today: 49, overdue: 51, upcoming: 0, awaiting: 10, completed: 75 };
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["/api/tasks"] });
    });

    await waitFor(() => expect(nav.replace).toHaveBeenCalledTimes(1));
    expect(nav.replace).toHaveBeenLastCalledWith(
      "/tasks?tab=overdue&overdue_offset=50&completed_offset=50&from=dashboard",
    );
    await waitFor(() => {
      const latest = seenParams.at(-1);
      expect(latest?.get("today_offset")).toBe("0");
      expect(latest?.get("overdue_offset")).toBe("50");
      expect(latest?.get("upcoming_offset")).toBe("0");
      expect(latest?.get("awaiting_offset")).toBe("0");
      expect(latest?.get("completed_offset")).toBe("50");
    });
  });

  it("hides the awaiting tab and its controls without verification permission", async () => {
    server.use(permissionsHandler(["tasks.view"]));
    nav.state.search = "tab=awaiting&awaiting_offset=50&overdue_offset=50";
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);

    await screen.findByText("Today row");
    expect(screen.queryByRole("tab", { name: /Awaiting verification/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "awaiting verification tasks pagination" }),
    ).not.toBeInTheDocument();
    const todayPager = screen.getByRole("navigation", { name: "today tasks pagination" });
    expect(seenParams[0].get("awaiting_offset")).toBe("50");

    await user.click(within(todayPager).getByRole("button", { name: "Next" }));
    await waitFor(() => expect(seenParams.at(-1)?.get("today_offset")).toBe("50"));
    expect(hrefParams(nav.push).get("tab")).toBe("today");
    expect(hrefParams(nav.push).get("awaiting_offset")).toBe("50");
  });
});
