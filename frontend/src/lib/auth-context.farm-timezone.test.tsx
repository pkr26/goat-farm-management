/**
 * Farm timezone flows from the generated FarmOut contract: selecting a farm
 * without an explicit timezone argument adopts the cached entry's zone
 * (required on the model — the historical `& { timezone?: string }` widen
 * hid contract drift). Asserted through farmToday(), the module's external
 * store.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { vi, describe, expect, it, beforeAll } from "vitest";

import { useAuth } from "@/lib/auth-context";
import { farmToday } from "@/lib/format";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

/** 03:00 UTC is 08:30 in Kolkata (same date) but 20:00 the previous day in
 * Phoenix — the two farms' calendars provably differ at this instant. */
const SAMPLE_INSTANT = new Date("2026-09-08T03:00:00Z");

function Probe() {
  const { farms, selectFarm } = useAuth();
  return (
    <div>
      <span data-testid="farm-today">{farmToday(SAMPLE_INSTANT)}</span>
      {farms.length > 1 && (
        <button type="button" onClick={() => selectFarm(farms[1].id)}>
          switch farm
        </button>
      )}
    </div>
  );
}

describe("selectFarm adopts the cached entry's timezone", () => {
  beforeAll(() => {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 1, name: "Solapur farm", location: "Solapur", timezone: "Asia/Kolkata", role: null },
          {
            id: 2,
            name: "Phoenix farm",
            location: "Arizona",
            timezone: "America/Phoenix",
            role: null,
          },
        ]),
      ),
    );
  });

  it("switches the business calendar when the farm selection changes", async () => {
    renderWithProviders(<Probe />);
    // Bootstrap picks the first farm: Kolkata's calendar day for the instant.
    await waitFor(() => expect(screen.getByTestId("farm-today")).toHaveTextContent("2026-09-08"));

    await userEvent.setup().click(screen.getByRole("button", { name: "switch farm" }));
    // No explicit timezone passed: the zone comes from the typed farm entry.
    await waitFor(() => expect(screen.getByTestId("farm-today")).toHaveTextContent("2026-09-07"));
  });
});
