import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import type { FeedingRecordOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FeedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const record: FeedingRecordOut = { id: 1, date: "2026-08-01", shift: "MORNING", bucket: "BREEDING",
  recipe_code: "", qty_kg: 1, created_at: "2026-08-01T06:30:00Z" };

function serve(records: FeedingRecordOut[], dates: string[] = []) {
  server.use(
    http.get("/api/feeding/plan", () => HttpResponse.json({ lines: [], records,
      records_total: records.length, records_limit: 200, dispensed_totals: [] })),
    http.get("/api/feeding/recipes", () => HttpResponse.json({ recipes: [], allocation: [] })),
    http.get("/api/feeding/records", ({ request }) => {
      const params = new URL(request.url).searchParams;
      dates.push(params.get("date_from") ?? "", params.get("date_to") ?? "");
      return HttpResponse.json({ records, total: records.length, limit: 50, offset: 0 });
    }),
  );
}

it("normalizes native expanded-year date edits before querying the bounded server date contract", async () => {
  const requestedDates: string[] = [];
  serve([], requestedDates);
  renderWithProviders(<FeedingPage />);
  await screen.findByText("No dispensing records in this date range.");
  for (const label of ["From date", "To date"]) {
    const field = screen.getByLabelText(label) as HTMLInputElement;
    // HTML date inputs allow years above9999 through the public native setter.
    // Python/date and the authored URL parser accept the bounded4-digit contract.
    const native = document.createElement("input");
    native.type = "date";
    native.value = "10000-01-01";
    expect(native.value).toBe("10000-01-01");
    fireEvent.change(field, { target: { value: native.value } });
    expect(field).toHaveValue("");
  }
  await act(async () => { await Promise.resolve(); });
  expect(requestedDates).not.toContain("10000-01-01");
});

it("uses the missing-recipe marker for a valid public blank response on the recent log and both history surfaces", async () => {
  // The public output DTO accepts a plain string. Persisted writers/DB reject
  // blank codes, but public response/cache consumers still need a readable gap.
  serve([record]);
  renderWithProviders(<FeedingPage />);
  await waitFor(() => expect(screen.getAllByRole("table")).toHaveLength(2));
  for (const table of screen.getAllByRole("table")) {
    const row = within(table).getAllByRole("row")[1];
    const recipeColumn = within(table).getAllByRole("columnheader").findIndex((cell) => cell.textContent === "Recipe");
    expect(within(row).getAllByRole("cell")[recipeColumn]).toHaveTextContent("—");
  }
  expect(screen.getByText(/Morning · Breeding · —/)).toBeInTheDocument();
});

it("preserves known recipe codes and the existing null marker", async () => {
  serve([{ ...record, recipe_code: null }, { ...record, id: 2, recipe_code: "MAINTENANCE_75_25", qty_kg: 2 }]);
  renderWithProviders(<FeedingPage />);
  await waitFor(() => expect(screen.getAllByRole("table")).toHaveLength(2));
  for (const table of screen.getAllByRole("table")) {
    expect(within(table).getByRole("cell", { name: "—" })).toBeInTheDocument();
    expect(within(table).getByRole("cell", { name: "MAINTENANCE_75_25" })).toBeInTheDocument();
  }
});
