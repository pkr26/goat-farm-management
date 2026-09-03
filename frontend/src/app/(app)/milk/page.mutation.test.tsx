/**
 * Mutation-hardening tests for the milk parlour page: shift-time copy,
 * chart tick labels, stat derivations, record-form validation and payload,
 * error/loading/permission branches. The AnimalPicker is stubbed with a
 * native select so form state can be driven directly.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server, TEST_FARMS, ALL_PERMISSIONS, permissionsHandler } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import MilkPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/milk",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  }),
}));

vi.mock("@/components/animal-picker", () => ({
  AnimalPicker: ({
    id,
    value,
    onValueChange,
    placeholder,
    dialogTitle,
  }: {
    id: string;
    value: string;
    onValueChange: (value: string) => void;
    placeholder: string;
    dialogTitle: string;
  }) => (
    <select
      id={id}
      value={value}
      onChange={(event) => onValueChange(event.target.value)}
      data-testid="animal-picker"
      aria-label={placeholder}
      data-dialog-title={dialogTitle}
    >
      <option value="">Pick…</option>
      <option value="5">BUF-005</option>
      <option value="7">BUF-007</option>
    </select>
  ),
}));

import { toast } from "sonner";

const DAIRY_FARM = { ...TEST_FARMS[0], farm_type: "BUFFALO_DAIRY" };
const dairyFarmHandler = () =>
  http.get("/api/auth/farms", () => HttpResponse.json([DAIRY_FARM]));

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

const TODAY = new Date()
  .toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
const [Y, M, D] = TODAY.split("-").map(Number);
/** Expected short tick label, computed independently of shortDay(). */
function expectedShortDay(iso: string): string {
  return `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]}`;
}
const TODAY_SHORT = `${D} ${MONTHS[M - 1]}`;
const TODAY_FULL = `${D} ${MONTHS[M - 1]} ${Y}`;

function shiftIso(iso: string, days: number): string {
  const utc = Date.UTC(
    Number(iso.slice(0, 4)),
    Number(iso.slice(5, 7)) - 1,
    Number(iso.slice(8, 10)),
  ) + days * 86_400_000;
  const shifted = new Date(utc);
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`;
}
const YESTERDAY = shiftIso(TODAY, -1);

const FULL_SUMMARY = {
  days: 30,
  total_litres: 2450.5,
  avg_daily_litres: 81.7,
  avg_fat_pct: 6.9,
  // API order: newest first.
  daily: [
    { date: TODAY, litres: 12.3, recorded_animals: 8, avg_fat_pct: 6.4 },
    { date: YESTERDAY, litres: 9.9, recorded_animals: 6, avg_fat_pct: 5.9 },
  ],
  animals: [
    {
      animal_id: 3,
      animal_tag: "BUF-003",
      total_litres: 30000.4,
      avg_daily_litres: 8.2,
      days_recorded: 30,
      avg_fat_pct: 7.1,
    },
    {
      animal_id: 4,
      animal_tag: "BUF-004",
      total_litres: 150.5,
      avg_daily_litres: 5.0,
      days_recorded: 30,
      avg_fat_pct: null,
    },
  ],
};

const FULL_RECORDS = {
  records: [
    {
      id: 2,
      animal_id: 4,
      animal_tag: "BUF-004",
      date: TODAY,
      shift: "NIGHT",
      litres: 1.94,
      fat_pct: null,
    },
    {
      id: 1,
      animal_id: 3,
      animal_tag: "BUF-003",
      date: TODAY,
      shift: "MORNING",
      litres: 2.5,
      fat_pct: 6.5,
    },
  ],
  total: 2,
  limit: 50,
  offset: 0,
  total_litres: 4.44,
};

function fullHandlers() {
  return [
    dairyFarmHandler(),
    http.get("/api/milk/summary", () => HttpResponse.json(FULL_SUMMARY)),
    http.get("/api/milk", () => HttpResponse.json(FULL_RECORDS)),
  ];
}

beforeEach(() => {
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
});

describe("MilkPage mutation hardening", () => {
  it("shows the skeleton until permissions resolve, then the parlour", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS }, { delay: 400 }),
      ),
      http.get("/api/milk/summary", () => HttpResponse.json(FULL_SUMMARY)),
      http.get("/api/milk", () => HttpResponse.json(FULL_RECORDS)),
    );

    renderWithProviders(<MilkPage />);

    // While permissions are in flight the page must be the busy skeleton,
    // never a flash of denial or half-loaded data.
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(screen.queryByText("Today's herd total")).not.toBeInTheDocument();
    expect(
      screen.queryByText("You do not have permission to view milk records."),
    ).not.toBeInTheDocument();

    expect(await screen.findByText("Today's herd total")).toBeInTheDocument();
  });

  it("renders the permissions dead-end when the permissions endpoint fails", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    renderWithProviders(<MilkPage />);

    expect(
      await screen.findByText(
        "Could not load your permissions — refresh the page to try again.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry permissions" })).toBeInTheDocument();
  });

  it("denies the page for a session without milk.view", async () => {
    server.use(
      dairyFarmHandler(),
      permissionsHandler(ALL_PERMISSIONS.filter((code) => code !== "milk.view")),
    );

    renderWithProviders(<MilkPage />);

    expect(
      await screen.findByText("You do not have permission to view milk records."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Today's herd total")).not.toBeInTheDocument();
  });

  it("renders today's stats, hint copy, sparkline and trend with recorded days", async () => {
    let summaryUrl = "";
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", ({ request }) => {
        summaryUrl = request.url;
        return HttpResponse.json(FULL_SUMMARY);
      }),
      http.get("/api/milk", () => HttpResponse.json(FULL_RECORDS)),
    );

    renderWithProviders(<MilkPage />);

    // The summary window is requested for 30 days.
    expect(await screen.findByText("12.3 L")).toBeInTheDocument();
    expect(summaryUrl).toContain("days=30");

    // Today's hint carries recorded animals and the day's average fat.
    expect(screen.getByText(/8 buffalo milked/)).toBeInTheDocument();
    expect(screen.getByText(/6\.4% fat/)).toBeInTheDocument();

    // Window stats and benchmark copy.
    expect(screen.getByText("2,451 L")).toBeInTheDocument();
    expect(screen.getByText("82 L/day average")).toBeInTheDocument();
    expect(screen.getByText("6.9%")).toBeInTheDocument();
    expect(screen.getByText("Buffalo dairy benchmark 6.0–7.5%")).toBeInTheDocument();

    // Sparkline over the last 14 days exactly (not a longer slice).
    const sparkline = screen.getByRole("img", { name: "Herd litres, last 14 days" });
    const polyline = sparkline.querySelector("polyline");
    expect(polyline).not.toBeNull();
    expect(polyline!.getAttribute("points")!.trim().split(/\s+/)).toHaveLength(14);

    // Trend chart: 30-day aria label and year-stripped tick labels.
    const trend = await screen.findByRole("img", {
      name: "Herd milk trend, daily litres over the last 30 days",
    });
    expect(trend).toBeInTheDocument();
    const trendCard = trend.closest('[data-slot="card"]');
    expect(trendCard).not.toBeNull();
    expect(within(trendCard as HTMLElement).getByText(TODAY_SHORT)).toBeInTheDocument();
    // shortDay must have stripped the year — the full date never appears here.
    expect(within(trendCard as HTMLElement).queryByText(TODAY_FULL)).not.toBeInTheDocument();

    // Readings table rows: shift label, litres rounding, fat dash for null.
    expect(screen.getAllByText("BUF-003").length).toBeGreaterThan(0);
    expect(screen.getAllByText("BUF-004").length).toBeGreaterThan(0);
    expect(screen.getAllByText(TODAY_FULL).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Morning").length).toBeGreaterThan(0);
    expect(screen.getByText("Night")).toBeInTheDocument();
    expect(screen.getByText("2.5")).toBeInTheDocument();
    expect(screen.getByText("1.9")).toBeInTheDocument();
    expect(screen.getByText("6.5")).toBeInTheDocument();

    // Per-animal averages: value and dash for a null average fat.
    expect(screen.getByText("30,000")).toBeInTheDocument();
    expect(screen.getByText("7.1")).toBeInTheDocument();
    const fatCells = screen.getAllByText("—").map((node) => node.textContent);
    expect(fatCells).toContain("—");
  });

  it("renders zero-state copy with no sparkline or trend when nothing is recorded", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () =>
        HttpResponse.json({
          days: 30,
          total_litres: 0,
          avg_daily_litres: 0,
          avg_fat_pct: null,
          daily: [],
          animals: [],
        }),
      ),
      http.get("/api/milk", () =>
        HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0, total_litres: 0 }),
      ),
    );

    renderWithProviders(<MilkPage />);

    expect(await screen.findByText("0 L")).toBeInTheDocument();
    expect(screen.getByText("No readings yet today")).toBeInTheDocument();
    // No recorded days: neither a flat-zero sparkline nor a flat-zero trend.
    expect(
      screen.queryByRole("img", { name: "Herd litres, last 14 days" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Herd milk trend")).not.toBeInTheDocument();
    // Average fat falls back to the em dash.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(await screen.findByText("No readings today yet")).toBeInTheDocument();
    expect(await screen.findByText("Averages appear once readings exist")).toBeInTheDocument();
  });

  it("omits today's fat from the hint when the day average is null", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () =>
        HttpResponse.json({
          days: 30,
          total_litres: 5.0,
          avg_daily_litres: 5.0,
          avg_fat_pct: null,
          daily: [
            { date: TODAY, litres: 5.0, recorded_animals: 3, avg_fat_pct: null },
          ],
          animals: [],
        }),
      ),
      http.get("/api/milk", () =>
        HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0, total_litres: 0 }),
      ),
    );

    renderWithProviders(<MilkPage />);

    expect(await screen.findByText("5.0 L")).toBeInTheDocument();
    const hint = screen.getByText(/3 buffalo milked/);
    expect(hint.textContent).toBe("3 buffalo milked");
  });

  it("shows em-dash stats and inline loading while the summary is pending", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => new Promise(() => {})),
      http.get("/api/milk", () => HttpResponse.json(FULL_RECORDS)),
    );

    renderWithProviders(<MilkPage />);

    // Readings arrive, but every summary-derived stat stays a dash and the
    // averages region stays in its inline loading state.
    expect(await screen.findByText("BUF-003")).toBeInTheDocument();
    const dashes = await screen.findAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("Per-milking buffalo averages (30 days)")).toBeInTheDocument();
  });

  it("surfaces the listing error detail with a retry", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => HttpResponse.json(FULL_SUMMARY)),
      http.get("/api/milk", () =>
        HttpResponse.json({ detail: "Parlour offline" }, { status: 500 }),
      ),
    );

    renderWithProviders(<MilkPage />);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Parlour offline")).toBeInTheDocument();
    expect(
      within(alert).getByRole("button", { name: "Retry readings" }),
    ).toBeInTheDocument();
  });

  it("shows inline loading for readings while the listing is pending", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => HttpResponse.json(FULL_SUMMARY)),
      http.get("/api/milk", () => new Promise(() => {})),
    );

    renderWithProviders(<MilkPage />);

    expect(await screen.findByText("Today's readings")).toBeInTheDocument();
    expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
    expect(screen.queryByText("No readings today yet")).not.toBeInTheDocument();
  });

  it("surfaces the summary error detail with a retry", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () =>
        HttpResponse.json({ detail: "Milk summary forbidden" }, { status: 403 }),
      ),
      http.get("/api/milk", () => HttpResponse.json(FULL_RECORDS)),
    );

    renderWithProviders(<MilkPage />);

    const alerts = await screen.findAllByRole("alert");
    const summaryAlert = alerts.find((node) =>
      within(node).queryByText("Milk summary forbidden"),
    );
    expect(summaryAlert).toBeDefined();
    expect(
      within(summaryAlert!).getByRole("button", { name: "Retry summary" }),
    ).toBeInTheDocument();
  });

  it("maps every shift to its parlour time in the dropdown", async () => {
    server.use(...fullHandlers());
    const user = userEvent.setup();

    renderWithProviders(<MilkPage />);

    await screen.findByText("Today's herd total");
    await user.click(screen.getByLabelText("Shift"));

    const options = await screen.findAllByRole("option", { name: /·/ });
    expect(options.map((option) => option.textContent)).toEqual([
      "Morning · 5:30 AM",
      "Afternoon · 1:30 PM",
      "Night · 7:30 PM",
    ]);

    await user.click(screen.getByRole("option", { name: "Night · 7:30 PM" }));
    expect(screen.getByLabelText("Shift")).toHaveTextContent("NIGHT");
  });

  it("validates the record form before posting anything", async () => {
    let posts = 0;
    server.use(
      ...fullHandlers(),
      http.post("/api/milk/new", () => {
        posts += 1;
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<MilkPage />);

    await screen.findByText("Today's herd total");

    // No animal picked.
    await user.click(screen.getByRole("button", { name: "Record yield" }));
    expect(
      await screen.findByText("Pick the milking buffalo this reading belongs to."),
    ).toBeInTheDocument();

    // Animal picked, litres empty / zero / over the cap.
    await user.selectOptions(screen.getByTestId("animal-picker"), "5");
    await user.click(screen.getByRole("button", { name: "Record yield" }));
    expect(
      await screen.findByText(
        "Litres must be greater than 0 and up to 100 for one milking.",
      ),
    ).toBeInTheDocument();

    const litres = screen.getByLabelText("Litres");
    await user.type(litres, "0");
    await user.click(screen.getByRole("button", { name: "Record yield" }));
    expect(
      screen.getByText("Litres must be greater than 0 and up to 100 for one milking."),
    ).toBeInTheDocument();

    await user.clear(litres);
    await user.type(litres, "101");
    await user.click(screen.getByRole("button", { name: "Record yield" }));
    expect(
      screen.getByText("Litres must be greater than 0 and up to 100 for one milking."),
    ).toBeInTheDocument();

    // Fat outside 3–12.
    await user.clear(litres);
    await user.type(litres, "2.5");
    await user.type(screen.getByLabelText("Fat % (optional)"), "2.9");
    await user.click(screen.getByRole("button", { name: "Record yield" }));
    expect(await screen.findByText("Fat % runs 3–12 for buffalo milk.")).toBeInTheDocument();

    expect(posts).toBe(0);
  });

  it("posts the payload with fat and clears the form on success", async () => {
    const bodies: unknown[] = [];
    server.use(
      ...fullHandlers(),
      http.post("/api/milk/new", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<MilkPage />);

    await screen.findByText("Today's herd total");
    await user.selectOptions(screen.getByTestId("animal-picker"), "5");
    await user.type(screen.getByLabelText("Litres"), "2.5");
    await user.type(screen.getByLabelText("Fat % (optional)"), "4.2");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({
      animal_id: 5,
      date: TODAY,
      shift: "MORNING",
      litres: 2.5,
      fat_pct: 4.2,
    });
    expect(toast.success).toHaveBeenCalledWith("Recorded 2.5 L (morning)");
    // Litres and fat clear after a successful record.
    await waitFor(() => expect((screen.getByLabelText("Litres") as HTMLInputElement).value).toBe(""));
    expect((screen.getByLabelText("Fat % (optional)") as HTMLInputElement).value).toBe("");

    // Second submission without fat carries null forward.
    await user.selectOptions(screen.getByTestId("animal-picker"), "7");
    await user.type(screen.getByLabelText("Litres"), "1.5");
    await user.click(screen.getByRole("button", { name: "Record yield" }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toEqual({
      animal_id: 7,
      date: TODAY,
      shift: "MORNING",
      litres: 1.5,
      fat_pct: null,
    });
  });

  it("toasts the API detail when recording fails", async () => {
    server.use(
      ...fullHandlers(),
      http.post("/api/milk/new", () =>
        HttpResponse.json({ detail: "Shift already recorded" }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();

    renderWithProviders(<MilkPage />);

    await screen.findByText("Today's herd total");
    await user.selectOptions(screen.getByTestId("animal-picker"), "5");
    await user.type(screen.getByLabelText("Litres"), "2.5");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Shift already recorded"),
    );
    // The form keeps its values on failure so nothing is silently lost.
    expect(screen.getByLabelText("Litres")).toHaveValue(2.5);
  });

  it("disables the record button and shows Saving… while posting", async () => {
    server.use(
      ...fullHandlers(),
      http.post("/api/milk/new", async () => {
        await delay(150);
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<MilkPage />);

    await screen.findByText("Today's herd total");
    await user.selectOptions(screen.getByTestId("animal-picker"), "5");
    await user.type(screen.getByLabelText("Litres"), "2.5");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    const saving = screen.getByRole("button", { name: /Saving…/ });
    expect(saving).toBeDisabled();

    await screen.findByRole("button", { name: "Record yield" });
  });

  it("disables Refresh while either query is refetching", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", async () => {
        await delay(150);
        return HttpResponse.json(FULL_SUMMARY);
      }),
      http.get("/api/milk", async () => {
        await delay(150);
        return HttpResponse.json(FULL_RECORDS);
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<MilkPage />);

    await screen.findByText("12.3 L");
    const refresh = screen.getByRole("button", { name: "Refresh" });
    await waitFor(() => expect(refresh).toBeEnabled());

    await user.click(refresh);
    await waitFor(() => expect(refresh).toBeDisabled());
    await waitFor(() => expect(refresh).toBeEnabled());
  });

  it("sorts the readings table by litres through asc, desc and back to neutral", async () => {
    server.use(...fullHandlers());
    const user = userEvent.setup();

    renderWithProviders(<MilkPage />);

    await screen.findByText("12.3 L");
    const litresHeader = () => screen.getByRole("button", { name: "Litres" });
    const litresColumn = () => screen.getByRole("columnheader", { name: "Litres" });

    // API order: newest first → BUF-004 (1.9) above BUF-003 (2.5).
    expect(screen.getAllByRole("row")[1].textContent).toContain("BUF-004");
    expect(litresColumn()).toHaveAttribute("aria-sort", "none");

    await user.click(litresHeader()); // asc
    await waitFor(() =>
      expect(screen.getAllByRole("row")[1].textContent).toContain("BUF-004"),
    );
    expect(screen.getAllByRole("row")[2].textContent).toContain("BUF-003");
    expect(litresColumn()).toHaveAttribute("aria-sort", "ascending");

    await user.click(litresHeader()); // desc
    await waitFor(() =>
      expect(screen.getAllByRole("row")[1].textContent).toContain("BUF-003"),
    );
    expect(screen.getAllByRole("row")[2].textContent).toContain("BUF-004");
    expect(litresColumn()).toHaveAttribute("aria-sort", "descending");

    await user.click(litresHeader()); // back to API order
    await waitFor(() =>
      expect(screen.getAllByRole("row")[1].textContent).toContain("BUF-004"),
    );
    expect(litresColumn()).toHaveAttribute("aria-sort", "none");
  });

  // ---------- round 2: remaining mutation survivors ----------

  it("titles and placeholders the animal picker for the dairy vocabulary", async () => {
    server.use(...fullHandlers());
    renderWithProviders(<MilkPage />);
    await screen.findByText("Today's herd total");

    const picker = screen.getByTestId("animal-picker");
    expect(picker).toHaveAttribute("aria-label", "Pick a milking buffalo…");
    expect(picker).toHaveAttribute("data-dialog-title", "Pick the milking buffalo milked");
  });

  it("starts the record form with empty litres and fat", async () => {
    server.use(...fullHandlers());
    renderWithProviders(<MilkPage />);
    await screen.findByText("Today's herd total");

    expect((screen.getByLabelText("Litres") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Fat % (optional)") as HTMLInputElement).value).toBe("");
  });

  it("describes the page in the skeleton state and after load", async () => {
    const description =
      "Milking buffalo yields by milking shift, herd daily totals and 30-day averages.";
    server.use(
      dairyFarmHandler(),
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS }, { delay: 400 }),
      ),
      http.get("/api/milk/summary", () => HttpResponse.json(FULL_SUMMARY)),
      http.get("/api/milk", () => HttpResponse.json(FULL_RECORDS)),
    );
    renderWithProviders(<MilkPage />);

    // The skeleton branch carries the same description (the dairy vocabulary
    // lands with the farms fetch, while permissions are still pending)…
    expect(await screen.findByText(description)).toBeInTheDocument();
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();
    // …and never flashes a denial while the permission set is still loading.
    expect(
      screen.queryByText("You do not have permission to view milk records."),
    ).not.toBeInTheDocument();

    await screen.findByText("Today's herd total");
    expect(screen.getByText(description)).toBeInTheDocument();
    expect(screen.getByText("30-day total")).toBeInTheDocument();
  });

  it("requests the listing for 50 readings from today", async () => {
    let listingUrl = "";
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => HttpResponse.json(FULL_SUMMARY)),
      http.get("/api/milk", ({ request }) => {
        listingUrl = request.url;
        return HttpResponse.json(FULL_RECORDS);
      }),
    );
    renderWithProviders(<MilkPage />);
    await screen.findByText("Today's herd total");

    await waitFor(() => expect(listingUrl).not.toBe(""));
    expect(listingUrl).toContain("limit=50");
    expect(listingUrl).toContain(`date_from=${TODAY}`);
  });

  it("counts only today's entry towards the today stat", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () =>
        HttpResponse.json({
          days: 30,
          total_litres: 9.9,
          avg_daily_litres: 9.9,
          avg_fat_pct: null,
          daily: [
            { date: YESTERDAY, litres: 9.9, recorded_animals: 6, avg_fat_pct: null },
          ],
          animals: [],
        }),
      ),
      http.get("/api/milk", () =>
        HttpResponse.json({ records: [], total: 0, limit: 50, offset: 0, total_litres: 0 }),
      ),
    );
    renderWithProviders(<MilkPage />);

    // Wait for the summary to land (30-day total formats 9.9 L → "10 L")
    // before judging the today stat, so the assertion cannot race a pending
    // summary that would vacuously show "0 L".
    expect(await screen.findByText("10 L")).toBeInTheDocument();
    expect(screen.getByText("0 L")).toBeInTheDocument();
    expect(screen.getByText("No readings yet today")).toBeInTheDocument();
    expect(screen.queryByText("9.9 L")).not.toBeInTheDocument();
  });

  it("clears a validation error once the reading is recorded", async () => {
    server.use(
      ...fullHandlers(),
      http.post("/api/milk/new", () => HttpResponse.json({ ok: true }, { status: 200 })),
    );
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);
    await screen.findByText("Today's herd total");

    await user.click(screen.getByRole("button", { name: "Record yield" }));
    expect(
      await screen.findByText("Pick the milking buffalo this reading belongs to."),
    ).toBeInTheDocument();

    await user.selectOptions(screen.getByTestId("animal-picker"), "5");
    await user.type(screen.getByLabelText("Litres"), "2.5");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    await waitFor(() =>
      expect(
        screen.queryByText("Pick the milking buffalo this reading belongs to."),
      ).not.toBeInTheDocument(),
    );
  });

  it("accepts the boundary values: 100 L, 3% fat and 12% fat", async () => {
    const bodies: unknown[] = [];
    server.use(
      ...fullHandlers(),
      http.post("/api/milk/new", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);
    await screen.findByText("Today's herd total");

    await user.selectOptions(screen.getByTestId("animal-picker"), "5");
    await user.type(screen.getByLabelText("Litres"), "100");
    await user.type(screen.getByLabelText("Fat % (optional)"), "3");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ litres: 100, fat_pct: 3 });

    await user.selectOptions(screen.getByTestId("animal-picker"), "7");
    await user.type(screen.getByLabelText("Litres"), "2.5");
    await user.type(screen.getByLabelText("Fat % (optional)"), "12");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toMatchObject({ litres: 2.5, fat_pct: 12 });
  });

  it("treats a whitespace-only fat entry as no fat test", async () => {
    const bodies: unknown[] = [];
    server.use(
      ...fullHandlers(),
      http.post("/api/milk/new", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);
    await screen.findByText("Today's herd total");

    await user.selectOptions(screen.getByTestId("animal-picker"), "5");
    await user.type(screen.getByLabelText("Litres"), "2.5");
    await user.type(screen.getByLabelText("Fat % (optional)"), " ");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ fat_pct: null });
  });

  it("rejects a fat test above 12 without posting", async () => {
    let posts = 0;
    server.use(
      ...fullHandlers(),
      http.post("/api/milk/new", () => {
        posts += 1;
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);
    await screen.findByText("Today's herd total");

    await user.selectOptions(screen.getByTestId("animal-picker"), "5");
    await user.type(screen.getByLabelText("Litres"), "2.5");
    await user.type(screen.getByLabelText("Fat % (optional)"), "12.5");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    expect(await screen.findByText("Fat % runs 3–12 for buffalo milk.")).toBeInTheDocument();
    expect(posts).toBe(0);
  });

  it("refetches the summary and listing after a recording", async () => {
    let summaryCalls = 0;
    let listingCalls = 0;
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => {
        summaryCalls += 1;
        return HttpResponse.json(FULL_SUMMARY);
      }),
      http.get("/api/milk", () => {
        listingCalls += 1;
        return HttpResponse.json(FULL_RECORDS);
      }),
      http.post("/api/milk/new", () => HttpResponse.json({ ok: true }, { status: 200 })),
    );
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);
    await screen.findByText("Today's herd total");
    await screen.findByText("12.3 L");
    const summaryBefore = summaryCalls;
    const listingBefore = listingCalls;

    await user.selectOptions(screen.getByTestId("animal-picker"), "5");
    await user.type(screen.getByLabelText("Litres"), "2.5");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    await waitFor(() => expect(summaryCalls).toBeGreaterThan(summaryBefore));
    await waitFor(() => expect(listingCalls).toBeGreaterThan(listingBefore));
  });

  it("falls back to the generic toast when the recording never reaches the server", async () => {
    server.use(
      ...fullHandlers(),
      http.post("/api/milk/new", () => HttpResponse.error()),
    );
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);
    await screen.findByText("Today's herd total");

    await user.selectOptions(screen.getByTestId("animal-picker"), "5");
    await user.type(screen.getByLabelText("Litres"), "2.5");
    await user.click(screen.getByRole("button", { name: "Record yield" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Could not record the yield."),
    );
  });

  it("keeps Refresh disabled while only the listing is still refetching", async () => {
    server.use(...fullHandlers());
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);
    await screen.findByText("12.3 L");
    const refresh = screen.getByRole("button", { name: "Refresh" });
    await waitFor(() => expect(refresh).toBeEnabled());

    // From here on the summary resolves instantly but the listing hangs.
    server.use(http.get("/api/milk", () => new Promise(() => {})));
    await user.click(refresh);

    await waitFor(() => expect(refresh).toBeDisabled());
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(refresh).toBeDisabled();
  });

  it("sorts the readings by date through asc and desc with the aria state", async () => {
    // Two records on different days whose litres order disagrees with the
    // date order, so a comparator that falls through to litres is exposed.
    const records = {
      records: [
        { id: 2, animal_id: 4, animal_tag: "BUF-004", date: TODAY, shift: "NIGHT", litres: 2.5, fat_pct: null },
        { id: 1, animal_id: 3, animal_tag: "BUF-003", date: YESTERDAY, shift: "MORNING", litres: 5, fat_pct: null },
      ],
      total: 2,
      limit: 50,
      offset: 0,
      total_litres: 7.5,
    };
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => HttpResponse.json(FULL_SUMMARY)),
      http.get("/api/milk", () => HttpResponse.json(records)),
    );
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);
    await screen.findAllByText("BUF-004");

    const dateHeader = () => screen.getByRole("button", { name: "Date" });
    const dateColumn = () => screen.getByRole("columnheader", { name: "Date" });
    const rows = () => screen.getAllByRole("row");

    // API order: today (2.5 L) above yesterday (5 L).
    expect(rows()[1].textContent).toContain("BUF-004");
    expect(dateColumn()).toHaveAttribute("aria-sort", "none");

    await user.click(dateHeader()); // date asc → yesterday first
    await waitFor(() => expect(rows()[1].textContent).toContain("BUF-003"));
    expect(rows()[2].textContent).toContain("BUF-004");
    expect(dateColumn()).toHaveAttribute("aria-sort", "ascending");

    await user.click(dateHeader()); // date desc → today first
    await waitFor(() => expect(rows()[1].textContent).toContain("BUF-004"));
    expect(rows()[2].textContent).toContain("BUF-003");
    expect(dateColumn()).toHaveAttribute("aria-sort", "descending");

    await user.click(dateHeader()); // neutral → API order
    await waitFor(() => expect(rows()[1].textContent).toContain("BUF-004"));
    expect(dateColumn()).toHaveAttribute("aria-sort", "none");
  });

  it("renders the em dash per stat and table cell that lacks a value", async () => {
    server.use(...fullHandlers());
    renderWithProviders(<MilkPage />);
    await screen.findAllByText("BUF-004");

    // Readings row: BUF-004 has no tested fat.
    const readingsRow = screen.getAllByText("BUF-004")[0].closest("tr") as HTMLElement;
    expect(within(readingsRow).getByText("—")).toBeInTheDocument();
    // Averages row: BUF-004 has no average fat.
    const averagesRow = screen.getAllByText("BUF-004")[1].closest("tr") as HTMLElement;
    expect(within(averagesRow).getByText("—")).toBeInTheDocument();
  });

  it("dashes the window stats while the summary never lands", async () => {
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => new Promise(() => {})),
      http.get("/api/milk", () => HttpResponse.json(FULL_RECORDS)),
    );
    renderWithProviders(<MilkPage />);
    await screen.findByText("BUF-003");

    const windowCard = screen.getByText("30-day total").closest('[data-slot="card"]');
    expect(windowCard).not.toBeNull();
    expect(within(windowCard as HTMLElement).getByText("—")).toBeInTheDocument();
    const fatCard = screen.getByText("Average fat").closest('[data-slot="card"]');
    expect(fatCard).not.toBeNull();
    expect(within(fatCard as HTMLElement).getByText("—")).toBeInTheDocument();
  });

  it("points the disabled fat field at its helper note", async () => {
    server.use(
      dairyFarmHandler(),
      permissionsHandler(ALL_PERMISSIONS.filter((code) => code !== "milk.quality")),
      http.get("/api/milk/summary", () => HttpResponse.json(FULL_SUMMARY)),
      http.get("/api/milk", () => HttpResponse.json(FULL_RECORDS)),
    );
    renderWithProviders(<MilkPage />);
    await screen.findByText("Today's herd total");

    const fat = screen.getByLabelText("Fat % (quality role only)");
    expect(fat).toBeDisabled();
    expect(fat).toHaveAttribute("aria-describedby", "milk-fat-help");
    expect(document.getElementById("milk-fat-help")).not.toBeNull();
    expect(
      screen.getByText(/Only the milk quality \/ manager roles record fat tests/),
    ).toBeInTheDocument();
  });

  it("retries the readings listing after a transport failure", async () => {
    let calls = 0;
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => HttpResponse.json(FULL_SUMMARY)),
      http.get("/api/milk", () => {
        calls += 1;
        return calls === 1 ? HttpResponse.error() : HttpResponse.json(FULL_RECORDS);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Could not load records.")).toBeInTheDocument();
    await user.click(within(alert).getByRole("button", { name: "Retry readings" }));

    expect(await screen.findAllByText("BUF-003")).not.toHaveLength(0);
    expect(calls).toBe(2);
  });

  it("retries the summary after a transport failure", async () => {
    let calls = 0;
    server.use(
      dairyFarmHandler(),
      http.get("/api/milk/summary", () => {
        calls += 1;
        return calls === 1 ? HttpResponse.error() : HttpResponse.json(FULL_SUMMARY);
      }),
      http.get("/api/milk", () => HttpResponse.json(FULL_RECORDS)),
    );
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Could not load summary.")).toBeInTheDocument();
    await user.click(within(alert).getByRole("button", { name: "Retry summary" }));

    expect(await screen.findByText("12.3 L")).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  it("recovers the page when the permissions retry succeeds", async () => {
    let calls = 0;
    server.use(
      dairyFarmHandler(),
      http.get("/api/auth/permissions", () => {
        calls += 1;
        return calls === 1
          ? new HttpResponse(null, { status: 500 })
          : HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
      http.get("/api/milk/summary", () => HttpResponse.json(FULL_SUMMARY)),
      http.get("/api/milk", () => HttpResponse.json(FULL_RECORDS)),
    );
    const user = userEvent.setup();
    renderWithProviders(<MilkPage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry permissions" }));

    expect(await screen.findByText("Today's herd total")).toBeInTheDocument();
    expect(calls).toBeGreaterThanOrEqual(2);
  });
});
