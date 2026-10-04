/**
 * Team page DOM caps: the worker invite form, the reset-password form, the
 * notification phone field and the role form all bound their text inputs in the DOM
 * at the exact schema limits.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import TeamPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/team",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

const ROLE_HELPER = {
  id: 11,
  code: null,
  name: "Helper",
  description: "general farm help",
  permissions: [],
  revision: 1,
  member_count: 1,
};

const MEMBER_SELF = {
  id: 1,
  user_id: TEST_USER.id,
  email: TEST_USER.email,
  name: TEST_USER.name,
  role_id: null,
  role_name: null,
  is_active: true,
  can_reset_password: false,
  reset_password_block_reason: "This account must use self-service password recovery.",
};

const MEMBER_RAVI = {
  id: 2,
  user_id: 22,
  email: "ravi@example.com",
  name: "Ravi Kumar",
  role_id: 11,
  role_name: "Helper",
  is_active: true,
  can_reset_password: true,
  reset_password_block_reason: null,
};

function teamPayload() {
  return {
    memberships: [MEMBER_SELF, MEMBER_RAVI],
    roles: [ROLE_HELPER],
    permission_groups: [],
    permission_labels: {},
  };
}

describe("TeamPage DOM caps", () => {
  beforeEach(() => {
    server.use(
      // The default owner permissions handler: Add worker and Reset
      // password are owner-gated surfaces.
      http.get("/api/team", () => HttpResponse.json(teamPayload())),
      http.post("/api/team/workers", () => HttpResponse.json({}, { status: 201 })),
      http.get("/api/team/workers/:membershipId/notifications", () =>
        HttpResponse.json({
          email_digest: false,
          push: false,
          digest_hour_utc: 6,
          phone: null,
          whatsapp: false,
        }),
      ),
      http.put("/api/team/workers/:membershipId/notifications", () =>
        HttpResponse.json({}),
      ),
    );
  });

  async function renderLoaded() {
    renderWithProviders(<TeamPage />);
    await screen.findAllByText("Ravi Kumar");
  }

  /** The below-md card list — one of the two surfaces each row renders on. */
  function mobileRow(email: string): HTMLElement {
    const list = document.querySelector('[class~="md:hidden"]');
    expect(list).not.toBeNull();
    return within(list as HTMLElement)
      .getAllByText(email)
      .map((el) => el.closest('[class*="rounded"], li, div'))
      .find((row) => row !== null) as HTMLElement;
  }

  it("caps the worker invite form (name 120, email 254, password 128)", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add worker" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByLabelText(/name/i)).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/email/i)).toHaveAttribute("maxlength", "254");
    expect(within(dialog).getByLabelText(/^Password \(min 12 chars\) \*$/i)).toHaveAttribute("maxlength", "128");
  });

  it("caps the reset-password form at 128", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(mobileRow("ravi@example.com")).getByRole("button", { name: "Reset password" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/^New password/i)).toHaveAttribute("maxlength", "128");
  });

  it("caps the notification phone at 20", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(mobileRow("ravi@example.com")).getByRole("button", { name: "Notifications" }),
    );
    const dialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(dialog).getByLabelText(/phone/i)).toHaveAttribute("maxlength", "20"),
    );
  });

  it("caps the role form (name 80, description 255)", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "New role" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "New role" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/name/i)).toHaveAttribute("maxlength", "80");
    expect(within(dialog).getByLabelText(/description/i)).toHaveAttribute("maxlength", "255");
  });
});
