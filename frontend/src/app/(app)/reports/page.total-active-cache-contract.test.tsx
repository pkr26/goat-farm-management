import { act, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { Component, type PropsWithChildren } from "react";
import { expect, it, vi } from "vitest";
import { getReportsApiDashboardReportsGetQueryKey, type ReportsApiDashboardReportsGetQueryResult } from "@/api/generated/endpoints";
import type { ReportsOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import ReportsPage from "./page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }), usePathname: () => "/reports", useSearchParams: () => new URLSearchParams(), useParams: () => ({}) }));
class ReportsBoundary extends Component<PropsWithChildren, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="alert">Report summary failed.</p> : this.props.children; }
}
const payload: ReportsOut = {
  bucket_rows: [], total_active: 10, sex_counts: { F: 8, M: 2 }, status_counts: {},
  breeding: { total_records: 5, conception_rate: 60, first_cycle_rate: 40, kiddings: 3, kids_per_kidding: 1.5, twin_rate: 25, cull_candidates: [], cull_candidates_total: 0, cull_candidates_limit: 20 },
  mortality: { total_deaths: 0, deaths_by_month: [], total_kids_born: 4, stillborn: 0, stillborn_rate: 0 },
};
it.each([
  { kind: "NaN", value: NaN, title: "Herd summary (— active)" },
  { kind: "positive infinity", value: Infinity, title: "Herd summary (— active)" },
  { kind: "negative infinity", value: -Infinity, title: "Herd summary (— active)" },
  { kind: "known zero", value: 0, title: "Herd summary (0 active)" },
  { kind: "withheld total", value: null, title: "Herd summary" },
])("renders the public summary correctly for a $kind cached active total", async ({ value, title }) => {
  server.use(http.get("/api/dashboard/reports", () => HttpResponse.json(payload)));
  const view = renderWithProviders(<ReportsBoundary><ReportsPage /></ReportsBoundary>);
  await screen.findByText("Herd summary (10 active)"); await view.waitForAuthIdle();
  const key = getReportsApiDashboardReportsGetQueryKey();
  const response = view.queryClient.getQueryData<ReportsApiDashboardReportsGetQueryResult>(key);
  expect(response?.status).toBe(200);
  // The generated SDK cache permits plain JS numbers, including nonfinite
  // values. JSON transport cannot supply these values; this is cache ingress.
  const cached: ReportsOut = { ...payload, total_active: value };
  act(() => view.queryClient.setQueryData<ReportsApiDashboardReportsGetQueryResult>(key, { ...response!, status: 200, data: cached }));
  await waitFor(() => expect(screen.getByText(title, { exact: true })).toBeInTheDocument());
  expect(screen.queryByText("Report summary failed.")).not.toBeInTheDocument();
  if (value !== null && !Number.isFinite(value)) expect(screen.queryByText("Herd summary (0 active)")).not.toBeInTheDocument();
});
