/**
 * Feeding page dispensing history — below-md mobile card list (md:hidden)
 * with right-aligned tabular-nums quantities, alongside the untouched
 * desktop table (hidden md:block, min-w-[640px]). Mirrors the tasks board's
 * worker-UX pattern.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FeedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(""),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI relies on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

const LINE_BREEDING = {
  bucket: "BREEDING",
  recipe_code: "LACTATING_60_40",
  recipe_name: "Lactating 60/40",
  heads: 20,
  kg_per_head: 1,
  daily_kg: 20,
  shifts: [{ shift: "MORNING", pct: 100, kg: 20, time: "6:30 AM" }],
};

/** The below-md card list inside the Dispensing-history section card. */
function mobileHistoryCards(): HTMLElement {
  const section = screen
    .getByText("Dispensing history")
    .closest("[data-slot='card']") as HTMLElement;
  const list = section.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

function desktopHistoryTable(): HTMLElement {
  const table = document.querySelector('[class~="md:block"] table[class*="min-w-[640px]"]');
  expect(table).not.toBeNull();
  return table as HTMLElement;
}

describe("FeedingPage dispensing-history mobile card list", () => {
  it("renders a below-md card per history entry and keeps the desktop table", async () => {
    server.use(
      http.get("/api/feeding/plan", () =>
        HttpResponse.json({
          lines: [LINE_BREEDING],
          records: [],
          records_total: 0,
          records_limit: 200,
          dispensed_totals: [],
        }),
      ),
      http.get("/api/feeding/recipes", () => HttpResponse.json({ recipes: [], allocation: [] })),
      http.get("/api/feeding/records", () =>
        HttpResponse.json({
          records: [
            {
              id: 88,
              date: "2026-01-01",
              shift: "NIGHT",
              bucket: "BREEDING",
              recipe_code: "LACTATING_60_40",
              qty_kg: 7.25,
            },
            {
              id: 89,
              date: "2026-01-02",
              shift: "MORNING",
              bucket: "MALE_KIDS",
              recipe_code: null,
              qty_kg: 3.5,
            },
          ],
          total: 2,
          limit: 50,
          offset: 0,
        }),
      ),
    );
    renderWithProviders(<FeedingPage />);
    await screen.findByText("Lactating 60/40");

    const cards = mobileHistoryCards();
    expect(within(cards).getByText("1 Jan 2026")).toBeInTheDocument();
    expect(within(cards).getByText("2 Jan 2026")).toBeInTheDocument();
    const firstCard = within(cards).getByText("1 Jan 2026").closest("div")!;
    expect(firstCard).toHaveTextContent("Night · Breeding · LACTATING_60_40");
    // No recipe recorded: the dash fallback survives on the card.
    const secondCard = within(cards).getByText("2 Jan 2026").closest("div")!;
    expect(secondCard).toHaveTextContent("Morning · Male kids · —");
    // Quantities stay tabular-nums on the phone surface.
    const qty = within(cards).getByText("7.25 kg");
    expect(qty).toHaveClass("tabular-nums");

    expect(desktopHistoryTable()).toHaveClass("min-w-[640px]");
  });
});
