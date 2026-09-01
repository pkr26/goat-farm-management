/**
 * Kidding page branch guards (companion to page.extended.test.tsx): the
 * conditions that decide what the page draws and when it talks to the API —
 * the deep-link id guard, the per-record kidding-date floor, the record
 * dialog's in-flight interlocks and per-field aria-invalid wiring, the kid
 * row numbering, the inline kids-cell separators, the doe-tag fallbacks,
 * the queue re-homing arithmetic and the permission gate.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  BreedingRecordOut,
  KiddingListOut,
  KiddingRecordOut,
} from "@/api/generated/models";
import { addDays, farmToday, formatDate } from "@/lib/format";
import { ALL_PERMISSIONS, permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

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
  // jsdom lacks APIs the Base UI select/dialog touch when opening a popup.
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

/** Farm-calendar fixture dates: the page's floors compare against the farm's
 * today, so fixtures are pinned relative to it rather than to the wall clock. */
const TODAY = farmToday();
function daysFromToday(delta: number): string {
  return addDays(TODAY, delta);
}
/** MIN_GESTATION_DAYS is 100, so a doe bred 120 days ago may not deliver
 * before 20 days ago. */
const BRED_ON = daysFromToday(-120);
const GESTATION_FLOOR = daysFromToday(-20);

function makeBreeding(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    semen_sire_name: null,
    breeding_date: BRED_ON,
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: daysFromToday(-90),
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

function card(title: string | RegExp): HTMLElement {
  return screen.getByText(title).closest("[data-slot='card']") as HTMLElement;
}

describe("KiddingPage branches", () => {
  let listRequests: URLSearchParams[];
  let postBody: Record<string, unknown> | null;
  let postCalls: number;
  let pregnancyCalls: number;
  let payload: KiddingListOut;

  beforeEach(() => {
    listRequests = [];
    postBody = null;
    postCalls = 0;
    pregnancyCalls = 0;
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
      http.get("/api/kidding", ({ request }) => {
        listRequests.push(new URL(request.url).searchParams);
        return HttpResponse.json(payload);
      }),
      http.get("/api/kidding/pregnancies/:recordId", ({ params }) => {
        pregnancyCalls += 1;
        const record = [...payload.overdue, ...payload.upcoming].find(
          (candidate) => String(candidate.id) === String(params.recordId),
        );
        return record
          ? HttpResponse.json(record)
          : HttpResponse.json({ detail: "Pregnancy not found" }, { status: 404 });
      }),
      http.post("/api/kidding", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeKidding({ id: 99 }), { status: 201 });
      }),
    );
  });

  afterEach(() => {
    navState.search = "";
  });

  async function renderLoaded() {
    const view = renderWithProviders(<KiddingPage />);
    await screen.findByText("Recent kiddings");
    await screen.findByText("Upcoming (next 30 days)");
    return view;
  }

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(card("Upcoming (next 30 days)")).getByRole("button", { name: "Record kidding" }),
    );
    return { user, dialog: await screen.findByRole("dialog") };
  }

  // ---------- page-level gates ----------

  it("waits for the permission set instead of flashing a denial", async () => {
    let releasePermissions!: () => void;
    let permissionsRequested = 0;
    const permissionsGate = new Promise<void>((resolve) => {
      releasePermissions = resolve;
    });
    server.use(
      http.get("/api/auth/permissions", async () => {
        permissionsRequested += 1;
        await permissionsGate;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );
    renderWithProviders(<KiddingPage />);

    await waitFor(() => expect(permissionsRequested).toBe(1));
    // The real header and skeleton stand in for the page — not a bare
    // "Loading…" line.
    expect(screen.getByRole("heading", { level: 1, name: "Kidding" })).toBeInTheDocument();
    expect(screen.queryByText("You don't have access to this page.")).not.toBeInTheDocument();

    releasePermissions();
    expect(await screen.findByText("Recent kiddings")).toBeInTheDocument();
  });

  it("announces the queues as settling only while a page transition is in flight", async () => {
    let releaseSecondPage!: () => void;
    const secondPageGate = new Promise<void>((resolve) => {
      releaseSecondPage = resolve;
    });
    server.use(
      http.get("/api/kidding", async ({ request }) => {
        const query = new URL(request.url).searchParams;
        const offset = Number(query.get("upcoming_offset") ?? 0);
        if (offset > 0) await secondPageGate;
        return HttpResponse.json({
          ...payload,
          upcoming_total: 60,
          upcoming_offset: offset,
        });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    expect(screen.queryByText("Updating kidding queues…")).not.toBeInTheDocument();

    await user.click(
      within(card("Upcoming (next 30 days)")).getByRole("button", { name: "Next" }),
    );

    expect(await screen.findByRole("status")).toHaveTextContent("Updating kidding queues…");

    releaseSecondPage();
    await waitFor(() =>
      expect(screen.queryByText("Updating kidding queues…")).not.toBeInTheDocument(),
    );
  });

  // ---------- doe identity + table shape ----------

  it("links each doe by tag, falling back to its id only when untagged", async () => {
    payload.upcoming = [UPCOMING_REC, makeBreeding({ id: 13, doe_id: 77, doe_tag: null })];
    payload.upcoming_total = 2;
    payload.records = [HISTORY, makeKidding({ id: 22, doe_id: 88, doe_tag: null, notes: "solo" })];
    payload.total = 2;
    await renderLoaded();

    const upcoming = card("Upcoming (next 30 days)");
    expect(within(upcoming).getByRole("link", { name: "G-010" })).toHaveAttribute(
      "href",
      "/animals/10",
    );
    expect(within(upcoming).getByRole("link", { name: "Doe #77" })).toHaveAttribute(
      "href",
      "/animals/77",
    );
    const history = card("Recent kiddings");
    expect(within(history).getByRole("link", { name: "G-010" })).toHaveAttribute(
      "href",
      "/animals/10",
    );
    expect(within(history).getByRole("link", { name: "Doe #88" })).toHaveAttribute(
      "href",
      "/animals/88",
    );
  });

  it("prints the doe tag as plain text without animals.view", async () => {
    server.use(permissionsHandler(["kidding.view", "kidding.manage"]));
    payload.upcoming = [UPCOMING_REC, makeBreeding({ id: 13, doe_id: 77, doe_tag: null })];
    payload.upcoming_total = 2;
    payload.records = [HISTORY, makeKidding({ id: 22, doe_id: 88, doe_tag: null, notes: "solo" })];
    payload.total = 2;
    await renderLoaded();

    const overdue = card(/Overdue/);
    expect(within(overdue).getByText("G-010")).toBeInTheDocument();
    expect(within(overdue).queryAllByRole("link")).toHaveLength(0);
    const upcoming = card("Upcoming (next 30 days)");
    expect(within(upcoming).getByText("G-010")).toBeInTheDocument();
    expect(within(upcoming).getByText("Doe #77")).toBeInTheDocument();
    expect(within(upcoming).queryAllByRole("link")).toHaveLength(0);
    const history = card("Recent kiddings");
    expect(within(history).getByText("G-010")).toBeInTheDocument();
    expect(within(history).getByText("Doe #88")).toBeInTheDocument();
    expect(within(history).queryAllByRole("link")).toHaveLength(0);
    // The id fallback belongs to untagged does only.
    expect(screen.queryByText("Doe #10")).not.toBeInTheDocument();
  });

  it("keeps the upcoming header aligned with the manage action column", async () => {
    await renderLoaded();
    const upcoming = card("Upcoming (next 30 days)");
    const [headerRow, bodyRow] = within(upcoming).getAllByRole("row");

    expect(within(headerRow).getAllByRole("columnheader")).toHaveLength(6);
    expect(within(bodyRow).getAllByRole("cell")).toHaveLength(6);
  });

  it("drops the upcoming action column entirely without kidding.manage", async () => {
    server.use(permissionsHandler(["kidding.view", "animals.view"]));
    await renderLoaded();
    const upcoming = card("Upcoming (next 30 days)");
    const [headerRow, bodyRow] = within(upcoming).getAllByRole("row");

    expect(within(headerRow).getAllByRole("columnheader")).toHaveLength(5);
    expect(within(bodyRow).getAllByRole("cell")).toHaveLength(5);
  });

  it("separates inline kids with a comma only between them", async () => {
    await renderLoaded();
    const row = screen.getByText("big twins").closest("tr")!;

    expect(within(row).getAllByRole("cell")[3].textContent).toBe(
      "G-101 (Female, alive), kid (Male, stillborn)",
    );
  });

  // ---------- queue offsets ----------

  it("re-homes each queue to its true last page when the totals shrink", async () => {
    const totals = { total: 200, upcoming_total: 100, overdue_total: 100 };
    server.use(
      http.get("/api/kidding", ({ request }) => {
        const query = new URL(request.url).searchParams;
        listRequests.push(query);
        return HttpResponse.json({
          ...payload,
          ...totals,
          limit: 50,
          offset: Number(query.get("offset") ?? 0),
          upcoming_limit: 25,
          upcoming_offset: Number(query.get("upcoming_offset") ?? 0),
          overdue_limit: 25,
          overdue_offset: Number(query.get("overdue_offset") ?? 0),
        });
      }),
    );
    const user = userEvent.setup();
    const { queryClient } = await renderLoaded();

    async function nextPage(title: string | RegExp, label: string) {
      const next = within(card(title)).getByRole("button", { name: "Next" });
      await waitFor(() => expect(next).toBeEnabled());
      await user.click(next);
      await screen.findByText(label);
    }
    await nextPage("Recent kiddings", "Showing 51–100 of 200 kidding records");
    await nextPage("Recent kiddings", "Showing 101–150 of 200 kidding records");
    await nextPage("Upcoming (next 30 days)", "Showing 26–50 of 100 upcoming pregnancies");
    await nextPage("Upcoming (next 30 days)", "Showing 51–75 of 100 upcoming pregnancies");
    await nextPage(/Overdue/, "Showing 26–50 of 100 overdue pregnancies");
    await nextPage(/Overdue/, "Showing 51–75 of 100 overdue pregnancies");

    totals.total = 100;
    totals.upcoming_total = 50;
    totals.overdue_total = 50;
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["/api/kidding"] });
    });

    await waitFor(() => {
      const latest = listRequests.at(-1);
      expect(latest?.get("offset")).toBe("50");
      expect(latest?.get("upcoming_offset")).toBe("25");
      expect(latest?.get("overdue_offset")).toBe("25");
    });
  });

  it("never asks an empty queue for a page before the first one", async () => {
    payload = {
      ...payload,
      records: [],
      total: 0,
      upcoming: [],
      upcoming_total: 0,
      overdue: [],
      overdue_total: 0,
    };
    const { queryClient } = renderWithProviders(<KiddingPage />);
    expect(await screen.findByText("No kiddings recorded yet.")).toBeInTheDocument();
    expect(
      screen.getByText("No confirmed pregnancies due in the next 30 days."),
    ).toBeInTheDocument();

    // Settling every queue proves the offsets the page committed to, not just
    // the ones it happened to have sent when the empty state first painted.
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["/api/kidding"] });
    });

    const offsets = new Set(
      listRequests.flatMap((query) => [
        query.get("offset"),
        query.get("upcoming_offset"),
        query.get("overdue_offset"),
      ]),
    );
    expect([...offsets]).toEqual(["0"]);
  });

  // ---------- deep link ----------

  it("ignores a deep-link id that is not a plain positive integer", async () => {
    payload.upcoming = [UPCOMING_REC, makeBreeding({ id: 100, doe_tag: "G-100" })];
    payload.upcoming_total = 2;
    navState.search = "?breeding_id=1e2";

    await renderLoaded();

    // The pregnancy is on the page — only the id parse keeps its dialog shut.
    expect(within(card("Upcoming (next 30 days)")).getByText("G-100")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("prefills a deep link from the loaded queues without a detail fetch", async () => {
    navState.search = "?breeding_id=12";

    renderWithProviders(<KiddingPage />);

    const dialog = await screen.findByRole("dialog", { name: "Record kidding" });
    expect(within(dialog).getByText(/Doe G-010 · due/)).toBeInTheDocument();
    expect(pregnancyCalls).toBe(0);
  });

  it("waits for the queues before fetching an off-page linked pregnancy", async () => {
    const offPage = makeBreeding({ id: 99, doe_tag: "G-099" });
    const order: string[] = [];
    let releaseList!: () => void;
    const listGate = new Promise<void>((resolve) => {
      releaseList = resolve;
    });
    server.use(
      http.get("/api/kidding", async ({ request }) => {
        listRequests.push(new URL(request.url).searchParams);
        await listGate;
        order.push("kidding list");
        return HttpResponse.json(payload);
      }),
      http.get("/api/kidding/pregnancies/99", () => {
        order.push("linked pregnancy");
        return HttpResponse.json(offPage);
      }),
    );
    navState.search = "?breeding_id=99";
    renderWithProviders(<KiddingPage />);

    await waitFor(() => expect(listRequests).toHaveLength(1));
    await act(async () => {});
    releaseList();

    const dialog = await screen.findByRole("dialog", { name: "Record kidding" });
    expect(within(dialog).getByText(/Doe G-099 · due/)).toBeInTheDocument();
    // The queues answer first; only a pregnancy missing from them is fetched.
    expect(order).toEqual(["kidding list", "linked pregnancy"]);
  });

  it("keeps a pending deep link alive when another row's dialog is dismissed", async () => {
    const offPage = makeBreeding({ id: 99, doe_tag: "G-099" });
    let releasePregnancy!: () => void;
    const pregnancyGate = new Promise<void>((resolve) => {
      releasePregnancy = resolve;
    });
    server.use(
      http.get("/api/kidding/pregnancies/99", async () => {
        pregnancyCalls += 1;
        await pregnancyGate;
        return HttpResponse.json(offPage);
      }),
    );
    navState.search = "?breeding_id=99";
    const user = userEvent.setup();
    await renderLoaded();
    await waitFor(() => expect(pregnancyCalls).toBe(1));

    // Recording another doe by hand while the linked pregnancy loads must not
    // retire the deep link the operator arrived from.
    await user.click(
      within(card("Upcoming (next 30 days)")).getByRole("button", { name: "Record kidding" }),
    );
    const handOpened = await screen.findByRole("dialog", { name: "Record kidding" });
    expect(within(handOpened).getByText(/Doe G-010 · due/)).toBeInTheDocument();
    await user.click(within(handOpened).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    releasePregnancy();

    const linked = await screen.findByRole("dialog", { name: "Record kidding" });
    expect(within(linked).getByText(/Doe G-099 · due/)).toBeInTheDocument();
  });

  // ---------- kidding date floor ----------

  it("floors the kidding date at the gestation minimum when no scan date exists", async () => {
    const { user, dialog } = await openDialog();
    const date = within(dialog).getByLabelText(/kidding date/i);
    expect(date).toHaveAttribute("min", GESTATION_FLOOR);

    fireEvent.change(date, { target: { value: daysFromToday(-21) } });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(
      await within(dialog).findByText(
        `Kidding date cannot be before ${formatDate(GESTATION_FLOOR)}`,
      ),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();

    fireEvent.change(date, { target: { value: GESTATION_FLOOR } });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ date: GESTATION_FLOOR });
  });

  it("keeps the gestation floor when the confirmation scan predates it", async () => {
    payload.upcoming = [{ ...UPCOMING_REC, ultrasound_result_date: daysFromToday(-40) }];
    const { user, dialog } = await openDialog();
    const date = within(dialog).getByLabelText(/kidding date/i);
    expect(date).toHaveAttribute("min", GESTATION_FLOOR);

    fireEvent.change(date, { target: { value: daysFromToday(-30) } });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(
      await within(dialog).findByText(
        `Kidding date cannot be before ${formatDate(GESTATION_FLOOR)}`,
      ),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  // ---------- dialog field wiring ----------

  it("numbers every kid row's controls from one", async () => {
    const { dialog } = await openDialog();

    for (const index of [1, 2]) {
      expect(within(dialog).getByLabelText(`Kid ${index} tag (auto if blank)`)).toBeInTheDocument();
      expect(within(dialog).getByLabelText(`Kid ${index} sex`)).toBeInTheDocument();
      expect(within(dialog).getByLabelText(`Kid ${index} weight (kg)`)).toBeInTheDocument();
      expect(within(dialog).getByLabelText(`Kid ${index} status`)).toBeInTheDocument();
    }
  });

  it("counts the listed kid rows as they are added and removed", async () => {
    const { user, dialog } = await openDialog();
    expect(within(dialog).getByText(new RegExp("2 kids listed"))).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Remove kid 2" }));
    expect(within(dialog).getByText(new RegExp("1 kid listed"))).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Add kid" }));
    expect(within(dialog).getByText(new RegExp("2 kids listed"))).toBeInTheDocument();
  });

  it("flags only the fields that failed validation as invalid", async () => {
    const { user, dialog } = await openDialog();
    const date = within(dialog).getByLabelText(/kidding date/i);
    const notes = within(dialog).getByLabelText(/notes/i);
    const tag = within(dialog).getByLabelText("Kid 1 tag (auto if blank)");
    const weight = within(dialog).getByLabelText("Kid 1 weight (kg)");
    for (const field of [date, notes, tag, weight]) {
      expect(field).not.toHaveAttribute("aria-invalid");
    }

    fireEvent.change(date, { target: { value: "" } });
    fireEvent.change(notes, { target: { value: "x".repeat(4_001) } });
    fireEvent.change(tag, { target: { value: "T".repeat(51) } });
    fireEvent.change(weight, { target: { value: "-1" } });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await within(dialog).findByText("Pick a valid date");
    expect(date).toHaveAttribute("aria-invalid", "true");
    expect(notes).toHaveAttribute("aria-invalid", "true");
    expect(tag).toHaveAttribute("aria-invalid", "true");
    expect(weight).toHaveAttribute("aria-invalid", "true");
    // The untouched second row stays clean.
    expect(within(dialog).getByLabelText("Kid 2 tag (auto if blank)")).not.toHaveAttribute(
      "aria-invalid",
    );
    expect(within(dialog).getByLabelText("Kid 2 weight (kg)")).not.toHaveAttribute(
      "aria-invalid",
    );
    expect(postBody).toBeNull();
  });

  it("asks for a mortality date without pre-flagging it as invalid", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Died");

    const mortality = within(dialog).getByLabelText("Kid 1 mortality date *");
    expect(mortality).not.toHaveAttribute("aria-invalid");
    expect(within(dialog).queryByText("Mortality date is required")).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(await within(dialog).findByText("Mortality date is required")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Kid 1 mortality date *")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("empties a mortality date the kid no longer qualifies for", async () => {
    const { user, dialog } = await openDialog();
    const status = within(dialog).getByLabelText("Kid 1 status");
    await pickOption(user, status, "Died");
    fireEvent.change(within(dialog).getByLabelText("Kid 1 mortality date *"), {
      target: { value: TODAY },
    });

    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Alive");
    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Died");

    expect(within(dialog).getByLabelText("Kid 1 mortality date *")).toHaveValue("");
  });

  it("clears a stale mortality error when the kid stops being DIED", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Died");
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    expect(await within(dialog).findByText("Mortality date is required")).toBeInTheDocument();

    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Stillborn");
    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Died");

    expect(within(dialog).queryByText("Mortality date is required")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Kid 1 mortality date *")).not.toHaveAttribute(
      "aria-invalid",
    );
  });

  it("sends a cleared birth weight as null rather than zero", async () => {
    const { user, dialog } = await openDialog();
    const weight = within(dialog).getByLabelText("Kid 1 weight (kg)");
    fireEvent.change(weight, { target: { value: "3" } });
    fireEvent.change(weight, { target: { value: "" } });

    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.kids).toEqual([
      { tag: null, sex: "F", birth_weight: null, status: "ALIVE", mortality_reported_at: null },
      { tag: null, sex: "F", birth_weight: null, status: "ALIVE", mortality_reported_at: null },
    ]);
  });

  // ---------- in-flight interlocks ----------

  it("locks every dialog control while the kidding POST is in flight", async () => {
    let releasePost!: () => void;
    let markPostStarted!: () => void;
    const postGate = new Promise<void>((resolve) => {
      releasePost = resolve;
    });
    const postStarted = new Promise<void>((resolve) => {
      markPostStarted = resolve;
    });
    server.use(
      http.post("/api/kidding", async () => {
        postCalls += 1;
        markPostStarted();
        await postGate;
        return HttpResponse.json(makeKidding({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    const controls = () => ({
      date: within(dialog).getByLabelText(/kidding date/i),
      ease: within(dialog).getByLabelText("Ease"),
      notes: within(dialog).getByLabelText(/notes/i),
      kids: within(dialog).getByRole("group", { name: "Kids" }),
      tag: within(dialog).getByLabelText("Kid 1 tag (auto if blank)"),
      cancel: within(dialog).getByRole("button", { name: "Cancel" }),
    });
    for (const control of Object.values(controls())) expect(control).toBeEnabled();
    expect(within(dialog).getByRole("button", { name: "Save kidding" })).toBeEnabled();

    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    await postStarted;

    for (const control of Object.values(controls())) expect(control).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Saving…" })).toBeDisabled();
    // A save in flight owns the dialog: Escape must not abandon it.
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    releasePost();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(postCalls).toBe(1);
  });

  it("stays locked when a stray resubmit resets the form's own submitting flag", async () => {
    let releasePost!: () => void;
    let markPostStarted!: () => void;
    const postGate = new Promise<void>((resolve) => {
      releasePost = resolve;
    });
    const postStarted = new Promise<void>((resolve) => {
      markPostStarted = resolve;
    });
    server.use(
      http.post("/api/kidding", async () => {
        postCalls += 1;
        markPostStarted();
        await postGate;
        return HttpResponse.json(makeKidding({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    await postStarted;

    // react-hook-form clears isSubmitting as soon as this second submission
    // returns; only the single-flight guard still knows the POST is running.
    await act(async () => {
      fireEvent.submit(dialog.querySelector("form")!);
    });

    expect(postCalls).toBe(1);
    expect(within(dialog).getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(within(dialog).getByRole("group", { name: "Kids" })).toBeDisabled();
    expect(within(dialog).getByLabelText(/kidding date/i)).toBeDisabled();
    expect(within(dialog).getByLabelText("Ease")).toBeDisabled();
    expect(within(dialog).getByLabelText(/notes/i)).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    releasePost();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(postCalls).toBe(1);
  });
});
