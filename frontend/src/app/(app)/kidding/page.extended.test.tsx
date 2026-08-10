/**
 * Kidding page: overdue card (days-late math), upcoming list (days left,
 * kids detected, record buttons), recent-kiddings history (KidsCell links,
 * statuses, dashes), RBAC gating (kidding.view / kidding.manage), and the
 * Record-kidding dialog — dynamic kid rows (add/remove up to 10 / down to
 * 1), per-row zod validation (tag length, negative weight), payload mapping
 * (blank → null, trimmed notes), and server-error handling.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BreedingRecordOut, KiddingRecordOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import KiddingPage from "./page";

const { navState } = vi.hoisted(() => ({ navState: { search: "" } }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/kidding",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

type User = ReturnType<typeof userEvent.setup>;

beforeAll(() => {
  // jsdom lacks APIs Radix Select/Popper touch when opening the listbox.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

/** UTC-relative fixture dates: the page compares against utcToday()
 *, so browser-local fixtures drift a day near midnight. */
const TODAY = farmToday();

/** Browser-local today — matches the forms' write-side date defaults (the
 * backend accepts one day of headroom, so writes stay local). */
function localTodayISO(): string {
  return farmToday();
}
function daysFromToday(delta: number): string {
  return addDays(TODAY, delta);
}

function makeBreeding(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    breeding_date: "2026-03-01",
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: "2026-04-02",
    ultrasound_result_date: null,
    ultrasound_done: true,
    pregnant: true,
    kid_count_detected: 2,
    expected_kidding_date: daysFromToday(10),
    outcome: "CONFIRMED_PREGNANT",
    loss_date: null,
    loss_cause: null,
    loss_notes: null,
    loss_recorded_by_id: null,
    loss_recorded_at: null,
    has_kidding: false,
    doe_tag: "G-010",
    buck_tag: "G-020",
    ...overrides,
  };
}

function makeKidding(overrides: Partial<KiddingRecordOut>): KiddingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    date: "2026-07-20",
    breeding_record_id: 1,
    ease: "NORMAL",
    notes: null,
    kids: [],
    doe_tag: "G-010",
    ...overrides,
  };
}

const OVERDUE_REC = makeBreeding({ id: 11, expected_kidding_date: daysFromToday(-5) });
const UPCOMING_REC = makeBreeding({ id: 12, expected_kidding_date: daysFromToday(10) });
const HISTORY = makeKidding({
  id: 21,
  date: "2026-07-20",
  ease: "ASSISTED",
  notes: "big twins",
  kids: [
    {
      id: 1,
      tag: "G-101",
      sex: "F",
      birth_weight: 2.4,
      status: "ALIVE",
      mortality_reported_at: null,
      animal_id: 55,
    },
    {
      id: 2,
      tag: null,
      sex: "M",
      birth_weight: null,
      status: "STILLBORN",
      mortality_reported_at: null,
      animal_id: null,
    },
  ],
});

function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  return user.click(trigger).then(async () => {
    await user.click(await screen.findByRole("option", { name }));
  });
}

describe("KiddingPage", () => {
  let listCalls: number;
  let postBody: Record<string, unknown> | null;
  let payload: {
    records: KiddingRecordOut[];
    upcoming: BreedingRecordOut[];
    upcoming_total: number;
    upcoming_limit: number;
    upcoming_offset: number;
    overdue: BreedingRecordOut[];
    overdue_total: number;
    overdue_limit: number;
    overdue_offset: number;
    total: number;
    limit: number;
    offset: number;
  };

  beforeEach(() => {
    listCalls = 0;
    postBody = null;
    payload = {
      records: [HISTORY],
      upcoming: [UPCOMING_REC],
      upcoming_total: 1,
      upcoming_limit: 25,
      upcoming_offset: 0,
      overdue: [OVERDUE_REC],
      overdue_total: 1,
      overdue_limit: 25,
      overdue_offset: 0,
      total: 1,
      limit: 50,
      offset: 0,
    };
    server.use(
      http.get("/api/kidding", () => {
        listCalls += 1;
        return HttpResponse.json(payload);
      }),
      http.get("/api/breeding/:recordId", ({ params }) => {
        const record = [...payload.overdue, ...payload.upcoming].find(
          (candidate) => String(candidate.id) === String(params.recordId),
        );
        return record
          ? HttpResponse.json(record)
          : HttpResponse.json({ detail: "Breeding record not found" }, { status: 404 });
      }),
      http.get("/api/kidding/pregnancies/:recordId", ({ params }) => {
        const record = [...payload.overdue, ...payload.upcoming].find(
          (candidate) => String(candidate.id) === String(params.recordId),
        );
        return record
          ? HttpResponse.json(record)
          : HttpResponse.json({ detail: "Pregnancy not found" }, { status: 404 });
      }),
      http.post("/api/kidding", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeKidding({ id: 99 }), { status: 201 });
      }),
    );
  });

  async function renderLoaded() {
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Recent kiddings");
    await screen.findByText("big twins");
  }

  // ---------- list rendering ----------

  it("paginates upcoming, overdue and history queues independently", async () => {
    const requests: URLSearchParams[] = [];
    server.use(
      http.get("/api/kidding", ({ request }) => {
        const query = new URL(request.url).searchParams;
        requests.push(new URLSearchParams(query));
        return HttpResponse.json({
          ...payload,
          upcoming_total: 60,
          upcoming_limit: 25,
          upcoming_offset: Number(query.get("upcoming_offset") ?? 0),
          overdue_total: 60,
          overdue_limit: 25,
          overdue_offset: Number(query.get("overdue_offset") ?? 0),
          total: 120,
          limit: 50,
          offset: Number(query.get("offset") ?? 0),
        });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    const upcomingCard = screen
      .getByText("Upcoming (next 30 days)")
      .closest('[data-slot="card"]') as HTMLElement;
    await user.click(within(upcomingCard).getByRole("button", { name: "Next" }));
    await waitFor(() => {
      const latest = requests.at(-1);
      expect(latest?.get("upcoming_offset")).toBe("25");
      expect(latest?.get("overdue_offset")).toBe("0");
      expect(latest?.get("offset")).toBe("0");
    });

    const overdueCard = screen
      .getByText("Overdue (past expected date, no kidding recorded)")
      .closest('[data-slot="card"]') as HTMLElement;
    await user.click(within(overdueCard).getByRole("button", { name: "Next" }));
    await waitFor(() => {
      const latest = requests.at(-1);
      expect(latest?.get("upcoming_offset")).toBe("25");
      expect(latest?.get("overdue_offset")).toBe("25");
      expect(latest?.get("offset")).toBe("0");
    });

    const historyCard = screen
      .getByText("Recent kiddings")
      .closest('[data-slot="card"]') as HTMLElement;
    await user.click(within(historyCard).getByRole("button", { name: "Next" }));
    await waitFor(() => {
      const latest = requests.at(-1);
      expect(latest?.get("upcoming_offset")).toBe("25");
      expect(latest?.get("overdue_offset")).toBe("25");
      expect(latest?.get("offset")).toBe("50");
    });
  });

  it("re-homes only a due queue whose current page disappears", async () => {
    const upcomingOffsets: number[] = [];
    let shrunk = false;
    server.use(
      http.get("/api/kidding", ({ request }) => {
        const query = new URL(request.url).searchParams;
        const requestedOffset = Number(query.get("upcoming_offset") ?? 0);
        upcomingOffsets.push(requestedOffset);
        if (requestedOffset === 25) shrunk = true;
        return HttpResponse.json({
          ...payload,
          upcoming_total: shrunk ? 1 : 60,
          upcoming_limit: 25,
          upcoming_offset: requestedOffset,
          overdue_offset: Number(query.get("overdue_offset") ?? 0),
          offset: Number(query.get("offset") ?? 0),
        });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const upcomingCard = screen
      .getByText("Upcoming (next 30 days)")
      .closest('[data-slot="card"]') as HTMLElement;

    await user.click(within(upcomingCard).getByRole("button", { name: "Next" }));

    await waitFor(() => expect(upcomingOffsets).toEqual([0, 25, 0]));
  });

  it("renders the overdue card with days late and a record button", async () => {
    await renderLoaded();
    expect(
      screen.getByText("Overdue (past expected date, no kidding recorded)"),
    ).toBeInTheDocument();
    expect(screen.getByText("(5d late)")).toBeInTheDocument();
    const card = screen.getByText(/Overdue/).closest("[data-slot='card']") as HTMLElement;
    const link = within(card).getByRole("link", { name: "G-010" });
    expect(link).toHaveAttribute("href", "/animals/10");
    expect(
      within(card).getByRole("button", { name: "Record kidding" }),
    ).toBeInTheDocument();
  });

  it("hides the overdue card when nothing is overdue", async () => {
    payload.overdue = [];
    payload.overdue_total = 0;
    renderWithProviders(<KiddingPage />);
    await screen.findByText("big twins");
    expect(screen.queryByText(/Overdue/)).not.toBeInTheDocument();
  });

  it("renders upcoming pregnancies with days left and kids detected", async () => {
    await renderLoaded();
    expect(screen.getByText("Upcoming (next 30 days)")).toBeInTheDocument();
    const section = screen
      .getByText("Upcoming (next 30 days)")
      .closest("[data-slot='card']") as HTMLElement;
    expect(within(section).getByText("10")).toBeInTheDocument(); // days left
    expect(within(section).getByText("2")).toBeInTheDocument(); // kids detected
    expect(
      within(section).getByRole("button", { name: "Record kidding" }),
    ).toBeInTheDocument();
  });

  it("shows a dash for days left when the expected date is missing", async () => {
    payload.upcoming = [makeBreeding({ id: 13, expected_kidding_date: null, kid_count_detected: null })];
    payload.overdue = [];
    payload.overdue_total = 0;
    renderWithProviders(<KiddingPage />);
    const section = (await screen.findByText("Upcoming (next 30 days)")).closest(
      "[data-slot='card']",
    ) as HTMLElement;
    expect(within(section).getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("shows the upcoming empty state when nothing is due", async () => {
    payload.upcoming = [];
    payload.upcoming_total = 0;
    payload.overdue = [];
    payload.overdue_total = 0;
    renderWithProviders(<KiddingPage />);
    expect(
      await screen.findByText("No confirmed pregnancies due in the next 30 days."),
    ).toBeInTheDocument();
  });

  it("renders recent kiddings with ease badge, linked kids and plain-text stillborn", async () => {
    await renderLoaded();
    const row = screen.getByText("big twins").closest("tr")!;
    expect(within(row).getByText("20 Jul 2026")).toBeInTheDocument();
    expect(within(row).getByText("ASSISTED")).toBeInTheDocument();
    const kidLink = within(row).getByRole("link", { name: "G-101" });
    expect(kidLink).toHaveAttribute("href", "/animals/55");
    expect(within(row).getByText(/\(F, alive\)/)).toBeInTheDocument();
    // Kid without an animal record: plain text fallback tag.
    expect(within(row).getByText(/kid\s*\(M, stillborn\)/)).toBeInTheDocument();
  });

  it("renders a dash in the kids cell when a kidding has no kids", async () => {
    payload.records = [makeKidding({ id: 22, kids: [] })];
    payload.overdue = [];
    payload.overdue_total = 0;
    renderWithProviders(<KiddingPage />);
    const section = (await screen.findByText("Recent kiddings")).closest(
      "[data-slot='card']",
    ) as HTMLElement;
    const cell = within(section).getByText("—");
    expect(cell).toBeInTheDocument();
  });

  it("shows the history empty state when no kiddings exist", async () => {
    payload.records = [];
    payload.overdue = [];
    payload.overdue_total = 0;
    renderWithProviders(<KiddingPage />);
    expect(await screen.findByText("No kiddings recorded yet.")).toBeInTheDocument();
  });

  it("shows the server error detail when the list fails", async () => {
    server.use(
      http.get("/api/kidding", () =>
        HttpResponse.json({ detail: "kidding blew up" }, { status: 500 }),
      ),
    );
    renderWithProviders(<KiddingPage />);
    expect(await screen.findByText("kidding blew up")).toBeInTheDocument();
  });

  it("announces a list failure and retries it in place", async () => {
    let fail = true;
    let calls = 0;
    server.use(
      http.get("/api/kidding", () => {
        calls += 1;
        return fail
          ? HttpResponse.json({ detail: "kidding temporarily unavailable" }, { status: 503 })
          : HttpResponse.json(payload);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<KiddingPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "kidding temporarily unavailable",
    );
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry kidding data" }));

    expect(await screen.findByText("Recent kiddings")).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  // ---------- RBAC ----------

  it("blocks the page without kidding.view", async () => {
    server.use(permissionsHandler(["kidding.manage"]));
    renderWithProviders(<KiddingPage />);
    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
  });

  it("hides Record-kidding buttons without kidding.manage", async () => {
    server.use(permissionsHandler(["kidding.view"]));
    await renderLoaded();
    expect(
      screen.queryByRole("button", { name: "Record kidding" }),
    ).not.toBeInTheDocument();
  });

  // ---------- Record-kidding dialog ----------

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    const section = screen
      .getByText("Upcoming (next 30 days)")
      .closest("[data-slot='card']") as HTMLElement;
    await user.click(within(section).getByRole("button", { name: "Record kidding" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("opens with two kid rows (twins default) and the due-date description", async () => {
    const { dialog } = await openDialog();
    expect(
      within(dialog).getByText(/Doe G-010 · due .+ \(2 detected\)/),
    ).toBeInTheDocument();
    expect(within(dialog).getAllByPlaceholderText("auto")).toHaveLength(2);
    expect(within(dialog).getByLabelText(/kidding date/i)).toHaveValue(localTodayISO());
    expect(within(dialog).getByLabelText(/kidding date/i)).toHaveAttribute(
      "max",
      localTodayISO(),
    );
  });

  it("adds kid rows up to 10, then disables the add button", async () => {
    const { user, dialog } = await openDialog();
    const add = within(dialog).getByRole("button", { name: "Add kid" });
    for (let i = 0; i < 7; i += 1) await user.click(add);
    expect(within(dialog).getAllByPlaceholderText("auto")).toHaveLength(9);
    await user.click(add);
    expect(within(dialog).getAllByPlaceholderText("auto")).toHaveLength(10);
    expect(add).toBeDisabled();
  });

  it("removes kid rows but never below one", async () => {
    const { user, dialog } = await openDialog();
    const removeButtons = () => within(dialog).getAllByRole("button", { name: /Remove kid/ });
    await user.click(removeButtons()[1]);
    expect(within(dialog).getAllByPlaceholderText("auto")).toHaveLength(1);
    expect(removeButtons()[0]).toBeDisabled();
  });

  it("unregisters a removed kid so stale values are not submitted", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText("Kid 2 tag (auto if blank)"), "STALE-2");
    await user.type(within(dialog).getByLabelText("Kid 2 weight (kg)"), "9.9");
    await user.click(within(dialog).getByRole("button", { name: "Remove kid 2" }));
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.kids).toEqual([
      { tag: null, sex: "F", birth_weight: null, status: "ALIVE", mortality_reported_at: null },
    ]);
  });

  it("rejects an emptied kidding date before posting", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/kidding date/i), {
      target: { value: "" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    expect(await within(dialog).findByText("Pick a valid date")).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("rejects a future kidding date (typed input bypasses the max attribute)", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/kidding date/i), {
      target: { value: daysFromToday(1) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    expect(
      await within(dialog).findByText("Date can't be in the future"),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("rejects a kid tag longer than 50 characters", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getAllByPlaceholderText("auto")[0], {
      target: { value: "X".repeat(51) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    expect(await within(dialog).findByText("Max 50 characters")).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("rejects a negative birth weight", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getAllByRole("spinbutton")[0], {
      target: { value: "-1" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    const error = await within(dialog).findByRole("alert", { name: "" });
    expect(error).toHaveTextContent("Must be ≥ 0");
    expect(within(dialog).getByLabelText("Kid 1 weight (kg)")).toHaveAccessibleDescription(
      "Must be ≥ 0",
    );
    expect(postBody).toBeNull();
  });

  // REGRESSION — birth_weight had no client-side cap although the backend's
  // NonNegativeWeightKgFloat rejects anything above 1000 kg, so a grams-as-kg
  // typo (5000) was only caught by an opaque server 422 that bounced the whole
  // kidding; the purchases weight field already flagged the same value inline.
  it("rejects a birth weight above the backend's 1000 kg cap", async () => {
    const { user, dialog } = await openDialog();
    expect(within(dialog).getByLabelText("Kid 1 weight (kg)")).toHaveAttribute("max", "1000");
    fireEvent.change(within(dialog).getAllByRole("spinbutton")[0], {
      target: { value: "5000" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    expect(await within(dialog).findByText("At most 1000 kg")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Kid 1 weight (kg)")).toHaveAccessibleDescription(
      "At most 1000 kg",
    );
    expect(postBody).toBeNull();
  });

  // REGRESSION — notes had no client-side length bound although the backend
  // caps free text at MAX_FREE_TEXT_LENGTH (4000), so a long pasted clinical
  // note failed the whole submission with a server 422 instead of the inline
  // field error the pregnancy-loss dialog's notes already produce.
  it("rejects notes longer than 4000 characters", async () => {
    const { user, dialog } = await openDialog();
    expect(within(dialog).getByLabelText(/notes/i)).toHaveAttribute("maxlength", "4000");
    // fireEvent bypasses the maxLength attribute the way a paste-driven
    // programmatic set can, proving the zod bound itself holds.
    fireEvent.change(within(dialog).getByLabelText(/notes/i), {
      target: { value: "x".repeat(4001) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    expect(
      await within(dialog).findByText("Notes cannot exceed 4000 characters"),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("posts mapped kids (blank tag → null, weight parsed, trimmed notes)", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getAllByPlaceholderText("auto")[0], {
      target: { value: "  G-201  " },
    });
    fireEvent.change(within(dialog).getAllByRole("spinbutton")[0], {
      target: { value: "2.5" },
    });
    fireEvent.change(within(dialog).getByLabelText(/notes/i), {
      target: { value: "  easy birth  " },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      breeding_record_id: 12,
      date: localTodayISO(),
      ease: "NORMAL",
      notes: "easy birth",
    });
    expect(postBody!.kids).toEqual([
      {
        tag: "G-201",
        sex: "F",
        birth_weight: 2.5,
        status: "ALIVE",
        mortality_reported_at: null,
      },
      { tag: null, sex: "F", birth_weight: null, status: "ALIVE", mortality_reported_at: null },
    ]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("maps blank notes to null", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.notes).toBeNull();
  });

  it("sends the selected sex, status and ease values", async () => {
    const { user, dialog } = await openDialog();
    const combos = within(dialog).getAllByRole("combobox");
    // Order: ease, then per-row [sex, status].
    await pickOption(user, combos[0], "DIFFICULT");
    await pickOption(user, within(dialog).getAllByRole("combobox")[1], "Male");
    await pickOption(user, within(dialog).getAllByRole("combobox")[2], "DIED");
    // The sex trigger shows the label "Male", not the raw value "M".
    expect(within(dialog).getAllByRole("combobox")[1]).toHaveTextContent("Male");
    fireEvent.change(within(dialog).getByLabelText("Kid 1 mortality date *"), {
      target: { value: localTodayISO() },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.ease).toBe("DIFFICULT");
    // The exact shape the backend accepts: KidIn is extra="forbid" and requires
    // mortality_reported_at whenever status is DIED.
    expect((postBody!.kids as Record<string, unknown>[])[0]).toEqual({
      tag: null,
      sex: "M",
      birth_weight: null,
      status: "DIED",
      mortality_reported_at: localTodayISO(),
    });
  });

  it("records a died kid against a backend-faithful handler", async () => {
    // Mirrors KidIn._mortality_report_matches_status: dropping the date again
    // turns this into the production 422 instead of a silent green test.
    server.use(
      http.post("/api/kidding", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        const kids = postBody.kids as { status: string; mortality_reported_at?: unknown }[];
        const invalid = kids.some(
          (kid) =>
            (kid.status === "DIED") !==
            (kid.mortality_reported_at !== null && kid.mortality_reported_at !== undefined),
        );
        return invalid
          ? HttpResponse.json(
              { detail: "mortality_reported_at is required when kid status is DIED" },
              { status: 422 },
            )
          : HttpResponse.json(makeKidding({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[2], "DIED");
    fireEvent.change(within(dialog).getByLabelText("Kid 1 mortality date *"), {
      target: { value: localTodayISO() },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect((postBody!.kids as Record<string, unknown>[])[0].mortality_reported_at).toBe(
      localTodayISO(),
    );
  });

  it("requires a mortality date before posting a died kid", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[2], "DIED");
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(await within(dialog).findByText("Mortality date is required")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Kid 1 mortality date *")).toHaveAccessibleDescription(
      "Mortality date is required",
    );
    expect(postBody).toBeNull();
  });

  it("rejects a mortality date before the kidding date", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[2], "DIED");
    fireEvent.change(within(dialog).getByLabelText("Kid 1 mortality date *"), {
      target: { value: daysFromToday(-1) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(
      await within(dialog).findByText("Can't be before the kidding date"),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("drops the mortality date when a kid is switched back off DIED", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[2], "DIED");
    fireEvent.change(within(dialog).getByLabelText("Kid 1 mortality date *"), {
      target: { value: localTodayISO() },
    });
    await pickOption(user, within(dialog).getAllByRole("combobox")[2], "ALIVE");
    expect(
      within(dialog).queryByLabelText("Kid 1 mortality date *"),
    ).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect((postBody!.kids as Record<string, unknown>[])[0]).toMatchObject({
      status: "ALIVE",
      mortality_reported_at: null,
    });
  });

  it("keeps the dialog open when the server rejects the kidding", async () => {
    server.use(
      http.post("/api/kidding", () =>
        HttpResponse.json({ detail: "kidding already recorded" }, { status: 409 }),
      ),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(listCalls).toBe(1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("announces a failed kidding save and retries without losing the rows", async () => {
    let calls = 0;
    server.use(
      http.post("/api/kidding", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "kidding version conflict" }, { status: 409 })
          : HttpResponse.json(makeKidding({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText("Kid 1 tag (auto if blank)"), "G-NEW");
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "kidding version conflict",
    );
    expect(within(dialog).getByLabelText("Kid 1 tag (auto if blank)")).toHaveValue("G-NEW");
    await user.click(within(dialog).getByRole("button", { name: "Retry save kidding" }));
    await waitFor(() => expect(calls).toBe(2));
  });

  it("single-flights a double-click on Save kidding", async () => {
    let calls = 0;
    server.use(
      http.post("/api/kidding", () => {
        calls += 1;
        return HttpResponse.json(makeKidding({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();

    await user.dblClick(within(dialog).getByRole("button", { name: "Save kidding" }));
    await waitFor(() => expect(calls).toBe(1));
  });

  it("Cancel closes the dialog without posting", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(postBody).toBeNull();
  });

  // ---------- URL prefill (/kidding/new?breeding_id=… redirect) ----------

  // REGRESSION — the id used to be read from window.location.search during
  // render, which Next 16 has not updated yet when the redirect target first
  // renders. It now comes from useSearchParams(), so these tests drive the
  // router mock rather than window.location.
  describe("URL prefill from /kidding/new?breeding_id=…", () => {
    afterEach(() => {
      navState.search = "";
      window.history.replaceState({}, "", "/kidding");
    });

    it("auto-opens the record dialog for the linked breeding record", async () => {
      navState.search = "?breeding_id=12";
      renderWithProviders(<KiddingPage />);
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("Record kidding")).toBeInTheDocument();
      expect(
        within(dialog).getByText(/Doe G-010 · due .+ \(2 detected\)/),
      ).toBeInTheDocument();
    });

    it("opens no dialog for an unknown breeding_id", async () => {
      navState.search = "?breeding_id=999";
      await renderLoaded();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("does not reinterpret a scientific-notation deep-link id as another pregnancy", async () => {
      let detailCalls = 0;
      server.use(
        http.get("/api/kidding/pregnancies/100", () => {
          detailCalls += 1;
          return HttpResponse.json(makeBreeding({ id: 100 }));
        }),
      );
      navState.search = "?breeding_id=1e2";

      await renderLoaded();

      expect(detailCalls).toBe(0);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("fetches and opens a linked pregnancy outside both current due pages", async () => {
      const older = makeBreeding({
        id: 99,
        expected_kidding_date: daysFromToday(45),
        doe_tag: "G-099",
      });
      let breedingDetailCalls = 0;
      let kiddingPregnancyCalls = 0;
      server.use(
        permissionsHandler(["kidding.view", "kidding.manage"]),
        http.get("/api/breeding/99", () => {
          breedingDetailCalls += 1;
          return HttpResponse.json(older);
        }),
        http.get("/api/kidding/pregnancies/99", () => {
          kiddingPregnancyCalls += 1;
          return HttpResponse.json(older);
        }),
      );
      navState.search = "?breeding_id=99";
      renderWithProviders(<KiddingPage />);

      const dialog = await screen.findByRole("dialog", { name: "Record kidding" });
      expect(within(dialog).getByText(/Doe G-099 · due/)).toBeInTheDocument();
      expect(kiddingPregnancyCalls).toBe(1);
      expect(breedingDetailCalls).toBe(0);
    });

    it("does not honor a forged record deep link without kidding.manage", async () => {
      server.use(permissionsHandler(["kidding.view"]));
      navState.search = "?breeding_id=12";
      await renderLoaded();

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Record kidding" })).not.toBeInTheDocument();
    });

    it("opens the dialog when only the router knows the param, not window.location", async () => {
      navState.search = "?breeding_id=12";
      window.history.replaceState({}, "", "/kidding/new");
      renderWithProviders(<KiddingPage />);

      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("Record kidding")).toBeInTheDocument();
    });
  });
});
