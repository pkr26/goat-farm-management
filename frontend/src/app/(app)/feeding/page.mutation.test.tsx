/**
 * Mutation-hardening tests for the feeding page: URL state sanitisation and
 * write-through (date window + ledger offset), the persisted-kg quantisation
 * toast, species-specific bucket/shift copy, permission gating of the
 * "New animal" link and manage controls, dispensing-form defaults, and
 * re-homing an offset that points past the last ledger page.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { farmToday } from "@/lib/format";

import FeedingPage from "./page";

const nav = vi.hoisted(() => ({ search: "", replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: nav.replace, prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(nav.search),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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

function localToday(): string {
  return farmToday();
}

describe("FeedingPage mutation hardening", () => {
  let historyQueries: URL[];

  beforeEach(() => {
    nav.search = "";
    nav.replace.mockClear();
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    historyQueries = [];
    server.use(
      http.get("/api/feeding/plan", () =>
        HttpResponse.json({
          lines: [LINE_BREEDING],
          records: [
            { id: 1, date: localToday(), shift: "MORNING", bucket: "BREEDING", recipe_code: "LACTATING_60_40", qty_kg: 8 },
          ],
          records_total: 1,
          records_limit: 200,
          dispensed_totals: [
            { bucket: "BREEDING", recipe_code: "LACTATING_60_40", shift: "MORNING", qty_kg: 8 },
          ],
        }),
      ),
      http.get("/api/feeding/recipes", () => HttpResponse.json(RECIPES_PAYLOAD)),
      http.get("/api/feeding/records", ({ request }) => {
        historyQueries.push(new URL(request.url));
        return HttpResponse.json({
          records: [
            { id: 9, date: localToday(), shift: "NIGHT", bucket: "RESTING", recipe_code: "LACTATING_60_40", qty_kg: 3.5 },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }),
    );
  });

  async function renderLoaded() {
    renderWithProviders(<FeedingPage />);
    expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
    await waitFor(() => expect(historyQueries.length).toBeGreaterThan(0));
  }

  it("ignores malformed URL dates but keeps a well-formed window and offset", async () => {
    nav.search = "date_from=garbage&date_to=abc&offset=30";
    await renderLoaded();
    expect(historyQueries[0].searchParams.get("date_from")).toBeNull();
    expect(historyQueries[0].searchParams.get("date_to")).toBeNull();
    expect(historyQueries[0].searchParams.get("offset")).toBe("30");
  });

  it("honours a well-formed URL date window in the ledger query", async () => {
    nav.search = "date_from=2026-08-01&date_to=2026-08-31";
    await renderLoaded();
    expect(historyQueries[0].searchParams.get("date_from")).toBe("2026-08-01");
    expect(historyQueries[0].searchParams.get("date_to")).toBe("2026-08-31");
  });

  it("writes date-filter edits to the URL and homes the offset", async () => {
    nav.search = "offset=30";
    await renderLoaded();

    const from = screen.getByLabelText("From date");
    fireEvent.change(from, { target: { value: "2026-08-01" } });
    expect(nav.replace).toHaveBeenCalledWith("/feeding?date_from=2026-08-01", { scroll: false });
    await waitFor(() => {
      const last = historyQueries[historyQueries.length - 1];
      expect(last.searchParams.get("date_from")).toBe("2026-08-01");
      expect(["0", null]).toContain(last.searchParams.get("offset"));
    });
  });

  it("re-homes an offset that points past the last ledger page", async () => {
    nav.search = "offset=90";
    server.use(
      http.get("/api/feeding/records", ({ request }) => {
        historyQueries.push(new URL(request.url));
        return HttpResponse.json({
          records: [],
          total: 1,
          limit: 50,
          offset: 90,
        });
      }),
    );
    await renderLoaded();
    await waitFor(() =>
      expect(historyQueries.some((q) => q.searchParams.get("offset") === "0")).toBe(true),
    );
  });

  it("quantises a saved ration like the server (1.2345 → 1.235, 1.2344 → 1.234)", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/feeding/settings", () => new HttpResponse(null, { status: 204 })),
    );
    await renderLoaded();

    await user.click(within(planRow()).getByRole("button", { name: "Edit" }));
    const field = await screen.findByLabelText(/kg per head per day/i);
    await user.clear(field);
    await user.type(field, "1.2345");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Saved 1.235 kg/head for Breeding."),
    );

    await user.click(within(planRow()).getByRole("button", { name: "Edit" }));
    const again = await screen.findByLabelText(/kg per head per day/i);
    await user.clear(again);
    await user.type(again, "1.2344");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Saved 1.234 kg/head for Breeding."),
    );
  });

  it("shows the species shift schedule and bucket-switch note for a goat farm", async () => {
    await renderLoaded();
    expect(
      screen.getByText(/Shifts: Morning 6:30 AM \(sweep bunks first\) · Afternoon 1:30 PM · Night 7:30 PM\./),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Resting switches Maintenance → Flush at day 10; Male kids frame-builder → fattening at day 91\./),
    ).toBeInTheDocument();
  });


  it("labels ledger rows with human shift and bucket names", async () => {
    await renderLoaded();
    const row = screen.getByText("3.5").closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("Night")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("Resting")).toBeInTheDocument();
  });

  it("seeds the dispensing form with the sole plan line's bucket, Morning and today", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("combobox", { name: "Bucket" })).toHaveTextContent("Breeding");
    expect(within(dialog).getByRole("combobox", { name: "Shift" })).toHaveTextContent("Morning");
    const date = within(dialog).getByLabelText(/date/i) as HTMLInputElement;
    expect(date.value).toBe(localToday());
    expect(date).toHaveAttribute("max", localToday());
  });

  it("falls back to Quarantine / Morning defaults when the plan is empty", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/feeding/plan", () =>
        HttpResponse.json({ lines: [], records: [], records_total: 0, records_limit: 200, dispensed_totals: [] }),
      ),
    );
    renderWithProviders(<FeedingPage />);
    await user.click(await screen.findByRole("button", { name: "Record dispensing" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("combobox", { name: "Bucket" })).toHaveTextContent("Quarantine");
    expect(within(dialog).getByRole("combobox", { name: "Shift" })).toHaveTextContent("Morning");
  });

  it("hides the New animal shortcut without animals.create", async () => {
    server.use(
      permissionsHandler([
        "feeding.view",
        "feeding.manage",
        "animals.view",
      ]),
    );
    await renderLoaded();
    expect(screen.queryByRole("link", { name: "Add animals" })).toBeNull();
  });

  it("offers the Add animals shortcut on an empty plan with animals.create", async () => {
    server.use(
      http.get("/api/feeding/plan", () =>
        HttpResponse.json({ lines: [], records: [], records_total: 0, records_limit: 200, dispensed_totals: [] }),
      ),
    );
    renderWithProviders(<FeedingPage />);
    const link = await screen.findByRole("link", { name: "Add animals" });
    expect(link).toHaveAttribute("href", "/animals/new");
  });
});

function planRow(): HTMLElement {
  const row = screen.getByText("Lactating 60/40").closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}
