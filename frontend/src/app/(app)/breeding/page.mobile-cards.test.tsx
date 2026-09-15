/**
 * Breeding register — below-md mobile card list (md:hidden) with ≥44px
 * touch-target actions alongside the untouched desktop table
 * (hidden md:block, min-w-[900px] floor). Mirrors the tasks board's
 * worker-UX pattern: the phone is the worker's primary device, and panning
 * a 9-column table inside a 390px screen is a scroll toy, not a register.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BreedingRecordOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import BreedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/breeding",
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

function makeRecord(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    semen_sire_name: null,
    breeding_date: "2026-07-01",
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: "2026-08-02",
    ultrasound_result_date: null,
    ultrasound_done: false,
    pregnant: null,
    kid_count_detected: null,
    expected_kidding_date: null,
    outcome: "PENDING",
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

const PENDING_REC = makeRecord({ id: 1 });
const PREGNANT_REC = makeRecord({
  id: 2,
  ultrasound_done: true,
  pregnant: true,
  kid_count_detected: 2,
  expected_kidding_date: "2026-11-28",
  outcome: "CONFIRMED_PREGNANT",
});

/** The below-md card list container (class-based lookup keeps jsdom honest —
 * Tailwind responsive classes are the source of truth for the breakpoint). */
function mobileCardList(): HTMLElement {
  const list = document.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

function desktopTable(): HTMLElement {
  const wrapper = document.querySelector('[class~="md:block"]');
  expect(wrapper).not.toBeNull();
  const table = wrapper!.querySelector("table");
  expect(table).not.toBeNull();
  return table as HTMLElement;
}

describe("BreedingPage mobile card list", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/breeding", () =>
        HttpResponse.json({
          records: [PENDING_REC, PREGNANT_REC],
          candidate_availability: null,
          total: 2,
          limit: 50,
          offset: 0,
        }),
      ),
    );
  });

  it("renders a below-md card per record and keeps the desktop table, both class-gated", async () => {
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("G-010");

    const cards = mobileCardList();
    // Both records render as cards with their key fields on the phone surface.
    expect(within(cards).getAllByText("G-010")).toHaveLength(2);
    expect(within(cards).getByText("Pending")).toBeInTheDocument();
    expect(within(cards).getByText("Confirmed Pregnant")).toBeInTheDocument();
    expect(within(cards).getByText(/Ultrasound due 2 Aug 2026/)).toBeInTheDocument();
    expect(within(cards).getByText(/Expected kidding: 28 Nov 2026/)).toBeInTheDocument();

    // The table stays untouched and hidden below md, keeping its wide floor.
    expect(desktopTable()).toHaveClass("min-w-[900px]");
  });

  it("links the doe and buck from the card when animals.view is held", async () => {
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("G-010");

    const card = mobileCardList().querySelectorAll(":scope > div")[0] as HTMLElement;
    expect(within(card).getByRole("link", { name: "G-010" })).toHaveAttribute(
      "href",
      "/animals/10",
    );
    expect(within(card).getByRole("link", { name: "G-020" })).toHaveAttribute(
      "href",
      "/animals/20",
    );
  });

  it("gives card actions ≥44px touch targets while table actions stay compact", async () => {
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("G-010");

    const cards = mobileCardList();
    const cardUltrasound = within(cards).getByRole("button", { name: "Ultrasound result" });
    expect(cardUltrasound).toHaveClass("h-11");
    const cardLoss = within(cards).getByRole("button", { name: "Record loss" });
    expect(cardLoss).toHaveClass("h-11");

    const table = desktopTable();
    expect(within(table).getByRole("button", { name: "Ultrasound result" })).toHaveClass("h-9");
    expect(within(table).getByRole("button", { name: "Record loss" })).toHaveClass("h-9");
  });

  it("opens the ultrasound dialog from the card action", async () => {
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("G-010");

    await user.click(
      within(mobileCardList()).getByRole("button", { name: "Ultrasound result" }),
    );
    expect(
      await screen.findByRole("dialog", { name: "Ultrasound result" }),
    ).toBeInTheDocument();
  });
});
