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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FinancePage from "./page";

/** Router replace spy and per-test URL, so one suite can exercise both the
 *  empty default params and a shared, filter-carrying link. */
const replaceMock = vi.fn();
let urlParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => urlParams,
  useParams: () => ({}),
}));

beforeEach(() => {
  urlParams = new URLSearchParams();
});

const PAYLOAD = {
  transactions: [],
  transactions_total: 0,
  limit: 50,
  offset: 0,
  total_income: 0,
  total_expense: 0,
  feed_stock_value: 12000,
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

  it("seeds the filters from the URL and mirrors every change back into it", async () => {
    urlParams = new URLSearchParams("month=2025-12&type=INCOME&category=FEED");
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Monthly P&L (last 12 months)")).toBeInTheDocument();

    // The shared link's view is the first request the ledger makes.
    await waitFor(() => {
      expect(lastParams.get("month")).toBe("2025-12");
      expect(lastParams.get("type")).toBe("INCOME");
      expect(lastParams.get("category")).toBe("FEED");
    });
    expect(screen.getByLabelText("Filter by month")).toHaveValue("2025-12");
    expect(screen.getByLabelText("Filter transactions by type")).toHaveTextContent("Income");
    expect(screen.getByLabelText("Filter transactions by category")).toHaveTextContent("Feed");

    await user.click(screen.getByRole("button", { name: "Clear" }));
    // Clear rewrites the URL without a history entry or a scroll, then drops
    // the params from the next request.
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/finance", { scroll: false }),
    );
    await waitFor(() => {
      expect(lastParams.get("month")).toBeNull();
      expect(lastParams.get("type")).toBeNull();
      expect(lastParams.get("category")).toBeNull();
    });
  });
});

// REGRESSION TESTS — bug fixed; these tests pin the fix.
//
// `useForm({ defaultValues: { date: localToday(), … } })` evaluates the date
// once, when the page mounts, and react-hook-form's bare `reset()` restores
// that mount-time snapshot. A shed tablet left on /finance across the farm's
// midnight therefore pre-filled yesterday's date, booking the transaction to
// the wrong day (and, at a month boundary, the wrong month of the P&L) while
// the input's `max` attribute already showed the new day. Both reset call
// sites now pass freshly computed defaults.

// REGRESSION TESTS — bug fixed; these tests pin the fix.
//
// Both ledger filter Selects used the ALL = "all" sentinel with a human label
// on the item but passed no root `items` map, and Base UI's Select.Value
// falls back to the raw value — so the default (unfiltered) page showed two
// dropdowns both reading "all".

describe("FinancePage filter triggers show labels, not the 'all' sentinel", () => {
  beforeEach(() => {
    server.use(http.get("/api/finance", () => HttpResponse.json(PAYLOAD)));
  });

  it("renders 'All types' and 'All categories' in the closed triggers", async () => {
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Monthly P&L (last 12 months)")).toBeInTheDocument();

    expect(screen.getByLabelText("Filter transactions by type")).toHaveTextContent(
      "All types",
    );
    expect(screen.getByLabelText("Filter transactions by category")).toHaveTextContent(
      "All categories",
    );
  });
});

describe("FinancePage date default survives a farm-midnight rollover", () => {
  beforeEach(() => {
    server.use(http.get("/api/finance", () => HttpResponse.json(PAYLOAD)));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("prefills the farm's current date, not the date the tab was opened", async () => {
    // 18:00 IST on 2026-08-09 — the tab is opened.
    vi.setSystemTime(new Date("2026-08-09T12:30:00Z"));
    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Monthly P&L (last 12 months)")).toBeInTheDocument();

    // 18:00 IST the next day — the operator finally records the expense.
    vi.setSystemTime(new Date("2026-08-10T12:30:00Z"));
    await user.click(screen.getByRole("button", { name: "New transaction" }));

    const dateInput = await screen.findByLabelText("Date *");
    expect(dateInput).toHaveValue("2026-08-10");
    expect(dateInput).toHaveAttribute("max", "2026-08-10");
  });
});
