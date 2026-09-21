/**
 * Login page TOTP step (2026-09-16): when the server answers a correct
 * password with an mfa_token instead of a session, the page asks for the
 * 6-digit code, exchanges both at /api/auth/totp/challenge, and only then
 * signs in. A wrong code keeps the challenge step with an inline error.
 * Backing out must also drop any partial code draft: react-hook-form keeps
 * it, and a stale value would fail zod invisibly on the next password
 * submit (the totp error only renders on the code step) — a silent
 * sign-in no-op (2026-09-20 audit P1-10).
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

/** Counts refresh attempts: a wrong code is a 401 *answer* on the challenge
 * route and must not trigger the refresh machinery (2026-09-17 re-audit). */
function countRefreshCalls() {
  const calls = { count: 0 };
  server.use(
    http.post("/api/auth/refresh", () => {
      calls.count += 1;
      return new HttpResponse(null, { status: 401 });
    }),
  );
  return calls;
}

describe("LoginPage TOTP challenge step", () => {
  beforeEach(() => {
    pushMock.mockClear();
    countRefreshCalls();
  });

  it("demands the code, exchanges it, and only then signs in", async () => {
    const getChallengeBody = installMfaLogin();
    const refresh = countRefreshCalls();
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    // Password accepted: the code step appears; no session was created and
    // the typed credentials are no longer on screen.
    const codeInput = await screen.findByLabelText(/authenticator code/i);
    expect(screen.getByText(/two-factor authentication/i)).toBeTruthy();
    expect(screen.queryByLabelText(/password/i)).toBeNull();
    expect(screen.queryByLabelText(/email/i)).toBeNull();
    // Baseline after the mount bootstrap's own refresh attempt.
    const refreshBeforeSubmit = refresh.count;

    await user.type(codeInput, "123456");
    await user.click(screen.getByRole("button", { name: /^verify code$/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(getChallengeBody()).toEqual({
      mfa_token: "challenge-token",
      code: "123456",
    });
    expect(refresh.count).toBe(refreshBeforeSubmit);
  });

  it("keeps the challenge step and shows the server message on a wrong code", async () => {
    installMfaLogin();
    const refresh = countRefreshCalls();
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
    // Baseline after the mount bootstrap's own refresh attempt.
    const refreshBeforeSubmit = refresh.count;
    await user.type(codeInput, "000000");
    await user.click(screen.getByRole("button", { name: /^verify code$/i }));

    expect(await screen.findByText(/That code is not valid right now/i)).toBeTruthy();
    // Still on the challenge step: the code input remains.
    expect(screen.getByLabelText(/authenticator code/i)).toBeTruthy();
    expect(pushMock).not.toHaveBeenCalledWith("/dashboard");
    // The 401 is the answer; no refresh/rotation is spun up around it.
    expect(refresh.count).toBe(refreshBeforeSubmit);
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

  // 2026-09-20 audit P1-10: the draft left in the code box used to survive
  // Back in react-hook-form's state, so the next password submit failed the
  // 6-digit regex invisibly (the totp error only renders on the code step)
  // and never reached POST /api/auth/login. The Back handler must drop it.
  describe("backing out with a stale code draft (2026-09-20 audit P1-10)", () => {
    /** Every password attempt answers with a fresh mfa challenge; the bodies
     * prove the login POST actually fired. */
    function installMfaLoginCounting() {
      const bodies: Array<{ email: string; password: string }> = [];
      server.use(
        http.post("/api/auth/login", async ({ request }) => {
          bodies.push((await request.json()) as { email: string; password: string });
          return HttpResponse.json({
            mfa_token: "challenge-token",
            access_token: null,
            user: null,
          });
        }),
      );
      return bodies;
    }

    async function reachChallengeStep(
      user: ReturnType<typeof userEvent.setup>,
    ): Promise<HTMLElement> {
      await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
      await user.type(screen.getByLabelText(/password/i), "demo1234");
      await user.click(screen.getByRole("button", { name: /sign in/i }));
      return await screen.findByLabelText(/authenticator code/i);
    }

    it("re-posts the password after backing out of a partial code draft", async () => {
      const loginBodies = installMfaLoginCounting();
      const user = userEvent.setup();
      renderWithProviders(<LoginPage />);

      const codeInput = await reachChallengeStep(user);
      await user.type(codeInput, "123");
      await user.click(screen.getByRole("button", { name: /back/i }));
      expect(screen.getByLabelText(/password/i)).toBeTruthy();

      await user.click(screen.getByRole("button", { name: /sign in/i }));

      // Not a silent no-op: the password really posts again, and the fresh
      // challenge step starts from an empty code box, not the abandoned draft.
      await waitFor(() => expect(loginBodies).toHaveLength(2));
      expect(loginBodies[1]).toEqual({
        email: "demo@goatfarm.in",
        password: "demo1234",
      });
      expect(await screen.findByLabelText(/authenticator code/i)).toHaveValue("");
    });

    it("re-posts the password after backing out of a code box cleared to empty", async () => {
      const loginBodies = installMfaLoginCounting();
      const user = userEvent.setup();
      renderWithProviders(<LoginPage />);

      const codeInput = await reachChallengeStep(user);
      await user.type(codeInput, "12345");
      await user.clear(codeInput);
      await user.click(screen.getByRole("button", { name: /back/i }));

      await user.click(screen.getByRole("button", { name: /sign in/i }));

      await waitFor(() => expect(loginBodies).toHaveLength(2));
      expect(loginBodies[1]).toEqual({
        email: "demo@goatfarm.in",
        password: "demo1234",
      });
    });
  });
});
