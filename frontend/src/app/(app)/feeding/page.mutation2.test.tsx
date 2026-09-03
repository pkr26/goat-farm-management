/**
 * Mutation-hardening round 2 for the feeding page. Walks the remaining
 * survivor clusters from reports/mutation/app1.json:
 *
 *  - urlDate anchor strictness (junk glued to a well-formed date),
 *  - external URL adoption after mount + composing writes onto adopted
 *    params (the ref/readers effect and the write-through callback),
 *  - every writeUrlState call site (To-date edit, clear, offset paging),
 *  - the ledger re-home arithmetic (exact boundary, empty ledger, total 0),
 *  - placeholder paging ("Updating dispensing history…" + previous rows),
 *  - the empty-state clear-dates CTA gating,
 *  - retry wiring (plan error, permissions error),
 *  - species vocabulary on every bucket surface (toast, closed select
 *    trigger, summary card, plan cells, mobile cards, log/ledger rows),
 *  - mobile plan cards (Done suffix, recorded volume, spacing, Edit gating),
 *  - two-digit ration quantisation (12.0005 → 12.001),
 *  - the Add-animals shortcut styling.
 *
 * Mutants that are provably equivalent (regex anchors unreachable for
 * validated numeric input, titleCase-identical shift labels, guards
 * subsumed by construction) are documented in the mutation report, not
 * chased here.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ALL_PERMISSIONS,
  permissionsHandler,
  server,
  TEST_FARMS,
} from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { farmToday } from "@/lib/format";

import FeedingPage from "./page";

// The URL mock commits writes: router.replace updates the search string the
// next render reads, exactly like a real soft navigation. Tests that need a
// pristine URL simply re-seed nav.search in beforeEach.
const nav = vi.hoisted(() => {
  const self = { search: "", replace: vi.fn() };
  self.replace.mockImplementation((url: string) => {
    self.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
  });
  return self;
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: nav.replace, prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(nav.search),
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

/** Plan payload builder — records default to no dispensing. */
function planPayload(
  lines: unknown[],
  opts: {
    records?: unknown[];
    records_total?: number;
    records_limit?: number;
    dispensed_totals?: unknown[];
  } = {},
) {
  const records = opts.records ?? [];
  return {
    lines,
    records,
    records_total: opts.records_total ?? records.length,
    records_limit: opts.records_limit ?? 200,
    dispensed_totals: opts.dispensed_totals ?? [],
  };
}

function historyCard(): HTMLElement {
  return screen.getByText("Dispensing history").closest('[data-slot="card"]') as HTMLElement;
}

describe("FeedingPage mutation hardening 2", () => {
  let historyQueries: URL[];

  beforeEach(() => {
    nav.search = "";
    nav.replace.mockClear();
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    historyQueries = [];
    server.use(
      http.get("/api/feeding/plan", () =>
        HttpResponse.json(planPayload([LINE_BREEDING])),
      ),
      http.get("/api/feeding/recipes", () => HttpResponse.json(RECIPES_PAYLOAD)),
      http.get("/api/feeding/records", ({ request }) => {
        historyQueries.push(new URL(request.url));
        return HttpResponse.json({
          records: [
            {
              id: 9,
              date: "2026-01-01",
              shift: "NIGHT",
              bucket: "RESTING",
              recipe_code: "LACTATING_60_40",
              qty_kg: 3.5,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }),
    );
  });

  async function renderLoaded() {
    const result = renderWithProviders(<FeedingPage />);
    expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
    await waitFor(() => expect(historyQueries.length).toBeGreaterThan(0));
    return result;
  }

  describe("URL date window", () => {
    it("rejects URL dates with stray digits glued to a well-formed date", async () => {
      // Anchors on both ends: "12026-08-01" must not match by suffix and
      // "2026-08-319" must not match by prefix — neither may reach the API.
      nav.search = "date_from=12026-08-01&date_to=2026-08-319";
      await renderLoaded();
      expect(historyQueries[0].searchParams.get("date_from")).toBeNull();
      expect(historyQueries[0].searchParams.get("date_to")).toBeNull();
    });

    it("adopts an external URL change after mount and composes further writes onto it", async () => {
      const user = userEvent.setup();
      // A ledger big enough that offset=50 is a valid page (no re-homing).
      server.use(
        http.get("/api/feeding/records", ({ request }) => {
          historyQueries.push(new URL(request.url));
          const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
          return HttpResponse.json({
            records: [
              {
                id: 9,
                date: "2026-01-01",
                shift: "NIGHT",
                bucket: "RESTING",
                recipe_code: null,
                qty_kg: 3.5,
              },
            ],
            total: 120,
            limit: 50,
            offset,
          });
        }),
      );
      const { rerender } = await renderLoaded();

      // An external navigation (sidebar link back, restored history entry)
      // changes the params this page did not write — the re-render a real
      // navigation triggers makes the page re-seed its local mirrors.
      nav.search = "date_from=2026-08-01&date_to=2026-08-31&offset=50";
      rerender(<FeedingPage />);

      await waitFor(() => {
        const adopted = historyQueries.filter(
          (q) => q.searchParams.get("date_from") === "2026-08-01",
        );
        expect(adopted.length).toBeGreaterThan(0);
        expect(adopted.some((q) => q.searchParams.get("offset") === "50")).toBe(true);
      });
      await waitFor(() =>
        expect(historyQueries.some((q) => q.searchParams.get("date_to") === "2026-08-31")).toBe(true),
      );
      expect(screen.getByLabelText("To date")).toHaveValue("2026-08-31");

      // A subsequent page write must compose onto the adopted params, not
      // onto the (stale) params the page booted from.
      await user.click(within(historyCard()).getByRole("button", { name: "Next" }));
      await waitFor(() =>
        expect(nav.replace).toHaveBeenCalledWith(
          "/feeding?date_from=2026-08-01&date_to=2026-08-31&offset=100",
          { scroll: false },
        ),
      );
    });
  });

  describe("write-through call sites", () => {
    it("writes the To-date edit to the URL", async () => {
      await renderLoaded();
      fireEvent.change(screen.getByLabelText("To date"), {
        target: { value: "2026-08-31" },
      });
      expect(nav.replace).toHaveBeenCalledWith("/feeding?date_to=2026-08-31", {
        scroll: false,
      });
    });

    it("strips the whole date window from the URL when the dates clear", async () => {
      await renderLoaded();
      fireEvent.change(screen.getByLabelText("From date"), {
        target: { value: "2026-01-01" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Clear dates" }));
      await waitFor(() =>
        expect(nav.replace).toHaveBeenLastCalledWith("/feeding", { scroll: false }),
      );
    });

    it("never rewrites the URL when the offset is already home at zero", async () => {
      nav.search = "offset=0";
      server.use(
        http.get("/api/feeding/records", ({ request }) => {
          historyQueries.push(new URL(request.url));
          return HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 });
        }),
      );
      renderWithProviders(<FeedingPage />);
      expect(
        await screen.findByText("No dispensing records in this date range."),
      ).toBeInTheDocument();
      await waitFor(() => expect(historyQueries.length).toBeGreaterThan(0));
      expect(nav.replace).not.toHaveBeenCalled();
    });
  });

  describe("ledger offset re-homing", () => {
    it("re-homes an offset that lands exactly on the last entry to the last page", async () => {
      nav.search = "offset=150";
      server.use(
        http.get("/api/feeding/records", ({ request }) => {
          historyQueries.push(new URL(request.url));
          // 150 entries: page size 50 → last page starts at 100, not 0/149/150.
          return HttpResponse.json({ records: [], total: 150, limit: 50, offset: 150 });
        }),
      );
      await renderLoaded();
      await waitFor(() =>
        expect(
          historyQueries.some((q) => q.searchParams.get("offset") === "100"),
        ).toBe(true),
      );
      expect(nav.replace).toHaveBeenCalledWith("/feeding?offset=100", { scroll: false });
    });

    it("re-homes an out-of-range offset to zero when the ledger is empty", async () => {
      nav.search = "offset=50";
      server.use(
        http.get("/api/feeding/records", ({ request }) => {
          historyQueries.push(new URL(request.url));
          return HttpResponse.json({ records: [], total: 0, limit: 50, offset: 50 });
        }),
      );
      await renderLoaded();
      await waitFor(() =>
        expect(
          historyQueries.some((q) => Number(q.searchParams.get("offset") ?? 0) === 0),
        ).toBe(true),
      );
      expect(nav.replace).toHaveBeenCalledWith("/feeding", { scroll: false });
      expect(
        historyQueries.some((q) => q.searchParams.get("offset") === "-50"),
      ).toBe(false);
    });
  });

  describe("placeholder paging", () => {
    it("keeps the previous page up and announces the update while the next page loads", async () => {
      const user = userEvent.setup();
      let calls = 0;
      let release: (() => void) | undefined;
      const parked = new Promise<void>((resolve) => {
        release = resolve;
      });
      server.use(
        http.get("/api/feeding/records", async ({ request }) => {
          calls += 1;
          historyQueries.push(new URL(request.url));
          const offset = new URL(request.url).searchParams.get("offset") ?? "0";
          if (calls >= 2) await parked;
          return HttpResponse.json({
            records:
              offset === "0"
                ? [
                    {
                      id: 88,
                      date: "2026-01-01",
                      shift: "NIGHT",
                      bucket: "RESTING",
                      recipe_code: null,
                      qty_kg: 7.25,
                    },
                  ]
                : [
                    {
                      id: 89,
                      date: "2026-01-15",
                      shift: "MORNING",
                      bucket: "RESTING",
                      recipe_code: null,
                      qty_kg: 1.5,
                    },
                  ],
            total: 120,
            limit: 50,
            offset: Number(offset),
          });
        }),
      );
      await renderLoaded();

      // Steady state: no update notice, previous rows visible, no loader.
      expect(await within(historyCard()).findByText("1 Jan 2026")).toBeInTheDocument();
      expect(screen.queryByText("Updating dispensing history…")).toBeNull();

      await user.click(within(historyCard()).getByRole("button", { name: "Next" }));
      await waitFor(() => expect(calls).toBe(2));

      // While the next page settles: the update notice shows, the previous
      // page stays rendered (placeholder data), and no full loader flashes.
      expect(await screen.findByText("Updating dispensing history…")).toBeInTheDocument();
      expect(screen.getByText("1 Jan 2026")).toBeInTheDocument();
      expect(screen.queryByText("Loading history…")).toBeNull();

      release?.();
      await waitFor(() =>
        expect(screen.queryByText("Updating dispensing history…")).toBeNull(),
      );
      expect(await within(historyCard()).findByText("15 Jan 2026")).toBeInTheDocument();
    });
  });

  describe("empty-state clear CTA", () => {
    beforeEach(() => {
      server.use(
        http.get("/api/feeding/records", ({ request }) => {
          historyQueries.push(new URL(request.url));
          return HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0 });
        }),
      );
    });

    it("offers no clear-dates CTA when no filter is active", async () => {
      await renderLoaded();
      expect(
        await screen.findByText("No dispensing records in this date range."),
      ).toBeInTheDocument();
      const clearButtons = screen.getAllByRole("button", { name: "Clear dates" });
      expect(clearButtons).toHaveLength(1); // toolbar only — the CTA needs a filter
      expect(clearButtons[0]).toBeDisabled();
    });

    it("offers the clear-dates CTA once either date is set", async () => {
      await renderLoaded();
      fireEvent.change(screen.getByLabelText("From date"), {
        target: { value: "2026-01-05" },
      });
      await waitFor(() => {
        expect(screen.getAllByRole("button", { name: "Clear dates" })).toHaveLength(2);
      });
    });
  });

  describe("retry wiring", () => {
    it("retries the feeding plan from the error state", async () => {
      const user = userEvent.setup();
      let planCalls = 0;
      server.use(
        http.get("/api/feeding/plan", () => {
          planCalls += 1;
          return planCalls === 1
            ? HttpResponse.json({ detail: "plan blew up" }, { status: 500 })
            : HttpResponse.json(planPayload([LINE_BREEDING]));
        }),
      );
      renderWithProviders(<FeedingPage />);

      expect(await screen.findByText("plan blew up")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Retry feeding plan" }));
      expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
      expect(planCalls).toBe(2);
    });

    it("retries permissions from the dead-end error", async () => {
      const user = userEvent.setup();
      let permsCalls = 0;
      server.use(
        http.get("/api/auth/permissions", () => {
          permsCalls += 1;
          return permsCalls === 1
            ? HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 })
            : HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
        }),
      );
      renderWithProviders(<FeedingPage />);

      expect(
        await screen.findByText("Could not load your permissions — refresh the page to try again."),
      ).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Retry permissions" }));
      expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();
      expect(permsCalls).toBe(2);
    });
  });

  describe("species vocabulary surfaces", () => {
    it("labels the dairy bucket in the ration confirmation toast", async () => {
      const user = userEvent.setup();
      server.use(
        http.get("/api/auth/farms", () =>
          HttpResponse.json([{ ...TEST_FARMS[0], farm_type: "BUFFALO_DAIRY" }]),
        ),
        http.post("/api/feeding/settings", () => new HttpResponse(null, { status: 204 })),
      );
      await renderLoaded();

      await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
      const dialog = await screen.findByRole("dialog");
      const input = within(dialog).getByLabelText(/kg per head per day/);
      await user.clear(input);
      await user.type(input, "2.5");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(toast.success).toHaveBeenCalledWith("Saved 2.5 kg/head for Awaiting AI."),
      );
    });

    it("labels the closed bucket trigger from the items map, not the raw code", async () => {
      const user = userEvent.setup();
      await renderLoaded();

      await user.click(screen.getByRole("button", { name: "Record dispensing" }));
      const dialog = await screen.findByRole("dialog");
      const bucketTrigger = within(dialog).getAllByRole("combobox")[0];
      await user.click(bucketTrigger);
      await user.click(await screen.findByRole("option", { name: "Male kids" }));

      expect(bucketTrigger).toHaveTextContent("Male kids");
      expect(bucketTrigger).not.toHaveTextContent("MALE_KIDS");
    });

    it("renders the Pregnancy A bucket label on every plan surface", async () => {
      server.use(
        http.get("/api/feeding/plan", () =>
          HttpResponse.json(
            planPayload([
              {
                ...LINE_BREEDING,
                bucket: "PREGNANCY_EARLY",
                recipe_code: "PREG_TRACE_MIX",
                recipe_name: "Trace mix",
              },
            ]),
          ),
        ),
      );
      renderWithProviders(<FeedingPage />);
      expect(await screen.findByText("Trace mix")).toBeInTheDocument();

      // Desktop cell + bucket summary card both read the species label
      // ("Pregnancy A"), never the Title-Case fallback ("Pregnancy Early").
      expect(screen.getAllByText("Pregnancy A")).toHaveLength(2);
      // Mobile card: "Pregnancy A — Trace mix" (and the card must exist at all).
      expect(screen.getByText(/Pregnancy A — Trace mix/)).toBeInTheDocument();
    });

    it("labels the dispensing log and history rows with the species bucket vocabulary", async () => {
      server.use(
        http.get("/api/feeding/plan", () =>
          HttpResponse.json(
            planPayload([LINE_BREEDING], {
              records: [
                {
                  id: 1,
                  date: localToday(),
                  shift: "NIGHT",
                  bucket: "MALE_KIDS",
                  recipe_code: "LACTATING_60_40",
                  qty_kg: 3.5,
                },
              ],
            }),
          ),
        ),
        http.get("/api/feeding/records", ({ request }) => {
          historyQueries.push(new URL(request.url));
          return HttpResponse.json({
            records: [
              {
                id: 9,
                date: "2026-01-01",
                shift: "MORNING",
                bucket: "MALE_KIDS",
                recipe_code: null,
                qty_kg: 2.25,
              },
            ],
            total: 1,
            limit: 50,
            offset: 0,
          });
        }),
      );
      renderWithProviders(<FeedingPage />);

      const logRow = (await screen.findByText("3.5")).closest("tr") as HTMLElement;
      expect(within(logRow).getByText("Male kids")).toBeInTheDocument();
      const historyRow = (await within(historyCard()).findByText("2.25"))
        .closest("tr") as HTMLElement;
      expect(within(historyRow).getByText("Male kids")).toBeInTheDocument();
    });

    it("keeps the spacing between the shift schedule and the pen-switch note", async () => {
      await renderLoaded();
      expect(
        screen.getByText(/Night 7:30 PM\. Resting switches Maintenance → Flush at day 10/),
      ).toBeInTheDocument();
    });
  });

  describe("mobile plan cards", () => {
    function mobileCompletePlan() {
      server.use(
        http.get("/api/feeding/plan", () =>
          HttpResponse.json(
            planPayload([LINE_BREEDING], {
              records: [
                { id: 1, date: localToday(), shift: "MORNING", bucket: "BREEDING", recipe_code: "LACTATING_60_40", qty_kg: 8 },
                { id: 2, date: localToday(), shift: "AFTERNOON", bucket: "BREEDING", recipe_code: "LACTATING_60_40", qty_kg: 4 },
                { id: 3, date: localToday(), shift: "NIGHT", bucket: "BREEDING", recipe_code: "LACTATING_60_40", qty_kg: 8 },
              ],
              dispensed_totals: [
                { bucket: "BREEDING", recipe_code: "LACTATING_60_40", shift: "MORNING", qty_kg: 8 },
                { bucket: "BREEDING", recipe_code: "LACTATING_60_40", shift: "AFTERNOON", qty_kg: 4 },
                { bucket: "BREEDING", recipe_code: "LACTATING_60_40", shift: "NIGHT", qty_kg: 8 },
              ],
            }),
          ),
        ),
      );
    }

    it("shows the exact recorded volume and the Done suffix on a complete card", async () => {
      mobileCompletePlan();
      renderWithProviders(<FeedingPage />);
      expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();

      expect(screen.getByText("20 heads · 1 kg/head · 20.0 kg/day")).toBeInTheDocument();
      expect(screen.getByText("Recorded 20.0 / 20.0 kg · Done")).toBeInTheDocument();
    });

    it("withholds Done from an incomplete card even when one shift is fully met", async () => {
      server.use(
        http.get("/api/feeding/plan", () =>
          HttpResponse.json(
            planPayload([LINE_BREEDING], {
              records: [
                { id: 1, date: localToday(), shift: "MORNING", bucket: "BREEDING", recipe_code: "LACTATING_60_40", qty_kg: 8 },
              ],
              dispensed_totals: [
                { bucket: "BREEDING", recipe_code: "LACTATING_60_40", shift: "MORNING", qty_kg: 8 },
              ],
            }),
          ),
        ),
      );
      renderWithProviders(<FeedingPage />);
      expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();

      // Morning is met (8/8) but the card as a whole is not done.
      expect(screen.getByText("Recorded 8.0 / 20.0 kg")).toBeInTheDocument();
      expect(screen.queryByText("Recorded 8.0 / 20.0 kg · Done")).toBeNull();
      expect(screen.queryByText(/Stryker was here/)).toBeNull();
    });

    it("withholds Done from an unsplit ration on mobile", async () => {
      server.use(
        http.get("/api/feeding/plan", () =>
          HttpResponse.json(planPayload([{ ...LINE_BREEDING, shifts: [] }])),
        ),
      );
      renderWithProviders(<FeedingPage />);
      expect(await screen.findByText("Lactating 60/40")).toBeInTheDocument();

      expect(screen.getByText("Recorded 0.0 / 20.0 kg")).toBeInTheDocument();
      expect(screen.queryByText(/ · Done/)).toBeNull();
    });

    it("renders the mobile edit action only alongside the desktop one for managers", async () => {
      await renderLoaded();
      // One plan line → exactly two Edit triggers: the desktop cell and the
      // mobile card.
      expect(screen.getAllByRole("button", { name: "Edit" })).toHaveLength(2);
    });

    it("renders no edit action at all for a view-only user", async () => {
      server.use(permissionsHandler(["feeding.view"]));
      await renderLoaded();
      expect(screen.queryAllByRole("button", { name: "Edit" })).toHaveLength(0);
    });
  });

  describe("ration quantisation", () => {
    it("quantises a two-digit ration exactly as the server does (12.0005 → 12.001)", async () => {
      const user = userEvent.setup();
      server.use(
        http.post("/api/feeding/settings", () => new HttpResponse(null, { status: 204 })),
      );
      await renderLoaded();

      await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
      const dialog = await screen.findByRole("dialog");
      const input = within(dialog).getByLabelText(/kg per head per day/);
      await user.clear(input);
      await user.type(input, "12.0005");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(toast.success).toHaveBeenCalledWith("Saved 12.001 kg/head for Breeding."),
      );
    });
  });

  describe("empty-plan shortcut styling", () => {
    it("styles the Add animals shortcut as a small outline button", async () => {
      server.use(
        http.get("/api/feeding/plan", () => HttpResponse.json(planPayload([]))),
      );
      renderWithProviders(<FeedingPage />);
      const link = await screen.findByRole("link", { name: "Add animals" });
      expect(link).toHaveAttribute("href", "/animals/new");
      expect(link).toHaveClass("bg-background", "px-3");
    });
  });
});
