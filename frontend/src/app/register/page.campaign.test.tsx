/**
 * Register page — fresh-domain mutation campaign kills (2026-09): a
 * registration without the optional name must succeed and navigate (the
 * name payload is optional; `values.name?.trim()` must survive undefined).
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server, TEST_FARMS } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import RegisterPage from "./page";

const { pushMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/register",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

describe("RegisterPage — campaign kills", () => {
  beforeEach(() => {
    pushMock.mockClear();
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
      http.post("/api/auth/register", () =>
        HttpResponse.json({
          access_token: "register-token",
          user: { id: 5, email: "new@goatfarm.in", name: null },
        }),
      ),
      http.get("/api/auth/farms", () => HttpResponse.json(TEST_FARMS)),
    );
  });

  it("registers successfully with the optional name left empty", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RegisterPage />);

    await user.type(screen.getByLabelText("Email"), "new@goatfarm.in");
    await user.type(screen.getByLabelText("Password"), "a-long-enough-password");
    await user.click(screen.getByRole("button", { name: /^Create account/ }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/farm-select"));
  });
});
