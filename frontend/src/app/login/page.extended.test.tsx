/**
 * Login page — extended coverage beyond page.test.tsx (which covers the
 * success path, 401, and network failure): rendering, field attributes,
 * zod validation (empty/invalid email, missing password, boundary password),
 * non-401 server errors, error clearing on retry, and the in-flight
 * submitting state.
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

describe("LoginPage — rendering", () => {
  beforeEach(() => {
    pushMock.mockClear();
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );
  });

  it("renders the sign-in card with email, password and submit button", () => {
    renderWithProviders(<LoginPage />);

    expect(screen.getByText("🐐 GoatFarm")).toBeInTheDocument();
    expect(screen.getByText("Sign in to your account")).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeEnabled();
  });

  it("email and password inputs carry the expected type/autocomplete attributes", () => {
    renderWithProviders(<LoginPage />);

    const email = screen.getByLabelText(/email/i);
    expect(email).toHaveAttribute("type", "email");
    expect(email).toHaveAttribute("autocomplete", "email");

    const password = screen.getByLabelText(/password/i);
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveAttribute("autocomplete", "current-password");
  });

  it("links to the registration page", () => {
    renderWithProviders(<LoginPage />);

    expect(screen.getByRole("link", { name: /register/i })).toHaveAttribute(
      "href",
      "/register",
    );
  });

  it("shows no validation errors before the first submit attempt", () => {
    renderWithProviders(<LoginPage />);

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

  it("accepts a single-character password (min length is 1)", async () => {
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

    await user.type(screen.getByLabelText(/email/i), "demo@goatfarm.in");
    await user.type(screen.getByLabelText(/password/i), "x");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
    expect(loginBody).toEqual({ email: "demo@goatfarm.in", password: "x" });
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

  it("shows the backend-unreachable message on a 403", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ detail: "Forbidden" }, { status: 403 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await submitValidForm(user);

    expect(
      await screen.findByText("Could not sign in — is the backend running?"),
    ).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("shows the backend-unreachable message on a 500 with a detail body", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ detail: "database is locked" }, { status: 500 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);
    await submitValidForm(user);

    expect(
      await screen.findByText("Could not sign in — is the backend running?"),
    ).toBeInTheDocument();
    expect(screen.queryByText("database is locked")).not.toBeInTheDocument();
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
