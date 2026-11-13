import { fireEvent, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import type { BreedingRecordOut, KiddingListOut } from "@/api/generated/models";
import { addDays, farmToday, formatDate } from "@/lib/format";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import KiddingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/kidding", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function breeding(id: number, doe: number, tag: string | null | undefined, expected: string): BreedingRecordOut {
  const today = farmToday();
  return { id, doe_id: doe, buck_id: 20, semen_sire_name: null,
    breeding_date: addDays(today, -120), method: "NATURAL", heat_cycle_number: 1,
    ultrasound_date: addDays(today, -90), ultrasound_result_date: null,
    ultrasound_done: true, pregnant: true, kid_count_detected: null,
    expected_kidding_date: expected, outcome: "CONFIRMED_PREGNANT",
    loss_date: null, loss_cause: null, loss_notes: null, loss_recorded_by_id: null,
    loss_recorded_at: null, created_at: "2026-01-01T00:00:00Z", has_kidding: false, doe_tag: tag, buck_tag: "G-020" };
}

it.each([
  { tag: "", links: true }, { tag: "", links: false },
  { tag: null, links: true }, { tag: undefined, links: false },
  { tag: "G-real", links: true }, { tag: "G-real", links: false },
])("keeps a readable doe identity across queues, history, and the recording dialog ($tag, links=$links)", async ({ tag, links }) => {
  server.use(permissionsHandler(["kidding.view", "kidding.manage", ...(links ? ["animals.view"] : [])]));
  const today = farmToday();
  const expected = addDays(today, 10);
  const payload: KiddingListOut = {
    upcoming: [breeding(12, 10, tag, expected)], upcoming_total: 1, upcoming_limit: 25, upcoming_offset: 0,
    overdue: [breeding(13, 11, tag, addDays(today, -5))], overdue_total: 1, overdue_limit: 25, overdue_offset: 0,
    records: [{ id: 21, doe_id: 12, date: addDays(today, -10), breeding_record_id: 14,
      ease: "NORMAL", parity: null, placenta_passed: null, mastitis_suspected: false,
      notes: null, created_at: "2026-01-01T00:00:00Z", kids: [], doe_tag: tag }],
    total: 1, limit: 50, offset: 0,
  };
  server.use(http.get("/api/kidding", () => HttpResponse.json(payload)));
  renderWithProviders(<KiddingPage />);
  await screen.findByRole("heading", { name: "Recent kiddings" });
  for (const id of [10, 11, 12]) {
    const label = tag || `Doe #${id}`;
    expect(screen.getAllByText(label).length).toBeGreaterThanOrEqual(2);
    if (links) {
      const animalLinks = screen.getAllByRole("link", { name: label });
      expect(animalLinks.filter((link) => link.getAttribute("href") === `/animals/${id}`)).toHaveLength(2);
    } else expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument();
  }
  const heading = screen.getByRole("heading", { name: "Upcoming (next 30 days)" });
  const card = heading.closest("[data-slot='card']");
  expect(card).not.toBeNull();
  fireEvent.click(within(card as HTMLElement).getAllByRole("button", { name: "Record kidding" })[0]);
  const dialog = await screen.findByRole("dialog", { name: "Record kidding" });
  expect(dialog).toHaveAccessibleDescription(`Doe ${tag || "#10"} · due ${formatDate(expected)}`);
});
