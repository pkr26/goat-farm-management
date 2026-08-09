/**
 * REGRESSION — the kg/head confirmation echoed the raw typed value while the
 * API quantises feed quantities to 0.001 kg with ROUND_HALF_UP, so the toast
 * reported a ration that was never stored.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FeedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const LINE_BREEDING = {
  bucket: "BREEDING",
  recipe_code: "LACTATING_60_40",
  recipe_name: "Lactating 60/40",
  heads: 20,
  kg_per_head: 1,
  daily_kg: 20,
  shifts: [
    { shift: "MORNING", pct: 40, kg: 8, time: "6:30 AM" },
    { shift: "AFTERNOON", pct: 20, kg: 4, time: "1:30 PM" },
    { shift: "NIGHT", pct: 40, kg: 8, time: "7:30 PM" },
  ],
};

describe("FeedingPage kg/head confirmation", () => {
  let savedKg: number | null;

  beforeEach(() => {
    vi.mocked(toast.success).mockClear();
    savedKg = null;
    server.use(
      permissionsHandler(["feeding.view", "feeding.manage"]),
      http.get("/api/feeding/plan", () =>
        HttpResponse.json({
          lines: [LINE_BREEDING],
          records: [],
          records_total: 0,
          records_limit: 200,
          dispensed_totals: [],
        }),
      ),
      http.get("/api/feeding/records", () =>
        HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/feeding/recipes", () =>
        HttpResponse.json({ recipes: [], allocation: [] }),
      ),
      // The endpoint answers 204, so the confirmation can only be truthful by
      // applying the server's own rounding to the typed value.
      http.post("/api/feeding/settings", async ({ request }) => {
        const body = (await request.json()) as { daily_kg_per_head: number };
        savedKg = body.daily_kg_per_head;
        return new HttpResponse(null, { status: 204 });
      }),
    );
  });

  async function saveRation(typed: string) {
    const user = userEvent.setup();
    renderWithProviders(<FeedingPage />);
    await screen.findByText("Lactating 60/40");

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, typed);
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(savedKg).not.toBeNull());
  }

  it("reports the quantised ration, not the raw keystrokes", async () => {
    await saveRation("1.2345");
    expect(savedKg).toBe(1.2345);
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Saved 1.235 kg/head for BREEDING."),
    );
  });

  it("rounds half up exactly as the server does, not as the binary double does", async () => {
    // (1.0005).toFixed(3) is "1.000" — the double sits just below the tie —
    // while Decimal("1.0005") rounds half up to 1.001.
    await saveRation("1.0005");
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Saved 1.001 kg/head for BREEDING."),
    );
  });

  it("leaves a value already at storage precision untouched", async () => {
    await saveRation("1.8");
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Saved 1.8 kg/head for BREEDING."),
    );
  });
});
