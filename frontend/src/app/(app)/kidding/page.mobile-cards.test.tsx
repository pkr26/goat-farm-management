/**
 * Kidding page — below-md mobile card lists (md:hidden) for the overdue,
 * upcoming and recent queues, with ≥44px Record touch targets, alongside
 * the untouched desktop tables (hidden md:block with their min-w floors).
 * Mirrors the tasks board's worker-UX pattern.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BreedingRecordOut, KiddingRecordOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import KiddingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/kidding",
  useSearchParams: () => new URLSearchParams(""),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI relies on.
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

const TODAY = farmToday();

function makeBreeding(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    semen_sire_name: null,
    breeding_date: addDays(TODAY, -200),
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: addDays(TODAY, -160),
    ultrasound_result_date: null,
    ultrasound_done: true,
    pregnant: true,
    kid_count_detected: 2,
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

function makeKidding(overrides: Partial<KiddingRecordOut>): KiddingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    date: "2026-07-20",
    breeding_record_id: 1,
    ease: "ASSISTED",
    parity: 2,
    placenta_passed: true,
    mastitis_suspected: false,
    notes: "big twins",
    kids: [
      {
        id: 1,
        tag: "G-101",
        sex: "F",
        birth_weight: 2.4,
        status: "ALIVE",
        mortality_reported_at: null,
        colostrum_within_2h: true,
        navel_dipped: null,
        dam_rejected: false,
        animal_id: 55,
      },
    ],
    doe_tag: "G-010",
    ...overrides,
  };
}

const OVERDUE_REC = makeBreeding({ id: 11, expected_kidding_date: addDays(TODAY, -5) });
const UPCOMING_REC = makeBreeding({ id: 12 });
const HISTORY = makeKidding({ id: 21 });

/** The section card by its visible title. */
function sectionCard(title: string | RegExp): HTMLElement {
  return screen.getByText(title).closest("[data-slot='card']") as HTMLElement;
}

/** The below-md card list inside a section card. */
function mobileCards(sectionCardEl: HTMLElement): HTMLElement {
  const list = sectionCardEl.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

/** The desktop table inside a section card. */
function desktopTable(sectionCardEl: HTMLElement): HTMLElement {
  const table = sectionCardEl.querySelector('[class~="md:block"] table');
  expect(table).not.toBeNull();
  return table as HTMLElement;
}

describe("KiddingPage mobile card lists", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/kidding", () =>
        HttpResponse.json({
          records: [HISTORY],
          upcoming: [UPCOMING_REC],
          upcoming_total: 1,
          upcoming_limit: 25,
          upcoming_offset: 0,
          overdue: [OVERDUE_REC],
          overdue_total: 1,
          overdue_limit: 25,
          overdue_offset: 0,
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
    );
  });

  it("renders the overdue queue as cards with days late and keeps the desktop table", async () => {
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Overdue (past expected date, no kidding recorded)");

    const section = sectionCard(/Overdue/);
    const cards = mobileCards(section);
    expect(within(cards).getByRole("link", { name: "G-010" })).toHaveAttribute(
      "href",
      "/animals/10",
    );
    expect(within(cards).getByText(/\(5d late\)/)).toBeInTheDocument();
    expect(desktopTable(section)).toHaveClass("min-w-[560px]");
  });

  it("renders the upcoming queue as cards with days left and kids detected", async () => {
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Upcoming (next 30 days)");

    const section = sectionCard("Upcoming (next 30 days)");
    const cards = mobileCards(section);
    expect(within(cards).getByRole("link", { name: "G-010" })).toBeInTheDocument();
    expect(within(cards).getByText("10d left")).toBeInTheDocument();
    expect(within(cards).getByText(/Kids detected: 2/)).toBeInTheDocument();
    expect(desktopTable(section)).toHaveClass("min-w-[720px]");
  });

  it("renders recent kiddings as cards with ease badge, care facts and linked kids", async () => {
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Recent kiddings");

    const section = sectionCard("Recent kiddings");
    const cards = mobileCards(section);
    expect(within(cards).getByText("20 Jul 2026")).toBeInTheDocument();
    expect(within(cards).getByText("Assisted")).toBeInTheDocument();
    expect(within(cards).getByText("parity 2 · placenta passed")).toBeInTheDocument();
    expect(within(cards).getByRole("link", { name: "G-101" })).toHaveAttribute(
      "href",
      "/animals/55",
    );
    expect(within(cards).getByText(/big twins/)).toBeInTheDocument();
    expect(desktopTable(section)).toHaveClass("min-w-[720px]");
  });

  it("gives card Record actions ≥44px touch targets while table actions stay compact", async () => {
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Upcoming (next 30 days)");

    const upcoming = sectionCard("Upcoming (next 30 days)");
    const cardButton = within(mobileCards(upcoming)).getByRole("button", {
      name: "Record kidding",
    });
    expect(cardButton).toHaveClass("h-11");
    const tableButton = within(desktopTable(upcoming)).getByRole("button", {
      name: "Record kidding",
    });
    expect(tableButton).toHaveClass("h-9");
    expect(tableButton).not.toHaveClass("h-11");
  });

  it("opens the record dialog from a mobile card action", async () => {
    const user = userEvent.setup();
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Upcoming (next 30 days)");

    await user.click(
      within(mobileCards(sectionCard("Upcoming (next 30 days)"))).getByRole("button", {
        name: "Record kidding",
      }),
    );
    expect(await screen.findByRole("dialog", { name: "Record kidding" })).toBeInTheDocument();
  });
});
