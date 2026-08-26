/**
 * Buckets board — branch/boundary detail not covered by page.test.tsx: the
 * exact truncation boundary (a preview that shows every animal must not claim
 * to be capped), the spacing that keeps the truncation sentence readable next
 * to its link, and the generic fallback used when a failure carries no server
 * detail.
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

describe("BucketsPage branches", () => {
  function useBoardHandler(rows: unknown[] = [ROW_BREEDING]) {
    server.use(http.get("/api/buckets", () => HttpResponse.json(rows)));
  }

  beforeEach(() => {
    useBoardHandler();
  });

  function cardOf(title: string): HTMLElement {
    return screen.getByText(title).closest('[data-slot="card"]') as HTMLElement;
  }

  async function renderBoard() {
    renderWithProviders(<BucketsPage />);
    await screen.findByText("Breeding Bucket");
  }

  // The note only makes sense when the board actually withheld somebody:
  // "Showing 2 of 2 animals" would tell the operator to go looking for rows
  // that are already on screen.
  it("omits the truncation note when the preview holds the whole bucket", async () => {
    await renderBoard();

    const card = cardOf("Breeding Bucket");
    expect(within(card).getAllByRole("row")).toHaveLength(3);
    expect(within(card).queryByText(/^Showing /)).not.toBeInTheDocument();
    expect(within(card).queryByText(/Board preview limit/)).not.toBeInTheDocument();
    expect(
      within(card).queryByRole("link", { name: "View the full bucket register" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the truncation note readable next to the register link", async () => {
    useBoardHandler([{ ...ROW_BREEDING, animals_total: 125, animals_limit: 2 }]);
    await renderBoard();

    // The sentence and the link text are separate nodes; without the explicit
    // separator they run together as "animals.View the full bucket register".
    expect(cardOf("Breeding Bucket").textContent).toContain(
      "Showing 2 of 125 animals. View the full bucket register",
    );
  });

  it("keeps the truncation note readable next to the no-access fallback", async () => {
    server.use(permissionsHandler(["buckets.view"]));
    useBoardHandler([{ ...ROW_BREEDING, animals_total: 125, animals_limit: 2 }]);
    await renderBoard();

    expect(cardOf("Breeding Bucket").textContent).toContain(
      "Showing 2 of 125 animals. The full register requires animal access.",
    );
  });

  it("falls back to a generic message when the board request fails in transport", async () => {
    // A dropped connection never reaches the ApiError mapping, so the page has
    // no server detail to quote and must still say what went wrong.
    server.use(http.get("/api/buckets", () => HttpResponse.error()));
    renderWithProviders(<BucketsPage />);

    expect(await screen.findByText("Could not load buckets.")).toBeInTheDocument();
  });
});
