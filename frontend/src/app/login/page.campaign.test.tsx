/**
 * Login page — fresh-domain mutation campaign kills (2026-09): the forgot
 * password dialog's close affordance and the session-epoch guard on the
 * post-permissions navigation continuation.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server, TEST_FARMS } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import LoginPage from "./page";

const { pushMock, navState, sessionEpochShift } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  navState: { search: "" },
  sessionEpochShift: { value: 0 },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

// Same seam as farm-select's tests: shift the epoch the page observes without
// touching the real session, opening the "a newer session landed between the
// permissions read and the continuation" window deterministically.
vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    authSessionEpochValue: () => actual.authSessionEpochValue() + sessionEpochShift.value,
  };
});

const USER = { id: 2, email: "demo@goatfarm.in", name: "Demo User" };

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Email"), "demo@goatfarm.in");
  await user.type(screen.getByLabelText("Password"), "super-secret-password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginPage — campaign kills", () => {
  beforeEach(() => {
    pushMock.mockClear();
    navState.search = "";
    sessionEpochShift.value = 0;
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
      http.post("/api/auth/login", () =>
        HttpResponse.json({ access_token: "login-token", user: USER }),
      ),
      http.get("/api/auth/farms", () => HttpResponse.json(TEST_FARMS)),
    );
  });

  it("closes the forgot-password dialog from its own Close button", async () => {
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.click(screen.getByRole("button", { name: "Forgot password?" }));
    const dialog = await screen.findByRole("dialog");
    // Two closers share the accessible name: the sheet's built-in X and the
    // footer button this test targets. The footer one is last in DOM order.
    const closeButtons = within(dialog).getAllByRole("button", { name: "Close" });
    expect(closeButtons).toHaveLength(2);
    // The footer button carries visible text; the sheet's icon close hides
    // its label in a screen-reader-only span.
    const footerClose = closeButtons.find((b) => !b.querySelector(".sr-only"));
    expect(footerClose).toBeDefined();

    await user.click(footerClose!);
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("stays put when a newer session lands while the permissions read is in flight", async () => {
    let permissionRequests = 0;
    let releasePermissions!: () => void;
    server.use(
      http.get("/api/auth/permissions", () => {
        permissionRequests += 1;
        return new Promise((resolve) => {
          releasePermissions = () =>
            resolve(HttpResponse.json({ is_owner: true, permissions: ["dashboard.view"] }));
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await fillAndSubmit(user);
    await waitFor(() => expect(permissionRequests).toBe(1));
    // A newer session takes over the epoch before the continuation runs.
    sessionEpochShift.value = 1;
    await act(async () => {
      releasePermissions();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(pushMock).not.toHaveBeenCalled();
  });
});
