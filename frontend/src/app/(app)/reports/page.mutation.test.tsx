/**
 * Reports page — mutation-hardening suite: status-key humanization, the
 * withheld cull total, truncated cull previews (with and without
 * breeding.view), and the two error retry paths.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

function makePayload(overrides: Record<string, unknown> = {}) {
  return {
    bucket_rows: [],
    total_active: 10,
    sex_counts: { F: 8, M: 2 },
    status_counts: {},
    breeding: {
      total_records: 5,
      conception_rate: 60,
      first_cycle_rate: 40,
      kiddings: 3,
      kids_per_kidding: 1.5,
      twin_rate: 25,
      cull_candidates: [],
      cull_candidates_total: 0,
      cull_candidates_limit: 20,
    },
    mortality: {
      total_deaths: 1,
      deaths_by_month: [["2026-01", 1]],
      total_kids_born: 4,
      stillborn: 0,
      stillborn_rate: 0,
    },
    ...overrides,
  };
}

function reportsHandler(payload: Record<string, unknown>) {
  return http.get("/api/dashboard/reports", () => HttpResponse.json(payload));
}

async function renderLoaded() {
  renderWithProviders(<ReportsPage />);
  // A data card title only renders once the report has landed.
  await screen.findByText("Herd summary (10 active)");
}

describe("ReportsPage — status rows", () => {
  it("humanizes snake_case status keys into sentence-case labels", async () => {
    server.use(
      reportsHandler(
        makePayload({ status_counts: { DEAD: 2, SOLD: 7, CULLED: 1 } }),
      ),
    );
    await renderLoaded();

    expect(screen.getByText("Dead (all time)")).toBeInTheDocument();
    expect(screen.getByText("Sold (all time)")).toBeInTheDocument();
    expect(screen.getByText("Culled (all time)")).toBeInTheDocument();
    expect(screen.queryByText("DEAD (all time)")).not.toBeInTheDocument();
  });
});

describe("ReportsPage — cull candidates", () => {
  it("marks a null total as withheld rather than a zero count", async () => {
    server.use(
      reportsHandler(
        makePayload({
          breeding: {
            total_records: 5,
            conception_rate: null,
            first_cycle_rate: null,
            kiddings: 3,
            kids_per_kidding: null,
            twin_rate: null,
            cull_candidates: [],
            cull_candidates_total: null,
            cull_candidates_limit: 20,
          },
        }),
      ),
    );
    await renderLoaded();

    expect(screen.getAllByText("Requires breeding access").length).toBeGreaterThan(0);
    expect(screen.queryByText(/Showing \d+ of/)).not.toBeInTheDocument();
  });

  it("names the truncation and links the full list with breeding.view", async () => {
    server.use(
      reportsHandler(
        makePayload({
          breeding: {
            total_records: 5,
            conception_rate: 60,
            first_cycle_rate: 40,
            kiddings: 3,
            kids_per_kidding: 1.5,
            twin_rate: 25,
            cull_candidates: [CULL_ANIMAL],
            cull_candidates_total: 3,
            cull_candidates_limit: 20,
          },
        }),
      ),
    );
    await renderLoaded();

    expect(screen.getByText("G-021 · Kali")).toBeInTheDocument();
    // The truncation sentence mixes text and link nodes — assert on the row.
    const cullRow = screen.getByText("Cull candidates").closest("tr") as HTMLElement;
    expect(cullRow.textContent).toContain("Showing 1 of 3; this report preview is capped at 20.");
    expect(screen.getByRole("link", { name: "Review the full operational list" })).toHaveAttribute(
      "href",
      "/breeding",
    );
  });

  it("states the access requirement instead of the link without breeding.view", async () => {
    server.use(
      permissionsHandler(["reports.view", "animals.view"]),
      reportsHandler(
        makePayload({
          breeding: {
            total_records: 5,
            conception_rate: null,
            first_cycle_rate: null,
            kiddings: 3,
            kids_per_kidding: null,
            twin_rate: null,
            cull_candidates: [CULL_ANIMAL],
            cull_candidates_total: 3,
            cull_candidates_limit: 20,
          },
        }),
      ),
    );
    await renderLoaded();

    // The animal chip still renders, but as plain text (no animals link page).
    expect(screen.getByText("G-021 · Kali")).toBeInTheDocument();
    const cullRow = screen.getByText("Cull candidates").closest("tr") as HTMLElement;
    expect(cullRow.textContent).toContain("The full list requires breeding access.");
    expect(
      screen.queryByRole("link", { name: "Review the full operational list" }),
    ).not.toBeInTheDocument();
  });
});

describe("ReportsPage — error and retry paths", () => {
  it("retries the report load from the error state", async () => {
    let calls = 0;
    server.use(
      http.get("/api/dashboard/reports", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "report offline" }, { status: 503 })
          : HttpResponse.json(makePayload());
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ReportsPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("report offline");
    await user.click(screen.getByRole("button", { name: "Retry reports" }));

    await waitFor(() => expect(calls).toBe(2));
    expect(await screen.findByText("Breeding performance")).toBeInTheDocument();
  });

  it("recovers the page when the permissions probe is retried", async () => {
    let permsCalls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        permsCalls += 1;
        return permsCalls === 1
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : HttpResponse.json({ is_owner: true, permissions: ["reports.view"] });
      }),
      reportsHandler(makePayload()),
    );
    const user = userEvent.setup();
    renderWithProviders(<ReportsPage />);

    await user.click(await screen.findByRole("button", { name: /retry/i }));
    expect(await screen.findByText("Breeding performance")).toBeInTheDocument();
  });
});
