/**
 * Breeding + kidding outcome-dialog DOM caps: the pregnancy-loss notes box is
 * bounded at 4 000 characters and the kidding notes box is 2 rows.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BreedingRecordOut, KiddingRecordOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import BreedingPage from "../breeding/page";
import KiddingPage from "../kidding/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/breeding",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

const TODAY = farmToday();
const daysFromToday = (delta: number) => addDays(TODAY, delta);
const BRED_ON = daysFromToday(-120);

function makeBreeding(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    semen_sire_name: null,
    breeding_date: BRED_ON,
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: daysFromToday(-90),
    ultrasound_result_date: null,
    ultrasound_done: true,
    pregnant: true,
    kid_count_detected: 2,
    expected_kidding_date: daysFromToday(10),
    outcome: "CONFIRMED_PREGNANT",
    loss_date: null,
    loss_cause: null,
    loss_notes: null,
    loss_recorded_by_id: null,
    loss_recorded_at: null,
    created_at: "2026-01-01T00:00:00Z",
    has_kidding: false,
    doe_tag: "G-010",
    buck_tag: "G-020",
    ...overrides,
  };
}

function makeKidding(overrides: Partial<KiddingRecordOut>): KiddingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    date: "2026-07-20",
    breeding_record_id: 1,
    ease: "NORMAL",
    parity: null,
    placenta_passed: null,
    mastitis_suspected: false,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    kids: [],
    doe_tag: "G-010",
    ...overrides,
  };
}

describe("breeding + kidding outcome dialog DOM caps", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/breeding", () =>
        HttpResponse.json({
          records: [makeBreeding({ id: 2 })],
          candidate_availability: { eligible_doe_count: 1, eligible_buck_count: 1 },
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/breeding/candidates", ({ request }) => {
        const kind = new URL(request.url).searchParams.get("kind");
        const candidates = kind === "doe"
          ? [{ id: 10, tag_number: "G-010", name: "Lakshmi", age_months: 18, latest_weight_kg: 30 }]
          : [{ id: 20, tag_number: "G-020", name: null, age_months: 24, latest_weight_kg: null }];
        return HttpResponse.json({ candidates, total: 1, limit: 50, offset: 0 });
      }),
      http.post("/api/breeding/:recordId/loss", () => HttpResponse.json({})),
      http.get("/api/kidding", () =>
        HttpResponse.json({
          records: [makeKidding({ id: 21, kids: [] })],
          upcoming: [makeBreeding({ id: 12, expected_kidding_date: daysFromToday(10) })],
          upcoming_total: 1,
          upcoming_limit: 25,
          upcoming_offset: 0,
          overdue: [],
          overdue_total: 0,
          overdue_limit: 25,
          overdue_offset: 0,
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/kidding/pregnancies/:recordId", () =>
        HttpResponse.json(makeBreeding({ id: 12, expected_kidding_date: daysFromToday(10) })),
      ),
      http.post("/api/kidding", () => HttpResponse.json(makeKidding({ id: 99 })),),
    );
  });

  it("caps the pregnancy-loss notes at 4 000 characters", async () => {
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);
    const row = (await within(await screen.findByRole("table")).findByText("Confirmed Pregnant")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Record loss" }));
    const dialog = await screen.findByRole("dialog", { name: "Record pregnancy loss" });
    expect(within(dialog).getByLabelText(/notes/i)).toHaveAttribute("maxlength", "4000");
  });

  it("caps the kidding notes box at 2 rows", async () => {
    const user = userEvent.setup();
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Upcoming (next 30 days)");
    const section = screen.getByText("Upcoming (next 30 days)").closest("[data-slot='card']") as HTMLElement;
    const table = section.querySelector('[class~="md:block"] table') as HTMLElement;
    await user.click(within(table).getByRole("button", { name: "Record kidding" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/notes/i)).toHaveAttribute("rows", "2");
  });
});
