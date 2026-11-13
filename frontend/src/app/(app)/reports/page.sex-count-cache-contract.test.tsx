import { act, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import {
  getReportsApiDashboardReportsGetQueryKey,
  type ReportsApiDashboardReportsGetQueryResult,
} from "@/api/generated/endpoints";
import type { ReportsOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import ReportsPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/reports",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const payload: ReportsOut = {
  bucket_rows: [], total_active: 10, sex_counts: { F: 8, M: 2 }, status_counts: {},
  breeding: {
    total_records: 5, conception_rate: 60, first_cycle_rate: 40, kiddings: 3,
    kids_per_kidding: 1.5, twin_rate: 25, cull_candidates: [],
    cull_candidates_total: 0, cull_candidates_limit: 20,
  },
  mortality: {
    total_deaths: 0, deaths_by_month: [], total_kids_born: 4,
    stillborn: 0, stillborn_rate: 0,
  },
};

const labels = { F: "Females (active)", M: "Males (active)" } as const;
function countCell(field: "F" | "M") {
  const row = screen.getByText(labels[field]).closest("tr");
  expect(row).not.toBeNull();
  return within(row!).getAllByRole("cell")[1];
}

async function loadReports() {
  server.use(http.get("/api/dashboard/reports", () => HttpResponse.json(payload)));
  const view = renderWithProviders(<ReportsPage />);
  await screen.findByText("Herd summary (10 active)");
  await view.waitForAuthIdle();
  const key = getReportsApiDashboardReportsGetQueryKey();
  const response = view.queryClient.getQueryData<ReportsApiDashboardReportsGetQueryResult>(key);
  expect(response?.status).toBe(200);
  return { ...view, key, response: response! };
}

const nonfiniteCases = (["F", "M"] as const).flatMap((field) => [
  { field, kind: "NaN", value: NaN },
  { field, kind: "positive infinity", value: Infinity },
  { field, kind: "negative infinity", value: -Infinity },
]);

it.each(nonfiniteCases)("shows $field as unavailable for a $kind cached count", async ({ field, value }) => {
  const view = await loadReports();
  // A typed query-cache update can carry non-finite JS numbers even though
  // JSON transport cannot. Keep this plain generated-model cache ingress
  // distinct from a malformed HTTP response or a mocked private renderer.
  const cached: ReportsOut = { ...payload, sex_counts: { ...payload.sex_counts, [field]: value } };
  act(() => view.queryClient.setQueryData<ReportsApiDashboardReportsGetQueryResult>(
    view.key, { ...view.response, status: 200, data: cached },
  ));
  await waitFor(() => expect(countCell(field)).toHaveTextContent(/^—$/));
  expect(countCell(field)).not.toHaveTextContent(/^0$/);
  const other = field === "F" ? "M" : "F";
  expect(countCell(other)).toHaveTextContent(other === "F" ? /^8$/ : /^2$/);
});

const knownCountCases: { kind: string; counts: Record<string, number> }[] = [
  { kind: "known zero", counts: { F: 0, M: 0 } },
  { kind: "missing sex entries", counts: {} },
];
it.each(knownCountCases)("keeps $kind at zero in the active-sex summary", async ({ counts }) => {
  const view = await loadReports();
  const cached: ReportsOut = { ...payload, sex_counts: counts };
  act(() => view.queryClient.setQueryData<ReportsApiDashboardReportsGetQueryResult>(
    view.key, { ...view.response, status: 200, data: cached },
  ));
  await waitFor(() => {
    expect(countCell("F")).toHaveTextContent(/^0$/);
    expect(countCell("M")).toHaveTextContent(/^0$/);
  });
});
