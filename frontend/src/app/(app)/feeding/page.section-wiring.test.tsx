/**
 * Feeding "today" page — the wiring the behavioural suites take for granted:
 * section navigation, bucket-summary emphasis, row identity across a plan
 * refetch, the accessible description of every inline error, the exact
 * bucket/shift vocabulary the dispensing form accepts, and the messages shown
 * when a request never reaches the server at all.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { enumLabel } from "@/lib/enum-labels";
import { addDays, farmToday } from "@/lib/format";
import { ALL_PERMISSIONS, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import FeedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeAll(() => {
  // jsdom lacks the pointer-capture/scroll APIs the Select primitive uses.
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

type PlanShift = { shift: string; pct: number; kg: number; time: string };

type PlanLine = {
  bucket: string;
  recipe_code: string;
  recipe_name: string;
  heads: number;
  kg_per_head: number;
  daily_kg: number;
  shifts: PlanShift[];
};

type PlanRecord = {
  id: number;
  date: string;
  shift: string;
  bucket: string;
  recipe_code: string | null;
  qty_kg: number;
};

const SHIFTS: PlanShift[] = [
  { shift: "MORNING", pct: 40, kg: 8, time: "6:30 AM" },
  { shift: "AFTERNOON", pct: 20, kg: 4, time: "1:30 PM" },
  { shift: "NIGHT", pct: 40, kg: 8, time: "7:30 PM" },
];

const LINE_BREEDING: PlanLine = {
  bucket: "BREEDING",
  recipe_code: "LACTATING_60_40",
  recipe_name: "Lactating 60/40",
  heads: 20,
  kg_per_head: 1,
  daily_kg: 20,
  shifts: SHIFTS,
};

const LINE_MALE_KIDS: PlanLine = {
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

const EMPTY_CATALOG: typeof RECIPES_PAYLOAD = { recipes: [], allocation: [] };

function planPayload(lines: PlanLine[], records: PlanRecord[] = []) {
  return {
    lines,
    records,
    records_total: records.length,
    records_limit: 200,
    dispensed_totals: records.map(({ bucket, recipe_code, shift, qty_kg }) => ({
      bucket,
      recipe_code,
      shift,
      qty_kg,
    })),
  };
}

function planHandler(lines: PlanLine[], records: PlanRecord[] = []) {
  return http.get("/api/feeding/plan", () => HttpResponse.json(planPayload(lines, records)));
}

function recipesHandler(payload: typeof RECIPES_PAYLOAD = RECIPES_PAYLOAD) {
  return http.get("/api/feeding/recipes", () => HttpResponse.json(payload));
}

function settingsHandler() {
  return http.post("/api/feeding/settings", () => new HttpResponse(null, { status: 204 }));
}

function record(
  id: number,
  bucket: string,
  recipe_code: string,
  shift: string,
  qty_kg: number,
): PlanRecord {
  return { id, date: farmToday(), bucket, recipe_code, shift, qty_kg };
}

/** The plan row for the BREEDING line (recipe name is unique to the plan table). */
function planRow(): HTMLElement {
  return screen.getByText("Lactating 60/40").closest("tr") as HTMLElement;
}

async function renderLoaded() {
  const result = renderWithProviders(<FeedingPage />);
  expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
  return result;
}

describe("FeedingPage section navigation", () => {
  beforeEach(() => {
    server.use(planHandler([LINE_BREEDING]), recipesHandler());
  });

  it("links every feeding section", async () => {
    await renderLoaded();
    const nav = screen.getByRole("navigation");

    expect(within(nav).getAllByRole("link")).toHaveLength(3);
    expect(within(nav).getByRole("link", { name: "Today's plan" })).toHaveAttribute(
      "href",
      "/feeding",
    );
    expect(within(nav).getByRole("link", { name: "Recipes" })).toHaveAttribute(
      "href",
      "/feeding/recipes",
    );
    expect(within(nav).getByRole("link", { name: "Inventory" })).toHaveAttribute(
      "href",
      "/feeding/inventory",
    );
  });

  it("emphasises today's plan as the current tab and mutes the others", async () => {
    await renderLoaded();
    const nav = screen.getByRole("navigation");

    expect(within(nav).getByRole("link", { name: "Today's plan" })).toHaveClass(
      "border-primary",
      "font-medium",
      "text-foreground",
    );
    for (const label of ["Recipes", "Inventory"]) {
      const link = within(nav).getByRole("link", { name: label });
      expect(link).toHaveClass("border-transparent", "text-muted-foreground");
      expect(link).not.toHaveClass("border-primary");
    }
  });
});

describe("FeedingPage bucket summary", () => {
  it("emphasises a completed bucket and mutes one still in progress", async () => {
    server.use(
      planHandler(
        [LINE_BREEDING, LINE_MALE_KIDS],
        [
          record(1, "BREEDING", "LACTATING_60_40", "MORNING", 8),
          record(2, "BREEDING", "LACTATING_60_40", "AFTERNOON", 4),
          record(3, "BREEDING", "LACTATING_60_40", "NIGHT", 8),
        ],
      ),
      recipesHandler(),
    );
    await renderLoaded();

    const complete = screen.getByText("complete");
    expect(complete).toHaveAttribute("data-variant", "default");
    expect(complete).toHaveClass("bg-primary", "text-primary-foreground");

    const pending = screen.getByText("0/1 rations");
    expect(pending).toHaveAttribute("data-variant", "secondary");
    expect(pending).toHaveClass("bg-secondary", "text-secondary-foreground");
  });

  it("keeps an open ration editor bound to its own bucket when the plan reorders", async () => {
    let lines = [LINE_BREEDING, LINE_MALE_KIDS];
    server.use(
      http.get("/api/feeding/plan", () => HttpResponse.json(planPayload(lines))),
      recipesHandler(),
    );
    const user = userEvent.setup();
    const { queryClient } = await renderLoaded();

    await user.click(within(planRow()).getAllByRole("button", { name: "Edit" })[0]);
    expect(await screen.findByRole("dialog")).toHaveTextContent("Daily ration — Breeding");

    // A background refetch can legitimately return the same lines in another
    // order; the editor must follow its own line, not the row position.
    lines = [LINE_MALE_KIDS, LINE_BREEDING];
    await queryClient.invalidateQueries({ queryKey: ["/api/feeding/plan"] });
    await waitFor(() => {
      const first = screen.getByText("Fattening 50/50").closest("tr");
      expect(first?.previousElementSibling).toBeNull();
    });

    expect(screen.getByRole("dialog")).toHaveTextContent("Daily ration — Breeding");
  });
});

describe("FeedingPage kg/head editor", () => {
  beforeEach(() => {
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
  });

  it("shows a ration refreshed in the background the first time the editor opens", async () => {
    let kgPerHead = 1;
    let planCalls = 0;
    server.use(
      http.get("/api/feeding/plan", () => {
        planCalls += 1;
        return HttpResponse.json(
          planPayload([{ ...LINE_BREEDING, kg_per_head: kgPerHead, daily_kg: kgPerHead * 20 }]),
        );
      }),
      recipesHandler(),
    );
    const user = userEvent.setup();
    const { queryClient } = await renderLoaded();

    // Another operator raises the ration while this tab sits on the plan and
    // has never opened the editor.
    kgPerHead = 2.2;
    await queryClient.invalidateQueries({ queryKey: ["/api/feeding/plan"] });
    await waitFor(() => expect(planCalls).toBeGreaterThan(1));
    await waitFor(() => expect(planRow()).toHaveTextContent("2.2"));

    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/kg per head per day/)).toHaveValue(2.2);
  });

  it("keeps the ration it confirmed and marks the plan stale", async () => {
    let planCalls = 0;
    let settingsBody: Record<string, unknown> | null = null;
    server.use(
      http.get("/api/feeding/plan", () => {
        planCalls += 1;
        // The read model lags behind the write, so the confirmed value is the
        // only truthful thing to show until it catches up.
        return HttpResponse.json(planPayload([LINE_BREEDING]));
      }),
      recipesHandler(),
      http.post("/api/feeding/settings", async ({ request }) => {
        settingsBody = (await request.json()) as Record<string, unknown>;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await waitFor(() => expect(planCalls).toBe(1));

    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    let dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "1.2345");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(settingsBody).not.toBeNull());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(planCalls).toBeGreaterThan(1));

    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/kg per head per day/)).toHaveValue(1.235);
  });

  it("quantises a multi-digit ration before confirming it", async () => {
    server.use(
      planHandler([{ ...LINE_BREEDING, kg_per_head: 12, daily_kg: 240 }]),
      recipesHandler(),
      settingsHandler(),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "12.3456");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Saved 12.346 kg/head for Breeding."),
    );
  });

  it("reports a generic failure when the ration save never reaches the server", async () => {
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.post("/api/feeding/settings", () => HttpResponse.error()),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "1.8");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Something went wrong"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("describes the kg/head field by its own inline error", async () => {
    server.use(planHandler([LINE_BREEDING]), recipesHandler(), settingsHandler());
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "0");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    const error = await within(dialog).findByRole("alert");
    expect(error).toHaveTextContent("kg/head must be greater than 0");
    expect(error).toHaveAttribute("id", "kg-BREEDING-error");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("kg/head must be greater than 0");
  });

  it("labels the save button while the ration write is in flight", async () => {
    let releaseSetting: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      releaseSetting = resolve;
    });
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.post("/api/feeding/settings", async () => {
        await parked;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "1.8");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    const saving = await within(dialog).findByRole("button", { name: "Saving…" });
    expect(saving).toBeDisabled();
    expect(within(dialog).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();

    releaseSetting?.();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

const VOCABULARY_BUCKETS = [
  "FOUNDATION",
  "PREGNANCY_EARLY",
  "PREGNANCY_LATE",
  "DELIVERY",
  "RECOVERY",
  "RESTING",
  "FEMALE_KIDS",
];

/** One plan line per bucket, each on its own recipe, so a bucket switch has an
 *  unambiguous ration to prefill. */
const VOCABULARY_LINES: PlanLine[] = VOCABULARY_BUCKETS.map((bucket, index) => ({
  bucket,
  recipe_code: `${bucket}_MIX`,
  recipe_name: `${bucket} mix`,
  heads: 4 + index,
  kg_per_head: 1,
  daily_kg: 4 + index,
  shifts: SHIFTS,
}));

describe("FeedingPage dispensing form", () => {
  let dispenseCalls: number;
  let dispenseBody: Record<string, unknown> | null;

  beforeEach(() => {
    dispenseCalls = 0;
    dispenseBody = null;
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    server.use(
      planHandler([LINE_BREEDING, LINE_MALE_KIDS]),
      recipesHandler(),
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

  it("shows the seeded bucket and shift in the closed triggers", async () => {
    const { dialog } = await openDialog();
    const [bucket, shift] = within(dialog).getAllByRole("combobox");

    expect(bucket).toHaveTextContent("Breeding");
    expect(shift).toHaveTextContent("Morning");
  });

  it("confirms a recorded dispensing", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "3");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Dispensing recorded."));
    expect(dispenseCalls).toBe(1);
  });

  it("describes the quantity and date fields by their own inline errors", async () => {
    const { user, dialog } = await openDialog();

    fireEvent.change(within(dialog).getByLabelText("Date"), {
      target: { value: addDays(farmToday(), 1) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    const qty = within(dialog).getByLabelText(/Quantity \(kg\)/);
    const date = within(dialog).getByLabelText("Date");
    await waitFor(() => expect(qty).toHaveAttribute("aria-invalid", "true"));
    expect(qty).toHaveAccessibleDescription("Quantity must be greater than 0");
    expect(date).toHaveAttribute("aria-invalid", "true");
    expect(date).toHaveAccessibleDescription("Date can't be in the future");
    expect(dispenseCalls).toBe(0);
  });

  it("requires a dispensing date rather than letting the server pick one", async () => {
    const { user, dialog } = await openDialog();

    fireEvent.change(within(dialog).getByLabelText("Date"), { target: { value: "" } });
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "4");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    expect(await within(dialog).findByText("Date is required")).toBeInTheDocument();
    expect(dispenseCalls).toBe(0);
  });

  it("offers a catalogued recipe that today's plan does not use", async () => {
    server.use(planHandler([LINE_BREEDING]));
    const { user, dialog } = await openDialog();

    await user.click(within(dialog).getAllByRole("combobox")[2]);
    await user.click(await screen.findByRole("option", { name: "Fattening 50/50" }));
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "9");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    expect(dispenseBody).toMatchObject({ recipe_code: "FATTENING_50_50" });
  });

  it("keeps planned recipes selectable when the catalog fails, and offers nothing else", async () => {
    server.use(
      planHandler([LINE_BREEDING]),
      http.get("/api/feeding/recipes", () =>
        HttpResponse.json({ detail: "catalog down" }, { status: 503 }),
      ),
    );
    const { user, dialog } = await openDialog();

    const recipe = within(dialog).getAllByRole("combobox")[2];
    expect(recipe).toHaveTextContent("Lactating 60/40");
    expect(recipe).not.toHaveTextContent("LACTATING_60_40");

    await user.click(recipe);
    const options = await screen.findAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options[0]).toHaveAccessibleName("Lactating 60/40");
    expect(options[1]).toHaveAccessibleName("Dry roughage only");
  });

  it("opens the dispensing form on a day with no plan lines at all", async () => {
    server.use(planHandler([]), recipesHandler(EMPTY_CATALOG));
    const user = userEvent.setup();
    renderWithProviders(<FeedingPage />);
    await screen.findByText("No active animals — nothing to feed.");

    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getAllByRole("combobox")[0]).toHaveTextContent("Quarantine");
    expect(within(dialog).getAllByRole("combobox")[2]).toHaveTextContent("Dry roughage only");
  });

  it("warns the moment a bucket switch leaves the ration ambiguous, and clears once one is picked", async () => {
    server.use(
      planHandler([
        LINE_BREEDING,
        LINE_MALE_KIDS,
        { ...LINE_MALE_KIDS, recipe_code: "LACTATING_60_40", recipe_name: "Lactating 60/40" },
      ]),
    );
    const user = userEvent.setup();
    renderWithProviders(<FeedingPage />);
    await screen.findByText("Fattening 50/50");
    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getAllByRole("combobox")[0]);
    await user.click(await screen.findByRole("option", { name: "Male kids" }));
    // No submit needed: an ambiguous prefill is reported as it happens.
    expect(await within(dialog).findByText("Pick a recipe")).toBeInTheDocument();

    await user.click(within(dialog).getAllByRole("combobox")[2]);
    await user.click(await screen.findByRole("option", { name: "Fattening 50/50" }));
    await waitFor(() =>
      expect(within(dialog).queryByText("Pick a recipe")).not.toBeInTheDocument(),
    );
    expect(dispenseCalls).toBe(0);
  });

  it("prefills the ration of the chosen bucket, not of the whole plan", async () => {
    server.use(planHandler(VOCABULARY_LINES), recipesHandler(EMPTY_CATALOG));
    const user = userEvent.setup();
    renderWithProviders(<FeedingPage />);
    await screen.findByText("RECOVERY mix");

    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getAllByRole("combobox")[0]);
    await user.click(await screen.findByRole("option", { name: "Recovery" }));
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "5");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    expect(dispenseBody).toMatchObject({ bucket: "RECOVERY", recipe_code: "RECOVERY_MIX" });
  });

  it("records every bucket and shift the API vocabulary allows", async () => {
    const bodies: Record<string, unknown>[] = [];
    server.use(
      planHandler(VOCABULARY_LINES),
      recipesHandler(EMPTY_CATALOG),
      // Rejecting each write keeps one dialog open across the whole
      // vocabulary; the request body is what this test is about.
      http.post("/api/feeding/dispense", async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ detail: "simulated rejection" }, { status: 400 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<FeedingPage />);
    await screen.findByText("FOUNDATION mix");

    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "5");

    for (const bucket of VOCABULARY_BUCKETS) {
      await user.click(within(dialog).getAllByRole("combobox")[0]);
      // The option list is labeled species-aware; the posted value stays raw.
      await user.click(
        await screen.findByRole("option", { name: enumLabel("bucket", bucket) }),
      );
      await user.click(within(dialog).getByRole("button", { name: "Record" }));
      await waitFor(() =>
        expect(bodies.at(-1)).toMatchObject({ bucket, recipe_code: `${bucket}_MIX` }),
      );
    }

    await user.click(within(dialog).getAllByRole("combobox")[1]);
    await user.click(await screen.findByRole("option", { name: "Afternoon" }));
    await user.click(within(dialog).getByRole("button", { name: "Record" }));
    await waitFor(() => expect(bodies.at(-1)).toMatchObject({ shift: "AFTERNOON" }));
  }, 20_000);
});

describe("FeedingPage dispensing history", () => {
  it("loads the whole ledger before any date filter is applied", async () => {
    let params: URLSearchParams | undefined;
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.get("/api/feeding/records", ({ request }) => {
        params ??= new URL(request.url).searchParams;
        return HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 });
      }),
    );
    await renderLoaded();
    await waitFor(() => expect(params).toBeDefined());

    expect(params?.has("date_from")).toBe(false);
    expect(params?.has("date_to")).toBe(false);
    expect(screen.getByLabelText("From date")).toHaveAttribute("max", farmToday());
    expect(screen.getByRole("button", { name: "Clear dates" })).toBeDisabled();
  });

  it("never queries an inverted range and describes both inputs by the error", async () => {
    const requested: string[] = [];
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.get("/api/feeding/records", ({ request }) => {
        const query = new URL(request.url).searchParams;
        requested.push(`${query.get("date_from") ?? ""}|${query.get("date_to") ?? ""}`);
        return HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 });
      }),
    );
    await renderLoaded();
    await waitFor(() => expect(requested).toContain("|"));

    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-02-01" } });
    await waitFor(() => expect(requested).toContain("2026-02-01|"));
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-01-01" } });

    const error = await screen.findByText("From date must be on or before to date.");
    expect(error).toHaveAttribute("id", "feeding-history-range-error");
    expect(screen.getByLabelText("From date")).toHaveAccessibleDescription(
      "From date must be on or before to date.",
    );
    expect(screen.getByLabelText("To date")).toHaveAccessibleDescription(
      "From date must be on or before to date.",
    );

    // Widening the range puts a request back on the wire, proving the
    // inverted one was withheld rather than merely still in flight.
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-03-01" } });
    await waitFor(() => expect(requested).toContain("2026-02-01|2026-03-01"));
    expect(requested).not.toContain("2026-02-01|2026-01-01");
  });

  it("renders a dash for a history entry recorded without a recipe", async () => {
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.get("/api/feeding/records", () =>
        HttpResponse.json({
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
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
    );
    await renderLoaded();

    const card = screen.getByText("Dispensing history").closest('[data-slot="card"]') as HTMLElement;
    const row = within(card).getByText("1 Jan 2026").closest("tr") as HTMLElement;
    expect(within(row).getByText("—")).toBeInTheDocument();
  });

  it("falls back to a generic message when the history request never reaches the server", async () => {
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.get("/api/feeding/records", () => HttpResponse.error()),
    );
    await renderLoaded();

    expect(await screen.findByText("Could not load dispensing history.")).toBeInTheDocument();
  });
});

describe("FeedingPage gating", () => {
  it("waits for permissions instead of announcing a denial", async () => {
    let releasePerms: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      releasePerms = resolve;
    });
    server.use(
      http.get("/api/auth/permissions", async () => {
        await parked;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
      planHandler([LINE_BREEDING]),
      recipesHandler(),
    );
    const { waitForAuthIdle } = renderWithProviders(<FeedingPage />);
    await waitForAuthIdle();

    // The page keeps its real chrome up while permissions settle — the header
    // plus a skeleton, never a bare "Loading…" line or an early denial.
    expect(screen.getByRole("heading", { name: "Feeding — today" })).toBeInTheDocument();
    expect(screen.queryByText("You don't have access to this page.")).not.toBeInTheDocument();

    releasePerms?.();
    expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
  });

  it("falls back to a generic message when the plan request never reaches the server", async () => {
    server.use(
      http.get("/api/feeding/plan", () => HttpResponse.error()),
      recipesHandler(),
    );
    renderWithProviders(<FeedingPage />);

    expect(await screen.findByText("Could not load the feeding plan.")).toBeInTheDocument();
  });
});
