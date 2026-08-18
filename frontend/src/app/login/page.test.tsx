/**
 * Login page: successful submit posts credentials to /api/auth/login, the
 * returned token is stored via signIn (proven by the follow-up /api/auth/farms
 * call carrying it), and the router navigates to /dashboard. A 401 surfaces
 * the server-error line instead of navigating.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import LoginPage from "./page";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

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
      await screen.findByText("Could not sign in — is the backend running?"),
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
    await new Promise((resolve) => setTimeout(resolve, 50));

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
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(pushMock).not.toHaveBeenCalledWith("/farm-select");
    expect(pushMock).not.toHaveBeenCalled();
  });
});
