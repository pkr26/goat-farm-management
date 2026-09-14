/**
 * Finance page: stat cards (totals + net), monthly P&L table, transactions
 * table, month/type/category filters wired to query params, new-transaction
 * dialog (date/amount validation, payload mapping, server errors) and RBAC
 * gating.
 */

import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { farmToday } from "@/lib/format";

import FinancePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture/scroll APIs Radix Select relies on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

function localToday(): string {
  return farmToday();
}

const TXN_INCOME = {
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

const TXN_EXPENSE = {
  id: 2,
  date: "2026-01-07",
  type: "EXPENSE",
  category: "FEED",
  amount: 90000,
  notes: null,
  related_animal_id: null,
  animal_tag: null,
  created_at: "2026-01-07T05:30:00Z",
  source_type: null,
  source_id: null,
  correction_of_id: null,
  voided_at: null,
  voided_by_id: null,
  void_reason: null,
};

const TXN_FEED_PURCHASE = {
  ...TXN_EXPENSE,
  id: 5,
  notes: "restock maize",
  source_type: "FEED_PURCHASE",
  source_id: 5,
  amount: 2000,
};

const PAYLOAD = {
  transactions: [TXN_INCOME, TXN_EXPENSE],
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

const ANIMAL = {
  id: 11,
  tag_number: "G-011",
  name: "Raja",
  breed: "Osmanabadi",
  sex: "M",
  date_of_birth: null,
  estimated_dob: null,
  birth_type: null,
  source: "BORN",
  dam_id: null,
  sire_id: null,
  birth_weight: null,
  current_bucket: "BREEDING",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  movement_restricted: false,
  restriction_reason: null,
  suspected_scheduled_disease: false,
  suspected_disease: null,
  authority_notified_at: null,
  restriction_cleared_at: null,
  restriction_cleared_by_id: null,
  restriction_clearance_reference: null,
  mortality_cause: null,
  mortality_reported_at: null,
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
};

function financeHandler(payload: Record<string, unknown>) {
  return http.get("/api/finance", () =>
    HttpResponse.json({ transactions_total: 0, limit: 50, offset: 0, ...payload }),
  );
}

async function renderLoaded() {
  renderWithProviders(<FinancePage />);
  expect(await screen.findByText("Total income")).toBeInTheDocument();
}

describe("FinancePage totals and P&L", () => {
  beforeEach(() => {
    server.use(financeHandler(PAYLOAD));
  });

  it("renders income, expense and net stat cards with ₹ formatting", async () => {
    await renderLoaded();

    // StatCard renders value directly below its label.
    const incomeLabel = screen.getByText("Total income");
    expect(incomeLabel.nextElementSibling).toHaveTextContent("₹1,50,000");
    const expenseLabel = screen.getByText("Total expense");
    expect(expenseLabel.nextElementSibling).toHaveTextContent("₹90,000");
    const netLabel = screen.getByText("Net (all time)");
    expect(netLabel.nextElementSibling).toHaveTextContent("₹60,000");
  });

  it("renders the P&L table and marks negative months destructive", async () => {
    await renderLoaded();

    expect(screen.getByText("Monthly P&L (last 12 months)")).toBeInTheDocument();
    const lossCell = screen.getByText("-₹5,000");
    expect(lossCell).toHaveClass("text-destructive");
    const profitRow = screen.getByText("2026-01").closest("tr") as HTMLElement;
    expect(within(profitRow).getByText("₹60,000")).not.toHaveClass("text-destructive");
  });

  it("carries the feed-stock memo beside the P&L, never as an expense", async () => {
    await renderLoaded();

    expect(
      screen.getByText("Feed stock on hand ₹12,000 (memo — not an expense)."),
    ).toBeInTheDocument();
    // The memo is the P&L card's description, not a ledger stat of its own.
    expect(screen.queryByLabelText(/feed stock/i)).not.toBeInTheDocument();
  });

  it("shows the empty P&L message when there are no transactions", async () => {
    server.use(
      financeHandler({ transactions: [], total_income: 0, total_expense: 0, pnl: [] }),
    );
    renderWithProviders(<FinancePage />);

    expect(await screen.findByText("No transactions yet.")).toBeInTheDocument();
    // An empty month range with no filters active is not a filter miss: the
    // ledger promises a first entry instead of a Clear nudge.
    expect(screen.getByText("No transactions yet")).toBeInTheDocument();
    expect(
      screen.getByText("Record income and expenses to build the farm ledger."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No transactions match.")).not.toBeInTheDocument();
  });

  it("renders transactions with type badges and an animal link", async () => {
    await renderLoaded();

    const row = screen.getByText("sold 10 bucks").closest("tr") as HTMLElement;
    expect(within(row).getByText("5 Jan 2026")).toBeInTheDocument();
    // StatusBadge maps INCOME→success and humanises the label.
    const incomeBadge = within(row).getByText("Income");
    expect(incomeBadge.closest("[data-slot=badge]")).toHaveAttribute(
      "data-variant",
      "success",
    );
    expect(within(row).getByText("Animal sale")).toBeInTheDocument();
    expect(within(row).getByText("₹1,50,000")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "G-011" })).toHaveAttribute(
      "href",
      "/animals/11",
    );

    const feedRow = screen.getByText("7 Jan 2026").closest("tr") as HTMLElement;
    const expenseBadge = within(feedRow).getByText("Expense");
    expect(expenseBadge.closest("[data-slot=badge]")).toHaveAttribute(
      "data-variant",
      "warning",
    );
    expect(within(feedRow).getByText("—")).toBeInTheDocument(); // no animal
  });

  it("marks voided rows and retains their audit reason without offering another correction", async () => {
    server.use(
      financeHandler({
        ...PAYLOAD,
        transactions: [
          {
            ...TXN_INCOME,
            voided_at: "2026-08-08T10:00:00Z",
            voided_by_id: 7,
            void_reason: "Wrong sale amount",
          },
        ],
        transactions_total: 1,
      }),
    );
    await renderLoaded();

    const row = screen.getByText("sold 10 bucks").closest("tr") as HTMLElement;
    expect(within(row).getByText("VOID")).toBeInTheDocument();
    expect(within(row).getByText("Void reason: Wrong sale amount")).toBeInTheDocument();
    expect(within(row).getByText("₹1,50,000")).toHaveClass("line-through");
    expect(within(row).queryByRole("button", { name: "Correct" })).not.toBeInTheDocument();
  });

  it("shows whether an entry is manual, system-generated, or a correction", async () => {
    server.use(
      financeHandler({
        ...PAYLOAD,
        transactions: [
          { ...TXN_EXPENSE, notes: "manual feed" },
          {
            ...TXN_INCOME,
            id: 7,
            notes: "automatic sale",
            source_type: "ANIMAL_SALE",
            source_id: 11,
          },
          {
            ...TXN_INCOME,
            id: 8,
            notes: "corrected automatic sale",
            source_type: "ANIMAL_SALE",
            source_id: 11,
            correction_of_id: 7,
          },
        ],
        transactions_total: 3,
      }),
    );
    await renderLoaded();

    expect(within(screen.getByText("manual feed").closest("tr")!).getByText("Manual entry"))
      .toBeInTheDocument();
    expect(within(screen.getByText("automatic sale").closest("tr")!).getByText("Source: Animal sale #11"))
      .toBeInTheDocument();
    const correctionRow = screen.getByText("corrected automatic sale").closest("tr")!;
    expect(within(correctionRow).getByText("Correction of transaction #7")).toBeInTheDocument();
    expect(within(correctionRow).getByText("Source: Animal sale #11")).toBeInTheDocument();
  });
});

describe("FinancePage filters", () => {
  let lastParams: URLSearchParams;

  beforeEach(() => {
    lastParams = new URLSearchParams();
    server.use(
      http.get("/api/finance", ({ request }) => {
        lastParams = new URL(request.url).searchParams;
        return HttpResponse.json(PAYLOAD);
      }),
    );
  });

  it("loads the ledger on mount", async () => {
    await renderLoaded();
    // P&L + transactions from the default (unfiltered) payload render.
    expect(screen.getByText("Monthly P&L (last 12 months)")).toBeInTheDocument();
    expect(screen.getByText("sold 10 bucks")).toBeInTheDocument();
  });

  it("sends the month param when the month input changes", async () => {
    await renderLoaded();

    fireEvent.change(screen.getByLabelText("Filter by month"), {
      target: { value: "2025-12" },
    });

    await waitFor(() => expect(lastParams.get("month")).toBe("2025-12"));
  });

  it("sets the month filter when a P&L month button is clicked", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "2025-12" }));

    await waitFor(() => expect(lastParams.get("month")).toBe("2025-12"));
    expect(screen.getByLabelText("Filter by month")).toHaveValue("2025-12");
  });

  it("sends the type param when a type filter is picked", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    const [typeSelect] = screen.getAllByRole("combobox");
    await user.click(typeSelect);
    await user.click(await screen.findByRole("option", { name: "Income" }));

    await waitFor(() => expect(lastParams.get("type")).toBe("INCOME"));
  });

  it("sends the category param when a category filter is picked", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    const [, categorySelect] = screen.getAllByRole("combobox");
    await user.click(categorySelect);
    await user.click(await screen.findByRole("option", { name: "Feed" }));

    await waitFor(() => expect(lastParams.get("category")).toBe("FEED"));
  });

  it("keeps the filter-miss copy when active filters exclude every row", async () => {
    server.use(financeHandler({ transactions: [], pnl: [] }));
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByLabelText("Filter transactions by type"));
    await user.click(await screen.findByRole("option", { name: "Income" }));

    expect(await screen.findByText("No transactions match.")).toBeInTheDocument();
    expect(screen.getByText("Try clearing the filters.")).toBeInTheDocument();
    // Only the unfiltered empty ledger promises a first entry.
    expect(screen.queryByText("No transactions yet")).not.toBeInTheDocument();
  });

  it("Clear resets every active filter", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    // No Clear button until a filter is active.
    expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by month"), {
      target: { value: "2025-12" },
    });
    await waitFor(() => expect(lastParams.get("month")).toBe("2025-12"));

    await user.click(screen.getByRole("button", { name: "Clear" }));
    // Filters reset in the UI; the Clear button disappears again.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Filter by month")).toHaveValue("");
  });

  it("uses the API total to page through the full ledger", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/finance", ({ request }) => {
        lastParams = new URL(request.url).searchParams;
        return HttpResponse.json({ ...PAYLOAD, transactions_total: 120 });
      }),
    );
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastParams.get("offset")).toBe("50"));
  });

  it("returns to the first ledger page whenever any filter changes or clears", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/finance", ({ request }) => {
        lastParams = new URL(request.url).searchParams;
        return HttpResponse.json({ ...PAYLOAD, transactions_total: 120 });
      }),
    );
    await renderLoaded();

    async function moveToSecondPage() {
      const next = screen.getByRole("button", { name: "Next" });
      await waitFor(() => expect(next).toBeEnabled());
      await user.click(next);
      await waitFor(() => expect(lastParams.get("offset")).toBe("50"));
    }

    await moveToSecondPage();
    fireEvent.change(screen.getByLabelText("Filter by month"), {
      target: { value: "2025-12" },
    });
    await waitFor(() => expect(lastParams.get("offset")).toBe("0"));

    await moveToSecondPage();
    await user.click(screen.getByLabelText("Filter transactions by type"));
    await user.click(await screen.findByRole("option", { name: "Income" }));
    await waitFor(() => expect(lastParams.get("offset")).toBe("0"));

    await moveToSecondPage();
    await user.click(screen.getByLabelText("Filter transactions by category"));
    await user.click(await screen.findByRole("option", { name: "Feed" }));
    await waitFor(() => expect(lastParams.get("offset")).toBe("0"));

    await moveToSecondPage();
    await user.click(screen.getByRole("button", { name: "2025-12" }));
    await waitFor(() => expect(lastParams.get("offset")).toBe("0"));

    await moveToSecondPage();
    await user.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(lastParams.get("offset")).toBe("0"));
    expect(lastParams.has("month")).toBe(false);
    expect(lastParams.has("type")).toBe(false);
    expect(lastParams.has("category")).toBe(false);
  });
});

describe("FinancePage RBAC and errors", () => {
  it("denies access without finance.view and never calls the endpoint", async () => {
    let calls = 0;
    server.use(
      permissionsHandler(["animals.view"]),
      http.get("/api/finance", () => {
        calls += 1;
        return HttpResponse.json(PAYLOAD);
      }),
    );
    renderWithProviders(<FinancePage />);

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    expect(calls).toBe(0);
  });

  it("shows a permission error instead of misreporting no access", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 }),
      ),
    );
    renderWithProviders(<FinancePage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
  });

  it("hides New transaction for a finance.view-only user", async () => {
    server.use(permissionsHandler(["finance.view"]), financeHandler(PAYLOAD));
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Total income")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New transaction" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Correct" })).not.toBeInTheDocument();
  });

  it("offers Add transaction from an empty ledger and hides it without finance.manage", async () => {
    server.use(financeHandler({ transactions: [], pnl: [] }));
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);

    // The CTA opens the same dialog as the header's New transaction button.
    await user.click(await screen.findByRole("button", { name: "Add transaction" }));
    expect(
      await screen.findByRole("dialog", { name: "New transaction" }),
    ).toBeInTheDocument();

    server.use(
      permissionsHandler(["finance.view"]),
      financeHandler({ transactions: [], pnl: [] }),
    );
    cleanup();
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("No transactions yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add transaction" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New transaction" })).not.toBeInTheDocument();
  });

  it("shows the error detail when the finance GET fails", async () => {
    server.use(
      http.get("/api/finance", () =>
        HttpResponse.json({ detail: "ledger unavailable" }, { status: 500 }),
      ),
    );
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("ledger unavailable")).toBeInTheDocument();
  });

  it("announces a finance query failure and retries it", async () => {
    let fail = true;
    let calls = 0;
    server.use(
      http.get("/api/finance", () => {
        calls += 1;
        return fail
          ? HttpResponse.json({ detail: "ledger temporarily unavailable" }, { status: 503 })
          : HttpResponse.json(PAYLOAD);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("ledger temporarily unavailable");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry finance" }));
    expect(await screen.findByText("Total income")).toBeInTheDocument();
    expect(calls).toBe(2);
  });
});

describe("FinancePage correction dialog", () => {
  let correctionBody: Record<string, unknown> | null;
  let correctionCalls: number;

  beforeEach(() => {
    correctionBody = null;
    correctionCalls = 0;
    server.use(
      financeHandler(PAYLOAD),
      http.get("/api/animals", () => HttpResponse.json({ animals: [ANIMAL], total: 1 })),
      http.post("/api/finance/transactions/1/correct", async ({ request }) => {
        correctionCalls += 1;
        correctionBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...TXN_INCOME, id: 3, correction_of_id: 1, amount: 145000 },
          { status: 201 },
        );
      }),
    );
  });

  async function openCorrection() {
    const user = userEvent.setup();
    await renderLoaded();
    const row = screen.getByText("sold 10 bucks").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Correct" }));
    return { user, dialog: await screen.findByRole("dialog", { name: "Correct transaction #1" }) };
  }

  async function confirmCorrection(
    user: ReturnType<typeof userEvent.setup>,
    dialog: HTMLElement,
  ) {
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: /I understand the original transaction will be voided and replaced/i,
      }),
    );
  }

  it("requires an explicit acknowledgement of the correction consequence", async () => {
    const { user, dialog } = await openCorrection();
    const submit = within(dialog).getByRole("button", { name: "Record correction" });
    const acknowledgement = within(dialog).getByRole("checkbox", {
      name: /I understand the original transaction will be voided and replaced/i,
    });

    expect(submit).toBeDisabled();
    expect(acknowledgement).toHaveAccessibleDescription(
      /original row will be marked void and retained/i,
    );
    await user.click(acknowledgement);
    expect(submit).toBeEnabled();
  });

  it("requires an audit reason before creating the replacement", async () => {
    const { user, dialog } = await openCorrection();
    await confirmCorrection(user, dialog);
    await user.clear(within(dialog).getByLabelText("Correction reason *"));
    await user.type(within(dialog).getByLabelText("Correction reason *"), "no");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    expect(await within(dialog).findByText("Reason must be at least 3 characters")).toBeInTheDocument();
    expect(correctionCalls).toBe(0);
  });

  it("rejects a non-zero correction below half a paisa", async () => {
    const { user, dialog } = await openCorrection();
    await confirmCorrection(user, dialog);
    const amount = within(dialog).getByLabelText("Amount (₹) *");
    await user.clear(amount);
    await user.type(amount, "0.004");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Fix amount");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    expect(await within(dialog).findByText("Amount must be ₹0 or at least ₹0.005"))
      .toBeInTheDocument();
    expect(correctionCalls).toBe(0);
  });

  it("does not coerce a blank correction amount into a zero-value replacement", async () => {
    const { user, dialog } = await openCorrection();
    await confirmCorrection(user, dialog);
    await user.clear(within(dialog).getByLabelText("Amount (₹) *"));
    await user.type(
      within(dialog).getByLabelText("Correction reason *"),
      "Correct receipt",
    );

    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    expect(await within(dialog).findByText("Amount is required")).toBeInTheDocument();
    expect(correctionCalls).toBe(0);
  });

  it("posts a full replacement while preserving the original as an audit row", async () => {
    const { user, dialog } = await openCorrection();
    await confirmCorrection(user, dialog);
    const amount = within(dialog).getByLabelText("Amount (₹) *");
    await user.clear(amount);
    await user.type(amount, "145000");
    const notes = within(dialog).getByLabelText("Notes");
    await user.clear(notes);
    await user.type(notes, "  corrected sale receipt  ");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Duplicate kid count");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    await waitFor(() => expect(correctionCalls).toBe(1));
    expect(correctionBody).toEqual({
      date: "2026-01-05",
      type: "INCOME",
      category: "ANIMAL_SALE",
      amount: 145000,
      notes: "corrected sale receipt",
      related_animal_id: 11,
      reason: "Duplicate kid count",
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("preserves an existing animal link read-only without animals.view", async () => {
    let animalCalls = 0;
    server.use(
      permissionsHandler(["finance.view", "finance.manage"]),
      http.get("/api/animals", () => {
        animalCalls += 1;
        return HttpResponse.json({ animals: [ANIMAL], total: 1 });
      }),
    );
    const { user, dialog } = await openCorrection();
    await confirmCorrection(user, dialog);

    expect(screen.queryByRole("link", { name: "G-011" })).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Linked animal")).toHaveTextContent("G-011");
    expect(
      within(dialog).getByText(/correction preserves the existing link/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByRole("combobox", { name: /Animal/ })).not.toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Correction reason *"), "Correct receipt");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));
    await waitFor(() => expect(correctionCalls).toBe(1));
    expect(correctionBody).toMatchObject({ related_animal_id: 11 });
    expect(animalCalls).toBe(0);
  });

  it("surfaces a server rejection and keeps the correction open", async () => {
    server.use(
      http.post("/api/finance/transactions/1/correct", () =>
        HttpResponse.json({ detail: "transaction is already voided" }, { status: 409 }),
      ),
    );
    const { user, dialog } = await openCorrection();
    await confirmCorrection(user, dialog);
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong amount");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    expect(await within(dialog).findByText("transaction is already voided")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("single-flights a double-click on Record correction", async () => {
    const { user, dialog } = await openCorrection();
    await confirmCorrection(user, dialog);
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Correct receipt");

    await user.dblClick(within(dialog).getByRole("button", { name: "Record correction" }));
    await waitFor(() => expect(correctionCalls).toBe(1));
  });

  it("does not let an older correction completion close a newer correction dialog", async () => {
    let financeCalls = 0;
    let releaseCorrection: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      releaseCorrection = resolve;
    });
    server.use(
      http.get("/api/finance", () => {
        financeCalls += 1;
        return HttpResponse.json(PAYLOAD);
      }),
      http.post("/api/finance/transactions/1/correct", async ({ request }) => {
        correctionCalls += 1;
        correctionBody = (await request.json()) as Record<string, unknown>;
        await parked;
        return HttpResponse.json(
          { ...TXN_INCOME, id: 3, correction_of_id: 1, amount: 145000 },
          { status: 201 },
        );
      }),
    );
    const { user, dialog } = await openCorrection();
    await confirmCorrection(user, dialog);
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Correct receipt");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));
    await waitFor(() => expect(correctionCalls).toBe(1));
    expect(dialog.querySelector("fieldset")).toBeDisabled();

    // Dismissal intentionally does not cancel the durable write.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    let expenseRow = screen.getByText("7 Jan 2026").closest("tr") as HTMLElement;
    let nextCorrection = within(expenseRow).getByRole("button", { name: "Correct" });
    expect(nextCorrection).toBeDisabled();
    await user.click(nextCorrection);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    releaseCorrection?.();
    await waitFor(() => expect(financeCalls).toBeGreaterThan(1));
    expenseRow = screen.getByText("7 Jan 2026").closest("tr") as HTMLElement;
    nextCorrection = within(expenseRow).getByRole("button", { name: "Correct" });
    await waitFor(() => expect(nextCorrection).toBeEnabled());
    await user.click(nextCorrection);
    // The completed transaction #1 continuation no longer has any live work
    // that can clear transaction #2.
    expect(
      await screen.findByRole("dialog", { name: "Correct transaction #2" }),
    ).toBeInTheDocument();
  });

  it("rejects a correction amount above the ₹1,00,00,00,000 cap", async () => {
    const { user, dialog } = await openCorrection();
    await confirmCorrection(user, dialog);
    const amount = within(dialog).getByLabelText("Amount (₹) *");
    await user.clear(amount);
    await user.type(amount, "1000000001");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Fix amount");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    expect(await within(dialog).findByText("Amount cannot exceed ₹1,00,00,00,000")).toBeInTheDocument();
    expect(correctionCalls).toBe(0);
  });

  it("does not show a corrected-quantity field for a non-FEED_PURCHASE transaction", async () => {
    const { dialog } = await openCorrection();
    expect(within(dialog).queryByLabelText("Corrected quantity (kg)")).not.toBeInTheDocument();
  });

  it("shows a corrected-quantity field for a FEED_PURCHASE row and omits it from the payload when left blank", async () => {
    let feedCorrectionBody: Record<string, unknown> | null = null;
    server.use(
      financeHandler({ ...PAYLOAD, transactions: [TXN_FEED_PURCHASE], transactions_total: 1 }),
      http.post("/api/finance/transactions/5/correct", async ({ request }) => {
        feedCorrectionBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...TXN_FEED_PURCHASE, id: 6, correction_of_id: 5 },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = screen.getByText("restock maize").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Correct" }));
    const dialog = await screen.findByRole("dialog", { name: "Correct transaction #5" });
    expect(within(dialog).getByLabelText("Corrected quantity (kg)")).toBeInTheDocument();

    await confirmCorrection(user, dialog);
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Attach invoice");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    await waitFor(() => expect(feedCorrectionBody).not.toBeNull());
    expect(feedCorrectionBody).not.toHaveProperty("feed_quantity_kg");
  });

  it("includes the corrected quantity in the payload when provided for a FEED_PURCHASE row", async () => {
    let feedCorrectionBody: Record<string, unknown> | null = null;
    server.use(
      financeHandler({ ...PAYLOAD, transactions: [TXN_FEED_PURCHASE], transactions_total: 1 }),
      http.post("/api/finance/transactions/5/correct", async ({ request }) => {
        feedCorrectionBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...TXN_FEED_PURCHASE, id: 6, correction_of_id: 5 },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = screen.getByText("restock maize").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Correct" }));
    const dialog = await screen.findByRole("dialog", { name: "Correct transaction #5" });

    await confirmCorrection(user, dialog);
    await user.type(within(dialog).getByLabelText("Corrected quantity (kg)"), "100");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Quantity was mistyped");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    await waitFor(() => expect(feedCorrectionBody).not.toBeNull());
    expect(feedCorrectionBody).toMatchObject({ feed_quantity_kg: 100 });
  });
});

describe("FinancePage new-transaction dialog", () => {
  let postCalls: number;
  let postBody: Record<string, unknown> | null;
  let getCalls: number;

  beforeEach(() => {
    postCalls = 0;
    postBody = null;
    getCalls = 0;
    server.use(
      http.get("/api/finance", () => {
        getCalls += 1;
        return HttpResponse.json(PAYLOAD);
      }),
      http.get("/api/animals", () => HttpResponse.json({ animals: [ANIMAL], total: 1 })),
      http.post("/api/finance/new", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...TXN_EXPENSE, id: 3 }, { status: 201 });
      }),
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("requires a date", async () => {
    const { user, dialog } = await openDialog();

    fireEvent.change(within(dialog).getByLabelText(/^Date/), { target: { value: "" } });
    await user.type(within(dialog).getByLabelText(/Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByText("Date is required")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a future date", async () => {
    const { user, dialog } = await openDialog();

    fireEvent.change(within(dialog).getByLabelText(/^Date/), {
      target: { value: "2099-01-01" },
    });
    await user.type(within(dialog).getByLabelText(/Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByText("Date can't be in the future")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a zero amount", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Amount/), "0");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByText("Amount must be greater than 0")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a negative amount", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Amount/), "-50");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByText("Amount must be greater than 0")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a non-zero transaction amount below half a paisa", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Amount/), "0.004");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByText("Amount must be at least ₹0.005"))
      .toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects an amount above the ₹1,00,00,00,000 cap", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Amount/), "1000000001");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByText("Amount cannot exceed ₹1,00,00,00,000"))
      .toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("POSTs EXPENSE/OTHER defaults with null notes and animal", async () => {
    const { user, dialog } = await openDialog();
    const callsBefore = getCalls;

    await user.type(within(dialog).getByLabelText(/Amount/), "250.50");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toEqual({
      date: localToday(),
      type: "EXPENSE",
      category: "OTHER",
      amount: 250.5,
      notes: null,
      related_animal_id: null,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(getCalls).toBeGreaterThan(callsBefore));
  });

  it("saves without an animal link and does not browse animals without animals.view", async () => {
    let animalCalls = 0;
    server.use(
      permissionsHandler(["finance.view", "finance.manage"]),
      http.get("/api/animals", () => {
        animalCalls += 1;
        return HttpResponse.json({ animals: [ANIMAL], total: 1 });
      }),
    );
    const { user, dialog } = await openDialog();

    expect(
      within(dialog).getByText(/transaction will be saved without an animal link/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByRole("combobox", { name: /Animal/ })).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/Amount/), "250");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ related_animal_id: null });
    expect(animalCalls).toBe(0);
  });

  it("POSTs trimmed notes and the selected animal id", async () => {
    const { user, dialog } = await openDialog();

    // Animal select (third combobox in the dialog: Type, Category, Animal).
    const animalSelect = within(dialog).getAllByRole("combobox")[2];
    await user.click(animalSelect);
    await user.click(await screen.findByRole("option", { name: "G-011 · Raja" }));
    // The closed trigger shows the animal label, not the raw id.
    expect(animalSelect).toHaveTextContent("G-011 · Raja");

    await user.type(within(dialog).getByLabelText(/Amount/), "5000");
    await user.type(within(dialog).getByLabelText(/Notes/), "  sale advance  ");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ notes: "sale advance", related_animal_id: 11 });
  });

  it("POSTs the chosen type and category", async () => {
    const { user, dialog } = await openDialog();

    const [typeSelect, categorySelect] = within(dialog).getAllByRole("combobox");
    await user.click(typeSelect);
    await user.click(await screen.findByRole("option", { name: "Income" }));
    await user.click(categorySelect);
    await user.click(await screen.findByRole("option", { name: "Animal sale" }));

    await user.type(within(dialog).getByLabelText(/Amount/), "1200");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ type: "INCOME", category: "ANIMAL_SALE", amount: 1200 });
  });

  it("shows the server detail inline on a 400", async () => {
    server.use(
      http.post("/api/finance/new", () => {
        postCalls += 1;
        return HttpResponse.json({ detail: "amount exceeds limit" }, { status: 400 });
      }),
    );
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByText("amount exceeds limit")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("shows the server detail inline on a 500", async () => {
    server.use(
      http.post("/api/finance/new", () => {
        postCalls += 1;
        return HttpResponse.json({ detail: "database locked" }, { status: 500 });
      }),
    );
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByText("database locked")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("announces a failed add and retries without clearing the form", async () => {
    let calls = 0;
    server.use(
      http.post("/api/finance/new", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "ledger write conflict" }, { status: 409 })
          : HttpResponse.json({ ...TXN_EXPENSE, id: 3 }, { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    const amount = within(dialog).getByLabelText(/Amount/);
    await user.type(amount, "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("ledger write conflict");
    expect(amount).toHaveValue(100);
    await user.click(within(dialog).getByRole("button", { name: "Retry add transaction" }));
    await waitFor(() => expect(calls).toBe(2));
  });

  it("single-flights a double-click on Add transaction", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Amount/), "100");

    await user.dblClick(within(dialog).getByRole("button", { name: "Add transaction" }));
    await waitFor(() => expect(postCalls).toBe(1));
  });

  it("does not let a dismissed add completion close or reset a reopened draft", async () => {
    let releaseAdd: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      releaseAdd = resolve;
    });
    server.use(
      http.post("/api/finance/new", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        await parked;
        return HttpResponse.json({ ...TXN_EXPENSE, id: 3 }, { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
    await waitFor(() => expect(postCalls).toBe(1));

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const reopened = await screen.findByRole("dialog");
    const newAmount = within(reopened).getByLabelText(/Amount/);
    expect(newAmount).toBeDisabled();

    releaseAdd?.();
    await waitFor(() => expect(newAmount).toBeEnabled());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.type(newAmount, "200");
    expect(newAmount).toHaveValue(200);
    expect(getCalls).toBeGreaterThan(1);
    expect(postBody).toMatchObject({ amount: 100 });
  });
});
