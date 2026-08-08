/**
 * Register page: rendering, zod validation (email format, 8-character
 * password minimum with the exact boundary, optional name capped at 120
 * chars), the submit contract (blank name → null in the POST body, token
 * stored via signIn, navigation to /farm-select), and server-error
 * surfacing (any ApiError's detail is shown; only a network failure gets
 * the generic fallback —).
 *
 * Note: the page has no confirm-password field, so mismatch validation
 * does not exist to test.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import RegisterPage from "./page";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/register",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

/** Counts POST /api/auth/register attempts; 400 by default. */
function trackRegisterRequests(status = 400) {
  const state = { count: 0 };
  server.use(
    http.post("/api/auth/register", () => {
      state.count += 1;
      return HttpResponse.json({ detail: "Email already registered" }, { status });
    }),
  );
  return state;
}

function registerSuccess() {
  const state: { body: unknown } = { body: null };
  server.use(
    http.post("/api/auth/register", async ({ request }) => {
      state.body = await request.json();
      return HttpResponse.json({
        access_token: "register-token",
        user: { id: 9, email: "new@goatfarm.in", name: null },
      });
    }),
  );
  return state;
}

describe("RegisterPage", () => {
  beforeEach(() => {
    pushMock.mockClear();
    // No existing session.
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );
  });

  async function fillValid(
    user: ReturnType<typeof userEvent.setup>,
    overrides: { name?: string; email?: string; password?: string } = {},
  ) {
    if (overrides.name !== undefined) {
      await user.type(screen.getByLabelText(/name/i), overrides.name);
    }
    await user.type(screen.getByLabelText(/email/i), overrides.email ?? "new@goatfarm.in");
    await user.type(
      screen.getByLabelText(/password/i),
      overrides.password ?? "secret123",
    );
  }

  describe("rendering", () => {
    it("renders the create-account card with all fields and the submit button", () => {
      renderWithProviders(<RegisterPage />);

      expect(screen.getByText("Create your account")).toBeInTheDocument();
      expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /create account/i })).toBeEnabled();
    });

    it("password field asks for a new password via autocomplete", () => {
      renderWithProviders(<RegisterPage />);

      expect(screen.getByLabelText(/password/i)).toHaveAttribute(
        "autocomplete",
        "new-password",
      );
      expect(screen.getByLabelText(/email/i)).toHaveAttribute("autocomplete", "email");
    });

    it("links back to the sign-in page", () => {
      renderWithProviders(<RegisterPage />);

      expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute(
        "href",
        "/login",
      );
    });

    it("has no confirm-password field (single password input only)", () => {
      renderWithProviders(<RegisterPage />);

      expect(screen.getAllByLabelText(/password/i)).toHaveLength(1);
    });
  });

  describe("validation", () => {
    it("empty submit shows email and password errors but none for the optional name", async () => {
      const register = trackRegisterRequests();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      await user.click(screen.getByRole("button", { name: /create account/i }));

      expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
      expect(
        screen.getByText("Password must be at least 8 characters"),
      ).toBeInTheDocument();
      expect(register.count).toBe(0);
      expect(pushMock).not.toHaveBeenCalled();
    });

    it("rejects a malformed email", async () => {
      const register = trackRegisterRequests();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      await fillValid(user, { email: "not-an-email" });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
      expect(register.count).toBe(0);
    });

    it("rejects a 7-character password (just below the minimum)", async () => {
      const register = trackRegisterRequests();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      await fillValid(user, { password: "1234567" });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      expect(
        await screen.findByText("Password must be at least 8 characters"),
      ).toBeInTheDocument();
      expect(register.count).toBe(0);
      expect(pushMock).not.toHaveBeenCalled();
    });

    it("accepts an 8-character password (exact boundary)", async () => {
      const register = registerSuccess();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      await fillValid(user, { password: "12345678" });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(register.body).toMatchObject({ password: "12345678" });
    });

    it("rejects a name longer than 120 characters", async () => {
      const register = trackRegisterRequests();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      await fillValid(user, { name: "x".repeat(121) });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      // zod max(120) violation — message mentions the 120-character cap.
      expect(await screen.findByText(/120/)).toBeInTheDocument();
      expect(register.count).toBe(0);
    });
  });

  describe("submit contract", () => {
    it("posts credentials, stores the token via signIn and navigates to /farm-select", async () => {
      const register = registerSuccess();
      let farmsAuthorization: string | null = null;
      server.use(
        http.get("/api/auth/farms", ({ request }) => {
          farmsAuthorization = request.headers.get("Authorization");
          return HttpResponse.json([]);
        }),
      );

      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      await fillValid(user, { name: "New Farmer" });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(register.body).toEqual({
        name: "New Farmer",
        email: "new@goatfarm.in",
        password: "secret123",
      });
      // signIn stored the access token: the farms fetch carried it.
      expect(farmsAuthorization).toBe("Bearer register-token");
    });

    it("sends name: null when the optional name is left blank", async () => {
      const register = registerSuccess();
      server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));

      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      await fillValid(user);
      await user.click(screen.getByRole("button", { name: /create account/i }));

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(register.body).toEqual({
        name: null,
        email: "new@goatfarm.in",
        password: "secret123",
      });
    });

    it("disables the button and shows 'Creating account…' while in flight", async () => {
      let resolveRegister: () => void = () => {};
      server.use(
        http.post(
          "/api/auth/register",
          () =>
            new Promise((resolve) => {
              resolveRegister = () =>
                resolve(
                  HttpResponse.json({
                    access_token: "tok",
                    user: { id: 9, email: "new@goatfarm.in", name: null },
                  }),
                );
            }),
        ),
      );

      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);
      await fillValid(user);
      await user.click(screen.getByRole("button", { name: /create account/i }));

      const button = screen.getByRole("button", { name: /creating account…/i });
      expect(button).toBeDisabled();
      expect(pushMock).not.toHaveBeenCalled();

      resolveRegister();
      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
    });
  });

  describe("server errors", () => {
    async function submitValid(user: ReturnType<typeof userEvent.setup>) {
      await fillValid(user);
      await user.click(screen.getByRole("button", { name: /create account/i }));
    }

    it("surfaces the server detail on a 400 (e.g. duplicate email)", async () => {
      server.use(
        http.post("/api/auth/register", () =>
          HttpResponse.json({ detail: "Email already registered" }, { status: 400 }),
        ),
      );

      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);
      await submitValid(user);

      expect(await screen.findByText("Email already registered")).toBeInTheDocument();
      expect(pushMock).not.toHaveBeenCalled();
    });

    it("surfaces the server detail on a 409", async () => {
      server.use(
        http.post("/api/auth/register", () =>
          HttpResponse.json({ detail: "conflict detail" }, { status: 409 }),
        ),
      );

      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);
      await submitValid(user);

      expect(await screen.findByText("conflict detail")).toBeInTheDocument();
      expect(
        screen.queryByText("Could not register — is the backend running?"),
      ).not.toBeInTheDocument();
    });

    it("surfaces the rate-limit detail on a 429", async () => {
      server.use(
        http.post("/api/auth/register", () =>
          HttpResponse.json(
            { detail: "Too many attempts — please try again later." },
            { status: 429 },
          ),
        ),
      );

      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);
      await submitValid(user);

      expect(
        await screen.findByText("Too many attempts — please try again later."),
      ).toBeInTheDocument();
      expect(pushMock).not.toHaveBeenCalled();
    });

    it("surfaces the server detail on a 500", async () => {
      server.use(
        http.post("/api/auth/register", () =>
          HttpResponse.json({ detail: "unique constraint failed" }, { status: 500 }),
        ),
      );

      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);
      await submitValid(user);

      expect(await screen.findByText("unique constraint failed")).toBeInTheDocument();
      expect(pushMock).not.toHaveBeenCalled();
    });

    it("shows the generic message on a network failure", async () => {
      server.use(http.post("/api/auth/register", () => HttpResponse.error()));

      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);
      await submitValid(user);

      expect(
        await screen.findByText("Could not register — is the backend running?"),
      ).toBeInTheDocument();
    });

    it("clears a previous server error when a retry succeeds", async () => {
      let attempt = 0;
      server.use(
        http.post("/api/auth/register", () => {
          attempt += 1;
          if (attempt === 1) {
            return HttpResponse.json(
              { detail: "Email already registered" },
              { status: 400 },
            );
          }
          return HttpResponse.json({
            access_token: "tok",
            user: { id: 9, email: "new@goatfarm.in", name: null },
          });
        }),
      );

      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      await submitValid(user);
      expect(await screen.findByText("Email already registered")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: /create account/i }));
      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(screen.queryByText("Email already registered")).not.toBeInTheDocument();
      expect(attempt).toBe(2);
    });
  });
});
