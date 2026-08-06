/**
 * Finance page: stat cards (totals + net), monthly P&L table, transactions
 * table, month/type/category filters wired to query params, new-transaction
 * dialog (date/amount validation, payload mapping, server errors) and RBAC
 * gating.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

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
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
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
};

const PAYLOAD = {
  transactions: [TXN_INCOME, TXN_EXPENSE],
  total_income: 150000,
  total_expense: 90000,
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
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
};

function financeHandler(payload: Record<string, unknown>) {
  return http.get("/api/finance", () => HttpResponse.json(payload));
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

    // StatCard renders value directly above its label.
    const incomeLabel = screen.getByText("Total income");
    expect(incomeLabel.previousElementSibling).toHaveTextContent("₹1,50,000");
    const expenseLabel = screen.getByText("Total expense");
    expect(expenseLabel.previousElementSibling).toHaveTextContent("₹90,000");
    const netLabel = screen.getByText("Net (all time)");
    expect(netLabel.previousElementSibling).toHaveTextContent("₹60,000");
  });

  it("renders the P&L table and marks negative months destructive", async () => {
    await renderLoaded();

    expect(screen.getByText("Monthly P&L (last 12 months)")).toBeInTheDocument();
    const lossCell = screen.getByText("-₹5,000");
    expect(lossCell).toHaveClass("text-destructive");
    const profitRow = screen.getByText("2026-01").closest("tr") as HTMLElement;
    expect(within(profitRow).getByText("₹60,000")).not.toHaveClass("text-destructive");
  });

  it("shows the empty P&L message when there are no transactions", async () => {
    server.use(
      financeHandler({ transactions: [], total_income: 0, total_expense: 0, pnl: [] }),
    );
    renderWithProviders(<FinancePage />);

    expect(await screen.findByText("No transactions yet.")).toBeInTheDocument();
    expect(screen.getByText("No transactions match.")).toBeInTheDocument();
  });

  it("renders transactions with type badges and an animal link", async () => {
    await renderLoaded();

    const row = screen.getByText("sold 10 bucks").closest("tr") as HTMLElement;
    expect(within(row).getByText("5 Jan 2026")).toBeInTheDocument();
    expect(within(row).getByText("INCOME")).toBeInTheDocument();
    expect(within(row).getByText("ANIMAL_SALE")).toBeInTheDocument();
    expect(within(row).getByText("₹1,50,000")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "G-011" })).toHaveAttribute(
      "href",
      "/animals/11",
    );

    const feedRow = screen.getByText("7 Jan 2026").closest("tr") as HTMLElement;
    expect(within(feedRow).getByText("EXPENSE")).toBeInTheDocument();
    expect(within(feedRow).getByText("—")).toBeInTheDocument(); // no animal
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
    await user.click(await screen.findByRole("option", { name: "INCOME" }));

    await waitFor(() => expect(lastParams.get("type")).toBe("INCOME"));
  });

  it("sends the category param when a category filter is picked", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    const [, categorySelect] = screen.getAllByRole("combobox");
    await user.click(categorySelect);
    await user.click(await screen.findByRole("option", { name: "FEED" }));

    await waitFor(() => expect(lastParams.get("category")).toBe("FEED"));
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

  it("hides New transaction for a finance.view-only user", async () => {
    server.use(permissionsHandler(["finance.view"]), financeHandler(PAYLOAD));
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Total income")).toBeInTheDocument();
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
    await user.click(await screen.findByRole("option", { name: "INCOME" }));
    await user.click(categorySelect);
    await user.click(await screen.findByRole("option", { name: "MILK" }));

    await user.type(within(dialog).getByLabelText(/Amount/), "1200");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ type: "INCOME", category: "MILK", amount: 1200 });
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
});
