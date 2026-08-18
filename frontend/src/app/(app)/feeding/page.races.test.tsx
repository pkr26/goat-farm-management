// REGRESSION TEST — bug fixed; this test pins the fix.
//
// Duplicate stock write on the dispensing dialog.
//
// The submit button's only in-flight guard was react-hook-form's
// `isSubmitting`. The page-header "Record dispensing" trigger stayed clickable
// throughout, because its only `disabled` was `recipeOptions.size === 0` —
// statically unreachable, since the DRY_ROUGHAGE virtual code is inserted
// unconditionally. Clicking it ran `reset({...})`, and react-hook-form's reset
// emits `isSubmitting: false` unconditionally, DESTROYING the guard mid-flight.
//
// /api/feeding/dispense is on the idempotency allowlist, so an identical
// resubmit would have coalesced onto the same Idempotency-Key. The duplicate
// existed only because that same reset re-seeded bucket/recipe/qty from
// `payload.lines[0]`, changing the request body enough to produce a different
// logical signature — a registry miss, a fresh key, and a second commit
// decrementing ingredient stock twice, against two different buckets.
//
// The guard now lives in a ref (useSingleFlight) that reset() cannot reach.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FeedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

const SHIFTS = [
  { shift: "MORNING", pct: 40, kg: 8, time: "6:30 AM" },
  { shift: "AFTERNOON", pct: 20, kg: 4, time: "1:30 PM" },
  { shift: "NIGHT", pct: 40, kg: 8, time: "7:30 PM" },
];

const LINE_BREEDING = {
  bucket: "BREEDING",
  recipe_code: "LACTATING_60_40",
  recipe_name: "Lactating 60/40",
  heads: 20,
  kg_per_head: 1.0,
  daily_kg: 20,
  shifts: SHIFTS,
};

const RECIPES_PAYLOAD = {
  recipes: [
    { id: 1, code: "LACTATING_60_40", name: "Lactating 60/40", description: null, lines: [] },
  ],
  allocation: [],
};

describe("FeedingPage dispensing — in-flight double-submit", () => {
  let dispenseCalls: number;
  let releaseDispense: (() => void) | undefined;

  beforeEach(() => {
    dispenseCalls = 0;
    releaseDispense = undefined;
    const parked = new Promise<void>((resolve) => {
      releaseDispense = resolve;
    });
    server.use(
      http.get("/api/feeding/plan", () =>
        HttpResponse.json({
          lines: [LINE_BREEDING],
          records: [],
          records_total: 0,
          records_limit: 200,
          dispensed_totals: [],
        }),
      ),
      http.get("/api/feeding/recipes", () => HttpResponse.json(RECIPES_PAYLOAD)),
      http.post("/api/feeding/dispense", async () => {
        dispenseCalls += 1;
        // Hold the write open so the "in flight" window is observable.
        await parked;
        return HttpResponse.json({ id: 10 }, { status: 201 });
      }),
    );
  });

  it("locks the header trigger while a dispense is in flight, so reset() cannot destroy the guard", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FeedingPage />);
    expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();

    const trigger = screen.getByRole("button", { name: "Record dispensing" });
    await user.click(trigger);
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Quantity (kg) *"), "500");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    expect(within(dialog).getByRole("button", { name: "Recording…" })).toBeDisabled();

    // The operator gives up on the stalled save and dismisses the dialog. That
    // is deliberately still allowed — blocking it on an in-flight write is what
    // made sibling dialogs unclosable — so the header trigger becomes reachable
    // again while the POST is still on the wire.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // The load-bearing assertion. Before the fix this trigger was enabled, and
    // clicking it re-seeded the form from payload.lines[0] and cleared
    // `isSubmitting` — arming a second, differently-bodied POST that the
    // idempotency registry could not recognise as the same logical write.
    const trigger2 = screen.getByRole("button", { name: "Record dispensing" });
    expect(trigger2).toBeDisabled();

    // Trying anyway must neither reopen the form nor start a second write.
    await user.click(trigger2);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(dispenseCalls).toBe(1);

    releaseDispense?.();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Record dispensing" })).toBeEnabled(),
    );
    expect(dispenseCalls).toBe(1);
  });
});
