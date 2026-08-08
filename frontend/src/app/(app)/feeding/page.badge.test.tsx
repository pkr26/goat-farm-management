/**
 * Feeding plan "done" badge: a bucket split across several
 * recipe lines (each with its own daily_kg) must only show "done" once the
 * dispensed total covers the SUM of that bucket's lines — never when a
 * single line's daily_kg is reached.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { farmToday } from "@/lib/format";

import FeedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const SHIFTS = [
  { shift: "MORNING", pct: 40, kg: 8, time: "6:30 AM" },
  { shift: "AFTERNOON", pct: 20, kg: 4, time: "1:30 PM" },
  { shift: "NIGHT", pct: 40, kg: 8, time: "7:30 PM" },
];

/** BREEDING split into two recipe lines: 30 kg + 15 kg = 45 kg planned. */
const LINE_A = {
  bucket: "BREEDING",
  recipe_code: "LACTATING_60_40",
  recipe_name: "Lactating 60/40",
  heads: 20,
  kg_per_head: 1.5,
  daily_kg: 30,
  shifts: SHIFTS,
};
const LINE_B = {
  bucket: "BREEDING",
  recipe_code: "FLUSH_70_30",
  recipe_name: "Flush 70/30",
  heads: 10,
  kg_per_head: 1.5,
  daily_kg: 15,
  shifts: SHIFTS,
};

function planWith(dispensedKg: number) {
  return http.get("/api/feeding/plan", () =>
    HttpResponse.json({
      lines: [LINE_A, LINE_B],
      records:
        dispensedKg > 0
          ? [
              {
                id: 1,
                date: farmToday(),
                shift: "MORNING",
                bucket: "BREEDING",
                recipe_code: "LACTATING_60_40",
                qty_kg: dispensedKg,
              },
            ]
          : [],
    }),
  );
}

const RECIPES_HANDLER = http.get("/api/feeding/recipes", () =>
  HttpResponse.json({ recipes: [], allocation: [] }),
);

function rowOf(text: string): HTMLElement {
  const row = screen.getByText(text).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe("FeedingPage done badge per-bucket aggregation (7-3)", () => {
  it("no 'done' badge when only the first line's daily_kg is dispensed", async () => {
    server.use(planWith(30), RECIPES_HANDLER);
    renderWithProviders(<FeedingPage />);

    // 30 kg dispensed covers line A (30 kg) but not the bucket (45 kg).
    await screen.findByText("Lactating 60/40");
    const rowA = rowOf("Lactating 60/40");
    const rowB = rowOf("Flush 70/30");
    expect(within(rowA).queryByText("done")).not.toBeInTheDocument();
    expect(within(rowB).queryByText("done")).not.toBeInTheDocument();
  });

  it("'done' on both lines once the bucket's summed plan is dispensed", async () => {
    server.use(planWith(45), RECIPES_HANDLER);
    renderWithProviders(<FeedingPage />);

    await screen.findByText("Lactating 60/40");
    const rowA = rowOf("Lactating 60/40");
    const rowB = rowOf("Flush 70/30");
    expect(within(rowA).getByText("done")).toBeInTheDocument();
    expect(within(rowB).getByText("done")).toBeInTheDocument();
  });
});
