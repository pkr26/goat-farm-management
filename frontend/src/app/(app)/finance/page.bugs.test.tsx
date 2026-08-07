// REGRESSION TESTS — bug fixed; these tests pin the fix.
//
// The finance page used to send filter params as the literal string "null"
// when no filter was active: `src/app/(app)/finance/page.tsx` built
//   params = { month: month || null, type: ... ? null : ..., category: ... }
// and the Orval URL builder serialized `null` as 'null', so the default page
// load requested `/api/finance?month=null&type=null&category=null`.
// The backend treated a truthy `month` that fails strptime("%Y-%m") as
// "match nothing" — so the transactions table rendered "No transactions
// match." on every load even when the farm had transactions, while totals
// and P&L (computed unfiltered server-side) masked it.
//
// With no active filter the GET now omits month/type/category entirely
// (searchParams.get(...) === null); these tests pin that.

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
