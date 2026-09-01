/**
 * Kidding page — a second pass over the exact copy and field wiring the page
 * promises: the queue-total descriptions, the doe/kid fallbacks used when a
 * tag or an animal link is missing, the dashes and blanks of the due and
 * history tables, and the record dialog's labels, live kid counter, per-field
 * error wiring, in-flight/toast messaging and the payload it maps a cleared
 * or whitespace-only field to.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BreedingRecordOut, KiddingRecordOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday, formatDate } from "@/lib/format";

import KiddingPage from "./page";

const { navState } = vi.hoisted(() => ({ navState: { search: "" } }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/kidding",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const toastMock = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

type User = ReturnType<typeof userEvent.setup>;

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI Select relies on.
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

/** Farm-calendar fixture dates: the page's "Xd late" and the dialog's date
 * bounds both follow the active farm timezone. */
const TODAY = farmToday();
function daysFromToday(delta: number): string {
  return addDays(TODAY, delta);
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function makeBreeding(overrides: Partial<BreedingRecordOut>): BreedingRecordOut {
  return {
    id: 1,
    doe_id: 10,
    buck_id: 20,
    semen_sire_name: null,
    // 200 days back keeps the dialog's MIN_GESTATION_DAYS floor in the past.
    breeding_date: daysFromToday(-200),
    method: "NATURAL",
    heat_cycle_number: 1,
    ultrasound_date: daysFromToday(-160),
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
    notes: "big twins",
    kids: [],
    doe_tag: "G-010",
    ...overrides,
  };
}

const OVERDUE_REC = makeBreeding({ id: 11, expected_kidding_date: daysFromToday(-5) });
const UPCOMING_REC = makeBreeding({ id: 12, expected_kidding_date: daysFromToday(10) });
const HISTORY = makeKidding({
  id: 21,
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

describe("KiddingPage copy and field wiring", () => {
  let postBody: Record<string, unknown> | null;
  let pregnancyCalls: number;
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
    postBody = null;
    pregnancyCalls = 0;
    navState.search = "";
    toastMock.success.mockClear();
    toastMock.error.mockClear();
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
      http.get("/api/kidding", () => HttpResponse.json(payload)),
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

  // ---------- page-level copy and fallbacks ----------

  it("keeps the page shell up while permissions are still in flight", async () => {
    const gate = deferred();
    server.use(
      http.get("/api/auth/permissions", async () => {
        await gate.promise;
        return HttpResponse.json({ is_owner: true, permissions: ["kidding.view"] });
      }),
    );
    const { waitForAuthIdle } = renderWithProviders(<KiddingPage />);
    await waitForAuthIdle();

    // The real header and skeleton stand in for the page — not a bare
    // "Loading…" line.
    expect(screen.getByRole("heading", { level: 1, name: "Kidding" })).toBeInTheDocument();
    expect(
      screen.getByText("Confirmed pregnancies due soon and recent kidding history."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();

    gate.resolve();
    expect(await screen.findByText("Recent kiddings")).toBeInTheDocument();
  });

  it("falls back to a generic message when the list fails without a server detail", async () => {
    server.use(http.get("/api/kidding", () => HttpResponse.error()));
    renderWithProviders(<KiddingPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load kidding data.");
    expect(
      within(alert).getByRole("button", { name: "Retry kidding data" }),
    ).toBeInTheDocument();
  });

  it("falls back to a generic message when the linked pregnancy fails without a detail", async () => {
    server.use(http.get("/api/kidding/pregnancies/99", () => HttpResponse.error()));
    navState.search = "?breeding_id=99";
    await renderLoaded();

    expect(await screen.findByText("Could not load the linked pregnancy.")).toBeInTheDocument();
  });

  it("describes each due queue by its full-queue total", async () => {
    payload.overdue_total = 4;
    payload.upcoming_total = 7;
    await renderLoaded();

    expect(screen.getByText("4 overdue pregnancies in the full queue.")).toBeInTheDocument();
    expect(
      screen.getByText("7 confirmed pregnancies in the full 30-day queue."),
    ).toBeInTheDocument();
  });

  it("spells out how late each overdue pregnancy is", async () => {
    await renderLoaded();

    const row = within(card(/Overdue \(past expected date/)).getAllByRole("row")[1];
    expect(within(row).getAllByRole("cell")[1]).toHaveTextContent(
      /^was due \d{1,2} [A-Z][a-z]{2} \d{4} \(5d late\)$/,
    );
  });

  it("links an untagged doe by id from every table", async () => {
    payload.overdue = [makeBreeding({ id: 11, doe_tag: null, expected_kidding_date: daysFromToday(-5) })];
    payload.upcoming = [makeBreeding({ id: 12, doe_tag: null })];
    payload.records = [makeKidding({ id: 21, doe_tag: null })];
    await renderLoaded();

    for (const title of [
      /Overdue \(past expected date/,
      "Upcoming (next 30 days)",
      "Recent kiddings",
    ]) {
      const link = within(card(title)).getByRole("link", { name: "Doe #10" });
      expect(link).toHaveAttribute("href", "/animals/10");
    }
  });

  it("renders an untagged doe as plain text without animals.view", async () => {
    server.use(permissionsHandler(["kidding.view"]));
    payload.overdue = [makeBreeding({ id: 11, doe_tag: null, expected_kidding_date: daysFromToday(-5) })];
    payload.upcoming = [makeBreeding({ id: 12, doe_tag: null })];
    payload.records = [makeKidding({ id: 21, doe_tag: null })];
    await renderLoaded();

    for (const title of [
      /Overdue \(past expected date/,
      "Upcoming (next 30 days)",
      "Recent kiddings",
    ]) {
      const section = card(title);
      expect(within(section).queryByRole("link", { name: "Doe #10" })).not.toBeInTheDocument();
      expect(within(section).getByText("Doe #10")).toBeInTheDocument();
    }
  });

  it("dashes the days-left and kids-detected cells of an undated pregnancy", async () => {
    payload.overdue = [];
    payload.overdue_total = 0;
    payload.upcoming = [
      makeBreeding({ id: 12, expected_kidding_date: null, kid_count_detected: null }),
    ];
    await renderLoaded();

    const row = within(card("Upcoming (next 30 days)")).getAllByRole("row")[1];
    const cells = within(row).getAllByRole("cell");
    expect(cells[3]).toHaveTextContent(/^—$/);
    expect(cells[4]).toHaveTextContent(/^—$/);
  });

  it("leaves the notes cell of an unannotated kidding blank", async () => {
    payload.overdue = [];
    payload.overdue_total = 0;
    payload.records = [makeKidding({ id: 22, notes: null })];
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Recent kiddings");

    const row = within(card("Recent kiddings")).getAllByRole("row")[1];
    expect(within(row).getAllByRole("cell")[4]).toBeEmptyDOMElement();
  });

  it("lists kids inline with separators, spacing and an untagged-kid fallback", async () => {
    payload.overdue = [];
    payload.overdue_total = 0;
    payload.records = [
      makeKidding({
        id: 23,
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
            status: "DIED",
            mortality_reported_at: "2026-07-21",
            animal_id: 56,
          },
        ],
      }),
    ];
    await renderLoaded();

    const row = within(card("Recent kiddings")).getAllByRole("row")[1];
    const kidsCell = within(row).getAllByRole("cell")[3];
    expect(kidsCell).toHaveTextContent(/^G-101 \(Female, alive\), kid \(Male, died\)$/);
    expect(within(kidsCell).getByRole("link", { name: "kid" })).toHaveAttribute(
      "href",
      "/animals/56",
    );
  });

  it("dashes the kids cell when the record omits its kids entirely", async () => {
    payload.overdue = [];
    payload.overdue_total = 0;
    payload.records = [makeKidding({ id: 24, kids: undefined })];
    await renderLoaded();

    const row = within(card("Recent kiddings")).getAllByRole("row")[1];
    expect(within(row).getAllByRole("cell")[3]).toHaveTextContent(/^—$/);
  });

  it("opens a deep link already on the loaded page without refetching it", async () => {
    navState.search = "?breeding_id=12";
    renderWithProviders(<KiddingPage />);

    const dialog = await screen.findByRole("dialog", { name: "Record kidding" });
    expect(within(dialog).getByText(/Doe G-010 · due/)).toBeInTheDocument();
    expect(pregnancyCalls).toBe(0);
  });

  it("keeps the settled queues on screen while the next page loads", async () => {
    const gate = deferred();
    server.use(
      http.get("/api/kidding", async ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("upcoming_offset") ?? 0);
        if (offset > 0) await gate.promise;
        return HttpResponse.json({
          ...payload,
          upcoming_total: 60,
          upcoming_limit: 25,
          upcoming_offset: offset,
        });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(within(card("Upcoming (next 30 days)")).getByRole("button", { name: "Next" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Updating kidding queues…");
    expect(screen.getByText("Recent kiddings")).toBeInTheDocument();
    expect(screen.getByText("big twins")).toBeInTheDocument();

    gate.resolve();
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  // ---------- record dialog ----------

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(card("Upcoming (next 30 days)")).getByRole("button", { name: "Record kidding" }),
    );
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("describes an untagged doe by id and omits an unknown detected-kid count", async () => {
    payload.upcoming = [makeBreeding({ id: 12, doe_tag: null, kid_count_detected: null })];
    const { dialog } = await openDialog();

    expect(
      within(dialog).getByText(/^Doe #10 · due \d{1,2} [A-Z][a-z]{2} \d{4}$/),
    ).toBeInTheDocument();
  });

  it("labels the per-kid pickers and shows their chosen labels, not raw values", async () => {
    const { user, dialog } = await openDialog();

    expect(within(dialog).getByLabelText("Kid 1 sex")).toHaveTextContent("Female");
    expect(within(dialog).getByLabelText("Kid 1 status")).toHaveTextContent("Alive");
    expect(within(dialog).getByLabelText("Kid 2 sex")).toHaveTextContent("Female");

    await pickOption(user, within(dialog).getByLabelText("Kid 1 sex"), "Male");
    expect(within(dialog).getByLabelText("Kid 1 sex")).toHaveTextContent("Male");
  });

  it("counts the listed kid rows in the live region", async () => {
    const { user, dialog } = await openDialog();
    expect(within(dialog).getByText("2 kids listed")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Add kid" }));
    // Three listed vs two detected: the reconciliation note is appended.
    expect(within(dialog).getByText(new RegExp("3 kids listed"))).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Remove kid 3" }));
    await user.click(within(dialog).getByRole("button", { name: "Remove kid 2" }));
    expect(within(dialog).getByText(new RegExp("1 kid listed"))).toBeInTheDocument();
  });

  it("ties every field message to its own control for screen readers", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/kidding date/i), { target: { value: "" } });
    fireEvent.change(within(dialog).getByLabelText(/notes/i), {
      target: { value: "x".repeat(4001) },
    });
    fireEvent.change(within(dialog).getAllByPlaceholderText("auto")[0], {
      target: { value: "X".repeat(51) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await within(dialog).findByText("Pick a valid date");
    expect(within(dialog).getByLabelText(/kidding date/i)).toHaveAccessibleDescription(
      "Pick a valid date",
    );
    expect(within(dialog).getByLabelText(/notes/i)).toHaveAccessibleDescription(
      "Notes cannot exceed 4000 characters",
    );
    expect(
      within(dialog).getByLabelText("Kid 1 tag (auto if blank)"),
    ).toHaveAccessibleDescription("Max 50 characters");
    expect(
      within(dialog).getByLabelText("Kid 2 tag (auto if blank)"),
    ).not.toHaveAccessibleDescription();
    expect(postBody).toBeNull();
  });

  it("rejects a five-digit year that the date control still accepts", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/kidding date/i), {
      target: { value: "12026-08-24" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(await within(dialog).findByText("Pick a valid date")).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("posts a cleared birth weight as null rather than zero", async () => {
    const { user, dialog } = await openDialog();
    const weight = within(dialog).getAllByRole("spinbutton")[0];
    fireEvent.change(weight, { target: { value: "3" } });
    fireEvent.change(weight, { target: { value: "" } });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect((postBody!.kids as Record<string, unknown>[])[0].birth_weight).toBeNull();
  });

  it("treats whitespace-only tags and notes as blank", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getAllByPlaceholderText("auto")[0], {
      target: { value: "   " },
    });
    fireEvent.change(within(dialog).getByLabelText(/notes/i), { target: { value: "   " } });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.notes).toBeNull();
    expect((postBody!.kids as Record<string, unknown>[])[0].tag).toBeNull();
  });

  it("bounds the mortality date by the kidding date on screen", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/kidding date/i), {
      target: { value: daysFromToday(-3) },
    });
    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Died");

    const mortality = within(dialog).getByLabelText("Kid 1 mortality date *");
    expect(mortality).toHaveAttribute("min", daysFromToday(-3));
    expect(mortality).toHaveAttribute("max", TODAY);
  });

  it("waits for a submit before flagging a newly died kid's mortality date", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Died");

    const mortality = within(dialog).getByLabelText("Kid 1 mortality date *");
    expect(mortality).not.toHaveAttribute("aria-invalid");
    fireEvent.change(mortality, { target: { value: TODAY } });
    expect(within(dialog).queryByText("Mortality date is required")).not.toBeInTheDocument();
    expect(mortality).not.toHaveAccessibleDescription();
  });

  it("flags only the kid row whose mortality date is missing", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Died");
    await pickOption(user, within(dialog).getByLabelText("Kid 2 status"), "Died");
    fireEvent.change(within(dialog).getByLabelText("Kid 1 mortality date *"), {
      target: { value: TODAY },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await within(dialog).findByText("Mortality date is required");
    expect(
      within(dialog).getByLabelText("Kid 2 mortality date *"),
    ).toHaveAccessibleDescription("Mortality date is required");
    expect(
      within(dialog).getByLabelText("Kid 1 mortality date *"),
    ).not.toHaveAccessibleDescription();
    expect(postBody).toBeNull();
  });

  it("clears a stale mortality date when a kid leaves DIED and returns", async () => {
    const { user, dialog } = await openDialog();
    const status = () => within(dialog).getByLabelText("Kid 1 status");
    await pickOption(user, status(), "Died");
    fireEvent.change(within(dialog).getByLabelText("Kid 1 mortality date *"), {
      target: { value: TODAY },
    });

    await pickOption(user, status(), "Stillborn");
    expect(within(dialog).queryByLabelText("Kid 1 mortality date *")).not.toBeInTheDocument();
    await pickOption(user, status(), "Died");
    expect(within(dialog).getByLabelText("Kid 1 mortality date *")).toHaveValue("");

    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    expect(await within(dialog).findByText("Mortality date is required")).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("retires the mortality error as soon as the kid leaves DIED", async () => {
    const { user, dialog } = await openDialog();
    const status = () => within(dialog).getByLabelText("Kid 1 status");
    await pickOption(user, status(), "Died");
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    await within(dialog).findByText("Mortality date is required");

    await pickOption(user, status(), "Alive");
    await pickOption(user, status(), "Died");

    expect(within(dialog).queryByText("Mortality date is required")).not.toBeInTheDocument();
    expect(
      within(dialog).getByLabelText("Kid 1 mortality date *"),
    ).not.toHaveAccessibleDescription();
  });

  it("re-derives the kidding-date floor when the pregnancy's scan date arrives", async () => {
    payload.upcoming = [makeBreeding({ id: 12 })];
    navState.search = "?breeding_id=12";
    const { queryClient } = renderWithProviders(<KiddingPage />);
    const dialog = await screen.findByRole("dialog", { name: "Record kidding" });
    const dateInput = within(dialog).getByLabelText(/kidding date/i);
    expect(dateInput).toHaveAttribute("min", daysFromToday(-100));

    payload.upcoming = [makeBreeding({ id: 12, ultrasound_result_date: daysFromToday(-10) })];
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["/api/kidding"] });
    });
    await waitFor(() => expect(dateInput).toHaveAttribute("min", daysFromToday(-10)));

    fireEvent.change(dateInput, { target: { value: daysFromToday(-50) } });
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(
      await within(dialog).findByText(
        `Kidding date cannot be before ${formatDate(daysFromToday(-10))}`,
      ),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("shows the saving label and retires the previous error while a retry is in flight", async () => {
    const gate = deferred();
    let calls = 0;
    server.use(
      http.post("/api/kidding", async () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json({ detail: "kidding already recorded" }, { status: 409 });
        }
        await gate.promise;
        return HttpResponse.json(makeKidding({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    await within(dialog).findByText("kidding already recorded");

    await user.click(within(dialog).getByRole("button", { name: "Retry save kidding" }));

    expect(await within(dialog).findByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(within(dialog).queryByText("kidding already recorded")).not.toBeInTheDocument();

    gate.resolve();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("toasts the recorded kidding once it is saved", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Delivery recorded."));
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it("reports a detail-less save failure in both the form and a toast", async () => {
    server.use(http.post("/api/kidding", () => HttpResponse.error()));
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(await within(dialog).findByText("Something went wrong")).toBeInTheDocument();
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("Something went wrong"));
    expect(toastMock.success).not.toHaveBeenCalled();
  });
});
