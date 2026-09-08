/**
 * Farm-select page: the picker (farm cards with location/role fallbacks,
 * current-farm badge, pick → selectFarm + /dashboard) and the create-farm
 * form (validation, POST contract, refresh + auto-pick of the new farm,
 * server-error surfacing). Also covers the logged-out / still-loading
 * states, where the page shows "Loading…" and the AuthProvider redirects
 * to /login.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "@/lib/api-client";
import { permissionsHandler, server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FarmSelectPage from "./page";

const { pushMock, replaceMock, navState, sessionEpochShift } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  navState: { search: "" },
  sessionEpochShift: { value: 0 },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/farm-select",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

// api-client is otherwise untouched (shift stays 0). The shift lets a test open
// the one window api-client's own epoch asserts cannot cover: a newer sign-in
// landing between a request resolving and this page's continuation running.
vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    authSessionEpochValue: () => actual.authSessionEpochValue() + sessionEpochShift.value,
  };
});

afterEach(() => {
  sessionEpochShift.value = 0;
});

const TWO_FARMS = [
  { id: 1, name: "Test Goat Farm", location: "Solapur", role: null },
  { id: 2, name: "Second Farm", location: null, role: "Mover" },
];

const TIMEZONE_HINT =
  "IANA name used for due dates and daily records, for example Asia/Kolkata.";

function cardOf(name: string): HTMLElement {
  const card = screen.getByText(name).closest("button");
  expect(card).not.toBeNull();
  return card as HTMLElement;
}

/** A promise a test releases by hand, to hold a request on the wire. */
function gate(): { wait: Promise<void>; release: () => void } {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { wait, release };
}

describe("FarmSelectPage — loading & logged-out states", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
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
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  it("shows 'Loading…' when logged out and the AuthProvider redirects to /login", async () => {
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );

    renderWithProviders(<FarmSelectPage />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("Your farms")).not.toBeInTheDocument();
  });
});

describe("FarmSelectPage — farm picker", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
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

  it("surfaces a permission-discovery failure, unlocks the picker, and allows retry", async () => {
    let permissionCalls = 0;
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)),
      http.get("/api/auth/permissions", () => {
        permissionCalls += 1;
        return permissionCalls === 1
          ? HttpResponse.json({ detail: "Permissions temporarily unavailable" }, { status: 503 })
          : HttpResponse.json({ is_owner: false, permissions: ["health.view"] });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    const secondFarm = await screen.findByText("Second Farm");

    await user.click(secondFarm);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Permissions temporarily unavailable",
    );
    expect(cardOf("Second Farm")).toBeEnabled();
    expect(pushMock).not.toHaveBeenCalled();

    await user.click(cardOf("Second Farm"));
    await waitFor(() => expect(pushMock).toHaveBeenLastCalledWith("/health"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(permissionCalls).toBe(2);
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

  it("does not navigate from a farm picker that unmounted mid-permissions read", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)));
    let releasePermissions!: () => void;
    let permissionsStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      permissionsStarted = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      releasePermissions = resolve;
    });
    server.use(
      http.get("/api/auth/permissions", async () => {
        permissionsStarted();
        await gate;
        return HttpResponse.json({
          is_owner: true,
          permissions: ["dashboard.view"],
        });
      }),
    );

    const user = userEvent.setup();
    const rendered = renderWithProviders(<FarmSelectPage />);
    await user.click(await screen.findByText("Second Farm"));
    await started;

    rendered.unmount();
    releasePermissions();
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(pushMock).not.toHaveBeenCalled();
  });

  it("shows the empty-state hint when the user has no farms", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));

    renderWithProviders(<FarmSelectPage />);

    expect(
      await screen.findByText("No farms yet — create your first one below."),
    ).toBeInTheDocument();
    expect(screen.getByText("Create a farm")).toBeInTheDocument();
  });

  it("shows each farm's own timezone on its card", async () => {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 1, name: "London Farm", location: "Kent", role: null, timezone: "Europe/London" },
          { id: 2, name: "Legacy Farm", location: null, role: null, timezone: "Asia/Kolkata" },
        ]),
      ),
    );

    renderWithProviders(<FarmSelectPage />);

    await screen.findByText("Legacy Farm");
    // The card must show the farm's OWN zone: due dates and daily records are
    // rendered in it, so a wrong zone would misdate the farm. Timezone is
    // required on the generated FarmOut contract — no silent default remains.
    expect(within(cardOf("London Farm")).getByText("Europe/London")).toBeInTheDocument();
    expect(within(cardOf("Legacy Farm")).getByText("Asia/Kolkata")).toBeInTheDocument();
    // The empty-state hint belongs to an empty list only.
    expect(
      screen.queryByText("No farms yet — create your first one below."),
    ).not.toBeInTheDocument();
  });

  it("marks the chosen card as opening and keeps the page locked past the navigation", async () => {
    const permissions = gate();
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)),
      http.get("/api/auth/permissions", async () => {
        await permissions.wait;
        return HttpResponse.json({ is_owner: false, permissions: ["dashboard.view"] });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await user.click(await screen.findByText("Second Farm"));

    // Only the picked card reports progress, and it replaces the farm name.
    const opening = await screen.findByText("Opening…");
    expect(screen.queryByText("Second Farm")).not.toBeInTheDocument();
    expect(opening.closest("button")).toBeDisabled();
    expect(cardOf("Test Goat Farm")).toBeDisabled();
    // Creating a farm mid-open would race a second selectFarm() for the API
    // client's X-Farm-Id, so the whole create form is locked too — and the
    // submit button carries its own disabled flag, not just the fieldset's.
    expect(screen.getByLabelText("Farm name")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Create farm" })).toHaveAttribute("disabled");

    permissions.release();
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    // useSingleFlight releases its guard as soon as openFarm returns (the
    // create form re-enables here), but router.push is still committing: the
    // picker must stay locked on the chosen farm so a second pick cannot swap
    // X-Farm-Id under the route being opened.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Create farm" })).not.toHaveAttribute(
        "disabled",
      ),
    );
    expect(screen.getByText("Opening…")).toBeInTheDocument();
    expect(cardOf("Test Goat Farm")).toBeDisabled();
  });

  it("names the failing step when the permissions read dies without a server detail", async () => {
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)),
      http.get("/api/auth/permissions", () => HttpResponse.error()),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await user.click(await screen.findByText("Second Farm"));

    // A transport failure carries no ApiError.detail — the operator still gets
    // a message naming what failed instead of a blank alert.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load permissions for this farm.",
    );
    expect(cardOf("Second Farm")).toBeEnabled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("stays silent when a permissions failure lands after another session took over", async () => {
    const permissions = gate();
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)),
      http.get("/api/auth/permissions", async () => {
        await permissions.wait;
        return HttpResponse.json(
          { detail: "Permissions temporarily unavailable" },
          { status: 503 },
        );
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await user.click(await screen.findByText("Second Farm"));
    await screen.findByText("Opening…");

    // Another tab signed in while the read was on the wire: this pick belongs
    // to a session that no longer owns the page.
    setAccessToken("second-session-token", TEST_USER.id);
    permissions.release();
    await new Promise((resolve) => setTimeout(resolve, 50));

    // The superseded pick neither reports its failure nor hands the picker
    // back — the new session owns both the error UI and the selection.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Permissions temporarily unavailable")).not.toBeInTheDocument();
    expect(screen.getByText("Opening…")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("does not navigate when a newer session lands while the permissions read resolves", async () => {
    const permissions = gate();
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json(TWO_FARMS)),
      http.get("/api/auth/permissions", async () => {
        await permissions.wait;
        return HttpResponse.json({ is_owner: false, permissions: ["dashboard.view"] });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await user.click(await screen.findByText("Second Farm"));
    await screen.findByText("Opening…");

    // The permissions answer belongs to the replaced session; routing on it
    // would drop the newer session's operator into a farm they never picked.
    sessionEpochShift.value = 1;
    permissions.release();
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(pushMock).not.toHaveBeenCalled();
  });
});

describe("FarmSelectPage — create a farm", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
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

  it("blocks a whitespace-only farm name and never posts", async () => {
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

    await user.type(screen.getByLabelText(/farm name/i), "   ");
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    expect(await screen.findByText("Name is required")).toBeInTheDocument();
    expect(posts).toBe(0);
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

    fireEvent.change(screen.getByLabelText(/farm name/i), {
      target: { value: "x".repeat(121) },
    });
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    expect(await screen.findByText(/120/)).toBeInTheDocument();
    expect(posts).toBe(0);
  });

  it.each(["Factory", "localtime"])(
    "rejects the non-location timezone placeholder %s before creating a farm",
    async (timezoneName) => {
      let postCalls = 0;
      server.use(
        http.post("/api/auth/farms", () => {
          postCalls += 1;
          return HttpResponse.json({ id: 7, name: "Bad zone", timezone: timezoneName });
        }),
      );
      const user = userEvent.setup();
      renderWithProviders(<FarmSelectPage />);
      await screen.findByText("Your farms");

      await user.type(screen.getByLabelText("Farm name"), "Bad zone");
      const timezone = screen.getByLabelText("Farm timezone");
      await user.clear(timezone);
      await user.type(timezone, timezoneName);
      await user.click(screen.getByRole("button", { name: "Create farm" }));

      expect(await screen.findByText("Enter a real location timezone")).toBeInTheDocument();
      expect(timezone).toHaveAccessibleDescription(/Enter a real location timezone/);
      expect(postCalls).toBe(0);
    },
  );

  it("enforces location/timezone ceilings and accepts every exact storage boundary", async () => {
    let postCalls = 0;
    let postBody: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/auth/farms", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 7, ...postBody, role: null });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Your farms");

    const name = screen.getByLabelText("Farm name");
    const location = screen.getByLabelText(/Location/);
    const timezone = screen.getByLabelText("Farm timezone");
    fireEvent.change(name, { target: { value: "n".repeat(120) } });
    fireEvent.change(location, { target: { value: "l".repeat(121) } });
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    expect(
      await screen.findByText("Location must be at most 120 characters"),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);

    fireEvent.change(location, { target: { value: "l".repeat(120) } });
    fireEvent.change(timezone, { target: { value: "t".repeat(65) } });
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    expect(
      await screen.findByText("Timezone must be at most 64 characters"),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);

    fireEvent.change(timezone, { target: { value: "t".repeat(64) } });
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toEqual({
      name: "n".repeat(120),
      location: "l".repeat(120),
      timezone: "t".repeat(64),
    });
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

  it("trims farm names and locations before persisting them", async () => {
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

    await user.type(screen.getByLabelText(/farm name/i), "  Hillside  ");
    await user.type(screen.getByLabelText(/location/i), "  Pune  ");
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
    expect(screen.getByLabelText(/farm name/i)).toBeDisabled();
    expect(screen.getByLabelText(/location/i)).toBeDisabled();
    expect(screen.getByLabelText(/timezone/i)).toBeDisabled();
    // Selecting a different farm while creation is pending would let the two
    // completions race and whichever resolved last would replace the active
    // farm/navigation chosen by the operator.
    expect(cardOf("Test Goat Farm")).toBeDisabled();

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

  // REGRESSION — the durable POST and the read-only follow-up refresh shared
  // one try/catch, so a 5xx on the refresh was reported as "Could not create
  // the farm.". The retry that invited would mint a fresh Idempotency-Key and
  // create a second, identical farm.
  it("does not report a created farm as failed when the follow-up refresh 500s", async () => {
    let posts = 0;
    let listed = false;
    server.use(
      http.get("/api/auth/farms", () => {
        // Answer the AuthProvider bootstrap, then fail every later refresh.
        if (listed) return HttpResponse.json({ detail: "Internal Server Error" }, { status: 500 });
        listed = true;
        return HttpResponse.json(TWO_FARMS);
      }),
      http.post("/api/auth/farms", () => {
        posts += 1;
        return HttpResponse.json({ id: 3, name: "Green Acres", location: null, role: null });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText(/farm name/i), "Green Acres");
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    // The created farm is still opened, and nothing blames the creation.
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByText("Could not create the farm.")).not.toBeInTheDocument();
    expect(screen.queryByText("Internal Server Error")).not.toBeInTheDocument();
    // The name is cleared, so the operator is not nudged into a duplicate.
    expect(screen.getByLabelText(/farm name/i)).toHaveValue("");
    expect(posts).toBe(1);
  });

  it("does not reopen a created farm after its follow-up refresh logs the user out", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    let refreshCalls = 0;
    server.use(
      http.post("/api/auth/farms", () =>
        HttpResponse.json({
          id: 3,
          name: "Green Acres",
          location: null,
          timezone: "Asia/Kolkata",
          role: null,
        }),
      ),
      http.get("/api/auth/farms", () => {
        refreshCalls += 1;
        return HttpResponse.json({ detail: "Expired" }, { status: 401 });
      }),
      http.post("/api/auth/refresh", () =>
        new HttpResponse(null, { status: 401 }),
      ),
    );

    await user.type(screen.getByLabelText(/farm name/i), "Green Acres");
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    await waitFor(() => expect(refreshCalls).toBe(1));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(localStorage.getItem("goatfarm.farmId")).toBeNull();
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

  it("drops the previous server error the moment a retry starts, not when it lands", async () => {
    const second = gate();
    let attempt = 0;
    server.use(
      http.post("/api/auth/farms", async () => {
        attempt += 1;
        if (attempt === 1) return HttpResponse.json({ detail: "boom" }, { status: 500 });
        await second.wait;
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

    // While the retry is in flight the stale failure must already be gone:
    // left next to the "Creating…" button it reads as "this attempt failed
    // too", and the operator submits a third time.
    await waitFor(() => expect(screen.queryByText("boom")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /creating…/i })).toBeDisabled();

    second.release();
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(attempt).toBe(2);
  });

  it("returns every field to its default after a creation, including the timezone", async () => {
    server.use(
      http.post("/api/auth/farms", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 3, ...body, role: null });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText("Farm name"), "Hillside");
    await user.type(screen.getByLabelText(/Location/), "Pune");
    const timezone = screen.getByLabelText("Farm timezone");
    await user.clear(timezone);
    await user.type(timezone, "Europe/London");
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    // A leftover location — or the previous farm's timezone — would be silently
    // attached to the NEXT farm created from this form.
    expect(screen.getByLabelText("Farm name")).toHaveValue("");
    expect(screen.getByLabelText(/Location/)).toHaveValue("");
    expect(screen.getByLabelText("Farm timezone")).toHaveValue("Asia/Kolkata");
  });

  it("does not blame a creation failure that lands after another session took over", async () => {
    const post = gate();
    server.use(
      http.post("/api/auth/farms", async () => {
        await post.wait;
        return HttpResponse.json({ detail: "Farm name already exists" }, { status: 400 });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText(/farm name/i), "Green Acres");
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    // Another tab signed in while the write was on the wire.
    setAccessToken("second-session-token", TEST_USER.id);
    post.release();
    await new Promise((resolve) => setTimeout(resolve, 50));

    // The failure belongs to the replaced session: showing it would blame the
    // new session's operator for a write they never made.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Could not create the farm.")).not.toBeInTheDocument();
    expect(screen.queryByText("Farm name already exists")).not.toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("does not open a farm whose creation resolved into a replaced session", async () => {
    const post = gate();
    let posts = 0;
    server.use(
      http.post("/api/auth/farms", async () => {
        posts += 1;
        await post.wait;
        return HttpResponse.json({
          id: 3,
          name: "Green Acres",
          location: null,
          timezone: "Asia/Kolkata",
          role: null,
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Test Goat Farm");

    await user.type(screen.getByLabelText(/farm name/i), "Green Acres");
    await user.click(screen.getByRole("button", { name: /create farm/i }));

    // A newer sign-in owns the page by the time the durable POST answers.
    sessionEpochShift.value = 1;
    post.release();
    await new Promise((resolve) => setTimeout(resolve, 50));

    // The farm exists, but it must not be selected for — nor its route pushed
    // at — whoever the newer session belongs to.
    expect(pushMock).not.toHaveBeenCalled();
    expect(localStorage.getItem("goatfarm.farmId")).toBe("1");
    // The form still holds what was typed: the new session owns any retry
    // decision, and a cleared form invites a duplicate farm.
    expect(screen.getByLabelText(/farm name/i)).toHaveValue("Green Acres");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(posts).toBe(1);
  });

  it("marks only the fields that failed validation and wires each message to its input", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FarmSelectPage />);
    await screen.findByText("Your farms");

    const name = screen.getByLabelText("Farm name");
    const location = screen.getByLabelText(/Location/);
    const timezone = screen.getByLabelText("Farm timezone");

    // A pristine form announces nothing as invalid (aria-invalid="false" is a
    // claim of its own, so the attribute must be absent, not falsy)…
    expect(name).not.toHaveAttribute("aria-invalid");
    expect(location).not.toHaveAttribute("aria-invalid");
    expect(timezone).not.toHaveAttribute("aria-invalid");
    // …and the timezone field is described by its format hint from the start.
    expect(timezone).toHaveAccessibleDescription(TIMEZONE_HINT);

    fireEvent.change(name, { target: { value: "n".repeat(121) } });
    fireEvent.change(location, { target: { value: "l".repeat(121) } });
    await user.clear(timezone);
    await user.click(screen.getByRole("button", { name: "Create farm" }));

    // Each message names its own limit and reaches screen readers through the
    // field it belongs to, not just as loose text on the page.
    await waitFor(() => expect(name).toHaveAttribute("aria-invalid", "true"));
    expect(name).toHaveAccessibleDescription("Farm name must be at most 120 characters");
    expect(location).toHaveAttribute("aria-invalid", "true");
    expect(location).toHaveAccessibleDescription("Location must be at most 120 characters");
    expect(timezone).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Timezone is required")).toBeInTheDocument();
    // The hint survives alongside the error rather than being replaced by it.
    expect(timezone).toHaveAccessibleDescription(`${TIMEZONE_HINT} Timezone is required`);
  });
});
