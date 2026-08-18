/**
 * Breeding page: list rendering (outcome badges, ultrasound cell, fallbacks),
 * RBAC gating (breeding.view / breeding.manage), the Add-breeding dialog
 * (candidate doe / active buck lists, zod validation, no-bucks guard,
 * submit mapping + server errors), the ultrasound outcome flow (pregnant
 * checkbox toggling the kid-count field, payload mapping), and the auditable
 * pregnancy-loss flow (date/cause/notes validation, POST, retry, refetch).
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnimalOut, BreedingRecordOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday, formatDate } from "@/lib/format";

import BreedingPage from "./page";

const { navState } = vi.hoisted(() => ({ navState: { search: "" } }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/breeding",
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

afterEach(() => vi.useRealTimers());

const TODAY = farmToday();

function makeAnimal(overrides: Partial<AnimalOut>): AnimalOut {
  return {
    id: 1,
    tag_number: "G-001",
    name: null,
    breed: "Osmanabadi",
    sex: "F",
    date_of_birth: "2024-01-01",
    estimated_dob: null,
    birth_type: null,
    source: "BORN",
    dam_id: null,
    sire_id: null,
    birth_weight: null,
    current_bucket: "BREEDING",
    status: "ACTIVE",
    status_date: null,
    sale_price: null,
    purchase_date: null,
    purchase_price: null,
    seller_name: null,
    cull_candidate: false,
    movement_restricted: false,
    restriction_reason: null,
    suspected_scheduled_disease: false,
    suspected_disease: null,
    authority_notified_at: null,
    restriction_cleared_at: null,
    restriction_cleared_by_id: null,
    restriction_clearance_reference: null,
    restriction_version: 0,
    mortality_cause: null,
    mortality_reported_at: null,
    notes: null,
    created_at: "2026-01-01T05:30:00Z",
    ...overrides,
  };
}

const DOE = makeAnimal({
  id: 10,
  tag_number: "G-010",
  name: "Lakshmi",
  sex: "F",
  age_months: 18,
  latest_weight_kg: 30,
});
const BUCK = makeAnimal({ id: 20, tag_number: "G-020", sex: "M", age_months: 24 });

function makeRecord(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    breeding_date: "2026-07-01",
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: "2026-08-02",
    ultrasound_result_date: null,
    ultrasound_done: false,
    pregnant: null,
    kid_count_detected: null,
    expected_kidding_date: null,
    outcome: "PENDING",
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

const PENDING_REC = makeRecord({ id: 1 });
const PREGNANT_REC = makeRecord({
  id: 2,
  ultrasound_done: true,
  pregnant: true,
  kid_count_detected: 2,
  expected_kidding_date: "2026-11-28",
  outcome: "CONFIRMED_PREGNANT",
});
const KIDDED_REC = makeRecord({
  id: 3,
  ultrasound_done: true,
  pregnant: true,
  kid_count_detected: 2,
  expected_kidding_date: "2026-11-28",
  outcome: "CONFIRMED_PREGNANT",
  has_kidding: true,
});
const FAILED_REC = makeRecord({
  id: 4,
  ultrasound_done: true,
  pregnant: false,
  outcome: "FAILED",
});
/** Bred 21 days ago — the documented heat cycle — so the doe can be seen back
 *  in standing heat 11 days before the day-32 scan the record still plans. */
const HEAT_RETURN_REC = makeRecord({
  id: 80,
  breeding_date: addDays(TODAY, -21),
  ultrasound_date: addDays(TODAY, 11),
});
const ABORTED_REC = makeRecord({
  id: 5,
  ultrasound_done: true,
  pregnant: true,
  outcome: "ABORTED",
  loss_date: "2026-08-04",
  loss_cause: "INJURY",
  loss_notes: "Fence accident",
  loss_recorded_by_id: 7,
  loss_recorded_at: "2026-08-04T12:00:00Z",
});

function rowOf(text: string): HTMLElement {
  const row = screen.getByText(text).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

/** Row containing the first match of possibly-duplicated text. */
function firstRowOf(text: string): HTMLElement {
  const row = screen.getAllByText(text)[0].closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

/** Open a Radix select and pick an option by its accessible name. */
async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("BreedingPage", () => {
  let listCalls: number;
  let breedingPostBody: Record<string, unknown> | null;
  let ultrasoundBody: Record<string, unknown> | null;
  let abortCalls: number;
  let abortBody: Record<string, unknown> | null;
  let listPayload: {
    records: BreedingRecordOut[];
    candidate_availability: {
      eligible_doe_count: number;
      eligible_buck_count: number;
    } | null;
    total: number;
    limit: number;
    offset: number;
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    listCalls = 0;
    breedingPostBody = null;
    ultrasoundBody = null;
    abortCalls = 0;
    abortBody = null;
    listPayload = {
      records: [PENDING_REC, PREGNANT_REC, KIDDED_REC, FAILED_REC, ABORTED_REC],
      candidate_availability: {
        eligible_doe_count: 1,
        eligible_buck_count: 1,
      },
      total: 5,
      limit: 50,
      offset: 0,
    };
    server.use(
      http.get("/api/breeding", () => {
        listCalls += 1;
        return HttpResponse.json(listPayload);
      }),
      http.get("/api/breeding/candidates", ({ request }) => {
        const url = new URL(request.url);
        const candidates = url.searchParams.get("kind") === "doe" ? [DOE] : [BUCK];
        return HttpResponse.json({
          candidates: candidates.map((animal) => ({
            id: animal.id,
            tag_number: animal.tag_number,
            name: animal.name,
            age_months: animal.age_months ?? null,
            latest_weight_kg: animal.latest_weight_kg ?? null,
          })),
          total: candidates.length,
          limit: 50,
          offset: 0,
        });
      }),
      http.post("/api/breeding", async ({ request }) => {
        breedingPostBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeRecord({ id: 99 }), { status: 201 });
      }),
      http.post("/api/breeding/:recordId/ultrasound", async ({ request }) => {
        ultrasoundBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeRecord({ id: 1, ultrasound_done: true }));
      }),
      http.post("/api/breeding/:recordId/abort", async ({ request }) => {
        abortCalls += 1;
        abortBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          makeRecord({
            id: 2,
            outcome: "ABORTED",
            loss_date: String(abortBody.loss_date),
            loss_cause: String(abortBody.cause),
            loss_notes: abortBody.notes as string | null,
          }),
        );
      }),
    );
  });

  async function renderLoaded() {
    renderWithProviders(<BreedingPage />);
    // First record's formatted date proves perms + payload resolved.
    await screen.findAllByText("1 Jul 2026");
  }

  // ---------- list rendering ----------

  it("renders the records table with formatted dates and animal links", async () => {
    await renderLoaded();
    const row = firstRowOf("CONFIRMED PREGNANT");
    const doeLink = within(row).getByRole("link", { name: "G-010" });
    expect(doeLink).toHaveAttribute("href", "/animals/10");
    const buckLink = within(row).getByRole("link", { name: "G-020" });
    expect(buckLink).toHaveAttribute("href", "/animals/20");
    expect(within(row).getByText("28 Nov 2026")).toBeInTheDocument();
    expect(within(row).getByText("1")).toBeInTheDocument(); // heat cycle
  });

  it("renders outcome badge text with underscores replaced", async () => {
    await renderLoaded();
    expect(screen.getByText("PENDING")).toBeInTheDocument();
    expect(screen.getAllByText("CONFIRMED PREGNANT")).toHaveLength(2);
    expect(screen.getByText("FAILED")).toBeInTheDocument();
    expect(screen.getByText("ABORTED")).toBeInTheDocument();
  });

  it("renders the recorded pregnancy-loss facts", async () => {
    await renderLoaded();
    const row = rowOf("ABORTED");
    expect(within(row).getByText("4 Aug 2026 · INJURY")).toBeInTheDocument();
    expect(within(row).getByText("Fence accident")).toBeInTheDocument();
  });

  it("ultrasound cell shows done / due date / dash per record state", async () => {
    await renderLoaded();
    expect(within(rowOf("PENDING")).getByText("due 2 Aug 2026")).toBeInTheDocument();
    expect(within(firstRowOf("CONFIRMED PREGNANT")).getByText("done")).toBeInTheDocument();
  });

  it("kid count and expected-kidding cells fall back to a dash", async () => {
    await renderLoaded();
    const pendingRow = rowOf("PENDING");
    expect(within(pendingRow).getAllByText("—").length).toBeGreaterThanOrEqual(2);
    expect(within(firstRowOf("CONFIRMED PREGNANT")).getByText("2")).toBeInTheDocument();
  });

  it("falls back to 'Doe #id' / 'Buck #id' when tags are absent", async () => {
    listPayload.records = [
      makeRecord({ id: 9, doe_id: 77, buck_id: 88, doe_tag: null, buck_tag: null }),
    ];
    renderWithProviders(<BreedingPage />);
    expect(await screen.findByRole("link", { name: "Doe #77" })).toHaveAttribute(
      "href",
      "/animals/77",
    );
    expect(screen.getByRole("link", { name: "Buck #88" })).toHaveAttribute(
      "href",
      "/animals/88",
    );
  });

  it("shows the empty state when there are no records", async () => {
    listPayload.records = [];
    renderWithProviders(<BreedingPage />);
    expect(await screen.findByText("No breeding records yet.")).toBeInTheDocument();
  });

  it("shows the server error detail when the list fails", async () => {
    server.use(
      http.get("/api/breeding", () =>
        HttpResponse.json({ detail: "breeding table exploded" }, { status: 500 }),
      ),
    );
    renderWithProviders(<BreedingPage />);
    expect(await screen.findByText("breeding table exploded")).toBeInTheDocument();
  });

  it("announces a list failure and retries it in place", async () => {
    let fail = true;
    let calls = 0;
    server.use(
      http.get("/api/breeding", () => {
        calls += 1;
        return fail
          ? HttpResponse.json({ detail: "breeding temporarily unavailable" }, { status: 503 })
          : HttpResponse.json(listPayload);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "breeding temporarily unavailable",
    );
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry breeding records" }));

    expect(await screen.findByText("Breeding records")).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  // ---------- RBAC ----------

  it("blocks the page without breeding.view", async () => {
    server.use(permissionsHandler(["breeding.manage"]));
    renderWithProviders(<BreedingPage />);
    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
  });

  it("hides Add breeding and the Actions column without breeding.manage", async () => {
    server.use(permissionsHandler(["breeding.view"]));
    await renderLoaded();
    expect(
      screen.queryByRole("button", { name: "Add breeding" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Actions")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Ultrasound result" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record loss" })).not.toBeInTheDocument();
  });

  it("uses breeding-scoped candidates and plain tags for a module-only manager", async () => {
    let generalAnimalCalls = 0;
    server.use(
      permissionsHandler(["breeding.view", "breeding.manage"]),
      http.get("/api/animals", () => {
        generalAnimalCalls += 1;
        return HttpResponse.json({ animals: [], total: 0 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    expect(screen.queryByRole("link", { name: "G-010" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add breeding" }));
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });
    await user.click(within(dialog).getByRole("combobox", { name: "Doe *" }));
    expect(await screen.findByRole("option", { name: /G-010 · Lakshmi/ })).toBeInTheDocument();
    expect(generalAnimalCalls).toBe(0);
  });

  it("shows management actions per row state with breeding.manage", async () => {
    await renderLoaded();
    expect(within(rowOf("PENDING")).getByRole("button", { name: "Ultrasound result" }))
      .toBeInTheDocument();
    const pregnantRow = screen.getAllByText("CONFIRMED PREGNANT")[0].closest("tr")!;
    expect(within(pregnantRow).getByRole("button", { name: "Record loss" })).toBeInTheDocument();
    const kiddedRow = screen.getAllByText("CONFIRMED PREGNANT")[1].closest("tr")!;
    expect(within(kiddedRow).getByText("Kidded")).toBeInTheDocument();
    expect(
      within(kiddedRow).queryByRole("button", { name: "Record loss" }),
    ).not.toBeInTheDocument();
    expect(
      within(rowOf("FAILED")).queryByRole("button"),
    ).not.toBeInTheDocument();
  });

  it("offers the result action before the planned date, keeping the plan as a hint", async () => {
    listPayload.records = [HEAT_RETURN_REC];
    renderWithProviders(<BreedingPage />);
    const pending = await screen.findByText("PENDING");
    const row = pending.closest("tr") as HTMLElement;

    // A doe back in standing heat has to be recordable as not-pregnant now.
    expect(within(row).getByRole("button", { name: "Ultrasound result" })).toBeInTheDocument();
    expect(
      within(row).getByText(`Scan planned ${formatDate(HEAT_RETURN_REC.ultrasound_date)}`),
    ).toBeInTheDocument();
  });

  it("shows no planned-scan hint once the scan date has arrived", async () => {
    await renderLoaded();
    expect(within(rowOf("PENDING")).queryByText(/^Scan planned /)).not.toBeInTheDocument();
  });

  // ---------- Add-breeding dialog ----------

  async function openNewDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add breeding" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("opens the dialog and lists only candidate does and active bucks", async () => {
    const { user, dialog } = await openNewDialog();
    await waitFor(() =>
      expect(within(dialog).queryByText("Loading animals…")).not.toBeInTheDocument(),
    );
    const [doeTrigger, buckTrigger] = within(dialog).getAllByRole("combobox");

    await user.click(doeTrigger);
    expect(
      await screen.findByRole("option", { name: /G-010 · Lakshmi — 18 mo, 30\.0 kg/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /G-030/ })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(buckTrigger);
    expect(
      await screen.findByRole("option", { name: /G-020 — 24 mo/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /G-010/ })).not.toBeInTheDocument();
  });

  it("shows the animal label, not the raw id, in the closed triggers after selection", async () => {
    const { user, dialog } = await openNewDialog();
    await waitFor(() =>
      expect(within(dialog).queryByText("Loading animals…")).not.toBeInTheDocument(),
    );
    const [doeTrigger, buckTrigger] = within(dialog).getAllByRole("combobox");

    await pickOption(user, doeTrigger, /G-010 · Lakshmi — 18 mo, 30\.0 kg/);
    expect(doeTrigger).toHaveTextContent("G-010 · Lakshmi — 18 mo, 30.0 kg");

    await pickOption(user, buckTrigger, /G-020 — 24 mo/);
    expect(buckTrigger).toHaveTextContent("G-020 — 24 mo");
  });

  it("defaults the breeding date to today and caps it at today", async () => {
    const { dialog } = await openNewDialog();
    const dateInput = within(dialog).getByLabelText(/breeding date/i);
    expect(dateInput).toHaveValue(TODAY);
    expect(dateInput).toHaveAttribute("max", TODAY);
  });

  it("refreshes the breeding-date default when a long-lived tab crosses midnight", async () => {
    vi.setSystemTime(new Date("2026-08-09T12:30:00Z")); // 18:00 IST
    const user = userEvent.setup();
    await renderLoaded();

    vi.setSystemTime(new Date("2026-08-10T12:30:00Z")); // 18:00 IST, next day
    await user.click(screen.getByRole("button", { name: "Add breeding" }));

    const dateInput = await screen.findByLabelText(/breeding date/i);
    expect(dateInput).toHaveValue("2026-08-10");
    expect(dateInput).toHaveAttribute("max", "2026-08-10");
  });

  it("shows the no-candidates guidance when no does are breeding-ready", async () => {
    listPayload.candidate_availability = {
      eligible_doe_count: 0,
      eligible_buck_count: 1,
    };
    const { dialog } = await openNewDialog();
    expect(
      await within(dialog).findByText(/No breeding-ready does right now/),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "Save breeding" }),
    ).not.toBeInTheDocument();
  });

  it("shows the no-bucks warning and disables Save when there are no active bucks", async () => {
    listPayload.candidate_availability = {
      eligible_doe_count: 1,
      eligible_buck_count: 0,
    };
    const { dialog } = await openNewDialog();
    expect(
      await within(dialog).findByText(/No eligible bucks are available/),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save breeding" })).toBeDisabled();
  });

  it("does not treat a held active male as a selectable buck", async () => {
    listPayload.candidate_availability = {
      eligible_doe_count: 1,
      eligible_buck_count: 0,
    };
    server.use(
      http.get("/api/breeding/candidates", ({ request }) => {
        const url = new URL(request.url);
        const candidates = url.searchParams.get("kind") === "doe" ? [DOE] : [];
        return HttpResponse.json({
          candidates: candidates.map((animal) => ({
            id: animal.id,
            tag_number: animal.tag_number,
            name: animal.name,
            age_months: animal.age_months ?? null,
            latest_weight_kg: animal.latest_weight_kg ?? null,
          })),
          total: candidates.length,
          limit: 50,
          offset: 0,
        });
      }),
    );

    const { dialog } = await openNewDialog();
    expect(await within(dialog).findByText(/No eligible bucks are available/)).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "Buck *" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Save breeding" })).toBeDisabled();
  });

  it("validates doe/buck selection and a non-empty date before posting", async () => {
    const { user, dialog } = await openNewDialog();
    await within(dialog).findByText("Select doe"); // animals loaded
    await user.clear(within(dialog).getByLabelText(/breeding date/i));
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    expect(await within(dialog).findByText("Select a doe")).toBeInTheDocument();
    expect(within(dialog).getByText("Select a buck")).toBeInTheDocument();
    expect(within(dialog).getByText("Pick a valid date")).toBeInTheDocument();
    expect(breedingPostBody).toBeNull();
  });

  it("rejects a future breeding date (typed input bypasses the max attribute)", async () => {
    const { user, dialog } = await openNewDialog();
    await within(dialog).findByText("Select doe"); // animals loaded
    const [doeTrigger, buckTrigger] = within(dialog).getAllByRole("combobox");
    await pickOption(user, doeTrigger, /G-010 · Lakshmi/);
    await pickOption(user, buckTrigger, /G-020 — 24 mo/);
    fireEvent.change(within(dialog).getByLabelText(/breeding date/i), {
      target: { value: addDays(TODAY, 1) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    expect(
      await within(dialog).findByText("Date can't be in the future"),
    ).toBeInTheDocument();
    expect(breedingPostBody).toBeNull();
  });

  it("posts numeric ids with the date, closes, and refetches the list", async () => {
    const { user, dialog } = await openNewDialog();
    await within(dialog).findByText("Select doe");
    const [doeTrigger, buckTrigger] = within(dialog).getAllByRole("combobox");
    await pickOption(user, doeTrigger, /G-010 · Lakshmi/);
    await pickOption(user, buckTrigger, /G-020 — 24 mo/);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    await waitFor(() => expect(breedingPostBody).not.toBeNull());
    expect(breedingPostBody).toEqual({
      doe_id: 10,
      buck_id: 20,
      breeding_date: TODAY,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("keeps the dialog open when the server rejects the breeding", async () => {
    server.use(
      http.post("/api/breeding", () =>
        HttpResponse.json({ detail: "Doe is already pregnant" }, { status: 422 }),
      ),
    );
    const { user, dialog } = await openNewDialog();
    await within(dialog).findByText("Select doe");
    const [doeTrigger, buckTrigger] = within(dialog).getAllByRole("combobox");
    await pickOption(user, doeTrigger, /G-010 · Lakshmi/);
    await pickOption(user, buckTrigger, /G-020 — 24 mo/);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    await waitFor(() => expect(listCalls).toBe(1)); // no invalidation happened
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("announces a failed breeding save and offers an in-dialog retry", async () => {
    let calls = 0;
    server.use(
      http.post("/api/breeding", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "Doe eligibility changed" }, { status: 409 })
          : HttpResponse.json(makeRecord({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openNewDialog();
    await within(dialog).findByText("Select doe");
    const [doeTrigger, buckTrigger] = within(dialog).getAllByRole("combobox");
    await pickOption(user, doeTrigger, /G-010 · Lakshmi/);
    await pickOption(user, buckTrigger, /G-020 — 24 mo/);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Doe eligibility changed",
    );
    await user.click(within(dialog).getByRole("button", { name: "Retry save breeding" }));

    await waitFor(() => expect(calls).toBe(2));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("single-flights a double-click on Save breeding", async () => {
    let calls = 0;
    server.use(
      http.post("/api/breeding", () => {
        calls += 1;
        return HttpResponse.json(makeRecord({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openNewDialog();
    await within(dialog).findByText("Select doe");
    const [doeTrigger, buckTrigger] = within(dialog).getAllByRole("combobox");
    await pickOption(user, doeTrigger, /G-010 · Lakshmi/);
    await pickOption(user, buckTrigger, /G-020 — 24 mo/);

    await user.dblClick(within(dialog).getByRole("button", { name: "Save breeding" }));
    await waitFor(() => expect(calls).toBe(1));
  });

  // ---------- Ultrasound dialog ----------

  async function openUltrasound() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(rowOf("PENDING")).getByRole("button", { name: "Ultrasound result" }),
    );
    return { user, dialog: await screen.findByRole("dialog") };
  }

  // REGRESSION — with the scan due, the dialog used to open with "Pregnant —
  // confirmed" already checked and kid count prefilled to 2, so an operator
  // recording a NEGATIVE scan who trusted the prefill silently booked a false
  // confirmed pregnancy (bucket move + follow-up tasks). A diagnostic outcome
  // now always starts neutral: unchecked, no kid count, until chosen.
  it("describes the record and starts with no pre-checked pregnancy outcome", async () => {
    const { dialog } = await openUltrasound();
    expect(
      within(dialog).getByText(/Doe G-010 · bred 1 Jul 2026 by G-020/),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/planned scan 2 Aug 2026/)).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox")).not.toBeChecked();
    expect(within(dialog).queryByText("Kid count detected")).not.toBeInTheDocument();
  });

  it("posts a not-pregnant result when the operator saves without checking the box", async () => {
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundBody).toEqual({ pregnant: false, date: farmToday(), kid_count: null });
  });

  it("saves a pregnant result with the default kid count of 2 once checked", async () => {
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("checkbox"));
    // The twins default appears only after the explicit positive choice.
    expect(within(dialog).getByText("Kid count detected")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundBody).toEqual({ pregnant: true, date: farmToday(), kid_count: 2 });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("saves the selected kid count when changed", async () => {
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("checkbox"));
    await pickOption(user, within(dialog).getByRole("combobox"), "3");
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundBody).toEqual({ pregnant: true, date: farmToday(), kid_count: 3 });
  });

  it("rejects a pregnant result date before the planned scan date", async () => {
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("checkbox"));
    fireEvent.change(within(dialog).getByLabelText("Result date *"), {
      target: { value: "2026-08-01" },
    });

    expect(
      within(dialog).getByText("Result date cannot be before 2 Aug 2026"),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save result" })).toBeDisabled();
    expect(ultrasoundBody).toBeNull();
  });

  // The server accepts a NOT-pregnant result from the breeding date onwards —
  // only a positive one has to wait for the planned scan — so the doe seen
  // back in heat at day 21 must be recordable without falsifying a day-32 scan.
  async function openEarlyUltrasound() {
    const user = userEvent.setup();
    listPayload.records = [HEAT_RETURN_REC];
    renderWithProviders(<BreedingPage />);
    const row = (await screen.findByText("PENDING")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Ultrasound result" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("records an early not-pregnant result for a doe back in heat", async () => {
    const { user, dialog } = await openEarlyUltrasound();

    expect(within(dialog).getByRole("checkbox")).not.toBeChecked();
    expect(within(dialog).queryByText("Kid count detected")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Result date *")).toHaveAttribute(
      "min",
      HEAT_RETURN_REC.breeding_date,
    );
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Save result" }));
    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundBody).toEqual({ pregnant: false, date: TODAY, kid_count: null });
  });

  it("still holds a pregnant result back to the planned scan date", async () => {
    const { user, dialog } = await openEarlyUltrasound();
    await user.click(within(dialog).getByRole("checkbox"));

    expect(
      within(dialog).getByText(
        `Result date cannot be before ${formatDate(HEAT_RETURN_REC.ultrasound_date)}`,
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save result" })).toBeDisabled();
    expect(ultrasoundBody).toBeNull();
  });

  it("rejects a not-pregnant result that predates the breeding date", async () => {
    const { dialog } = await openEarlyUltrasound();
    fireEvent.change(within(dialog).getByLabelText("Result date *"), {
      target: { value: addDays(HEAT_RETURN_REC.breeding_date, -1) },
    });

    expect(
      within(dialog).getByText(
        `Result date cannot be before ${formatDate(HEAT_RETURN_REC.breeding_date)}`,
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save result" })).toBeDisabled();
    expect(ultrasoundBody).toBeNull();
  });

  it("hides the kid count and posts kid_count null when unchecked again", async () => {
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(within(dialog).getByText("Kid count detected")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(within(dialog).queryByText("Kid count detected")).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Save result" }));
    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundBody).toEqual({ pregnant: false, date: farmToday(), kid_count: null });
  });

  it("clears a hidden kid count instead of restoring stale data", async () => {
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("checkbox"));
    await pickOption(user, within(dialog).getByRole("combobox"), "3");
    await user.click(within(dialog).getByRole("checkbox"));
    expect(within(dialog).queryByRole("combobox")).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("checkbox"));
    expect(within(dialog).getByRole("combobox")).toHaveTextContent("2");
  });

  it("Cancel closes the dialog without posting", async () => {
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(ultrasoundBody).toBeNull();
  });

  it("keeps the dialog open when the ultrasound save fails", async () => {
    server.use(
      http.post("/api/breeding/:recordId/ultrasound", () =>
        HttpResponse.json({ detail: "too early for a scan" }, { status: 400 }),
      ),
    );
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    await waitFor(() => expect(listCalls).toBe(1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("announces an ultrasound failure and retries without losing the form", async () => {
    let calls = 0;
    server.use(
      http.post("/api/breeding/:recordId/ultrasound", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "scanner result conflict" }, { status: 409 })
          : HttpResponse.json(makeRecord({ id: 1, ultrasound_done: true }));
      }),
    );
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "scanner result conflict",
    );
    await user.click(within(dialog).getByRole("button", { name: "Retry save result" }));
    await waitFor(() => expect(calls).toBe(2));
  });

  // ---------- Pregnancy-loss flow ----------

  async function openPregnancyLossDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    const pregnantRow = screen.getAllByText("CONFIRMED PREGNANT")[0].closest("tr")!;
    await user.click(within(pregnantRow).getByRole("button", { name: "Record loss" }));
    const dialog = await screen.findByRole("dialog", { name: "Record pregnancy loss" });
    return { user, dialog };
  }

  it("collects auditable loss facts and refetches after the POST", async () => {
    const { user, dialog } = await openPregnancyLossDialog();
    await pickOption(user, within(dialog).getByLabelText("Cause *"), "DISEASE");
    await user.type(within(dialog).getByLabelText("Notes"), "Lab-confirmed infection");
    await user.click(within(dialog).getByRole("button", { name: "Record pregnancy loss" }));

    await waitFor(() => expect(abortCalls).toBe(1));
    expect(abortBody).toEqual({
      loss_date: TODAY,
      cause: "DISEASE",
      notes: "Lab-confirmed infection",
    });
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("does not record a loss when the dialog is cancelled", async () => {
    const { user, dialog } = await openPregnancyLossDialog();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(abortCalls).toBe(0);
    expect(listCalls).toBe(1);
  });

  it("rejects a loss date before the breeding chronology", async () => {
    const { dialog } = await openPregnancyLossDialog();
    fireEvent.change(within(dialog).getByLabelText("Loss date *"), {
      target: { value: "2026-06-30" },
    });

    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Loss date cannot be before 1 Jul 2026",
    );
    expect(within(dialog).getByRole("button", { name: "Record pregnancy loss" })).toBeDisabled();
    expect(abortCalls).toBe(0);
  });

  it("keeps the loss dialog and facts available after a server conflict", async () => {
    let failedAbortCalls = 0;
    server.use(
      http.post("/api/breeding/:recordId/abort", () => {
        failedAbortCalls += 1;
        return HttpResponse.json({ detail: "kidding already recorded" }, { status: 409 });
      }),
    );
    const { user, dialog } = await openPregnancyLossDialog();
    await user.type(within(dialog).getByLabelText("Notes"), "Observed loss");
    await user.click(within(dialog).getByRole("button", { name: "Record pregnancy loss" }));

    await waitFor(() => expect(failedAbortCalls).toBe(1));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("kidding already recorded");
    expect(within(dialog).getByLabelText("Notes")).toHaveValue("Observed loss");
    expect(listCalls).toBe(1); // no refresh on failure
    expect(screen.getAllByText("CONFIRMED PREGNANT")).toHaveLength(2);
  });

  it("announces a loss conflict and retries without losing the form", async () => {
    let calls = 0;
    server.use(
      http.post("/api/breeding/:recordId/abort", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "pregnancy changed" }, { status: 409 })
          : HttpResponse.json(makeRecord({ id: 2, outcome: "ABORTED" }));
      }),
    );
    const { user, dialog } = await openPregnancyLossDialog();
    await user.click(within(dialog).getByRole("button", { name: "Record pregnancy loss" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("pregnancy changed");
    await user.click(within(dialog).getByRole("button", { name: "Retry record loss" }));
    await waitFor(() => expect(calls).toBe(2));
  });

  // ---------- URL prefill (/breeding/{id}/ultrasound redirect) ----------

  // REGRESSION — the id used to be read from window.location.search in a
  // useState initializer. Next 16 writes the browser URL in HistoryUpdater's
  // useInsertionEffect, i.e. after the destination page has rendered, so the
  // /breeding/{id}/ultrasound → /breeding?ultrasound_id={id} redirect left the
  // page reading the PREVIOUS URL and the dialog never opened. The param now
  // comes from useSearchParams(), which is why these tests drive the router
  // mock rather than window.location.
  describe("URL prefill from /breeding?ultrasound_id=…", () => {
    afterEach(() => {
      navState.search = "";
      window.history.replaceState({}, "", "/breeding");
    });

    it("auto-opens the ultrasound dialog for the linked PENDING record", async () => {
      navState.search = "?ultrasound_id=1";
      renderWithProviders(<BreedingPage />);
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("Ultrasound result")).toBeInTheDocument();
      expect(
        within(dialog).getByText(/Doe G-010 · bred 1 Jul 2026 by G-020/),
      ).toBeInTheDocument();
    });

    it("opens no dialog when the linked record is not PENDING", async () => {
      navState.search = "?ultrasound_id=2";
      await renderLoaded();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("does not reinterpret a scientific-notation deep-link id as another record", async () => {
      let detailCalls = 0;
      server.use(
        http.get("/api/breeding/100", () => {
          detailCalls += 1;
          return HttpResponse.json(makeRecord({ id: 100 }));
        }),
      );
      navState.search = "?ultrasound_id=1e2";

      await renderLoaded();

      expect(detailCalls).toBe(0);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("fetches and opens an older linked record outside the current page", async () => {
      const older = makeRecord({ id: 99, breeding_date: "2026-06-01", ultrasound_date: "2026-07-03" });
      server.use(http.get("/api/breeding/99", () => HttpResponse.json(older)));
      navState.search = "?ultrasound_id=99";
      renderWithProviders(<BreedingPage />);

      const dialog = await screen.findByRole("dialog", { name: "Ultrasound result" });
      expect(within(dialog).getByText(/bred 1 Jun 2026/)).toBeInTheDocument();
      expect(within(dialog).getByText(/planned scan 3 Jul 2026/)).toBeInTheDocument();
    });

    it("opens a linked record whose planned scan is still ahead", async () => {
      // The task deep-link is only gated on PENDING now: the dialog itself
      // decides that just the not-pregnant result is recordable this early.
      listPayload.records = [HEAT_RETURN_REC];
      navState.search = "?ultrasound_id=80";
      renderWithProviders(<BreedingPage />);

      const dialog = await screen.findByRole("dialog", { name: "Ultrasound result" });
      expect(within(dialog).getByRole("checkbox")).not.toBeChecked();
    });

    it("opens the dialog when only the router knows the param, not window.location", async () => {
      // Exactly the redirect ordering: the router already carries the query
      // string while document.location is still the pre-navigation URL.
      navState.search = "?ultrasound_id=1";
      window.history.replaceState({}, "", "/breeding/1/ultrasound");
      renderWithProviders(<BreedingPage />);

      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("Ultrasound result")).toBeInTheDocument();
    });

    it("opens a different deep-linked record after the first link was dismissed", async () => {
      const secondPending = makeRecord({
        id: 6,
        doe_tag: "G-006",
        breeding_date: "2026-07-02",
        ultrasound_date: "2026-08-03",
      });
      listPayload.records = [PENDING_REC, secondPending];
      listPayload.total = 2;
      navState.search = "?ultrasound_id=1";
      const view = renderWithProviders(<BreedingPage />);
      const firstDialog = await screen.findByRole("dialog", { name: "Ultrasound result" });
      await userEvent
        .setup()
        .click(within(firstDialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

      navState.search = "?ultrasound_id=6";
      view.rerender(<BreedingPage />);

      const nextDialog = await screen.findByRole("dialog", { name: "Ultrasound result" });
      expect(within(nextDialog).getByText(/Doe G-006 · bred 2 Jul 2026/)).toBeInTheDocument();
    });
  });
});
