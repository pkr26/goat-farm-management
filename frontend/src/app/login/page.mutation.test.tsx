/**
 * Login page — mutation-hardening suite: returnTo deep-link handling,
 * the mounted-guard happy path, the 401 message and the network-failure
 * fallback.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server, TEST_FARMS } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import LoginPage from "./page";

const { pushMock, navState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  navState: { search: "" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

const USER = { id: 2, email: "demo@goatfarm.in", name: "Demo User" };

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Email"), "demo@goatfarm.in");
  await user.type(screen.getByLabelText("Password"), "super-secret-password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginPage — mutation targets", () => {
  beforeEach(() => {
    pushMock.mockClear();
    navState.search = "";
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
      http.post("/api/auth/login", () =>
        HttpResponse.json({ access_token: "login-token", user: USER }),
      ),
      // The login continuation only routes to /farm-select for a farmless
      // account; these tests sign in a member, so the farms list is non-empty.
      http.get("/api/auth/farms", () => HttpResponse.json(TEST_FARMS)),
    );
  });

  it("keeps a permitted returnTo destination through the whole sign-in", async () => {
    navState.search = "?returnTo=/animals";
    server.use(permissionsHandler(["animals.view", "tasks.view"]));
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await fillAndSubmit(user);
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/animals"));
  });

  it("honours a returnTo that is not the first permitted module", async () => {
    // If returnTo were dropped, the fallback would send the operator to the
    // first permitted module (/tasks) instead of their deep link.
    navState.search = "?returnTo=/reports";
    server.use(permissionsHandler(["tasks.view", "reports.view"]));
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await fillAndSubmit(user);
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/reports"));
    expect(pushMock).not.toHaveBeenCalledWith("/tasks");
  });

  it("lands on the first permitted module without a returnTo", async () => {
    server.use(permissionsHandler(["tasks.view", "reports.view"]));
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await fillAndSubmit(user);
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/tasks"));
  });

  it("recovers to farm selection when permission discovery fails", async () => {
    server.use(http.get("/api/auth/permissions", () => HttpResponse.error()));
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await fillAndSubmit(user);
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
  });

  it("routes a farmless account to farm selection without requesting permissions", async () => {
    let permissionCalls = 0;
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json([])),
      http.get("/api/auth/permissions", () => {
        permissionCalls += 1;
        // A farmless account has no X-Farm-Id: the backend rejects the call.
        return HttpResponse.json({ detail: "farm id required" }, { status: 422 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await fillAndSubmit(user);
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
    // The early return must skip the request entirely — navigation via the
    // old 422-catch fallback would look identical to the router alone.
    expect(permissionCalls).toBe(0);
  });

  it("surfaces the server's own words for a 401", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ detail: "bad credentials" }, { status: 401 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await fillAndSubmit(user);
    expect(await screen.findByText("Invalid email or password.")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("asks whether the backend is running on a network failure", async () => {
    server.use(http.post("/api/auth/login", () => HttpResponse.error()));
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await fillAndSubmit(user);
    expect(
      await screen.findByText("Could not sign in — is the backend running?"),
    ).toBeInTheDocument();
  });
});
