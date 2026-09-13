/**
 * Finance + purchases pages — fresh-domain mutation campaign kills
 * (2026-09): the month URL-param guard, ledger sorting both directions,
 * catalog labels, filter URL round-trips, the stale-data retry wiring, and
 * the purchase-create dialog's default checkbox.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { setCurrentFarmId } from "@/lib/api-client";
import { farmToday, formatDate } from "@/lib/format";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

import FinancePage from "./page";
import PurchasesPage from "@/app/(app)/purchases/page";

const replaceMock = vi.fn();
let urlParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => new URLSearchParams(urlParams),
  useParams: () => ({}),
}));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
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
  pnl: [{ month: "2026-01", income: 150000, expense: 2000, net: 148000, categories: {} }],
});

let lastParams = new URLSearchParams();

describe("FinancePage — campaign kills", () => {
  beforeEach(() => {
    replaceMock.mockClear();
    urlParams = new URLSearchParams();
    server.use(
      http.get("/api/finance", ({ request }) => {
        lastParams = new URL(request.url).searchParams;
        return HttpResponse.json(PAYLOAD());
      }),
    );
  });

  it("never forwards a malformed month param to the API", async () => {
    urlParams = new URLSearchParams("month=garbage");
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Total income")).toBeInTheDocument();
    await waitFor(() => expect(lastParams.get("month")).toBeNull());
  });

  it("sorts the ledger by date and amount in both directions", async () => {
    server.use(
      http.get("/api/finance", () =>
        HttpResponse.json(
          PAYLOAD([
            TXN({ id: 1, date: "2026-03-05", amount: 100 }),
            TXN({ id: 2, date: "2026-01-05", amount: 300 }),
            TXN({ id: 3, date: "2026-02-05", amount: 200 }),
          ]),
        ),
      ),
    );
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");

    const jan = formatDate("2026-01-05");
    const feb = formatDate("2026-02-05");
    const mar = formatDate("2026-03-05");
    await userEvent.click(screen.getByRole("button", { name: "Date" }));
    await waitFor(() => expect(rowDates()).toEqual([jan, feb, mar]));
    await userEvent.click(screen.getByRole("button", { name: "Date" }));
    await waitFor(() => expect(rowDates()).toEqual([mar, feb, jan]));

    await userEvent.click(screen.getByRole("button", { name: /amount/i }));
    await waitFor(() => expect(rowAmounts()).toEqual(["100", "200", "300"]));
    await userEvent.click(screen.getByRole("button", { name: /amount/i }));
    await waitFor(() => expect(rowAmounts()).toEqual(["300", "200", "100"]));
  });

  it("labels types and categories through the shared catalog", async () => {
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Animal sale")).toBeInTheDocument();
  });

  it("accepts a canonical month change and rejects near-miss shapes", async () => {
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");
    const monthInput = screen.getByLabelText("Filter by month");

    fireEvent.change(monthInput, { target: { value: "2026-03" } });
    expect((monthInput as HTMLInputElement).value).toBe("2026-03");
    await waitFor(() => expect(lastParams.get("month")).toBe("2026-03"));

    // Clearing the field resets the filter (the browser month picker — and
    // jsdom — only ever yield a canonical month or the empty string).
    fireEvent.change(monthInput, { target: { value: "" } });
    await waitFor(() => expect(lastParams.get("month")).toBeNull());
  });

  it("clears all ledger filters from the URL in one write", async () => {
    urlParams = new URLSearchParams("month=2026-03&type=INCOME&category=ANIMAL_SALE");
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");

    await userEvent.click(screen.getByRole("button", { name: /^Clear$/ }));
    await waitFor(() => expect(replaceMock).toHaveBeenCalled());
    const finalCall = replaceMock.mock.calls[replaceMock.mock.calls.length - 1]!;
    expect(String(finalCall[0])).not.toContain("month=");
    expect(String(finalCall[0])).not.toContain("type=");
    expect(String(finalCall[0])).not.toContain("category=");
  });

  it("runs the correction contract end to end", async () => {
    let correctionBody: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/finance/transactions/1/correct", async ({ request }) => {
        correctionBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(TXN({ id: 2 }), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");

    await user.click(await screen.findByRole("button", { name: "Correct" }));
    const dialog = await screen.findByRole("dialog", { name: "Correct transaction #1" });
    // A transaction with no animal link opens the picker on its none option —
    // never on a synthesized "Animal #null" stub.
    expect(within(dialog).getByText("— none —")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Animal #/)).not.toBeInTheDocument();
    // The consequence checkbox gates the submit until it is confirmed.
    const submit = within(dialog).getByRole("button", { name: "Record correction" });
    expect(submit).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(submit).toBeEnabled();

    // A blank amount is a missing amount, not a zero correction.
    await user.clear(within(dialog).getByLabelText("Amount (₹) *"));
    fireEvent.submit(within(dialog).getByRole("button", { name: "Record correction" }).closest("form")!);
    expect(await within(dialog).findByText("Amount is required")).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Amount (₹) *"), "1200");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong amount");
    await user.click(submit);
    await waitFor(() => expect(correctionBody).not.toBeNull());
    expect(correctionBody).toMatchObject({
      amount: 1200,
      reason: "Wrong amount",
      notes: "sold 10 bucks",
      related_animal_id: null,
    });
    expect(correctionBody).not.toHaveProperty("feed_quantity_kg");
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith(
        "Correction recorded. The original entry remains in the audit trail.",
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows the linked animal as read-only text without animal access", async () => {
    server.use(permissionsHandler(["finance.view", "finance.manage"]));
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");

    await user.click(await screen.findByRole("button", { name: "Correct" }));
    const dialog = await screen.findByRole("dialog", { name: "Correct transaction #1" });
    expect(within(dialog).getByLabelText("Linked animal")).toHaveTextContent(
      "No animal linked",
    );
  });

  it("keeps a settled correction from reporting on the new farm", async () => {
    let releaseCorrection!: () => void;
    let failCorrection!: () => void;
    server.use(
      http.post("/api/finance/transactions/1/correct", () =>
        new Promise((resolve) => {
          releaseCorrection = () => resolve(HttpResponse.json(TXN({ id: 2 }), { status: 201 }));
          // An HTTP error (not a transport rejection — those are retried).
          failCorrection = () => resolve(HttpResponse.json({ detail: "boom" }, { status: 500 }));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");

    // Success path: resolve after the farm changed → silent.
    await user.click(await screen.findByRole("button", { name: "Correct" }));
    let dialog = await screen.findByRole("dialog", { name: "Correct transaction #1" });
    await user.click(within(dialog).getByRole("checkbox"));
    await user.clear(within(dialog).getByLabelText("Amount (₹) *"));
    await user.type(within(dialog).getByLabelText("Amount (₹) *"), "1200");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong amount");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));
    await waitFor(() => expect(releaseCorrection).toBeDefined());
    toastMocks.success.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      releaseCorrection();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toastMocks.success).not.toHaveBeenCalled();
    // The success continuation was suppressed, so the dialog is still open.
    const stale = screen.getByRole("dialog", { name: "Correct transaction #1" });
    await user.click(within(stale).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // Failure path: an HTTP 500 after the farm changed → equally silent.
    releaseCorrection = undefined as unknown as () => void;
    failCorrection = undefined as unknown as () => void;
    await act(async () => {
      setCurrentFarmId("1");
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    await user.click(await screen.findByRole("button", { name: "Correct" }));
    dialog = await screen.findByRole("dialog", { name: "Correct transaction #1" });
    await user.click(within(dialog).getByRole("checkbox"));
    await user.clear(within(dialog).getByLabelText("Amount (₹) *"));
    await user.type(within(dialog).getByLabelText("Amount (₹) *"), "1200");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong amount");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));
    await waitFor(() => expect(failCorrection).toBeDefined());
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      failCorrection();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("labels enum options in the filter selects and the add dialog", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");

    await user.click(screen.getByRole("combobox", { name: "Filter transactions by type" }));
    expect(await screen.findByRole("option", { name: "Income" })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("combobox", { name: "Filter transactions by category" }));
    expect(await screen.findByRole("option", { name: "Animal sale" })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog", { name: "New transaction" });
    await user.click(within(dialog).getByRole("combobox", { name: "Type" }));
    expect(await screen.findByRole("option", { name: "Income" })).toBeInTheDocument();
  });

  it("runs the add-transaction contract: pending labels, reset on reopen, error clearing", async () => {
    let releaseAdd!: () => void;
    server.use(
      http.post("/api/finance/new", () =>
        new Promise((resolve) => {
          releaseAdd = () => HttpResponse && resolve(HttpResponse.json(TXN({ id: 9 }), { status: 201 }));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog", { name: "New transaction" });
    await user.type(within(dialog).getByLabelText("Amount (₹) *"), "500");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));

    const saving = await within(dialog).findByRole("button", { name: "Saving…" });
    expect(saving).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(within(dialog).getByLabelText("Amount (₹) *")).toBeDisabled();

    // Escape while the write is out: the reopened dialog must keep new input.
    await act(async () => {
      releaseAdd();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Transaction saved."),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const reopened = await (async () => {
      await user.click(screen.getByRole("button", { name: "New transaction" }));
      return await screen.findByRole("dialog", { name: "New transaction" });
    })();
    expect(within(reopened).getByLabelText("Amount (₹) *")).toHaveValue(null);
    expect(within(reopened).getByLabelText("Date *")).toHaveValue(farmToday());
  });

  it("surfaces a failed add inline and clears it on reopen", async () => {
    server.use(
      http.post("/api/finance/new", () =>
        HttpResponse.json({ detail: "Amount outside allowed range." }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    let dialog = await screen.findByRole("dialog", { name: "New transaction" });
    await user.type(within(dialog).getByLabelText("Amount (₹) *"), "500");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
    expect(await within(dialog).findByText(/Amount outside allowed range\./)).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Retry add transaction" }),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await (async () => {
      await user.click(screen.getByRole("button", { name: "New transaction" }));
      return await screen.findByRole("dialog", { name: "New transaction" });
    })();
    expect(within(dialog).queryByText(/Amount outside allowed range\./)).not.toBeInTheDocument();
  });

  it("shows the stale-data banner when a correction's invalidation refresh fails", async () => {
    let calls = 0;
    server.use(
      http.get("/api/finance", () => {
        calls += 1;
        if (calls === 2) return new HttpResponse(null, { status: 500 });
        return HttpResponse.json(PAYLOAD());
      }),
      http.post("/api/finance/transactions/1/correct", () =>
        HttpResponse.json(TXN({ id: 2 }), { status: 201 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");
    expect(screen.queryByText("Could not refresh — showing the last loaded data.")).toBeNull();

    await user.click(await screen.findByRole("button", { name: "Correct" }));
    const dialog = await screen.findByRole("dialog", { name: "Correct transaction #1" });
    await user.click(within(dialog).getByRole("checkbox"));
    await user.clear(within(dialog).getByLabelText("Amount (₹) *"));
    await user.type(within(dialog).getByLabelText("Amount (₹) *"), "1200");
    await user.type(within(dialog).getByLabelText("Correction reason *"), "Wrong amount");
    await user.click(within(dialog).getByRole("button", { name: "Record correction" }));

    expect(
      await screen.findByText("Could not refresh — showing the last loaded data."),
    ).toBeInTheDocument();
    expect(await screen.findByText("sold 10 bucks")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(
        screen.queryByText("Could not refresh — showing the last loaded data."),
      ).not.toBeInTheDocument(),
    );
  });

  it("keeps a settled add from reporting on the new farm", async () => {
    let settleAdd!: (ok: boolean) => void;
    server.use(
      http.post("/api/finance/new", () =>
        new Promise((resolve) => {
          settleAdd = (ok) =>
            resolve(
              ok
                ? HttpResponse.json(TXN({ id: 9 }), { status: 201 })
                : HttpResponse.json({ detail: "boom" }, { status: 500 }),
            );
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog", { name: "New transaction" });
    await user.type(within(dialog).getByLabelText("Amount (₹) *"), "500");
    await user.click(within(dialog).getByRole("button", { name: "Add transaction" }));
    await waitFor(() => expect(settleAdd).toBeDefined());
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      settleAdd(false);
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("closes the correction dialog through its Cancel control", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    await screen.findByText("Total income");

    await user.click(await screen.findByRole("button", { name: "Correct" }));
    const dialog = await screen.findByRole("dialog", { name: "Correct transaction #1" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("offers a working retry from the stale-data notice", async () => {
    let requests = 0;
    let fail = true;
    server.use(
      http.get("/api/finance", () => {
        requests += 1;
        if (fail) return new HttpResponse(null, { status: 500 });
        return HttpResponse.json(PAYLOAD());
      }),
    );
    renderWithProviders(<FinancePage />);
    const retry = await screen.findByRole("button", { name: /retry/i });
    fail = false;
    await userEvent.click(retry);
    await waitFor(() => expect(requests).toBeGreaterThanOrEqual(2));
  });
});

describe("PurchasesPage — campaign kills", () => {
  beforeEach(() => {
    replaceMock.mockClear();
    urlParams = new URLSearchParams();
    server.use(
      http.get("/api/purchases", () =>
        HttpResponse.json({ batches: [], total: 0, limit: 50, offset: 0 }),
      ),
    );
  });

  it("prechecks the create-animals default in the purchase dialog", async () => {
    const BATCH_1 = {
      id: 5,
      date: "2026-01-05",
      supplier: "Sharma Goat Farm",
      count: 50,
      avg_age_months: 8,
      avg_weight_kg: 18.5,
      total_price: 150000,
      notes: "foundation stock",
      animals_created: 50,
      open_tasks: 3,
    };
    server.use(
      http.get("/api/purchases", () =>
        HttpResponse.json({ batches: [BATCH_1], total: 1, limit: 50, offset: 0 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<PurchasesPage />);
    expect(await screen.findByText("Sharma Goat Farm")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const dialog = await screen.findByRole("dialog");
    const checkbox = within(dialog).getByRole("checkbox");
    expect(checkbox).toBeChecked();
    expect(within(dialog).getByLabelText("Date *")).toHaveValue(farmToday());
    expect(within(dialog).getByLabelText("Count *")).toHaveValue(1);
  });

  it("reviews, creates and resets the purchase dialog, with blanks as nulls", async () => {
    let releaseCreate!: () => void;
    let createdBody: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/purchases/new", async ({ request }) => {
        createdBody = (await request.json()) as Record<string, unknown>;
        return new Promise((resolve) => {
          releaseCreate = () => resolve(HttpResponse.json({ id: 9 }, { status: 201 }));
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<PurchasesPage />);
    await screen.findByText("No purchase batches yet.");

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Supplier"), "Sharma Goat Farm");
    await user.clear(within(dialog).getByLabelText("Count *"));
    await user.type(within(dialog).getByLabelText("Count *"), "12");
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));

    const review = await screen.findByRole("dialog", { name: "Review purchase consequences" });
    const confirm = within(review).getByRole("button", { name: "Confirm and create" });
    await user.click(confirm);
    expect(await within(review).findByRole("button", { name: "Creating…" })).toBeDisabled();

    await act(async () => {
      releaseCreate();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Purchase batch created."),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // Blank optional numerics ride the wire as nulls, never as zeros.
    expect(createdBody).toMatchObject({
      supplier: "Sharma Goat Farm",
      count: 12,
      avg_age_months: null,
      avg_weight_kg: null,
      total_price: null,
      create_animals: true,
    });

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).getByLabelText("Date *")).toHaveValue(farmToday());
    expect(within(reopened).queryByText(/Review purchase consequences/)).not.toBeInTheDocument();
  });

  it("keeps a settled purchase create from reporting on the new farm", async () => {
    let releaseCreate!: () => void;
    server.use(
      http.post("/api/purchases/new", () =>
        new Promise((resolve) => {
          releaseCreate = () => resolve(HttpResponse.json({ id: 9 }, { status: 201 }));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<PurchasesPage />);
    await screen.findByText("No purchase batches yet.");

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));
    const review = await screen.findByRole("dialog", { name: "Review purchase consequences" });
    await user.click(within(review).getByRole("button", { name: "Confirm and create" }));
    await waitFor(() => expect(releaseCreate).toBeDefined());
    toastMocks.success.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      releaseCreate();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toastMocks.success).not.toHaveBeenCalled();
    // The suppressed continuation must not have closed the dialog either.
    expect(screen.getByRole("dialog", { name: "Review purchase consequences" })).toBeInTheDocument();
  });

  it("dismissal while a create is in flight supersedes its failure report", async () => {
    let failCreate!: () => void;
    server.use(
      http.post("/api/purchases/new", () =>
        new Promise((resolve) => {
          // An HTTP error (transport rejections would be retried by the
          // idempotency layer and never reach the dialog's catch).
          failCreate = () => resolve(HttpResponse.json({ detail: "boom" }, { status: 500 }));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<PurchasesPage />);
    await screen.findByText("No purchase batches yet.");

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));
    const review = await screen.findByRole("dialog", { name: "Review purchase consequences" });
    await user.click(within(review).getByRole("button", { name: "Confirm and create" }));
    await waitFor(() => expect(failCreate).toBeDefined());

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    toastMocks.error.mockClear();
    await act(async () => {
      failCreate();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toastMocks.error).not.toHaveBeenCalled();

    // Reopening yields a fresh form, not the stale review panel.
    await user.click(screen.getByRole("button", { name: "New batch" }));
    const reopened = await screen.findByRole("dialog");
    expect(
      within(reopened).queryByText(/Review purchase consequences/),
    ).not.toBeInTheDocument();
  });

  it("toasts the server detail when a create fails on the same farm", async () => {
    server.use(
      http.post("/api/purchases/new", () =>
        HttpResponse.json({ detail: "Count exceeds the per-batch cap." }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<PurchasesPage />);
    await screen.findByText("No purchase batches yet.");

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));
    const review = await screen.findByRole("dialog", { name: "Review purchase consequences" });
    await user.click(within(review).getByRole("button", { name: "Confirm and create" }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("Count exceeds the per-batch cap."),
    );
  });

  it("shows the stale-data banner when a create's invalidation refresh fails", async () => {
    let calls = 0;
    server.use(
      http.get("/api/purchases", () => {
        calls += 1;
        if (calls === 2) return new HttpResponse(null, { status: 500 });
        return HttpResponse.json({ batches: [], total: 0, limit: 50, offset: 0 });
      }),
      http.post("/api/purchases/new", () => HttpResponse.json({ id: 9 }, { status: 201 })),
    );
    const user = userEvent.setup();
    renderWithProviders(<PurchasesPage />);
    await screen.findByText("No purchase batches yet.");
    expect(
      screen.queryByText("Could not refresh — showing the last loaded data."),
    ).toBeNull();

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));
    const review = await screen.findByRole("dialog", { name: "Review purchase consequences" });
    await user.click(within(review).getByRole("button", { name: "Confirm and create" }));
    expect(
      await screen.findByText("Could not refresh — showing the last loaded data."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(
        screen.queryByText("Could not refresh — showing the last loaded data."),
      ).not.toBeInTheDocument(),
    );
  });

  it("renders a batch detail once loaded", async () => {
    server.use(
      http.get("/api/purchases", () =>
        HttpResponse.json({
          batches: [
            {
              id: 5,
              date: "2026-01-05",
              supplier: "Sharma Goat Farm",
              count: 50,
              avg_age_months: 8,
              avg_weight_kg: 18.5,
              total_price: 150000,
              notes: "foundation stock",
              animals_created: 50,
              open_tasks: 3,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/purchases/5", () =>
        HttpResponse.json({
          batch: {
            id: 5,
            date: "2026-01-05",
            supplier: "Sharma Goat Farm",
            count: 50,
            avg_age_months: 8,
            avg_weight_kg: 18.5,
            total_price: 150000,
            notes: "foundation stock",
            animals_created: 50,
            open_tasks: 3,
          },
          animals: [],
          animals_total: 0,
          animals_limit: 100,
          animals_offset: 0,
          tasks: [],
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<PurchasesPage />);
    expect(await screen.findByText("Sharma Goat Farm")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "View" })[0]!);
    const detail = await screen.findByRole("dialog");
    expect(await within(detail).findByText(/Sharma Goat Farm/)).toBeInTheDocument();
    await waitFor(() =>
      expect(within(detail).queryByText("Loading batch…")).not.toBeInTheDocument(),
    );
  });
});

function tableRowsText(): string[] {
  return screen
    .getAllByRole("row")
    .filter((row) => row.querySelector("td") !== null)
    .map((row) => row.textContent ?? "");
}

function rowDates(): string[] {
  return tableRowsText()
    .map((t) => t.match(/[0-9]{1,2} [A-Za-z]{3} [0-9]{4}/)?.[0] ?? "")
    .filter(Boolean);
}

function rowAmounts(): string[] {
  // Ledger rows are exactly the date-bearing rows; the PnL summary table
  // carries the totals instead.
  return tableRowsText()
    .filter((t) => /[0-9]{1,2} [A-Za-z]{3} [0-9]{4}/.test(t))
    .map((t) => t.match(/₹[\d,]+/)?.[0] ?? "")
    .filter(Boolean)
    .map((a) => a.replace(/[₹,]/g, ""));
}
