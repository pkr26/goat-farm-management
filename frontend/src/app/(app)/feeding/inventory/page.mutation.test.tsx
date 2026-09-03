/**
 * Feed inventory page — mutation-hardening suite. Targets the exact
 * branches the Stryker report flagged as surviving: the optional-price
 * preprocess (blank → null payload), the derived-expense ceilings, the
 * a11y wiring of the add-stock/mix error paragraphs (aria-invalid /
 * aria-describedby / element ids), in-flight submit labels, recipe-picker
 * value→label mapping and dialog reset, shortage clearing on close,
 * permission-gated buttons and the permission error/retry paths.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import InventoryPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding/inventory",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeAll(() => {
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

const ITEM = {
  id: 3,
  ingredient: "Crushed maize",
  category: "ENERGY",
  unit: "kg",
  qty_on_hand: 45,
  reorder_level: 10,
  last_purchase_price_per_kg: 28.5,
};

const RECIPES_PAYLOAD = {
  recipes: [
    { id: 1, code: "LACTATING_60_40", name: "Lactating 60/40", description: null, lines: [] },
  ],
  allocation: [],
};

function inventoryHandler(items: unknown[]) {
  return http.get("/api/feeding/inventory", () => HttpResponse.json(items));
}

async function renderLoaded() {
  renderWithProviders(<InventoryPage />);
  expect(await screen.findByText("Crushed maize")).toBeInTheDocument();
}

function rowOf(ingredient: string) {
  return screen.getByText(ingredient).closest("tr") as HTMLElement;
}

describe("InventoryPage add-stock — optional price preprocess", () => {
  let bodies: Array<Record<string, unknown>>;

  beforeEach(() => {
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    bodies = [];
    server.use(
      inventoryHandler([ITEM]),
      http.post("/api/feeding/inventory/:itemId/add", async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ ...ITEM, qty_on_hand: 50 }, { status: 200 });
      }),
    );
  });

  async function openAddStock() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf("Crushed maize")).getByRole("button", { name: "Add stock" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("sends a null price when the optional field is left untouched", async () => {
    const { user, dialog } = await openAddStock();
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ qty_kg: 10, price_per_kg: null });
  });

  it("treats an explicitly blanked price as absent, not as zero", async () => {
    const { user, dialog } = await openAddStock();
    const price = within(dialog).getByLabelText(/Price per kg/);
    await user.type(price, "5");
    await user.clear(price);
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ qty_kg: 10, price_per_kg: null });
  });

  it("keeps an explicit zero price bookable as a ₹0 restock", async () => {
    const { user, dialog } = await openAddStock();
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "0");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ qty_kg: 10, price_per_kg: 0 });
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("rejects a positive-price restock whose derived expense tops ₹1,000,000,000", async () => {
    const { user, dialog } = await openAddStock();
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "100000");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "10001");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    expect(
      await within(dialog).findByText("Restock cost cannot exceed ₹1,000,000,000"),
    ).toBeInTheDocument();
    expect(bodies).toHaveLength(0);
  });

  it("books a restock that lands exactly on the ₹1,000,000,000 ceiling", async () => {
    const { user, dialog } = await openAddStock();
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10000");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "100000");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("books a restock whose derived expense is exactly ₹1,000,000,000.004 — not a hair over", async () => {
    // 6.8 kg × ₹147,058,823.53 normalizes (3dp × 2dp) to the exact double
    // 1_000_000_000.004, the value the `total > 1_000_000_000.004` guard must
    // treat as still bookable (a >= boundary would wrongly reject it).
    const { user, dialog } = await openAddStock();
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "6.8");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "147058823.53");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ qty_kg: 6.8, price_per_kg: 147058823.53 });
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("InventoryPage add-stock — a11y error wiring", () => {
  beforeEach(() => {
    server.use(inventoryHandler([ITEM]));
  });

  async function openAddStock() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf("Crushed maize")).getByRole("button", { name: "Add stock" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("marks both inputs invalid and points describedby at the error ids", async () => {
    const { user, dialog } = await openAddStock();
    const qty = within(dialog).getByLabelText(/Quantity \(kg\)/);
    const price = within(dialog).getByLabelText(/Price per kg/);

    // Clean state: no aria-invalid, no describedby error target.
    expect(qty.getAttribute("aria-invalid")).toBeNull();
    expect(qty.getAttribute("aria-describedby")).toBeNull();
    expect(price.getAttribute("aria-invalid")).toBeNull();

    await user.type(price, "-1");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    expect(await within(dialog).findByText("Quantity must be greater than 0")).toBeInTheDocument();
    expect(await within(dialog).findByText("Price cannot be negative")).toBeInTheDocument();

    expect(qty.getAttribute("aria-invalid")).toBe("true");
    expect(qty.getAttribute("aria-describedby")).toBe(`qty-${ITEM.id}-error`);
    expect(price.getAttribute("aria-invalid")).toBe("true");
    expect(price.getAttribute("aria-describedby")).toBe(`price-${ITEM.id}-error`);
    expect(document.getElementById(`qty-${ITEM.id}-error`)).not.toBeNull();
    expect(document.getElementById(`price-${ITEM.id}-error`)).not.toBeNull();
  });
});

describe("InventoryPage mix dialog — picker labels, reset and shortage lifecycle", () => {
  let mixBodies: Array<Record<string, unknown>>;

  beforeEach(() => {
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    mixBodies = [];
    server.use(
      inventoryHandler([ITEM]),
      http.get("/api/feeding/recipes", () => HttpResponse.json(RECIPES_PAYLOAD)),
      http.post("/api/feeding/mix", async ({ request }) => {
        mixBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
  });

  async function openMix() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Mix batch" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/One batch is 100 kg/);
    return { user, dialog };
  }

  async function pickRecipe(user: ReturnType<typeof userEvent.setup>) {
    await user.click(within(screen.getByRole("dialog")).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Lactating 60/40 (LACTATING_60_40)" }));
  }

  it("starts on the placeholder recipe, one batch, with no batches error wiring", async () => {
    const { dialog } = await openMix();
    const batches = within(dialog).getByLabelText(/Batches/);

    expect(within(dialog).getByRole("combobox")).toHaveTextContent("recipe…");
    expect(batches).toHaveValue(1);
    expect(batches.getAttribute("aria-invalid")).toBeNull();
    expect(batches.getAttribute("aria-describedby")).toBeNull();
  });

  it("shows the picked recipe's label in the closed trigger, not its raw code", async () => {
    const { user, dialog } = await openMix();
    await pickRecipe(user);

    expect(within(dialog).getByRole("combobox")).toHaveTextContent("Lactating 60/40 (LACTATING_60_40)");
  });

  it("flags an invalid batch count with the exact error id and message", async () => {
    const { user, dialog } = await openMix();
    await pickRecipe(user);
    const batches = within(dialog).getByLabelText(/Batches/);

    await user.clear(batches);
    await user.type(batches, "0");
    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));

    expect(await within(dialog).findByText("At least 1 batch")).toBeInTheDocument();
    expect(batches.getAttribute("aria-invalid")).toBe("true");
    expect(batches.getAttribute("aria-describedby")).toBe("mix-batches-error");
    expect(document.getElementById("mix-batches-error")).not.toBeNull();

    await user.clear(batches);
    await user.type(batches, "1.5");
    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));
    expect(await within(dialog).findByText("Whole batches only")).toBeInTheDocument();
    expect(mixBodies).toHaveLength(0);
  });

  it("sends batch_kg as batches × 100", async () => {
    const { user, dialog } = await openMix();
    await pickRecipe(user);
    const batches = within(dialog).getByLabelText(/Batches/);
    await user.clear(batches);
    await user.type(batches, "2");
    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));

    await waitFor(() => expect(mixBodies).toHaveLength(1));
    expect(mixBodies[0]).toEqual({ recipe_code: "LACTATING_60_40", batch_kg: 200 });
  });

  it("clears a shortage when the dialog is closed, not only on the next submit", async () => {
    server.use(
      http.post("/api/feeding/mix", () =>
        HttpResponse.json({ detail: "Crushed maize: need 36 kg, have 20 kg" }, { status: 400 }),
      ),
    );
    const { user, dialog } = await openMix();
    await pickRecipe(user);
    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));
    expect(await within(dialog).findByText(/Cannot mix — insufficient stock/)).toBeInTheDocument();

    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mix batch" }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).queryByText(/Cannot mix/)).not.toBeInTheDocument();
  });

  it("resets the draft recipe and batch count on every closed→open transition", async () => {
    const { user } = await openMix();
    await pickRecipe(user);
    const batches = within(screen.getByRole("dialog")).getByLabelText(/Batches/);
    await user.clear(batches);
    await user.type(batches, "3");

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Mix batch" }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).getByLabelText(/Batches/)).toHaveValue(1);
    expect(within(reopened).getByRole("combobox")).toHaveTextContent("recipe…");
  });
});

describe("InventoryPage — permission gating of the mix entry points", () => {
  beforeEach(() => {
    server.use(inventoryHandler([ITEM]));
  });

  it("hides both mix entry points from a view-only feeder", async () => {
    server.use(permissionsHandler(["feeding.view"]));
    await renderLoaded();

    expect(screen.queryByRole("button", { name: "Mix batch" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mix your first batch" })).not.toBeInTheDocument();
  });

  it("gives a manager the empty-state CTA that opens the shared mix dialog", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Mix your first batch" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

describe("InventoryPage — permissions failure and retry", () => {
  it("surfaces the permissions error with a working retry", async () => {
    let permsCalls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        permsCalls += 1;
        return permsCalls === 1
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : HttpResponse.json({ is_owner: true, permissions: ["feeding.view", "feeding.manage"] });
      }),
      inventoryHandler([ITEM]),
    );
    const user = userEvent.setup();
    renderWithProviders(<InventoryPage />);

    const retry = await screen.findByRole("button", { name: /retry/i });
    expect(retry).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Feed inventory" })).not.toBeInTheDocument();

    await user.click(retry);
    expect(await screen.findByText("Crushed maize")).toBeInTheDocument();
  });
});

describe("InventoryPage — first-load, non-200 fallback and inventory error retry", () => {
  it("shows the polite loading region — not the loaded tree — while the first fetch is in flight", async () => {
    server.use(
      http.get("/api/feeding/inventory", () => new Promise<Response>(() => {})),
    );
    renderWithProviders(<InventoryPage />);

    const status = await screen.findByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(screen.getByText("Loading feed inventory…")).toBeInTheDocument();
    // The tab strip and the stock card only mount once data settles.
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByText("Stock on hand")).not.toBeInTheDocument();
  });

  it("keeps the inline loading row (no crash) when the API answers a non-200 status", async () => {
    server.use(
      http.get("/api/feeding/inventory", () => HttpResponse.json([], { status: 201 })),
    );
    renderWithProviders(<InventoryPage />);

    expect(await screen.findByText("Stock on hand")).toBeInTheDocument();
    expect(screen.getByText("Loading feed inventory…")).toBeInTheDocument();
    expect(screen.queryByText("Category")).not.toBeInTheDocument();
    expect(screen.queryByText("No feed inventory items yet.")).not.toBeInTheDocument();
  });

  it("retries the inventory load from the error state", async () => {
    let calls = 0;
    server.use(
      http.get("/api/feeding/inventory", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "inventory unavailable" }, { status: 500 })
          : HttpResponse.json([ITEM]);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<InventoryPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("inventory unavailable");
    await user.click(screen.getByRole("button", { name: "Retry inventory" }));
    expect(await screen.findByText("Crushed maize")).toBeInTheDocument();
    expect(calls).toBe(2);
  });
});
