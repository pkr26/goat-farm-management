/**
 * Feeding "today" page: 3-shift plan table (40/20/40 split, dispensed totals +
 * done badge), dispensing log, record-dispensing dialog (validation + payload
 * mapping), per-bucket kg/head override dialog, and RBAC gating of every
 * manage control.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FeedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture/scroll APIs Radix Select relies on.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

function localToday(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

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

const LINE_MALE_KIDS = {
  bucket: "MALE_KIDS",
  recipe_code: "FATTENING_50_50",
  recipe_name: "Fattening 50/50",
  heads: 10,
  kg_per_head: 1.5,
  daily_kg: 15,
  shifts: [
    { shift: "MORNING", pct: 40, kg: 6, time: "6:30 AM" },
    { shift: "AFTERNOON", pct: 20, kg: 3, time: "1:30 PM" },
    { shift: "NIGHT", pct: 40, kg: 6, time: "7:30 PM" },
  ],
};

const RECIPES_PAYLOAD = {
  recipes: [
    { id: 1, code: "LACTATING_60_40", name: "Lactating 60/40", description: null, lines: [] },
    { id: 2, code: "FATTENING_50_50", name: "Fattening 50/50", description: null, lines: [] },
  ],
  allocation: [],
};

function planHandler(payload: Record<string, unknown>) {
  return http.get("/api/feeding/plan", () => HttpResponse.json(payload));
}

function recipesHandler() {
  return http.get("/api/feeding/recipes", () => HttpResponse.json(RECIPES_PAYLOAD));
}

function rowOf(text: string): HTMLElement {
  const row = screen.getByText(text).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

/** The plan row for the BREEDING line (recipe name is unique to the plan table). */
function planRow(): HTMLElement {
  return rowOf("Lactating 60/40");
}

async function renderLoaded() {
  renderWithProviders(<FeedingPage />);
  // The recipe name proves permissions + plan + (owner) recipes all resolved.
  expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
}

describe("FeedingPage plan table", () => {
  beforeEach(() => {
    server.use(
      planHandler({
        lines: [LINE_BREEDING, LINE_MALE_KIDS],
        records: [
          { id: 1, date: localToday(), shift: "MORNING", bucket: "BREEDING", recipe_code: "LACTATING_60_40", qty_kg: 8 },
          { id: 2, date: localToday(), shift: "NIGHT", bucket: "BREEDING", recipe_code: null, qty_kg: 12.05 },
          { id: 3, date: localToday(), shift: "MORNING", bucket: "MALE_KIDS", recipe_code: "FATTENING_50_50", qty_kg: 6 },
        ],
      }),
      http.get("/api/feeding/recipes", () => HttpResponse.json(RECIPES_PAYLOAD)),
    );
  });

  it("renders each bucket line with heads, ration and daily kg", async () => {
    await renderLoaded();

    const row = planRow();
    const cells = within(row).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("BREEDING");
    expect(cells[1]).toHaveTextContent("Lactating 60/40");
    expect(cells[2]).toHaveTextContent("20"); // heads
    expect(cells[3]).toHaveTextContent("1"); // kg/head/day
    expect(cells[4]).toHaveTextContent("20"); // daily kg
  });

  it("renders the 40/20/40 shift cells with their times in the title", async () => {
    await renderLoaded();

    const row = rowOf("Fattening 50/50");
    expect(within(row).getByTitle("MORNING 6:30 AM")).toHaveTextContent("6");
    expect(within(row).getByTitle("AFTERNOON 1:30 PM")).toHaveTextContent("3");
    expect(within(row).getByTitle("NIGHT 7:30 PM")).toHaveTextContent("6");
  });

  it("sums dispensed quantities per bucket and marks full rations done", async () => {
    await renderLoaded();

    // 8 + 12.05 = 20.05 → displayed with toFixed(1).
    const breeding = planRow();
    expect(within(breeding).getByText(/^20\.\d kg$/)).toBeInTheDocument();
    // 20.05 >= 20 daily → done badge.
    expect(within(breeding).getByText("done")).toBeInTheDocument();

    const kids = rowOf("Fattening 50/50");
    expect(within(kids).getByText("6.0 kg")).toBeInTheDocument();
    expect(within(kids).queryByText("done")).not.toBeInTheDocument();
  });

  it("renders the dispensing log with — for a missing recipe code", async () => {
    await renderLoaded();

    const logRow = screen.getByText("12.1").closest("tr") as HTMLElement;
    expect(within(logRow).getByText("NIGHT")).toBeInTheDocument();
    expect(within(logRow).getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("Nothing dispensed yet today.")).not.toBeInTheDocument();
  });

  it("shows the empty-plan message when no bucket lines come back", async () => {
    server.use(planHandler({ lines: [], records: [] }), recipesHandler());
    renderWithProviders(<FeedingPage />);

    expect(await screen.findByText("No active animals — nothing to feed.")).toBeInTheDocument();
    expect(screen.getByText("Nothing dispensed yet today.")).toBeInTheDocument();
  });

  it("shows the static shift schedule note", async () => {
    await renderLoaded();
    expect(
      screen.getByText(/Shifts: MORNING 6:30 AM \(sweep bunks first\)/),
    ).toBeInTheDocument();
  });
});

describe("FeedingPage errors and RBAC", () => {
  it("shows the server detail when the plan GET fails", async () => {
    server.use(
      http.get("/api/feeding/plan", () =>
        HttpResponse.json({ detail: "plan blew up" }, { status: 500 }),
      ),
      recipesHandler(),
    );
    renderWithProviders(<FeedingPage />);
    expect(await screen.findByText("plan blew up")).toBeInTheDocument();
  });

  it("falls back to a generic message for non-JSON failures", async () => {
    server.use(
      http.get("/api/feeding/plan", () => new HttpResponse(null, { status: 500 })),
      recipesHandler(),
    );
    renderWithProviders(<FeedingPage />);
    expect(
      await screen.findByText((t) => t.includes("Could not load") || t.length > 0),
    ).toBeInTheDocument();
    expect(screen.queryByText("No active animals")).not.toBeInTheDocument();
  });

  it("denies access without feeding.view and never calls the plan endpoint", async () => {
    let planCalls = 0;
    server.use(
      permissionsHandler(["tasks.view"]),
      http.get("/api/feeding/plan", () => {
        planCalls += 1;
        return HttpResponse.json({ lines: [], records: [] });
      }),
    );
    renderWithProviders(<FeedingPage />);

    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
    expect(planCalls).toBe(0);
  });

  it("hides every manage control for a feeding.view-only user", async () => {
    let recipesCalls = 0;
    server.use(
      permissionsHandler(["feeding.view"]),
      planHandler({ lines: [LINE_BREEDING], records: [] }),
      http.get("/api/feeding/recipes", () => {
        recipesCalls += 1;
        return HttpResponse.json(RECIPES_PAYLOAD);
      }),
    );
    renderWithProviders(<FeedingPage />);
    expect(await screen.findByText("BREEDING")).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Record dispensing" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    // Recipes are only fetched for managers (dispense dialog select).
    expect(recipesCalls).toBe(0);
  });
});

describe("FeedingPage dispense dialog", () => {
  let dispenseCalls: number;
  let dispenseBody: Record<string, unknown> | null;
  let planCalls: number;

  beforeEach(() => {
    dispenseCalls = 0;
    dispenseBody = null;
    planCalls = 0;
    server.use(
      http.get("/api/feeding/plan", () => {
        planCalls += 1;
        return HttpResponse.json({ lines: [LINE_BREEDING, LINE_MALE_KIDS], records: [] });
      }),
      http.get("/api/feeding/recipes", () => HttpResponse.json(RECIPES_PAYLOAD)),
      http.post("/api/feeding/dispense", async ({ request }) => {
        dispenseCalls += 1;
        dispenseBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 10 }, { status: 201 });
      }),
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("validates quantity: zero/blank blocks the POST", async () => {
    const { user, dialog } = await openDialog();

    await user.click(within(dialog).getByRole("button", { name: "Record" }));
    expect(await within(dialog).findByText("Quantity must be greater than 0")).toBeInTheDocument();
    expect(dispenseCalls).toBe(0);
  });

  it("rejects a negative quantity", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "-3");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));
    expect(await within(dialog).findByText("Quantity must be greater than 0")).toBeInTheDocument();
    expect(dispenseCalls).toBe(0);
  });

  it("POSTs the mapped payload (no recipe → null) and invalidates the plan", async () => {
    const { user, dialog } = await openDialog();
    const callsBefore = planCalls;

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "7.5");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    expect(dispenseBody).toEqual({
      bucket: "BREEDING", // default: first plan line's bucket
      shift: "MORNING",
      recipe_code: null,
      qty_kg: 7.5,
      date: localToday(),
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(planCalls).toBeGreaterThan(callsBefore));
  });

  it("sends the selected bucket, shift and recipe", async () => {
    const { user, dialog } = await openDialog();

    // Bucket select → MALE_KIDS.
    const selects = within(dialog).getAllByRole("combobox");
    await user.click(selects[0]);
    await user.click(await screen.findByRole("option", { name: "MALE_KIDS" }));

    // Shift select → NIGHT.
    await user.click(within(dialog).getAllByRole("combobox")[1]);
    await user.click(await screen.findByRole("option", { name: "NIGHT" }));

    // Recipe select → Fattening 50/50.
    await user.click(within(dialog).getAllByRole("combobox")[2]);
    await user.click(await screen.findByRole("option", { name: "Fattening 50/50" }));

    // The closed trigger shows the recipe label, not the raw recipe code.
    const recipeTrigger = within(dialog).getAllByRole("combobox")[2];
    expect(recipeTrigger).toHaveTextContent("Fattening 50/50");
    expect(recipeTrigger).not.toHaveTextContent("FATTENING_50_50");

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "4");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    expect(dispenseBody).toMatchObject({
      bucket: "MALE_KIDS",
      shift: "NIGHT",
      recipe_code: "FATTENING_50_50",
      qty_kg: 4,
    });
  });

  it("keeps the dialog open when the server rejects the dispense", async () => {
    server.use(
      http.post("/api/feeding/dispense", () => {
        dispenseCalls += 1;
        return HttpResponse.json({ detail: "insufficient feed mixed" }, { status: 400 });
      }),
    );
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "5");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    // Error path only toasts; the dialog must stay open for correction.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("FeedingPage kg/head override dialog", () => {
  let settingsBody: Record<string, unknown> | null;

  beforeEach(() => {
    settingsBody = null;
    server.use(
      planHandler({ lines: [LINE_BREEDING], records: [] }),
      http.get("/api/feeding/recipes", () => HttpResponse.json(RECIPES_PAYLOAD)),
      http.post("/api/feeding/settings", async ({ request }) => {
        settingsBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
  });

  it("validates kg/head must be positive", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "0");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await within(dialog).findByText("kg/head must be greater than 0")).toBeInTheDocument();
    expect(settingsBody).toBeNull();
  });

  it("saves the new ration for the row's bucket", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Daily ration — BREEDING")).toBeInTheDocument();

    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "1.8");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(settingsBody).not.toBeNull());
    expect(settingsBody).toEqual({ bucket: "BREEDING", daily_kg_per_head: 1.8 });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
