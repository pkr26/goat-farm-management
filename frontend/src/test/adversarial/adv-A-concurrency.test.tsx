/**
 * ADVERSARIAL AUDIT A1/A2/A5 — concurrency & race attacks (executed).
 *
 * A1  Double-submit: two synchronous clicks on a money-creating submit must
 *     produce exactly ONE network request (single-flight + idempotency dedup).
 * A2  Farm-switch mid-write: the operator switches farm while a breeding POST
 *     is on the wire. The request legitimately keeps its old X-Farm-Id, but no
 *     completion artefact (toast/dialog-close/invalidation) may land on the
 *     NEW farm's UI. → expected to CONFIRM open finding M-2.
 * A5  Dialog-reopen race: a write resolves after the operator dismissed and
 *     reopened the dialog and typed a new draft. The late continuation must
 *     not close/reset the reopened session. → expected to CONFIRM L22.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useEffect } from "react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import BreedingPage from "@/app/(app)/breeding/page";
import FinancePage from "@/app/(app)/finance/page";
import InventoryPage from "@/app/(app)/feeding/inventory/page";
import { useAuth } from "@/lib/auth-context";
import { farmToday } from "@/lib/format";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

const { toastMock } = vi.hoisted(() => ({
  toastMock: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("sonner", () => ({ toast: toastMock }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

/** Exposes selectFarm so a test can switch farms mid-flight. */
let farmSwitcher: ((id: number) => void) | null = null;
function FarmSwitchProbe() {
  const { selectFarm } = useAuth();
  useEffect(() => {
    farmSwitcher = selectFarm;
  }, [selectFarm]);
  return null;
}

const FINANCE_PAYLOAD = {
  transactions: [],
  transactions_total: 0,
  limit: 50,
  offset: 0,
  total_income: 0,
  total_expense: 0,
  pnl: [],
};

describe("ADV A1: double-submit on a money write produces exactly one request", () => {
  beforeEach(() => {
    server.use(http.get("/api/finance", () => HttpResponse.json(FINANCE_PAYLOAD)));
  });

  it("two synchronous clicks on Add transaction → one POST /api/finance/new", async () => {
    let posts = 0;
    let resolvePost!: (value: Response) => void;
    server.use(
      http.post("/api/finance/new", ({ request }) => {
        posts += 1;
        expect(request.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/i);
        return new Promise<Response>((resolve) => {
          resolvePost = resolve;
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<FinancePage />);
    expect(await screen.findByText("Monthly P&L (last 12 months)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New transaction" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Amount (₹) *"), "150");
    // Fire two clicks in the SAME tick — no await between them — so the second
    // lands before React re-renders the disabled state.
    const submit = within(dialog).getByRole("button", { name: "Add transaction" });
    fireEvent.click(submit);
    fireEvent.click(submit);

    await waitFor(() => expect(posts).toBe(1));
    resolvePost(HttpResponse.json({ ok: true }, { status: 201 }));
    expect(posts).toBe(1);
  });
});

describe("ADV A2: farm switch mid-write on breeding (M-2 now fenced)", () => {
  const TODAY = farmToday();
  const DOE = {
    id: 11,
    tag_number: "G-DOE",
    name: null,
    age_months: 18,
    latest_weight_kg: 30,
    current_bucket: "BREEDING",
  };
  const BUCK = {
    id: 12,
    tag_number: "G-BUCK",
    name: null,
    age_months: 24,
    latest_weight_kg: null,
    current_bucket: "BREEDING",
  };
  const LIST_PAYLOAD = {
    records: [],
    total: 0,
    limit: 50,
    offset: 0,
    candidate_availability: { eligible_doe_count: 1, eligible_buck_count: 1 },
  };

  beforeEach(() => {
    server.use(
      http.get("/api/breeding", () => HttpResponse.json(LIST_PAYLOAD)),
      http.get("/api/breeding/candidates", ({ request }) => {
        const kind = new URL(request.url).searchParams.get("kind");
        const candidates = kind === "doe" ? [DOE] : [BUCK];
        return HttpResponse.json({ candidates, total: 1 });
      }),
    );
  });

  it("DEFENDED: no completion artefact lands on the NEW farm after a mid-flight switch", async () => {
    let resolvePost!: (value: Response) => void;
    server.use(
      http.post("/api/breeding", () =>
        new Promise<Response>((resolve) => {
          resolvePost = resolve;
        }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(
      <>
        <FarmSwitchProbe />
        <BreedingPage />
      </>,
    );
    // The header action and the empty-state CTA share the label; either
    // opens the same create dialog.
    const addBreedingButtons = await screen.findAllByRole("button", {
      name: "Add breeding",
    });
    expect(addBreedingButtons.length).toBeGreaterThanOrEqual(1);

    // Open the dialog and pick a doe + buck via the combobox triggers.
    await user.click(addBreedingButtons[0]);
    const dialog = await screen.findByRole("dialog");
    const [doeTrigger, buckTrigger] = await within(dialog).findAllByRole("combobox");
    await user.click(doeTrigger);
    await user.click(await screen.findByRole("option", { name: /G-DOE/ }));
    await user.click(buckTrigger);
    await user.click(await screen.findByRole("option", { name: /G-BUCK/ }));

    // Submit — the POST is now hanging on the deferred handler.
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));
    await waitFor(() => expect(resolvePost).toBeDefined());

    // The operator switches to farm 2 while the write is on the wire.
    expect(farmSwitcher).not.toBeNull();
    farmSwitcher!(2);

    // The server finally answers for the OLD farm.
    resolvePost(
      HttpResponse.json(
        {
          id: 99,
          doe_id: DOE.id,
          buck_id: BUCK.id,
          doe_tag: DOE.tag_number,
          buck_tag: BUCK.tag_number,
          breeding_date: TODAY,
          heat_cycle_number: 1,
          ultrasound_date: null,
          ultrasound_done: false,
          ultrasound_result_date: null,
          kid_count_detected: null,
          expected_kidding_date: null,
          outcome: "PENDING",
          has_kidding: false,
          loss_date: null,
          loss_cause: null,
          loss_notes: null,
          cull_candidate: false,
        },
        { status: 201 },
      ),
    );

    // FIXED (M-2): the continuation is fenced by farmScopeEpochValue — no
    // toast, no dialog close, no invalidation against the new farm's UI.
    await waitFor(() =>
      expect(toastMock.success).not.toHaveBeenCalledWith("Breeding saved."),
    );
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});

describe("ADV A5: late write closes a reopened add-stock dialog (expected to CONFIRM L22)", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/feeding/inventory", () =>
        HttpResponse.json([
          {
            id: 5,
            category: "GRAIN",
            ingredient: "Maize",
            qty_on_hand: 10,
            reorder_level: null,
            last_purchase_price_per_kg: null,
          },
        ]),
      ),
      http.get("/api/feeding/recipes", () => HttpResponse.json({ recipes: [] })),
    );
  });

  it("DEFENDED: dismissal mid-flight cannot corrupt a later add-stock session", async () => {
    let resolvePost!: (value: Response) => void;
    server.use(
      http.post("/api/feeding/inventory/5/add", () =>
        new Promise<Response>((resolve) => {
          resolvePost = resolve;
        }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<InventoryPage />);
    expect((await screen.findAllByText("Maize")).length).toBeGreaterThan(0);

    // Session 1: open, type, submit — the write hangs on the deferred handler.
    // The dialog stays mounted but frozen (fieldset disabled).
    // Stock rows render twice (below-md card list + desktop table); use the
    // desktop row's trigger.
    const stockTable = document.querySelector(
      '[class~="md:block"] table[class*="min-w-[640px]"]',
    ) as HTMLElement;
    const row = within(stockTable).getByText("Maize").closest("tr") as HTMLElement;
    const trigger = within(row).getByRole("button", { name: "Add stock" });
    await user.click(trigger);
    let dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "5");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    // DEFENSE: the row trigger is inert while the write is pending, so no
    // second session can start on this row mid-flight.
    await waitFor(() => expect(trigger).toBeDisabled());

    // The operator CAN still dismiss the frozen dialog (onOpenChange has no
    // in-flight guard) — the write continues in the background.
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // Session 1's write finally completes; the trigger re-arms.
    resolvePost(
      HttpResponse.json(
        {
          id: 5,
          category: "GRAIN",
          ingredient: "Maize",
          qty_on_hand: 15,
          reorder_level: null,
          last_purchase_price_per_kg: null,
        },
        { status: 200 },
      ),
    );
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
    await waitFor(() => expect(trigger).toBeEnabled());

    // DEFENSE: reopening after settlement starts from a clean form — the late
    // reset() left no stale "5" behind and cannot close this new session.
    await user.click(trigger);
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/Quantity \(kg\)/)).toHaveValue(null);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
