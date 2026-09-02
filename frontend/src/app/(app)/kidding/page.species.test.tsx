/**
 * Species-aware gestation windows on the record-{parturition} dialog. The
 * backend enforces per-species bands (goats 100–200 days, buffalo 270–350 —
 * backend/app/models/species.py); the client gate must mirror them or the
 * intersection goes empty and one species can never record a birth at all.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server, TEST_FARMS, ALL_PERMISSIONS, permissionsHandler } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import KiddingPage from "./page";
import type { BreedingRecordOut, KiddingListOut, KiddingRecordOut } from "@/api/generated/models";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/kidding",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const TODAY = farmToday();

function makeBreeding(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    semen_sire_name: null,
    breeding_date: addDays(TODAY, -120),
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: addDays(TODAY, -90),
    ultrasound_result_date: null,
    ultrasound_done: true,
    pregnant: true,
    kid_count_detected: 1,
    expected_kidding_date: addDays(TODAY, 10),
    outcome: "CONFIRMED_PREGNANT",
    loss_date: null,
    loss_cause: null,
    loss_notes: null,
    loss_recorded_by_id: null,
    loss_recorded_at: null,
    has_kidding: false,
    doe_tag: "G-010",
    buck_tag: "G-020",
    ...overrides,
  };
}

function listPayload(upcoming: BreedingRecordOut[]): KiddingListOut {
  return {
    records: [],
    upcoming,
    upcoming_total: upcoming.length,
    upcoming_limit: 25,
    upcoming_offset: 0,
    overdue: [],
    overdue_total: 0,
    overdue_limit: 25,
    overdue_offset: 0,
    total: 0,
    limit: 50,
    offset: 0,
  };
}

function stubKiddingEndpoints(payload: KiddingListOut, posts: unknown[]) {
  server.use(
    http.get("/api/kidding", () => HttpResponse.json(payload)),
    http.get("/api/kidding/pregnancies/:recordId", () =>
      HttpResponse.json(payload.upcoming[0] ?? { detail: "not found" }),
    ),
    http.post("/api/kidding", async ({ request }) => {
      posts.push(await request.json());
      return HttpResponse.json({} as KiddingRecordOut, { status: 201 });
    }),
  );
}

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

describe("KiddingPage — species gestation windows", () => {
  let posts: unknown[];

  beforeEach(() => {
    posts = [];
    server.use(permissionsHandler(ALL_PERMISSIONS));
  });

  it("bounds a goat record to the goat band (100–200 days)", async () => {
    stubKiddingEndpoints(listPayload([makeBreeding({})]), posts);
    // Default MSW session farm is a GOAT farm.
    renderWithProviders(<KiddingPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Record kidding/i }));

    const dateInput = await screen.findByLabelText(/Kidding date/i);
    // Bred 120 days ago: floor is breeding+100 = today−20, ceiling today.
    expect(dateInput).toHaveAttribute("min", addDays(TODAY, -20));
    expect(dateInput).toHaveAttribute("max", TODAY);
  });

  it("bounds a buffalo calving to the buffalo band (270–350 days)", async () => {
    const DAIRY_FARM = { ...TEST_FARMS[0], farm_type: "BUFFALO_DAIRY" };
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([DAIRY_FARM])));
    // Bred 300 days ago — inside the buffalo band, outside the goat band.
    stubKiddingEndpoints(listPayload([makeBreeding({ breeding_date: addDays(TODAY, -300) })]), posts);
    renderWithProviders(<KiddingPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Record calving/i }));

    const dateInput = await screen.findByLabelText(/Calving date/i);
    // Floor is breeding+270 = today−30; ceiling is min(breeding+350, today).
    expect(dateInput).toHaveAttribute("min", addDays(TODAY, -30));
    expect(dateInput).toHaveAttribute("max", TODAY);

    // The species facts in the helper copy must match the dairy protocol.
    expect(screen.getByText(/sexed young-stock pens/)).toBeInTheDocument();
    expect(screen.getByText(/\+ 90 days/i)).toBeInTheDocument();

    // A day-295 calving (within the buffalo band) is submittable — under the
    // old goat-only gate this was impossible for a buffalo dairy.
    fireEvent.change(dateInput, { target: { value: addDays(TODAY, -5) } });
    await user.click(screen.getByRole("button", { name: /Save calving/i }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ date: addDays(TODAY, -5) });
  });

  it("caps a buffalo calving at one calf by default and two rows maximum", async () => {
    const DAIRY_FARM = { ...TEST_FARMS[0], farm_type: "BUFFALO_DAIRY" };
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([DAIRY_FARM])));
    stubKiddingEndpoints(listPayload([makeBreeding({ breeding_date: addDays(TODAY, -300) })]), posts);
    renderWithProviders(<KiddingPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Record calving/i }));

    // Single-calf norm: one row on open, not the goat twins default.
    expect(await screen.findAllByPlaceholderText("auto")).toHaveLength(1);

    // Species litter cap of 2: one add is possible, the second is not.
    const add = screen.getByRole("button", { name: /Add calf/i });
    await user.click(add);
    expect(screen.getAllByPlaceholderText("auto")).toHaveLength(2);
    expect(add).toBeDisabled();
  });
});
