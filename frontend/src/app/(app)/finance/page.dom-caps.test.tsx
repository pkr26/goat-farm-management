/**
 * Finance ledger DOM caps (2026-10-01 campaign follow-up): the correction
 * dialog's notes and reason and the new-transaction notes are all bounded
 * at 255 characters in the DOM.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FinancePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  Element.prototype.scrollIntoView = () => {};
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

const TXN = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  date: "2026-01-05",
  type: "INCOME",
  category: "ANIMAL_SALE",
  amount: 150000,
  notes: "sold 10 bucks",
  related_animal_id: null,
  animal_tag: null,
  created_at: "2026-01-05T05:30:00Z",
  source_type: null,
  source_id: null,
  correction_of_id: null,
  voided_at: null,
  voided_by_id: null,
  void_reason: null,
  ...overrides,
});

const PAYLOAD = (transactions: Record<string, unknown>[] = [TXN()]) => ({
  transactions,
  transactions_total: transactions.length,
  limit: 50,
  offset: 0,
  total_income: 150000,
  total_expense: 2000,
  feed_stock_value: 12000,
  pnl: [{ month: "2026-01", income: 150000, expense: 2000, net: 148000, categories: {} }],
});

function ledgerScope() {
  const section = screen.getByText("Transactions").closest("[data-slot='card']") as HTMLElement;
  const table = section.querySelector('[class~="md:block"] table');
  expect(table).not.toBeNull();
  return table as HTMLElement;
}

describe("FinancePage DOM caps", () => {
  beforeEach(() => {
    server.use(http.get("/api/finance", () => HttpResponse.json(PAYLOAD())));
  });

  it("caps the correction dialog's notes and reason at 255", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income (all time)");

    await user.click(within(ledgerScope()).getByRole("button", { name: "Correct" }));
    const dialog = await screen.findByRole("dialog", { name: "Correct transaction #1" });
    expect(within(dialog).getByLabelText(/notes/i)).toHaveAttribute("maxlength", "255");
    expect(within(dialog).getByLabelText(/reason/i)).toHaveAttribute("maxlength", "255");
  });

  it("caps the new-transaction notes at 255", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income (all time)");

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog", { name: "New transaction" });
    expect(within(dialog).getByLabelText(/notes/i)).toHaveAttribute("maxlength", "255");
  });
});
