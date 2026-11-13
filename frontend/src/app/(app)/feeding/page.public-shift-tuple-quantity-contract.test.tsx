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

it.each([
  { description: "canonical codes", firstBucket: "BREEDING", firstRecipe: "A", secondBucket: "RESTING", secondRecipe: "B" },
  { description: "accepted string codes containing a tuple delimiter", firstBucket: "BREEDING\u0000A", firstRecipe: "B", secondBucket: "BREEDING", secondRecipe: "A\u0000B" },
])("keeps recorded shift quantities on their exact physical ration with $description", async ({ firstBucket, firstRecipe, secondBucket, secondRecipe }) => {
  const first: PlanLineOut = {
    line_id: "first-segment", allocation_id: "first-allocation", segment: "ALL", bucket: firstBucket,
    recipe_code: firstRecipe, recipe_name: "First physical ration", heads: 1, kg_per_head: 1, daily_kg: 1,
    basis: "flat", shifts: [{ shift: "MORNING", kg: 1, time: "06:30" }],
  };
  const second: PlanLineOut = {
    ...first, line_id: "second-segment", allocation_id: "second-allocation", bucket: secondBucket,
    recipe_code: secondRecipe, recipe_name: "Second physical ration", heads: 9, kg_per_head: 2, daily_kg: 18,
    shifts: [{ shift: "MORNING", kg: 18, time: "06:30" }],
  };
  const plan: FeedingPlanOut = {
    lines: [first, second], records: [], records_total: 0, records_limit: 200,
    dispensed_totals: [{ bucket: firstBucket, recipe_code: firstRecipe, shift: "MORNING", qty_kg: 1 }],
  };
  server.use(
    http.get("/api/feeding/plan", () => HttpResponse.json(plan)),
    http.get("/api/feeding/records", () => HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 })),
    http.get("/api/feeding/recipes", () => HttpResponse.json({ recipes: [], allocation: [] })),
  );
  renderWithProviders(<FeedingPage />);
  for (const [name, quantity] of [["First physical ration", "Shared allocation recorded 1.0 / 1.0 kg"],
    ["Second physical ration", "Shared allocation recorded 0.0 / 18.0 kg"]]) {
    const labels = await screen.findAllByText(new RegExp(name));
    const label = labels.find((element) => element.tagName === "P");
    expect(label).toBeDefined();
    expect(label!.parentElement!).toHaveTextContent(quantity);
  }
}, 60_000);
