/**
 * Finance ledger — below-md mobile card lists (md:hidden) for the monthly
 * P&L and the transactions ledger, alongside the untouched desktop tables
 * (hidden md:block with min-w-[560px] / min-w-[900px] floors). Amounts stay
 * right-aligned tabular-nums; Correct keeps a ≥44px touch target. Mirrors
 * the tasks board's worker-UX pattern.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FinancePage from "./page";

const { navState } = vi.hoisted(() => ({ navState: { search: "" } }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture/scroll APIs Base UI relies on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

const TXN_SALE = {
  id: 1,
  date: "2026-01-05",
  type: "INCOME",
  category: "ANIMAL_SALE",
  amount: 150000,
  notes: "sold 10 bucks",
  related_animal_id: 11,
  animal_tag: "G-011",
  created_at: "2026-01-05T05:30:00Z",
  source_type: null,
  source_id: null,
  correction_of_id: null,
  voided_at: null,
  voided_by_id: null,
  void_reason: null,
};

const TXN_FEED = {
  ...TXN_SALE,
  id: 2,
  date: "2026-01-07",
  type: "EXPENSE",
  category: "FEED",
  amount: 90000,
  notes: null,
  related_animal_id: null,
  animal_tag: null,
};

const PAYLOAD = {
  transactions: [TXN_SALE, TXN_FEED],
  transactions_total: 2,
  limit: 50,
  offset: 0,
  total_income: 150000,
  total_expense: 90000,
  feed_stock_value: 12000,
  pnl: [
    { month: "2026-01", income: 150000, expense: 90000, net: 60000, categories: {} },
    { month: "2025-12", income: 10000, expense: 15000, net: -5000, categories: {} },
  ],
};

/** The section card by its visible title. */
function sectionCard(title: string): HTMLElement {
  return screen.getByText(title).closest("[data-slot='card']") as HTMLElement;
}

/** The below-md card list inside a section card. */
function mobileCards(sectionCardEl: HTMLElement): HTMLElement {
  const list = sectionCardEl.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

/** The desktop table inside a section card. */
function desktopTable(sectionCardEl: HTMLElement): HTMLElement {
  const table = sectionCardEl.querySelector('[class~="md:block"] table');
  expect(table).not.toBeNull();
  return table as HTMLElement;
}

describe("FinancePage mobile card lists", () => {
  beforeEach(() => {
    navState.search = "";
    server.use(http.get("/api/finance", () => HttpResponse.json(PAYLOAD)));
  });

  it("renders the P&L as a card per month and keeps the desktop table, both class-gated", async () => {
    renderWithProviders(<FinancePage />);
    await screen.findByText("Monthly P&L (last 12 months)");

    const section = sectionCard("Monthly P&L (last 12 months)");
    const cards = mobileCards(section);
    const janCard = within(cards).getByRole("button", { name: "2026-01" }).closest("div")!;
    expect(within(janCard.parentElement as HTMLElement).getAllByRole("button")).toHaveLength(2);
    expect(within(cards).getAllByText(/₹60,000/)).not.toHaveLength(0);
    // Sign tinting survives on the phone surface.
    expect(within(cards).getByText("-₹5,000")).toHaveClass("text-destructive");
    expect(within(cards).getByText("₹60,000")).toHaveClass("text-success");
    expect(desktopTable(section)).toHaveClass("min-w-[560px]");
  });

  it("filters the ledger when a P&L month card is tapped", async () => {
    const requests: string[] = [];
    server.use(
      http.get("/api/finance", ({ request }) => {
        requests.push(new URL(request.url).search);
        return HttpResponse.json(PAYLOAD);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Monthly P&L (last 12 months)");

    await user.click(
      within(mobileCards(sectionCard("Monthly P&L (last 12 months)"))).getByRole("button", {
        name: "2026-01",
      }),
    );
    expect(requests.at(-1)).toContain("month=2026-01");
  });

  it("renders the ledger as a card per transaction with amounts, badges and source", async () => {
    renderWithProviders(<FinancePage />);
    await screen.findByText("Transactions");

    const section = sectionCard("Transactions");
    const cards = mobileCards(section);
    expect(within(cards).getByText("sold 10 bucks")).toBeInTheDocument();
    expect(within(cards).getByText("Income")).toBeInTheDocument();
    expect(within(cards).getByText("Expense")).toBeInTheDocument();
    expect(within(cards).getByRole("link", { name: "G-011" })).toHaveAttribute(
      "href",
      "/animals/11",
    );
    expect(within(cards).getAllByText("Manual entry")).toHaveLength(2);
    expect(desktopTable(section)).toHaveClass("min-w-[900px]");
  });

  it("gives the card Correct action a ≥44px touch target while the table stays compact", async () => {
    renderWithProviders(<FinancePage />);
    await screen.findByText("Transactions");

    const section = sectionCard("Transactions");
    const cardCorrect = within(mobileCards(section)).getAllByRole("button", {
      name: "Correct",
    })[0]!;
    expect(cardCorrect).toHaveClass("h-11");
    const tableCorrect = within(desktopTable(section)).getAllByRole("button", {
      name: "Correct",
    })[0]!;
    expect(tableCorrect).toHaveClass("h-9");
    expect(tableCorrect).not.toHaveClass("h-11");
  });
});
