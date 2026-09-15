/**
 * Vaccination schedule — below-md mobile card list (md:hidden) with the
 * status chip and all four dates per vaccine, alongside the untouched
 * desktop table (hidden md:block, min-w-[640px]). Mirrors the tasks
 * board's worker-UX pattern.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import VaccinationSchedulePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/health/schedule/7",
  useSearchParams: () => new URLSearchParams(""),
  useParams: () => ({ animalId: "7" }),
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

const ROWS = [
  {
    template_id: 1,
    template_name: "PPR",
    timing_note: "Annual booster",
    first_due: "2026-01-10",
    booster_due: "2027-01-10",
    last_done: "2026-01-10",
    next_due: "2027-01-10",
    status: "DONE",
  },
  {
    template_id: 2,
    template_name: "ET + TT",
    timing_note: null,
    first_due: "2026-03-01",
    booster_due: null,
    last_done: null,
    next_due: "2026-03-01",
    status: "OVERDUE",
  },
];

/** The below-md card list container. */
function mobileCardList(): HTMLElement {
  const list = document.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

function desktopTable(): HTMLElement {
  const table = document.querySelector('[class~="md:block"] table');
  expect(table).not.toBeNull();
  return table as HTMLElement;
}

describe("VaccinationSchedulePage mobile card list", () => {
  it("renders a below-md card per vaccine with dates and status, keeping the desktop table", async () => {
    server.use(
      http.get("/api/health/schedule/:animalId", () =>
        HttpResponse.json({ animal_id: 7, rows: ROWS }),
      ),
    );
    renderWithProviders(<VaccinationSchedulePage />);
    await screen.findAllByText("PPR");

    const cards = mobileCardList();
    expect(within(cards).getByText("PPR")).toBeInTheDocument();
    expect(within(cards).getByText("Annual booster")).toBeInTheDocument();
    expect(within(cards).getByText("ET + TT")).toBeInTheDocument();
    expect(within(cards).getByText("Done")).toBeInTheDocument();
    expect(within(cards).getByText("Overdue")).toBeInTheDocument();
    // The overdue card keeps the table row's tint.
    const overdueCard = within(cards).getByText("Overdue").closest("div[class*='rounded-xl']")!;
    expect(overdueCard).toHaveClass("bg-destructive/[0.04]");
    // All four dates survive on the card, with dash fallbacks.
    const pprCard = within(cards).getByText("PPR").closest("div[class*='rounded-xl']")!;
    expect(pprCard).toHaveTextContent("First dose due10 Jan 2026");
    expect(pprCard).toHaveTextContent("Last done10 Jan 2026");
    const etCard = within(cards).getByText("ET + TT").closest("div[class*='rounded-xl']")!;
    expect(etCard).toHaveTextContent("Booster due—");

    expect(desktopTable()).toHaveClass("min-w-[640px]");
  });
});
