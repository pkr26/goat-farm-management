/**
 * Feeding "today" page: 3-shift plan table (40/20/40 split, dispensed totals +
 * done badge), dispensing log, record-dispensing dialog (validation + payload
 * mapping), per-bucket kg/head override dialog, and RBAC gating of every
 * manage control.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import FeedingPage from "./page";

// Mutable navigation state so a test can seed the URL the page boots from
// (F-7: the history window survives refresh via the search string).
const nav = vi.hoisted(() => ({ search: "", replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: nav.replace, prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(nav.search),
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

afterEach(() => vi.useRealTimers());

function localToday(): string {
  return farmToday();
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
  const records = Array.isArray(payload.records) ? payload.records : [];
  const dispensedTotals = records.map((record) => {
    const row = record as {
      bucket: string;
      recipe_code: string | null;
      shift: string;
      qty_kg: number;
    };
    return {
      bucket: row.bucket,
      recipe_code: row.recipe_code,
      shift: row.shift,
      qty_kg: row.qty_kg,
    };
  });
  return http.get("/api/feeding/plan", () =>
    HttpResponse.json({
      ...payload,
      records_total: payload.records_total ?? records.length,
      records_limit: payload.records_limit ?? 200,
      dispensed_totals: payload.dispensed_totals ?? dispensedTotals,
    }),
  );
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
          { id: 2, date: localToday(), shift: "AFTERNOON", bucket: "BREEDING", recipe_code: "LACTATING_60_40", qty_kg: 4 },
          { id: 4, date: localToday(), shift: "NIGHT", bucket: "BREEDING", recipe_code: "LACTATING_60_40", qty_kg: 8.05 },
          { id: 5, date: localToday(), shift: "NIGHT", bucket: "BREEDING", recipe_code: null, qty_kg: 12.05 },
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
    expect(cells[0]).toHaveTextContent("Breeding");
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

  it("degrades malformed shift cells to placeholders instead of crashing", async () => {
    server.use(
      planHandler({
        lines: [
          {
            ...LINE_BREEDING,
            shifts: [
              { shift: "MORNING", pct: 40, kg: 8, time: "6:30 AM" },
              { bogus: true },
              null,
            ],
          },
        ],
        records: [],
      }),
      recipesHandler(),
    );
    renderWithProviders(<FeedingPage />);

    const row = (await screen.findByText("Lactating 60/40")).closest("tr") as HTMLElement;
    const cells = within(row).getAllByRole("cell");
    expect(cells[5]).toHaveTextContent("8"); // the valid MORNING cell
    // Malformed cells: fallback "?" label, zeroed kg, column positions kept.
    expect(cells[6]).toHaveTextContent("0");
    expect(cells[6]).toHaveAttribute("title", "? ");
    expect(cells[7]).toHaveTextContent("0");
  });

  it("tracks the exact recipe and shift before marking a ration done", async () => {
    await renderLoaded();

    // Exact LACTATING allocations: 8 + 4 + 8.05 = 20.05. The legacy null
    // recipe record is visible in the bucket-volume summary but cannot satisfy
    // a planned ration.
    const breeding = planRow();
    expect(within(breeding).getByText("20.05 / 20.0 kg")).toBeInTheDocument();
    expect(within(breeding).getByText("Done")).toBeInTheDocument();
    expect(within(breeding).getByText("8.0 / 8.0 kg")).toBeInTheDocument();
    expect(within(breeding).getByText("4.0 / 4.0 kg")).toBeInTheDocument();

    const kids = rowOf("Fattening 50/50");
    expect(within(kids).getByText("6.0 / 15.0 kg")).toBeInTheDocument();
    expect(within(kids).queryByText("Done")).not.toBeInTheDocument();

    expect(screen.getByText("32.1 / 20.0 kg recorded")).toBeInTheDocument();
  });

  it("renders the dispensing log with — for a missing recipe code", async () => {
    await renderLoaded();

    const logRow = screen.getByText("12.05").closest("tr") as HTMLElement;
    expect(within(logRow).getByText("Night")).toBeInTheDocument();
    expect(within(logRow).getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("Nothing dispensed yet today.")).not.toBeInTheDocument();
  });

  it("labels a bounded today log with its exact full-day total", async () => {
    server.use(
      planHandler({
        lines: [LINE_BREEDING],
        records: [
          {
            id: 91,
            date: localToday(),
            shift: "MORNING",
            bucket: "BREEDING",
            recipe_code: "LACTATING_60_40",
            qty_kg: 8,
          },
        ],
        records_total: 225,
        records_limit: 200,
        dispensed_totals: [
          {
            bucket: "BREEDING",
            recipe_code: "LACTATING_60_40",
            shift: "MORNING",
            qty_kg: 8,
          },
          {
            bucket: "BREEDING",
            recipe_code: "LACTATING_60_40",
            shift: "AFTERNOON",
            qty_kg: 4,
          },
          {
            bucket: "BREEDING",
            recipe_code: "LACTATING_60_40",
            shift: "NIGHT",
            qty_kg: 8,
          },
        ],
      }),
      recipesHandler(),
    );
    renderWithProviders(<FeedingPage />);

    expect(await screen.findByText("Today's dispensing log (225)")).toBeInTheDocument();
    expect(
      screen.getByText(/Showing the latest 1 of 225 entries/),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Plan progress and completion remain exact because they use full-day totals computed by the server",
    );
    expect(screen.getByText("complete")).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("20.0 / 20.0 kg recorded")).toBeInTheDocument();
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

  it("surfaces the transport error's status text for a bodyless 500", async () => {
    server.use(
      http.get("/api/feeding/plan", () => new HttpResponse(null, { status: 500 })),
      recipesHandler(),
    );
    renderWithProviders(<FeedingPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Internal Server Error");
    expect(
      within(alert).getByRole("button", { name: "Retry feeding plan" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("No active animals")).not.toBeInTheDocument();
  });

  it("surfaces a recipe-catalog failure while retaining planned recipe options", async () => {
    let recipeCalls = 0;
    server.use(
      planHandler({ lines: [LINE_BREEDING], records: [] }),
      http.get("/api/feeding/recipes", () => {
        recipeCalls += 1;
        return recipeCalls === 1
          ? HttpResponse.json({ detail: "recipe catalog unavailable" }, { status: 503 })
          : HttpResponse.json(RECIPES_PAYLOAD);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<FeedingPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Could not load the recipe catalog",
    );
    await user.click(within(alert).getByRole("button", { name: "Retry recipes" }));
    await waitFor(() => expect(recipeCalls).toBe(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Lactating 60/40");
  });

  it("denies access without feeding.view and never calls the plan endpoint", async () => {
    let planCalls = 0;
    server.use(
      permissionsHandler(["tasks.view"]),
      http.get("/api/feeding/plan", () => {
        planCalls += 1;
        return HttpResponse.json({
          lines: [],
          records: [],
          records_total: 0,
          records_limit: 200,
          dispensed_totals: [],
        });
      }),
    );
    const { waitForAuthIdle } = renderWithProviders(<FeedingPage />);
    await waitForAuthIdle();

    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
    expect(planCalls).toBe(0);
  });

  it("shows a permission error instead of misreporting no access", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 }),
      ),
    );
    renderWithProviders(<FeedingPage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
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
    expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Record dispensing" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    // Recipes are only fetched for managers (dispense dialog select).
    expect(recipesCalls).toBe(0);
  });
});

describe("FeedingPage dispensing history", () => {
  let historyParams: URLSearchParams;

  beforeEach(() => {
    historyParams = new URLSearchParams();
    nav.search = "";
    nav.replace.mockClear();
    server.use(
      planHandler({ lines: [LINE_BREEDING], records: [] }),
      recipesHandler(),
      http.get("/api/feeding/records", ({ request }) => {
        historyParams = new URL(request.url).searchParams;
        return HttpResponse.json({
          records: [
            {
              id: 88,
              date: "2026-01-01",
              shift: "NIGHT",
              bucket: "BREEDING",
              recipe_code: null,
              qty_kg: 7.25,
            },
          ],
          total: 120,
          limit: 50,
          offset: Number(historyParams.get("offset") ?? 0),
        });
      }),
    );
  });

  it("makes backdated dispensing discoverable with exact-total pagination", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const card = screen.getByText("Dispensing history").closest('[data-slot="card"]') as HTMLElement;

    expect(within(card).getByText("1 Jan 2026")).toBeInTheDocument();
    expect(within(card).getByText("7.25")).toBeInTheDocument();
    expect(within(card).getByText("Showing 1–50 of 120 dispensing records")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Next" }));
    await waitFor(() => expect(historyParams.get("offset")).toBe("50"));
  });

  it("sends date filters and clears them without losing history access", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-01-01" },
    });
    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-01-31" },
    });
    await waitFor(() => {
      expect(historyParams.get("date_from")).toBe("2026-01-01");
      expect(historyParams.get("date_to")).toBe("2026-01-31");
    });

    await user.click(screen.getByRole("button", { name: "Clear dates" }));
    await waitFor(() => {
      expect(historyParams.has("date_from")).toBe(false);
      expect(historyParams.has("date_to")).toBe(false);
    });
  });

  it("returns to the first history page whenever either date changes or both clear", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const card = screen.getByText("Dispensing history").closest('[data-slot="card"]') as HTMLElement;

    async function moveToSecondPage() {
      const next = within(card).getByRole("button", { name: "Next" });
      await waitFor(() => expect(next).toBeEnabled());
      await user.click(next);
      await waitFor(() => expect(historyParams.get("offset")).toBe("50"));
    }

    await moveToSecondPage();
    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-01-01" },
    });
    await waitFor(() => expect(historyParams.get("offset")).toBe("0"));

    await moveToSecondPage();
    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-01-31" },
    });
    await waitFor(() => expect(historyParams.get("offset")).toBe("0"));

    await moveToSecondPage();
    await user.click(screen.getByRole("button", { name: "Clear dates" }));
    await waitFor(() => expect(historyParams.get("offset")).toBe("0"));
  });

  it("restores the history window from the URL and writes edits back", async () => {
    // F-7: dateFrom/dateTo/offset persist in the search string, so a refresh
    // (or a shared link) reopens the same slice of the ledger.
    nav.search = "date_from=2026-01-01&date_to=2026-01-31&offset=50";
    await renderLoaded();

    await waitFor(() => {
      expect(historyParams.get("date_from")).toBe("2026-01-01");
      expect(historyParams.get("date_to")).toBe("2026-01-31");
      expect(historyParams.get("offset")).toBe("50");
    });
    expect(screen.getByLabelText("From date")).toHaveValue("2026-01-01");
    expect(screen.getByLabelText("To date")).toHaveValue("2026-01-31");
    // Defaults never pollute the URL: nothing was written before an edit.
    expect(nav.replace).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-01-05" },
    });
    await waitFor(() =>
      expect(nav.replace).toHaveBeenCalledWith(
        "/feeding?date_from=2026-01-05&date_to=2026-01-31",
        { scroll: false },
      ),
    );
    // A date edit returns the ledger to its first page.
    await waitFor(() => expect(historyParams.get("offset")).toBe("0"));
  });

  it("blocks an inverted date range before sending it", async () => {
    await renderLoaded();
    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-02-01" },
    });
    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-01-01" },
    });

    expect(
      await screen.findByText("From date must be on or before to date."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("From date")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("To date")).toHaveAttribute("aria-invalid", "true");
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
        return HttpResponse.json({
          lines: [LINE_BREEDING, LINE_MALE_KIDS],
          records: [],
          records_total: 0,
          records_limit: 200,
          dispensed_totals: [],
        });
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

  it("refreshes the dispensing date when a long-lived tab crosses midnight", async () => {
    vi.setSystemTime(new Date("2026-08-09T12:30:00Z")); // 18:00 IST
    const user = userEvent.setup();
    await renderLoaded();

    vi.setSystemTime(new Date("2026-08-10T12:30:00Z")); // 18:00 IST, next day
    await user.click(screen.getByRole("button", { name: "Record dispensing" }));

    const dateInput = await screen.findByLabelText("Date");
    expect(dateInput).toHaveValue("2026-08-10");
    expect(dateInput).toHaveAttribute("max", "2026-08-10");
  });

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

  it("rejects a non-zero quantity below the persisted half-gram boundary", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "0.0004");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));
    expect(await within(dialog).findByText("Quantity must be at least 0.0005 kg"))
      .toBeInTheDocument();
    expect(dispenseCalls).toBe(0);
  });

  // REGRESSION — qty_kg had no client-side maximum although the backend's
  // QuantityKgFloat rejects anything above 1,000,000 kg, so a fat-fingered
  // dispense passed validation and only failed with an opaque server 422.
  it("rejects a quantity above the backend's 1,000,000 kg cap", async () => {
    const { user, dialog } = await openDialog();

    expect(within(dialog).getByLabelText(/Quantity \(kg\)/)).toHaveAttribute("max", "1000000");
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "2000000");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));
    expect(
      await within(dialog).findByText("Quantity cannot exceed 1,000,000 kg"),
    ).toBeInTheDocument();
    expect(dispenseCalls).toBe(0);
  });

  it("rejects a future date (typed input bypasses the max attribute)", async () => {
    const { user, dialog } = await openDialog();

    fireEvent.change(within(dialog).getByLabelText("Date"), {
      target: { value: addDays(farmToday(), 1) },
    });
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "4");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    expect(
      await within(dialog).findByText("Date can't be in the future"),
    ).toBeInTheDocument();
    expect(dispenseCalls).toBe(0);
  });

  it("accepts a backdated record so it can appear in dated history", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Date"), {
      target: { value: "2026-01-01" },
    });
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "4");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    expect(dispenseBody).toMatchObject({ date: "2026-01-01", qty_kg: 4 });
  });

  it("defaults to the planned recipe and invalidates the plan", async () => {
    const { user, dialog } = await openDialog();
    const callsBefore = planCalls;

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "7.5");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    expect(dispenseBody).toEqual({
      bucket: "BREEDING", // default: first plan line's bucket
      shift: "MORNING",
      recipe_code: "LACTATING_60_40",
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
    await user.click(await screen.findByRole("option", { name: "Male kids" }));

    // Shift select → NIGHT.
    await user.click(within(dialog).getAllByRole("combobox")[1]);
    await user.click(await screen.findByRole("option", { name: "Night" }));

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

  it("never offers a null-recipe option", async () => {
    const { dialog } = await openDialog();
    const recipeTrigger = within(dialog).getAllByRole("combobox")[2];
    await userEvent.setup().click(recipeTrigger);
    expect(screen.queryByRole("option", { name: /none/i })).not.toBeInTheDocument();
  });

  it("records the explicit dry-roughage ration even when it is not in the recipe catalog", async () => {
    server.use(
      planHandler({
        lines: [
          {
            ...LINE_BREEDING,
            bucket: "QUARANTINE",
            recipe_code: "DRY_ROUGHAGE_ONLY",
            recipe_name: "Dry roughage only",
          },
        ],
        records: [],
      }),
      http.get("/api/feeding/recipes", () =>
        HttpResponse.json({ recipes: [], allocation: [] }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<FeedingPage />);

    expect(await screen.findByText("Dry roughage only")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByRole("combobox")[2]).toHaveTextContent("Dry roughage only");
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "3");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    expect(dispenseBody).toMatchObject({
      bucket: "QUARANTINE",
      recipe_code: "DRY_ROUGHAGE_ONLY",
      qty_kg: 3,
    });
  });

  // REGRESSION — DRY_ROUGHAGE_ONLY is a virtual code with no feed_recipes row,
  // so it only ever entered the dropdown via today's plan. Once the quarantine
  // animals passed bucket-day 3 the option vanished and the stover actually fed
  // on an earlier day could never be recorded (or debited from inventory).
  it("offers the dry-roughage ration even when today's plan does not use it", async () => {
    const { user, dialog } = await openDialog();

    await user.click(within(dialog).getAllByRole("combobox")[2]);
    await user.click(await screen.findByRole("option", { name: "Dry roughage only" }));
    fireEvent.change(within(dialog).getByLabelText("Date"), {
      target: { value: "2026-01-01" },
    });
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "48");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    expect(dispenseBody).toMatchObject({
      recipe_code: "DRY_ROUGHAGE_ONLY",
      qty_kg: 48,
      date: "2026-01-01",
    });
  });

  // REGRESSION — a bucket split by age carries two plan lines; the handler used
  // `.find(...)`, silently pre-selecting the alphabetically-first recipe and
  // debiting the wrong finished-stock balance.
  it("clears the recipe instead of guessing when a bucket has two plan lines", async () => {
    server.use(
      planHandler({
        lines: [
          LINE_BREEDING,
          LINE_MALE_KIDS,
          {
            ...LINE_MALE_KIDS,
            recipe_code: "LACTATING_60_40",
            recipe_name: "Lactating 60/40",
          },
        ],
        records: [],
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<FeedingPage />);
    await screen.findByText("Fattening 50/50");
    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getAllByRole("combobox")[0]);
    await user.click(await screen.findByRole("option", { name: "Male kids" }));

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "6");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    expect(await within(dialog).findByText("Pick a recipe")).toBeInTheDocument();
    expect(dispenseCalls).toBe(0);
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
    expect(toast.error).toHaveBeenCalledWith("insufficient feed mixed");
  });
});

describe("FeedingPage kg/head override dialog", () => {
  let settingsBody: Record<string, unknown> | null;

  beforeEach(() => {
    vi.mocked(toast.error).mockClear();
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

  // REGRESSION — daily_kg_per_head had no client-side maximum although the
  // backend's QuantityKgFloat rejects anything above 1,000,000 kg, so a
  // fat-fingered override passed validation and only failed with an opaque
  // server 422.
  it("rejects a kg/head value above the backend's 1,000,000 kg cap", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "2000000");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(
      await within(dialog).findByText("Quantity cannot exceed 1,000,000 kg"),
    ).toBeInTheDocument();
    expect(settingsBody).toBeNull();
  });

  it("saves the new ration for the row's bucket", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Daily ration — Breeding")).toBeInTheDocument();

    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "1.8");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(settingsBody).not.toBeNull());
    expect(settingsBody).toEqual({ bucket: "BREEDING", daily_kg_per_head: 1.8 });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("reports a rejected ration save and keeps the editor open", async () => {
    server.use(
      http.post("/api/feeding/settings", () =>
        HttpResponse.json({ detail: "ration is locked" }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "1.8");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("ration is locked"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(input).toHaveValue(1.8);
  });

  it("shows the refreshed ration value when the editor is reopened", async () => {
    let currentKg = 1;
    server.use(
      http.get("/api/feeding/plan", () =>
        HttpResponse.json({
          lines: [{ ...LINE_BREEDING, kg_per_head: currentKg, daily_kg: currentKg * 20 }],
          records: [],
          records_total: 0,
          records_limit: 200,
          dispensed_totals: [],
        }),
      ),
      http.post("/api/feeding/settings", async ({ request }) => {
        const body = (await request.json()) as { daily_kg_per_head: number };
        currentKg = body.daily_kg_per_head;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    let dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "1.8");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(currentKg).toBe(1.8));

    await user.click(screen.getByRole("button", { name: "Edit" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/kg per head per day/)).toHaveValue(1.8);
  });

  it("preserves an open ration draft across a background plan refresh", async () => {
    let currentKg = 1;
    let planCalls = 0;
    server.use(
      http.get("/api/feeding/plan", () => {
        planCalls += 1;
        return HttpResponse.json({
          lines: [{ ...LINE_BREEDING, kg_per_head: currentKg, daily_kg: currentKg * 20 }],
          records: [],
          records_total: 0,
          records_limit: 200,
          dispensed_totals: [],
        });
      }),
    );
    const user = userEvent.setup();
    const { queryClient } = renderWithProviders(<FeedingPage />);
    await screen.findByText("Lactating 60/40");

    await user.click(screen.getByRole("button", { name: "Edit" }));
    let dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "1.8");

    // Another write/operator changes the server value while this operator is
    // still editing. The refetch should update the next-open baseline, not
    // call reset() over the live draft.
    currentKg = 2.2;
    await queryClient.invalidateQueries({ queryKey: ["/api/feeding/plan"] });
    await waitFor(() => expect(planCalls).toBeGreaterThan(1));
    await waitFor(() => expect(planRow()).toHaveTextContent("2.2"));
    expect(input).toHaveValue(1.8);

    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Edit" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/kg per head per day/)).toHaveValue(2.2);
  });

  it("does not reopen a ration editor while its dismissed save is pending", async () => {
    let settingsCalls = 0;
    let releaseSetting: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      releaseSetting = resolve;
    });
    server.use(
      http.post("/api/feeding/settings", async () => {
        settingsCalls += 1;
        await parked;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "1.8");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(settingsCalls).toBe(1));
    expect(dialog.querySelector("fieldset")).toBeDisabled();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const trigger = screen.getByRole("button", { name: "Edit" });
    expect(trigger).toBeDisabled();
    await user.click(trigger);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    releaseSetting?.();
    await waitFor(() => expect(trigger).toBeEnabled());
    expect(settingsCalls).toBe(1);
  });
});
