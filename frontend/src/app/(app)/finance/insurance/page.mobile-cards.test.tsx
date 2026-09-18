/**
 * Insurance register — below-md mobile card list (md:hidden) with ≥44px
 * Renew/Claim touch targets alongside the untouched desktop table
 * (hidden md:block, min-w-[840px] floor). Mirrors the tasks board's
 * worker-UX pattern.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { InsurancePolicyOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import InsurancePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance/insurance",
  useSearchParams: () => new URLSearchParams(""),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture/scroll APIs Base UI relies on.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

function makePolicy(overrides: Partial<InsurancePolicyOut> = {}): InsurancePolicyOut {
  return {
    id: 1,
    policy_number: "POL-2026-001",
    insurer: "Oriental Insurance",
    animal_id: 11,
    animal_tag: "G-011",
    sum_insured: 12000,
    premium: 480,
    start_date: "2026-01-10",
    renewal_date: addDays(farmToday(), 20),
    status: "active",
    notes: null,
    created_at: "2026-01-10T05:30:00Z",
    claim_date: null,
    claimed_at: null,
    claimed_by_id: null,
    ...overrides,
  };
}

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

describe("InsurancePage mobile card list", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/finance/insurance", () =>
        HttpResponse.json({
          policies: [makePolicy()],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
    );
  });

  it("renders a below-md card per policy and keeps the desktop table, both class-gated", async () => {
    renderWithProviders(<InsurancePage />);
    await screen.findAllByText("POL-2026-001");

    const cards = mobileCardList();
    expect(within(cards).getByText("POL-2026-001")).toBeInTheDocument();
    expect(within(cards).getByText(/Oriental Insurance/)).toBeInTheDocument();
    expect(within(cards).getByRole("link", { name: "G-011" })).toHaveAttribute(
      "href",
      "/animals/11",
    );
    expect(within(cards).getByText("₹12,000")).toBeInTheDocument();
    expect(within(cards).getByText("₹480")).toBeInTheDocument();
    expect(within(cards).getByText("Active")).toBeInTheDocument();
    // The audit trail is a native, keyboard-focusable disclosure in both
    // responsive renderings; it does not turn the compact card into a link.
    expect(within(cards).getByText("Premium & claim history").tagName).toBe("SUMMARY");

    expect(desktopTable()).toHaveClass("min-w-[840px]");
  });

  it("gives card Renew/Claim ≥44px touch targets while table actions stay compact", async () => {
    renderWithProviders(<InsurancePage />);
    await screen.findAllByText("POL-2026-001");

    for (const name of ["Renew", "Claim"]) {
      expect(within(mobileCardList()).getByRole("button", { name })).toHaveClass("h-11");
      expect(within(desktopTable()).getByRole("button", { name })).toHaveClass("h-9");
    }
  });

  it("opens the renew dialog from the card action", async () => {
    const user = userEvent.setup();
    renderWithProviders(<InsurancePage />);
    await screen.findAllByText("POL-2026-001");

    await user.click(within(mobileCardList()).getByRole("button", { name: "Renew" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
