/**
 * Breeding + kidding pages — fresh-domain mutation campaign kills
 * (2026-09): direct schema-level kills for the inline gates (breeding
 * method/buck rules, kidding litter and mortality rules), the loss-cause
 * label catalog, and the stale-data retry wiring.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { addDays, farmToday } from "@/lib/format";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import { breedingSchema, default as BreedingPage } from "./page";
import KiddingPage from "@/app/(app)/kidding/page";
import { kiddingSchema } from "@/app/(app)/kidding/page";

const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

const navState = vi.hoisted(() => ({ search: "", pathname: "/breeding" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => navState.pathname,
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

beforeAll(() => {
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

function messagesOf(result: { success: boolean; error?: { issues: Array<{ message: string }> } }) {
  return result.success ? [] : (result.error?.issues ?? []).map((i) => i.message);
}

describe("breedingSchema — campaign kills", () => {
  const schema = breedingSchema(farmVocabulary);
  const base = {
    doe_id: "7",
    buck_id: "9",
    method: "NATURAL" as const,
    breeding_date: TODAY,
  };

  it("requires a doe", () => {
    expect(messagesOf(schema.safeParse({ ...base, doe_id: "" }))).toContain("Select a doe");
  });

  it("requires a buck for natural service only", () => {
    expect(
      messagesOf(schema.safeParse({ ...base, buck_id: "" })),
    ).toContain("Select a buck for a natural service");
    expect(messagesOf(schema.safeParse({ ...base, method: "AI", buck_id: "" }))).toEqual([]);
  });

  it("validates the date grammar and future bound", () => {
    expect(messagesOf(schema.safeParse({ ...base, breeding_date: "2026-9-1" }))).toContain(
      "Pick a valid date",
    );
    expect(messagesOf(schema.safeParse({ ...base, breeding_date: addDays(TODAY, 1) }))).toContain(
      "Date can't be in the future",
    );
  });

  it("files its custom gates with the custom zod code", () => {
    const bad = schema.safeParse({ doe_id: "10", buck_id: "", breeding_date: "2026-01-01", method: "NATURAL" });
    if (!bad.success) {
      expect(bad.error.issues.map((i) => i.code)).toContain("custom");
    }
  });

  it("rejects trailing garbage behind a valid date prefix", () => {
    const bad = schema.safeParse({
      doe_id: "10",
      buck_id: "20",
      breeding_date: "2026-01-01x",
      method: "NATURAL",
    });
    expect(bad.success).toBe(false);
    const ok = schema.safeParse({
      doe_id: "10",
      buck_id: "20",
      breeding_date: "2026-01-01",
      method: "NATURAL",
    });
    expect(ok.success).toBe(true);
  });

  it("caps the semen sire name", () => {
    expect(
      messagesOf(
        schema.safeParse({ ...base, method: "AI", semen_sire_name: "x".repeat(121) }),
      ),
    ).not.toEqual([]);
    expect(
      messagesOf(schema.safeParse({ ...base, method: "AI", semen_sire_name: "Named sire" })),
    ).toEqual([]);
  });
});

describe("kiddingSchema — campaign kills", () => {
  const schema = kiddingSchema(farmVocabulary);
  const kid = (overrides: Record<string, unknown> = {}) => ({
    tag: "",
    sex: "F" as const,
    birth_weight: 2.5,
    status: "ALIVE" as const,
    mortality_reported_at: "",
    colostrum: "unrecorded" as const,
    navel: "unrecorded" as const,
    dam_rejected: false,
    ...overrides,
  });
  const base = {
    date: TODAY,
    ease: "NORMAL" as const,
    placenta: "unrecorded" as const,
    mastitis_suspected: false,
    notes: "",
    kids: [kid()],
  };

  it("validates the date grammar and future bound", () => {
    expect(messagesOf(schema.safeParse({ ...base, date: "2026-9-1" }))).toContain(
      "Pick a valid date",
    );
    expect(messagesOf(schema.safeParse({ ...base, date: addDays(TODAY, 1) }))).toContain(
      "Date can't be in the future",
    );
  });

  it("requires at least one kid and caps the litter", () => {
    expect(messagesOf(schema.safeParse({ ...base, kids: [] }))).toContain("At least one kid");
    const bigLitter = Array.from({ length: farmVocabulary.facts.maxLitterSize + 1 }, () => kid());
    expect(messagesOf(schema.safeParse({ ...base, kids: bigLitter }))).toContain(
      `A kidding delivers at most ${farmVocabulary.facts.maxLitterSize} kids on this farm`,
    );
  });

  it("requires a mortality date only for kids that died", () => {
    expect(
      messagesOf(schema.safeParse({ ...base, kids: [kid({ status: "DIED" })] })),
    ).toContain("Mortality date is required");
    expect(
      messagesOf(
        schema.safeParse({
          ...base,
          kids: [kid({ status: "DIED", mortality_reported_at: addDays(TODAY, -1) })],
        }),
      ),
    ).toContain("Can't be before the kidding date");
    expect(
      messagesOf(
        schema.safeParse({
          ...base,
          kids: [kid({ status: "DIED", mortality_reported_at: TODAY })],
        }),
      ),
    ).toEqual([]);
  });

  it("rejects trailing garbage dates, empty litters, and files custom codes", () => {
    expect(schema.safeParse({ ...base, date: "2026-01-01x" }).success).toBe(false);
    const died = schema.safeParse({ ...base, kids: [kid({ status: "DIED" })] });
    if (!died.success) {
      expect(died.error.issues.map((i) => i.code)).toContain("custom");
    }
    // A whitespace-only mortality date is an absent date: the DIED kid still
    // demands a real one.
    const blank = schema.safeParse({
      ...base,
      kids: [kid({ status: "DIED", mortality_reported_at: "   " })],
    });
    expect(blank.success).toBe(false);
    // Whitespace reads as ABSENT specifically — not as a date before the
    // kidding date (which an untrimmed value would trip instead).
    expect(messagesOf(blank)).toContain("Mortality date is required");
    // A future mortality date files the future gate.
    const future = schema.safeParse({
      ...base,
      kids: [kid({ status: "DIED", mortality_reported_at: addDays(TODAY, 1) })],
    });
    if (!future.success) {
      expect(future.error.issues.map((i) => i.message)).toContain("Date can't be in the future");
      expect(future.error.issues.map((i) => i.code)).toContain("custom");
    }
  });

  it("caps free-form fields per kid", () => {
    expect(messagesOf(schema.safeParse({ ...base, kids: [kid({ tag: "x".repeat(51) })] }))).toContain(
      "Max 50 characters",
    );
    expect(
      messagesOf(schema.safeParse({ ...base, kids: [kid({ birth_weight: 0.0001 })] })),
    ).toContain(`A newborn kid weighs at least ${farmVocabulary.facts.birthWeightKg.min} kg`);
    expect(messagesOf(schema.safeParse({ ...base, notes: "x".repeat(4001) }))).toContain(
      "Notes cannot exceed 4000 characters",
    );
  });
});

describe("BreedingPage — campaign kills", () => {
  const BREEDING = (overrides: Record<string, unknown> = {}) => ({
    id: 1,
    doe_id: 10,
    buck_id: 20,
    semen_sire_name: null,
    breeding_date: addDays(TODAY, -30),
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
    doe_tag: "D-1",
    buck_tag: "B-1",
    ...overrides,
  });

  const listBody = (records: Record<string, unknown>[]) => ({
    records,
    candidate_availability: { eligible_doe_count: 3, eligible_buck_count: 1 },
    total: records.length,
    limit: 50,
    offset: 0,
  });

  beforeEach(() => {
    navState.search = "";
    server.use(
      http.get("/api/breeding", () => HttpResponse.json(listBody([BREEDING()]))),
      http.get("/api/breeding/candidates", () =>
        HttpResponse.json({
          candidates: [
            { id: 10, tag_number: "D-1", name: null, age_months: 24, latest_weight_kg: null },
            { id: 20, tag_number: "B-1", name: null, age_months: 30, latest_weight_kg: null },
          ],
          total: 2,
        }),
      ),
    );
  });

  it("renders the loss-cause catalog labels and candidate counts", async () => {
    server.use(
      http.get("/api/breeding", () =>
        HttpResponse.json(
          listBody([
            BREEDING({
              outcome: "ABORTED",
              // ANIMAL_STATUS_CHANGE is the one cause whose catalog label is
              // not a titlecase of the raw enum — it pins the catalog lookup.
              loss_cause: "ANIMAL_STATUS_CHANGE",
              loss_date: addDays(TODAY, -5),
            }),
          ]),
        ),
      ),
    );
    renderWithProviders(<BreedingPage />);
    // The loss cause reads through the shared enum-label catalog, not raw.
    expect((await screen.findAllByText(/Herd exit \(administrative close\)/))[0]).toBeInTheDocument();
    // An available buck means the no-buck warning must stay hidden (the
    // availability read feeds the create dialog's eligibility copy).
    await waitFor(() =>
      expect(screen.queryByText(/No eligible/i)).not.toBeInTheDocument(),
    );
  });

  it("saves an AI breeding with a trimmed sire name and clears lifted picker labels", async () => {
    let releaseSave!: () => void;
    let savedBody: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/breeding", async ({ request }) => {
        savedBody = (await request.json()) as Record<string, unknown>;
        return new Promise((resolve) => {
          releaseSave = () => resolve(HttpResponse.json(BREEDING({ id: 2 }), { status: 201 }));
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("D-1");

    await user.click(screen.getAllByRole("button", { name: "Add breeding" })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });

    // Pick the first doe through the remote picker.
    await user.click(within(dialog).getAllByRole("combobox")[0]!);
    const pickerDialog = await screen.findByRole("dialog");
    await user.click((await within(pickerDialog).findAllByRole("option"))[0]!);

    await user.click(within(dialog).getByRole("radio", { name: /conventional semen/i }));
    await user.type(within(dialog).getByLabelText("Semen buck (optional)"), "  Boss  ");
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));
    expect(await within(dialog).findByRole("button", { name: "Saving…" })).toBeDisabled();

    await act(async () => {
      releaseSave();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    await waitFor(() => expect(toastMocks.success).toHaveBeenCalledWith("Breeding saved."));
    expect(savedBody).toMatchObject({ doe_id: 10, method: "AI", semen_sire_name: "Boss" });
    expect(savedBody).not.toHaveProperty("buck_id");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // Reopening shows a fresh form: the lifted doe label was cleared with it.
    await user.click(screen.getAllByRole("button", { name: "Add breeding" })[0]!);
    const reopened = await screen.findByRole("dialog", { name: "Add breeding" });
    expect(within(reopened).queryByText("D-1")).not.toBeInTheDocument();
  });

  it("maps 422 field issues inline and banners only the unmapped remainder", async () => {
    server.use(
      http.post("/api/breeding", () =>
        HttpResponse.json(
          {
            detail: [
              { loc: ["body", "doe_id"], msg: "Select a doe" },
              { loc: ["body", "mystery"], msg: "unknown field" },
              { loc: ["body", "mystery2"], msg: "second unknown" },
            ],
          },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("D-1");

    await user.click(screen.getAllByRole("button", { name: "Add breeding" })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });
    await user.click(within(dialog).getAllByRole("combobox")[0]!);
    const pickerDialog = await screen.findByRole("dialog");
    await user.click((await within(pickerDialog).findAllByRole("option"))[0]!);
    await user.click(within(dialog).getByRole("radio", { name: /conventional semen/i }));
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    expect(await within(dialog).findByText("Select a doe")).toBeInTheDocument();
    // Only the unmapped issues degrade to the banner, joined with "; " — not
    // the full server sentence.
    expect(
      await within(dialog).findByText("unknown field; second unknown"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith("unknown field; second unknown"),
    );
    expect(
      within(dialog).queryByText(/The server rejected these values/),
    ).not.toBeInTheDocument();

    expect(
      within(dialog).queryByText(/The server rejected these values/),
    ).not.toBeInTheDocument();

    // Dismissing and reopening starts clear of the stale banner.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getAllByRole("button", { name: "Add breeding" })[0]!);
    const reopened = await screen.findByRole("dialog", { name: "Add breeding" });
    expect(within(reopened).queryByText("unknown field; second unknown")).not.toBeInTheDocument();
  });

  it("renders no banner at all when every 422 issue maps onto a field", async () => {
    server.use(
      http.post("/api/breeding", () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "doe_id"], msg: "Select a doe" }] },
          { status: 422 },
        ),
      ),
    );
    toastMocks.error.mockClear();
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("D-1");

    await user.click(screen.getAllByRole("button", { name: "Add breeding" })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });
    await user.click(within(dialog).getAllByRole("combobox")[0]!);
    const pickerDialog = await screen.findByRole("dialog");
    await user.click((await within(pickerDialog).findAllByRole("option"))[0]!);
    await user.click(within(dialog).getByRole("radio", { name: /conventional semen/i }));
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));

    expect(await within(dialog).findByText("Select a doe")).toBeInTheDocument();
    // Nothing unmapped: no banner, no toast — the inline error is the whole story.
    expect(within(dialog).queryByRole("alert")).toHaveTextContent("Select a doe");
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("shows the no-eligible-buck warning when the herd has none", async () => {
    server.use(
      http.get("/api/breeding", () =>
        HttpResponse.json({
          records: [],
          candidate_availability: { eligible_doe_count: 3, eligible_buck_count: 0 },
          total: 0,
          limit: 50,
          offset: 0,
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);
    await screen.findByText(/Add a breeding to start tracking/i);
    await user.click(screen.getAllByRole("button", { name: "Add breeding" })[0]!);
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });
    expect(
      await within(dialog).findByText(/No eligible bucks are available/, { selector: "p" }),
    ).toBeInTheDocument();
  });

  it("ignores a deep-link id that is not a plain positive integer", async () => {
    server.use(
      http.get("/api/breeding", () =>
        HttpResponse.json(
          listBody([BREEDING({ id: 100000, doe_tag: "D-100000", outcome: "PENDING" })]),
        ),
      ),
    );
    renderWithProviders(<BreedingPage />);
    expect((await screen.findAllByText("D-100000"))[0]).toBeInTheDocument();
    // "1e5" parses to 100000 through Number() but must never become an id.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers a working retry from the stale-data notice", async () => {
    let requests = 0;
    let fail = true;
    server.use(
      http.get("/api/breeding", () => {
        requests += 1;
        if (fail) return new HttpResponse(null, { status: 500 });
        return HttpResponse.json(listBody([]));
      }),
    );
    renderWithProviders(<BreedingPage />);
    const retry = await screen.findByRole("button", { name: /retry/i });
    fail = false;
    await userEvent.click(retry);
    await waitFor(() => expect(requests).toBeGreaterThanOrEqual(2));
  });

  it("records a positive ultrasound result from the deep-linked dialog", async () => {
    navState.search = "?ultrasound_id=1";
    let ultrasoundBody: Record<string, unknown> | null = null;
    server.use(
      http.get("/api/breeding", () =>
        // No planned scan date: the description's planned-scan span must
        // render nothing at all, never a placeholder tail.
        HttpResponse.json(listBody([BREEDING({ ultrasound_date: null })])),
      ),
      http.post("/api/breeding/:recordId/ultrasound", async ({ request }) => {
        ultrasoundBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          BREEDING({ id: 1, ultrasound_done: true, pregnant: true }),
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);
    const dialog = await screen.findByRole("dialog", { name: "Ultrasound result" });
    expect(within(dialog).queryByText(/Stryker was here/)).not.toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText(/Result date/), {
      target: { value: TODAY },
    });
    await user.click(within(dialog).getByRole("checkbox", { name: /Pregnant/ }));
    // Checking Pregnant seeds a valid count, so the kid-count block must not
    // sprout any stray alert paragraph.
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));
    await waitFor(() =>
      expect(ultrasoundBody).toMatchObject({ pregnant: true, date: TODAY, kid_count: 2 }),
    );
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith("Ultrasound result saved."),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Ultrasound result" })).not.toBeInTheDocument(),
    );
  });

  it("blocks an ultrasound submit whose result date is out of range", async () => {
    navState.search = "?ultrasound_id=1";
    let ultrasoundPosts = 0;
    server.use(
      http.get("/api/breeding", () => HttpResponse.json(listBody([BREEDING()]))),
      http.post("/api/breeding/:recordId/ultrasound", () => {
        ultrasoundPosts += 1;
        return HttpResponse.json(BREEDING({ id: 1, ultrasound_done: true }));
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);
    const dialog = await screen.findByRole("dialog", { name: "Ultrasound result" });

    // A future result date must block the save at the dialog itself.
    fireEvent.change(within(dialog).getByLabelText(/Result date/), {
      target: { value: addDays(TODAY, 1) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));
    expect(
      await within(dialog).findByText("Result date can't be in the future"),
    ).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(ultrasoundPosts).toBe(0);
  });

  it("never fetches a prefill record detail without a deep link", async () => {
    let detailCalls = 0;
    server.use(
      http.get("/api/breeding/:recordId", () => {
        detailCalls += 1;
        return HttpResponse.json(BREEDING());
      }),
    );
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("D-1");
    await new Promise((resolve) => setTimeout(resolve, 50));
    // enabled: canManage && requestedUltrasoundId !== null && … — a manager
    // with no link must never issue the id-0 detail request.
    expect(detailCalls).toBe(0);
  });

  it("does not resolve an ultrasound deep link for view-only users", async () => {
    server.use(permissionsHandler(["breeding.view"]));
    navState.search = "?ultrasound_id=1";
    let detailCalls = 0;
    server.use(
      http.get("/api/breeding/:recordId", () => {
        detailCalls += 1;
        return HttpResponse.json(BREEDING());
      }),
    );
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("D-1");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(detailCalls).toBe(0);
    expect(screen.queryByRole("dialog", { name: "Ultrasound result" })).not.toBeInTheDocument();
  });
});

describe("KiddingPage — campaign kills", () => {
  const PREGNANCY = (overrides: Record<string, unknown> = {}) => ({
    id: 12,
    doe_id: 10,
    buck_id: 20,
    semen_sire_name: null,
    breeding_date: addDays(TODAY, -140),
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: addDays(TODAY, -40),
    ultrasound_result_date: addDays(TODAY, -40),
    ultrasound_done: true,
    pregnant: true,
    kid_count_detected: 2,
    expected_kidding_date: addDays(TODAY, 25),
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
  });

  const queueBody = (pregnancies: Record<string, unknown>[]) => ({
    records: [],
    records_total: 0,
    upcoming: pregnancies,
    upcoming_total: pregnancies.length,
    upcoming_limit: 25,
    upcoming_offset: 0,
    overdue: [],
    overdue_total: 0,
    overdue_limit: 25,
    overdue_offset: 0,
    total: pregnancies.length,
    limit: 50,
    offset: 0,
  });

  let pregnancyCalls: number[];
  let kiddingPosts: number;

  beforeEach(() => {
    navState.search = "";
    navState.pathname = "/kidding";
    pregnancyCalls = [];
    kiddingPosts = 0;
    server.use(
      http.get("/api/kidding", () => HttpResponse.json(queueBody([PREGNANCY()]))),
      http.get("/api/kidding/pregnancies/:recordId", ({ params }) => {
        pregnancyCalls.push(Number(params.recordId));
        return HttpResponse.json(PREGNANCY());
      }),
      http.post("/api/kidding", () => {
        kiddingPosts += 1;
        return HttpResponse.json(
          { id: 99, doe_id: 10, date: TODAY, breeding_record_id: 12, ease: "NORMAL", notes: null, kids: [], doe_tag: "G-010" },
          { status: 201 },
        );
      }),
    );
  });

  it("demands a mortality date for a died kid whose field is absent entirely", () => {
    // The dialog always registers the input (""), but the schema accepts
    // programmatic payloads — an omitted field must fail, not crash.
    const withoutField: Record<string, unknown> = {
      tag: "",
      sex: "F" as const,
      birth_weight: 2.5,
      status: "DIED" as const,
      colostrum: "unrecorded" as const,
      navel: "unrecorded" as const,
      dam_rejected: false,
    };
    expect(
      messagesOf(
        kiddingSchema(farmVocabulary).safeParse({
          date: TODAY,
          ease: "NORMAL",
          placenta: "unrecorded",
          mastitis_suspected: false,
          notes: "",
          kids: [withoutField],
        }),
      ),
    ).toContain("Mortality date is required");
  });

  it("retries the queue from the stale-data notice after a save", async () => {
    let listCalls = 0;
    server.use(
      http.get("/api/kidding", () => {
        listCalls += 1;
        if (listCalls === 1) return HttpResponse.json(queueBody([PREGNANCY()]));
        return new HttpResponse(null, { status: 500 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Upcoming (next 30 days)");
    // Happy render: no stale-data notice anywhere.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    const section = screen.getByText("Upcoming (next 30 days)").closest("[data-slot='card']") as HTMLElement;
    // Queue rows render twice (below-md card list + desktop table) — either
    // Record button opens the same dialog.
    await user.click(within(section).getAllByRole("button", { name: "Record kidding" })[0]!);
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    await waitFor(() => expect(kiddingPosts).toBe(1));
    // The invalidation refetch failed while the queue stayed on screen.
    const notice = await screen.findByRole("status");
    await user.click(within(notice).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(3));
  });

  it("does not resolve a kidding deep link for view-only users", async () => {
    server.use(permissionsHandler(["kidding.view"]));
    // Off the visible queue: the paged lookup cannot short-circuit, so only
    // the canManage leg of the enabled guard stands between the URL and the
    // detail fetch.
    navState.search = "?breeding_id=55";
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Upcoming (next 30 days)");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(pregnancyCalls).toEqual([]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

const PENDING_BASE = {
  id: 1,
  doe_id: 10,
  buck_id: 20,
  semen_sire_name: null,
  breeding_date: addDays(TODAY, -30),
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
  doe_tag: "D-1",
  buck_tag: "B-1",
};

describe("BreedingPage — stale-data notice retry", () => {
  it("retries the list from the stale-data notice after a save", async () => {
    let listCalls = 0;
    let postCalls = 0;
    server.use(
      http.get("/api/breeding", () => {
        listCalls += 1;
        if (listCalls === 1) {
          return HttpResponse.json({
            records: [
              {
                ...PENDING_BASE,
                id: 3,
                doe_tag: "D-3",
              },
            ],
            candidate_availability: { eligible_doe_count: 3, eligible_buck_count: 1 },
            total: 1,
            limit: 50,
            offset: 0,
          });
        }
        return new HttpResponse(null, { status: 500 });
      }),
      http.post("/api/breeding", () => {
        postCalls += 1;
        return HttpResponse.json({ ...PENDING_BASE, id: 9 }, { status: 201 });
      }),
      http.get("/api/breeding/candidates", () =>
        HttpResponse.json({
          candidates: [
            { id: 10, tag_number: "D-1", name: null, age_months: 24, latest_weight_kg: null },
            { id: 20, tag_number: "B-1", name: null, age_months: 30, latest_weight_kg: null },
          ],
          total: 2,
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<BreedingPage />);
    await screen.findAllByText("D-3");
    // Happy render: no stale-data notice.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // A successful save invalidates the list; that refetch 500s while the
    // old rows stay on screen — the stale-data notice path.
    await user.click(screen.getAllByRole("button", { name: "Add breeding" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Add breeding" });
    await user.click(within(dialog).getAllByRole("combobox")[0]);
    const pickerDialog = await screen.findByRole("dialog");
    await user.click((await within(pickerDialog).findAllByRole("option"))[0]);
    await user.click(within(dialog).getByRole("radio", { name: /conventional semen/i }));
    await user.click(within(dialog).getByRole("button", { name: "Save breeding" }));
    await waitFor(() => expect(postCalls).toBe(1));

    const notice = await screen.findByRole("status");
    await user.click(within(notice).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(3));
  });
});
