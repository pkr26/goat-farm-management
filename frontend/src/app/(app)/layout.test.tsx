/**
 * (app) shell layout: header (brand, active farm, switch-farm link, user,
 * logout) and the permission-filtered nav — every nav item renders only
 * when the user holds its permission on the active farm (mirrors SPEC's
 * RBAC rules: e.g. Team requires team.manage). Also covers the loading
 * and no-farm redirect states.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AppLayout from "./layout";

const { pushMock, replaceMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

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
  "Simulation",
  "Reports",
  "Team",
];

function navLinks() {
  return screen
    .getAllByRole("link")
    .filter((el) => el.closest('[data-slot="sidebar-content"]'));
}

describe("AppLayout — header", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
  });

  it("renders brand, active farm name, switch-farm link and the user's name", async () => {
    renderWithProviders(
      <AppLayout>
        <p>page body</p>
      </AppLayout>,
    );

    expect(await screen.findByText("Test Goat Farm")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "GoatFarm dashboard" }),
    ).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("link", { name: "switch farm" })).toHaveAttribute(
      "href",
      "/farm-select",
    );
    expect(screen.getByText(TEST_USER.name as string)).toBeInTheDocument();
    expect(screen.getByText("page body")).toBeInTheDocument();
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

    renderWithProviders(<AppLayout>{null}</AppLayout>);

    expect(await screen.findByText(TEST_USER.email)).toBeInTheDocument();
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
    renderWithProviders(<AppLayout>{null}</AppLayout>);
    await screen.findByText("Test Goat Farm");
    expect(localStorage.getItem("goatfarm.farmId")).toBe("1");

    await user.click(screen.getByRole("button", { name: "Logout" }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/login"));
    expect(logoutCalls).toBe(1);
    expect(localStorage.getItem("goatfarm.farmId")).toBeNull();
  });

  it("still navigates to /login when the logout request fails", async () => {
    server.use(http.post("/api/auth/logout", () => HttpResponse.error()));

    const user = userEvent.setup();
    renderWithProviders(<AppLayout>{null}</AppLayout>);
    await screen.findByText("Test Goat Farm");

    await user.click(screen.getByRole("button", { name: "Logout" }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/login"));
  });
});

describe("AppLayout — permission-gated nav", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
  });

  it("shows all 13 nav items to the farm owner (full catalog)", async () => {
    renderWithProviders(<AppLayout>{null}</AppLayout>);

    await screen.findByText("Test Goat Farm");
    await waitFor(() => expect(navLinks()).toHaveLength(13));
    for (const label of ALL_NAV_LABELS) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("shows only the permitted subset to a restricted worker", async () => {
    server.use(permissionsHandler(["dashboard.view", "animals.view", "tasks.view"]));

    renderWithProviders(<AppLayout>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(3));
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Animals" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tasks" })).toBeInTheDocument();
    for (const label of ["Breeding", "Health", "Finance", "Reports", "Team"]) {
      expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument();
    }
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

    renderWithProviders(<AppLayout>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(11));
    expect(screen.queryByRole("link", { name: "Team" })).not.toBeInTheDocument();
  });

  it("shows no nav items at all when the user holds no permissions", async () => {
    server.use(permissionsHandler([]));

    renderWithProviders(<AppLayout>{null}</AppLayout>);

    await screen.findByText("Test Goat Farm");
    // Let the permissions query settle (it resolves to an empty set).
    await waitFor(() => expect(navLinks()).toHaveLength(0));
  });

  it("keeps the nav hidden while permissions are still loading", async () => {
    server.use(
      http.get("/api/auth/permissions", () => new Promise<Response>(() => {})),
    );

    renderWithProviders(<AppLayout>{null}</AppLayout>);

    expect(await screen.findByText("Test Goat Farm")).toBeInTheDocument();
    expect(navLinks()).toHaveLength(0);
  });

  it("nav links point at their module routes", async () => {
    renderWithProviders(<AppLayout>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(13));
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
      Simulation: "/simulation",
      Reports: "/reports",
      Team: "/team",
    };
    for (const [label, href] of Object.entries(expected)) {
      expect(screen.getByRole("link", { name: label })).toHaveAttribute("href", href);
    }
  });

  it("highlights the nav item matching the current path", async () => {
    renderWithProviders(<AppLayout>{null}</AppLayout>);

    await waitFor(() => expect(navLinks()).toHaveLength(13));
    // usePathname is mocked to /dashboard.
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "data-active",
    );
    expect(screen.getByRole("link", { name: "Animals" })).not.toHaveAttribute(
      "data-active",
    );
  });
});

describe("AppLayout — loading and no-farm states", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
  });

  it("shows 'Loading…' while the session bootstrap is pending", async () => {
    server.use(
      http.post("/api/auth/refresh", () => new Promise<Response>(() => {})),
    );

    renderWithProviders(<AppLayout>{null}</AppLayout>);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByRole("nav")).not.toBeInTheDocument();
  });

  it("redirects to /farm-select when logged in without any farm", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));

    renderWithProviders(<AppLayout>{null}</AppLayout>);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/farm-select"));
  });
});
