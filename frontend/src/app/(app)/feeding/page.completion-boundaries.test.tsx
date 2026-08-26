/**
 * Feeding "today" page — the branch boundaries the behavioural suites step
 * over: the half-gram completion tolerance, a plan line the server sent
 * without a shift breakdown, the bounded-log notices, which gate decides
 * between the loader and an error, and every condition that decides whether
 * the history panel queries the ledger at all and what it renders.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { addDays, farmToday } from "@/lib/format";
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
  /** The generated shift item is schema-less, so malformed cells are typable. */
  shifts: unknown[];
};

type PlanRecord = {
  id: number;
  date: string;
  shift: string;
  bucket: string;
  recipe_code: string | null;
  qty_kg: number;
};

type DispensedTotal = {
  bucket: string;
  recipe_code: string | null;
  shift: string;
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

type RecipeCatalog = {
  recipes: {
    id: number;
    code: string;
    name: string;
    description: string | null;
    lines: unknown[];
  }[];
  allocation: unknown[];
};

const RECIPES_PAYLOAD: RecipeCatalog = {
  recipes: [
    { id: 1, code: "LACTATING_60_40", name: "Lactating 60/40", description: null, lines: [] },
    { id: 2, code: "FATTENING_50_50", name: "Fattening 50/50", description: null, lines: [] },
  ],
  allocation: [],
};

function planPayload(
  lines: PlanLine[],
  records: PlanRecord[] = [],
  overrides: {
    records_total?: number;
    records_limit?: number;
    dispensed_totals?: DispensedTotal[];
  } = {},
) {
  return {
    lines,
    records,
    records_total: overrides.records_total ?? records.length,
    records_limit: overrides.records_limit ?? 200,
    dispensed_totals:
      overrides.dispensed_totals ??
      records.map(({ bucket, recipe_code, shift, qty_kg }) => ({
        bucket,
        recipe_code,
        shift,
        qty_kg,
      })),
  };
}

function planHandler(
  lines: PlanLine[],
  records: PlanRecord[] = [],
  overrides: {
    records_total?: number;
    records_limit?: number;
    dispensed_totals?: DispensedTotal[];
  } = {},
) {
  return http.get("/api/feeding/plan", () =>
    HttpResponse.json(planPayload(lines, records, overrides)),
  );
}

function recipesHandler(payload: RecipeCatalog = RECIPES_PAYLOAD) {
  return http.get("/api/feeding/recipes", () => HttpResponse.json(payload));
}

function historyHandler(records: PlanRecord[] = [], total = records.length) {
  return http.get("/api/feeding/records", () =>
    HttpResponse.json({ records, total, limit: 50, offset: 0 }),
  );
}

function settingsHandler() {
  return http.post("/api/feeding/settings", () => new HttpResponse(null, { status: 204 }));
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

function historyCard(): HTMLElement {
  return screen.getByText("Dispensing history").closest('[data-slot="card"]') as HTMLElement;
}

async function renderLoaded() {
  const result = renderWithProviders(<FeedingPage />);
  expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
  return result;
}

describe("FeedingPage completion boundaries", () => {
  // Quantities are stored to 0.001 kg, so a ration short by half of the
  // smallest persisted unit is a rounding artefact, not an unfed animal —
  // while anything larger is a genuinely unfinished shift.
  it("counts a ration done at the half-gram tolerance but not beyond it", async () => {
    const trace: PlanLine = {
      bucket: "QUARANTINE",
      recipe_code: "TRACE_MIX",
      recipe_name: "Trace mix",
      heads: 1,
      kg_per_head: 0.001,
      daily_kg: 0.001,
      shifts: [{ shift: "MORNING", pct: 100, kg: 0.001, time: "6:30 AM" }],
    };
    const short: PlanLine = {
      bucket: "RESTING",
      recipe_code: "MAINTENANCE_MIX",
      recipe_name: "Maintenance mix",
      heads: 1,
      kg_per_head: 0.002,
      daily_kg: 0.002,
      shifts: [{ shift: "MORNING", pct: 100, kg: 0.002, time: "6:30 AM" }],
    };
    server.use(
      planHandler([trace, short], [], {
        dispensed_totals: [
          { bucket: "QUARANTINE", recipe_code: "TRACE_MIX", shift: "MORNING", qty_kg: 0.0005 },
          {
            bucket: "RESTING",
            recipe_code: "MAINTENANCE_MIX",
            shift: "MORNING",
            qty_kg: 0.0005,
          },
        ],
      }),
      recipesHandler(),
    );
    renderWithProviders(<FeedingPage />);
    expect(await screen.findByText("Trace mix")).toBeInTheDocument();

    expect(within(rowOf("Trace mix")).getByText("done")).toBeInTheDocument();
    expect(within(rowOf("Maintenance mix")).queryByText("done")).not.toBeInTheDocument();
    expect(screen.getByText("complete")).toBeInTheDocument();
    expect(screen.getByText("0/1 rations")).toBeInTheDocument();
  });

  it("never calls a line done when the plan carries no shift breakdown", async () => {
    server.use(planHandler([{ ...LINE_BREEDING, shifts: [] }]), recipesHandler());
    await renderLoaded();

    // `[].every()` is vacuously true — an unsplit ration must not inherit that.
    expect(within(planRow()).queryByText("done")).not.toBeInTheDocument();
    expect(screen.getByText("0/1 rations")).toBeInTheDocument();
    expect(screen.queryByText("complete")).not.toBeInTheDocument();
  });

  it("reads a malformed shift cell as a zero ration, not as an unknown one", async () => {
    server.use(
      planHandler([
        {
          ...LINE_BREEDING,
          shifts: [
            { shift: "MORNING", pct: 40, kg: 8, time: "6:30 AM" },
            { shift: "AFTERNOON", time: "1:30 PM" },
            null,
          ],
        },
      ]),
      recipesHandler(),
    );
    await renderLoaded();

    const cells = within(planRow()).getAllByRole("cell");
    expect(cells[5]).toHaveTextContent("0.0 / 8.0 kg");
    // A cell without a numeric kg is a zero ration; "—" would read as an
    // unknown plan and hide the fact that nothing is owed for that shift.
    expect(cells[6]).toHaveTextContent("0.0 / 0.0 kg");
    expect(cells[6]).toHaveAttribute("title", "AFTERNOON 1:30 PM");
    expect(cells[7]).toHaveTextContent("0.0 / 0.0 kg");
  });

  it("drops the truncation notices once today's log is whole", async () => {
    const records: PlanRecord[] = [
      {
        id: 1,
        date: farmToday(),
        shift: "MORNING",
        bucket: "BREEDING",
        recipe_code: "LACTATING_60_40",
        qty_kg: 8,
      },
      {
        id: 2,
        date: farmToday(),
        shift: "AFTERNOON",
        bucket: "BREEDING",
        recipe_code: "LACTATING_60_40",
        qty_kg: 4,
      },
    ];
    server.use(planHandler([LINE_BREEDING], records), recipesHandler());
    await renderLoaded();

    expect(screen.getByText("Today's dispensing log (2)")).toBeInTheDocument();
    expect(screen.queryByText(/Today's dispensing log contains/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Showing the latest/)).not.toBeInTheDocument();
  });
});

describe("FeedingPage gating branches", () => {
  it("keeps the loader up while the plan request is still in flight", async () => {
    let planStarted = false;
    let release: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/feeding/plan", async () => {
        planStarted = true;
        await parked;
        return HttpResponse.json(planPayload([LINE_BREEDING]));
      }),
      recipesHandler(),
    );
    renderWithProviders(<FeedingPage />);

    // The plan is only requested once permissions have resolved, so reaching
    // the handler proves this is the plan gate rather than the permission one.
    await waitFor(() => expect(planStarted).toBe(true));
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("Could not load the feeding plan.")).not.toBeInTheDocument();

    release?.();
    expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
  });

  it("waits for permissions instead of reporting a denial", async () => {
    let release: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/auth/permissions", async () => {
        await parked;
        return HttpResponse.json({ is_owner: true, permissions: ["feeding.view"] });
      }),
      planHandler([LINE_BREEDING]),
      recipesHandler(),
    );
    const { waitForAuthIdle } = renderWithProviders(<FeedingPage />);
    await waitForAuthIdle();

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("You don't have access to this page.")).not.toBeInTheDocument();

    release?.();
    expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
  });

  it("never queries the dispensing ledger without feeding.view", async () => {
    let historyCalls = 0;
    server.use(
      permissionsHandler(["tasks.view"]),
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.get("/api/feeding/records", () => {
        historyCalls += 1;
        return HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 });
      }),
    );
    renderWithProviders(<FeedingPage />);

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    expect(historyCalls).toBe(0);
  });
});

describe("FeedingPage history panel branches", () => {
  it("treats a single-day range as a filter worth sending", async () => {
    const ranges: string[] = [];
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.get("/api/feeding/records", ({ request }) => {
        const query = new URL(request.url).searchParams;
        ranges.push(`${query.get("date_from") ?? ""}|${query.get("date_to") ?? ""}`);
        return HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 });
      }),
    );
    await renderLoaded();

    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-01-05" } });
    fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-01-05" } });

    // From == To is one whole day of the ledger, not an inverted range.
    await waitFor(() => expect(ranges).toContain("2026-01-05|2026-01-05"));
    expect(screen.queryByText("From date must be on or before to date.")).not.toBeInTheDocument();
    expect(screen.getByLabelText("From date")).not.toHaveAttribute("aria-invalid");
  });

  it("shows the history loader only while the ledger request is open", async () => {
    let release: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.get("/api/feeding/records", async () => {
        await parked;
        return HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 });
      }),
    );
    await renderLoaded();

    expect(await screen.findByText("Loading history…")).toBeInTheDocument();
    expect(
      screen.queryByText("No dispensing records in this date range."),
    ).not.toBeInTheDocument();

    release?.();
    expect(
      await screen.findByText("No dispensing records in this date range."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Loading history…")).not.toBeInTheDocument();
  });

  it("offers guidance instead of an empty ledger table", async () => {
    server.use(planHandler([LINE_BREEDING]), recipesHandler(), historyHandler([], 0));
    await renderLoaded();

    const card = historyCard();
    expect(
      await within(card).findByText("No dispensing records in this date range."),
    ).toBeInTheDocument();
    expect(within(card).queryByRole("table")).not.toBeInTheDocument();
    expect(within(card).queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Could not load dispensing history.")).not.toBeInTheDocument();
  });

  it("lists ledger rows with their recipe and drops the empty-range guidance", async () => {
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      historyHandler([
        {
          id: 88,
          date: "2026-01-01",
          shift: "NIGHT",
          bucket: "BREEDING",
          recipe_code: "LACTATING_60_40",
          qty_kg: 7.25,
        },
        {
          id: 89,
          date: "2026-01-02",
          shift: "MORNING",
          bucket: "BREEDING",
          recipe_code: null,
          qty_kg: 1.5,
        },
      ]),
    );
    await renderLoaded();

    const card = historyCard();
    const coded = (await within(card).findByText("1 Jan 2026")).closest("tr") as HTMLElement;
    expect(within(coded).getByText("LACTATING_60_40")).toBeInTheDocument();
    const legacy = within(card).getByText("2 Jan 2026").closest("tr") as HTMLElement;
    expect(within(legacy).getByText("—")).toBeInTheDocument();
    expect(
      within(card).queryByText("No dispensing records in this date range."),
    ).not.toBeInTheDocument();
  });

  it("surfaces the ledger's own failure message", async () => {
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.get("/api/feeding/records", () =>
        HttpResponse.json({ detail: "history ledger offline" }, { status: 503 }),
      ),
    );
    await renderLoaded();

    expect(await screen.findByText("history ledger offline")).toBeInTheDocument();
    expect(
      screen.queryByText("No dispensing records in this date range."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Loading history…")).not.toBeInTheDocument();
  });

  it("bounds each date filter by the other and gates the clear control", async () => {
    server.use(planHandler([LINE_BREEDING]), recipesHandler(), historyHandler([], 0));
    await renderLoaded();

    const from = screen.getByLabelText("From date");
    const to = screen.getByLabelText("To date");
    const clear = screen.getByRole("button", { name: "Clear dates" });
    expect(from).toHaveAttribute("max", farmToday());
    expect(to).toHaveAttribute("max", farmToday());
    expect(to).not.toHaveAttribute("min");
    expect(to).not.toHaveAttribute("aria-invalid");
    expect(clear).toBeDisabled();

    fireEvent.change(from, { target: { value: "2026-01-05" } });
    // One date is already a filter worth clearing.
    expect(clear).toBeEnabled();
    expect(to).toHaveAttribute("min", "2026-01-05");

    fireEvent.change(to, { target: { value: "2026-01-20" } });
    expect(from).toHaveAttribute("max", "2026-01-20");
  });
});

describe("FeedingPage section navigation", () => {
  it("marks exactly the open section as the active tab", async () => {
    server.use(planHandler([LINE_BREEDING]), recipesHandler());
    await renderLoaded();

    const nav = screen.getByRole("navigation");
    const links = within(nav).getAllByRole("link");
    const active = links.filter((link) => link.classList.contains("border-primary"));

    expect(active).toHaveLength(1);
    expect(active[0]).toHaveAccessibleName("Today's plan");
    for (const label of ["Recipes", "Inventory"]) {
      expect(within(nav).getByRole("link", { name: label })).toHaveClass(
        "border-transparent",
        "text-muted-foreground",
      );
    }
  });
});

describe("FeedingPage ration editor branches", () => {
  beforeEach(() => {
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
  });

  it("adopts a ration changed in the background before the editor is first opened", async () => {
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

    kgPerHead = 2.4;
    await queryClient.invalidateQueries({ queryKey: ["/api/feeding/plan"] });
    await waitFor(() => expect(planCalls).toBeGreaterThan(1));
    await waitFor(() => expect(planRow()).toHaveTextContent("2.4"));

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/kg per head per day/)).toHaveValue(2.4);
  });

  it("confirms a whole-number ration and keeps it when the editor reopens", async () => {
    // The plan read model still reports the old ration after the write, so
    // reopening must show what the save confirmed rather than that stale value.
    server.use(planHandler([LINE_BREEDING]), recipesHandler(), settingsHandler());
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    let dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "3");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Saved 3 kg/head for BREEDING."),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Edit" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/kg per head per day/)).toHaveValue(3);
  });

  it("keeps a dismissed draft when the write the operator walked away from is rejected", async () => {
    let release: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.post("/api/feeding/settings", async () => {
        await parked;
        return HttpResponse.json({ detail: "ration is locked" }, { status: 409 });
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
    await within(dialog).findByRole("button", { name: "Saving…" });

    // Dismissing a stalled save is deliberately allowed. The close must not
    // adopt the plan's ration over a draft whose write is still unanswered —
    // if the server rejects it, that draft is the only copy of the correction.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    release?.();
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("ration is locked"));
    const trigger = screen.getByRole("button", { name: "Edit" });
    await waitFor(() => expect(trigger).toBeEnabled());

    await user.click(trigger);
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).getByLabelText(/kg per head per day/)).toHaveValue(1.8);
  });

  it("locks the ration form and its submit control while the save is in flight", async () => {
    let release: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
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

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    await user.clear(input);
    await user.type(input, "1.8");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    const saving = await within(dialog).findByRole("button", { name: "Saving…" });
    expect(dialog.querySelector("fieldset")).toBeDisabled();
    // The submit control carries its own disabled state rather than borrowing
    // it from the fieldset it happens to sit in.
    expect(saving).toHaveAttribute("disabled");

    release?.();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("marks the ration field invalid only once it is invalid", async () => {
    server.use(planHandler([LINE_BREEDING]), recipesHandler(), settingsHandler());
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/kg per head per day/);
    expect(input).not.toHaveAttribute("aria-invalid");

    await user.clear(input);
    await user.type(input, "0");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await within(dialog).findByText("kg/head must be greater than 0")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
  });
});

describe("FeedingPage dispensing form branches", () => {
  let dispenseCalls: number;

  beforeEach(() => {
    dispenseCalls = 0;
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    server.use(
      http.post("/api/feeding/dispense", () => {
        dispenseCalls += 1;
        return HttpResponse.json({ id: 10 }, { status: 201 });
      }),
    );
  });

  it("seeds the recipe from today's plan while still offering the whole catalog", async () => {
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler({
        recipes: [
          // Deliberately ordered ahead of the planned ration: the catalog's
          // first entry must not decide what the operator is about to record.
          { id: 7, code: "ALFALFA_ONLY", name: "Alfalfa only", description: null, lines: [] },
          {
            id: 1,
            code: "LACTATING_60_40",
            name: "Lactating 60/40",
            description: null,
            lines: [],
          },
        ],
        allocation: [],
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");
    const recipe = within(dialog).getAllByRole("combobox")[2];
    expect(recipe).toHaveTextContent("Lactating 60/40");
    expect(recipe).not.toHaveTextContent("Alfalfa only");

    await user.click(recipe);
    expect(await screen.findByRole("option", { name: "Alfalfa only" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Dry roughage only" })).toBeInTheDocument();
  });

  it("prefills an unambiguous ration on a bucket switch and reports an ambiguous one at once", async () => {
    server.use(
      planHandler([
        LINE_BREEDING,
        LINE_MALE_KIDS,
        { ...LINE_MALE_KIDS, recipe_code: "LACTATING_60_40", recipe_name: "Lactating 60/40" },
      ]),
      recipesHandler(),
    );
    const user = userEvent.setup();
    renderWithProviders(<FeedingPage />);
    // Two lines share the lactating ration, so the fattening one identifies
    // that the plan has rendered.
    expect(await screen.findByText("Fattening 50/50")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");

    // MALE_KIDS is split across two rations, so nothing can be prefilled and
    // the gap is reported before the operator reaches the submit button.
    await user.click(within(dialog).getAllByRole("combobox")[0]);
    await user.click(await screen.findByRole("option", { name: "MALE_KIDS" }));
    expect(await within(dialog).findByText("Pick a recipe")).toBeInTheDocument();

    // Choosing one clears the same error just as immediately.
    await user.click(within(dialog).getAllByRole("combobox")[2]);
    await user.click(await screen.findByRole("option", { name: "Fattening 50/50" }));
    await waitFor(() =>
      expect(within(dialog).queryByText("Pick a recipe")).not.toBeInTheDocument(),
    );

    // BREEDING has exactly one planned ration, so switching back prefills it.
    await user.click(within(dialog).getAllByRole("combobox")[0]);
    await user.click(await screen.findByRole("option", { name: "BREEDING" }));
    await waitFor(() =>
      expect(within(dialog).getAllByRole("combobox")[2]).toHaveTextContent("Lactating 60/40"),
    );
    expect(within(dialog).queryByText("Pick a recipe")).not.toBeInTheDocument();
    expect(dispenseCalls).toBe(0);
  });

  it("marks only the dispensing fields that are actually invalid", async () => {
    server.use(planHandler([LINE_BREEDING]), recipesHandler());
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");
    const qty = within(dialog).getByLabelText(/Quantity \(kg\)/);
    const date = within(dialog).getByLabelText("Date");
    expect(qty).not.toHaveAttribute("aria-invalid");
    expect(date).not.toHaveAttribute("aria-invalid");

    fireEvent.change(date, { target: { value: addDays(farmToday(), 1) } });
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    expect(await within(dialog).findByText("Date can't be in the future")).toBeInTheDocument();
    expect(qty).toHaveAttribute("aria-invalid", "true");
    expect(date).toHaveAttribute("aria-invalid", "true");
    expect(dispenseCalls).toBe(0);
  });

  it("locks the dispensing submit control while the write is in flight", async () => {
    let release: (() => void) | undefined;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      planHandler([LINE_BREEDING]),
      recipesHandler(),
      http.post("/api/feeding/dispense", async () => {
        dispenseCalls += 1;
        await parked;
        return HttpResponse.json({ id: 10 }, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Quantity \(kg\)/), "5");
    await user.click(within(dialog).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(dispenseCalls).toBe(1));
    const recording = within(dialog).getByRole("button", { name: "Recording…" });
    // Disabled in its own right, not only through the fieldset around it.
    expect(recording).toHaveAttribute("disabled");

    release?.();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
