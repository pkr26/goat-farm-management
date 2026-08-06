/**
 * Reports page: herd summary (bucket rows, sex/status counts), breeding
 * performance (percentages vs — for null, cull candidate links), mortality
 * card (monthly table + empty state), error state and RBAC gating.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import ReportsPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/reports",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const CULL_ANIMAL = {
  id: 21,
  tag_number: "G-021",
  name: "Kali",
  breed: "Osmanabadi",
  sex: "F",
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
  cull_candidate: true,
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
};

interface ReportsPayload {
  bucket_rows: { name: string; code: string; count: number; avg_weight: number | null }[];
  total_active: number;
  sex_counts: Record<string, number>;
  status_counts: Record<string, number>;
  breeding: {
    total_records: number;
    conception_rate: number | null;
    first_cycle_rate: number | null;
    kiddings: number;
    kids_per_kidding: number | null;
    twin_rate: number | null;
    cull_candidates: unknown[];
  };
  mortality: {
    total_deaths: number;
    deaths_by_month: [string, number][];
    total_kids_born: number;
    stillborn: number;
    stillborn_rate: number | null;
  };
}

const PAYLOAD: ReportsPayload = {
  bucket_rows: [
    { name: "Breeding", code: "BREEDING", count: 20, avg_weight: 32.45 },
    { name: "Quarantine Ward", code: "QUARANTINE", count: 5, avg_weight: null },
  ],
  total_active: 47,
  sex_counts: { F: 30, M: 17 },
  status_counts: { SOLD: 8, DEAD: 3 },
  breeding: {
    total_records: 24,
    conception_rate: 66,
    first_cycle_rate: 50,
    kiddings: 12,
    kids_per_kidding: 1.8,
    twin_rate: 33,
    cull_candidates: [CULL_ANIMAL],
  },
  mortality: {
    total_deaths: 3,
    deaths_by_month: [
      ["2026-01", 2],
      ["2025-12", 1],
    ],
    total_kids_born: 40,
    stillborn: 4,
    stillborn_rate: 10,
  },
};

function summaryValue(label: string): HTMLElement {
  const row = screen.getByText(label).closest("tr");
  expect(row).not.toBeNull();
  return within(row as HTMLElement).getAllByRole("cell")[1];
}

async function renderLoaded(payload: ReportsPayload = PAYLOAD) {
  server.use(http.get("/api/dashboard/reports", () => HttpResponse.json(payload)));
  renderWithProviders(<ReportsPage />);
  expect(await screen.findByText(/Herd summary/)).toBeInTheDocument();
}

describe("ReportsPage", () => {
  it("renders the herd summary title with the active count", async () => {
    await renderLoaded();
    expect(screen.getByText("Herd summary (47 active)")).toBeInTheDocument();
  });

  it("renders bucket rows with headcounts and average weights", async () => {
    await renderLoaded();

    const breeding = screen.getByText("Breeding").closest("tr") as HTMLElement;
    expect(within(breeding).getByText("BREEDING")).toBeInTheDocument();
    expect(within(breeding).getByText("20")).toBeInTheDocument();
    // avg_weight is rendered with toFixed(1).
    expect(within(breeding).getByText("32.5 kg")).toBeInTheDocument();

    const quarantine = screen.getByText("Quarantine Ward").closest("tr") as HTMLElement;
    expect(within(quarantine).getByText("—")).toBeInTheDocument();
  });

  it("renders sex and status counts", async () => {
    await renderLoaded();

    expect(summaryValue("Females (active)")).toHaveTextContent("30");
    expect(summaryValue("Males (active)")).toHaveTextContent("17");
    expect(summaryValue("SOLD (all time)")).toHaveTextContent("8");
    expect(summaryValue("DEAD (all time)")).toHaveTextContent("3");
  });

  it("renders breeding performance percentages and counts", async () => {
    await renderLoaded();

    expect(summaryValue("Breeding records")).toHaveTextContent("24");
    expect(summaryValue("Conception rate (confirmed / completed)")).toHaveTextContent("66%");
    expect(summaryValue("First-cycle success")).toHaveTextContent("50%");
    expect(summaryValue("Kiddings recorded")).toHaveTextContent("12");
    expect(summaryValue("Alive kids per kidding")).toHaveTextContent("1.8");
    expect(summaryValue("Twin rate (≥2 kids)")).toHaveTextContent("33%");
  });

  it("links each cull candidate to its animal page with tag · name", async () => {
    await renderLoaded();

    const row = screen.getByText("Cull candidates").closest("tr") as HTMLElement;
    expect(within(row).getByText("1")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "G-021 · Kali" })).toHaveAttribute(
      "href",
      "/animals/21",
    );
  });

  it("shows the cull count with no links when there are no candidates", async () => {
    const payload = structuredClone(PAYLOAD);
    payload.breeding.cull_candidates = [];
    await renderLoaded(payload);

    const row = screen.getByText("Cull candidates").closest("tr") as HTMLElement;
    expect(within(row).getByText("0")).toBeInTheDocument();
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders — for null breeding percentages", async () => {
    const payload = structuredClone(PAYLOAD);
    payload.breeding.conception_rate = null;
    payload.breeding.first_cycle_rate = null;
    payload.breeding.twin_rate = null;
    payload.breeding.kids_per_kidding = null;
    await renderLoaded(payload);

    expect(summaryValue("Conception rate (confirmed / completed)")).toHaveTextContent("—");
    expect(summaryValue("First-cycle success")).toHaveTextContent("—");
    expect(summaryValue("Twin rate (≥2 kids)")).toHaveTextContent("—");
    expect(summaryValue("Alive kids per kidding")).toHaveTextContent("—");
  });

  it("renders mortality numbers and the deaths-by-month table", async () => {
    await renderLoaded();

    expect(summaryValue("Total deaths (herd)")).toHaveTextContent("3");
    expect(summaryValue("Kids born (recorded)")).toHaveTextContent("40");
    expect(summaryValue("Stillborn")).toHaveTextContent("4");
    expect(summaryValue("Stillborn rate")).toHaveTextContent("10%");

    const janRow = screen.getByText("2026-01").closest("tr") as HTMLElement;
    expect(within(janRow).getByText("2")).toBeInTheDocument();
  });

  it("shows No deaths recorded when the monthly list is empty", async () => {
    const payload = structuredClone(PAYLOAD);
    payload.mortality.deaths_by_month = [];
    payload.mortality.total_deaths = 0;
    await renderLoaded(payload);

    expect(screen.getByText("No deaths recorded.")).toBeInTheDocument();
  });

  it("links to the financial summary", async () => {
    await renderLoaded();
    expect(screen.getByRole("link", { name: "Financial summary →" })).toHaveAttribute(
      "href",
      "/finance",
    );
  });

  it("shows the error detail when the reports GET fails", async () => {
    server.use(
      http.get("/api/dashboard/reports", () =>
        HttpResponse.json({ detail: "reports down" }, { status: 500 }),
      ),
    );
    renderWithProviders(<ReportsPage />);
    expect(await screen.findByText("reports down")).toBeInTheDocument();
  });

  it("denies access without reports.view and never calls the endpoint", async () => {
    let calls = 0;
    server.use(
      permissionsHandler(["animals.view"]),
      http.get("/api/dashboard/reports", () => {
        calls += 1;
        return HttpResponse.json(PAYLOAD);
      }),
    );
    renderWithProviders(<ReportsPage />);

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    expect(calls).toBe(0);
  });
});
