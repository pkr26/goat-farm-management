/**
 * Finance page — a second pass over the exact copy and wiring the ledger
 * promises its operators: income/expense tints, the blank and humanised
 * fallbacks in the transactions table, P&L sign styling, the correction
 * dialog's prefill/label/validation wiring and in-flight button copy, the
 * add dialog's defaults and full category coverage, and the states the page
 * shows while permissions, filters and writes settle.
 */

import { configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
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

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const toastMock = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

beforeAll(() => {
  // Dialog round-trips can outrun the 1 s findBy*/waitFor default when this
  // file shares a loaded machine with the rest of the gate.
  configure({ asyncUtilTimeout: 3000 });
  // jsdom lacks the pointer-capture/scroll APIs Base UI Select relies on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

type User = ReturnType<typeof userEvent.setup>;

function setInput(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

async function pickOption(user: User, trigger: HTMLElement, name: string) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

/** A response the test releases by hand, to observe in-flight copy. */
function createGate() {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { gate, release };
}

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

const TXN_FEED_PURCHASE = {
  ...TXN_FEED,
  id: 5,
  date: "2026-01-09",
  notes: "restock maize",
  amount: 2000,
  source_type: "FEED_PURCHASE",
  source_id: 5,
};

const PAYLOAD = {
  transactions: [TXN_SALE, TXN_FEED],
  transactions_total: 2,
  limit: 50,
  offset: 0,
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

function animalsHandler() {
  return http.get("/api/animals", () =>
    HttpResponse.json({ animals: [ANIMAL], total: 1 }),
  );
}

async function renderLoaded() {
  renderWithProviders(<FinancePage />);
  expect(await screen.findByText("Total income")).toBeInTheDocument();
}

function rowFor(text: string): HTMLElement {
  return screen.getByText(text).closest("tr") as HTMLElement;
}

describe("FinancePage ledger copy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server.use(financeHandler(PAYLOAD));
  });

  it("tints the type badge and the amount by income or expense", async () => {
    await renderLoaded();

    const saleRow = rowFor("sold 10 bucks");
    const incomeBadge = within(saleRow).getByText("Income");
    expect(incomeBadge.closest("[data-slot=badge]")).toHaveClass(
      "border-transparent",
      "bg-success-tint",
      "text-success-tint-foreground",
    );
    expect(within(saleRow).getByText("₹1,50,000")).toHaveClass(
      "text-right",
      "tabular-nums",
      "font-medium",
      "text-success",
    );

    const feedRow = rowFor("7 Jan 2026");
    const expenseBadge = within(feedRow).getByText("Expense");
    expect(expenseBadge.closest("[data-slot=badge]")).toHaveClass(
      "border-transparent",
      "bg-warning-tint",
      "text-warning-tint-foreground",
    );
    expect(within(feedRow).getByText("₹90,000")).toHaveClass(
      "text-right",
      "tabular-nums",
      "font-medium",
      "text-destructive",
    );
  });

  it("dims the whole row of a voided transaction", async () => {
    server.use(
      financeHandler({
        ...PAYLOAD,
        transactions: [
          { ...TXN_SALE, voided_at: "2026-08-08T10:00:00Z", void_reason: "Wrong amount" },
        ],
        transactions_total: 1,
      }),
    );
    await renderLoaded();

    expect(rowFor("sold 10 bucks")).toHaveClass("bg-muted/40", "opacity-70");
  });

  it("leaves the notes cell blank when a transaction carries no note", async () => {
    await renderLoaded();

    // Date, Type, Category, Amount, Animal, Notes, Source/audit, Actions.
    const notesCell = within(rowFor("7 Jan 2026")).getAllByRole("cell")[5];
    expect(notesCell).toBeEmptyDOMElement();
  });

  it("names every generated source and humanises an unmapped one", async () => {
    server.use(
      financeHandler({
        ...PAYLOAD,
        transactions: [
          { ...TXN_FEED, id: 21, notes: "bought doe", source_type: "ANIMAL_PURCHASE", source_id: 12 },
          { ...TXN_FEED, id: 22, notes: "vet visit", source_type: "HEALTH_EVENT", source_id: 4 },
          { ...TXN_FEED, id: 23, notes: "batch intake", source_type: "PURCHASE_BATCH", source_id: 9 },
          { ...TXN_FEED_PURCHASE, notes: "restock maize" },
        ],
        transactions_total: 4,
      }),
    );
    await renderLoaded();

    expect(within(rowFor("bought doe")).getByText("Source: Animal purchase #12")).toBeInTheDocument();
    expect(within(rowFor("vet visit")).getByText("Source: Health event #4")).toBeInTheDocument();
    expect(within(rowFor("batch intake")).getByText("Source: Purchase batch #9")).toBeInTheDocument();
    // No label is mapped for a feed purchase, so the raw source type is spaced out.
    expect(within(rowFor("restock maize")).getByText("Source: FEED PURCHASE #5")).toBeInTheDocument();
  });

  it("styles the monthly net column by sign", async () => {
    await renderLoaded();

    expect(within(rowFor("2026-01")).getByText("₹60,000")).toHaveClass(
      "text-right",
      "tabular-nums",
      "text-success",
    );
    expect(within(rowFor("2025-12")).getByText("-₹5,000")).toHaveClass(
      "text-right",
      "tabular-nums",
      "text-destructive",
    );
  });
});

describe("FinancePage correction dialog copy", () => {
  let correctionBody: Record<string, unknown> | null;
  let correctionCalls: number;

  beforeEach(() => {
    vi.clearAllMocks();
    correctionBody = null;
    correctionCalls = 0;
    server.use(
      financeHandler({
        ...PAYLOAD,
        transactions: [TXN_SALE, TXN_FEED, TXN_FEED_PURCHASE],
        transactions_total: 3,
      }),
      animalsHandler(),
      http.post("/api/finance/transactions/:transactionId/correct", async ({ request }) => {
        correctionCalls += 1;
        correctionBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...TXN_SALE, id: 99, correction_of_id: 1 }, { status: 201 });
      }),
    );
  });

  async function openCorrection(notesText: string, title: string) {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowFor(notesText)).getByRole("button", { name: "Correct" }));
    return { user, dialog: await screen.findByRole("dialog", { name: title }) };
  }

  async function acknowledge(user: User, dialog: HTMLElement) {
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: /I understand the original transaction will be voided and replaced/i,
      }),
    );
  }

  it("prefills and labels every field of the transaction under correction", async () => {
    const { dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");

    expect(within(dialog).getByLabelText("Date *")).toHaveValue("2026-01-05");
    expect(within(dialog).getByRole("combobox", { name: "Type" })).toHaveTextContent("Income");
    expect(within(dialog).getByRole("combobox", { name: "Category" })).toHaveTextContent(
      "Animal sale",
    );
    expect(within(dialog).getByLabelText("Amount (₹) *")).toHaveValue(150000);
    expect(within(dialog).getByLabelText("Notes")).toHaveValue("sold 10 bucks");
    expect(within(dialog).getByLabelText("Animal (optional)")).toHaveTextContent("G-011");
    expect(within(dialog).getByLabelText("Correction reason *")).toHaveValue("");
  });

  it("opens a blank note and the none sentinel for an unlinked transaction", async () => {
    const { dialog } = await openCorrection("7 Jan 2026", "Correct transaction #2");

    expect(within(dialog).getByLabelText("Notes")).toHaveValue("");
    expect(within(dialog).getByLabelText("Animal (optional)")).toHaveTextContent("— none —");
  });

  it("identifies a linked animal by id when the ledger row has no tag", async () => {
    server.use(
      financeHandler({
        ...PAYLOAD,
        transactions: [{ ...TXN_SALE, animal_tag: null }],
        transactions_total: 1,
      }),
    );
    const { dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");

    expect(within(dialog).getByLabelText("Animal (optional)")).toHaveTextContent("Animal #11");
  });

  it("reads out the preserved animal link without animals.view", async () => {
    server.use(
      permissionsHandler(["finance.view", "finance.manage"]),
      financeHandler({
        ...PAYLOAD,
        transactions: [{ ...TXN_SALE, animal_tag: null }, TXN_FEED],
        transactions_total: 2,
      }),
    );
    const { user, dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");

    expect(within(dialog).getByLabelText("Linked animal")).toHaveTextContent("Animal #11");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(within(rowFor("7 Jan 2026")).getByRole("button", { name: "Correct" }));
    const unlinked = await screen.findByRole("dialog", { name: "Correct transaction #2" });
    expect(within(unlinked).getByLabelText("Linked animal")).toHaveTextContent("No animal linked");
  });

  it("records a changed type, category and animal link", async () => {
    const { user, dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");
    await acknowledge(user, dialog);

    const typeSelect = within(dialog).getByRole("combobox", { name: "Type" });
    await user.click(typeSelect);
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual([
      "Income",
      "Expense",
    ]);
    await user.click(screen.getByRole("option", { name: "Expense" }));
    await waitFor(() => expect(typeSelect).toHaveTextContent("Expense"));

    const categorySelect = within(dialog).getByRole("combobox", { name: "Category" });
    await user.click(categorySelect);
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual([
      "Animal sale",
      "Animal purchase",
      "Feed",
      "Medicine",
      "Vet",
      "Labour",
      "Equipment",
      "Milk",
      "Manure",
      "Other",
    ]);
    await user.click(screen.getByRole("option", { name: "Vet" }));
    await waitFor(() => expect(categorySelect).toHaveTextContent("Vet"));

    const animalPicker = within(dialog).getByLabelText("Animal (optional)");
    await user.click(animalPicker);
    await user.click(await screen.findByRole("option", { name: "— none —" }));
    await waitFor(() => expect(animalPicker).toHaveTextContent("— none —"));

    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong ledger side");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    await waitFor(() => expect(correctionCalls).toBe(1));
    expect(correctionBody).toMatchObject({
      type: "EXPENSE",
      category: "VET",
      related_animal_id: null,
      reason: "Wrong ledger side",
    });
  });

  it("describes every invalid correction field to assistive tech", async () => {
    const { user, dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");
    await acknowledge(user, dialog);

    setInput(within(dialog).getByLabelText("Date *"), "");
    setInput(within(dialog).getByLabelText("Amount (₹) *"), "");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "no");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    await waitFor(() =>
      expect(within(dialog).getByLabelText("Date *")).toHaveAccessibleDescription(
        "Date is required",
      ),
    );
    expect(within(dialog).getByLabelText("Amount (₹) *")).toHaveAccessibleDescription(
      "Amount is required",
    );
    expect(within(dialog).getByLabelText("Correction reason *")).toHaveAccessibleDescription(
      "Reason must be at least 3 characters",
    );
    expect(correctionCalls).toBe(0);
  });

  it("rejects a negative correction amount", async () => {
    const { user, dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");
    await acknowledge(user, dialog);

    setInput(within(dialog).getByLabelText("Amount (₹) *"), "-5");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Fix amount");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    expect(await within(dialog).findByText("Amount can't be negative")).toBeInTheDocument();
    expect(correctionCalls).toBe(0);
  });

  it("bounds the corrected feed quantity at both ends", async () => {
    const { user, dialog } = await openCorrection("restock maize", "Correct transaction #5");
    await acknowledge(user, dialog);
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Recount sacks");

    const quantity = within(dialog).getByLabelText("Corrected quantity (kg)");
    setInput(quantity, "0");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));
    await waitFor(() =>
      expect(quantity).toHaveAccessibleDescription("Quantity must be greater than 0"),
    );

    setInput(quantity, "1000001");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));
    await waitFor(() =>
      expect(quantity).toHaveAccessibleDescription("Quantity cannot exceed 1,000,000 kg"),
    );
    expect(correctionCalls).toBe(0);
  });

  it("trims the audit reason and confirms the recorded correction", async () => {
    const { user, dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");
    await acknowledge(user, dialog);

    const reason = within(dialog).getByLabelText("Correction reason *");
    await user.type(reason, "  ab  ");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));
    expect(
      await within(dialog).findByText("Reason must be at least 3 characters"),
    ).toBeInTheDocument();
    expect(correctionCalls).toBe(0);

    await user.clear(reason);
    await user.type(reason, "  Recount at weighbridge  ");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    await waitFor(() => expect(correctionCalls).toBe(1));
    expect(correctionBody).toMatchObject({ reason: "Recount at weighbridge" });
    expect(toastMock.success).toHaveBeenCalledWith(
      "Correction recorded. The original entry remains in the audit trail.",
    );
  });

  it("labels the correction button while saving and after a rejection", async () => {
    const { gate, release } = createGate();
    let rejectNext = true;
    server.use(
      http.post("/api/finance/transactions/:transactionId/correct", async ({ request }) => {
        correctionCalls += 1;
        correctionBody = (await request.json()) as Record<string, unknown>;
        if (rejectNext) {
          return HttpResponse.json({ detail: "transaction is already voided" }, { status: 409 });
        }
        await gate;
        return HttpResponse.json({ ...TXN_SALE, id: 99, correction_of_id: 1 }, { status: 201 });
      }),
    );
    const { user, dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");
    await acknowledge(user, dialog);
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong amount");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    expect(await within(dialog).findByText("transaction is already voided")).toBeInTheDocument();
    expect(toastMock.error).toHaveBeenCalledWith("transaction is already voided");
    expect(
      within(dialog).getByRole("button", { name: "Retry correction" }),
    ).toBeInTheDocument();

    rejectNext = false;
    await user.click(within(dialog).getByRole("button", { name: "Retry correction" }));
    // The retry owns the dialog: the previous rejection is cleared while it runs.
    expect(
      await within(dialog).findByRole("button", { name: "Saving correction…" }),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("transaction is already voided")).not.toBeInTheDocument();

    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(correctionCalls).toBe(2);
  });
});

describe("FinancePage new transaction copy", () => {
  let postBody: Record<string, unknown> | null;
  let postCalls: number;

  beforeEach(() => {
    vi.clearAllMocks();
    postBody = null;
    postCalls = 0;
    server.use(
      financeHandler(PAYLOAD),
      animalsHandler(),
      http.post("/api/finance/new", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...TXN_FEED, id: 3 }, { status: 201 });
      }),
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New transaction" }));
    return { user, dialog: await screen.findByRole("dialog", { name: "New transaction" }) };
  }

  it("opens on the farm's expense defaults with no animal linked", async () => {
    const { dialog } = await openDialog();

    expect(within(dialog).getByRole("combobox", { name: "Type" })).toHaveTextContent("Expense");
    expect(within(dialog).getByRole("combobox", { name: "Category" })).toHaveTextContent("Other");
    expect(within(dialog).getByLabelText("Animal (optional)")).toHaveTextContent("— none —");
  });

  /** Walks a set of categories through one open draft: the server keeps
   *  rejecting, so what is proven is which human label picks which value —
   *  the enum codes on the wire stay paired with their picker labels. */
  async function postEachCategory(categories: string[], optionLabels: string[]) {
    server.use(
      http.post("/api/finance/new", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ detail: "ledger busy" }, { status: 503 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/^Amount/), "100");

    for (const [index, category] of categories.entries()) {
      await pickOption(
        user,
        within(dialog).getByRole("combobox", { name: "Category" }),
        optionLabels[index],
      );
      await user.click(
        within(dialog).getByRole("button", { name: /^(Add|Retry add) transaction$/ }),
      );
      await waitFor(() => expect(postBody).toMatchObject({ category, amount: 100 }));
    }
    expect(postCalls).toBe(categories.length);
  }

  it("posts the animal-side spending categories", async () => {
    await postEachCategory(
      ["ANIMAL_PURCHASE", "MEDICINE", "VET"],
      ["Animal purchase", "Medicine", "Vet"],
    );
  });

  it("posts the operating categories", async () => {
    await postEachCategory(["LABOUR", "EQUIPMENT", "MANURE"], ["Labour", "Equipment", "Manure"]);
  });

  it("describes every invalid new-transaction field to assistive tech", async () => {
    const { user, dialog } = await openDialog();

    setInput(within(dialog).getByLabelText(/^Date/), "");
    setInput(within(dialog).getByLabelText(/^Notes/), "x".repeat(256));
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    await waitFor(() =>
      expect(within(dialog).getByLabelText(/^Date/)).toHaveAccessibleDescription(
        "Date is required",
      ),
    );
    expect(within(dialog).getByLabelText(/^Amount/)).toHaveAccessibleDescription(
      "Amount must be greater than 0",
    );
    expect(within(dialog).getByLabelText(/^Notes/)).toHaveAccessibleDescription(/255 characters/);
    expect(postCalls).toBe(0);
  });

  it("reports an add that never reached the server", async () => {
    server.use(http.post("/api/finance/new", () => HttpResponse.error()));
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/^Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByText("Something went wrong")).toBeInTheDocument();
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("Something went wrong"));
  });

  it("labels the add button while saving, clearing the previous failure", async () => {
    const { gate, release } = createGate();
    let rejectNext = true;
    server.use(
      http.post("/api/finance/new", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        if (rejectNext) return HttpResponse.json({ detail: "database locked" }, { status: 500 });
        await gate;
        return HttpResponse.json({ ...TXN_FEED, id: 3 }, { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/^Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    expect(await within(dialog).findByText("database locked")).toBeInTheDocument();
    expect(toastMock.error).toHaveBeenCalledWith("database locked");

    rejectNext = false;
    await user.click(within(dialog).getByRole("button", { name: "Retry add transaction" }));
    expect(await within(dialog).findByRole("button", { name: "Saving…" })).toBeInTheDocument();
    expect(within(dialog).queryByText("database locked")).not.toBeInTheDocument();

    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(toastMock.success).toHaveBeenCalledWith("Transaction saved.");
  });
});

describe("FinancePage settling states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps the settled ledger on screen while a filter refetches", async () => {
    const { gate, release } = createGate();
    let calls = 0;
    server.use(
      http.get("/api/finance", async () => {
        calls += 1;
        if (calls > 1) await gate;
        return HttpResponse.json(PAYLOAD);
      }),
    );
    await renderLoaded();
    expect(screen.getByText("sold 10 bucks")).toBeInTheDocument();

    setInput(screen.getByLabelText("Filter by month"), "2025-12");

    expect(await screen.findByRole("status")).toHaveTextContent("Updating transactions…");
    expect(screen.getByText("sold 10 bucks")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "transactions pagination" })).toHaveAttribute(
      "aria-busy",
      "true",
    );

    release();
    await waitFor(() =>
      expect(screen.queryByText("Updating transactions…")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("sold 10 bucks")).toBeInTheDocument();
  });

  it("waits for the permission answer before deciding access", async () => {
    const { gate, release } = createGate();
    server.use(
      financeHandler(PAYLOAD),
      http.get("/api/auth/permissions", async () => {
        await gate;
        return HttpResponse.json({ is_owner: true, permissions: ["finance.view"] });
      }),
    );
    renderWithProviders(<FinancePage />);

    // The page holds its loading skeleton (real header copy, no data) until
    // the permission answer lands — it never guesses "no access" meanwhile.
    expect(await screen.findByRole("heading", { name: "Finance" })).toBeInTheDocument();
    expect(
      screen.getByText("Income, expenses and monthly profit & loss for the farm."),
    ).toBeInTheDocument();
    expect(screen.queryByText("You don't have access to this page.")).not.toBeInTheDocument();
    expect(screen.queryByText("Total income")).not.toBeInTheDocument();

    release();
    expect(await screen.findByText("Total income")).toBeInTheDocument();
  });

  it("falls back to a generic message when the ledger request never lands", async () => {
    server.use(http.get("/api/finance", () => HttpResponse.error()));
    renderWithProviders(<FinancePage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load finance.");
    expect(screen.getByRole("button", { name: "Retry finance" })).toBeInTheDocument();
  });
});
