import { fireEvent, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, it, vi } from "vitest";

import type { FinanceOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import FinancePage from "./page";

const { navigation } = vi.hoisted(() => ({ navigation: { search: "", router: { push: vi.fn(), replace: vi.fn() } } }));
vi.mock("next/navigation", () => ({
  useRouter: () => navigation.router, usePathname: () => "/finance",
  useSearchParams: () => new URLSearchParams(navigation.search), useParams: () => ({}),
}));
beforeEach(() => { navigation.search = ""; navigation.router.replace.mockClear(); });
const payload: FinanceOut = { transactions: [], transactions_total: 0, limit: 50, offset: 0,
  total_income: 0, total_expense: 0, feed_stock_value: 0, mortality_loss: null, pnl: [] };
function observeRequests() {
  const months: (string | null)[] = [];
  server.use(http.get("/api/finance", ({ request }) => {
    months.push(new URL(request.url).searchParams.get("month")); return HttpResponse.json(payload);
  }));
  return months;
}

it.each(["0000-01", "0000-12", "9999-12"])("ignores the unrepresentable calendar month %s in a shared link", async (month) => {
  navigation.search = `month=${month}`;
  const months = observeRequests(); const view = renderWithProviders(<FinancePage />);
  try {
    await screen.findByText("Total income (all time)");
    expect(months).toEqual([null]); expect(screen.getByLabelText("Filter by month")).toHaveValue("");
  } finally { view.unmount(); view.queryClient.clear(); }
});

it.each(["0001-01", "0099-02", "2026-10", "9999-11"])("keeps the representable calendar month %s in a shared link", async (month) => {
  navigation.search = `month=${month}`;
  const months = observeRequests(); const view = renderWithProviders(<FinancePage />);
  try {
    await screen.findByText("Total income (all time)");
    expect(months).toEqual([month]); expect(screen.getByLabelText("Filter by month")).toHaveValue(month);
  } finally { view.unmount(); view.queryClient.clear(); }
});

it("ignores the unrepresentable upper month from the native month input", async () => {
  const months = observeRequests(); const view = renderWithProviders(<FinancePage />);
  try {
    await screen.findByText("Total income (all time)");
    fireEvent.change(screen.getByLabelText("Filter by month"), { target: { value: "9999-12" } });
    expect(screen.getByLabelText("Filter by month")).toHaveValue("");
    expect(navigation.router.replace).not.toHaveBeenCalled(); expect(months).toEqual([null]);
  } finally { view.unmount(); view.queryClient.clear(); }
});

it("drops an unrepresentable filter after a committed shared navigation", async () => {
  navigation.search = "month=2026-10";
  const months = observeRequests(); const view = renderWithProviders(<FinancePage />);
  try {
    await screen.findByText("Total income (all time)");
    navigation.search = "month=0000-01"; view.rerender(<FinancePage />);
    await waitFor(() => expect(months).toEqual(["2026-10", null]));
    expect(screen.getByLabelText("Filter by month")).toHaveValue("");
  } finally { view.unmount(); view.queryClient.clear(); }
});

it.each(["0000-01", "9999-12"])("cannot activate an unrepresentable %s month from a public P&L snapshot", async (month) => {
  const months = observeRequests();
  server.use(http.get("/api/finance", ({ request }) => {
    months.push(new URL(request.url).searchParams.get("month"));
    return HttpResponse.json({ ...payload, pnl: [{ month, income: 1, expense: 0, net: 1, categories: {} }] } satisfies FinanceOut);
  }));
  const view = renderWithProviders(<FinancePage />);
  try {
    await screen.findByText("Total income (all time)");
    const shortcuts = screen.getAllByRole("button", { name: month }); expect(shortcuts).toHaveLength(2);
    for (const shortcut of shortcuts) { expect(shortcut).toBeDisabled(); fireEvent.click(shortcut); }
    expect(months).toEqual([null]); expect(navigation.router.replace).not.toHaveBeenCalled();
  } finally { view.unmount(); view.queryClient.clear(); }
});
