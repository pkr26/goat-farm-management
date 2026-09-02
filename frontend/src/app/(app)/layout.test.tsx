/**
 * (app) shell layout: header (brand, active farm, switch-farm link, user,
 * logout) and the permission-filtered nav — every nav item renders only
 * when the user holds its permission on the active farm (mirrors SPEC's
 * RBAC rules: e.g. Team requires team.manage). Also covers the loading
 * and no-farm redirect states.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { StrictMode, useState } from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "@/lib/auth-context";
import { permissionsHandler, server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

// The server wrapper only reads the sidebar cookie; every behavioural
// assertion targets the client shell it renders.
import { AppLayoutClient as AppLayout } from "./app-layout-client";

const { pushMock, replaceMock, navState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  navState: { pathname: "/dashboard", search: "" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => navState.pathname,
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const toastMock = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

beforeAll(() => {
  // jsdom has no matchMedia; the sidebar's useIsMobile hook needs it.
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
});

const ALL_NAV_LABELS = [
  "Dashboard",
  "Animals",
  "Buckets",
  "Breeding",
  "Kidding",
  "Health",
  "Purchases",
  "Feeding",
  "Tasks",
  "Finance",
  "Planner",
  "Simulation",
  "Reports",
  "Team",
];

function navLinks() {
  return screen
    .getAllByRole("link")
    .filter((el) => el.closest('[data-slot="sidebar-content"]'));
}

/** The sidebar group whose heading is `label`, so a test can assert which
 *  items an operator finds under that heading. */
function navGroup(label: string) {
  const group = screen.getByText(label).closest('[data-slot="sidebar-group"]');
  if (!group) throw new Error(`no sidebar group titled "${label}"`);
  return group as HTMLElement;
}

function navGroupItems(label: string) {
  return within(navGroup(label))
    .getAllByRole("link")
    .map((link) => link.textContent);
}

function FarmScopedDraft() {
  const { refreshFarms } = useAuth();
  const [draft, setDraft] = useState("");
  return (
    <div>
      <label htmlFor="farm-scoped-draft">Farm-scoped draft</label>
      <input
        id="farm-scoped-draft"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <button type="button" onClick={() => void refreshFarms()}>
        refresh memberships
      </button>
    </div>
  );
}

function LayoutMountedAfterBootstrap() {
  const { loading } = useAuth();
  return loading ? null : (
    <StrictMode>
      <AppLayout defaultOpen={true}>{null}</AppLayout>
    </StrictMode>
  );
}

describe("AppLayout — header", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    toastMock.success.mockClear();
    toastMock.error.mockClear();
    navState.pathname = "/dashboard";
    navState.search = "";
  });

  it("renders brand, active farm name, switch-farm link and the user's name", async () => {
    renderWithProviders(
      <AppLayout defaultOpen={true}>
        <p>page body</p>
      </AppLayout>,
    );

    expect(await screen.findByText("Test Goat Farm")).toBeInTheDocument();
    expect(
      await screen.findByRole("link", { name: "Herdly — go to Dashboard" }),
    ).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("link", { name: /^Switch farm/ })).toHaveAttribute(
      "href",
      "/farm-select?returnTo=%2Fdashboard",
    );
    expect(screen.getByText(TEST_USER.name as string)).toBeInTheDocument();
    expect(screen.getByText("page body")).toBeInTheDocument();
  });

  it("includes the current detail route and query in the switch-farm return state", async () => {
    navState.pathname = "/tasks";
    navState.search = "?tab=overdue";
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    expect(await screen.findByRole("link", { name: /^Switch farm/ })).toHaveAttribute(
      "href",
      "/farm-select?returnTo=%2Ftasks%3Ftab%3Doverdue",
    );
  });

  it("remounts page state when a membership refresh replaces the active farm", async () => {
    let membershipChanged = false;
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json(
          membershipChanged
            ? [
                {
                  id: 2,
                  name: "Replacement Farm",
                  location: null,
                  timezone: "America/Phoenix",
                  role: null,
                },
              ]
            : [
                {
                  id: 1,
                  name: "Original Farm",
                  location: null,
                  timezone: "Asia/Kolkata",
                  role: null,
                },
                {
                  id: 2,
                  name: "Replacement Farm",
                  location: null,
                  timezone: "America/Phoenix",
                  role: null,
                },
              ],
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <AppLayout defaultOpen={true}>
        <FarmScopedDraft />
      </AppLayout>,
    );
    await screen.findByText("Original Farm");
    await user.type(screen.getByLabelText("Farm-scoped draft"), "farm A notes");
    expect(screen.getByLabelText("Farm-scoped draft")).toHaveValue("farm A notes");

    membershipChanged = true;
    await user.click(screen.getByRole("button", { name: "refresh memberships" }));

    await screen.findByText("Replacement Farm");
    expect(screen.getByLabelText("Farm-scoped draft")).toHaveValue("");
  });

  it("falls back to the email when the user has no name", async () => {
    server.use(
      http.post("/api/auth/refresh", () =>
        HttpResponse.json({
          access_token: "tok",
          user: { ...TEST_USER, name: null },
        }),
      ),
    );

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    expect(await screen.findByText(TEST_USER.email)).toBeInTheDocument();
  });

  it("shows the explicitly selected farm when several memberships exist", async () => {
    localStorage.setItem("goatfarm.farmId", "2");
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          {
            id: 1,
            name: "First Farm",
            location: null,
            timezone: "Asia/Kolkata",
            role: null,
          },
          {
            id: 2,
            name: "Selected Farm",
            location: null,
            timezone: "America/Phoenix",
            role: "Vet",
          },
        ]),
      ),
    );

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    expect(await screen.findByText("Selected Farm")).toBeInTheDocument();
    expect(screen.queryByText("First Farm")).not.toBeInTheDocument();
  });

  it("logout posts to the API, clears the stored farm and navigates to /login", async () => {
    let logoutCalls = 0;
    server.use(
      http.post("/api/auth/logout", () => {
        logoutCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    await screen.findByText("Test Goat Farm");
    expect(localStorage.getItem("goatfarm.farmId")).toBe("1");

    await user.click(screen.getByRole("button", { name: "Logout" }));

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(logoutCalls).toBe(1);
    expect(localStorage.getItem("goatfarm.farmId")).toBeNull();
  });

  it("still navigates to /login when the logout request fails", async () => {
    server.use(http.post("/api/auth/logout", () => HttpResponse.error()));

    const user = userEvent.setup();
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    await screen.findByText("Test Goat Farm");

    await user.click(screen.getByRole("button", { name: "Logout" }));

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  it("validates password confirmation before submitting", async () => {
    let calls = 0;
    server.use(
      http.post("/api/auth/change-password", () => {
        calls += 1;
        return HttpResponse.json({ access_token: "rotated", user: TEST_USER });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    await screen.findByText("Test Goat Farm");

    await user.click(screen.getByRole("button", { name: /^Account/ }));
    const dialog = await screen.findByRole("dialog", { name: "Account & password" });
    await user.type(
      within(dialog).getByLabelText("Current password for password change"),
      "old-password",
    );
    await user.type(within(dialog).getByLabelText("New password"), "new-password");
    await user.type(within(dialog).getByLabelText("Confirm new password"), "different");
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));

    expect(await within(dialog).findByText("Passwords do not match")).toBeInTheDocument();
    expect(calls).toBe(0);
  });

  it("changes the current user's password through the account dialog", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/auth/change-password", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ access_token: "rotated", user: TEST_USER });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    await screen.findByText("Test Goat Farm");

    await user.click(screen.getByRole("button", { name: /^Account/ }));
    const dialog = await screen.findByRole("dialog", { name: "Account & password" });
    await user.type(
      within(dialog).getByLabelText("Current password for password change"),
      "old-password",
    );
    await user.type(within(dialog).getByLabelText("New password"), "new-password");
    await user.type(within(dialog).getByLabelText("Confirm new password"), "new-password");
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({ current_password: "old-password", new_password: "new-password" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("downloads the authenticated account export", async () => {
    let calls = 0;
    server.use(
      http.get("/api/auth/account/export", () => {
        calls += 1;
        return HttpResponse.json({
          exported_at: "2026-08-08T12:00:00Z",
          account: TEST_USER,
          owned_farms: [],
          memberships: [],
        });
      }),
    );
    const createObjectURL = vi.fn(() => "blob:account-export");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    const user = userEvent.setup();
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    await screen.findByText("Test Goat Farm");

    await user.click(screen.getByRole("button", { name: /^Account/ }));
    const dialog = await screen.findByRole("dialog", { name: "Account & password" });
    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));

    await waitFor(() => expect(calls).toBe(1));
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:account-export");
  });

  it("requires a password and surfaces the owned-farm restriction when deleting", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.delete("/api/auth/account", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            detail:
              "Account deletion is unavailable while this account owns a farm; farm ownership cannot currently be transferred or deleted.",
          },
          { status: 409 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    await screen.findByText("Test Goat Farm");

    await user.click(screen.getByRole("button", { name: /^Account/ }));
    const dialog = await screen.findByRole("dialog", { name: "Account & password" });
    expect(
      within(dialog).getByText(
        /Deletion removes your sign-in identity and profile and disables your farm access\./,
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        /Inactive membership rows and operational records may retain a pseudonymous audit reference\./,
      ),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    const confirm = within(dialog).getByRole("button", {
      name: "Delete account and access",
    });
    expect(confirm).toBeDisabled();
    await user.type(
      within(dialog).getByLabelText("Current password to delete account"),
      "owner-password",
    );
    await user.click(confirm);

    expect(
      await within(dialog).findByText(
        "Account deletion is unavailable while this account owns a farm; farm ownership cannot currently be transferred or deleted.",
      ),
    ).toBeInTheDocument();
    expect(body).toEqual({ current_password: "owner-password" });
  });

  it("describes a successful deletion without claiming audit-history erasure", async () => {
    let deleteCalls = 0;
    server.use(
      http.delete("/api/auth/account", () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
      http.post("/api/auth/logout", () => new HttpResponse(null, { status: 204 })),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    await screen.findByText("Test Goat Farm");

    await user.click(screen.getByRole("button", { name: /^Account/ }));
    const dialog = await screen.findByRole("dialog", { name: "Account & password" });
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    await user.type(
      within(dialog).getByLabelText("Current password to delete account"),
      "worker-password",
    );
    await user.click(within(dialog).getByRole("button", { name: "Delete account and access" }));

    await waitFor(() => expect(deleteCalls).toBe(1));
    expect(toastMock.success).toHaveBeenCalledWith(
      "Your sign-in identity and profile were removed, and your farm access was disabled. Inactive membership audit anchors and de-identified operational references may remain.",
    );
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  it("shows the signed-in name and email in the account dialog", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    await screen.findByText("Test Goat Farm");

    await user.click(screen.getByRole("button", { name: /^Account/ }));
    const dialog = await screen.findByRole("dialog", { name: "Account & password" });

    // The dialog falls back to the email whenever it is handed no name, so a
    // name that never reaches it fails silently: the panel still looks right
    // while every account reads as nameless.
    expect(within(dialog).getByText(TEST_USER.name as string)).toBeInTheDocument();
    expect(within(dialog).getByText(TEST_USER.email)).toBeInTheDocument();
  });
});

describe("AppLayout — permission-gated nav", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
  });

  it("shows all 14 goat-farm nav items to the farm owner (full catalog minus dairy-only Milk)", async () => {
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await screen.findByText("Test Goat Farm");
    // The default test farm is a goat (meat) farm; its owner holds every
    // permission including milk.view, but Milk is a buffalo-dairy module.
    await waitFor(() => expect(navLinks()).toHaveLength(14));
    for (const label of ALL_NAV_LABELS) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("shows only the permitted subset to a restricted worker", async () => {
    server.use(permissionsHandler(["dashboard.view", "animals.view", "tasks.view"]));

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(3));
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Animals" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tasks" })).toBeInTheDocument();
    for (const label of ["Breeding", "Health", "Finance", "Reports", "Team"]) {
      expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument();
    }
  });

  it("points the brand at the first permitted page instead of a forbidden dashboard", async () => {
    server.use(permissionsHandler(["health.view"]));

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    expect(
      await screen.findByRole("link", { name: "Herdly — go to Health" }),
    ).toHaveAttribute("href", "/health");
    expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
  });

  it("hides the Team link unless team.manage is held", async () => {
    server.use(
      permissionsHandler([
        "dashboard.view",
        "animals.view",
        "buckets.view",
        "breeding.view",
        "kidding.view",
        "health.view",
        "purchases.view",
        "feeding.view",
        "tasks.view",
        "finance.view",
        "reports.view",
      ]),
    );

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(11));
    expect(screen.queryByRole("link", { name: "Team" })).not.toBeInTheDocument();
  });

  it("shows no nav items at all when the user holds no permissions", async () => {
    server.use(permissionsHandler([]));

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await screen.findByText("Test Goat Farm");
    // Let the permissions query settle (it resolves to an empty set).
    await waitFor(() => expect(navLinks()).toHaveLength(0));
    expect(
      screen.getByRole("link", { name: "Herdly — go to access status" }),
    ).toHaveAttribute("href", "/no-access");
  });

  it("keeps the nav hidden while permissions are still loading", async () => {
    server.use(
      http.get("/api/auth/permissions", () => new Promise<Response>(() => {})),
    );

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    expect(await screen.findByText("Test Goat Farm")).toBeInTheDocument();
    expect(navLinks()).toHaveLength(0);
  });

  it("shows a permission failure without exposing stale navigation", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 }),
      ),
    );

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Herdly — go to/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Herdly")).toBeInTheDocument();
  });

  it("nav links point at their module routes", async () => {
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(14));
    const expected: Record<string, string> = {
      Dashboard: "/dashboard",
      Animals: "/animals",
      Buckets: "/buckets",
      Breeding: "/breeding",
      Kidding: "/kidding",
      Health: "/health",
      Purchases: "/purchases",
      Feeding: "/feeding",
      Tasks: "/tasks",
      Finance: "/finance",
      Planner: "/planner",
      Simulation: "/simulation",
      Reports: "/reports",
      Team: "/team",
    };
    for (const [label, href] of Object.entries(expected)) {
      expect(screen.getByRole("link", { name: label })).toHaveAttribute("href", href);
    }
  });

  it("highlights the nav item matching the current path", async () => {
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(14));
    // usePathname is mocked to /dashboard.
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "data-active",
    );
    expect(screen.getByRole("link", { name: "Animals" })).not.toHaveAttribute(
      "data-active",
    );
  });

  it("highlights nested module routes but not similarly prefixed siblings", async () => {
    navState.pathname = "/animals/42";
    const { unmount } = renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    expect(await screen.findByRole("link", { name: "Animals" })).toHaveAttribute(
      "data-active",
    );

    unmount();
    navState.pathname = "/animals-archive";
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    expect(await screen.findByRole("link", { name: "Animals" })).not.toHaveAttribute(
      "data-active",
    );
  });

  it("files every nav item under its own labelled group", async () => {
    navState.pathname = "/dashboard";
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(14));
    // The group heading is the only thing that explains why Health and
    // Feeding sit together; without it the sidebar is one flat list. The
    // default test farm is a goat (meat) farm, so the buffalo-dairy Milk
    // module stays hidden even though the owner holds milk.view.
    expect(navGroupItems("Overview")).toEqual(["Dashboard"]);
    expect(navGroupItems("Herd")).toEqual([
      "Animals",
      "Buckets",
      "Breeding",
      "Kidding",
    ]);
    expect(navGroupItems("Health & Feed")).toEqual(["Health", "Feeding"]);
    expect(navGroupItems("Operations")).toEqual(["Purchases", "Tasks"]);
    expect(navGroupItems("Business")).toEqual([
      "Finance",
      "Planner",
      "Simulation",
      "Reports",
      "Team",
    ]);
  });

  it("shows the Milk module on buffalo dairy farms", async () => {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          {
            id: 7,
            name: "Navipet Dairy",
            location: "Navipet",
            timezone: "Asia/Kolkata",
            role: null,
            farm_type: "BUFFALO_DAIRY",
          },
        ]),
      ),
    );
    navState.pathname = "/dashboard";
    const { unmount } = renderWithProviders(
      <AppLayout defaultOpen={true}>{null}</AppLayout>,
    );

    await waitFor(() => expect(navLinks()).toHaveLength(15));
    expect(navGroupItems("Health & Feed")).toEqual(["Health", "Feeding", "Milk"]);
    unmount();
  });

  it("keeps a group heading only while the worker can reach something under it", async () => {
    server.use(permissionsHandler(["dashboard.view", "animals.view", "tasks.view"]));

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(3));
    expect(navGroupItems("Overview")).toEqual(["Dashboard"]);
    expect(navGroupItems("Herd")).toEqual(["Animals"]);
    expect(navGroupItems("Operations")).toEqual(["Tasks"]);
    // A heading with nothing under it reads as "this section is broken"
    // rather than "you do not have this section".
    expect(screen.queryByText("Health & Feed")).not.toBeInTheDocument();
    expect(screen.queryByText("Business")).not.toBeInTheDocument();
  });

  it("keeps the permissions-failure notice out of a healthy sidebar", async () => {
    const { unmount } = renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(14));
    expect(
      screen.queryByText(/Could not load your permissions/),
    ).not.toBeInTheDocument();

    // A genuinely empty grant is not a failure either: telling that worker to
    // refresh the page would send them round a loop that changes nothing.
    unmount();
    server.use(permissionsHandler([]));
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await screen.findByRole("link", { name: "Herdly — go to access status" });
    expect(
      screen.queryByText(/Could not load your permissions/),
    ).not.toBeInTheDocument();
  });
});

describe("AppLayout — loading and no-farm states", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
  });

  it("shows 'Loading…' while the session bootstrap is pending", async () => {
    let releaseRefresh: (() => void) | undefined;
    server.use(
      http.post(
        "/api/auth/refresh",
        () =>
          new Promise<Response>((resolve) => {
            releaseRefresh = () =>
              resolve(
                HttpResponse.json({ access_token: "tok", user: TEST_USER }),
              );
          }),
      ),
    );

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByRole("nav")).not.toBeInTheDocument();
    // Release the module-level refresh promise so this test cannot poison the
    // following AuthProvider bootstrap when files run serially.
    releaseRefresh?.();
    await screen.findByText("Test Goat Farm");
  });

  it("redirects to /farm-select when logged in without any farm", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));

    const rendered = renderWithProviders(<LayoutMountedAfterBootstrap />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/farm-select"));
    expect(replaceMock).toHaveBeenCalledTimes(1);

    // If another client navigation wins before the replacement commits, that
    // new source route owns a distinct redirect intent and must not inherit
    // the old route's Strict Mode de-duplication latch.
    navState.pathname = "/animals";
    rendered.rerender(<LayoutMountedAfterBootstrap />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledTimes(2));
    expect(replaceMock).toHaveBeenLastCalledWith("/farm-select");
  });
  it("closes the mobile nav drawer after following a nav link", async () => {
    // Below the breakpoint the sidebar is a modal Sheet with a backdrop and a
    // body scroll lock; the (app) layout does not unmount on an intra-group
    // navigation, so the drawer has to be closed explicitly.
    const user = userEvent.setup();
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: true,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    try {
      renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
      await screen.findByText("Test Goat Farm");

      await user.click(screen.getByRole("button", { name: /toggle sidebar/i }));
      const drawer = await screen.findByRole("dialog");

      await user.click(within(drawer).getByRole("link", { name: "Animals" }));

      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
    } finally {
      window.matchMedia = vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }));
    }
  });

  it("never sends a session that already has an active farm to /farm-select", async () => {
    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(14));
    // The farm gate is the only navigation this shell performs; bouncing a
    // fully selected session would make every page load lose its route.
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("does not mistake a pending session bootstrap for a missing farm", async () => {
    let refreshRequested = false;
    let releaseRefresh: (() => void) | undefined;
    server.use(
      http.post("/api/auth/refresh", () => {
        refreshRequested = true;
        return new Promise<Response>((resolve) => {
          releaseRefresh = () =>
            resolve(HttpResponse.json({ access_token: "tok", user: TEST_USER }));
        });
      }),
    );

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);
    await screen.findByText("Loading…");

    // Settled signal, not a wall-clock sleep: the request is on the wire and
    // no farm is known yet — exactly the state that must NOT redirect.
    await waitFor(() => expect(refreshRequested).toBe(true));
    expect(replaceMock).not.toHaveBeenCalled();

    // Release the module-level refresh promise so this test cannot poison the
    // following AuthProvider bootstrap when files run serially.
    releaseRefresh?.();
    await screen.findByText("Test Goat Farm");
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("leaves a signed-out session to the /login redirect, not the farm gate", async () => {
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );

    renderWithProviders(<AppLayout defaultOpen={true}>{null}</AppLayout>);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    // No user means no farm either, but /farm-select would be a dead end for
    // someone who is not signed in — and the shell must keep its loading gate
    // instead of rendering a header for a null user.
    expect(replaceMock).not.toHaveBeenCalledWith("/farm-select");
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});

describe("AppLayout — skip to content", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    navState.pathname = "/dashboard";
    navState.search = "";
  });

  it("renders the skip link before the sidebar nav in DOM order", async () => {
    const { container } = renderWithProviders(
      <AppLayout defaultOpen={true}>
        <p>page body</p>
      </AppLayout>,
    );
    await screen.findByText("page body");

    const skipLink = screen.getByRole("link", { name: "Skip to content" });
    expect(skipLink.getAttribute("href")).toBe("#main-content");
    // The target landmark exists and is focusable for the programmatic focus.
    const main = document.getElementById("main-content");
    expect(main).not.toBeNull();
    expect(main?.getAttribute("tabindex")).toBe("-1");

    // On desktop the sidebar is an in-flow sibling, so the skip link must
    // precede its first link in DOM order or a keyboard user tabs the whole
    // nav before ever reaching it.
    const links = Array.from(container.querySelectorAll("a[href]")).filter(
      (node) => node.getAttribute("href") !== "#main-content",
    );
    expect(links.length).toBeGreaterThan(0);
    const position = links[0]!.compareDocumentPosition(skipLink);
    // The skip link must come first: skipLink PRECEDES the next link.
    expect(position & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });
});
