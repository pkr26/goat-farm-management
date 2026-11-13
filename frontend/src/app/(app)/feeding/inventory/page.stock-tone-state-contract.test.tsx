import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import InventoryPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding/inventory", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));

it("uses the low-stock semantic state for warning tone on both stock surfaces", async () => {
  const items = [
    { ingredient: "Threshold reached", qty_on_hand: 5, reorder_level: 5, low: true },
    { ingredient: "Zero threshold reached", qty_on_hand: 0, reorder_level: 0, low: true },
    { ingredient: "Stock above threshold", qty_on_hand: 6, reorder_level: 5, low: false },
    { ingredient: "Threshold absent", qty_on_hand: 0, reorder_level: null, low: false },
  ];
  server.use(http.get("/api/feeding/inventory", () => HttpResponse.json(items.map((item, index) => ({
    id: index + 1, ingredient: item.ingredient, category: "CONCENTRATE", unit: "kg",
    qty_on_hand: item.qty_on_hand, reorder_level: item.reorder_level, last_purchase_price_per_kg: 0,
  })))));
  renderWithProviders(<InventoryPage />);
  const table = await screen.findByRole("table");
  for (const item of items) {
    const row = within(table).getByRole("row", { name: new RegExp(item.ingredient) });
    const name = screen.getAllByText(item.ingredient).find((element) => element.tagName === "SPAN")!;
    const mobileCard = name.parentElement!.parentElement!;
    for (const surface of [row, mobileCard]) {
      expect(surface).toHaveAttribute("data-low", String(item.low));
      if (item.low) expect(within(surface).getByText("low")).toBeInTheDocument();
      else expect(within(surface).queryByText("low")).not.toBeInTheDocument();
    }
  }
});
