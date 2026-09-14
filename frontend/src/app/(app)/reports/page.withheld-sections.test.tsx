/**
 * Reports page — branch detail not covered by page.extended.test.tsx: the two
 * independent sources of "withheld" (a nulled payload field and a missing
 * permission) driving the health/breeding rows, the clinical rows that must
 * stay visible as withheld, the pending states, and the small pieces of
 * copy/markup (separators, the outline action link) that carry meaning.
 */

import { screen, waitFor, within } from "@testing-library/react";
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

function cullAnimal(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

interface ReportsPayload {
  // The API nulls the animal aggregates without animals.view; the generated
  // client types lag the contract, so the fixture widens them (RT-P7-1).
  bucket_rows: { name: string; code: string; count: number; avg_weight: number | null }[] | null;
  total_active: number | null;
  sex_counts: Record<string, number> | null;
  status_counts: Record<string, number>;
  breeding: {
    total_records: number;
    conception_rate: number | null;
    first_cycle_rate: number | null;
    kiddings: number;
    kids_per_kidding: number | null;
    twin_rate: number | null;
    cull_candidates: Record<string, unknown>[];
    cull_candidates_total: number | null;
    cull_candidates_limit: number;
  };
  mortality: {
    total_deaths: number | null;
    deaths_by_month: [string, number][];
    total_kids_born: number;
    stillborn: number | null;
    stillborn_rate: number | null;
  };
}

/** A fully permitted payload: nothing withheld, and no clinical statuses in
 *  `status_counts` (a permitted farm simply has no DEAD/CULLED animals). */
const PAYLOAD: ReportsPayload = {
  bucket_rows: [{ name: "Breeding herd", code: "BREEDING", count: 20, avg_weight: 32.45 }],
  total_active: 47,
  sex_counts: { F: 30, M: 17 },
  status_counts: { SOLD: 8 },
  breeding: {
    total_records: 24,
    conception_rate: 66,
    first_cycle_rate: 50,
    kiddings: 12,
    kids_per_kidding: 1.8,
    twin_rate: 33,
    cull_candidates: [cullAnimal()],
    cull_candidates_total: 1,
    cull_candidates_limit: 20,
  },
  mortality: {
    total_deaths: 3,
    deaths_by_month: [["2026-01", 2]],
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

function cullCell(): HTMLElement {
  const row = screen.getByText("Cull candidates").closest("tr") as HTMLElement;
  return within(row).getAllByRole("cell")[1];
}

async function renderLoaded(payload: ReportsPayload = PAYLOAD) {
  server.use(http.get("/api/dashboard/reports", () => HttpResponse.json(payload)));
  renderWithProviders(<ReportsPage />);
  expect(await screen.findByText(/Herd summary/)).toBeInTheDocument();
}

describe("ReportsPage branches", () => {
  it("shows the page shell skeleton while the permission probe is in flight", async () => {
    server.use(http.get("/api/auth/permissions", () => new Promise<Response>(() => {})));
    renderWithProviders(<ReportsPage />);

    // The real header and skeleton stand in for the page — not a bare
    // "Loading…" line.
    expect(
      await screen.findByRole("heading", { level: 1, name: "Reports" }),
    ).toBeInTheDocument();
    // An unresolved probe is not a denial.
    expect(screen.queryByText(/don't have access to this page/)).not.toBeInTheDocument();
  });

  it("shows the loading state while the reports request is in flight", async () => {
    const requested = vi.fn();
    server.use(
      http.get("/api/dashboard/reports", () => {
        requested();
        return new Promise<Response>(() => {});
      }),
    );
    renderWithProviders(<ReportsPage />);

    // Wait past the permission probe: only then is the page's loading skeleton
    // reporting the reports fetch rather than the probe.
    await waitFor(() => expect(requested).toHaveBeenCalled());
    // A request still on the wire is not a failure.
    await waitFor(() =>
      expect(screen.queryByText("Could not load the reports.")).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading reports…");
    expect(screen.getByRole("heading", { level: 1, name: "Reports" })).toBeInTheDocument();
  });

  it("falls back to a generic message when the reports request fails in transport", async () => {
    // A dropped connection never reaches the ApiError mapping, so there is no
    // server detail to quote.
    server.use(http.get("/api/dashboard/reports", () => HttpResponse.error()));
    renderWithProviders(<ReportsPage />);

    expect(await screen.findByText("Could not load the reports.")).toBeInTheDocument();
  });

  // The payload sentinel alone must be enough: permissions come from a
  // separate, separately-cached query that can still say "allowed".
  it("names every health figure the payload withheld, permission or not", async () => {
    const payload = structuredClone(PAYLOAD);
    payload.mortality.total_deaths = null;
    payload.mortality.stillborn = null;
    payload.mortality.deaths_by_month = [];
    await renderLoaded(payload);

    expect(summaryValue("Total deaths (herd)")).toHaveTextContent("Requires health access");
    expect(summaryValue("Stillborn")).toHaveTextContent("Requires health access");
    // A withheld rate is not "not enough data" — it must not read as "—".
    expect(summaryValue("Stillborn rate")).toHaveTextContent("Requires health access");
    expect(summaryValue("Stillborn rate")).not.toHaveTextContent("10%");
    // The clinical statuses the API dropped from status_counts stay in the
    // table, labelled, instead of vanishing.
    expect(summaryValue("Dead (all time)")).toHaveTextContent("Requires health access");
    expect(summaryValue("Culled (all time)")).toHaveTextContent("Requires health access");
    // An empty monthly breakdown is withheld here, not "no deaths".
    expect(screen.queryByText("No deaths recorded.")).not.toBeInTheDocument();
  });

  // The mirror case: a stale privileged payload after health.view was revoked.
  it("keeps withholding health figures when only the permission is gone", async () => {
    const payload = structuredClone(PAYLOAD);
    payload.status_counts = { SOLD: 8, DEAD: 3 };
    server.use(permissionsHandler(["reports.view", "breeding.view", "animals.view"]));
    await renderLoaded(payload);

    expect(summaryValue("Stillborn rate")).toHaveTextContent("Requires health access");
    // DEAD is present in status_counts, so it is already rendered with its
    // count — the withheld filler must not duplicate the row.
    expect(screen.getAllByText("Dead (all time)")).toHaveLength(1);
    expect(summaryValue("Dead (all time)")).toHaveTextContent("3");
    // CULLED is the one the payload omitted.
    expect(summaryValue("Culled (all time)")).toHaveTextContent("Requires health access");
  });

  it("adds no withheld clinical rows when the health figures are present", async () => {
    await renderLoaded();

    expect(summaryValue("Stillborn rate")).toHaveTextContent("10%");
    expect(screen.queryByText("Dead (all time)")).not.toBeInTheDocument();
    expect(screen.queryByText("Culled (all time)")).not.toBeInTheDocument();
    expect(screen.queryByText(/Requires health access/)).not.toBeInTheDocument();
  });

  it("names every breeding rate the payload withheld, permission or not", async () => {
    const payload = structuredClone(PAYLOAD);
    payload.breeding.cull_candidates = [];
    payload.breeding.cull_candidates_total = null;
    payload.breeding.kids_per_kidding = null;
    await renderLoaded(payload);

    expect(
      summaryValue("Conception rate (ultrasound-confirmed / completed)"),
    ).toHaveTextContent("Requires breeding access");
    expect(summaryValue("First-cycle success")).toHaveTextContent("Requires breeding access");
    expect(summaryValue("Twin rate (≥2 kids)")).toHaveTextContent("Requires breeding access");
    expect(summaryValue("Twin rate (≥2 kids)")).not.toHaveTextContent("33%");
    // RT-P2-2: a withheld kids-per-kidding reads as withheld, never as the
    // "not enough data" dash the stale-permission window used to show.
    expect(summaryValue("Alive kids per kidding")).toHaveTextContent("Requires breeding access");
    expect(summaryValue("Alive kids per kidding")).not.toHaveTextContent("—");
    expect(cullCell()).toHaveTextContent("Requires breeding access");
    // With no total to compare against, there is no cap to report either.
    expect(screen.queryByText(/this report preview is capped at/)).not.toBeInTheDocument();
  });

  it("keeps withholding breeding rates when only the permission is gone", async () => {
    server.use(permissionsHandler(["reports.view", "health.view", "animals.view"]));
    await renderLoaded();

    expect(
      summaryValue("Conception rate (ultrasound-confirmed / completed)"),
    ).toHaveTextContent("Requires breeding access");
    expect(summaryValue("First-cycle success")).toHaveTextContent("Requires breeding access");
    expect(summaryValue("Twin rate (≥2 kids)")).toHaveTextContent("Requires breeding access");
    expect(summaryValue("Alive kids per kidding")).toHaveTextContent("Requires breeding access");
  });

  // The animal aggregates (bucket rows, sex split, active total) now carry
  // the same null sentinels when animals.view is missing (RT-P7-1): the
  // sentinel alone must withhold even while the permission cache says yes.
  it("names every animal aggregate the payload withheld, permission or not", async () => {
    const payload = structuredClone(PAYLOAD);
    payload.bucket_rows = null;
    payload.total_active = null;
    payload.sex_counts = null;
    await renderLoaded(payload);

    expect(screen.getByText("Herd summary")).toBeInTheDocument();
    expect(screen.queryByText(/Herd summary \(/)).not.toBeInTheDocument();
    expect(summaryValue("Females (active)")).toHaveTextContent("Requires animals access");
    expect(summaryValue("Males (active)")).toHaveTextContent("Requires animals access");
    // The bucket register names the withholding instead of rendering empty —
    // the marker sits in the table body the rows would occupy.
    expect(screen.getAllByText("Requires animals access")).toHaveLength(3);
    // Status counts are lifetime aggregates the API still sends un-nullled.
    expect(summaryValue("Sold (all time)")).toHaveTextContent("8");
  });

  it("keeps withholding the animal aggregates when only the permission is gone", async () => {
    server.use(permissionsHandler(["reports.view", "breeding.view", "health.view"]));
    await renderLoaded();

    expect(screen.getByText("Herd summary")).toBeInTheDocument();
    expect(screen.queryByText(/Herd summary \(/)).not.toBeInTheDocument();
    expect(summaryValue("Females (active)")).toHaveTextContent("Requires animals access");
    expect(summaryValue("Males (active)")).toHaveTextContent("Requires animals access");
  });

  it("renders the financial summary action as an outline button", async () => {
    await renderLoaded();

    const link = screen.getByRole("link", { name: "Financial summary →" });
    // A secondary navigation action, not the page's primary call to action.
    expect(link).toHaveClass("border-border", "bg-background");
    expect(link).not.toHaveClass("bg-primary");
  });

  it("separates a bucket's name from its code", async () => {
    await renderLoaded();

    const cell = screen.getByText("BREEDING").closest("td") as HTMLElement;
    expect(cell.textContent).toBe("Breeding herd BREEDING");
  });

  it("explains which breeding records the rates are computed over", async () => {
    await renderLoaded();

    expect(
      screen.getByText(
        /^Conception, kidding and twinning rates across all breeding records\. A pregnancy confirmed by ultrasound/,
      ),
    ).toBeInTheDocument();
  });

  it("renders the bare cull count with no chip list when there are no candidates", async () => {
    const payload = structuredClone(PAYLOAD);
    payload.breeding.cull_candidates = [];
    payload.breeding.cull_candidates_total = 0;
    await renderLoaded(payload);

    const cell = cullCell();
    expect(cell.textContent).toBe("0");
    // Not even an empty chip container: it would trail the count with a gap.
    expect(cell.children).toHaveLength(0);
  });

  it("keeps the cap note readable next to the full-list link", async () => {
    const payload = structuredClone(PAYLOAD);
    payload.breeding.cull_candidates_total = 37;
    payload.breeding.cull_candidates_limit = 20;
    await renderLoaded(payload);

    // The sentence and the link text are separate nodes; without the explicit
    // separator they run together as "capped at 20.Review the full…".
    expect(cullCell().textContent).toContain(
      "capped at 20. Review the full operational list",
    );
  });

  it("renders an unnamed cull candidate as its bare tag", async () => {
    const payload = structuredClone(PAYLOAD);
    payload.breeding.cull_candidates = [cullAnimal({ id: 22, tag_number: "G-022", name: null })];
    await renderLoaded(payload);

    expect(within(cullCell()).getByRole("link", { name: "G-022" })).toHaveAttribute(
      "href",
      "/animals/22?returnTo=%2Freports",
    );
  });
});
