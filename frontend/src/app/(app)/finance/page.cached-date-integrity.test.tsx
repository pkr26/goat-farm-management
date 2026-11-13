import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";

import type { listTransactionsApiFinanceGetResponse } from "@/api/generated/endpoints";
import type { TransactionOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import FinancePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));

it.each([
  { date: "1", valid: false },
  { date: "2026-02-30", valid: false },
  { date: "0000-01-01", valid: false },
  { date: "", valid: false },
  { date: "2024-02-29", valid: true },
  { date: "0001-01-01", valid: true },
])("validates the actual cached correction date $date before sending a ledger write", async ({ date, valid }) => {
  const transaction: TransactionOut = {
    id: 41, date: "2026-01-05", type: "EXPENSE", category: "OTHER", amount: 120,
    notes: "Cached calendar correction", related_animal_id: null, animal_tag: null,
    created_at: "2026-01-05T05:30:00Z", source_type: null, source_id: null,
    correction_of_id: null, voided_at: null, voided_by_id: null, void_reason: null,
  };
  const corrections: Record<string, unknown>[] = [];
  server.use(
    http.get("/api/finance", () => HttpResponse.json({ transactions: [transaction],
      transactions_total: 1, limit: 50, offset: 0, total_income: 0, total_expense: 120,
      feed_stock_value: 0, pnl: [] })),
    http.post("/api/finance/transactions/:id/correct", async ({ request }) => {
      const body = await request.json() as Record<string, unknown>; corrections.push(body);
      // These malformed fixture dates are rejected by the actual Pydantic
      // correction input. Model that public server response, while observing
      // whether the form prevents the bad request in the first place.
      if (["1", "2026-02-30", "0000-01-01", ""].includes(String(body.date))) {
        return HttpResponse.json({ detail: "Invalid ledger calendar date" }, { status: 422 });
      }
      return HttpResponse.json({ ...transaction, ...body });
    }),
  );
  const view = renderWithProviders(<FinancePage />);
  try {
    await view.waitForAuthIdle();
    await screen.findAllByText(transaction.notes!);
    const query = view.queryClient.getQueryCache().getAll().find(candidate => candidate.queryKey.includes("/api/finance"));
    expect(query).toBeDefined();
    const previous = query!.state.data as listTransactionsApiFinanceGetResponse;
    expect(previous.status).toBe(200);
    if (previous.status !== 200) throw new Error("Healthy generated ledger response required");
    // A malformed in-memory cache date is a public typed string, while the
    // initial real generated request has a valid backend format:date value.
    // No claim that the ordinary backend emits malformed date JSON.
    act(() => view.queryClient.setQueryData(query!.queryKey, {
      ...previous, data: { ...previous.data, transactions: [{ ...transaction, date }] },
    } satisfies listTransactionsApiFinanceGetResponse));
    const row = (await screen.findAllByText(transaction.notes!)).find(node => node.closest("tr"))!.closest("tr")!;
    act(() => within(row).getByRole("button", { name: "Correct" }).click());
    const dialog = await screen.findByRole("dialog", { name: "Correct transaction #41" });
    fireEvent.change(within(dialog).getByLabelText("Correction reason *"), { target: { value: "Corrected calendar record" } });
    act(() => within(dialog).getByRole("checkbox", { name: /I understand the original transaction will be voided and replaced/ }).click());
    const dateInput = within(dialog).getByLabelText("Date *");
    act(() => (within(dialog).getByRole("button", { name: "Record correction" }).closest("form") as HTMLFormElement).requestSubmit());
    if (valid) {
      await waitFor(() => expect(corrections).toHaveLength(1));
      expect(corrections[0].date).toBe(date);
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "Correct transaction #41" })).toBeNull());
    } else {
      await waitFor(() => expect(dateInput).toHaveAttribute("aria-invalid", "true"));
      expect(within(dialog).getByRole("alert")).toHaveTextContent(/date/i);
      expect(corrections).toHaveLength(0);
      fireEvent.change(dateInput, { target: { value: "2024-02-29" } });
      act(() => (within(dialog).getByRole("button", { name: "Record correction" }).closest("form") as HTMLFormElement).requestSubmit());
      await waitFor(() => expect(corrections).toHaveLength(1));
      expect(corrections[0].date).toBe("2024-02-29");
    }
  } finally { view.unmount(); view.queryClient.clear(); }
});
