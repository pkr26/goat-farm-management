/**
 * Breeding page — a second pass over the copy the page commits to and the
 * bookkeeping behind it: the outcome tints and recorded loss facts of each
 * row, the accessible wiring of every dialog message, the in-flight and retry
 * labels plus the toast each write announces, the chronology guidance for a
 * not-pregnant result nobody could have observed, and the page query itself.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BreedingRecordOut } from "@/api/generated/models";
import { ALL_PERMISSIONS, permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import BreedingPage from "./page";

const { navState } = vi.hoisted(() => ({ navState: { search: "" } }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/breeding",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

type User = ReturnType<typeof userEvent.setup>;

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI Select touches.
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

const DOE_CANDIDATE = {
  id: 10,
  tag_number: "G-010",
  name: "Lakshmi",
  age_months: 18,
  latest_weight_kg: 30,
};
const BUCK_CANDIDATE = {
  id: 20,
  tag_number: "G-020",
  name: null,
  age_months: 24,
  latest_weight_kg: null,
};

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
  ultrasound_done: true,
  pregnant: true,
  kid_count_detected: 2,
  expected_kidding_date: "2026-11-28",
  outcome: "CONFIRMED_PREGNANT",
});
const FAILED_REC = makeRecord({ id: 4, ultrasound_done: true, pregnant: false, outcome: "FAILED" });
const ABORTED_REC = makeRecord({
  id: 5,
  ultrasound_done: true,
  pregnant: false,
  outcome: "ABORTED",
  loss_date: "2026-08-04",
  loss_cause: "INJURY",
  loss_notes: "Fence accident",
  loss_recorded_by_id: 7,
  loss_recorded_at: "2026-08-04T12:00:00Z",
});
/** Bred 20 days ago: the return to heat has arrived, so today is a day on
 *  which a failed service IS observable — day 2..17 is not. */
const HEAT_RETURN_REC = makeRecord({
  id: 80,
  breeding_date: addDays(TODAY, -20),
  ultrasound_date: addDays(TODAY, 12),
});

/** Open a Base UI select and pick an option by its accessible name. */
async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

/** A response the test releases by hand, to observe in-flight copy. */
function createGate() {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { gate, release };
}

describe("BreedingPage copy and write bookkeeping", () => {
  let listUrls: string[];
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
    navState.search = "";
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    listUrls = [];
    breedingPostBody = null;
    ultrasoundBody = null;
    abortCalls = 0;
    abortBody = null;
    listPayload = {
      records: [PENDING_REC, PREGNANT_REC, FAILED_REC, ABORTED_REC],
      candidate_availability: { eligible_doe_count: 1, eligible_buck_count: 1 },
      total: 4,
      limit: 50,
      offset: 0,
    };
    server.use(
      http.get("/api/breeding", ({ request }) => {
        listUrls.push(request.url);
        return HttpResponse.json(listPayload);
      }),
      http.get("/api/breeding/candidates", ({ request }) => {
        const url = new URL(request.url);
        const candidates =
          url.searchParams.get("kind") === "doe" ? [DOE_CANDIDATE] : [BUCK_CANDIDATE];
        return HttpResponse.json({ candidates, total: candidates.length, limit: 50, offset: 0 });
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
        return HttpResponse.json(makeRecord({ id: 2, outcome: "ABORTED", loss_date: TODAY }));
      }),
    );
  });

  async function renderLoaded() {
    const view = renderWithProviders(<BreedingPage />);
    await screen.findAllByText("1 Jul 2026");
    return view;
  }

  // ---------- what the table itself states ----------

  it("tints every outcome badge with the colour of that outcome", async () => {
    await renderLoaded();

    expect(screen.getByText("CONFIRMED PREGNANT")).toHaveClass(
      "bg-emerald-100",
      "text-emerald-800",
      "dark:bg-emerald-950",
      "dark:text-emerald-300",
    );
    expect(screen.getByText("FAILED")).toHaveClass(
      "bg-red-100",
      "text-red-800",
      "dark:bg-red-950",
      "dark:text-red-300",
    );
    expect(screen.getByText("ABORTED")).toHaveClass(
      "bg-zinc-200",
      "text-zinc-700",
      "dark:bg-zinc-800",
      "dark:text-zinc-300",
    );
  });

  it("dashes the ultrasound cell of a record that has no planned scan", async () => {
    listPayload.records = [makeRecord({ id: 11, ultrasound_date: null })];
    renderWithProviders(<BreedingPage />);

    const row = (await screen.findByText("PENDING")).closest("tr") as HTMLElement;
    // Bred | Doe | Buck | Cycle | Ultrasound | Kids | Expected | Outcome | …
    expect(within(row).getAllByRole("cell")[4].textContent).toBe("—");
  });

  it("spells out the recorded loss cause, including the herd-exit close", async () => {
    listPayload.records = [
      makeRecord({
        id: 12,
        ultrasound_done: true,
        pregnant: false,
        outcome: "ABORTED",
        loss_date: "2026-08-04",
        // Written by the animals module when a pregnant doe leaves the herd.
        loss_cause: "ANIMAL_STATUS_CHANGE",
      }),
      makeRecord({
        id: 13,
        breeding_date: "2026-07-02",
        ultrasound_done: true,
        pregnant: false,
        outcome: "ABORTED",
        loss_date: "2026-08-05",
        loss_cause: null,
      }),
    ];
    renderWithProviders(<BreedingPage />);

    expect(await screen.findByText("4 Aug 2026 · ANIMAL STATUS CHANGE")).toBeInTheDocument();
    // A cause-less row still names a cause rather than trailing a bare dot.
    expect(screen.getByText("5 Aug 2026 · UNKNOWN")).toBeInTheDocument();
  });

  it("names an untagged doe and buck in plain text without animals.view", async () => {
    server.use(permissionsHandler(["breeding.view", "breeding.manage"]));
    listPayload.records = [
      makeRecord({ id: 14, doe_id: 77, buck_id: 88, doe_tag: null, buck_tag: null }),
    ];
    renderWithProviders(<BreedingPage />);

    expect(await screen.findByText("Doe #77")).toBeInTheDocument();
    expect(screen.getByText("Buck #88")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Doe #77" })).not.toBeInTheDocument();
  });

  it("asks for the first page of records with the page size the pager reports", async () => {
    await renderLoaded();

    const params = new URL(listUrls[0]).searchParams;
    expect(params.get("limit")).toBe("50");
    expect(params.get("offset")).toBe("0");
  });

  it("waits for the permission answer instead of announcing no access", async () => {
    const { gate, release } = createGate();
    server.use(
      http.get("/api/auth/permissions", async () => {
        await gate;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );
    const view = renderWithProviders(<BreedingPage />);
    await view.waitForAuthIdle();

    await waitFor(() => expect(screen.getByText("Loading…")).toBeInTheDocument());
    expect(screen.queryByText("You don't have access to this page.")).not.toBeInTheDocument();

    release();
    expect(await screen.findAllByText("1 Jul 2026")).not.toHaveLength(0);
  });

  it("reports a transport failure of the list, not just a server detail", async () => {
    server.use(http.get("/api/breeding", () => HttpResponse.error()));
    renderWithProviders(<BreedingPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load breeding records.");
    expect(screen.getByRole("button", { name: "Retry breeding records" })).toBeInTheDocument();
  });

  it("reports a transport failure of a deep-linked record", async () => {
    navState.search = "?ultrasound_id=99";
    server.use(http.get("/api/breeding/99", () => HttpResponse.error()));
    renderWithProviders(<BreedingPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the linked ultrasound record.",
    );
    expect(screen.getByRole("button", { name: "Retry ultrasound record" })).toBeInTheDocument();
  });

  // ---------- Add breeding ----------

  async function openNewDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add breeding" }));
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });
    return { user, dialog };
  }

  async function chooseDoeAndBuck(user: User, dialog: HTMLElement) {
    await within(dialog).findByText("Select doe"); // animals loaded
    const [doeTrigger, buckTrigger] = within(dialog).getAllByRole("combobox");
    await pickOption(user, doeTrigger, /G-010 · Lakshmi/);
    await pickOption(user, buckTrigger, /G-020 — 24 mo/);
  }

  it("points every add-breeding message at the control it belongs to", async () => {
    const { user, dialog } = await openNewDialog();
    await within(dialog).findByText("Select doe");
    await user.clear(within(dialog).getByLabelText(/breeding date/i));
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    expect(await within(dialog).findByText("Pick a valid date")).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "Doe *" })).toHaveAccessibleDescription(
      /Select a doe/,
    );
    expect(within(dialog).getByRole("combobox", { name: "Buck *" })).toHaveAccessibleDescription(
      /Select a buck/,
    );
    expect(within(dialog).getByLabelText(/breeding date/i)).toHaveAccessibleDescription(
      "Pick a valid date",
    );
    expect(breedingPostBody).toBeNull();
  });

  // A date field happily holds a five-digit year ("12026-07-01" is a valid
  // date string), and that year sorts BELOW today as a string, so only the
  // anchored shape check stops it from being posted as a breeding date.
  it("rejects a five-digit year the date field still accepts", async () => {
    const { user, dialog } = await openNewDialog();
    await chooseDoeAndBuck(user, dialog);
    const dateInput = within(dialog).getByLabelText(/breeding date/i);
    fireEvent.change(dateInput, { target: { value: "12026-07-01" } });
    expect(dateInput).toHaveValue("12026-07-01");

    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    expect(await within(dialog).findByText("Pick a valid date")).toBeInTheDocument();
    expect(breedingPostBody).toBeNull();
  });

  it("clears the failed-save message while the retry is in flight", async () => {
    const { gate, release } = createGate();
    let calls = 0;
    server.use(
      http.post("/api/breeding", async () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json({ detail: "Doe eligibility changed" }, { status: 409 });
        }
        await gate;
        return HttpResponse.json(makeRecord({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openNewDialog();
    await chooseDoeAndBuck(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Doe eligibility changed");

    await user.click(within(dialog).getByRole("button", { name: "Retry save breeding" }));

    // The stale failure must not sit over an attempt that is still running.
    await waitFor(() => expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument());
    expect(within(dialog).getByRole("button", { name: "Saving…" })).toBeDisabled();

    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(toast.success).toHaveBeenCalledWith("Breeding saved.");
  });

  it("forgets a failed save once the dialog is dismissed and reopened", async () => {
    server.use(
      http.post("/api/breeding", () =>
        HttpResponse.json({ detail: "Doe is already pregnant" }, { status: 422 }),
      ),
    );
    const { user, dialog } = await openNewDialog();
    await chooseDoeAndBuck(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Doe is already pregnant");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Add breeding" }));

    const reopened = await screen.findByRole("dialog", { name: "Add breeding" });
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(reopened).getByRole("button", { name: "Save breeding" })).toBeInTheDocument();
  });

  it("starts the next breeding from an empty form after a saved one", async () => {
    const yesterday = addDays(TODAY, -1);
    const { user, dialog } = await openNewDialog();
    await chooseDoeAndBuck(user, dialog);
    fireEvent.change(within(dialog).getByLabelText(/breeding date/i), {
      target: { value: yesterday },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    await waitFor(() =>
      expect(breedingPostBody).toEqual({ doe_id: 10, buck_id: 20, breeding_date: yesterday }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Add breeding" }));

    const reopened = await screen.findByRole("dialog", { name: "Add breeding" });
    expect(within(reopened).getByLabelText(/breeding date/i)).toHaveValue(TODAY);
    expect(within(reopened).getByRole("combobox", { name: "Doe *" })).toHaveTextContent(
      "Select doe",
    );
    expect(within(reopened).getByRole("combobox", { name: "Buck *" })).toHaveTextContent(
      "Select buck",
    );
  });

  it("falls back to a generic message when the save never reaches the server", async () => {
    server.use(http.post("/api/breeding", () => HttpResponse.error()));
    const { user, dialog } = await openNewDialog();
    await chooseDoeAndBuck(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Something went wrong");
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Something went wrong"));
    expect(
      within(dialog).getByRole("button", { name: "Retry save breeding" }),
    ).toBeInTheDocument();
  });

  it("refuses to add a breeding while candidate availability is unknown", async () => {
    listPayload.candidate_availability = null;
    const { dialog } = await openNewDialog();

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Candidate availability is unavailable.",
    );
    expect(
      within(dialog).queryByRole("button", { name: "Save breeding" }),
    ).not.toBeInTheDocument();
  });

  // ---------- Ultrasound result ----------

  async function openUltrasound(record: BreedingRecordOut = PENDING_REC) {
    const user = userEvent.setup();
    listPayload.records = [record];
    renderWithProviders(<BreedingPage />);
    const row = (await screen.findByText("PENDING")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Ultrasound result" }));
    return { user, dialog: await screen.findByRole("dialog", { name: "Ultrasound result" }) };
  }

  // The server rejects the window between standing heat and the return to
  // heat with a 409: nothing could have been seen there. The dialog has to
  // name the day it counted, and reopen on both observable edges.
  it("explains the days after service on which a failure is not observable", async () => {
    const { dialog } = await openUltrasound(HEAT_RETURN_REC);
    const resultDate = within(dialog).getByLabelText("Result date *");
    const save = within(dialog).getByRole("button", { name: "Save result" });
    const day = (offset: number) => addDays(HEAT_RETURN_REC.breeding_date, offset);

    // Today is day 20 — the doe is back in heat, so the failure is visible.
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(save).toBeEnabled();

    fireEvent.change(resultDate, { target: { value: day(17) } });
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "A not-pregnant result 17 days after service is not observable — record it on the " +
        "service day or the day after, or from day 18 (return to heat).",
    );
    expect(save).toBeDisabled();

    fireEvent.change(resultDate, { target: { value: day(2) } });
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "A not-pregnant result 2 days after service is not observable",
    );

    // Both edges of the gap are recordable: the watched service and the
    // earliest return to heat.
    fireEvent.change(resultDate, { target: { value: day(1) } });
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.change(resultDate, { target: { value: day(18) } });
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(save).toBeEnabled();
  });

  it("requires a result date, refuses a future one, and labels the field", async () => {
    const { dialog } = await openUltrasound();
    const resultDate = within(dialog).getByLabelText("Result date *");
    const save = within(dialog).getByRole("button", { name: "Save result" });

    fireEvent.change(resultDate, { target: { value: "" } });
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Result date is required");
    expect(resultDate).toHaveAttribute("aria-describedby", "ultrasound-result-date-1-error");
    expect(resultDate).toHaveAccessibleDescription("Result date is required");
    expect(resultDate).toHaveAttribute("aria-invalid", "true");
    expect(save).toBeDisabled();

    fireEvent.change(resultDate, { target: { value: addDays(TODAY, 1) } });
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Result date can't be in the future",
    );
    expect(save).toBeDisabled();
    expect(ultrasoundBody).toBeNull();
  });

  it("identifies an untagged record without inventing a planned scan", async () => {
    const { dialog } = await openUltrasound(
      makeRecord({ id: 15, doe_tag: null, buck_tag: null, ultrasound_date: null }),
    );

    expect(within(dialog).getByText("Doe #10 · bred 1 Jul 2026 by #20")).toBeInTheDocument();
  });

  it("offers each detectable kid count and saves a singleton", async () => {
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("checkbox"));

    const kidCount = within(dialog).getByLabelText("Kid count detected");
    expect(kidCount).toHaveTextContent("2");
    await user.click(kidCount);
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual([
      "1",
      "2",
      "3",
    ]);

    await user.click(screen.getByRole("option", { name: "1" }));
    expect(within(dialog).queryByText("Select the detected kid count")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    await waitFor(() =>
      expect(ultrasoundBody).toEqual({ pregnant: true, date: TODAY, kid_count: 1 }),
    );
  });

  it("clears the failed result message while the retry is in flight", async () => {
    const { gate, release } = createGate();
    let calls = 0;
    server.use(
      http.post("/api/breeding/:recordId/ultrasound", async () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json({ detail: "scanner result conflict" }, { status: 409 });
        }
        await gate;
        return HttpResponse.json(makeRecord({ id: 1, ultrasound_done: true }));
      }),
    );
    const { user, dialog } = await openUltrasound();
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("scanner result conflict");
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("scanner result conflict"));

    await user.click(within(dialog).getByRole("button", { name: "Retry save result" }));
    await waitFor(() => expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument());
    expect(within(dialog).getByRole("button", { name: "Saving…" })).toBeDisabled();

    release();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Ultrasound result saved."));
  });

  // ---------- Pregnancy loss ----------

  async function openPregnancyLoss(record: BreedingRecordOut = PREGNANT_REC) {
    const user = userEvent.setup();
    listPayload.records = [record];
    renderWithProviders(<BreedingPage />);
    const row = (await screen.findByText("CONFIRMED PREGNANT")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Record loss" }));
    return { user, dialog: await screen.findByRole("dialog", { name: "Record pregnancy loss" }) };
  }

  it("requires a loss date, refuses a future one, and labels the field", async () => {
    const { dialog } = await openPregnancyLoss();
    const lossDate = within(dialog).getByLabelText("Loss date *");
    const record = within(dialog).getByRole("button", { name: "Record pregnancy loss" });

    fireEvent.change(lossDate, { target: { value: "" } });
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Loss date is required");
    expect(lossDate).toHaveAttribute("aria-describedby", "pregnancy-loss-date-error-2");
    expect(lossDate).toHaveAccessibleDescription("Loss date is required");
    expect(record).toBeDisabled();

    fireEvent.change(lossDate, { target: { value: addDays(TODAY, 1) } });
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Loss date can't be in the future");
    expect(record).toBeDisabled();
    expect(abortCalls).toBe(0);
  });

  it("caps the loss notes and points the message at the textarea", async () => {
    const { dialog } = await openPregnancyLoss();
    const notes = within(dialog).getByLabelText("Notes");
    const record = within(dialog).getByRole("button", { name: "Record pregnancy loss" });

    fireEvent.change(notes, { target: { value: "x".repeat(4_001) } });
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Notes cannot exceed 4000 characters",
    );
    expect(notes).toHaveAttribute("aria-describedby", "pregnancy-loss-notes-error-2");
    expect(notes).toHaveAccessibleDescription("Notes cannot exceed 4000 characters");
    expect(notes).toHaveAttribute("aria-invalid", "true");
    expect(record).toBeDisabled();

    fireEvent.change(notes, { target: { value: "x".repeat(4_000) } });
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(record).toBeEnabled();
  });

  it("offers every recordable loss cause and shows the chosen one", async () => {
    const { user, dialog } = await openPregnancyLoss();
    const cause = within(dialog).getByLabelText("Cause *");

    expect(cause).toHaveTextContent("UNKNOWN");
    await user.click(cause);
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual([
      "UNKNOWN",
      "DISEASE",
      "INJURY",
      "NUTRITIONAL",
      "TRAUMA",
      "OTHER",
    ]);

    // The closed trigger has to name the cause, not fall back to a blank.
    await user.click(screen.getByRole("option", { name: "DISEASE" }));
    expect(cause).toHaveTextContent("DISEASE");
  });

  it("names the doe of the pregnancy being closed even without a tag", async () => {
    const { dialog } = await openPregnancyLoss(
      makeRecord({
        id: 2,
        ultrasound_done: true,
        pregnant: true,
        outcome: "CONFIRMED_PREGNANT",
        doe_tag: null,
      }),
    );

    expect(
      within(dialog).getByText(/^Doe #10 · bred 1 Jul 2026\. This closes the pregnancy/),
    ).toBeInTheDocument();
  });

  it("records trimmed notes, closes the dialog and announces the loss", async () => {
    const submissions: Event[] = [];
    const capture = (event: Event) => submissions.push(event);
    document.addEventListener("submit", capture);
    try {
      const { user, dialog } = await openPregnancyLoss();
      fireEvent.change(within(dialog).getByLabelText("Notes"), {
        target: { value: "  Observed at dawn  " },
      });
      await user.click(within(dialog).getByRole("button", { name: "Record pregnancy loss" }));

      await waitFor(() => expect(abortCalls).toBe(1));
      expect(abortBody).toEqual({
        loss_date: TODAY,
        cause: "UNKNOWN",
        notes: "Observed at dawn",
      });
      // The dialog saves over fetch; a native form post would reload the app.
      expect(submissions.map((event) => event.defaultPrevented)).toEqual([true]);
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(toast.success).toHaveBeenCalledWith("Pregnancy loss recorded.");
    } finally {
      document.removeEventListener("submit", capture);
    }
  });

  it("clears the loss conflict while the retry is in flight", async () => {
    const { gate, release } = createGate();
    let calls = 0;
    server.use(
      http.post("/api/breeding/:recordId/abort", async () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json({ detail: "kidding already recorded" }, { status: 409 });
        }
        await gate;
        return HttpResponse.json(makeRecord({ id: 2, outcome: "ABORTED", loss_date: TODAY }));
      }),
    );
    const { user, dialog } = await openPregnancyLoss();
    await user.click(within(dialog).getByRole("button", { name: "Record pregnancy loss" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("kidding already recorded");
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("kidding already recorded"));

    await user.click(within(dialog).getByRole("button", { name: "Retry record loss" }));
    await waitFor(() => expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument());
    expect(within(dialog).getByRole("button", { name: "Recording…" })).toBeDisabled();

    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
