// SUSPECTED APP BUG — reported to orchestrator
//
// Finance page sends filter params as the literal string "null" when no
// filter is active. `src/app/(app)/finance/page.tsx` builds
//   params = { month: month || null, type: ... ? null : ..., category: ... }
// and the Orval URL builder (src/api/generated/endpoints.ts,
// getListTransactionsApiFinanceGetUrl) serializes `null` as 'null', so the
// default page load requests `/api/finance?month=null&type=null&category=null`.
// Backend `backend/app/api/finance.py` treats a truthy `month` that fails
// `strptime("%Y-%m")` as "match nothing" and compares `type`/`category`
// verbatim — so the transactions table renders "No transactions match." on
// every load (and after Clear), even when the farm has transactions.
// Totals and P&L are computed unfiltered server-side, which masks the bug:
// cards look right while the table is always empty.
//
// Expected: with no active filter the GET omits month/type/category entirely
// (searchParams.get(...) === null). Actual: each is the string "null".

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FinancePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const PAYLOAD = {
  transactions: [],
  total_income: 0,
  total_expense: 0,
  pnl: [{ month: "2026-01", income: 100, expense: 0, net: 100, categories: {} }],
};

describe("FinancePage filter params must be omitted, not the string 'null'", () => {
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

  it("the default unfiltered load sends no month/type/category params", async () => {
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Monthly P&L (last 12 months)")).toBeInTheDocument();

    expect(lastParams.get("month")).toBeNull();
    expect(lastParams.get("type")).toBeNull();
    expect(lastParams.get("category")).toBeNull();
  });

  it("Clear removes the filter params from the request", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Monthly P&L (last 12 months)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "2026-01" }));
    await waitFor(() => expect(lastParams.get("month")).toBe("2026-01"));

    await user.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(lastParams.get("month")).toBeNull());
  });
});
