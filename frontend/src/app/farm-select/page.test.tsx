/**
 * Farm-select page: the picker (farm cards with location/role fallbacks,
 * current-farm badge, pick → selectFarm + /dashboard) and the create-farm
 * form (validation, POST contract, refresh + auto-pick of the new farm,
 * server-error surfacing). Also covers the logged-out / still-loading
 * states, where the page shows "Loading…" and the AuthProvider redirects
 * to /login.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FarmSelectPage from "./page";

const { pushMock, navState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  navState: { search: "" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/farm-select",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

const TWO_FARMS = [
  { id: 1, name: "Test Goat Farm", location: "Solapur", role: null },
  { id: 2, name: "Second Farm", location: null, role: "Mover" },
];

function cardOf(name: string): HTMLElement {
  const card = screen.getByText(name).closest("button");
  expect(card).not.toBeNull();
  return card as HTMLElement;
}

describe("FarmSelectPage — loading & logged-out states", () => {
  beforeEach(() => {
    pushMock.mockClear();
    navState.search = "";
  });

  it("shows 'Loading…' while the session bootstrap is still pending", async () => {
    let releaseRefresh: (() => void) | undefined;
    server.use(
      http.post(
        "/api/auth/refresh",
        () =>
          new Promise<Response>((resolve) => {
            releaseRefresh = () => resolve(new HttpResponse(null, { status: 401 }));
          }),
      ),
    );

    renderWithProviders(<FarmSelectPage />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("Your farms")).not.toBeInTheDocument();
    releaseRefresh?.();
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/login"));
  });

  it("shows 'Loading…' when logged out and the AuthProvider redirects to /login", async () => {
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );

    renderWithProviders(<FarmSelectPage />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("Your farms")).not.toBeInTheDocument();
  });
});

describe("FarmSelectPage — farm picker", () => {
  beforeEach(() => {
    pushMock.mockClear();
    navState.search = "";
  });

  it("lists the farms with heading, location and Owner fallback for a null role", async () => {
    renderWithProviders(<FarmSelectPage />);

    expect(await screen.findByText("Your farms")).toBeInTheDocument();
    const card = cardOf("Test Goat Farm");
    expect(within(card).getByText("Solapur · Owner")).toBeInTheDocument();
  });

  it("shows '—' for a null location and the role name for memberships", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)));

    renderWithProviders(<FarmSelectPage />);

    const card = await screen.findByText("Second Farm");
    expect(within(card.closest("button") as HTMLElement).getByText("— · Mover"))
      .toBeInTheDocument();
  });

  it("marks only the auto-selected farm with the 'current' badge", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)));

    renderWithProviders(<FarmSelectPage />);

    await screen.findByText("Second Farm");
    // Nothing stored → refreshFarms auto-selects the first farm.
    expect(within(cardOf("Test Goat Farm")).getByText("current")).toBeInTheDocument();
    expect(within(cardOf("Second Farm")).queryByText("current")).not.toBeInTheDocument();
  });

  it("picking a farm selects it (persisted) and navigates to /dashboard", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)));

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);

    await user.click(await screen.findByText("Second Farm"));

    expect(pushMock).toHaveBeenCalledWith("/dashboard");
    expect(localStorage.getItem("goatfarm.farmId")).toBe("2");
  });

  it("opens a selected farm at the first permitted module", async () => {
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)),
      permissionsHandler(["health.view"]),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await user.click(await screen.findByText("Second Farm"));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/health"));
    expect(pushMock).not.toHaveBeenCalledWith("/dashboard");
    expect(localStorage.getItem("goatfarm.farmId")).toBe("2");
  });

  it("restores a permitted internal route and its page state after switching farms", async () => {
    navState.search = "?returnTo=%2Ftasks%3Ftab%3Doverdue";
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)),
      permissionsHandler(["tasks.view"]),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await user.click(await screen.findByText("Second Farm"));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/tasks?tab=overdue"));
  });

  it("falls back safely when returnTo is external or unavailable on the selected farm", async () => {
    navState.search = "?returnTo=https%3A%2F%2Fevil.test%2Fhealth";
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)),
      permissionsHandler(["health.view"]),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await user.click(await screen.findByText("Second Farm"));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/health"));
    expect(pushMock).not.toHaveBeenCalledWith(expect.stringContaining("evil.test"));
  });

  it("shows the empty-state hint when the user has no farms", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));

    renderWithProviders(<FarmSelectPage />);

    expect(
      await screen.findByText("No farms yet — create your first one below."),
    ).toBeInTheDocument();
    expect(screen.getByText("Create a farm")).toBeInTheDocument();
  });
});

describe("FarmSelectPage — create a farm", () => {
  beforeEach(() => {
    pushMock.mockClear();
    navState.search = "";
  });

  it("blocks an empty farm name and never posts", async () => {
    let posts = 0;
    server.use(
      http.post("/api/auth/farms", () => {
        posts += 1;
        return HttpResponse.json({ id: 9, name: "x", location: null, role: null });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.click(screen.getByRole("button", { name: /create farm/i }));

    expect(await screen.findByText("Name is required")).toBeInTheDocument();
    expect(posts).toBe(0);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("blocks a farm name longer than 120 characters", async () => {
    let posts = 0;
    server.use(
      http.post("/api/auth/farms", () => {
        posts += 1;
        return HttpResponse.json({ id: 9, name: "x", location: null, role: null });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText(/farm name/i), "x".repeat(121));
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    expect(await screen.findByText(/120/)).toBeInTheDocument();
    expect(posts).toBe(0);
  });

  it("creates the farm, refreshes the list, selects the new farm and navigates", async () => {
    let postBody: unknown;
    let farmFetches = 0;
    server.use(
      http.get("/api/auth/farms", () => {
        farmFetches += 1;
        return HttpResponse.json([
          ...TWO_FARMS,
          { id: 3, name: "New Osmanabadi Farm", location: null, role: null },
        ]);
      }),
      http.post("/api/auth/farms", async ({ request }) => {
        postBody = await request.json();
        return HttpResponse.json({
          id: 3,
          name: "New Osmanabadi Farm",
          location: null,
          role: null,
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText(/farm name/i), "New Osmanabadi Farm");
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    // Blank location → null in the POST body.
    expect(postBody).toEqual({
      name: "New Osmanabadi Farm",
      location: null,
      timezone: "Asia/Kolkata",
    });
    // refreshFarms re-fetched the list after creation.
    expect(farmFetches).toBeGreaterThanOrEqual(2);
    // The newly created farm (not the previously selected one) is active.
    expect(localStorage.getItem("goatfarm.farmId")).toBe("3");
    // The form was reset.
    expect(screen.getByLabelText(/farm name/i)).toHaveValue("");
  });

  it("sends the location through when provided", async () => {
    let postBody: unknown;
    server.use(
      http.post("/api/auth/farms", async ({ request }) => {
        postBody = await request.json();
        return HttpResponse.json({ id: 3, name: "Hillside", location: "Pune", role: null });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText(/farm name/i), "Hillside");
    await user.type(screen.getByLabelText(/location/i), "Pune");
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(postBody).toEqual({
      name: "Hillside",
      location: "Pune",
      timezone: "Asia/Kolkata",
    });
  });

  it("disables the button and shows 'Creating…' while in flight", async () => {
    let resolveCreate: () => void = () => {};
    server.use(
      http.post(
        "/api/auth/farms",
        () =>
          new Promise((resolve) => {
            resolveCreate = () =>
              resolve(
                HttpResponse.json({ id: 3, name: "Hillside", location: null, role: null }),
              );
          }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText(/farm name/i), "Hillside");
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    expect(screen.getByRole("button", { name: /creating…/i })).toBeDisabled();

    resolveCreate();
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
  });

  it("surfaces the server detail on an API error and stays put", async () => {
    server.use(
      http.post("/api/auth/farms", () =>
        HttpResponse.json({ detail: "Farm name already exists" }, { status: 400 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText(/farm name/i), "Test Goat Farm");
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    expect(await screen.findByText("Farm name already exists")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("shows the generic message on a network failure", async () => {
    server.use(http.post("/api/auth/farms", () => HttpResponse.error()));

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText(/farm name/i), "Hillside");
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    expect(await screen.findByText("Could not create the farm.")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("clears a previous server error when a retry succeeds", async () => {
    let attempt = 0;
    server.use(
      http.post("/api/auth/farms", () => {
        attempt += 1;
        if (attempt === 1) {
          return HttpResponse.json({ detail: "boom" }, { status: 500 });
        }
        return HttpResponse.json({ id: 3, name: "Hillside", location: null, role: null });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText(/farm name/i), "Hillside");
    await user.click(screen.getByRole("button", { name: /create farm/i }));
    expect(await screen.findByText("boom")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /create farm/i }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByText("boom")).not.toBeInTheDocument();
    expect(attempt).toBe(2);
  });
});
