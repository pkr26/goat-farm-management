import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import type { KidEntryOut, KiddingListOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import KiddingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/kidding", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

it.each([
  { tag: "", links: true }, { tag: "", links: false },
  { tag: null, links: true }, { tag: null, links: false },
  { tag: "G-101", links: true }, { tag: "G-101", links: false },
])("keeps the recorded child's identity readable ($tag, links=$links)", async ({ tag, links }) => {
  server.use(permissionsHandler(["kidding.view", ...(links ? ["animals.view"] : [])]));
  const child: KidEntryOut = {
    id: 1, tag, sex: "F", birth_weight: 2.4, status: "ALIVE",
    mortality_reported_at: null, colostrum_within_2h: null, navel_dipped: null,
    dam_rejected: false, animal_id: 55, created_at: "2026-01-01T00:00:00Z",
  };
  const payload: KiddingListOut = {
    upcoming: [], upcoming_total: 0, upcoming_limit: 25, upcoming_offset: 0,
    overdue: [], overdue_total: 0, overdue_limit: 25, overdue_offset: 0,
    records: [{ id: 21, doe_id: 12, date: "2026-01-01", breeding_record_id: 14,
      ease: "NORMAL", parity: null, placenta_passed: null, mastitis_suspected: false,
      notes: "Child identity record", created_at: "2026-01-01T00:00:00Z", kids: [child], doe_tag: "G-012" }],
    total: 1, limit: 50, offset: 0,
  };
  server.use(http.get("/api/kidding", () => HttpResponse.json(payload)));
  renderWithProviders(<KiddingPage />);
  const row = (await screen.findByText("Child identity record", { selector: "td" })).closest("tr");
  expect(row).not.toBeNull();
  const cell = within(row as HTMLElement).getAllByRole("cell")[3];
  const name = tag || "kid";
  expect(cell).toHaveTextContent(`${name} (Female, Alive)`);
  if (links) expect(within(cell).getByRole("link", { name })).toHaveAttribute("href", "/animals/55");
  else expect(within(cell).queryByRole("link")).not.toBeInTheDocument();
});
