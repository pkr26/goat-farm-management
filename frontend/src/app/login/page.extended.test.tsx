/**
 * Login page — extended coverage beyond page.test.tsx (which covers the
 * success path, 401, and network failure): rendering, field attributes,
 * zod validation (empty/invalid email, missing password, boundary password),
 * non-401 server errors, error clearing on retry, and the in-flight
 * submitting state.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
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

/** Counts POST /api/auth/login attempts; returns a 401 by default. */
function trackLoginRequests(status = 401) {
  const state = { count: 0 };
  server.use(
    http.post("/api/auth/login", () => {
      state.count += 1;
      return HttpResponse.json({ detail: "Invalid email or password." }, { status });
    }),
  );
  return state;
}

async function renderPage() {
  const page = renderWithProviders(<LoginPage />);
  await page.waitForAuthIdle();
}

describe("LoginPage — rendering", () => {
  beforeEach(() => {
    pushMock.mockClear();
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );
  });

  it("renders the sign-in card with email, password and submit button", async () => {
    await renderPage();

    // Brand wordmark (Logo) appears in the desktop panel and the mobile header.
    expect(screen.getAllByText("GoatFarm").length).toBeGreaterThan(0);
    expect(screen.getByText("Sign in to your account")).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeEnabled();
  });

  it("email and password inputs carry the expected type/autocomplete attributes", async () => {
    await renderPage();

    const email = screen.getByLabelText(/email/i);
    expect(email).toHaveAttribute("type", "email");
    expect(email).toHaveAttribute("autocomplete", "email");

    const password = screen.getByLabelText(/password/i);
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveAttribute("autocomplete", "current-password");
  });

  it("links to the registration page", async () => {
    await renderPage();

    expect(screen.getByRole("link", { name: /register/i })).toHaveAttribute(
      "href",
      "/register",
    );
  });

  it("keeps a space between the sign-up prompt and the Register link", async () => {
    await renderPage();

    // The trailing {" "} is load-bearing: JSX drops the whitespace between a
    // text node and the following element, so without it the sentence runs
    // together as "No account?Register".
    const prompt = screen.getByRole("link", { name: /register/i }).closest("p");
    expect(prompt).toHaveTextContent(/^No account\? Register$/);
  });

  it("lists every product highlight in the brand panel", async () => {
    await renderPage();

    // The brand panel is the whole value proposition on the desktop layout;
    // a dropped entry (or a title rendered without its description) would
    // leave a silently blank column rather than fail anything else.
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
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("marks both fields valid and announces nothing before the first submit", async () => {
    await renderPage();

    // aria-invalid must be a definite "false" rather than simply absent, and
    // nothing may hold the alert role while the form is untouched — an empty
    // live region is still announced by a screen reader on page load.
    const email = screen.getByLabelText(/email/i);
    expect(email).toHaveAttribute("aria-invalid", "false");
    expect(email).not.toHaveAttribute("aria-describedby");

    const password = screen.getByLabelText(/password/i);
    expect(password).toHaveAttribute("aria-invalid", "false");
    expect(password).not.toHaveAttribute("aria-describedby");

    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  it("shows no validation errors before the first submit attempt", async () => {
    await renderPage();

    expect(screen.queryByText(/enter a valid email/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/password is required/i)).not.toBeInTheDocument();
  });
});

describe("LoginPage — validation", () => {
  beforeEach(() => {
    pushMock.mockClear();
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );
  });

  it("empty submit shows both errors and never hits the API", async () => {
    const login = trackLoginRequests();
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
    expect(screen.getByText("Password is required")).toBeInTheDocument();
    expect(login.count).toBe(0);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("points each invalid field at its own error message", async () => {
    const login = trackLoginRequests();
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();

    const email = screen.getByLabelText(/email/i);
    expect(email).toHaveAttribute("aria-invalid", "true");
    expect(email).toHaveAttribute("aria-describedby", "email-error");
    // The pointer has to resolve to the message itself: a describedby that
    // names a missing (or empty) node leaves the field announced as invalid
    // with no reason given.
    expect(email).toHaveAccessibleDescription("Enter a valid email address");

    const password = screen.getByLabelText(/password/i);
    expect(password).toHaveAttribute("aria-invalid", "true");
    expect(password).toHaveAttribute("aria-describedby", "password-error");
    expect(password).toHaveAccessibleDescription("Password is required");

    // Exactly the two field messages are alerts — no stray empty live region.
    expect(screen.getAllByRole("alert")).toHaveLength(2);
    expect(login.count).toBe(0);
  });

  it("rejects a malformed email and does not call the API", async () => {
    const login = trackLoginRequests();
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "not-an-email");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
    expect(login.count).toBe(0);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("rejects an email without a TLD", async () => {
    const login = trackLoginRequests();
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "farmer@goatfarm");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
    expect(login.count).toBe(0);
  });

  it("rejects an empty password while a valid email passes", async () => {
    const login = trackLoginRequests();
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Password is required")).toBeInTheDocument();
    expect(screen.queryByText("Enter a valid email address")).not.toBeInTheDocument();
    expect(login.count).toBe(0);
  });

  it("trims email whitespace and accepts a single-character password", async () => {
    let loginBody: unknown;
    server.use(
      http.post("/api/auth/login", async ({ request }) => {
        loginBody = await request.json();
        return HttpResponse.json({
          access_token: "tok",
          user: { id: 3, email: "demo@goatfarm.in", name: null },
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "  demo@goatfarm.in  ");
    await user.type(screen.getByLabelText(/password/i), "x");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(loginBody).toEqual({ email: "demo@goatfarm.in", password: "x" });
  });

  it("accepts an email pasted with non-breaking spaces around it", async () => {
    let loginBody: unknown;
    server.use(
      http.post("/api/auth/login", async ({ request }) => {
        loginBody = await request.json();
        return HttpResponse.json({
          access_token: "tok",
          user: { id: 3, email: "demo@goatfarm.in", name: null },
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    // An <input type="email"> sanitises only ASCII whitespace, so a value
    // copied out of a document or mail client keeps its U+00A0 padding —
    // only the schema's own .trim() rescues it from "Enter a valid email".
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "\u00A0demo@goatfarm.in\u00A0" },
    });
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByText("Enter a valid email address")).not.toBeInTheDocument();
    // The trimmed value is what reaches the API, not the padded original.
    expect(loginBody).toEqual({ email: "demo@goatfarm.in", password: "demo1234" });
  });

  it("rejects credentials just above the API's bounded input sizes", async () => {
    const login = trackLoginRequests();
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    const overlongEmail = `${"a".repeat(243)}@example.com`;
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: overlongEmail } });
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: "p".repeat(129) },
    });
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Email must be at most 254 characters")).toBeInTheDocument();
    expect(screen.getByText("Password must be at most 128 characters")).toBeInTheDocument();
    expect(login.count).toBe(0);
  });

  it("accepts credentials at both exact API size boundaries", async () => {
    let loginBody: unknown;
    server.use(
      http.post("/api/auth/login", async ({ request }) => {
        loginBody = await request.json();
        return HttpResponse.json({
          access_token: "tok",
          user: { id: 3, email: "boundary@example.com", name: null },
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    const boundaryEmail = `${"a".repeat(242)}@example.com`;
    const boundaryPassword = "p".repeat(128);

    await user.type(screen.getByLabelText(/email/i), boundaryEmail);
    await user.type(screen.getByLabelText(/password/i), boundaryPassword);
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(loginBody).toEqual({ email: boundaryEmail, password: boundaryPassword });
  });

  it("clears validation errors once the fields are fixed and the form resubmits", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          access_token: "tok",
          user: { id: 3, email: "demo@goatfarm.in", name: null },
        }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();

    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByText("Enter a valid email address")).not.toBeInTheDocument();
    expect(screen.queryByText("Password is required")).not.toBeInTheDocument();
  });
});

describe("LoginPage — server error handling", () => {
  beforeEach(() => {
    pushMock.mockClear();
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );
  });

  async function submitValidForm(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "demo1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));
  }

  it("shows the fixed credentials message on 401 regardless of the server detail", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ detail: "Account locked until tomorrow" }, { status: 401 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await submitValidForm(user);

    expect(await screen.findByText("Invalid email or password.")).toBeInTheDocument();
    expect(screen.queryByText("Account locked until tomorrow")).not.toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("announces the server error through its own live alert region", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ detail: "Service temporarily unavailable" }, { status: 503 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await submitValidForm(user);

    // The message has to live inside the role="alert" paragraph rather than
    // as loose text in the form: the fields are valid, so this alert is the
    // only announcement a screen reader gets that the sign-in failed.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Service temporarily unavailable");
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("surfaces the server detail on a 403", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ detail: "Forbidden" }, { status: 403 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await submitValidForm(user);

    expect(await screen.findByText("Forbidden")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("surfaces the rate-limit detail on a 429 instead of 'backend running?'", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json(
          { detail: "Too many attempts — please try again later." },
          { status: 429 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await submitValidForm(user);

    expect(
      await screen.findByText("Too many attempts — please try again later."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Could not sign in — is the backend running?"),
    ).not.toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("surfaces the server detail on a 500 with a detail body", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ detail: "database is locked" }, { status: 500 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await submitValidForm(user);

    expect(await screen.findByText("database is locked")).toBeInTheDocument();
    expect(
      screen.queryByText("Could not sign in — is the backend running?"),
    ).not.toBeInTheDocument();
  });

  it("clears a previous server error when a retry succeeds", async () => {
    let attempt = 0;
    server.use(
      http.post("/api/auth/login", () => {
        attempt += 1;
        if (attempt === 1) {
          return HttpResponse.json(
            { detail: "Invalid email or password." },
            { status: 401 },
          );
        }
        return HttpResponse.json({
          access_token: "tok",
          user: { id: 3, email: "demo@goatfarm.in", name: null },
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await submitValidForm(user);
    expect(await screen.findByText("Invalid email or password.")).toBeInTheDocument();

    // Fields are already filled from the first attempt — just resubmit.
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByText("Invalid email or password.")).not.toBeInTheDocument();
    expect(attempt).toBe(2);
  });

  it("disables the button and shows 'Signing in…' while the request is in flight", async () => {
    let resolveLogin: () => void = () => {};
    server.use(
      http.post(
        "/api/auth/login",
        () =>
          new Promise((resolve) => {
            resolveLogin = () =>
              resolve(
                HttpResponse.json({
                  access_token: "tok",
                  user: { id: 3, email: "demo@goatfarm.in", name: null },
                }),
              );
          }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await submitValidForm(user);

    const button = screen.getByRole("button", { name: /signing in…/i });
    expect(button).toBeDisabled();

    resolveLogin();
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
  });
});
