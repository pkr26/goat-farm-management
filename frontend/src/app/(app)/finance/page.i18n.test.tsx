/**
 * Finance page i18n: the ledger chrome and the new-transaction dialog render
 * in Telugu when the language is te, and the zod validation messages resolve
 * through the finance.* catalog keys (mirrors health/page.i18n.test.tsx).
 * Also pins the catalog money floor byte-identical to the shared
 * MIN_PERSISTED_MONEY_MESSAGE constant.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { MIN_PERSISTED_MONEY_MESSAGE } from "@/lib/persisted-numbers";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FinancePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI Select relies on.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = vi.fn();
});

const TXN = {
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

beforeEach(() => {
  server.use(
    http.get("/api/finance", () =>
      HttpResponse.json({
        transactions: [TXN],
        transactions_total: 1,
        limit: 50,
        offset: 0,
        total_income: 150000,
        total_expense: 0,
        feed_stock_value: 0,
        pnl: [{ month: "2026-01", income: 150000, expense: 0, net: 150000, categories: {} }],
      }),
    ),
    http.get("/api/animals", () => HttpResponse.json({ animals: [], total: 0 })),
  );
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

async function openAddDialogInTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  renderWithProviders(
    <LanguageProvider>
      <FinancePage />
    </LanguageProvider>,
  );
  // Page chrome renders in Telugu once the ledger loads.
  await screen.findByText("మొత్తం ఆదాయం (మొత్తం కాలం)");
  await user.click(screen.getByRole("button", { name: "కొత్త లావాదేవీ" }));
  const dialog = await screen.findByRole("dialog", { name: "కొత్త లావాదేవీ" });
  return { user, dialog };
}

describe("FinancePage new-transaction dialog — Telugu", () => {
  it("renders the ledger type chips in Telugu (2026-10-01 audit, 06-2)", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    renderWithProviders(
      <LanguageProvider>
        <FinancePage />
      </LanguageProvider>,
    );
    await screen.findByText("మొత్తం ఆదాయం (మొత్తం కాలం)");

    // Both renderings of the INCOME transaction (below-md card and desktop
    // row) resolve the chip through the txType family — the badge's own
    // humanize fallback is English-only ("Income").
    const chips = await screen.findAllByText("ఆదాయం");
    expect(chips.length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("Income")).not.toBeInTheDocument();
    expect(screen.queryByText("Expense")).not.toBeInTheDocument();
  });

  it("renders the dialog fields in Telugu and shows zod validation in Telugu", async () => {
    const { user, dialog } = await openAddDialogInTelugu();

    expect(within(dialog).getByLabelText("మొత్తం (₹) *")).toBeInTheDocument();
    expect(within(dialog).getByText("గమనికలు")).toBeInTheDocument();
    expect(within(dialog).queryByText("Amount (₹) *")).not.toBeInTheDocument();

    // Amount is blank: z.coerce maps it to 0, which the positive check rejects.
    await user.click(within(dialog).getByRole("button", { name: "లావాదేవీ చేర్చు" }));
    const error = await within(dialog).findByText("మొత్తం 0 కంటే ఎక్కువగా ఉండాలి", {
      selector: "p[role='alert']",
    });
    expect(error).toHaveAttribute("id", "transaction-amount-error");
  });

  it("keeps the Telugu catalog at parity for the ledger keys", () => {
    expect(translate("te", "finance.title")).toBe("ఫైనాన్స్");
    expect(translate("te", "finance.correction.title", { number: 7 })).toBe(
      "లావాదేవీ #7 సరిదిద్దండి",
    );
    expect(
      translate("te", "finance.validation.amountMax", { max: "₹1,00,00,00,000" }),
    ).toBe("మొత్తం ₹1,00,00,00,000 మించకూడదు");
  });

  it("keeps the catalog money floor identical to the shared persisted-money constant", () => {
    expect(translate("en", "finance.validation.moneyMin")).toBe(MIN_PERSISTED_MONEY_MESSAGE);
  });
});
