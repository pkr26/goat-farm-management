/**
 * Buckets board: card rendering (name, head-count badge, kg/head/day, who,
 * exit rule), per-bucket animal tables with tag links and null fallbacks,
 * empty-bucket / empty-board states, loading and 4xx/5xx error states, the
 * X-Farm-Id header, and the buckets.view permission gate.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import BucketsPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/buckets",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function boardAnimal(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    tag_number: "G-001",
    name: "Lakshmi",
    breed: "Osmanabadi",
    sex: "F",
    date_of_birth: "2025-05-10",
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
    cull_candidate: false,
    notes: null,
    created_at: "2026-01-01T05:30:00Z",
    age_months: 14,
    latest_weight_kg: 30.5,
    days_in_current_bucket: 21,
    ...overrides,
  };
}

const ROW_BREEDING = {
  bucket: "BREEDING",
  name: "Breeding Bucket",
  who: "Does ready to conceive",
  exit_rule: "Confirmed pregnant → PREGNANCY_EARLY",
  daily_kg_per_head: 1.5,
  animals_total: 2,
  animals_limit: 100,
  animals_page_path: "/animals?bucket=BREEDING",
  animals: [boardAnimal(), boardAnimal({ id: 2, tag_number: "G-002", name: null, sex: "M" })],
};

const ROW_QUARANTINE = {
  bucket: "QUARANTINE",
  name: "Quarantine",
  who: "New arrivals, 45 days",
  exit_rule: "",
  daily_kg_per_head: 1.25,
  animals_total: 0,
  animals_limit: 100,
  animals_page_path: "/animals?bucket=QUARANTINE",
  animals: [],
};

describe("BucketsPage", () => {
  let getCalls: number;
  let lastFarmHeader: string | null;

  function useBoardHandler(rows: unknown[] = [ROW_BREEDING, ROW_QUARANTINE]) {
    server.use(
      http.get("/api/buckets", ({ request }) => {
        getCalls += 1;
        lastFarmHeader = request.headers.get("X-Farm-Id");
        return HttpResponse.json(rows);
      }),
    );
  }

  beforeEach(() => {
    getCalls = 0;
    lastFarmHeader = null;
    useBoardHandler();
  });

  async function renderBoard() {
    renderWithProviders(<BucketsPage />);
    await screen.findByText("Breeding Bucket");
  }

  function cardOf(title: string): HTMLElement {
    return screen.getByText(title).closest('[data-slot="card"]') as HTMLElement;
  }

  it("renders the page heading and one card per bucket", async () => {
    await renderBoard();
    expect(screen.getByRole("heading", { level: 1, name: "Buckets" })).toBeInTheDocument();
    expect(screen.getByText("Breeding Bucket")).toBeInTheDocument();
    expect(screen.getByText("Quarantine")).toBeInTheDocument();
  });

  it("sends the selected farm header with the board request", async () => {
    await renderBoard();
    expect(getCalls).toBe(1);
    expect(lastFarmHeader).toBe("1");
  });

  it("shows the head-count badge per bucket", async () => {
    await renderBoard();
    expect(within(cardOf("Breeding Bucket")).getByText("2 head")).toBeInTheDocument();
    expect(within(cardOf("Quarantine")).getByText("0 head")).toBeInTheDocument();
  });

  it("shows the exact total and a full-list link when the preview is bounded", async () => {
    useBoardHandler([
      {
        ...ROW_BREEDING,
        animals_total: 125,
        animals_limit: 2,
      },
    ]);

    await renderBoard();
    const card = cardOf("Breeding Bucket");
    expect(within(card).getByText("125 head")).toBeInTheDocument();
    expect(within(card).getByText("Showing 2 of 125 animals.")).toBeInTheDocument();
    expect(within(card).getByText("Board preview limit: 2 animals.")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "View the full bucket register" })).toHaveAttribute(
      "href",
      "/animals?bucket=BREEDING",
    );
  });

  it("shows bucket code, daily kg/head and who for each card", async () => {
    await renderBoard();
    expect(
      within(cardOf("Breeding Bucket")).getByText(/BREEDING · 1\.5 kg\/head\/day · Does ready to conceive/),
    ).toBeInTheDocument();
  });

  it("rounds the daily kg/head to one decimal", async () => {
    await renderBoard();
    expect(within(cardOf("Quarantine")).getByText(/1\.3 kg\/head\/day|1\.2 kg\/head\/day/)).toBeInTheDocument();
    // Exact rounding of 1.25 → toFixed(1):
    expect(within(cardOf("Quarantine")).getByText(new RegExp(`${(1.25).toFixed(1)} kg/head/day`))).toBeInTheDocument();
  });

  it("shows the exit rule when present and hides it when empty", async () => {
    await renderBoard();
    expect(
      within(cardOf("Breeding Bucket")).getByText(/Exit: Confirmed pregnant → PREGNANCY_EARLY/),
    ).toBeInTheDocument();
    expect(within(cardOf("Quarantine")).queryByText(/^Exit:/)).not.toBeInTheDocument();
  });

  it("renders animal rows with tag links, sex, weight and days", async () => {
    await renderBoard();
    const card = cardOf("Breeding Bucket");
    const row = within(card).getByText("G-001").closest("tr") as HTMLElement;
    expect(within(row).getByRole("link", { name: "G-001" })).toHaveAttribute("href", "/animals/1");
    const cells = within(row).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("Lakshmi");
    expect(cells[2]).toHaveTextContent("F");
    expect(cells[3]).toHaveTextContent("30.5 kg");
    expect(cells[4]).toHaveTextContent("21");
  });

  it("renders a table row per animal (occupancy)", async () => {
    await renderBoard();
    // 2 animals + 1 header row.
    expect(within(cardOf("Breeding Bucket")).getAllByRole("row")).toHaveLength(3);
  });

  it("renders em dashes for null name, weight and missing days", async () => {
    useBoardHandler([
      {
        ...ROW_BREEDING,
        animals: [
          boardAnimal({ id: 3, tag_number: "G-003", name: null, latest_weight_kg: null, days_in_current_bucket: undefined }),
        ],
      },
    ]);
    renderWithProviders(<BucketsPage />);
    const row = (await screen.findByText("G-003")).closest("tr") as HTMLElement;
    const cells = within(row).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("—");
    expect(cells[3]).toHaveTextContent("—");
    expect(cells[4]).toHaveTextContent("—");
  });

  it("shows the per-bucket empty state for a bucket with no animals", async () => {
    await renderBoard();
    expect(
      within(cardOf("Quarantine")).getByText("No animals in this bucket."),
    ).toBeInTheDocument();
  });

  it("shows the board empty state when no buckets are configured", async () => {
    useBoardHandler([]);
    renderWithProviders(<BucketsPage />);
    expect(await screen.findByText("No buckets configured.")).toBeInTheDocument();
  });

  it("shows a loading indicator while the board request is pending", async () => {
    server.use(http.get("/api/buckets", () => new Promise<Response>(() => {})));
    renderWithProviders(<BucketsPage />);
    expect((await screen.findAllByText("Loading…")).length).toBeGreaterThan(0);
  });

  it("shows the server detail on a 500 response", async () => {
    server.use(
      http.get("/api/buckets", () =>
        HttpResponse.json({ detail: "Board aggregation failed" }, { status: 500 }),
      ),
    );
    renderWithProviders(<BucketsPage />);
    expect(await screen.findByText("Board aggregation failed")).toBeInTheDocument();
  });

  it("shows the server detail on a 400 response", async () => {
    server.use(
      http.get("/api/buckets", () =>
        HttpResponse.json({ detail: "Farm mismatch" }, { status: 400 }),
      ),
    );
    renderWithProviders(<BucketsPage />);
    expect(await screen.findByText("Farm mismatch")).toBeInTheDocument();
  });

  it("denies access without buckets.view and never calls the API", async () => {
    server.use(permissionsHandler(["animals.view"]));
    renderWithProviders(<BucketsPage />);
    expect(await screen.findByText(/don't have access to this page/)).toBeInTheDocument();
    expect(getCalls).toBe(0);
  });

  it("shows a loading indicator while permissions resolve", async () => {
    server.use(http.get("/api/auth/permissions", () => new Promise<Response>(() => {})));
    renderWithProviders(<BucketsPage />);
    expect((await screen.findAllByText("Loading…")).length).toBeGreaterThan(0);
  });

  it("loads the board with only buckets.view", async () => {
    server.use(permissionsHandler(["buckets.view"]));
    useBoardHandler([{ ...ROW_BREEDING, animals_total: 125 }]);
    await renderBoard();
    expect(getCalls).toBe(1);
    expect(screen.queryByRole("link", { name: "View the full bucket register" })).not.toBeInTheDocument();
    expect(cardOf("Breeding Bucket")).toHaveTextContent("The full register requires animal access.");
  });
});
