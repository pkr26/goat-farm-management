/**
 * New-batch dialog aria wiring (2026-09-30 fresh mutation campaign): the
 * origin market, transport hours, individual-weights and seller-history
 * fields must carry aria-invalid plus their own describedby alert when the
 * review rejects them.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import PurchasesPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/purchases",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

const BATCH_1 = {
  id: 5,
  date: "2026-01-05",
  supplier: "Sharma Goat Farm",
  count: 50,
  avg_age_months: 8,
  avg_weight_kg: 18.5,
  total_price: 150000,
  notes: "foundation stock",
  animals_created: 50,
  open_tasks: 3,
};

describe("PurchasesPage new-batch dialog aria wiring", () => {
  let postCalls: number;

  beforeEach(() => {
    postCalls = 0;
    server.use(
      permissionsHandler(["purchases.view", "purchases.manage"]),
      http.get("/api/purchases", () =>
        HttpResponse.json({ batches: [BATCH_1], total: 1, limit: 50, offset: 0 }),
      ),
      http.post("/api/purchases/new", () => {
        postCalls += 1;
        return HttpResponse.json({ ...BATCH_1, id: 7 }, { status: 201 });
      }),
    );
  });

  it("wires origin market, transport hours, weights and history to their alerts", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PurchasesPage />);
    await screen.findAllByText("Sharma Goat Farm");
    await user.click(screen.getByRole("button", { name: "New batch" }));
    const dialog = await screen.findByRole("dialog");

    const origin = within(dialog).getByLabelText(/origin market/i);
    const transport = within(dialog).getByLabelText(/transport/i);
    const history = within(dialog).getByLabelText(/health history/i);
    const weights = within(dialog).getByLabelText(/individual arrival weights/i);
    for (const el of [origin, transport, history, weights]) {
      expect(el).not.toHaveAttribute("aria-invalid");
    }

    // DOM caps the schema enforces alongside the wiring under test.
    expect(origin).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/supplier/i)).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/^notes/i)).toHaveAttribute("maxlength", "4000");
    expect(history).toHaveAttribute("maxlength", "4000");
    expect(history).toHaveAttribute("rows", "2");
    expect(weights).toHaveAttribute("rows", "3");
    // The input's own maxLength clips interactive typing: go over the caps
    // programmatically. Transport rejects negatives; two weight lines for a
    // count of one fails the plausibility refine.
    fireEvent.change(origin, { target: { value: "m".repeat(121) } });
    fireEvent.change(transport, { target: { value: "-1" } });
    fireEvent.change(history, { target: { value: "h".repeat(4_001) } });
    fireEvent.change(weights, { target: { value: "30.0\n31.0" } });
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));

    await waitFor(() =>
      expect(document.getElementById("purchase-origin-error")).not.toBeNull(),
    );
    expect(origin).toHaveAttribute("aria-invalid", "true");
    expect(origin).toHaveAttribute("aria-describedby", "purchase-origin-error");
    expect(transport).toHaveAttribute("aria-invalid", "true");
    expect(transport).toHaveAttribute("aria-describedby", "purchase-transport-error");
    expect(history).toHaveAttribute("aria-invalid", "true");
    expect(history.getAttribute("aria-describedby")).toMatch(/^purchase-history|^purchase-seller/);
    expect(weights).toHaveAttribute("aria-invalid", "true");
    expect(weights).toHaveAttribute("aria-describedby", "purchase-weights-error");
    expect(postCalls).toBe(0);
    expect(within(dialog).queryByText("Review purchase consequences")).not.toBeInTheDocument();
  });
});
