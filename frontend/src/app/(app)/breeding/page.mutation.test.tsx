/**
 * Mutation-hardening for the breeding page: the method switch in the
 * add-breeding form (NATURAL requires a buck; AI methods take an optional
 * semen sire and post a method-specific payload), the eligible doe/buck
 * count gates, species vocabulary (goat vs buffalo dairy) in dialog copy
 * and the ultrasound kid-count defaults, the deep-link prefill/dismissal
 * logic (including the stale not-PENDING notice), and the paginated list's
 * settling note.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect } from "react";

import type { AnimalOut, BreedingRecordOut } from "@/api/generated/models";
import { useAuth } from "@/lib/auth-context";
import { permissionsHandler, server, TEST_FARMS } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { farmToday } from "@/lib/format";

import BreedingPage from "./page";

const { navState, toastMock } = vi.hoisted(() => ({
  navState: { search: "" },
  toastMock: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("sonner", () => ({ toast: toastMock }));

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
    restriction_cleared_at: null,
    restriction_cleared_by_id: null,
    restriction_clearance_reference: null,
    suspected_scheduled_disease: false,
    suspected_disease: null,
    authority_notified_at: null,
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

function rowOf(text: string): HTMLElement {
  const row = screen.getByText(text).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("BreedingPage mutation hardening", () => {
  let breedingPostBody: Record<string, unknown> | null;
  let ultrasoundBody: Record<string, unknown> | null;
  let abortBody: Record<string, unknown> | null;
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
    vi.restoreAllMocks();
    breedingPostBody = null;
    ultrasoundBody = null;
    abortBody = null;
    detailCalls = 0;
    listPayload = {
      records: [PENDING_REC, PREGNANT_REC],
      candidate_availability: {
        eligible_doe_count: 1,
        eligible_buck_count: 1,
      },
      total: 2,
      limit: 50,
      offset: 0,
    };
    server.use(
      http.get("/api/breeding", () => HttpResponse.json(listPayload)),
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
        abortBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeRecord({ id: 2, outcome: "ABORTED" }));
      }),
      http.get("/api/breeding/:recordId", ({ params }) => {
        detailCalls += 1;
        return HttpResponse.json(makeRecord({ id: Number(params.recordId) }));
      }),
    );
  });

  afterEach(() => {
    navState.search = "";
    window.history.replaceState({}, "", "/breeding");
  });

  async function renderLoaded() {
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("1 Jul 2026");
  }

  async function openNewDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add breeding" }));
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });
    await waitFor(() =>
      expect(within(dialog).getAllByRole("combobox").length).toBeGreaterThan(0),
    ); // candidates loaded
    return { user, dialog };
  }

  async function pickDoe(user: User, dialog: HTMLElement) {
    const [doeTrigger] = within(dialog).getAllByRole("combobox");
    await pickOption(user, doeTrigger, /G-010 · Lakshmi/);
  }

  // ---------- method switch: labels, fields, gates ----------

  it("renders the three method options with their species hints", async () => {
    const { dialog } = await openNewDialog();
    const group = within(dialog).getByRole("radiogroup", { name: "Breeding method" });
    expect(within(group).getByRole("radio", { name: /Natural/ })).toBeChecked();
    expect(within(group).getByRole("radio", { name: /Conventional semen/ })).not.toBeChecked();
    expect(within(group).getByRole("radio", { name: /90% female kids/ })).not.toBeChecked();
    expect(within(group).getByText("Herd buck")).toBeInTheDocument();
    expect(within(group).getByText("Conventional semen")).toBeInTheDocument();
    expect(within(group).getByText("~90% female kids")).toBeInTheDocument();
  });

  it("hides the buck picker and shows the optional semen-sire field for AI", async () => {
    const { user, dialog } = await openNewDialog();
    await user.click(within(dialog).getByRole("radio", { name: /Conventional semen/ }));

    expect(within(dialog).queryByRole("combobox", { name: "Buck \*" })).not.toBeInTheDocument();
    const sireInput = within(dialog).getByLabelText(/Semen buck \(optional\)/);
    expect(sireInput).toHaveAttribute("placeholder", "Buck name / straw code");
    expect(
      within(dialog).getByText("The sire named on the straw used for this doe."),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/for a natural service/)).not.toBeInTheDocument();
  });

  it("keeps the buck requirement to natural service only", async () => {
    const { user, dialog } = await openNewDialog();
    await pickDoe(user, dialog);
    // NATURAL without a buck is rejected…
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));
    expect(
      await within(dialog).findByText("Select a buck for a natural service"),
    ).toBeInTheDocument();
    expect(breedingPostBody).toBeNull();

    // …but the same form is submittable the moment the method switches to AI.
    await user.click(within(dialog).getByRole("radio", { name: /Conventional semen/ }));
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    await waitFor(() => expect(breedingPostBody).not.toBeNull());
  });

  it("posts an AI breeding with the method and a null semen sire, and no buck id", async () => {
    const { user, dialog } = await openNewDialog();
    await pickDoe(user, dialog);
    await user.click(within(dialog).getByRole("radio", { name: /Conventional semen/ }));
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    await waitFor(() => expect(breedingPostBody).not.toBeNull());
    expect(breedingPostBody).toEqual({
      doe_id: 10,
      method: "AI",
      semen_sire_name: null,
      breeding_date: TODAY,
    });
    expect("buck_id" in breedingPostBody!).toBe(false);
  });

  it("posts a sexed-AI breeding with the trimmed semen-sire name", async () => {
    const { user, dialog } = await openNewDialog();
    await pickDoe(user, dialog);
    await user.click(within(dialog).getByRole("radio", { name: /90% female kids/ }));
    await user.type(within(dialog).getByLabelText(/Semen buck \(optional\)/), "  Fleet  ");
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    await waitFor(() => expect(breedingPostBody).not.toBeNull());
    expect(breedingPostBody).toEqual({
      doe_id: 10,
      method: "AI_SEXED",
      semen_sire_name: "Fleet",
      breeding_date: TODAY,
    });
  });

  it("keeps the no-eligible-buck gate to natural service", async () => {
    listPayload.candidate_availability = {
      eligible_doe_count: 1,
      eligible_buck_count: 0,
    };
    const { user, dialog } = await openNewDialog();

    const save = within(dialog).getByRole("button", { name: "Save breeding" });
    expect(save).toBeDisabled();
    expect(
      await within(dialog).findByText(/No eligible bucks are available/),
    ).toBeInTheDocument();

    await pickDoe(user, dialog);
    await user.click(within(dialog).getByRole("radio", { name: /90% female kids/ }));
    expect(within(dialog).queryByText(/No eligible bucks are available/)).not.toBeInTheDocument();
    expect(save).toBeEnabled();

    await user.click(save);
    await waitFor(() => expect(breedingPostBody).not.toBeNull());
    expect("buck_id" in breedingPostBody!).toBe(false);
  });

  it("rejects a breeding date whose typed shape is not YYYY-MM-DD", async () => {
    const { user, dialog } = await openNewDialog();
    await pickDoe(user, dialog);
    fireEvent.change(within(dialog).getByLabelText(/breeding date/i), {
      target: { value: "2026-7-1" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    expect(await within(dialog).findByText("Pick a valid date")).toBeInTheDocument();
    expect(breedingPostBody).toBeNull();
  });

  it("posts a natural breeding with only the numeric buck id and no method", async () => {
    const { user, dialog } = await openNewDialog();
    await pickDoe(user, dialog);
    const [, buckTrigger] = within(dialog).getAllByRole("combobox");
    await pickOption(user, buckTrigger, /G-020 — 24 mo/);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    await waitFor(() => expect(breedingPostBody).not.toBeNull());
    expect(breedingPostBody).toEqual({
      doe_id: 10,
      buck_id: 20,
      breeding_date: TODAY,
    });
    expect("method" in breedingPostBody!).toBe(false);
    expect("semen_sire_name" in breedingPostBody!).toBe(false);
  });

  // ---------- species vocabulary (buffalo dairy) ----------

  function useDairyFarm() {
    const DAIRY_FARM = { ...TEST_FARMS[0], farm_type: "BUFFALO_DAIRY" as const };
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([DAIRY_FARM])));
  }

  it("uses buffalo dairy nouns throughout the add-breeding dialog", async () => {
    useDairyFarm();
    listPayload.candidate_availability = { eligible_doe_count: 1, eligible_buck_count: 0 };
    const { dialog } = await openNewDialog();

    expect(
      within(dialog).getByText(
        /A pregnancy-check task is auto-created \(60 days after the service; buffaloes are bred back during lactation\)\./,
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Milking buffalo *")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Bull *")).toBeInTheDocument();
    expect(within(dialog).getByText("Herd bull")).toBeInTheDocument();
    expect(within(dialog).getByText("~90% female calves")).toBeInTheDocument();
    expect(
      await within(dialog).findByText(/No eligible bulls are available/),
    ).toBeInTheDocument();
  });

  it("uses the dairy semen-sire hint and straw-code example", async () => {
    useDairyFarm();
    const { user, dialog } = await openNewDialog();
    await pickDoe(user, dialog);
    await user.click(within(dialog).getByRole("radio", { name: /Conventional semen/ }));

    const sireInput = within(dialog).getByLabelText(/Semen bull \(optional\)/);
    expect(sireInput).toHaveAttribute(
      "placeholder",
      "Bull name / straw code, e.g. Karanvir 999",
    );
    expect(
      within(dialog).getByText(
        "The sire on the straw — verifiable daughters' yield >3,000 kg/lactation.",
      ),
    ).toBeInTheDocument();
  });

  it("states the goat pregnancy-check offset without the dairy lactation note", async () => {
    const { dialog } = await openNewDialog();
    expect(
      within(dialog).getByText(
        "A pregnancy-check task is auto-created (32 days after the service).",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/bred back during lactation/)).not.toBeInTheDocument();
  });

  it("uses the heifer gate copy for a dairy farm with no breeding-ready females", async () => {
    useDairyFarm();
    listPayload.candidate_availability = { eligible_doe_count: 0, eligible_buck_count: 0 };
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add breeding" }));
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });

    expect(
      await within(dialog).findByText(/No breeding-ready females right now\./),
    ).toBeInTheDocument();
    expect(
      await within(dialog).findByText(/No breeding-ready females right now\./),
    ).toHaveTextContent(
      "Heifers are bred at 24 months and ≥340 kg (AI at 60 days post-calving; max 3 services before cull review).",
    );
  });

  it("labels the dairy table with buffalo nouns and the calving expectation", async () => {
    useDairyFarm();
    await renderLoaded();
    expect(screen.getByRole("columnheader", { name: "Milking buffalo" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Bull" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Expected calving" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Calves" })).toBeInTheDocument();
  });

  it("defaults a dairy pregnancy to a single calf and caps the count at two", async () => {
    useDairyFarm();
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(rowOf("Pending")).getByRole("button", { name: "Ultrasound result" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Ultrasound result" });

    await user.click(within(dialog).getByRole("checkbox"));
    expect(within(dialog).getByText("Calf count detected")).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox")).toHaveTextContent("1");

    await user.click(within(dialog).getByRole("combobox"));
    expect((await screen.findAllByRole("option")).map((o) => o.textContent)).toEqual(["1", "2"]);

    await user.click(within(dialog).getByRole("button", { name: "Save result" }));
    await waitFor(() => expect(ultrasoundBody).not.toBeNull());
    expect(ultrasoundBody).toEqual({ pregnant: true, date: TODAY, kid_count: 1 });
  });

  // ---------- deep-link prefill / dismissal ----------

  it("shows the stale-link notice for a resolved non-PENDING record and clears it", async () => {
    navState.search = "?ultrasound_id=2";
    await renderLoaded();

    const notice = await screen.findByText(/is not awaiting a result — nothing to record\./);
    expect(notice).toHaveTextContent("Ultrasound record #2 (confirmed pregnant)");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Clear link" }));
    await waitFor(() =>
      expect(
        screen.queryByText(/is not awaiting a result/),
      ).not.toBeInTheDocument(),
    );
  });

  it("defers a slowly-resolving deep link while the add-breeding dialog is open", async () => {
    // Off-page record whose detail fetch resolves slowly, so the page is
    // still free when it lands.
    server.use(
      http.get("/api/breeding/99", async () => {
        detailCalls += 1;
        await new Promise((resolve) => setTimeout(resolve, 300));
        return HttpResponse.json(makeRecord({ id: 99, breeding_date: "2026-06-01" }));
      }),
    );
    navState.search = "?ultrasound_id=99";
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Add breeding" }));
    const newDialog = await screen.findByRole("dialog", { name: "Add breeding" });
    await waitFor(() => expect(detailCalls).toBe(1));
    await waitFor(() =>
      expect(within(newDialog).getAllByRole("combobox").length).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("dialog", { name: "Ultrasound result" })).not.toBeInTheDocument();

    await user.keyboard("{Escape}");
    const ultrasound = await screen.findByRole("dialog", { name: "Ultrasound result" });
    expect(within(ultrasound).getByText(/bred 1 Jun 2026/)).toBeInTheDocument();
  });

  it("hides the stale-link notice while a pregnancy-loss dialog is open", async () => {
    navState.search = "?ultrasound_id=2";
    const user = userEvent.setup();
    await renderLoaded();
    expect(await screen.findByText(/is not awaiting a result/)).toBeInTheDocument();

    const pregnantRow = screen.getAllByText("Confirmed Pregnant")[0].closest("tr")!;
    await user.click(within(pregnantRow).getByRole("button", { name: "Record loss" }));
    await screen.findByRole("dialog", { name: "Record pregnancy loss" });
    expect(screen.queryByText(/is not awaiting a result/)).not.toBeInTheDocument();

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Record pregnancy loss" })).not.toBeInTheDocument(),
    );
    expect(await screen.findByText(/is not awaiting a result/)).toBeInTheDocument();
  });

  it("ignores a deep link without breeding.manage and never fetches the record", async () => {
    server.use(permissionsHandler(["breeding.view"]));
    navState.search = "?ultrasound_id=99";
    await renderLoaded();

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText(/is not awaiting a result/)).not.toBeInTheDocument();
    expect(detailCalls).toBe(0);
  });

  it("still opens a row's own ultrasound dialog after its deep link was dismissed", async () => {
    navState.search = "?ultrasound_id=1";
    const user = userEvent.setup();
    await renderLoaded();
    const deepLinked = await screen.findByRole("dialog", { name: "Ultrasound result" });
    await user.click(within(deepLinked).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(
      within(rowOf("Pending")).getByRole("button", { name: "Ultrasound result" }),
    );
    const reopened = await screen.findByRole("dialog", { name: "Ultrasound result" });
    expect(
      within(reopened).getByText(/Doe G-010 · bred 1 Jul 2026 by G-020/),
    ).toBeInTheDocument();
  });

  // ---------- list details ----------

  it("announces the page turn while the next page settles", async () => {
    listPayload.total = 120;
    let calls = 0;
    server.use(
      http.get("/api/breeding", async () => {
        calls += 1;
        if (calls > 1) await new Promise((resolve) => setTimeout(resolve, 150));
        return HttpResponse.json(listPayload);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Updating breeding records…")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText("Updating breeding records…")).not.toBeInTheDocument(),
    );
  });

  it("shows the loss-cause label in the closed trigger and posts the herd-exit cause", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const pregnantRow = screen.getAllByText("Confirmed Pregnant")[0].closest("tr")!;
    await user.click(within(pregnantRow).getByRole("button", { name: "Record loss" }));
    const dialog = await screen.findByRole("dialog", { name: "Record pregnancy loss" });

    expect(within(dialog).getByLabelText("Cause *")).toHaveTextContent("Unknown");
    await user.click(within(dialog).getByLabelText("Cause *"));
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual([
      "Unknown",
      "Disease",
      "Injury",
      "Nutritional",
      "Trauma",
      "Other",
    ]);
    await user.click(screen.getByRole("option", { name: "Trauma" }));
    expect(within(dialog).getByLabelText("Cause *")).toHaveTextContent("Trauma");
    await user.click(within(dialog).getByRole("button", { name: "Record pregnancy loss" }));

    await waitFor(() => expect(abortBody).not.toBeNull());
    expect(abortBody).toMatchObject({
      loss_date: TODAY,
      cause: "TRAUMA",
      notes: null,
    });
  });

  it("keeps the breeding date as the loss floor when the scan result equals it", async () => {
    listPayload.records = listPayload.records.map((record) =>
      record.id === PREGNANT_REC.id
        ? { ...record, ultrasound_result_date: "2026-07-01" }
        : record,
    );
    const user = userEvent.setup();
    await renderLoaded();
    const pregnantRow = screen.getAllByText("Confirmed Pregnant")[0].closest("tr")!;
    await user.click(within(pregnantRow).getByRole("button", { name: "Record loss" }));
    const dialog = await screen.findByRole("dialog", { name: "Record pregnancy loss" });

    fireEvent.change(within(dialog).getByLabelText("Loss date *"), {
      target: { value: "2026-06-30" },
    });
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Loss date cannot be before 1 Jul 2026",
    );
  });

  it("offers the empty-state add button only to managers", async () => {
    listPayload.records = [];
    listPayload.total = 0;
    server.use(permissionsHandler(["breeding.view"]));
    renderWithProviders(<BreedingPage />);
    expect(await screen.findByText("No breeding records yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add breeding" })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Add a breeding to start tracking ultrasound checks and expected kidding dates/),
    ).toBeInTheDocument();
  });

  it("opens the add-breeding dialog from the empty state", async () => {
    listPayload.records = [];
    listPayload.total = 0;
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);
    await screen.findByText("No breeding records yet.");
    await user.click(screen.getAllByRole("button", { name: "Add breeding" }).pop()!);
    expect(await screen.findByRole("dialog", { name: "Add breeding" })).toBeInTheDocument();
  });

  // ---------- round 2: remaining mutation survivors ----------

  it("measures the semen-sire length cap after trimming", async () => {
    // 120 visible characters plus trailing spaces: the schema's trim must run
    // before max(120) accepts it (and the payload posts the trimmed name).
    const { user, dialog } = await openNewDialog();
    await pickDoe(user, dialog);
    await user.click(within(dialog).getByRole("radio", { name: /Conventional semen/ }));
    fireEvent.change(within(dialog).getByLabelText(/Semen buck \(optional\)/), {
      target: { value: "F".repeat(120) + "   " },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    await waitFor(() => expect(breedingPostBody).not.toBeNull());
    expect(breedingPostBody).toMatchObject({ semen_sire_name: "F".repeat(120) });
  });

  it("titles the doe and buck candidate-picker dialogs", async () => {
    const { user, dialog } = await openNewDialog();

    await user.click(within(dialog).getAllByRole("combobox")[0]);
    expect(
      await screen.findByRole("dialog", { name: "Choose a breeding-ready doe" }),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");

    const [, buckTrigger] = within(dialog).getAllByRole("combobox");
    await user.click(buckTrigger);
    expect(
      await screen.findByRole("dialog", { name: "Choose an active buck" }),
    ).toBeInTheDocument();
  });

  it("retries the permissions load in place after a failed fetch", async () => {
    let calls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        calls += 1;
        return calls === 1
          ? new HttpResponse(null, { status: 500 })
          : HttpResponse.json({
              is_owner: false,
              permissions: ["breeding.view", "breeding.manage"],
            });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load your permissions");
    await user.click(screen.getByRole("button", { name: "Retry permissions" }));

    await screen.findAllByText("1 Jul 2026");
    expect(calls).toBeGreaterThanOrEqual(2);
  });

  it("does not flag a still-pending deep link as stale (off-page fetch)", async () => {
    navState.search = "?ultrasound_id=99";
    await renderLoaded();

    expect(await screen.findByRole("dialog", { name: "Ultrasound result" })).toBeInTheDocument();
    expect(screen.queryByText(/is not awaiting a result/)).not.toBeInTheDocument();
    expect(detailCalls).toBe(1);
  });

  it("does not flag an on-page pending deep link as stale", async () => {
    navState.search = "?ultrasound_id=1";
    await renderLoaded();

    expect(await screen.findByRole("dialog", { name: "Ultrasound result" })).toBeInTheDocument();
    expect(screen.queryByText(/is not awaiting a result/)).not.toBeInTheDocument();
    expect(detailCalls).toBe(0);
  });

  it("offers both the header and empty-state add buttons to managers", async () => {
    listPayload.records = [];
    listPayload.total = 0;
    renderWithProviders(<BreedingPage />);
    await screen.findByText("No breeding records yet.");

    expect(screen.getAllByRole("button", { name: "Add breeding" })).toHaveLength(2);
  });

  it("describes the ultrasound cadence on the records card", async () => {
    await renderLoaded();
    expect(
      screen.getByText(
        "Ultrasound is due 32 days after breeding; confirmed pregnancies get an expected kidding date.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps loss metadata off rows that were not aborted", async () => {
    listPayload.records = [
      makeRecord({
        id: 3,
        outcome: "PENDING",
        loss_date: "2026-08-05",
        loss_cause: "DISEASE",
        loss_notes: "stale",
      }),
    ];
    listPayload.total = 1;
    await renderLoaded();

    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.queryByText(/Disease/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Loss notes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/5 Aug 2026/)).not.toBeInTheDocument();
  });

  // ---------- round 2: farm-switch fences (M-2) ----------

  /** Exposes selectFarm so a test can move to another farm mid-flight. */
  let farmSwitcher: ((id: number) => void) | null = null;
  function FarmSwitchProbe() {
    const { selectFarm } = useAuth();
    useEffect(() => {
      farmSwitcher = selectFarm;
    }, [selectFarm]);
    return null;
  }

  beforeEach(() => {
    toastMock.success.mockClear();
    toastMock.error.mockClear();
  });

  it("silences the breeding-save continuation after a farm switch", async () => {
    let resolvePost!: (value: Response) => void;
    server.use(
      http.post("/api/breeding", () => new Promise<Response>((resolve) => {
        resolvePost = resolve;
      })),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <FarmSwitchProbe />
        <BreedingPage />
      </>,
    );
    await screen.findAllByText("1 Jul 2026");

    await user.click(screen.getByRole("button", { name: "Add breeding" }));
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });
    await waitFor(() =>
      expect(within(dialog).getAllByRole("combobox").length).toBeGreaterThan(0),
    );
    await pickDoe(user, dialog);
    const [, buckTrigger] = within(dialog).getAllByRole("combobox");
    await pickOption(user, buckTrigger, /G-020 — 24 mo/);
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));
    await waitFor(() => expect(resolvePost).toBeDefined());

    expect(farmSwitcher).not.toBeNull();
    farmSwitcher!(2);
    resolvePost(HttpResponse.json(makeRecord({ id: 99 }), { status: 201 }));

    await waitFor(() => expect(toastMock.error).not.toHaveBeenCalled());
    expect(toastMock.success).not.toHaveBeenCalledWith("Breeding saved.");
  });

  it("silences the ultrasound-result continuation after a farm switch", async () => {
    let resolvePost!: (value: Response) => void;
    server.use(
      http.post("/api/breeding/:recordId/ultrasound", () => new Promise<Response>((resolve) => {
        resolvePost = resolve;
      })),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <FarmSwitchProbe />
        <BreedingPage />
      </>,
    );
    await screen.findAllByText("1 Jul 2026");

    await user.click(
      within(rowOf("Pending")).getByRole("button", { name: "Ultrasound result" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Ultrasound result" });
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));
    await waitFor(() => expect(resolvePost).toBeDefined());

    farmSwitcher!(2);
    resolvePost(HttpResponse.json(makeRecord({ id: 1, ultrasound_done: true })));

    await waitFor(() => expect(toastMock.error).not.toHaveBeenCalled());
    expect(toastMock.success).not.toHaveBeenCalledWith("Ultrasound result saved.");
  });

  it("silences the pregnancy-loss continuation after a farm switch", async () => {
    let resolvePost!: (value: Response) => void;
    server.use(
      http.post("/api/breeding/:recordId/abort", () => new Promise<Response>((resolve) => {
        resolvePost = resolve;
      })),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <FarmSwitchProbe />
        <BreedingPage />
      </>,
    );
    await screen.findAllByText("1 Jul 2026");

    const pregnantRow = screen.getAllByText("Confirmed Pregnant")[0].closest("tr")!;
    await user.click(within(pregnantRow).getByRole("button", { name: "Record loss" }));
    const dialog = await screen.findByRole("dialog", { name: "Record pregnancy loss" });
    await user.click(within(dialog).getByRole("button", { name: "Record pregnancy loss" }));
    await waitFor(() => expect(resolvePost).toBeDefined());

    farmSwitcher!(2);
    resolvePost(HttpResponse.json(makeRecord({ id: 2, outcome: "ABORTED" })));

    await waitFor(() => expect(toastMock.error).not.toHaveBeenCalled());
    expect(toastMock.success).not.toHaveBeenCalledWith("Pregnancy loss recorded.");
  });
});
