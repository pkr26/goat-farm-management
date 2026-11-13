import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import type { FeedingPlanOut, PlanLineOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FeedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const breeding: PlanLineOut = {
  line_id: "BREEDING:A:ALL", allocation_id: "BREEDING:A", segment: "ALL", bucket: "BREEDING",
  recipe_code: "A", recipe_name: "Breeding ration", heads: 1, kg_per_head: 1, daily_kg: 1,
  basis: "flat", shifts: [{ shift: "MORNING", kg: 1, time: "06:30" }],
};
const resting: PlanLineOut = {
  ...breeding, line_id: "RESTING:B:ALL", allocation_id: "RESTING:B", bucket: "RESTING",
  recipe_code: "B", recipe_name: "Resting ration", heads: 9, kg_per_head: 2, daily_kg: 18,
  shifts: [{ shift: "MORNING", kg: 18, time: "06:30" }],
};

const separatedQuantities = [
  ["Breeding ration", "Shared allocation recorded 1.0 / 1.0 kg"],
  ["Resting ration", "Shared allocation recorded 0.0 / 18.0 kg"],
] as const;

it.each([
  { description: "canonical allocation IDs", rows: [breeding, resting], quantities: separatedQuantities },
  { description: "a repeated public allocation ID on distinct physical rations", rows: [
    { ...breeding, allocation_id: "legacy-allocation-1" },
    { ...resting, allocation_id: "legacy-allocation-1" },
  ], quantities: separatedQuantities },
  { description: "distinct segment allocation IDs for one physical ration", rows: [
    { ...breeding, line_id: "BREEDING:A:sex:F", allocation_id: "legacy-doe-ration", segment: "FEMALE" as const, recipe_name: "Doe ration" },
    { ...breeding, line_id: "BREEDING:A:sex:M", allocation_id: "legacy-buck-ration", segment: "MALE" as const, recipe_name: "Buck ration", kg_per_head: 2, daily_kg: 2, shifts: [{ shift: "MORNING", kg: 2, time: "06:30" }] },
  ], quantities: [
    ["Doe ration", "Shared allocation recorded 1.0 / 3.0 kg · shared across 2 segments"],
    ["Buck ration", "Shared allocation recorded 1.0 / 3.0 kg · shared across 2 segments"],
  ] },
])("keeps bucket-and-recipe quantities truthful with $description", async ({ rows, quantities }) => {
  const plan: FeedingPlanOut = {
    lines: rows, records: [], records_total: 0, records_limit: 200,
    dispensed_totals: [{ bucket: "BREEDING", recipe_code: "A", shift: "MORNING", qty_kg: 1 }],
  };
  server.use(
    http.get("/api/feeding/plan", () => HttpResponse.json(plan)),
    http.get("/api/feeding/records", () => HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 })),
    http.get("/api/feeding/recipes", () => HttpResponse.json({ recipes: [], allocation: [] })),
  );
  renderWithProviders(<FeedingPage />);
  for (const [name, quantity] of quantities) {
    const titles = await screen.findAllByText(new RegExp(name));
    const title = titles.find((element) => element.tagName === "P");
    expect(title).toBeDefined();
    expect(title!.parentElement!).toHaveTextContent(quantity);
  }
}, 60_000);
