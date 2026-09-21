/**
 * Branch coverage the other finance suites leave open: ledger row rendering
 * (the void tint, half-linked animals, source vs. correction provenance, the
 * manager-only actions column, a break-even P&L month), the Clear affordance
 * for a type- or category-only filter, the settling banner, the access gate
 * while the permission answer is still unknown, and — in both dialogs — the
 * aria-invalid contract, the prefilled correction state, the acknowledgement
 * gate and the in-flight submit lock.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ALL_PERMISSIONS, permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FinancePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture/scroll APIs the Select relies on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

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
  ...TXN_INCOME,
  id: 2,
  date: "2026-01-07",
  type: "EXPENSE",
  category: "FEED",
  amount: 90000,
  notes: "bagged maize",
  related_animal_id: null,
  animal_tag: null,
};

const TXN_FEED_PURCHASE = {
  ...TXN_EXPENSE,
  id: 5,
  notes: "restock maize",
  amount: 2000,
  source_type: "FEED_PURCHASE",
  source_id: 5,
};

const PAYLOAD = {
  transactions: [TXN_INCOME, TXN_EXPENSE],
  transactions_total: 2,
  limit: 50,
  offset: 0,
  total_income: 150000,
  total_expense: 90000,
  feed_stock_value: 12000,
  pnl: [{ month: "2026-01", income: 150000, expense: 90000, net: 60000, categories: {} }],
};

function financeHandler(payload: Record<string, unknown>) {
  return http.get("/api/finance", () =>
    HttpResponse.json({
      transactions_total: 0,
      limit: 50,
      offset: 0,
      total_income: 0,
      total_expense: 0,
  feed_stock_value: 12000,
      pnl: [],
      ...payload,
    }),
  );
}

/** A response the test releases by hand, so an in-flight UI state can be read. */
function parkRequest() {
  let release: (() => void) | undefined;
  const parked = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { parked, release: () => release?.() };
}

async function renderLoaded() {
  renderWithProviders(<FinancePage />);
  expect(await screen.findByText("Total income (all time)")).toBeInTheDocument();
}

/** The ledger table, addressed by a header only it renders. */
function ledgerTable(): HTMLElement {
  return screen.getByText("Source / audit").closest("table") as HTMLElement;
}

function rowOf(notes: string): HTMLElement {
  // Ledger rows also render in the below-md card list (md:hidden) — scope to
  // the desktop table inside the Transactions card.
  const section = screen.getByText("Transactions").closest("[data-slot='card']") as HTMLElement;
  const table = section.querySelector('[class~="md:block"] table') as HTMLElement;
  return within(table).getByText(notes).closest("tr") as HTMLElement;
}

/** Date, Type, Category, Amount, Animal, Notes, Source / audit[, Actions]. */
function animalCell(row: HTMLElement): HTMLElement {
  return within(row).getAllByRole("cell")[4];
}

/** Ledger/P&L rows also render in below-md card lists (md:hidden) inside the
 * same section cards — scope to the desktop table for unambiguous lookups. */
function desktopTableOf(title: string) {
  const section = screen.getByText(title).closest("[data-slot='card']") as HTMLElement;
  const table = section.querySelector('[class~="md:block"] table');
  expect(table).not.toBeNull();
  return within(table as HTMLElement);
}
/** The monthly P&L table. */
function pnlScope() {
  return desktopTableOf("Monthly P&L (last 12 months)");
}

describe("FinancePage ledger row rendering", () => {
  it("tints the voided rows only", async () => {
    server.use(
      financeHandler({
        transactions: [
          { ...TXN_INCOME, voided_at: "2026-08-08T10:00:00Z", void_reason: "Wrong amount" },
          TXN_EXPENSE,
        ],
        transactions_total: 2,
      }),
    );
    await renderLoaded();

    const voided = rowOf("sold 10 bucks");
    expect(voided).toHaveClass("bg-muted/40", "opacity-70");
    const live = rowOf("bagged maize");
    expect(live).not.toHaveClass("bg-muted/40");
    expect(live).not.toHaveClass("opacity-70");
  });

  it("renders an em dash unless a row carries both an animal id and a tag", async () => {
    server.use(
      financeHandler({
        transactions: [
          { ...TXN_INCOME, id: 21, notes: "id without tag", animal_tag: null },
          { ...TXN_INCOME, id: 22, notes: "tag without id", related_animal_id: null },
        ],
        transactions_total: 2,
      }),
    );
    await renderLoaded();

    for (const notes of ["id without tag", "tag without id"]) {
      const row = rowOf(notes);
      expect(animalCell(row)).toHaveTextContent(/^—$/);
      expect(within(row).queryByRole("link")).not.toBeInTheDocument();
    }
  });

  it("keeps the tag as plain text for an operator who cannot open animal profiles", async () => {
    server.use(
      permissionsHandler(["finance.view"]),
      financeHandler({ transactions: [TXN_INCOME], transactions_total: 1 }),
    );
    await renderLoaded();

    const row = rowOf("sold 10 bucks");
    expect(animalCell(row)).toHaveTextContent(/^G-011$/);
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
  });

  it("names the source only when the row carries both a source type and a source id", async () => {
    server.use(
      financeHandler({
        transactions: [
          { ...TXN_EXPENSE, id: 31, notes: "half sourced", source_type: "ANIMAL_SALE" },
          { ...TXN_EXPENSE, id: 32, notes: "orphan source id", source_id: 9 },
          { ...TXN_EXPENSE, id: 33, notes: "fully sourced", source_type: "PURCHASE_BATCH", source_id: 4 },
        ],
        transactions_total: 3,
      }),
    );
    await renderLoaded();

    // A half-populated source is no source at all: the row stays a manual entry.
    const half = rowOf("half sourced");
    expect(within(half).queryByText(/^Source:/)).not.toBeInTheDocument();
    expect(within(half).getByText("Manual entry")).toBeInTheDocument();
    const orphan = rowOf("orphan source id");
    expect(within(orphan).queryByText(/^Source:/)).not.toBeInTheDocument();
    expect(within(orphan).getByText("Manual entry")).toBeInTheDocument();
    expect(within(rowOf("fully sourced")).getByText("Source: Purchase batch #4")).toBeInTheDocument();
  });

  it("separates a replacement row from a manual entry in the audit column", async () => {
    server.use(
      financeHandler({
        transactions: [
          { ...TXN_EXPENSE, id: 41, notes: "manual row" },
          { ...TXN_EXPENSE, id: 42, notes: "replacement row", correction_of_id: 9 },
        ],
        transactions_total: 2,
      }),
    );
    await renderLoaded();

    const manual = rowOf("manual row");
    expect(within(manual).getByText("Manual entry")).toBeInTheDocument();
    expect(within(manual).queryByText(/Correction of transaction/)).not.toBeInTheDocument();
    const replacement = rowOf("replacement row");
    expect(within(replacement).getByText("Correction of transaction #9")).toBeInTheDocument();
    // A correction is not a manual entry, even with no system source.
    expect(within(replacement).queryByText("Manual entry")).not.toBeInTheDocument();
  });

  it("heads the actions column only for an operator who may correct", async () => {
    server.use(financeHandler({ transactions: [TXN_EXPENSE], transactions_total: 1 }));
    await renderLoaded();

    const table = ledgerTable();
    expect(within(table).getAllByRole("columnheader")).toHaveLength(8);
    expect(within(table).getByRole("columnheader", { name: "Actions" })).toBeInTheDocument();
  });

  it("drops the actions header for a read-only operator so the columns still line up", async () => {
    server.use(
      permissionsHandler(["finance.view"]),
      financeHandler({ transactions: [TXN_EXPENSE], transactions_total: 1 }),
    );
    await renderLoaded();

    const table = ledgerTable();
    expect(within(table).getAllByRole("columnheader")).toHaveLength(7);
    expect(within(table).queryByRole("columnheader", { name: "Actions" })).not.toBeInTheDocument();
  });

  it("does not paint a break-even month as a loss", async () => {
    server.use(
      financeHandler({
        transactions: [],
        pnl: [
          { month: "2026-03", income: 1000, expense: 1000, net: 0, categories: {} },
          { month: "2026-02", income: 0, expense: 500, net: -500, categories: {} },
        ],
      }),
    );
    await renderLoaded();

    const breakEven = within(
      pnlScope().getByRole("button", { name: "2026-03" }).closest("tr") as HTMLElement,
    ).getAllByRole("cell")[3];
    expect(breakEven).toHaveTextContent("₹0");
    expect(breakEven).toHaveClass("text-success");
    expect(breakEven).not.toHaveClass("text-destructive");
    const loss = within(
      pnlScope().getByRole("button", { name: "2026-02" }).closest("tr") as HTMLElement,
    ).getAllByRole("cell")[3];
    expect(loss).toHaveClass("text-destructive");
  });
});

describe("FinancePage filter affordances", () => {
  beforeEach(() => {
    server.use(financeHandler(PAYLOAD));
  });

  it("offers Clear once a type-only filter is active", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Filter transactions by type"));
    await user.click(await screen.findByRole("option", { name: "Income" }));

    expect(await screen.findByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("offers Clear once a category-only filter is active", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Filter transactions by category"));
    await user.click(await screen.findByRole("option", { name: "Feed" }));

    expect(await screen.findByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("announces the stale ledger only while a filter change is settling", async () => {
    const { parked, release } = parkRequest();
    let requests = 0;
    server.use(
      http.get("/api/finance", async () => {
        requests += 1;
        if (requests > 1) await parked;
        return HttpResponse.json(PAYLOAD);
      }),
    );
    await renderLoaded();
    expect(screen.queryByText("Updating transactions…")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by month"), {
      target: { value: "2025-12" },
    });

    const status = await screen.findByText("Updating transactions…");
    expect(status).toHaveAttribute("role", "status");
    // The previous page stays on screen while the replacement is fetched.
    expect(screen.getAllByText("sold 10 bucks").length).toBeGreaterThan(0);

    release();
    await waitFor(() =>
      expect(screen.queryByText("Updating transactions…")).not.toBeInTheDocument(),
    );
  });
});

describe("FinancePage permission gate", () => {
  it("waits for the permission answer before refusing access", async () => {
    const { parked, release } = parkRequest();
    server.use(
      http.get("/api/auth/permissions", async () => {
        await parked;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
      financeHandler(PAYLOAD),
    );
    const { waitForAuthIdle } = renderWithProviders(<FinancePage />);
    await waitForAuthIdle();

    // The page holds its loading skeleton (real header copy, no data) until
    // the permission answer lands — it never guesses "no access" meanwhile.
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Finance" })).toBeInTheDocument(),
    );
    expect(
      screen.getByText("Income, expenses and monthly profit & loss for the farm."),
    ).toBeInTheDocument();
    expect(screen.queryByText("You don't have access to this page.")).not.toBeInTheDocument();
    expect(screen.queryByText("Total income (all time)")).not.toBeInTheDocument();

    release();
    expect(await screen.findByText("Total income (all time)")).toBeInTheDocument();
  });
});

describe("FinancePage new-transaction dialog branches", () => {
  let postCalls: number;

  beforeEach(() => {
    postCalls = 0;
    server.use(
      financeHandler(PAYLOAD),
      http.post("/api/finance/new", () => {
        postCalls += 1;
        return HttpResponse.json({ ...TXN_EXPENSE, id: 3 }, { status: 201 });
      }),
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New transaction" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("marks nothing invalid on a freshly opened form", async () => {
    const { dialog } = await openDialog();

    for (const label of [/^Date/, /^Amount/, /^Notes/]) {
      const field = within(dialog).getByLabelText(label);
      expect(field).not.toHaveAttribute("aria-invalid");
      expect(field).not.toHaveAttribute("aria-describedby");
    }
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("marks the date and links its message when the entry is dated ahead", async () => {
    const { user, dialog } = await openDialog();

    fireEvent.change(within(dialog).getByLabelText(/^Date/), {
      target: { value: "2099-01-01" },
    });
    await user.type(within(dialog).getByLabelText(/^Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    const date = within(dialog).getByLabelText(/^Date/);
    await waitFor(() => expect(date).toHaveAttribute("aria-invalid", "true"));
    expect(date).toHaveAttribute("aria-describedby", "transaction-date-error");
    expect(within(dialog).getByText("Date can't be in the future")).toHaveAttribute(
      "id",
      "transaction-date-error",
    );
    // Only the offending field is flagged.
    expect(within(dialog).getByLabelText(/^Amount/)).not.toHaveAttribute("aria-invalid");
    expect(postCalls).toBe(0);
  });

  it("marks the amount and links its message when it is not a usable figure", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/^Amount/), "0");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    const amount = within(dialog).getByLabelText(/^Amount/);
    await waitFor(() => expect(amount).toHaveAttribute("aria-invalid", "true"));
    expect(amount).toHaveAttribute("aria-describedby", "transaction-amount-error");
    expect(within(dialog).getByText("Amount must be greater than 0")).toHaveAttribute(
      "id",
      "transaction-amount-error",
    );
    expect(within(dialog).getByLabelText(/^Date/)).not.toHaveAttribute("aria-invalid");
    expect(postCalls).toBe(0);
  });

  it("rejects notes longer than the ledger column stores", async () => {
    const { user, dialog } = await openDialog();

    // maxLength only guards typing; a paste-sized value still has to be caught.
    fireEvent.change(within(dialog).getByLabelText(/^Notes/), {
      target: { value: "n".repeat(256) },
    });
    await user.type(within(dialog).getByLabelText(/^Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    const notes = within(dialog).getByLabelText(/^Notes/);
    await waitFor(() => expect(notes).toHaveAttribute("aria-invalid", "true"));
    expect(notes).toHaveAttribute("aria-describedby", "transaction-notes-error");
    expect(within(dialog).getByRole("alert")).toHaveAttribute("id", "transaction-notes-error");
    expect(postCalls).toBe(0);
  });

  it("locks and relabels the submit while the write is in flight", async () => {
    const { parked, release } = parkRequest();
    server.use(
      http.post("/api/finance/new", async () => {
        postCalls += 1;
        await parked;
        return HttpResponse.json({ ...TXN_EXPENSE, id: 3 }, { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/^Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
    await waitFor(() => expect(postCalls).toBe(1));

    // The button itself is disabled, not merely covered by the fieldset.
    expect(within(dialog).getByRole("button", { name: "Saving…" })).toHaveAttribute("disabled");

    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("FinancePage correction dialog branches", () => {
  let correctionCalls: number;

  beforeEach(() => {
    correctionCalls = 0;
    server.use(
      financeHandler({
        ...PAYLOAD,
        transactions: [TXN_INCOME, TXN_EXPENSE, TXN_FEED_PURCHASE],
        transactions_total: 3,
      }),
      // The picker only reaches for a profile when it was handed no label.
      http.get("/api/animals/:animalId", () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
      http.post("/api/finance/transactions/:transactionId/correct", () => {
        correctionCalls += 1;
        return HttpResponse.json({ ...TXN_INCOME, id: 9, correction_of_id: 1 }, { status: 201 });
      }),
    );
  });

  async function openCorrection(notes: string, title: string) {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf(notes)).getByRole("button", { name: "Correct" }));
    return { user, dialog: await screen.findByRole("dialog", { name: title }) };
  }

  function acknowledgement(dialog: HTMLElement): HTMLElement {
    return within(dialog).getByRole("checkbox", {
      name: /I understand the original transaction will be voided and replaced/i,
    });
  }

  it("prefills the entry it is replacing", async () => {
    const { dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");

    expect(within(dialog).getByLabelText("Date *")).toHaveValue("2026-01-05");
    expect(within(dialog).getByLabelText("Amount (₹) *")).toHaveValue(150000);
    expect(within(dialog).getByLabelText("Notes")).toHaveValue("sold 10 bucks");
    // Type, Category, then the animal picker.
    expect(within(dialog).getAllByRole("combobox")[2]).toHaveTextContent(/^G-011$/);
    // Nothing has failed yet: no field is flagged and no error is announced.
    for (const label of ["Date *", "Amount (₹) *", "Correction reason *"]) {
      expect(within(dialog).getByLabelText(label)).not.toHaveAttribute("aria-invalid");
    }
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("starts from the none sentinel when the entry has no animal", async () => {
    const { dialog } = await openCorrection("bagged maize", "Correct transaction #2");

    expect(within(dialog).getAllByRole("combobox")[2]).toHaveTextContent(/^— none —$/);
  });

  it("reports an unlinked entry as such in the read-only animal field", async () => {
    server.use(permissionsHandler(["finance.view", "finance.manage"]));
    const { dialog } = await openCorrection("bagged maize", "Correct transaction #2");

    expect(within(dialog).getByLabelText("Linked animal")).toHaveTextContent(/^No animal linked$/);
  });

  it("flags exactly the correction fields that failed and links their messages", async () => {
    const { user, dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");
    await user.click(acknowledgement(dialog));

    fireEvent.change(within(dialog).getByLabelText("Date *"), {
      target: { value: "2099-01-01" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    const date = within(dialog).getByLabelText("Date *");
    await waitFor(() => expect(date).toHaveAttribute("aria-invalid", "true"));
    expect(date).toHaveAttribute("aria-describedby", "correction-date-1-error");
    expect(within(dialog).getByText("Date can't be in the future")).toHaveAttribute(
      "id",
      "correction-date-1-error",
    );
    const reason = within(dialog).getByLabelText("Correction reason *");
    expect(reason).toHaveAttribute("aria-invalid", "true");
    expect(reason).toHaveAttribute("aria-describedby", "correction-reason-1-error");
    expect(within(dialog).getByText("Reason must be at least 3 characters")).toHaveAttribute(
      "id",
      "correction-reason-1-error",
    );
    // The untouched amount carried over from the original entry stays clean.
    expect(within(dialog).getByLabelText("Amount (₹) *")).not.toHaveAttribute("aria-invalid");
    expect(correctionCalls).toBe(0);
  });

  it("flags a corrected feed quantity that is not a positive weight", async () => {
    const { user, dialog } = await openCorrection("restock maize", "Correct transaction #5");
    const quantity = within(dialog).getByLabelText("Corrected quantity (kg)");
    expect(quantity).not.toHaveAttribute("aria-invalid");

    await user.click(acknowledgement(dialog));
    await user.type(quantity, "0");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Reweighed the load");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    await waitFor(() => expect(quantity).toHaveAttribute("aria-invalid", "true"));
    expect(quantity).toHaveAttribute("aria-describedby", "correction-feed-quantity-5-error");
    expect(within(dialog).getByText("Quantity must be greater than 0")).toHaveAttribute(
      "id",
      "correction-feed-quantity-5-error",
    );
    expect(correctionCalls).toBe(0);
  });

  it("records nothing from a submit that never acknowledged the consequence", async () => {
    const { user, dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong amount");

    // An implicit submission cannot route around the acknowledgement: the
    // dialog is still standing, unposted, when the operator ticks the box —
    // and the very same form then records exactly one replacement.
    fireEvent.submit(dialog.querySelector("form") as HTMLFormElement);
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Record correction" }));
    await waitFor(() => expect(correctionCalls).toBe(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(correctionCalls).toBe(1);
  });

  it("re-locks the submit when the acknowledgement is withdrawn", async () => {
    const { user, dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");
    const submit = within(dialog).getByRole("button", { name: "Record correction" });
    expect(submit).toHaveAttribute("disabled");

    await user.click(acknowledgement(dialog));
    expect(acknowledgement(dialog)).toBeChecked();
    expect(submit).not.toHaveAttribute("disabled");

    await user.click(acknowledgement(dialog));
    expect(acknowledgement(dialog)).not.toBeChecked();
    expect(submit).toHaveAttribute("disabled");
  });

  it("locks and relabels the correction submit while the replacement is written", async () => {
    const { parked, release } = parkRequest();
    server.use(
      http.post("/api/finance/transactions/:transactionId/correct", async () => {
        correctionCalls += 1;
        await parked;
        return HttpResponse.json({ ...TXN_INCOME, id: 9, correction_of_id: 1 }, { status: 201 });
      }),
    );
    const { user, dialog } = await openCorrection("sold 10 bucks", "Correct transaction #1");
    await user.click(acknowledgement(dialog));
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong amount");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));
    await waitFor(() => expect(correctionCalls).toBe(1));

    expect(within(dialog).getByRole("button", { name: "Saving correction…" })).toHaveAttribute(
      "disabled",
    );

    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
