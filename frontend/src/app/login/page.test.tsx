/**
 * Login page: successful submit posts credentials to /api/auth/login, the
 * returned token is stored via signIn (proven by the follow-up /api/auth/farms
 * call carrying it), and the router navigates to /dashboard. A 401 surfaces
 * the server-error line instead of navigating.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { LanguageProvider, LANGUAGE_STORAGE_KEY } from "@/lib/i18n";

import LoginPage from "./page";
import { settle } from "@/test/settle";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

/**
 * Leaves the page's route while keeping the AuthProvider mounted — exactly
 * what a real navigation does, since the provider lives in the root layout.
 * `rendered.unmount()` tears down the provider too, which hides everything
 * the page's own mounted guard is responsible for.
 */
function LoginRoute({ visible }: { visible: boolean }) {
  return visible ? <LoginPage /> : <p>Left the login route</p>;
}

describe("LoginPage", () => {
  beforeEach(() => {
    pushMock.mockClear();
    // No existing session: the AuthProvider bootstrap refresh is rejected,
    // so the page starts signed out and nothing redirects away.
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );
  });

  it("posts credentials, stores the token via signIn and navigates to /dashboard", async () => {
    let loginBody: unknown;
    let farmsAuthorization: string | null = null;
    server.use(
      http.post("/api/auth/login", async ({ request }) => {
        loginBody = await request.json();
        return HttpResponse.json({
          access_token: "login-token",
          user: { id: 2, email: "demo@goatfarm.in", name: "Demo User" },
        });
      }),
      http.get("/api/auth/farms", ({ request }) => {
        farmsAuthorization = request.headers.get("Authorization");
        return HttpResponse.json([
          { id: 7, name: "Demo Osmanabadi Farm", location: null, role: null },
        ]);
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(loginBody).toEqual({ email: "demo@goatfarm.in", password: "demo1234" });
    // signIn stored the access token: the farms fetch carried it.
    expect(farmsAuthorization).toBe("Bearer login-token");
  });

  it("navigates to the first module allowed by a restricted farm role", async () => {
    server.use(
      permissionsHandler(["health.view"]),
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          access_token: "login-token",
          user: { id: 2, email: "demo@goatfarm.in", name: "Demo User" },
        }),
      ),
      http.get("/api/auth/farms", () =>
        HttpResponse.json([{ id: 7, name: "Restricted Farm", location: null, role: "Vet" }]),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/health"));
    expect(pushMock).not.toHaveBeenCalledWith("/dashboard");
  });

  it("falls back to farm selection when permission discovery fails without logging out", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          access_token: "login-token",
          user: { id: 2, email: "demo@goatfarm.in", name: "Demo User" },
        }),
      ),
      http.get("/api/auth/farms", () =>
        HttpResponse.json([{ id: 7, name: "Demo Farm", location: null, role: null }]),
      ),
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "Permissions unavailable" }, { status: 503 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(pushMock).toHaveBeenLastCalledWith("/farm-select"));
  });

  it("shows 'Invalid email or password.' on a 401 and does not navigate", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ detail: "Invalid email or password." }, { status: 401 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "wrong-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Invalid email or password.")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("shows the backend-unreachable message on non-API failures", async () => {
    server.use(http.post("/api/auth/login", () => HttpResponse.error()));

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(
      await screen.findByText("Network is weak — please check your connection and try again."),
    ).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("coalesces same-tick form submissions into one login request", async () => {
    let loginCalls = 0;
    let releaseLogin!: () => void;
    const loginGate = new Promise<void>((resolve) => {
      releaseLogin = resolve;
    });
    server.use(
      http.post("/api/auth/login", async () => {
        loginCalls += 1;
        await loginGate;
        return HttpResponse.json({
          access_token: "login-token",
          user: { id: 2, email: "demo@goatfarm.in", name: "Demo User" },
        });
      }),
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 7, name: "Demo Farm", location: null, role: null },
        ]),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");

    const form = screen.getByRole("button", { name: /sign in/i }).closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(loginCalls).toBeGreaterThan(0));
    expect(screen.getByLabelText(/email/i)).toBeDisabled();
    expect(screen.getByLabelText(/password/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /signing in…/i })).toBeDisabled();
    releaseLogin();

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(loginCalls).toBe(1);
  });

  it("does not establish or navigate a login page that already unmounted", async () => {
    let releaseLogin!: () => void;
    let loginStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      loginStarted = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      releaseLogin = resolve;
    });
    server.use(
      http.post("/api/auth/login", async () => {
        loginStarted();
        await gate;
        return HttpResponse.json({
          access_token: "stale-login-token",
          user: { id: 2, email: "demo@goatfarm.in", name: "Demo User" },
        });
      }),
    );
    const user = userEvent.setup();
    const rendered = renderWithProviders(<LoginPage />);
    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    await started;

    rendered.unmount();
    releaseLogin();
    await settle(50);

    expect(pushMock).not.toHaveBeenCalled();
  });

  it("does not let a failed-permissions logout navigate back to farm selection", async () => {
    let permissionCalls = 0;
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          access_token: "login-token",
          user: { id: 2, email: "demo@goatfarm.in", name: "Demo User" },
        }),
      ),
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 7, name: "Demo Farm", location: null, role: null },
        ]),
      ),
      http.get("/api/auth/permissions", () => {
        permissionCalls += 1;
        return HttpResponse.json({ detail: "Expired" }, { status: 401 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(permissionCalls).toBe(1));
    await settle(50);

    expect(pushMock).not.toHaveBeenCalledWith("/farm-select");
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("keeps the submit button itself disabled for the whole coalesced flight", async () => {
    let loginCalls = 0;
    let releaseLogin!: () => void;
    const loginGate = new Promise<void>((resolve) => {
      releaseLogin = resolve;
    });
    server.use(
      http.post("/api/auth/login", async () => {
        loginCalls += 1;
        await loginGate;
        return HttpResponse.json({
          access_token: "login-token",
          user: { id: 2, email: "demo@goatfarm.in", name: "Demo User" },
        });
      }),
      http.get("/api/auth/farms", () =>
        HttpResponse.json([{ id: 7, name: "Demo Farm", location: null, role: null }]),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");

    const form = screen.getByRole("button", { name: /sign in/i }).closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(loginCalls).toBeGreaterThan(0));

    // react-hook-form clears isSubmitting the moment the SECOND (coalesced)
    // submit returns, so from here only the single-flight `pending` flag still
    // reports the request. The button must carry its own disabled attribute
    // through that window, not merely inherit the fieldset's — that is what a
    // second Enter press on the focused button hits.
    const button = screen.getByRole("button", { name: /signing in…/i });
    expect(button).toHaveAttribute("disabled");
    expect(button).toBeDisabled();

    releaseLogin();
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(loginCalls).toBe(1);
  });

  it("does not establish a session for a page that left the route mid-login", async () => {
    let farmsCalls = 0;
    let releaseLogin!: () => void;
    let loginStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      loginStarted = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      releaseLogin = resolve;
    });
    server.use(
      http.post("/api/auth/login", async () => {
        loginStarted();
        await gate;
        return HttpResponse.json({
          access_token: "stale-login-token",
          user: { id: 2, email: "demo@goatfarm.in", name: "Demo User" },
        });
      }),
      http.get("/api/auth/farms", () => {
        farmsCalls += 1;
        return HttpResponse.json([
          { id: 7, name: "Demo Farm", location: null, role: null },
        ]);
      }),
    );

    const user = userEvent.setup();
    const view = renderWithProviders(<LoginRoute visible />);
    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    await started;

    view.rerender(<LoginRoute visible={false} />);
    releaseLogin();
    await settle(50);

    // The provider is still mounted here, so signIn WOULD have installed the
    // token: only the page's own mounted guard stops it. Establishing the
    // session anyway would authenticate a route the operator already left —
    // the membership fetch signIn always makes is the observable proof.
    expect(farmsCalls).toBe(0);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("does not discover permissions for a page that left the route mid-signIn", async () => {
    let permissionCalls = 0;
    let releaseFarms!: () => void;
    let farmsStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      farmsStarted = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      releaseFarms = resolve;
    });
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          access_token: "login-token",
          user: { id: 2, email: "demo@goatfarm.in", name: "Demo User" },
        }),
      ),
      http.get("/api/auth/farms", async () => {
        farmsStarted();
        await gate;
        return HttpResponse.json([
          { id: 7, name: "Demo Farm", location: null, role: null },
        ]);
      }),
      http.get("/api/auth/permissions", () => {
        permissionCalls += 1;
        return HttpResponse.json({ is_owner: true, permissions: ["dashboard.view"] });
      }),
    );

    const user = userEvent.setup();
    const view = renderWithProviders(<LoginRoute visible />);
    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    await started;

    // The session itself completes — the page is gone by the time signIn
    // resolves, so the login continuation must stop before deciding where
    // this now-unmounted form should have sent the operator.
    view.rerender(<LoginRoute visible={false} />);
    releaseFarms();
    await settle(50);

    expect(permissionCalls).toBe(0);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("does not navigate for a page that left the route while permissions loaded", async () => {
    let releasePermissions!: () => void;
    let permissionsStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      permissionsStarted = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      releasePermissions = resolve;
    });
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          access_token: "login-token",
          user: { id: 2, email: "demo@goatfarm.in", name: "Demo User" },
        }),
      ),
      http.get("/api/auth/farms", () =>
        HttpResponse.json([{ id: 7, name: "Demo Farm", location: null, role: null }]),
      ),
      http.get("/api/auth/permissions", async () => {
        permissionsStarted();
        await gate;
        return HttpResponse.json({ is_owner: true, permissions: ["dashboard.view"] });
      }),
    );

    const user = userEvent.setup();
    const view = renderWithProviders(<LoginRoute visible />);
    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    await started;

    // Permissions resolve successfully, so nothing else rejects this
    // continuation: the mounted check is the only thing keeping a
    // router.push from yanking the operator off the route they moved to.
    view.rerender(<LoginRoute visible={false} />);
    releasePermissions();
    await settle(50);

    expect(pushMock).not.toHaveBeenCalled();
  });

  // ---------- forgot-password path + language ----------

  it("explains the owner-reset recovery path from the Forgot password link", async () => {
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.click(screen.getByRole("button", { name: "Forgot password?" }));
    const dialog = await screen.findByRole("dialog", { name: "Forgot password?" });
    // Honest copy: owner resets from Team, no self-service email recovery.
    expect(within(dialog).getByText(/reset by the farm owner/)).toBeInTheDocument();
    expect(within(dialog).getByText(/no self-service email recovery/)).toBeInTheDocument();

    // The explicit footer Close (the dialog also ships a corner X close).
    const closeButtons = within(dialog).getAllByRole("button", { name: "Close" });
    await user.click(closeButtons[closeButtons.length - 1]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("offers the language toggle and switches the error copy to Telugu", async () => {
    server.use(http.post("/api/auth/login", () => HttpResponse.error()));
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    const user = userEvent.setup();
    renderWithProviders(
      <LanguageProvider>
        <LoginPage />
      </LanguageProvider>,
    );

    expect(await screen.findByRole("button", { name: "తెలుగు" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("te");

    // The form is fully localized: labels and the submit button are Telugu.
    await user.type(screen.getByLabelText("ఇమెయిల్"), "demo@goatfarm.in");
    await user.type(screen.getByLabelText("పాస్‌వర్డ్"), "demo1234");
    await user.click(screen.getByRole("button", { name: "సైన్ ఇన్" }));

    expect(
      await screen.findByText("నెట్‌వర్క్ బలహీనంగా ఉంది — దయచేసి కనెక్షన్ సరిచూసి మళ్లీ ప్రయత్నించండి."),
    ).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });
});
