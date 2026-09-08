/**
 * Register page — mutation-hardening suite: the trimmed/absent name in the
 * payload, the success navigation, validation messages and the server-error
 * surfacing.
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

const USER = { id: 5, email: "new@goatfarm.in", name: "New User" };

async function fillValid(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/Email/), "new@goatfarm.in");
  await user.type(screen.getByLabelText("Password"), "twelve-characters");
}

describe("RegisterPage — mutation targets", () => {
  let bodies: Array<Record<string, unknown>>;

  beforeEach(() => {
    pushMock.mockClear();
    bodies = [];
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
      http.post("/api/auth/register", async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ access_token: "reg-token", user: USER });
      }),
      http.get("/api/auth/farms", () => HttpResponse.json([])),
    );
  });

  it("trims a typed name into the payload and lands on farm selection", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RegisterPage />);

    await user.type(screen.getByLabelText(/Name \(optional\)/), "  Ram  ");
    await fillValid(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ name: "Ram", email: "new@goatfarm.in", password: "twelve-characters" });
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
  });

  it("sends a null name when the optional field is left blank or whitespace", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RegisterPage />);

    await user.type(screen.getByLabelText(/Name \(optional\)/), "   ");
    await fillValid(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ name: null, email: "new@goatfarm.in", password: "twelve-characters" });
  });

  it("registers cleanly when the optional name field is never touched", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RegisterPage />);

    // No name interaction at all: the draft value is undefined, not "".
    await fillValid(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ name: null, email: "new@goatfarm.in", password: "twelve-characters" });
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
    expect(
      screen.queryByText("Network is weak — please check your connection and try again."),
    ).not.toBeInTheDocument();
  });

  it("blocks a short password inline", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RegisterPage />);

    await user.type(screen.getByLabelText(/Email/), "new@goatfarm.in");
    await user.type(screen.getByLabelText("Password"), "short");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Password must be at least 12 characters")).toBeInTheDocument();
    expect(bodies).toHaveLength(0);
  });

  it("blocks a malformed email inline", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RegisterPage />);

    await user.type(screen.getByLabelText(/Email/), "not-an-email");
    await user.type(screen.getByLabelText("Password"), "twelve-characters");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
    expect(bodies).toHaveLength(0);
  });

  it("surfaces the server's own message for a duplicate email", async () => {
    server.use(
      http.post("/api/auth/register", () =>
        HttpResponse.json({ detail: "Email already registered" }, { status: 400 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<RegisterPage />);

    await fillValid(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Email already registered")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });
});
