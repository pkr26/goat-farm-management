/**
 * Breeding page — the guards the main suite only ever exercises from their
 * happy side: deep-link ids that are not canonical integers, dialogs dismissed
 * while a save is in flight, aria-invalid in the *valid* state, the window in
 * which a not-pregnant result is not observable, the pregnancy-loss chronology
 * floor and notes cap, and the table's per-row gating of loss facts, the
 * planned-scan hint and the untagged-animal fallbacks.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BreedingRecordOut } from "@/api/generated/models";
import { ALL_PERMISSIONS, permissionsHandler, server } from "@/test/msw-server";
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
  // jsdom lacks APIs the Select/Popper layer touches when opening a listbox.
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

const TODAY = farmToday();

/** Server-side hold: the response waits until the test releases it, so the
 *  in-flight window is a state the assertions can stand still in. */
function responseGate() {
  let open!: () => void;
  const held = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { held, open: () => open() };
}

function makeRecord(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    semen_sire_name: null,
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
  doe_tag: "G-002",
  ultrasound_done: true,
  pregnant: true,
  kid_count_detected: 2,
  expected_kidding_date: "2026-11-28",
  outcome: "CONFIRMED_PREGNANT",
});

/** A pending record whose service was `gapDays` ago, carrying the day-32 scan
 *  that service planned. */
function gapRecord(gapDays: number, overrides: Partial<BreedingRecordOut> = {}) {
  const bred = addDays(TODAY, -gapDays);
  return makeRecord({
    id: 300 + gapDays,
    doe_tag: `G-3${gapDays}`,
    breeding_date: bred,
    ultrasound_date: addDays(bred, 32),
    ...overrides,
  });
}

/** Open a listbox/picker and choose an option by its accessible name. */
async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

function rowOfText(text: string): HTMLElement {
  const row = screen.getByText(text).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe("BreedingPage branches", () => {
  let listCalls: number;
  let breedingPostBody: Record<string, unknown> | null;
  let ultrasoundBody: Record<string, unknown> | null;
  let ultrasoundCalls: number;
  let abortBody: Record<string, unknown> | null;
  let abortCalls: number;
  let detailCalls: number;
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
    navState.search = "";
    listCalls = 0;
    breedingPostBody = null;
    ultrasoundBody = null;
    ultrasoundCalls = 0;
    abortBody = null;
    abortCalls = 0;
    detailCalls = 0;
    listPayload = {
      records: [PENDING_REC, PREGNANT_REC],
      candidate_availability: { eligible_doe_count: 1, eligible_buck_count: 1 },
      total: 2,
      limit: 50,
      offset: 0,
    };
    server.use(
      http.get("/api/breeding", () => {
        listCalls += 1;
        return HttpResponse.json(listPayload);
      }),
      http.get("/api/breeding/candidates", ({ request }) => {
        const doe = new URL(request.url).searchParams.get("kind") === "doe";
        const candidates = doe
          ? [{ id: 10, tag_number: "G-010", name: "Lakshmi", age_months: 18, latest_weight_kg: 30 }]
          : [{ id: 20, tag_number: "G-020", name: null, age_months: 24, latest_weight_kg: null }];
        return HttpResponse.json({ candidates, total: 1, limit: 50, offset: 0 });
      }),
      http.post("/api/breeding", async ({ request }) => {
        breedingPostBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeRecord({ id: 99 }), { status: 201 });
      }),
      http.post("/api/breeding/:recordId/ultrasound", async ({ request }) => {
        ultrasoundCalls += 1;
        ultrasoundBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeRecord({ ultrasound_done: true }));
      }),
      http.post("/api/breeding/:recordId/abort", async ({ request }) => {
        abortCalls += 1;
        abortBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeRecord({ id: 2, outcome: "ABORTED" }));
      }),
    );
  });

  async function renderLoaded() {
    renderWithProviders(<BreedingPage />);
    await screen.findByText("Breeding records");
  }

  async function openNewDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add breeding" }));
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });
    return { user, dialog };
  }

  async function chooseAnimals(user: User, dialog: HTMLElement) {
    await within(dialog).findByText("Select doe");
    const [doeTrigger, buckTrigger] = within(dialog).getAllByRole("combobox");
    await pickOption(user, doeTrigger, /G-010 · Lakshmi/);
    await pickOption(user, buckTrigger, /G-020 — 24 mo/);
  }

  /** Open the ultrasound dialog from the row carrying `doeTag`. */
  async function openUltrasound(doeTag = "G-010") {
    const user = userEvent.setup();
    await renderLoaded();
    const row = rowOfText(doeTag);
    await user.click(within(row).getByRole("button", { name: "Ultrasound result" }));
    return { user, dialog: await screen.findByRole("dialog", { name: "Ultrasound result" }) };
  }

  async function openLoss(record: BreedingRecordOut = PREGNANT_REC) {
    listPayload.records = [record];
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Record loss" }));
    return { user, dialog: await screen.findByRole("dialog", { name: "Record pregnancy loss" }) };
  }

  // ---------- deep-link id canonicalisation ----------

  // Number() happily reads exponents, hex, whitespace and decimal points, so
  // the raw query string is matched against digits FIRST. Without that, four
  // spellings of "100" would each open somebody else's record.
  it.each(["1e2", "0x64", "100.0", "+100"])(
    "ignores the non-canonical deep-link id %s even when record 100 is on the page",
    async (rawId) => {
      listPayload.records = [PENDING_REC, makeRecord({ id: 100, doe_tag: "G-100" })];
      server.use(
        http.get("/api/breeding/100", () => {
          detailCalls += 1;
          return HttpResponse.json(makeRecord({ id: 100 }));
        }),
      );
      navState.search = `?ultrasound_id=${rawId}`;

      await renderLoaded();

      expect(screen.getByText("G-100")).toBeInTheDocument();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(detailCalls).toBe(0);
    },
  );

  // ---------- permissions gate ----------

  it("shows the loading placeholder, not an access denial, while permissions resolve", async () => {
    const gate = responseGate();
    server.use(
      http.get("/api/auth/permissions", async () => {
        await gate.held;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );
    renderWithProviders(<BreedingPage />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();

    gate.open();
    expect(await screen.findByText("Breeding")).toBeInTheDocument();
  });

  // ---------- add-breeding dialog ----------

  it("refuses to offer the form when candidate availability could not be read", async () => {
    listPayload.candidate_availability = null;
    const { dialog } = await openNewDialog();

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Candidate availability is unavailable. Refresh the breeding records before adding a breeding.",
    );
    expect(within(dialog).queryByRole("combobox")).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "Save breeding" }),
    ).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/No breeding-ready does right now/)).not.toBeInTheDocument();
  });

  it("marks the add-breeding fields invalid only once they are rejected", async () => {
    const { user, dialog } = await openNewDialog();
    await within(dialog).findByText("Select doe");
    const doe = within(dialog).getByRole("combobox", { name: "Doe *" });
    const buck = within(dialog).getByRole("combobox", { name: "Buck *" });
    const date = within(dialog).getByLabelText(/breeding date/i);

    expect(doe).not.toHaveAttribute("aria-invalid");
    expect(buck).not.toHaveAttribute("aria-invalid");
    expect(date).not.toHaveAttribute("aria-invalid");

    await user.clear(date);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));
    expect(await within(dialog).findByText("Select a doe")).toBeInTheDocument();

    expect(doe).toHaveAttribute("aria-invalid", "true");
    expect(buck).toHaveAttribute("aria-invalid", "true");
    expect(date).toHaveAttribute("aria-invalid", "true");
    expect(breedingPostBody).toBeNull();
  });

  it("still shows the chosen animals after the dialog is dismissed and reopened", async () => {
    const { user, dialog } = await openNewDialog();
    await chooseAnimals(user, dialog);

    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Add breeding" })).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Add breeding" }));

    const reopened = await screen.findByRole("dialog", { name: "Add breeding" });
    const [doeTrigger, buckTrigger] = within(reopened).getAllByRole("combobox");
    // The picker's own memory died with the closed dialog; the ids RHF kept
    // must still read as animals, not as "Selected item 10".
    expect(doeTrigger).toHaveTextContent("G-010 · Lakshmi — 18 mo, 30.0 kg");
    expect(buckTrigger).toHaveTextContent("G-020 — 24 mo");
  });

  it("holds the add-breeding dialog open and locked while the save is in flight", async () => {
    const gate = responseGate();
    server.use(
      http.post("/api/breeding", async ({ request }) => {
        breedingPostBody = (await request.json()) as Record<string, unknown>;
        await gate.held;
        return HttpResponse.json(makeRecord({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openNewDialog();
    await chooseAnimals(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    const saving = await within(dialog).findByRole("button", { name: "Saving…" });
    // The button carries the disabled state itself, not only via the fieldset.
    expect(saving).toHaveAttribute("disabled");
    expect(within(dialog).getByLabelText(/breeding date/i)).toBeDisabled();

    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Add breeding" })).toBeInTheDocument();

    gate.open();
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Add breeding" })).not.toBeInTheDocument(),
    );
  });

  it("drops a failed save's message when the dialog is dismissed and reopened", async () => {
    server.use(
      http.post("/api/breeding", () =>
        HttpResponse.json({ detail: "Doe is already pregnant" }, { status: 422 }),
      ),
    );
    const { user, dialog } = await openNewDialog();
    await chooseAnimals(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Doe is already pregnant");

    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Add breeding" })).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Add breeding" }));

    const reopened = await screen.findByRole("dialog", { name: "Add breeding" });
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(reopened).getByRole("button", { name: "Save breeding" })).toBeInTheDocument();
  });

  // ---------- ultrasound: the not-observable window ----------

  it.each([2, 5, 17])(
    "refuses a not-pregnant result %i days after the service",
    async (gapDays) => {
      listPayload.records = [gapRecord(gapDays)];
      const { dialog } = await openUltrasound(`G-3${gapDays}`);

      expect(within(dialog).getByRole("alert")).toHaveTextContent(
        `A not-pregnant result ${gapDays} days after service is not observable — record it on the service day or the day after, or from day 18 (return to heat).`,
      );
      expect(within(dialog).getByLabelText("Result date *")).toHaveAttribute(
        "aria-invalid",
        "true",
      );
      expect(within(dialog).getByRole("button", { name: "Save result" })).toBeDisabled();
      expect(ultrasoundBody).toBeNull();
    },
  );

  it.each([0, 1, 18, 40])(
    "records a not-pregnant result %i days after the service",
    async (gapDays) => {
      listPayload.records = [gapRecord(gapDays)];
      const { user, dialog } = await openUltrasound(`G-3${gapDays}`);

      expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
      expect(within(dialog).getByLabelText("Result date *")).not.toHaveAttribute("aria-invalid");
      await user.click(within(dialog).getByRole("button", { name: "Save result" }));

      await waitFor(() => expect(ultrasoundBody).not.toBeNull());
      expect(ultrasoundBody).toEqual({ pregnant: false, date: TODAY, kid_count: null });
    },
  );

  it("never applies the return-to-heat window to a pregnant result", async () => {
    // Bred five days ago with the scan brought forward to today: the window
    // bounds an unobservable FAILURE, so a positive result is unaffected by it.
    listPayload.records = [
      makeRecord({ id: 210, doe_tag: "G-210", breeding_date: addDays(TODAY, -5), ultrasound_date: TODAY }),
    ];
    const { user, dialog } = await openUltrasound("G-210");
    await user.click(within(dialog).getByRole("checkbox"));

    // A result on (not after) the planned scan date is in time.
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundBody).toEqual({ pregnant: true, date: TODAY, kid_count: 2 });
  });

  it("rejects an ultrasound result dated in the future", async () => {
    listPayload.records = [gapRecord(40)];
    const { dialog } = await openUltrasound("G-340");
    fireEvent.change(within(dialog).getByLabelText("Result date *"), {
      target: { value: addDays(TODAY, 1) },
    });

    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Result date can't be in the future",
    );
    expect(within(dialog).getByRole("button", { name: "Save result" })).toBeDisabled();
    expect(ultrasoundBody).toBeNull();
  });

  it("single-flights a double-click on Save result", async () => {
    const { user, dialog } = await openUltrasound();

    await user.dblClick(within(dialog).getByRole("button", { name: "Save result" }));

    await waitFor(() => expect(ultrasoundCalls).toBe(1));
    expect(ultrasoundCalls).toBe(1);
  });

  it("ignores a second Save result that lands before the first one renders", async () => {
    const { dialog } = await openUltrasound();
    const save = within(dialog).getByRole("button", { name: "Save result" });

    // Both clicks are delivered in one commit, so the second still sees an
    // enabled button — the same-render gap the save lock exists to close.
    await act(async () => {
      fireEvent.click(save);
      fireEvent.click(save);
    });

    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundCalls).toBe(1);
  });

  it("holds the ultrasound dialog open and locked while the result is in flight", async () => {
    const gate = responseGate();
    server.use(
      http.post("/api/breeding/:recordId/ultrasound", async ({ request }) => {
        ultrasoundCalls += 1;
        ultrasoundBody = (await request.json()) as Record<string, unknown>;
        await gate.held;
        return HttpResponse.json(makeRecord({ ultrasound_done: true }));
      }),
    );
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    expect(await within(dialog).findByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(within(dialog).getByLabelText("Result date *")).toBeDisabled();
    expect(within(dialog).getByRole("checkbox")).toHaveAttribute("aria-disabled", "true");

    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Ultrasound result" })).toBeInTheDocument();

    gate.open();
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Ultrasound result" })).not.toBeInTheDocument(),
    );
  });

  // ---------- pregnancy loss ----------

  it("keeps the service date as the loss floor when the stored scan date precedes it", async () => {
    // The floor is the later of the two dates: a scan date that predates the
    // service it belongs to must never open the record up to earlier losses.
    const { dialog } = await openLoss(
      makeRecord({ ...PREGNANT_REC, ultrasound_result_date: "2026-06-25" }),
    );
    const lossDate = within(dialog).getByLabelText("Loss date *");

    expect(lossDate).toHaveAttribute("min", "2026-07-01");
    fireEvent.change(lossDate, { target: { value: "2026-06-30" } });

    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Loss date cannot be before 1 Jul 2026",
    );
    expect(
      within(dialog).getByRole("button", { name: "Record pregnancy loss" }),
    ).toBeDisabled();
  });

  it("rejects a loss date in the future", async () => {
    const { dialog } = await openLoss();
    const lossDate = within(dialog).getByLabelText("Loss date *");
    fireEvent.change(lossDate, { target: { value: addDays(TODAY, 1) } });

    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Loss date can't be in the future",
    );
    expect(lossDate).toHaveAttribute("aria-invalid", "true");
    expect(
      within(dialog).getByRole("button", { name: "Record pregnancy loss" }),
    ).toBeDisabled();
    expect(abortCalls).toBe(0);
  });

  it("flags only the loss field that is actually rejected", async () => {
    const { dialog } = await openLoss();
    const lossDate = within(dialog).getByLabelText("Loss date *");
    const notes = within(dialog).getByLabelText("Notes");

    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(lossDate).not.toHaveAttribute("aria-invalid");
    expect(notes).not.toHaveAttribute("aria-invalid");

    fireEvent.change(lossDate, { target: { value: "" } });

    expect(within(dialog).getByRole("alert")).toHaveTextContent("Loss date is required");
    expect(lossDate).toHaveAttribute("aria-invalid", "true");
    expect(notes).not.toHaveAttribute("aria-invalid");
  });

  it("caps the loss notes at 4000 characters and accepts exactly 4000", async () => {
    const { user, dialog } = await openLoss();
    const notes = within(dialog).getByLabelText("Notes");
    // maxLength only caps typing; a paste or autofill still has to be caught.
    fireEvent.change(notes, { target: { value: "n".repeat(4_001) } });

    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Notes cannot exceed 4000 characters",
    );
    expect(notes).toHaveAttribute("aria-invalid", "true");
    expect(
      within(dialog).getByRole("button", { name: "Record pregnancy loss" }),
    ).toBeDisabled();

    fireEvent.change(notes, { target: { value: "n".repeat(4_000) } });

    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(notes).not.toHaveAttribute("aria-invalid");
    await user.click(within(dialog).getByRole("button", { name: "Record pregnancy loss" }));

    await waitFor(() => expect(abortCalls).toBe(1));
    expect(String(abortBody?.notes)).toHaveLength(4_000);
  });

  it("ignores a submit event that arrives while the facts are invalid", async () => {
    const { user, dialog } = await openLoss();
    const lossDate = within(dialog).getByLabelText("Loss date *");
    fireEvent.change(lossDate, { target: { value: "" } });

    // A submit event can reach the form without the disabled button (implicit
    // submission, autofill); the handler has to refuse it on its own.
    fireEvent.submit(within(dialog).getByLabelText("Notes").closest("form") as HTMLFormElement);

    fireEvent.change(lossDate, { target: { value: TODAY } });
    await user.click(within(dialog).getByRole("button", { name: "Record pregnancy loss" }));

    await waitFor(() => expect(abortCalls).toBe(1));
    expect(abortBody).toEqual({ loss_date: TODAY, cause: "UNKNOWN", notes: null });
  });

  it("holds the loss dialog open and locked while the record is in flight", async () => {
    const gate = responseGate();
    server.use(
      http.post("/api/breeding/:recordId/abort", async ({ request }) => {
        abortCalls += 1;
        abortBody = (await request.json()) as Record<string, unknown>;
        await gate.held;
        return HttpResponse.json(makeRecord({ id: 2, outcome: "ABORTED" }));
      }),
    );
    const { user, dialog } = await openLoss();
    await user.click(within(dialog).getByRole("button", { name: "Record pregnancy loss" }));

    expect(await within(dialog).findByRole("button", { name: "Recording…" })).toBeDisabled();
    expect(within(dialog).getByLabelText("Notes")).toBeDisabled();

    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Record pregnancy loss" })).toBeInTheDocument();

    gate.open();
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Record pregnancy loss" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("names an untagged doe by id in the loss dialog", async () => {
    const { dialog } = await openLoss(
      makeRecord({ ...PREGNANT_REC, doe_id: 77, doe_tag: null }),
    );

    expect(within(dialog).getByText(/Doe #77 · bred 1 Jul 2026\./)).toBeInTheDocument();
  });

  // ---------- records table ----------

  it("gives the manager's action column a header", async () => {
    await renderLoaded();

    expect(screen.getByRole("columnheader", { name: "Actions" })).toBeInTheDocument();
  });

  it("falls back to plain 'Doe #id' / 'Buck #id' text without animals.view", async () => {
    server.use(permissionsHandler(["breeding.view", "breeding.manage"]));
    listPayload.records = [
      makeRecord({ id: 9, doe_id: 77, buck_id: 88, doe_tag: null, buck_tag: null }),
    ];
    await renderLoaded();
    const row = rowOfText("Doe #77");

    expect(within(row).getByText("Buck #88")).toBeInTheDocument();
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows the loss detail line only for an aborted record that has a loss date", async () => {
    listPayload.records = [
      makeRecord({
        id: 5,
        doe_tag: "G-005",
        outcome: "ABORTED",
        loss_date: "2026-08-04",
        loss_cause: "INJURY",
        loss_notes: "Fence accident",
      }),
      makeRecord({ id: 6, doe_tag: "G-006", outcome: "ABORTED", loss_date: null }),
      makeRecord({
        id: 7,
        doe_tag: "G-007",
        outcome: "FAILED",
        ultrasound_done: true,
        pregnant: false,
        loss_date: "2026-08-04",
        loss_cause: "INJURY",
      }),
      PENDING_REC,
    ];
    await renderLoaded();

    expect(within(rowOfText("G-005")).getByText("4 Aug 2026 · INJURY")).toBeInTheDocument();
    expect(within(rowOfText("G-006")).queryByText(/UNKNOWN/)).not.toBeInTheDocument();
    expect(within(rowOfText("G-007")).queryByText(/INJURY/)).not.toBeInTheDocument();
    expect(within(rowOfText("G-010")).queryByText(/UNKNOWN/)).not.toBeInTheDocument();
  });

  it("hides the loss-notes disclosure when no notes were recorded", async () => {
    listPayload.records = [
      makeRecord({
        id: 5,
        doe_tag: "G-005",
        outcome: "ABORTED",
        loss_date: "2026-08-04",
        loss_cause: "INJURY",
        loss_notes: "Fence accident",
      }),
      makeRecord({
        id: 6,
        doe_tag: "G-006",
        outcome: "ABORTED",
        loss_date: "2026-08-04",
        loss_cause: "INJURY",
      }),
    ];
    await renderLoaded();

    const withNotes = rowOfText("G-005");
    expect(within(withNotes).getByText("Loss notes")).toBeInTheDocument();
    expect(within(withNotes).getByText("Fence accident")).toBeInTheDocument();
    expect(within(rowOfText("G-006")).queryByText("Loss notes")).not.toBeInTheDocument();
  });

  it("advertises a planned scan only while it is still ahead of a pending record", async () => {
    const ahead = addDays(TODAY, 11);
    listPayload.records = [
      makeRecord({ id: 11, doe_tag: "G-011", breeding_date: addDays(TODAY, -21), ultrasound_date: ahead }),
      makeRecord({
        id: 12,
        doe_tag: "G-012",
        outcome: "FAILED",
        ultrasound_done: true,
        pregnant: false,
        breeding_date: addDays(TODAY, -21),
        ultrasound_date: ahead,
      }),
      makeRecord({ id: 13, doe_tag: "G-013", breeding_date: addDays(TODAY, -32), ultrasound_date: TODAY }),
    ];
    await renderLoaded();

    expect(
      within(rowOfText("G-011")).getByText(`Scan planned ${formatDate(ahead)}`),
    ).toBeInTheDocument();
    // Settled records have no scan to keep; a scan due today is not "planned".
    expect(within(rowOfText("G-012")).queryByText(/^Scan planned/)).not.toBeInTheDocument();
    expect(within(rowOfText("G-013")).queryByText(/^Scan planned/)).not.toBeInTheDocument();
  });

  // ---------- deep-linked record fetching ----------

  it("does not fetch the linked record while the list itself is failing", async () => {
    server.use(
      http.get("/api/breeding", () => {
        listCalls += 1;
        return HttpResponse.json({ detail: "breeding unavailable" }, { status: 503 });
      }),
      http.get("/api/breeding/99", () => {
        detailCalls += 1;
        return HttpResponse.json(makeRecord({ id: 99 }));
      }),
    );
    navState.search = "?ultrasound_id=99";
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);

    expect(await screen.findByText("breeding unavailable")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry breeding records" }));
    await waitFor(() => expect(listCalls).toBe(2));

    expect(detailCalls).toBe(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not refetch a linked record that is already on the current page", async () => {
    server.use(
      http.get("/api/breeding/1", () => {
        detailCalls += 1;
        return HttpResponse.json(PENDING_REC);
      }),
    );
    navState.search = "?ultrasound_id=1";
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);

    const dialog = await screen.findByRole("dialog", { name: "Ultrasound result" });
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));
    await waitFor(() => expect(ultrasoundBody).not.toBeNull());

    expect(detailCalls).toBe(0);
  });

  it("keeps a still-loading deep link alive when another row's dialog is dismissed", async () => {
    const gate = responseGate();
    server.use(
      http.get("/api/breeding/99", async () => {
        detailCalls += 1;
        await gate.held;
        return HttpResponse.json(
          makeRecord({ id: 99, doe_tag: "G-099", breeding_date: "2026-06-01", ultrasound_date: "2026-07-03" }),
        );
      }),
    );
    navState.search = "?ultrasound_id=99";
    const user = userEvent.setup();
    await renderLoaded();

    // The operator works a row of their own while the linked record loads.
    await user.click(
      within(rowOfText("G-010")).getByRole("button", { name: "Ultrasound result" }),
    );
    const ownDialog = await screen.findByRole("dialog", { name: "Ultrasound result" });
    expect(within(ownDialog).getByText(/Doe G-010 · bred 1 Jul 2026/)).toBeInTheDocument();
    await user.click(within(ownDialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    gate.open();

    // Dismissing their row must not silently retire the task they arrived from.
    const linked = await screen.findByRole("dialog", { name: "Ultrasound result" });
    expect(within(linked).getByText(/Doe G-099 · bred 1 Jun 2026/)).toBeInTheDocument();
  });
});
