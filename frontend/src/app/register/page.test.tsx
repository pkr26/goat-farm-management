/**
 * Register page: rendering, zod validation (email format, 12-character
 * password minimum with the exact boundary, optional name capped at 120
 * chars), the submit contract (blank name → null in the POST body, token
 * stored via signIn, navigation to /farm-select), and server-error
 * surfacing (any ApiError's detail is shown; only a network failure gets
 * the generic fallback —), plus the accessibility contract (aria-invalid /
 * aria-describedby wiring, role="alert" live regions) and the
 * navigated-away-mid-request guards.
 *
 * Note: the page has no confirm-password field, so mismatch validation
 * does not exist to test.
 */

import { act, fireEvent, screen, waitFor } from "@testing-library/react";
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
      overrides.password ?? "secret123456",
    );
  }

  async function renderPage() {
    const page = renderWithProviders(<RegisterPage />);
    await page.waitForAuthIdle();
  }

  describe("rendering", () => {
    it("renders the create-account card with all fields and the submit button", async () => {
      await renderPage();

      expect(screen.getByText("Create your account")).toBeInTheDocument();
      expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /create account/i })).toBeEnabled();
    });

    it("password field asks for a new password via autocomplete", async () => {
      await renderPage();

      expect(screen.getByLabelText(/password/i)).toHaveAttribute(
        "autocomplete",
        "new-password",
      );
      expect(screen.getByLabelText(/email/i)).toHaveAttribute("autocomplete", "email");
    });

    it("links back to the sign-in page", async () => {
      await renderPage();

      expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute(
        "href",
        "/login",
      );
    });

    it("has no confirm-password field (single password input only)", async () => {
      await renderPage();

      expect(screen.getAllByLabelText(/password/i)).toHaveLength(1);
    });

    it("lists all three product highlights in the brand panel", async () => {
      await renderPage();

      // The brand panel is the page's only sales copy; each highlight is one
      // list item carrying both a title and its supporting line.
      expect(screen.getAllByRole("listitem")).toHaveLength(3);
      expect(screen.getByText("Complete herd records")).toBeInTheDocument();
      expect(
        screen.getByText("Track every animal, tag and lineage in one place."),
      ).toBeInTheDocument();
      expect(screen.getByText("Proactive health care")).toBeInTheDocument();
      expect(
        screen.getByText("Stay ahead of vaccinations, treatments and checkups."),
      ).toBeInTheDocument();
      expect(screen.getByText("Insights that pay off")).toBeInTheDocument();
      expect(
        screen.getByText("Breeding, kidding and finance reports at a glance."),
      ).toBeInTheDocument();
    });

    it("separates the sign-in prompt from its link with a space", async () => {
      await renderPage();

      // The {" "} between the sentence and the <Link> is load-bearing: drop it
      // and the footer reads "Already have an account?Sign in".
      expect(screen.getByText(/already have an account/i)).toHaveTextContent(
        /^Already have an account\? Sign in$/,
      );
    });

    it("marks no field invalid and shows no alert before the first submit", async () => {
      await renderPage();

      // aria-invalid must be ABSENT (not "false") on an untouched field, and
      // no describedby may point at an error paragraph that does not exist.
      for (const field of [/name/i, /email/i, /password/i]) {
        expect(screen.getByLabelText(field)).not.toHaveAttribute("aria-invalid");
        expect(screen.getByLabelText(field)).not.toHaveAttribute("aria-describedby");
      }
      // No empty live region either — screen readers would announce nothing
      // but its presence still pollutes the alert queries below.
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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
        screen.getByText("Password must be at least 12 characters"),
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

    it("rejects an 11-character password (just below the minimum)", async () => {
      const register = trackRegisterRequests();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      await fillValid(user, { password: "12345678901" });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      expect(
        await screen.findByText("Password must be at least 12 characters"),
      ).toBeInTheDocument();
      expect(register.count).toBe(0);
      expect(pushMock).not.toHaveBeenCalled();
    });

    it("accepts a 12-character password (exact boundary)", async () => {
      const register = registerSuccess();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      await fillValid(user, { password: "123456789012" });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(register.body).toMatchObject({ password: "123456789012" });
    });

    it("rejects email and password values just above the API ceilings", async () => {
      const register = trackRegisterRequests();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      fireEvent.change(screen.getByLabelText(/email/i), {
        target: { value: `${"a".repeat(243)}@example.com` },
      });
      fireEvent.change(screen.getByLabelText(/password/i), {
        target: { value: "p".repeat(129) },
      });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      expect(await screen.findByText("Email must be at most 254 characters")).toBeInTheDocument();
      expect(screen.getByText("Password must be at most 128 characters")).toBeInTheDocument();
      expect(register.count).toBe(0);
    });

    it("accepts email and password values at both exact API ceilings", async () => {
      const register = registerSuccess();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);
      const boundaryEmail = `${"a".repeat(242)}@example.com`;
      const boundaryPassword = "p".repeat(128);

      await fillValid(user, { email: boundaryEmail, password: boundaryPassword });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(register.body).toMatchObject({
        email: boundaryEmail,
        password: boundaryPassword,
      });
    });

    it("rejects a name longer than 120 characters", async () => {
      const register = trackRegisterRequests();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      fireEvent.change(screen.getByLabelText(/name/i), {
        target: { value: "x".repeat(121) },
      });
      await fillValid(user);
      await user.click(screen.getByRole("button", { name: /create account/i }));

      // zod max(120) violation — message mentions the 120-character cap.
      expect(await screen.findByText(/120/)).toBeInTheDocument();
      expect(register.count).toBe(0);
    });

    it("wires every field error to its input via aria-invalid and aria-describedby", async () => {
      const register = trackRegisterRequests();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      fireEvent.change(screen.getByLabelText(/name/i), {
        target: { value: "x".repeat(121) },
      });
      await fillValid(user, { email: "not-an-email", password: "short" });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();

      // A screen reader needs both halves: the invalid state on the control
      // AND the id of the paragraph that explains why, so the reason is
      // announced with the field rather than left visual-only.
      expect(screen.getByLabelText(/name/i)).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByLabelText(/name/i)).toHaveAttribute(
        "aria-describedby",
        "register-name-error",
      );
      expect(screen.getByLabelText(/email/i)).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByLabelText(/email/i)).toHaveAttribute(
        "aria-describedby",
        "register-email-error",
      );
      expect(screen.getByLabelText(/password/i)).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByLabelText(/password/i)).toHaveAttribute(
        "aria-describedby",
        "register-password-error",
      );
      // Each referenced id must actually resolve to that field's message.
      expect(document.getElementById("register-name-error")).toHaveTextContent(/120/);
      expect(document.getElementById("register-email-error")).toHaveTextContent(
        "Enter a valid email address",
      );
      expect(document.getElementById("register-password-error")).toHaveTextContent(
        "Password must be at least 12 characters",
      );
      expect(register.count).toBe(0);
    });

    it("accepts an email padded with non-breaking spaces and posts it trimmed", async () => {
      const register = registerSuccess();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      // A NBSP survives the browser's own email-input sanitisation (that only
      // strips ASCII whitespace), so the schema's .trim() is the one thing
      // standing between an address pasted from chat/PDF and a bogus
      // "Enter a valid email address" rejection.
      fireEvent.change(screen.getByLabelText(/email/i), {
        target: { value: "\u00A0new@goatfarm.in\u00A0" },
      });
      await user.type(screen.getByLabelText(/password/i), "secret123456");
      await user.click(screen.getByRole("button", { name: /create account/i }));

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(register.body).toMatchObject({ email: "new@goatfarm.in" });
      expect(
        screen.queryByText("Enter a valid email address"),
      ).not.toBeInTheDocument();
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

      await fillValid(user, { name: "New Farmer", email: "  new@goatfarm.in  " });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(register.body).toEqual({
        name: "New Farmer",
        email: "new@goatfarm.in",
        password: "secret123456",
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
        password: "secret123456",
      });
    });

    it("sends name: null when the optional name is only whitespace", async () => {
      const register = registerSuccess();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      // "   " is a truthy string: without the trim the backend would store a
      // blank-looking farmer name instead of nothing at all.
      await fillValid(user, { name: "   " });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(register.body).toEqual({
        name: null,
        email: "new@goatfarm.in",
        password: "secret123456",
      });
    });

    it("trims the padding off a name typed with stray spaces", async () => {
      const register = registerSuccess();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);

      // Unlike the email input, a text input keeps leading/trailing spaces,
      // so the page is what has to clean them up before they are stored.
      await fillValid(user, { name: "  Rani Patil  " });
      await user.click(screen.getByRole("button", { name: /create account/i }));

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(register.body).toMatchObject({ name: "Rani Patil" });
    });

    it("locks the form the instant a submit starts, before validation resolves", async () => {
      const register = registerSuccess();
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);
      await fillValid(user);

      const form = screen
        .getByRole("button", { name: /create account/i })
        .closest("form")!;
      fireEvent.submit(form);

      // react-hook-form flips isSubmitting synchronously, one render BEFORE
      // its async resolver hands control to onSubmit and the single-flight
      // guard turns `pending` on. Both halves of the disabled condition
      // matter: in this window only isSubmitting is set, and the controls
      // must already be locked so a second Enter cannot edit or resubmit.
      const button = screen.getByRole("button", { name: /creating account…/i });
      expect(button).toHaveAttribute("disabled");
      expect(screen.getByLabelText(/email/i).closest("fieldset")).toBeDisabled();

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(register.body).toMatchObject({ email: "new@goatfarm.in" });
    });

    it("does not establish a session when the operator leaves before the response", async () => {
      let farmsCalls = 0;
      let farmsAuthorization: string | null = null;
      let releaseRegister!: () => void;
      let registerStarted!: () => void;
      const started = new Promise<void>((resolve) => {
        registerStarted = resolve;
      });
      const gate = new Promise<void>((resolve) => {
        releaseRegister = resolve;
      });
      server.use(
        http.post("/api/auth/register", async () => {
          registerStarted();
          await gate;
          return HttpResponse.json({
            access_token: "stale-register-token",
            user: { id: 9, email: "new@goatfarm.in", name: null },
          });
        }),
        http.get("/api/auth/farms", ({ request }) => {
          farmsCalls += 1;
          farmsAuthorization = request.headers.get("Authorization");
          return HttpResponse.json([]);
        }),
      );
      const user = userEvent.setup();
      const rendered = renderWithProviders(<RegisterPage />);
      await fillValid(user);
      await user.click(screen.getByRole("button", { name: /create account/i }));
      await started;

      // Client-side navigation away from /register: the page unmounts while
      // the AuthProvider in the layout above stays mounted (unlike a whole
      // tree teardown, so signIn here WOULD succeed if it were reached).
      rendered.rerender(<p>navigated away</p>);
      await act(async () => {
        releaseRegister();
        await new Promise((resolve) => setTimeout(resolve, 50));
      });

      // The late token must not be installed behind the operator's back.
      expect(farmsCalls).toBe(0);
      expect(farmsAuthorization).toBeNull();
      expect(pushMock).not.toHaveBeenCalled();
    });

    it("does not navigate when the operator leaves while sign-in is finishing", async () => {
      let releaseFarms!: () => void;
      let farmsStarted!: () => void;
      const started = new Promise<void>((resolve) => {
        farmsStarted = resolve;
      });
      const gate = new Promise<void>((resolve) => {
        releaseFarms = resolve;
      });
      server.use(
        http.post("/api/auth/register", () =>
          HttpResponse.json({
            access_token: "register-token",
            user: { id: 9, email: "new@goatfarm.in", name: null },
          }),
        ),
        // signIn's own membership fetch is the slow leg here, so the page
        // unmounts AFTER registration succeeded but BEFORE signIn resolves.
        http.get("/api/auth/farms", async () => {
          farmsStarted();
          await gate;
          return HttpResponse.json([]);
        }),
      );
      const user = userEvent.setup();
      const rendered = renderWithProviders(<RegisterPage />);
      await fillValid(user);
      await user.click(screen.getByRole("button", { name: /create account/i }));
      await started;

      rendered.rerender(<p>navigated away</p>);
      await act(async () => {
        releaseFarms();
        await new Promise((resolve) => setTimeout(resolve, 50));
      });

      // Router pushes are global: yanking a departed page's destination in
      // would throw the operator off whatever screen they moved to.
      expect(pushMock).not.toHaveBeenCalled();
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
      expect(screen.getByLabelText(/name/i)).toBeDisabled();
      expect(screen.getByLabelText(/email/i)).toBeDisabled();
      expect(screen.getByLabelText(/password/i)).toBeDisabled();
      expect(pushMock).not.toHaveBeenCalled();

      resolveRegister();
      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
    });

    it("coalesces same-tick form submissions into one registration request", async () => {
      let registerCalls = 0;
      let releaseRegister!: () => void;
      const gate = new Promise<void>((resolve) => {
        releaseRegister = resolve;
      });
      server.use(
        http.post("/api/auth/register", async () => {
          registerCalls += 1;
          await gate;
          return HttpResponse.json({
            access_token: "register-token",
            user: { id: 9, email: "new@goatfarm.in", name: null },
          });
        }),
        http.get("/api/auth/farms", () => HttpResponse.json([])),
      );
      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);
      await fillValid(user);

      const form = screen
        .getByRole("button", { name: /create account/i })
        .closest("form")!;
      fireEvent.submit(form);
      fireEvent.submit(form);
      await waitFor(() => expect(registerCalls).toBeGreaterThan(0));
      releaseRegister();

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
      expect(registerCalls).toBe(1);
    });

    it("does not establish or navigate a registration page that unmounted", async () => {
      let releaseRegister!: () => void;
      let registerStarted!: () => void;
      const started = new Promise<void>((resolve) => {
        registerStarted = resolve;
      });
      const gate = new Promise<void>((resolve) => {
        releaseRegister = resolve;
      });
      server.use(
        http.post("/api/auth/register", async () => {
          registerStarted();
          await gate;
          return HttpResponse.json({
            access_token: "stale-register-token",
            user: { id: 9, email: "new@goatfarm.in", name: null },
          });
        }),
      );
      const user = userEvent.setup();
      const rendered = renderWithProviders(<RegisterPage />);
      await fillValid(user);
      await user.click(screen.getByRole("button", { name: /create account/i }));
      await started;

      rendered.unmount();
      releaseRegister();
      await new Promise((resolve) => setTimeout(resolve, 50));

      expect(pushMock).not.toHaveBeenCalled();
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

    it("announces the server error in a live region rather than as bare text", async () => {
      server.use(
        http.post("/api/auth/register", () =>
          HttpResponse.json({ detail: "Email already registered" }, { status: 400 }),
        ),
      );

      const user = userEvent.setup();
      renderWithProviders(<RegisterPage />);
      await submitValid(user);

      // The message has to sit INSIDE role="alert"; a bare text node beside
      // the button is silent for anyone not looking at that corner of the
      // form. The submitted form is otherwise valid, so this is the only
      // alert on the page.
      const alerts = await screen.findAllByRole("alert");
      expect(alerts).toHaveLength(1);
      expect(alerts[0]).toHaveTextContent("Email already registered");
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
