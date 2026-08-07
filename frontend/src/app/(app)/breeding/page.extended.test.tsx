/**
 * Breeding page: list rendering (outcome badges, ultrasound cell, fallbacks),
 * RBAC gating (breeding.view / breeding.manage), the Add-breeding dialog
 * (candidate doe / active buck lists, zod validation, no-bucks guard,
 * submit mapping + server errors), the ultrasound outcome flow (pregnant
 * checkbox toggling the kid-count field, payload mapping), and the abort
 * flow (window.confirm guard, POST, refetch).
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnimalOut, BreedingRecordOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import BreedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/breeding",
  useSearchParams: () => new URLSearchParams(),
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

/** Local YYYY-MM-DD (mirrors the page's localToday). */
function localISO(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}
const TODAY = localISO(new Date());

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
const NOT_CANDIDATE = makeAnimal({ id: 30, tag_number: "G-030", sex: "F" });

function makeRecord(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    breeding_date: "2026-07-01",
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: "2026-08-02",
    ultrasound_done: false,
    pregnant: null,
    kid_count_detected: null,
    expected_kidding_date: null,
    outcome: "PENDING",
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
const ABORTED_REC = makeRecord({
  id: 5,
  ultrasound_done: true,
  pregnant: true,
  outcome: "ABORTED",
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
  let listPayload: {
    records: BreedingRecordOut[];
    candidate_doe_ids: number[];
    active_buck_ids: number[];
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    listCalls = 0;
    breedingPostBody = null;
    ultrasoundBody = null;
    abortCalls = 0;
    listPayload = {
      records: [PENDING_REC, PREGNANT_REC, KIDDED_REC, FAILED_REC, ABORTED_REC],
      candidate_doe_ids: [10],
      active_buck_ids: [20],
    };
    server.use(
      http.get("/api/breeding", () => {
        listCalls += 1;
        return HttpResponse.json(listPayload);
      }),
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [DOE, BUCK, NOT_CANDIDATE], total: 3 }),
      ),
      http.post("/api/breeding", async ({ request }) => {
        breedingPostBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeRecord({ id: 99 }), { status: 201 });
      }),
      http.post("/api/breeding/:recordId/ultrasound", async ({ request }) => {
        ultrasoundBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeRecord({ id: 1, ultrasound_done: true }));
      }),
      http.post("/api/breeding/:recordId/abort", () => {
        abortCalls += 1;
        return HttpResponse.json(makeRecord({ id: 2, outcome: "ABORTED" }));
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
    expect(screen.queryByRole("button", { name: "Abort" })).not.toBeInTheDocument();
  });

  it("shows management actions per row state with breeding.manage", async () => {
    await renderLoaded();
    expect(within(rowOf("PENDING")).getByRole("button", { name: "Ultrasound result" }))
      .toBeInTheDocument();
    const pregnantRow = screen.getAllByText("CONFIRMED PREGNANT")[0].closest("tr")!;
    expect(within(pregnantRow).getByRole("button", { name: "Abort" })).toBeInTheDocument();
    const kiddedRow = screen.getAllByText("CONFIRMED PREGNANT")[1].closest("tr")!;
    expect(within(kiddedRow).getByText("Kidded")).toBeInTheDocument();
    expect(
      within(kiddedRow).queryByRole("button", { name: "Abort" }),
    ).not.toBeInTheDocument();
    expect(
      within(rowOf("FAILED")).queryByRole("button"),
    ).not.toBeInTheDocument();
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

  it("shows the no-candidates guidance when no does are breeding-ready", async () => {
    listPayload.candidate_doe_ids = [];
    const { dialog } = await openNewDialog();
    expect(
      await within(dialog).findByText(/No breeding-ready does right now/),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "Save breeding" }),
    ).not.toBeInTheDocument();
  });

  it("shows the no-bucks warning and disables Save when there are no active bucks", async () => {
    listPayload.active_buck_ids = [];
    const { dialog } = await openNewDialog();
    expect(
      await within(dialog).findByText("No active bucks on this farm — add one first."),
    ).toBeInTheDocument();
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
      target: { value: localISO(new Date(Date.now() + 86_400_000)) },
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

  // ---------- Ultrasound dialog ----------

  async function openUltrasound() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(rowOf("PENDING")).getByRole("button", { name: "Ultrasound result" }),
    );
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("describes the record and defaults to pregnant with 2 kids", async () => {
    const { dialog } = await openUltrasound();
    expect(
      within(dialog).getByText(/Doe G-010 · bred 1 Jul 2026 by G-020/),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/planned scan 2 Aug 2026/)).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox")).toBeChecked();
    expect(within(dialog).getByText("Kid count detected")).toBeInTheDocument();
  });

  it("saves a pregnant result with the default kid count of 2", async () => {
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundBody).toEqual({ pregnant: true, kid_count: 2 });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("saves the selected kid count when changed", async () => {
    const { user, dialog } = await openUltrasound();
    await pickOption(user, within(dialog).getByRole("combobox"), "3");
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundBody).toEqual({ pregnant: true, kid_count: 3 });
  });

  it("hides the kid count and posts kid_count null when not pregnant", async () => {
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(within(dialog).queryByText("Kid count detected")).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Save result" }));
    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundBody).toEqual({ pregnant: false, kid_count: null });
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

  // ---------- Abort flow ----------

  it("confirms before aborting and refetches after the POST", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    await renderLoaded();
    const pregnantRow = screen.getAllByText("CONFIRMED PREGNANT")[0].closest("tr")!;
    await user.click(within(pregnantRow).getByRole("button", { name: "Abort" }));

    expect(confirmSpy).toHaveBeenCalledWith("Mark this pregnancy as aborted?");
    await waitFor(() => expect(abortCalls).toBe(1));
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("does not abort when the confirm dialog is cancelled", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    await renderLoaded();
    const pregnantRow = screen.getAllByText("CONFIRMED PREGNANT")[0].closest("tr")!;
    await user.click(within(pregnantRow).getByRole("button", { name: "Abort" }));

    await waitFor(() => expect(abortCalls).toBe(0));
    expect(listCalls).toBe(1);
  });

  it("survives a server error on abort without crashing the list", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let failedAbortCalls = 0;
    server.use(
      http.post("/api/breeding/:recordId/abort", () => {
        failedAbortCalls += 1;
        return HttpResponse.json({ detail: "kidding already recorded" }, { status: 409 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const pregnantRow = screen.getAllByText("CONFIRMED PREGNANT")[0].closest("tr")!;
    await user.click(within(pregnantRow).getByRole("button", { name: "Abort" }));

    await waitFor(() => expect(failedAbortCalls).toBe(1));
    expect(listCalls).toBe(1); // no refresh on failure
    expect(screen.getAllByText("CONFIRMED PREGNANT")).toHaveLength(2);
  });

  // ---------- URL prefill (/breeding/{id}/ultrasound redirect) ----------

  describe("URL prefill from /breeding?ultrasound_id=…", () => {
    afterEach(() => {
      window.history.replaceState({}, "", "/breeding");
    });

    it("auto-opens the ultrasound dialog for the linked PENDING record", async () => {
      window.history.replaceState({}, "", "/breeding?ultrasound_id=1");
      renderWithProviders(<BreedingPage />);
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("Ultrasound result")).toBeInTheDocument();
      expect(
        within(dialog).getByText(/Doe G-010 · bred 1 Jul 2026 by G-020/),
      ).toBeInTheDocument();
    });

    it("opens no dialog when the linked record is not PENDING", async () => {
      window.history.replaceState({}, "", "/breeding?ultrasound_id=2");
      await renderLoaded();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
