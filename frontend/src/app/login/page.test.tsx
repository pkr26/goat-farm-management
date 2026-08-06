/**
 * Login page: successful submit posts credentials to /api/auth/login, the
 * returned token is stored via signIn (proven by the follow-up /api/auth/farms
 * call carrying it), and the router navigates to /dashboard. A 401 surfaces
 * the server-error line instead of navigating.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
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
});
