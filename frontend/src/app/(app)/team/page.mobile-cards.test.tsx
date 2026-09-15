/**
 * Team page — below-md mobile card list (md:hidden) for the workers table,
 * carrying the same role/status/reset controls at ≥44px touch targets,
 * alongside the untouched desktop table (hidden md:block, min-w-[760px]).
 * Mirrors the tasks board's worker-UX pattern.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import TeamPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/team",
  useSearchParams: () => new URLSearchParams(""),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI relies on.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

const MEMBER_RAVI = {
  id: 2,
  user_id: 2,
  email: "ravi@example.com",
  name: "Ravi Kumar",
  role_id: 2,
  role_name: "Vet",
  is_active: true,
  can_reset_password: true,
  reset_password_block_reason: null,
};

const TEAM_PAYLOAD = {
  memberships: [MEMBER_RAVI],
  roles: [
    { id: 1, code: "MANAGER", name: "Manager", description: null, permissions: [], revision: 1, member_count: 0 },
    { id: 2, code: "VET", name: "Vet", description: null, permissions: [], revision: 1, member_count: 1 },
  ],
  permission_groups: [],
  permission_labels: {},
};

/** The below-md card list container. */
function mobileCardList(): HTMLElement {
  const list = document.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

function desktopTable(): HTMLElement {
  const table = document.querySelector('[class~="md:block"] table');
  expect(table).not.toBeNull();
  return table as HTMLElement;
}

describe("TeamPage workers mobile card list", () => {
  beforeEach(() => {
    // The default MSW permissions handler answers is_owner: true (owner holds
    // all permissions), so Reset password renders for a non-self member.
    server.use(http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)));
  });

  it("renders a below-md card per worker and keeps the desktop table, both class-gated", async () => {
    renderWithProviders(<TeamPage />);
    await screen.findAllByText("ravi@example.com");

    const cards = mobileCardList();
    expect(within(cards).getByText("Ravi Kumar")).toBeInTheDocument();
    expect(within(cards).getByText("ravi@example.com")).toBeInTheDocument();
    expect(within(cards).getByText("Active")).toBeInTheDocument();
    // The role Select survives on the card with its accessible name.
    expect(
      within(cards).getByRole("combobox", { name: "Role for Ravi Kumar" }),
    ).toBeInTheDocument();

    expect(desktopTable()).toHaveClass("min-w-[760px]");
  });

  it("gives card actions ≥44px touch targets while table actions stay compact", async () => {
    renderWithProviders(<TeamPage />);
    await screen.findAllByText("ravi@example.com");

    const cardDeactivate = within(mobileCardList()).getByRole("button", { name: "Deactivate" });
    expect(cardDeactivate).toHaveClass("h-11");
    const cardReset = within(mobileCardList()).getByRole("button", { name: "Reset password" });
    expect(cardReset).toHaveClass("h-11");

    const tableDeactivate = within(desktopTable()).getByRole("button", { name: "Deactivate" });
    expect(tableDeactivate).toHaveClass("h-9");
    expect(tableDeactivate).not.toHaveClass("h-11");
  });

  it("asks for deactivation confirmation from the card action too", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TeamPage />);
    await screen.findAllByText("ravi@example.com");

    await user.click(within(mobileCardList()).getByRole("button", { name: "Deactivate" }));
    expect(await screen.findByText("Deactivate Ravi Kumar?")).toBeInTheDocument();
  });

  it("keeps the reset block-reason id unique across the card and the row", async () => {
    server.use(
      http.get("/api/team", () =>
        HttpResponse.json({
          ...TEAM_PAYLOAD,
          memberships: [
            {
              ...MEMBER_RAVI,
              can_reset_password: false,
              reset_password_block_reason: "Reactivate first.",
            },
          ],
        }),
      ),
    );
    renderWithProviders(<TeamPage />);
    await screen.findAllByText("ravi@example.com");

    // Both surfaces render the reason, but the describedby target stays a
    // single element per surface.
    expect(document.querySelectorAll('[id$="reset-password-reason-2"]')).toHaveLength(2);
    const cardReset = within(mobileCardList()).getByRole("button", { name: "Reset password" });
    const cardTarget = document.getElementById(
      cardReset.getAttribute("aria-describedby") ?? "",
    );
    expect(cardTarget).not.toBeNull();
    expect(mobileCardList().contains(cardTarget)).toBe(true);
    const rowReset = within(desktopTable()).getByRole("button", { name: "Reset password" });
    const rowTarget = document.getElementById(rowReset.getAttribute("aria-describedby") ?? "");
    expect(rowTarget).not.toBeNull();
    expect(desktopTable().contains(rowTarget)).toBe(true);
  });
});
