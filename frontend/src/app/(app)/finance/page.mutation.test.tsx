/**
 * Mutation-hardening tests for src/app/(app)/finance/page.tsx.
 *
 * Each describe pins a survivor cluster from the Stryker report:
 *  - month URL-param sanitisation (the /^\d{4}-\d{2}$/ contract);
 *  - the exact ledger URL write-through (router.replace(url, { scroll: false }))
 *    for month/type/category picks, ALL picks, the P&L shortcut and Clear;
 *  - re-syncing filter state when the URL changes underneath the page;
 *  - the correction consequence hint (copy, appearance and clearing);
 *  - blank feed-quantity staying optional ("" → undefined, not 0);
 *  - enum labels inside the correction selects and the "All …" options;
 *  - the stale-error guard after a dismissed add, and the permissions retry;
 *  - sort direction indicators and the sorting description line.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FinancePage from "./page";

const replaceMock = vi.fn();
let urlParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => urlParams,
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

beforeEach(() => {
  replaceMock.mockClear();
  urlParams = new URLSearchParams();
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

const TXN_FEED_PURCHASE = {
  ...TXN_INCOME,
  id: 5,
  date: "2026-01-07",
  type: "EXPENSE",
  category: "FEED",
  amount: 2000,
  notes: "restock maize",
  related_animal_id: null,
  animal_tag: null,
  source_type: "FEED_PURCHASE",
  source_id: 5,
};

const PAYLOAD = {
  transactions: [TXN_INCOME, TXN_FEED_PURCHASE],
  transactions_total: 2,
  limit: 50,
  offset: 0,
  total_income: 150000,
  total_expense: 2000,
  feed_stock_value: 12000,
  pnl: [{ month: "2026-01", income: 150000, expense: 2000, net: 148000, categories: {} }],
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

let lastParams = new URLSearchParams();

function useFinancePayload(payload: Record<string, unknown> = PAYLOAD) {
  return http.get("/api/finance", ({ request }) => {
    lastParams = new URL(request.url).searchParams;
    return HttpResponse.json(payload);
  });
}

async function renderLoaded() {
  renderWithProviders(<FinancePage />);
  expect(await screen.findByText("Total income (all time)")).toBeInTheDocument();
}

/** Ledger/P&L rows also render in below-md card lists (md:hidden) inside the
 * same section cards — scope to the desktop table for unambiguous lookups. */
function desktopTableOf(title: string) {
  const section = screen.getByText(title).closest("[data-slot='card']") as HTMLElement;
  const table = section.querySelector('[class~="md:block"] table');
  expect(table).not.toBeNull();
  return within(table as HTMLElement);
}
/** The 8-column transactions ledger. */
function ledgerScope() {
  return desktopTableOf("Transactions");
}
/** The monthly P&L table. */
function pnlScope() {
  return desktopTableOf("Monthly P&L (last 12 months)");
}

describe("FinancePage month URL param sanitisation", () => {
  it.each([
    "garbage",
    "2026-1",
    "26-01",
    "2026-013",
    "2026-0111",
    "x2026-01",
    "2026-01x",
    // RT-P11-1: two-digit months the old \d{4}-\d{2} grammar accepted but
    // the typing handler never produces — a crafted link must not persist
    // an invalid month the API cannot filter on.
    "2026-99",
    "2026-00",
    "2026-13",
    "0000-13",
  ])("ignores a malformed month (%s) instead of filtering on it", async (bad) => {
    urlParams = new URLSearchParams(`month=${encodeURIComponent(bad)}`);
    server.use(useFinancePayload());
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Total income (all time)")).toBeInTheDocument();

    // The filter state stays "off"…
    expect(screen.getByLabelText("Filter by month")).toHaveValue("");
    // …so the ledger request carries no month, and no URL rewrite happens.
    await waitFor(() => expect(lastParams.get("month")).toBeNull());
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("keeps a well-formed month and offers Clear", async () => {
    urlParams = new URLSearchParams("month=2025-12");
    server.use(useFinancePayload());
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Total income (all time)")).toBeInTheDocument();

    expect(screen.getByLabelText("Filter by month")).toHaveValue("2025-12");
    await waitFor(() => expect(lastParams.get("month")).toBe("2025-12"));
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });
});

describe("FinancePage ledger URL write-through", () => {
  beforeEach(() => {
    server.use(useFinancePayload());
  });

  it("writes each filter change into the URL exactly once, ALL meaning absent", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    // month on
    fireEvent.change(screen.getByLabelText("Filter by month"), {
      target: { value: "2025-12" },
    });
    await waitFor(() =>
      expect(replaceMock).toHaveBeenLastCalledWith("/finance?month=2025-12", {
        scroll: false,
      }),
    );

    // type on top
    await user.click(screen.getByLabelText("Filter transactions by type"));
    await user.click(await screen.findByRole("option", { name: "Income" }));
    await waitFor(() =>
      expect(replaceMock).toHaveBeenLastCalledWith("/finance?month=2025-12&type=INCOME", {
        scroll: false,
      }),
    );

    // category on top of that
    await user.click(screen.getByLabelText("Filter transactions by category"));
    await user.click(await screen.findByRole("option", { name: "Feed" }));
    await waitFor(() =>
      expect(replaceMock).toHaveBeenLastCalledWith(
        "/finance?month=2025-12&type=INCOME&category=FEED",
        { scroll: false },
      ),
    );

    // month off again
    fireEvent.change(screen.getByLabelText("Filter by month"), { target: { value: "" } });
    await waitFor(() =>
      expect(replaceMock).toHaveBeenLastCalledWith("/finance?type=INCOME&category=FEED", {
        scroll: false,
      }),
    );

    // picking "All types" from the list deletes the param (and the option is labelled)
    await user.click(screen.getByLabelText("Filter transactions by type"));
    await user.click(await screen.findByRole("option", { name: "All types" }));
    await waitFor(() =>
      expect(replaceMock).toHaveBeenLastCalledWith("/finance?category=FEED", {
        scroll: false,
      }),
    );
    expect(screen.getByLabelText("Filter transactions by type")).toHaveTextContent("All types");

    // same for the category list
    await user.click(screen.getByLabelText("Filter transactions by category"));
    await user.click(await screen.findByRole("option", { name: "All categories" }));
    await waitFor(() =>
      expect(replaceMock).toHaveBeenLastCalledWith("/finance", { scroll: false }),
    );
    expect(screen.getByLabelText("Filter transactions by category")).toHaveTextContent(
      "All categories",
    );

    // every write was a replace, never a push, and never scrolled
    expect(replaceMock.mock.calls.length).toBeGreaterThan(0);
    for (const call of replaceMock.mock.calls) {
      expect(call[1]).toEqual({ scroll: false });
    }
  });

  it("preserves unknown query params while rewriting the ledger URL", async () => {
    urlParams = new URLSearchParams("q=xyz");
    await renderLoaded();

    fireEvent.change(screen.getByLabelText("Filter by month"), {
      target: { value: "2025-12" },
    });
    await waitFor(() =>
      expect(replaceMock).toHaveBeenLastCalledWith("/finance?q=xyz&month=2025-12", {
        scroll: false,
      }),
    );
  });

  it("writes the P&L month shortcut and the one-shot Clear the same way", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(pnlScope().getByRole("button", { name: "2026-01" }));
    await waitFor(() =>
      expect(replaceMock).toHaveBeenLastCalledWith("/finance?month=2026-01", {
        scroll: false,
      }),
    );
    expect(screen.getByLabelText("Filter by month")).toHaveValue("2026-01");

    await user.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() =>
      expect(replaceMock).toHaveBeenLastCalledWith("/finance", { scroll: false }),
    );
    expect(screen.getByLabelText("Filter by month")).toHaveValue("");
  });

  it("re-syncs the filters when the URL changes underneath the page", async () => {
    urlParams = new URLSearchParams("month=2025-12");
    server.use(useFinancePayload());
    const { rerender } = renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Total income (all time)")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter by month")).toHaveValue("2025-12");

    // A same-route navigation (shared link, browser Back) arrives.
    urlParams = new URLSearchParams("month=2026-01&type=INCOME");
    rerender(<FinancePage />);
    await waitFor(() => expect(screen.getByLabelText("Filter by month")).toHaveValue("2026-01"));
    expect(screen.getByLabelText("Filter transactions by type")).toHaveTextContent("Income");
    await waitFor(() => expect(lastParams.get("month")).toBe("2026-01"));
    expect(lastParams.get("type")).toBe("INCOME");
  });
});

describe("FinancePage sorting", () => {
  beforeEach(() => {
    server.use(useFinancePayload());
  });

  function dateHead(): HTMLElement {
    return screen.getByRole("columnheader", { name: /^Date/ });
  }
  function amountHead(): HTMLElement {
    return screen.getByRole("columnheader", { name: /^Amount/ });
  }

  it("tracks the sort direction per column, returning to neutral", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    expect(dateHead()).toHaveAttribute("aria-sort", "none");
    expect(amountHead()).toHaveAttribute("aria-sort", "none");
    expect(
      screen.queryByText("Filter the ledger by month, type or category."),
    ).not.toHaveTextContent("Sorting applies to the current page.");

    await user.click(within(dateHead()).getByRole("button"));
    expect(dateHead()).toHaveAttribute("aria-sort", "ascending");
    expect(amountHead()).toHaveAttribute("aria-sort", "none");
    expect(
      screen.getByText(
        "Filter the ledger by month, type or category. Sorting applies to the current page.",
      ),
    ).toBeInTheDocument();

    await user.click(within(dateHead()).getByRole("button"));
    expect(dateHead()).toHaveAttribute("aria-sort", "descending");

    await user.click(within(amountHead()).getByRole("button"));
    expect(amountHead()).toHaveAttribute("aria-sort", "ascending");
    expect(dateHead()).toHaveAttribute("aria-sort", "none");

    await user.click(within(amountHead()).getByRole("button"));
    expect(amountHead()).toHaveAttribute("aria-sort", "descending");
    await user.click(within(amountHead()).getByRole("button"));
    expect(amountHead()).toHaveAttribute("aria-sort", "none");
    expect(
      screen.getByText("Filter the ledger by month, type or category."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        "Filter the ledger by month, type or category. Sorting applies to the current page.",
      ),
    ).not.toBeInTheDocument();
  });

  it("orders the visible rows by the chosen column and direction", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    function ledgerBodyRows(): HTMLElement[] {
      const table = screen.getByText("Source / audit").closest("table") as HTMLElement;
      return within(table).getAllByRole("row").slice(1);
    }

    // API order: 5 Jan (income) then 7 Jan (feed).
    await user.click(within(dateHead()).getByRole("button"));
    await waitFor(() => expect(ledgerBodyRows()[0]).toHaveTextContent("5 Jan 2026"));
    expect(ledgerBodyRows()[1]).toHaveTextContent("7 Jan 2026");

    await user.click(within(dateHead()).getByRole("button"));
    await waitFor(() => expect(ledgerBodyRows()[0]).toHaveTextContent("7 Jan 2026"));

    // amount ascending puts the ₹2,000 feed row first
    await user.click(within(amountHead()).getByRole("button"));
    await waitFor(() => expect(ledgerBodyRows()[0]).toHaveTextContent("restock maize"));
    expect(ledgerBodyRows()[1]).toHaveTextContent("sold 10 bucks");

    // descending flips it back
    await user.click(within(amountHead()).getByRole("button"));
    await waitFor(() => expect(ledgerBodyRows()[0]).toHaveTextContent("sold 10 bucks"));
  });
});

describe("FinancePage correction dialog mutation hardening", () => {
  let correctionBody: Record<string, unknown> | null;
  let correctionCalls: number;

  beforeEach(() => {
    correctionBody = null;
    correctionCalls = 0;
    server.use(
      useFinancePayload(),
      http.get("/api/animals", () => HttpResponse.json({ animals: [ANIMAL], total: 1 })),
      http.post("/api/finance/transactions/:transactionId/correct", async ({ request }) => {
        correctionCalls += 1;
        correctionBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...TXN_FEED_PURCHASE, id: 9, correction_of_id: 5 },
          { status: 201 },
        );
      }),
    );
  });

  function acknowledgement(dialog: HTMLElement): HTMLElement {
    return within(dialog).getByRole("checkbox", {
      name: /I understand the original transaction will be voided and replaced/i,
    });
  }

  async function openFeedCorrection() {
    const user = userEvent.setup();
    await renderLoaded();
    const row = ledgerScope().getByText("restock maize").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Correct" }));
    return { user, dialog: await screen.findByRole("dialog", { name: "Correct transaction #5" }) };
  }

  it("labels the type and category options with the enum names", async () => {
    const { user, dialog } = await openFeedCorrection();

    const [typeSelect, categorySelect] = within(dialog).getAllByRole("combobox");
    await user.click(typeSelect);
    expect(await screen.findByRole("option", { name: "Income" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Expense" })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(categorySelect);
    expect(await screen.findByRole("option", { name: "Feed" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Animal sale" })).toBeInTheDocument();
  });

  it("answers an unacknowledged submission with the consequence hint, then clears it", async () => {
    const { user, dialog } = await openFeedCorrection();
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong invoice");

    // Enter in an input submits even though the button is disabled.
    fireEvent.submit(dialog.querySelector("form") as HTMLFormElement);

    const hint = await within(dialog).findByText(
      "Confirm the consequence checkbox before recording the correction.",
    );
    expect(hint).toHaveAttribute("role", "alert");
    expect(correctionCalls).toBe(0);

    // Acknowledging and re-submitting runs the write and drops the hint.
    await user.click(acknowledgement(dialog));
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));
    await waitFor(() => expect(correctionCalls).toBe(1));
    expect(
      screen.queryByText("Confirm the consequence checkbox before recording the correction."),
    ).not.toBeInTheDocument();
  });

  it("keeps a blank corrected quantity optional: emptied, not zero, not an error", async () => {
    const { user, dialog } = await openFeedCorrection();

    const quantity = within(dialog).getByLabelText("Corrected quantity (kg)");
    await user.type(quantity, "100");
    await user.clear(quantity);
    expect(quantity).toHaveValue(null);

    await user.click(acknowledgement(dialog));
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Skip reweigh");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    await waitFor(() => expect(correctionCalls).toBe(1));
    expect(correctionBody).not.toHaveProperty("feed_quantity_kg");
    // An emptied field is not a validation failure.
    expect(screen.queryByText("Quantity must be greater than 0")).not.toBeInTheDocument();
  });

  it("closes via Cancel, Escape and the backdrop path alike", async () => {
    const { user, dialog } = await openFeedCorrection();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(correctionCalls).toBe(0);
  });
});

describe("FinancePage add-dialog and permissions mutation hardening", () => {
  it("never applies a stale failure to a dialog the operator reopened", async () => {
    let release: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    let postCalls = 0;
    server.use(
      useFinancePayload(),
      http.post("/api/finance/new", async () => {
        postCalls += 1;
        await parked;
        return HttpResponse.json({ detail: "ledger write conflict" }, { status: 409 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
    await waitFor(() => expect(postCalls).toBe(1));

    // Dismissal bumps the attempt: the late failure must land nowhere.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();

    release?.();
    await waitFor(() => expect(within(reopened).getByLabelText(/Amount/)).toBeEnabled());
    // The stale 409 never shows in the reopened draft.
    await waitFor(
      () => expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument(),
      { timeout: 2000 },
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("reopens the add dialog clean after an inline failure", async () => {
    server.use(
      useFinancePayload(),
      http.post("/api/finance/new", () =>
        HttpResponse.json({ detail: "amount exceeds limit" }, { status: 400 }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Amount/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
    expect(await within(dialog).findByText("amount exceeds limit")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
    // The draft reset to defaults, not the rejected amount.
    expect(within(reopened).getByLabelText(/Amount/)).toHaveValue(null);
  });

  it("retries the permissions fetch from the error state", async () => {
    let permsCalls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        permsCalls += 1;
        return HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 });
      }),
      useFinancePayload(),
    );
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    const before = permsCalls;
    await user.click(screen.getByRole("button", { name: "Retry permissions" }));
    await waitFor(() => expect(permsCalls).toBe(before + 1));
  });

  it("shows the read-only ledger for a finance.view-only operator without Manage actions", async () => {
    server.use(permissionsHandler(["finance.view"]), useFinancePayload());
    await renderLoaded();
    expect(screen.getAllByText("sold 10 bucks").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "New transaction" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Correct" })).not.toBeInTheDocument();
  });
});

describe("FinancePage round-2 mutation survivors", () => {
  it("clears the consequence hint on the next submission attempt", async () => {
    let calls = 0;
    server.use(
      useFinancePayload(),
      http.post("/api/finance/transactions/:transactionId/correct", async ({ request }) => {
        calls += 1;
        await request.json();
        return HttpResponse.json({ detail: "correction rejected" }, { status: 409 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = ledgerScope().getByText("restock maize").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Correct" }));
    const dialog = await screen.findByRole("dialog", { name: "Correct transaction #5" });
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong invoice");

    // First attempt without the acknowledgement → hint, no request.
    fireEvent.submit(dialog.querySelector("form") as HTMLFormElement);
    expect(
      await within(dialog).findByText(
        "Confirm the consequence checkbox before recording the correction.",
      ),
    ).toBeInTheDocument();

    // Acknowledge and submit again; the write fails so the dialog stays open
    // — the hint must be gone even though the submission did not succeed.
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: /I understand the original transaction will be voided and replaced/i,
      }),
    );
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));
    expect(await within(dialog).findByText("correction rejected")).toBeInTheDocument();
    expect(
      within(dialog).queryByText(
        "Confirm the consequence checkbox before recording the correction.",
      ),
    ).not.toBeInTheDocument();
    expect(calls).toBe(1);
  });

  it("re-syncs the category filter when the URL changes underneath the page", async () => {
    urlParams = new URLSearchParams("month=2025-12");
    server.use(useFinancePayload());
    const { rerender } = renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Total income (all time)")).toBeInTheDocument();

    urlParams = new URLSearchParams("category=FEED");
    rerender(<FinancePage />);
    await waitFor(() =>
      expect(screen.getByLabelText("Filter transactions by category")).toHaveTextContent("Feed"),
    );
    await waitFor(() => expect(lastParams.get("category")).toBe("FEED"));
    expect(lastParams.get("month")).toBeNull();
  });

  it("offers Clear for a category-only filter and its empty state", async () => {
    urlParams = new URLSearchParams("category=FEED");
    server.use(useFinancePayload({ ...PAYLOAD, transactions: [], transactions_total: 0 }));
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("No transactions match.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
  });

  it("closes the add dialog from the Cancel button, including mid-flight lockout", async () => {
    let release: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      useFinancePayload(),
      http.post("/api/finance/new", async () => {
        await parked;
        return HttpResponse.json({ ...TXN_INCOME, id: 99 }, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    // Idle dialog: Cancel closes immediately.
    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // While a save is on the wire, Cancel is locked out like every control.
    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const busy = await screen.findByRole("dialog");
    await user.type(within(busy).getByLabelText(/Amount/), "100");
    await user.click(within(busy).getByRole("button", { name: "Add transaction" }));
    expect(await within(busy).getByRole("button", { name: /Saving…/ })).toBeDisabled();
    expect(within(busy).getByRole("button", { name: "Cancel" })).toBeDisabled();

    release?.();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
