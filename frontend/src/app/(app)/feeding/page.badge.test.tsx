/**
 * Feeding completion is allocation-specific: bucket + recipe + shift.
 * Bucket volume remains visible, but cannot make the plan complete by itself.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { farmToday } from "@/lib/format";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/msw-server";

import FeedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const LINE_A = {
  bucket: "BREEDING",
  recipe_code: "LACTATING_60_40",
  recipe_name: "Lactating 60/40",
  heads: 20,
  kg_per_head: 1.5,
  daily_kg: 30,
  shifts: [
    { shift: "MORNING", pct: 40, kg: 12, time: "6:30 AM" },
    { shift: "AFTERNOON", pct: 20, kg: 6, time: "1:30 PM" },
    { shift: "NIGHT", pct: 40, kg: 12, time: "7:30 PM" },
  ],
};
const LINE_B = {
  bucket: "BREEDING",
  recipe_code: "FLUSH_70_30",
  recipe_name: "Flush 70/30",
  heads: 10,
  kg_per_head: 1.5,
  daily_kg: 15,
  shifts: [
    { shift: "MORNING", pct: 40, kg: 6, time: "6:30 AM" },
    { shift: "AFTERNOON", pct: 20, kg: 3, time: "1:30 PM" },
    { shift: "NIGHT", pct: 40, kg: 6, time: "7:30 PM" },
  ],
};

const RECIPES_HANDLER = http.get("/api/feeding/recipes", () =>
  HttpResponse.json({ recipes: [], allocation: [] }),
);

function record(
  id: number,
  recipe_code: string,
  shift: string,
  qty_kg: number,
) {
  return {
    id,
    date: farmToday(),
    bucket: "BREEDING",
    recipe_code,
    shift,
    qty_kg,
  };
}

function planWith(records: ReturnType<typeof record>[]) {
  return http.get("/api/feeding/plan", () =>
    HttpResponse.json({
      lines: [LINE_A, LINE_B],
      records,
      records_total: records.length,
      records_limit: 200,
      dispensed_totals: records.map(({ bucket, recipe_code, shift, qty_kg }) => ({
        bucket,
        recipe_code,
        shift,
        qty_kg,
      })),
    }),
  );
}

function rowOf(text: string): HTMLElement {
  const row = screen.getByText(text).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe("FeedingPage allocation completion", () => {
  it("does not mark completion when bucket volume is all on one recipe and shift", async () => {
    server.use(
      planWith([record(1, "LACTATING_60_40", "MORNING", 45)]),
      RECIPES_HANDLER,
    );
    renderWithProviders(<FeedingPage />);

    await screen.findByText("Lactating 60/40");
    expect(within(rowOf("Lactating 60/40")).queryByText("Done")).not.toBeInTheDocument();
    expect(within(rowOf("Flush 70/30")).queryByText("Done")).not.toBeInTheDocument();
    expect(screen.getByText("45.0 / 45.0 kg recorded")).toBeInTheDocument();
    expect(screen.getByText("0/2 rations")).toBeInTheDocument();
  });

  it("marks each ration and bucket complete only after every exact shift", async () => {
    server.use(
      planWith([
        record(1, "LACTATING_60_40", "MORNING", 12),
        record(2, "LACTATING_60_40", "AFTERNOON", 6),
        record(3, "LACTATING_60_40", "NIGHT", 12),
        record(4, "FLUSH_70_30", "MORNING", 6),
        record(5, "FLUSH_70_30", "AFTERNOON", 3),
        record(6, "FLUSH_70_30", "NIGHT", 6),
      ]),
      RECIPES_HANDLER,
    );
    renderWithProviders(<FeedingPage />);

    await screen.findByText("Lactating 60/40");
    expect(within(rowOf("Lactating 60/40")).getByText("Done")).toBeInTheDocument();
    expect(within(rowOf("Flush 70/30")).getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("complete")).toBeInTheDocument();
  });
});
