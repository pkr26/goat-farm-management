/**
 * Login page TOTP step (2026-09-16): when the server answers a correct
 * password with an mfa_token instead of a session, the page asks for the
 * 6-digit code, exchanges both at /api/auth/totp/challenge, and only then
 * signs in. A wrong code keeps the challenge step with an inline error.
 */

import { screen, waitFor } from "@testing-library/react";
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

function installMfaLogin() {
  let challengeBody: unknown;
  server.use(
    http.post("/api/auth/login", () =>
      HttpResponse.json({ mfa_token: "challenge-token", access_token: null, user: null }),
    ),
    http.post("/api/auth/totp/challenge", async ({ request }) => {
      challengeBody = await request.json();
      return HttpResponse.json({
        access_token: "session-token",
        token_type: "bearer",
        user: { id: 2, email: "demo@goatfarm.in", name: "Demo User", totp_state: "ACTIVE" },
      });
    }),
    http.get("/api/auth/farms", ({ request }) => {
      if (request.headers.get("Authorization") !== "Bearer session-token") {
        return new HttpResponse(null, { status: 401 });
      }
      return HttpResponse.json([
        { id: 7, name: "Demo Osmanabadi Farm", location: null, role: null },
      ]);
    }),
    permissionsHandler(["dashboard.view"]),
  );
  return () => challengeBody;
}

describe("LoginPage TOTP challenge step", () => {
  beforeEach(() => {
    pushMock.mockClear();
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );
  });

  it("demands the code, exchanges it, and only then signs in", async () => {
    const getChallengeBody = installMfaLogin();
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    // Password accepted: the code step appears; no session was created.
    const codeInput = await screen.findByLabelText(/authenticator code/i);
    expect(screen.getByText(/two-factor authentication/i)).toBeTruthy();

    await user.type(codeInput, "123456");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(getChallengeBody()).toEqual({
      mfa_token: "challenge-token",
      code: "123456",
    });
  });

  it("keeps the challenge step and shows the server message on a wrong code", async () => {
    installMfaLogin();
    server.use(
      http.post("/api/auth/totp/challenge", () =>
        HttpResponse.json({ detail: "Invalid or expired challenge." }, { status: 401 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    const codeInput = await screen.findByLabelText(/authenticator code/i);
    await user.type(codeInput, "000000");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByText(/That code is not valid right now/i)).toBeTruthy();
    // Still on the challenge step: the code input remains.
    expect(screen.getByLabelText(/authenticator code/i)).toBeTruthy();
    expect(pushMock).not.toHaveBeenCalledWith("/dashboard");
  });

  it("backs out of the challenge step to the plain password form", async () => {
    installMfaLogin();
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await screen.findByLabelText(/authenticator code/i);
    await user.click(screen.getByRole("button", { name: /back/i }));
    expect(screen.queryByLabelText(/authenticator code/i)).toBeNull();
    expect(screen.getByLabelText(/password/i)).toBeTruthy();
  });
});
