/**
 * Feed inventory — below-md mobile card list (md:hidden) for the stock
 * table, carrying the low-stock flag and a ≥44px Add-stock action,
 * alongside the untouched desktop table (hidden md:block, min-w-[640px]).
 * Mirrors the tasks board's worker-UX pattern.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import InventoryPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding/inventory",
  useSearchParams: () => new URLSearchParams(""),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI relies on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

const ITEM_MAIZE = {
  id: 3,
  ingredient: "Crushed maize",
  category: "ENERGY",
  unit: "kg",
  qty_on_hand: 45,
  reorder_level: 50,
  last_purchase_price_per_kg: 28.5,
};

const ITEM_GREEN = {
  id: 7,
  ingredient: "Super Napier green fodder",
  category: "GREEN",
  unit: "kg",
  qty_on_hand: 500,
  reorder_level: null,
  last_purchase_price_per_kg: null,
};

/** The below-md card list inside the Stock-on-hand section card. */
function mobileCardList(): HTMLElement {
  const section = screen.getByText("Stock on hand").closest("[data-slot='card']") as HTMLElement;
  const list = section.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

function desktopTable(): HTMLElement {
  const table = document.querySelector('[class~="md:block"] table[class*="min-w-[640px]"]');
  expect(table).not.toBeNull();
  return table as HTMLElement;
}

describe("InventoryPage mobile card list", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/feeding/inventory", () => HttpResponse.json([ITEM_MAIZE, ITEM_GREEN])),
      http.get("/api/feeding/finished-stock", () => HttpResponse.json([])),
    );
  });

  it("renders a below-md card per ingredient with the low-stock flag", async () => {
    renderWithProviders(<InventoryPage />);
    await screen.findAllByText("Crushed maize");

    const cards = mobileCardList();
    expect(within(cards).getByText("Crushed maize")).toBeInTheDocument();
    expect(within(cards).getByText("Super Napier green fodder")).toBeInTheDocument();
    // The low item carries its warning on the phone surface too.
    const lowCard = within(cards).getByText("Crushed maize").closest("div")!;
    expect(lowCard).toHaveTextContent("45.0 kg");
    expect(within(cards).getByText(/Reorder at 50 kg · Last price ₹28\.50\/kg/)).toBeInTheDocument();
    expect(within(cards).getByText(/Reorder at — kg/)).toBeInTheDocument();

    expect(desktopTable()).toHaveClass("min-w-[640px]");
  });

  it("gives the card Add-stock action a ≥44px touch target while the table stays compact", async () => {
    renderWithProviders(<InventoryPage />);
    await screen.findAllByText("Crushed maize");

    const cardButton = within(mobileCardList()).getAllByRole("button", { name: "Add stock" })[0]!;
    expect(cardButton).toHaveClass("h-11");
    const tableButton = within(desktopTable()).getAllByRole("button", { name: "Add stock" })[0]!;
    expect(tableButton).toHaveClass("h-9");
    expect(tableButton).not.toHaveClass("h-11");
  });

  it("opens the add-stock dialog from the card action", async () => {
    const user = userEvent.setup();
    renderWithProviders(<InventoryPage />);
    await screen.findAllByText("Crushed maize");

    await user.click(
      within(mobileCardList()).getAllByRole("button", { name: "Add stock" })[0]!,
    );
    expect(
      await screen.findByRole("dialog", { name: "Add stock — Crushed maize" }),
    ).toBeInTheDocument();
  });
});
