/**
 * Feed inventory page: stock table (low badge, reorder/price fallbacks,
 * Indian ₹ formatting), add-stock dialog (validation + payload mapping),
 * mix-batch dialog (batch bounds, shortage error on 400) and RBAC gating.
 */

import { screen, waitFor, within } from "@testing-library/react";
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
  // jsdom lacks the pointer-capture/scroll APIs Radix Select relies on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

const ITEM_MAIZE = {
  id: 3,
  ingredient: "Crushed maize",
  category: "ENERGY",
  unit: "kg",
  qty_on_hand: 45,
  reorder_level: 50,
  last_purchase_price_per_kg: 28.5,
};

const ITEM_GREEN = {
  id: 7,
  ingredient: "Super Napier green fodder",
  category: "GREEN",
  unit: "kg",
  qty_on_hand: 500,
  reorder_level: null,
  last_purchase_price_per_kg: null,
};

const RECIPES_PAYLOAD = {
  recipes: [
    { id: 1, code: "LACTATING_60_40", name: "Lactating 60/40", description: null, lines: [] },
    { id: 2, code: "FATTENING_50_50", name: "Fattening 50/50", description: null, lines: [] },
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

describe("InventoryPage stock table", () => {
  beforeEach(() => {
    server.use(inventoryHandler([ITEM_MAIZE, ITEM_GREEN]));
  });

  it("renders rows with category, quantity, reorder level and ₹ price", async () => {
    await renderLoaded();

    const row = screen.getByText("Crushed maize").closest("tr") as HTMLElement;
    expect(within(row).getByText("ENERGY")).toBeInTheDocument();
    expect(within(row).getByText("45.0")).toBeInTheDocument();
    expect(within(row).getByText("50")).toBeInTheDocument();
    expect(within(row).getByText("₹28.50")).toBeInTheDocument();
  });

  it("flags stock at or below the reorder level with a low badge", async () => {
    await renderLoaded();

    const lowRow = screen.getByText("Crushed maize").closest("tr") as HTMLElement;
    expect(within(lowRow).getByText("low")).toBeInTheDocument();

    const okRow = screen.getByText("Super Napier green fodder").closest("tr") as HTMLElement;
    expect(within(okRow).queryByText("low")).not.toBeInTheDocument();
  });

  it("shows — for a null reorder level and a null last price", async () => {
    await renderLoaded();

    const row = screen.getByText("Super Napier green fodder").closest("tr") as HTMLElement;
    const cells = within(row).getAllByRole("cell");
    expect(cells[3]).toHaveTextContent("—"); // reorder at
    expect(cells[4]).toHaveTextContent("—"); // last price / kg
  });

  it("formats large prices with Indian digit grouping", async () => {
    server.use(
      inventoryHandler([{ ...ITEM_MAIZE, qty_on_hand: 9999, last_purchase_price_per_kg: 123456.5 }]),
    );
    renderWithProviders(<InventoryPage />);
    expect(await screen.findByText("₹1,23,456.50")).toBeInTheDocument();
  });

  it("shows the empty message when there are no items", async () => {
    server.use(inventoryHandler([]));
    renderWithProviders(<InventoryPage />);
    expect(await screen.findByText("No feed inventory items yet.")).toBeInTheDocument();
  });

  it("shows the ready-to-dispense balance created by mixing", async () => {
    server.use(
      http.get("/api/feeding/finished-stock", () =>
        HttpResponse.json([
          {
            recipe_code: "LACTATING_60_40",
            recipe_name: "Lactating 60/40",
            qty_on_hand: 175.25,
          },
        ]),
      ),
    );
    await renderLoaded();

    const card = screen
      .getByText("Ready-to-dispense mixed feed")
      .closest('[data-slot="card"]') as HTMLElement;
    expect(within(card).getByText("Lactating 60/40")).toBeInTheDocument();
    expect(within(card).getByText("LACTATING_60_40")).toBeInTheDocument();
    expect(within(card).getByText("175.25")).toBeInTheDocument();
  });

  it("shows the error detail when the inventory GET fails", async () => {
    server.use(
      http.get("/api/feeding/inventory", () =>
        HttpResponse.json({ detail: "inventory down" }, { status: 500 }),
      ),
    );
    renderWithProviders(<InventoryPage />);
    expect(await screen.findByText("inventory down")).toBeInTheDocument();
  });
});

describe("InventoryPage RBAC", () => {
  it("denies access without feeding.view and never calls the endpoint", async () => {
    let calls = 0;
    server.use(
      permissionsHandler(["animals.view"]),
      http.get("/api/feeding/inventory", () => {
        calls += 1;
        return HttpResponse.json([]);
      }),
    );
    renderWithProviders(<InventoryPage />);

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    expect(calls).toBe(0);
  });

  it("shows a permission error instead of misreporting no access", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 }),
      ),
    );
    renderWithProviders(<InventoryPage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
  });

  it("hides Mix batch and Add stock for a feeding.view-only user", async () => {
    server.use(permissionsHandler(["feeding.view"]), inventoryHandler([ITEM_MAIZE]));
    renderWithProviders(<InventoryPage />);
    expect(await screen.findByText("Crushed maize")).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Mix batch" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add stock" })).not.toBeInTheDocument();
  });

  it("shows Mix batch and per-row Add stock for a manager", async () => {
    server.use(inventoryHandler([ITEM_MAIZE]));
    renderWithProviders(<InventoryPage />);
    expect(await screen.findByText("Crushed maize")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Mix batch" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add stock" })).toBeInTheDocument();
  });
});

describe("InventoryPage add-stock dialog", () => {
  let addCalls: number;
  let addBody: Record<string, unknown> | null;
  let addItemId: string | null;
  let listCalls: number;

  beforeEach(() => {
    vi.mocked(toast.error).mockClear();
    addCalls = 0;
    addBody = null;
    addItemId = null;
    listCalls = 0;
    server.use(
      http.get("/api/feeding/inventory", () => {
        listCalls += 1;
        return HttpResponse.json([ITEM_MAIZE, ITEM_GREEN]);
      }),
      http.post("/api/feeding/inventory/:itemId/add", async ({ request, params }) => {
        addCalls += 1;
        addItemId = String(params.itemId);
        addBody = (await request.json()) as Record<string, unknown>;
        // The endpoint answers with the updated FeedInventoryOut; the page
        // confirms the persisted balance from it, not from the typed value.
        return HttpResponse.json(
          { ...ITEM_MAIZE, id: Number(params.itemId), qty_on_hand: 55.5 },
          { status: 200 },
        );
      }),
    );
  });

  async function openAddStock(ingredient = "Crushed maize") {
    const user = userEvent.setup();
    await renderLoaded();
    const row = screen.getByText(ingredient).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Add stock" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("opens with the row's ingredient in the title", async () => {
    const { dialog } = await openAddStock("Super Napier green fodder");
    expect(
      within(dialog).getByText("Add stock — Super Napier green fodder"),
    ).toBeInTheDocument();
  });

  it("blocks a zero quantity with the zod error and no POST", async () => {
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "0");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    expect(await within(dialog).findByText("Quantity must be greater than 0")).toBeInTheDocument();
    expect(addCalls).toBe(0);
  });

  it("blocks stock below half a gram before it reaches the API", async () => {
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "0.0004");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    expect(await within(dialog).findByText("Quantity must be at least 0.0005 kg"))
      .toBeInTheDocument();
    expect(addCalls).toBe(0);
  });

  // REGRESSION — qty_kg had no client-side maximum although the backend's
  // QuantityKgFloat rejects anything above 1,000,000 kg, so a fat-fingered
  // restock passed validation and only failed with an opaque server 422.
  it("blocks a quantity above the backend's 1,000,000 kg cap", async () => {
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "2000000");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    expect(
      await within(dialog).findByText("Quantity cannot exceed 1,000,000 kg"),
    ).toBeInTheDocument();
    expect(addCalls).toBe(0);
  });

  it("posts an explicit zero price instead of treating it as blank or invalid", async () => {
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "0");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(addCalls).toBe(1));
    expect(addBody).toEqual({ qty_kg: 10, price_per_kg: 0 });
  });

  it("blocks a non-zero price below half a paisa", async () => {
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "0.004");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    expect(
      await within(dialog).findByText("Price must be ₹0 or at least ₹0.005 (or leave blank)"),
    ).toBeInTheDocument();
    expect(addCalls).toBe(0);
  });

  it("blocks a positive price whose derived expense would round to zero", async () => {
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "0.001");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "0.005");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    expect(
      await within(dialog).findByText("Positive-price restock must total at least ₹0.01"),
    ).toBeInTheDocument();
    expect(addCalls).toBe(0);
  });

  it("blocks a derived restock expense above the ledger cap", async () => {
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "2");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "500000000.01");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    expect(
      await within(dialog).findByText("Restock cost cannot exceed ₹1,000,000,000"),
    ).toBeInTheDocument();
    expect(addCalls).toBe(0);
  });

  it("shows gram-scale inventory and finished stock without rounding them to zero", async () => {
    server.use(
      inventoryHandler([{ ...ITEM_MAIZE, qty_on_hand: 0.001 }]),
      http.get("/api/feeding/finished-stock", () =>
        HttpResponse.json([
          {
            recipe_code: "LACTATING_60_40",
            recipe_name: "Lactating 60/40",
            qty_on_hand: 0.001,
          },
        ]),
      ),
    );

    await renderLoaded();
    expect(screen.getAllByText("0.001")).toHaveLength(2);
  });

  it("POSTs quantity with a null price when price is left blank", async () => {
    const { user, dialog } = await openAddStock();
    const callsBefore = listCalls;

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "25");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(addCalls).toBe(1));
    expect(addItemId).toBe("3");
    expect(addBody).toEqual({ qty_kg: 25, price_per_kg: null });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(listCalls).toBeGreaterThan(callsBefore));
  });

  it("POSTs quantity and price when both are given", async () => {
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "100");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "31.25");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(addCalls).toBe(1));
    expect(addBody).toEqual({ qty_kg: 100, price_per_kg: 31.25 });
  });

  it("keeps the dialog open on a server error", async () => {
    server.use(
      http.post("/api/feeding/inventory/:itemId/add", () => {
        addCalls += 1;
        return HttpResponse.json({ detail: "cannot add stock" }, { status: 400 });
      }),
    );
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(addCalls).toBe(1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("cannot add stock");
  });

  it("does not reopen the same stock form while its dismissed write is pending", async () => {
    let releaseAdd: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      releaseAdd = resolve;
    });
    server.use(
      http.post("/api/feeding/inventory/:itemId/add", async ({ params }) => {
        addCalls += 1;
        await parked;
        return HttpResponse.json(
          { ...ITEM_MAIZE, id: Number(params.itemId), qty_on_hand: 55.5 },
          { status: 200 },
        );
      }),
    );
    const { user, dialog } = await openAddStock();
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(addCalls).toBe(1));
    expect(dialog.querySelector("fieldset")).toBeDisabled();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const row = screen.getByText("Crushed maize").closest("tr") as HTMLElement;
    const trigger = within(row).getByRole("button", { name: "Add stock" });
    expect(trigger).toBeDisabled();
    await user.click(trigger);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    releaseAdd?.();
    await waitFor(() =>
      expect(
        within(screen.getByText("Crushed maize").closest("tr") as HTMLElement).getByRole(
          "button",
          { name: "Add stock" },
        ),
      ).toBeEnabled(),
    );
    expect(addCalls).toBe(1);
  });
});

describe("InventoryPage mix-batch dialog", () => {
  let mixCalls: number;
  let mixBody: Record<string, unknown> | null;

  beforeEach(() => {
    vi.mocked(toast.error).mockClear();
    mixCalls = 0;
    mixBody = null;
    server.use(
      inventoryHandler([ITEM_MAIZE]),
      http.get("/api/feeding/recipes", () => HttpResponse.json(RECIPES_PAYLOAD)),
      http.post("/api/feeding/mix", async ({ request }) => {
        mixCalls += 1;
        mixBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
  });

  async function openMix() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Mix batch" }));
    const dialog = await screen.findByRole("dialog");
    // Recipes load lazily once the dialog opens.
    await within(dialog).findByText(/One batch is 100 kg/);
    return { user, dialog };
  }

  async function pickRecipe(user: ReturnType<typeof userEvent.setup>, name: string) {
    await user.click(within(screen.getByRole("dialog")).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name }));
  }

  it("requires a recipe before submitting", async () => {
    const { user, dialog } = await openMix();

    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));
    expect(await within(dialog).findByText("Pick a recipe")).toBeInTheDocument();
    expect(mixCalls).toBe(0);
  });

  it("blocks mixing and offers retry when recipes fail to load", async () => {
    let recipeCalls = 0;
    server.use(
      http.get("/api/feeding/recipes", () => {
        recipeCalls += 1;
        return recipeCalls === 1
          ? HttpResponse.json({ detail: "recipes unavailable" }, { status: 503 })
          : HttpResponse.json(RECIPES_PAYLOAD);
      }),
    );
    const { user, dialog } = await openMix();

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not load recipes",
    );
    expect(within(dialog).getByRole("button", { name: "Mix batch" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Retry recipes" })).toBeEnabled();
    expect(mixCalls).toBe(0);

    await user.click(within(dialog).getByRole("button", { name: "Retry recipes" }));
    await waitFor(() => expect(recipeCalls).toBe(2));
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Mix batch" })).toBeEnabled();
  });

  it("rejects 0 batches", async () => {
    const { user, dialog } = await openMix();

    await pickRecipe(user, "Lactating 60/40 (LACTATING_60_40)");
    // The closed trigger shows the full label, not just the raw recipe code.
    expect(within(dialog).getByRole("combobox")).toHaveTextContent(
      "Lactating 60/40 (LACTATING_60_40)",
    );
    const input = within(dialog).getByLabelText(/Batches/);
    await user.clear(input);
    await user.type(input, "0");
    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));

    expect(await within(dialog).findByText("At least 1 batch")).toBeInTheDocument();
    expect(mixCalls).toBe(0);
  });

  it("rejects 51 batches", async () => {
    const { user, dialog } = await openMix();

    await pickRecipe(user, "Lactating 60/40 (LACTATING_60_40)");
    const input = within(dialog).getByLabelText(/Batches/);
    await user.clear(input);
    await user.type(input, "51");
    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));

    expect(await within(dialog).findByText("At most 50 batches")).toBeInTheDocument();
    expect(mixCalls).toBe(0);
  });

  it("rejects fractional batches", async () => {
    const { user, dialog } = await openMix();

    await pickRecipe(user, "Lactating 60/40 (LACTATING_60_40)");
    const input = within(dialog).getByLabelText(/Batches/);
    await user.clear(input);
    await user.type(input, "1.5");
    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));

    expect(await within(dialog).findByText("Whole batches only")).toBeInTheDocument();
    expect(mixCalls).toBe(0);
  });

  it("POSTs recipe_code with batch_kg = batches × 100", async () => {
    const { user, dialog } = await openMix();

    await pickRecipe(user, "Fattening 50/50 (FATTENING_50_50)");
    const input = within(dialog).getByLabelText(/Batches/);
    await user.clear(input);
    await user.type(input, "2");
    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));

    await waitFor(() => expect(mixCalls).toBe(1));
    expect(mixBody).toEqual({ recipe_code: "FATTENING_50_50", batch_kg: 200 });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows the shortage detail inside the dialog on a 400", async () => {
    server.use(
      http.post("/api/feeding/mix", () => {
        mixCalls += 1;
        return HttpResponse.json(
          { detail: "Crushed maize: need 36 kg, have 20 kg" },
          { status: 400 },
        );
      }),
    );
    const { user, dialog } = await openMix();

    await pickRecipe(user, "Lactating 60/40 (LACTATING_60_40)");
    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));

    expect(
      await within(dialog).findByText(/Cannot mix — insufficient stock: Crushed maize: need 36 kg, have 20 kg/),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Mix batch" }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).queryByText(/Cannot mix/)).not.toBeInTheDocument();
  });

  it("does not show the shortage panel for non-400 errors", async () => {
    server.use(
      http.post("/api/feeding/mix", () => {
        mixCalls += 1;
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    const { user, dialog } = await openMix();

    await pickRecipe(user, "Lactating 60/40 (LACTATING_60_40)");
    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));

    await waitFor(() => expect(mixCalls).toBe(1));
    expect(within(dialog).queryByText(/Cannot mix/)).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("boom");
  });
});
