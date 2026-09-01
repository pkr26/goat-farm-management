/**
 * Feed inventory page — branch-level behaviour the payload/validation suite
 * does not reach: the feeding tab strip, low-stock boundaries and column
 * alignment, the loading/error states of each query, in-flight form locking,
 * the wording of the success toasts, and the mix dialog's lazy recipe load.
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
  // jsdom lacks the pointer-capture/scroll APIs the Select primitive relies on.
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

/** Exactly at the reorder level — the inclusive edge of "low". */
const ITEM_AT_REORDER = {
  id: 11,
  ingredient: "Maize bran",
  category: "ENERGY",
  unit: "kg",
  qty_on_hand: 50,
  reorder_level: 50,
  last_purchase_price_per_kg: 20,
};

/** No reorder level at all — never low, not even when it runs out. */
const ITEM_UNTRACKED_EMPTY = {
  id: 12,
  ingredient: "Mineral mixture",
  category: "MINERAL",
  unit: "kg",
  qty_on_hand: 0,
  reorder_level: null,
  last_purchase_price_per_kg: null,
};

/** Tracked and comfortably above its reorder level. */
const ITEM_WELL_STOCKED = {
  id: 13,
  ingredient: "Lucerne hay",
  category: "GREEN",
  unit: "kg",
  qty_on_hand: 500,
  reorder_level: 10,
  last_purchase_price_per_kg: 12,
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

/** A handler that answers only once `release()` is called. */
function parkedHandler() {
  let release!: () => void;
  const parked = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { parked, release: () => release() };
}

async function renderLoaded() {
  renderWithProviders(<InventoryPage />);
  expect(await screen.findByText("Crushed maize")).toBeInTheDocument();
}

function rowOf(ingredient: string) {
  return screen.getByText(ingredient).closest("tr") as HTMLElement;
}

describe("InventoryPage feeding tabs", () => {
  it("links the three feeding views and marks Inventory as the active tab", async () => {
    server.use(inventoryHandler([ITEM_MAIZE]));
    await renderLoaded();

    const nav = screen.getByRole("navigation");
    const tabs = within(nav).getAllByRole("link");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Today's plan",
      "Recipes",
      "Inventory",
    ]);
    expect(tabs.map((tab) => tab.getAttribute("href"))).toEqual([
      "/feeding",
      "/feeding/recipes",
      "/feeding/inventory",
    ]);

    const active = within(nav).getByRole("link", { name: "Inventory" });
    expect(active).toHaveClass("border-primary", "font-medium", "text-foreground");
    for (const label of ["Today's plan", "Recipes"]) {
      const inactive = within(nav).getByRole("link", { name: label });
      expect(inactive).toHaveClass("border-transparent", "text-muted-foreground");
      expect(inactive).not.toHaveClass("border-primary");
    }
  });
});

describe("InventoryPage loading and error states", () => {
  it("waits for permissions instead of flashing an access denial", async () => {
    const { parked, release } = parkedHandler();
    server.use(
      http.get("/api/auth/permissions", async () => {
        await parked;
        return HttpResponse.json({ is_owner: true, permissions: ["feeding.view"] });
      }),
      inventoryHandler([ITEM_MAIZE]),
    );
    const { container } = renderWithProviders(<InventoryPage />);

    // The header stays up and a skeleton fills the data region — the page no
    // longer collapses to a bare "Loading…" line while the probe is out.
    expect(
      await screen.findByRole("heading", { name: "Feed inventory" }),
    ).toBeInTheDocument();
    expect(container.querySelector('[data-slot="skeleton"]')).not.toBeNull();
    expect(screen.queryByText("You don't have access to this page.")).not.toBeInTheDocument();

    release();
    expect(await screen.findByText("Crushed maize")).toBeInTheDocument();
  });

  it("keeps the skeleton up while the inventory request is in flight", async () => {
    const { parked, release } = parkedHandler();
    let inventoryCalls = 0;
    server.use(
      http.get("/api/feeding/inventory", async () => {
        inventoryCalls += 1;
        await parked;
        return HttpResponse.json([ITEM_MAIZE]);
      }),
    );
    renderWithProviders(<InventoryPage />);

    // The request only leaves once permissions have resolved, so this is the
    // query's own pending state rather than the permission one.
    await waitFor(() => expect(inventoryCalls).toBe(1));
    expect(screen.getByRole("heading", { name: "Feed inventory" })).toBeInTheDocument();
    expect(screen.getByText("Loading feed inventory…")).toBeInTheDocument();
    expect(screen.queryByText("Could not load feed inventory.")).not.toBeInTheDocument();

    release();
    expect(await screen.findByText("Crushed maize")).toBeInTheDocument();
  });

  it("falls back to a generic message when the inventory request never lands", async () => {
    server.use(http.get("/api/feeding/inventory", () => HttpResponse.error()));
    renderWithProviders(<InventoryPage />);

    expect(await screen.findByText("Could not load feed inventory.")).toBeInTheDocument();
  });

  it("falls back to a generic message when mixed-feed stock never lands", async () => {
    server.use(
      inventoryHandler([ITEM_MAIZE]),
      http.get("/api/feeding/finished-stock", () => HttpResponse.error()),
    );
    await renderLoaded();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load mixed-feed stock.",
    );
  });

  it("shows the mixed-feed empty state instead of a headers-only table", async () => {
    server.use(inventoryHandler([ITEM_MAIZE]));
    await renderLoaded();

    const card = screen
      .getByText("Ready-to-dispense mixed feed")
      .closest('[data-slot="card"]') as HTMLElement;
    expect(within(card).getByText("No mixed feed is ready.")).toBeInTheDocument();
    expect(
      within(card).getByText(
        "Use Mix batch to turn ingredient stock into a ready recipe balance.",
      ),
    ).toBeInTheDocument();
    expect(within(card).queryByText("Ready (kg)")).not.toBeInTheDocument();
  });
});

describe("InventoryPage low-stock flagging", () => {
  beforeEach(() => {
    server.use(
      inventoryHandler([ITEM_AT_REORDER, ITEM_UNTRACKED_EMPTY, ITEM_WELL_STOCKED]),
    );
  });

  async function renderRows() {
    renderWithProviders(<InventoryPage />);
    expect(await screen.findByText("Maize bran")).toBeInTheDocument();
  }

  it("flags and tints stock sitting exactly on its reorder level", async () => {
    await renderRows();

    const row = rowOf("Maize bran");
    expect(within(row).getByText("low")).toBeInTheDocument();
    expect(row).toHaveClass("bg-warning-tint/50");
  });

  it("never flags an ingredient without a reorder level, even when it is empty", async () => {
    await renderRows();

    const row = rowOf("Mineral mixture");
    expect(within(row).getByText("0.0")).toBeInTheDocument();
    expect(within(row).queryByText("low")).not.toBeInTheDocument();
    expect(row).not.toHaveClass("bg-warning-tint/50");
  });

  it("leaves a well-stocked tracked ingredient unflagged", async () => {
    await renderRows();

    const row = rowOf("Lucerne hay");
    expect(within(row).queryByText("low")).not.toBeInTheDocument();
    expect(row).not.toHaveClass("bg-warning-tint/50");
  });
});

describe("InventoryPage table columns", () => {
  it("gives managers an action column that lines up with the body rows", async () => {
    server.use(inventoryHandler([ITEM_MAIZE]));
    await renderLoaded();

    const headers = within(screen.getAllByRole("rowgroup")[0]).getAllByRole("columnheader");
    expect(headers).toHaveLength(6);
    expect(headers.slice(0, 5).map((cell) => cell.textContent)).toEqual([
      "Category",
      "Ingredient",
      "On hand (kg)",
      "Reorder at",
      "Last price / kg",
    ]);
    expect(within(rowOf("Crushed maize")).getAllByRole("cell")).toHaveLength(6);
  });

  it("drops the action column entirely for a view-only user", async () => {
    server.use(permissionsHandler(["feeding.view"]), inventoryHandler([ITEM_MAIZE]));
    await renderLoaded();

    expect(
      within(screen.getAllByRole("rowgroup")[0]).getAllByRole("columnheader"),
    ).toHaveLength(5);
    expect(within(rowOf("Crushed maize")).getAllByRole("cell")).toHaveLength(5);
  });

  it("never queries mixed-feed stock without feeding.view", async () => {
    let finishedCalls = 0;
    server.use(
      permissionsHandler(["animals.view"]),
      http.get("/api/feeding/finished-stock", () => {
        finishedCalls += 1;
        return HttpResponse.json([]);
      }),
    );
    const { waitForAuthIdle } = renderWithProviders(<InventoryPage />);
    await waitForAuthIdle();

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    expect(finishedCalls).toBe(0);
  });
});

describe("InventoryPage add-stock submission", () => {
  let addCalls: number;
  let addBodies: Array<Record<string, unknown>>;

  beforeEach(() => {
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    addCalls = 0;
    addBodies = [];
    server.use(
      inventoryHandler([ITEM_MAIZE]),
      http.post("/api/feeding/inventory/:itemId/add", async ({ request }) => {
        addCalls += 1;
        addBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ ...ITEM_MAIZE, qty_on_hand: 1234.5 }, { status: 200 });
      }),
    );
  });

  async function openAddStock() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf("Crushed maize")).getByRole("button", { name: "Add stock" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("locks the form and says Adding… from the moment the submit starts", async () => {
    const { user, dialog } = await openAddStock();
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10");

    const submit = dialog.querySelector('button[type="submit"]') as HTMLButtonElement;
    expect(submit).toBeEnabled();
    expect(submit).toHaveTextContent("Add");

    // Submitted, but validation has not resolved yet: the guard has to be on
    // before the request exists, or a second submit slips through behind it.
    fireEvent.submit(dialog.querySelector("form") as HTMLFormElement);
    expect(dialog.querySelector("fieldset")).toBeDisabled();
    expect(submit).toBeDisabled();
    expect(submit).toHaveTextContent("Adding…");

    await waitFor(() => expect(addCalls).toBe(1));
  });

  it("confirms the balance the API persisted, not the quantity typed", async () => {
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "12");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "0");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(addCalls).toBe(1));
    expect(toast.success).toHaveBeenCalledWith(
      "Stock added — Crushed maize is now at 1,234.5 kg.",
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("confirms without inventing a balance when the API returns no content", async () => {
    server.use(
      http.post("/api/feeding/inventory/:itemId/add", () => {
        addCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "12");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(addCalls).toBe(1));
    expect(toast.success).toHaveBeenCalledWith("Stock added — Crushed maize.");
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("reports a generic failure when the restock never reaches the server", async () => {
    server.use(
      http.post("/api/feeding/inventory/:itemId/add", () => {
        addCalls += 1;
        return HttpResponse.error();
      }),
    );
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Something went wrong"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("empties the form for the next restock instead of replaying the last one", async () => {
    const { user, dialog } = await openAddStock();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "10");
    await user.type(within(dialog).getByLabelText(/Price per kg/), "5");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(addCalls).toBe(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const trigger = within(rowOf("Crushed maize")).getByRole("button", { name: "Add stock" });
    await waitFor(() => expect(trigger).toBeEnabled());
    await user.click(trigger);
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).getByLabelText(/Quantity \(kg\)/)).toHaveValue(null);
    expect(within(reopened).getByLabelText(/Price per kg/)).toHaveValue(null);

    await user.type(within(reopened).getByLabelText(/Quantity \(kg\)/), "7");
    await user.click(within(reopened).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(addCalls).toBe(2));
    expect(addBodies[1]).toEqual({ qty_kg: 7, price_per_kg: null });
  });

  it("rejects a negative price and one above the per-kg ceiling", async () => {
    const { user, dialog } = await openAddStock();
    const qty = within(dialog).getByLabelText(/Quantity \(kg\)/);
    const price = within(dialog).getByLabelText(/Price per kg/);

    await user.type(qty, "10");
    fireEvent.change(price, { target: { value: "-5" } });
    await user.click(within(dialog).getByRole("button", { name: "Add" }));
    expect(await within(dialog).findByText("Price cannot be negative")).toBeInTheDocument();

    fireEvent.change(price, { target: { value: "1000000001" } });
    await user.click(within(dialog).getByRole("button", { name: "Add" }));
    expect(
      await within(dialog).findByText("Price cannot exceed ₹1,000,000,000 per kg"),
    ).toBeInTheDocument();
    expect(addCalls).toBe(0);
  });

  it("accepts a restock whose expense rounds up to the smallest bookable ₹0.01", async () => {
    const { user, dialog } = await openAddStock();
    const qty = within(dialog).getByLabelText(/Quantity \(kg\)/);
    const price = within(dialog).getByLabelText(/Price per kg/);

    // 0.004 kg × ₹1 = ₹0.004, which rounds to nothing.
    await user.type(qty, "0.004");
    await user.type(price, "1");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));
    expect(
      await within(dialog).findByText("Positive-price restock must total at least ₹0.01"),
    ).toBeInTheDocument();
    expect(addCalls).toBe(0);

    // 0.005 kg × ₹1 = ₹0.005, which the backend rounds HALF_UP to ₹0.01.
    await user.clear(qty);
    await user.type(qty, "0.005");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(addCalls).toBe(1));
    expect(addBodies[0]).toEqual({ qty_kg: 0.005, price_per_kg: 1 });
  });
});

describe("InventoryPage mix-batch dialog", () => {
  let mixCalls: number;
  let recipeCalls: number;
  let inventoryCalls: number;

  beforeEach(() => {
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    mixCalls = 0;
    recipeCalls = 0;
    inventoryCalls = 0;
    server.use(
      http.get("/api/feeding/inventory", () => {
        inventoryCalls += 1;
        return HttpResponse.json([ITEM_MAIZE]);
      }),
      http.get("/api/feeding/recipes", () => {
        recipeCalls += 1;
        return HttpResponse.json(RECIPES_PAYLOAD);
      }),
      http.post("/api/feeding/mix", () => {
        mixCalls += 1;
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

  async function pickRecipe(user: ReturnType<typeof userEvent.setup>, name: string) {
    await user.click(within(screen.getByRole("dialog")).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name }));
  }

  it("does not fetch recipes until the dialog is opened", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    expect(recipeCalls).toBe(0);

    await user.click(screen.getByRole("button", { name: "Mix batch" }));
    await screen.findByRole("dialog");
    await waitFor(() => expect(recipeCalls).toBe(1));
  });

  it("announces the recipe load and keeps the picker disabled until it lands", async () => {
    const { parked, release } = parkedHandler();
    server.use(
      http.get("/api/feeding/recipes", async () => {
        recipeCalls += 1;
        await parked;
        return HttpResponse.json(RECIPES_PAYLOAD);
      }),
    );
    const { dialog } = await openMix();

    const status = await within(dialog).findByRole("status");
    expect(status).toHaveTextContent("Loading recipes…");
    expect(within(dialog).getByRole("combobox")).toBeDisabled();
    expect(dialog.querySelector('button[type="submit"]')).toBeDisabled();

    release();
    await waitFor(() => expect(within(dialog).queryByRole("status")).not.toBeInTheDocument());
    expect(within(dialog).getByRole("combobox")).toBeEnabled();
    expect(dialog.querySelector('button[type="submit"]')).toBeEnabled();
  });

  it("disables the recipe picker when the recipe list fails", async () => {
    server.use(
      http.get("/api/feeding/recipes", () => {
        recipeCalls += 1;
        return HttpResponse.json({ detail: "recipes unavailable" }, { status: 503 });
      }),
    );
    const { dialog } = await openMix();

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Could not load recipes");
    expect(within(dialog).getByRole("combobox")).toBeDisabled();
  });

  it("opens on the placeholder recipe and a single batch", async () => {
    const { dialog } = await openMix();

    expect(within(dialog).getByRole("combobox")).toHaveTextContent("recipe…");
    expect(within(dialog).getByLabelText(/Batches/)).toHaveValue(1);
  });

  it("clears the missing-recipe error as soon as a recipe is picked", async () => {
    const { user, dialog } = await openMix();

    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));
    expect(await within(dialog).findByText("Pick a recipe")).toBeInTheDocument();

    await pickRecipe(user, "Lactating 60/40 (LACTATING_60_40)");
    await waitFor(() =>
      expect(within(dialog).queryByText("Pick a recipe")).not.toBeInTheDocument(),
    );
    expect(mixCalls).toBe(0);
  });

  it("locks the mix form and says Mixing… from the moment the submit starts", async () => {
    const { user, dialog } = await openMix();
    await pickRecipe(user, "Lactating 60/40 (LACTATING_60_40)");

    const submit = dialog.querySelector('button[type="submit"]') as HTMLButtonElement;
    expect(submit).toBeEnabled();
    expect(submit).toHaveTextContent("Mix batch");

    fireEvent.submit(dialog.querySelector("form") as HTMLFormElement);
    expect(dialog.querySelector("fieldset")).toBeDisabled();
    expect(submit).toBeDisabled();
    expect(submit).toHaveTextContent("Mixing…");

    await waitFor(() => expect(mixCalls).toBe(1));
  });

  it("confirms the mixed quantity and refreshes the stock table", async () => {
    const { user, dialog } = await openMix();
    await pickRecipe(user, "Lactating 60/40 (LACTATING_60_40)");
    const batches = within(dialog).getByLabelText(/Batches/);
    await user.clear(batches);
    await user.type(batches, "3");
    const callsBefore = inventoryCalls;

    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));

    await waitFor(() => expect(mixCalls).toBe(1));
    expect(toast.success).toHaveBeenCalledWith("Mixed 300 kg — inventory decremented.");
    await waitFor(() => expect(inventoryCalls).toBeGreaterThan(callsBefore));
  });

  it("drops a stale shortage when the next attempt fails another way", async () => {
    server.use(
      http.post("/api/feeding/mix", () => {
        mixCalls += 1;
        return mixCalls === 1
          ? HttpResponse.json({ detail: "Crushed maize: need 36 kg, have 20 kg" }, { status: 400 })
          : HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    const { user, dialog } = await openMix();
    await pickRecipe(user, "Lactating 60/40 (LACTATING_60_40)");

    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));
    expect(await within(dialog).findByText(/Cannot mix — insufficient stock/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("boom"));
    expect(within(dialog).queryByText(/Cannot mix/)).not.toBeInTheDocument();
  });

  it("returns the recipe picker to its placeholder after a successful mix", async () => {
    const { user, dialog } = await openMix();
    await pickRecipe(user, "Fattening 50/50 (FATTENING_50_50)");
    expect(within(dialog).getByRole("combobox")).toHaveTextContent(
      "Fattening 50/50 (FATTENING_50_50)",
    );

    await user.click(within(dialog).getByRole("button", { name: "Mix batch" }));
    await waitFor(() => expect(mixCalls).toBe(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const trigger = screen.getByRole("button", { name: "Mix batch" });
    await waitFor(() => expect(trigger).toBeEnabled());
    await user.click(trigger);
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).getByRole("combobox")).toHaveTextContent("recipe…");
    expect(within(reopened).getByLabelText(/Batches/)).toHaveValue(1);
  });
});
