import { fireEvent, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import type { TransactionOut } from "@/api/generated/models/transactionOut";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FinancePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

it.each([
  { tag: "", label: "Animal #7" },
  { tag: null, label: "Animal #7" },
  { tag: "G-007", label: "G-007" },
].flatMap((entry) => [
  { ...entry, canBrowseAnimals: true },
  { ...entry, canBrowseAnimals: false },
]))("identifies the linked animal with tag $tag when animal browsing is $canBrowseAnimals", async ({ tag, label, canBrowseAnimals }) => {
  const transaction: TransactionOut = {
    id: 41, date: "2026-01-05", type: "INCOME", category: "ANIMAL_SALE",
    amount: 1500, notes: "Existing animal sale", related_animal_id: 7,
    animal_tag: tag, created_at: "2026-01-05T05:30:00Z",
    source_type: null, source_id: null, correction_of_id: null,
    voided_at: null, voided_by_id: null, void_reason: null,
  };
  server.use(
    permissionsHandler(["finance.view", "finance.manage", ...(canBrowseAnimals ? ["animals.view"] : [])]),
    http.get("/api/finance", () => HttpResponse.json({
      transactions: [transaction], transactions_total: 1, limit: 50, offset: 0,
      total_income: 1500, total_expense: 0, feed_stock_value: 0, pnl: [],
    })),
  );
  const view = renderWithProviders(<FinancePage />);
  try {
    await view.waitForAuthIdle();
    const row = (await screen.findAllByText(transaction.notes!)).find((node) => node.closest("tr"))!.closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Correct" }));
    const dialog = await screen.findByRole("dialog", { name: "Correct transaction #41" });
    const animal = canBrowseAnimals
      ? within(dialog).getByRole("button", { name: "Animal (optional)" })
      : within(dialog).getByLabelText("Linked animal");
    expect(animal).toHaveTextContent(label);
  } finally {
    view.unmount();
    view.queryClient.clear();
  }
});
