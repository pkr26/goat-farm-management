import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import FeedingPage from "./page";

const nav = vi.hoisted(() => ({ search: "offset=10000", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: nav.replace, prefetch: vi.fn() }),
  usePathname: () => "/feeding", useSearchParams: () => new URLSearchParams(nav.search), useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

it("keeps a large history ledger at the inclusive server offset ceiling while showing its real total", async () => {
  const offsets: number[] = [];
  const record = { id: 1, date: "2026-10-08", shift: "MORNING", bucket: "BREEDING", recipe_code: "A", qty_kg: 1, created_at: "2026-10-08T06:30:00Z" };
  server.use(
    http.get("/api/feeding/plan", () => HttpResponse.json({ lines: [], records: [], records_total: 0, records_limit: 200, dispensed_totals: [] })),
    http.get("/api/feeding/recipes", () => HttpResponse.json({ recipes: [], allocation: [] })),
    http.get("/api/feeding/records", ({ request }) => {
      const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
      offsets.push(offset);
      if (offset > 10_000) return HttpResponse.json({ detail: [{ loc: ["query", "offset"], msg: "Input should be less than or equal to 10000", type: "less_than_equal" }] }, { status: 422 });
      return HttpResponse.json({ records: [record], total: 10_051, limit: 50, offset });
    }),
  );
  const view = renderWithProviders(<FeedingPage />);
  await view.waitForAuthIdle();
  const pagination = await screen.findByRole("navigation", { name: "dispensing records pagination" });
  await waitFor(() => expect(offsets).toContain(10_000));
  expect(within(pagination).getByText("Showing 10001–10050 of 10051 dispensing records")).toBeInTheDocument();
  const next = within(pagination).getByRole("button", { name: "Next" });
  expect(next).toBeDisabled();
  fireEvent.click(next);
  expect(nav.replace).not.toHaveBeenCalled();
  expect(offsets.every(offset => offset <= 10_000)).toBe(true);
});
