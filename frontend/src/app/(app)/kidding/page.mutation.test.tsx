/**
 * Kidding page mutation hardening (companion to page.record-guards.test.tsx):
 * pins the exact strings and branch outcomes that Stryker survivors hovered
 * on — the deep-link id vocabulary, the mortality-date validation messages,
 * the gestation ceiling message, tag/notes trimming in the POST payload, the
 * ultrasound reconciliation hint, kid status option labels, the mortality
 * field's DIED-only visibility, the stale deep-link notice, permission-gated
 * copy (breeding.view, kidding.manage) and the loading/error states.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, HttpResponse, http } from "msw";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect } from "react";

import type {
  BreedingRecordOut,
  KiddingListOut,
  KiddingRecordOut,
} from "@/api/generated/models";
import { useAuth } from "@/lib/auth-context";
import { addDays, farmToday, formatDate } from "@/lib/format";
import { ALL_PERMISSIONS, permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import KiddingPage, { kiddingSchema } from "./page";
import { farmVocabulary } from "@/lib/farm-vocabulary";

const { navState, toastMock } = vi.hoisted(() => ({
  navState: { search: "" },
  toastMock: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("sonner", () => ({ toast: toastMock }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/kidding",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

type User = ReturnType<typeof userEvent.setup>;

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
function daysFromToday(delta: number): string {
  return addDays(TODAY, delta);
}
const BRED_ON = daysFromToday(-120);

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
    parity: null,
    placenta_passed: null,
    mastitis_suspected: false,
    notes: null,
    kids: [],
    doe_tag: "G-010",
    ...overrides,
  };
}

const UPCOMING_REC = makeBreeding({ id: 12, expected_kidding_date: daysFromToday(10) });
const HISTORY = makeKidding({ id: 21, kids: [] });

function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  return user.click(trigger).then(async () => {
    await user.click(await screen.findByRole("option", { name }));
  });
}

function card(title: string | RegExp): HTMLElement {
  return screen.getByText(title).closest("[data-slot='card']") as HTMLElement;
}

/** Queue content also renders in below-md card lists (md:hidden) inside the
 * same section card — scope to the desktop table for unambiguous lookups. */
function desktopScope(sectionCard: HTMLElement) {
  const table = sectionCard.querySelector('[class~="md:block"] table');
  expect(table).not.toBeNull();
  return within(table as HTMLElement);
}

describe("KiddingPage mutation hardening", () => {
  let postBody: Record<string, unknown> | null;
  let pregnancyCalls: number[];
  let payload: KiddingListOut;

  beforeEach(() => {
    postBody = null;
    pregnancyCalls = [];
    toastMock.success.mockClear();
    toastMock.error.mockClear();
    payload = {
      records: [HISTORY],
      upcoming: [UPCOMING_REC],
      upcoming_total: 1,
      upcoming_limit: 25,
      upcoming_offset: 0,
      overdue: [],
      overdue_total: 0,
      overdue_limit: 25,
      overdue_offset: 0,
      total: 1,
      limit: 50,
      offset: 0,
    };
    server.use(
      http.get("/api/kidding", () => HttpResponse.json(payload)),
      http.get("/api/kidding/pregnancies/:recordId", ({ params }) => {
        pregnancyCalls.push(Number(params.recordId));
        const record = payload.upcoming.find(
          (candidate) => candidate.id === Number(params.recordId),
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

  afterEach(() => {
    navState.search = "";
  });

  async function renderLoaded() {
    renderWithProviders(<KiddingPage />);
    await screen.findByText("Recent kiddings");
    await screen.findByText("Upcoming (next 30 days)");
  }

  async function openDialog(breeding: BreedingRecordOut = UPCOMING_REC) {
    const user = userEvent.setup();
    payload.upcoming = [breeding];
    payload.upcoming_total = 1;
    await renderLoaded();
    await user.click(
      desktopScope(card("Upcoming (next 30 days)")).getByRole("button", { name: "Record kidding" }),
    );
    return { user, dialog: await screen.findByRole("dialog") };
  }

  // ---------- URL id parsing ----------

  it.each(["abc", "-3", "12x", "0", "99999999999999999999"])(
    "ignores a breeding_id that is not a positive safe integer (%s)",
    async (raw) => {
      // The linked pregnancy sits in the queue, so only the id parse can keep
      // its dialog shut — and no detail fetch may be attempted either.
      navState.search = `?breeding_id=${raw}`;
      await renderLoaded();

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(pregnancyCalls).toEqual([]);
    },
  );

  it("keeps the page quiet with no breeding_id at all", async () => {
    await renderLoaded();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(pregnancyCalls).toEqual([]);
  });

  // ---------- gestation ceiling ----------

  it("caps the kidding date at the gestation maximum when it predates today", async () => {
    // Bred 250 days ago: ceiling is breeding+200 = today−50 (not today).
    const { user, dialog } = await openDialog(
      makeBreeding({ id: 12, breeding_date: daysFromToday(-250) }),
    );
    const ceiling = daysFromToday(-50);
    const date = within(dialog).getByLabelText(/kidding date/i);
    expect(date).toHaveAttribute("max", ceiling);

    fireEvent.change(date, { target: { value: daysFromToday(-10) } });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(
      await within(dialog).findByText(
        `Kidding date cannot be after ${formatDate(ceiling)} (gestation over 200 days)`,
      ),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  // ---------- mortality date validation ----------

  async function openDialogWithDiedKid() {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Died");
    const mortality = within(dialog).getByLabelText("Kid 1 mortality date *");
    return { user, dialog, mortality };
  }

  it("rejects a mortality date before the kidding date", async () => {
    const { user, dialog, mortality } = await openDialogWithDiedKid();
    fireEvent.change(mortality, { target: { value: "2020-01-01" } });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(
      await within(dialog).findByText("Can't be before the kidding date"),
    ).toBeInTheDocument();
    expect(mortality).toHaveAttribute("aria-invalid", "true");
    expect(postBody).toBeNull();
  });

  it("rejects a mortality date in the future", async () => {
    const { user, dialog, mortality } = await openDialogWithDiedKid();
    fireEvent.change(mortality, { target: { value: daysFromToday(1) } });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(await within(dialog).findByText("Date can't be in the future")).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("requires a mortality date for a died kid", async () => {
    const { user, dialog } = await openDialogWithDiedKid();
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(await within(dialog).findByText("Mortality date is required")).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("shows the mortality date field only for a died kid", async () => {
    const { user, dialog } = await openDialog();
    // Default (ALIVE) and STILLBORN rows never expose the mortality input.
    expect(within(dialog).queryByLabelText(/mortality date/i)).not.toBeInTheDocument();
    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Stillborn");
    expect(within(dialog).queryByLabelText(/mortality date/i)).not.toBeInTheDocument();

    await pickOption(user, within(dialog).getByLabelText("Kid 1 status"), "Died");
    expect(within(dialog).getByLabelText("Kid 1 mortality date *")).toBeInTheDocument();
  });

  // ---------- per-kid field error text ----------

  it("spells out the kid tag and weight error messages", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Kid 1 tag (auto if blank)"), {
      target: { value: "T".repeat(51) },
    });
    fireEvent.change(within(dialog).getByLabelText("Kid 1 weight (kg)"), {
      target: { value: "0.2" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    expect(await within(dialog).findByText("Max 50 characters")).toBeInTheDocument();
    expect(within(dialog).getByText("A newborn kid weighs at least 0.5 kg")).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText("Kid 1 weight (kg)"), {
      target: { value: "9" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    expect(await within(dialog).findByText("A newborn kid weighs at most 8 kg")).toBeInTheDocument();
  });

  // ---------- select option labels ----------

  it("offers the ease options under their display labels", async () => {
    const user = userEvent.setup();
    const { dialog } = await openDialog();
    await user.click(within(dialog).getByLabelText("Ease"));

    expect(await screen.findByRole("option", { name: "Normal" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Assisted" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Difficult" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "NORMAL" })).not.toBeInTheDocument();
  });

  it("offers the kid status options under their display labels", async () => {
    const user = userEvent.setup();
    const { dialog } = await openDialog();
    await user.click(within(dialog).getByLabelText("Kid 1 status"));

    expect(await screen.findByRole("option", { name: "Alive" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Stillborn" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Died" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "ALIVE" })).not.toBeInTheDocument();
  });

  // ---------- POST payload trimming ----------

  it("trims kid tags and notes before sending, blank becoming null", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Kid 1 tag (auto if blank)"), {
      target: { value: "  G-101  " },
    });
    fireEvent.change(within(dialog).getByLabelText("Kid 2 tag (auto if blank)"), {
      target: { value: "   " },
    });
    fireEvent.change(within(dialog).getByLabelText(/notes/i), {
      target: { value: "  clean note  " },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.notes).toBe("clean note");
    expect(postBody!.kids).toEqual([
      expect.objectContaining({ tag: "G-101" }),
      expect.objectContaining({ tag: null }),
    ]);
  });

  it("sends null notes when the field is only whitespace", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/notes/i), {
      target: { value: "   " },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.notes).toBeNull();
  });

  it("closes the dialog after a successful save", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  // ---------- ultrasound reconciliation ----------

  it("hints when the listed rows disagree with the ultrasound count", async () => {
    const { user, dialog } = await openDialog(
      makeBreeding({ id: 12, kid_count_detected: 3 }),
    );

    // Default is two rows against a scan of three.
    expect(
      within(dialog).getByText(
        /2 kids listed — ultrasound detected 3\. Reconcile the difference or note the reason\./,
      ),
    ).toBeInTheDocument();

    // Growing to the scanned count retires the hint.
    await user.click(within(dialog).getByRole("button", { name: "Add kid" }));
    expect(within(dialog).getByText(/3 kids listed/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Reconcile the difference/)).not.toBeInTheDocument();
  });

  it("stays quiet when the scan count is unknown", async () => {
    const { dialog } = await openDialog(makeBreeding({ id: 12, kid_count_detected: null }));
    expect(within(dialog).getByText(/2 kids listed/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Reconcile the difference/)).not.toBeInTheDocument();
  });

  it("quotes the detected count in the dialog header", async () => {
    const { dialog } = await openDialog(makeBreeding({ id: 12, kid_count_detected: 3 }));
    expect(within(dialog).getByText(/\(3 detected\)/)).toBeInTheDocument();
  });

  it("says nothing about detected kids without a scan", async () => {
    const { dialog } = await openDialog(makeBreeding({ id: 12, kid_count_detected: null }));
    expect(within(dialog).queryByText(/detected/)).not.toBeInTheDocument();
  });

  // ---------- species copy ----------

  it("describes goat young staying with the dam in the recovery bucket", async () => {
    const { dialog } = await openDialog();
    expect(
      within(dialog).getByText(/raised alongside the dam in the RECOVERY bucket/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/sexed young-stock pens/)).not.toBeInTheDocument();
  });

  // ---------- stale deep links ----------

  it("explains a deep link to a pregnancy that already kidded", async () => {
    payload.upcoming = [makeBreeding({ id: 12, has_kidding: true })];
    navState.search = "?breeding_id=12";
    await renderLoaded();

    expect(
      screen.getByText("Pregnancy #12 already has a kidding recorded."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear link" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("explains a deep link to a pregnancy that is no longer confirmed", async () => {
    payload.upcoming = [makeBreeding({ id: 12, outcome: "FAILED" })];
    navState.search = "?breeding_id=12";
    await renderLoaded();

    expect(
      screen.getByText("Pregnancy #12 is no longer confirmed pregnant — no kidding to record."),
    ).toBeInTheDocument();
  });

  it("clears the stale-link notice with the Clear link button", async () => {
    payload.upcoming = [makeBreeding({ id: 12, has_kidding: true })];
    navState.search = "?breeding_id=12";
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Clear link" }));
    await waitFor(() =>
      expect(screen.queryByText(/already has a kidding recorded/)).not.toBeInTheDocument(),
    );
  });

  it("keeps the stale-link notice behind the kidding.manage gate", async () => {
    server.use(permissionsHandler(["kidding.view"]));
    payload.upcoming = [makeBreeding({ id: 12, has_kidding: true })];
    navState.search = "?breeding_id=12";
    await renderLoaded();

    expect(screen.queryByText(/already has a kidding recorded/)).not.toBeInTheDocument();
  });

  it("retires a deep link only after its own dialog is dismissed", async () => {
    navState.search = "?breeding_id=12";
    const user = userEvent.setup();
    await renderLoaded();

    const dialog = await screen.findByRole("dialog", { name: "Record kidding" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // Dismissal is latched: the same URL does not reopen it.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("re-arms a dismissed deep link after the id leaves the URL", async () => {
    navState.search = "?breeding_id=12";
    const user = userEvent.setup();
    const view = renderWithProviders(<KiddingPage />);
    const dialog = await screen.findByRole("dialog", { name: "Record kidding" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // Navigate away, then back to the same pregnancy: the dismissal must not
    // outlive the URL intent that produced it.
    navState.search = "";
    view.rerender(<KiddingPage />);
    await screen.findByText("Recent kiddings");

    navState.search = "?breeding_id=12";
    view.rerender(<KiddingPage />);
    expect(await screen.findByRole("dialog", { name: "Record kidding" })).toBeInTheDocument();
  });

  // ---------- loading / error states ----------

  it("announces loading while the kidding data is on the wire", async () => {
    let releaseList!: () => void;
    const listGate = new Promise<void>((resolve) => {
      releaseList = resolve;
    });
    server.use(
      http.get("/api/kidding", async () => {
        await listGate;
        return HttpResponse.json(payload);
      }),
    );
    renderWithProviders(<KiddingPage />);

    expect(await screen.findByText("Loading kidding data…")).toBeInTheDocument();
    releaseList();
    await screen.findByText("Recent kiddings");
  });

  it("shows a plain-language error and retries a failed list load", async () => {
    let fail = true;
    server.use(
      http.get("/api/kidding", () =>
        fail ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : HttpResponse.json(payload),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<KiddingPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("boom");
    expect(screen.getByRole("button", { name: "Retry kidding data" })).toBeInTheDocument();

    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry kidding data" }));
    expect(await screen.findByText("Recent kiddings")).toBeInTheDocument();
  });

  it("falls back to a generic message for a non-API list failure", async () => {
    server.use(
      http.get("/api/kidding", () => HttpResponse.error()),
    );
    renderWithProviders(<KiddingPage />);

    expect(await screen.findByText("Could not load kidding data.")).toBeInTheDocument();
  });

  it("recovers the page from a permissions failure via in-place retry", async () => {
    let fail = true;
    let permissionCalls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        permissionCalls += 1;
        return fail
          ? HttpResponse.json({ detail: "nope" }, { status: 500 })
          : HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<KiddingPage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    await waitFor(() => expect(permissionCalls).toBeGreaterThanOrEqual(1));

    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry permissions" }));
    expect(await screen.findByText("Recent kiddings")).toBeInTheDocument();
  });

  // ---------- permission-gated copy ----------

  it("links to Breeding from the empty upcoming state only with breeding.view", async () => {
    payload.upcoming = [];
    payload.upcoming_total = 0;
    await renderLoaded();
    const link = screen.getByRole("link", { name: "Go to Breeding" });
    expect(link).toHaveAttribute("href", "/breeding");
  });

  it("omits the Go to Breeding shortcut without breeding.view", async () => {
    server.use(permissionsHandler(["kidding.view", "kidding.manage"]));
    payload.upcoming = [];
    payload.upcoming_total = 0;
    await renderLoaded();

    expect(screen.queryByRole("link", { name: "Go to Breeding" })).not.toBeInTheDocument();
  });

  it("keeps the record buttons and dialog off the page without kidding.manage", async () => {
    server.use(permissionsHandler(["kidding.view"]));
    await renderLoaded();

    expect(screen.queryByRole("button", { name: "Record kidding" })).not.toBeInTheDocument();
    navState.search = "?breeding_id=12";
  });

  // ---------- round 2: remaining mutation survivors ----------

  it("opens a recordable deep link without the stale-link notice", async () => {
    navState.search = "?breeding_id=12";
    await renderLoaded();

    expect(await screen.findByRole("dialog", { name: "Record kidding" })).toBeInTheDocument();
    expect(screen.queryByText(/already has a kidding recorded/)).not.toBeInTheDocument();
    expect(screen.queryByText(/no longer confirmed pregnant/)).not.toBeInTheDocument();
  });

  it("describes the page in the perms skeleton, the loading branch and the cards", async () => {
    const description = "Confirmed pregnancies due soon and recent kidding history.";

    // Permissions skeleton branch (goat vocabulary needs no farms fetch).
    server.use(
      http.get("/api/auth/permissions", async () => {
        await delay(400);
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );
    const { unmount } = renderWithProviders(<KiddingPage />);
    expect(screen.getByText(description)).toBeInTheDocument();
    unmount();

    // Data-loading branch: perms resolve immediately, the list hangs.
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS }),
      ),
      http.get("/api/kidding", () => new Promise(() => {})),
    );
    const second = renderWithProviders(<KiddingPage />);
    expect(await screen.findByText("Loading kidding data…")).toBeInTheDocument();
    expect(screen.getByText(description)).toBeInTheDocument();
    second.unmount();

    // Loaded branch.
    server.use(http.get("/api/kidding", () => HttpResponse.json(payload)));
    renderWithProviders(<KiddingPage />);
    expect(await screen.findByText("Recent kiddings")).toBeInTheDocument();
    expect(screen.getByText(description)).toBeInTheDocument();
  });

  it("describes the recent-kiddings card and its empty state", async () => {
    await renderLoaded();
    expect(
      screen.getByText("Latest recorded kiddings with ease and kids outcomes."),
    ).toBeInTheDocument();
  });

  it("describes the recent-kiddings empty state", async () => {
    payload.records = [];
    payload.total = 0;
    await renderLoaded();
    expect(screen.getByText("No kiddings recorded yet.")).toBeInTheDocument();
    expect(
      screen.getByText("Record a kidding from the upcoming list once a doe delivers."),
    ).toBeInTheDocument();
  });

  it("counts the listed rows with singular and plural nouns", async () => {
    const { user, dialog } = await openDialog();
    expect(within(dialog).getByText("2 kids listed")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Remove kid 2" }));
    // Singular noun, and the mismatch against the detected scan of 2 is
    // spelled out on the same line.
    expect(
      within(dialog).getByText(/^1 kid listed — ultrasound detected 2\. Reconcile the difference or note the reason\.$/),
    ).toBeInTheDocument();
  });

  it("keeps the dialog open when Escape lands while the save is in flight", async () => {
    let resolvePost!: (value: Response) => void;
    server.use(
      http.post("/api/kidding", () => new Promise<Response>((resolve) => {
        resolvePost = resolve;
      })),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    expect(within(dialog).getByRole("button", { name: /Saving…/ })).toBeDisabled();

    await user.keyboard("{Escape}");
    expect(within(dialog).getByRole("button", { name: /Saving…/ })).toBeInTheDocument();

    resolvePost(HttpResponse.json(makeKidding({ id: 99 }), { status: 201 }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("silences the delivery continuation after a farm switch", async () => {
    let resolvePost!: (value: Response) => void;
    server.use(
      http.post("/api/kidding", () => new Promise<Response>((resolve) => {
        resolvePost = resolve;
      })),
    );
    let farmSwitcher: ((id: number) => void) | null = null;
    function FarmSwitchProbe() {
      const { selectFarm } = useAuth();
      useEffect(() => {
        farmSwitcher = selectFarm;
      }, [selectFarm]);
      return null;
    }
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <FarmSwitchProbe />
        <KiddingPage />
      </>,
    );
    await screen.findByText("Recent kiddings");
    await user.click(
      desktopScope(card("Upcoming (next 30 days)")).getByRole("button", { name: "Record kidding" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Record kidding" });
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));
    await waitFor(() => expect(resolvePost).toBeDefined());

    expect(farmSwitcher).not.toBeNull();
    farmSwitcher!(2);
    resolvePost(HttpResponse.json(makeKidding({ id: 99 }), { status: 201 }));

    await waitFor(() => expect(toastMock.error).not.toHaveBeenCalled());
    expect(toastMock.success).not.toHaveBeenCalledWith("Delivery recorded.");
  });
});

afterAll(() => {
  vi.restoreAllMocks();
});


describe("kiddingSchema — regex and message anchors", () => {
  const schema = kiddingSchema(farmVocabulary);
  const kid = { tag: "", sex: "F" as const, birth_weight: 2.5, status: "ALIVE" as const, mortality_reported_at: "" };
  const base = { date: "2026-07-20", ease: "NORMAL" as const, notes: "", kids: [kid] };

  it("rejects trailing garbage behind a valid date prefix", () => {
    expect(schema.safeParse({ ...base, date: "2026-07-20x" }).success).toBe(false);
  });

  it("carries the litter bounds as operator-readable messages", () => {
    const empty = schema.safeParse({ ...base, kids: [] });
    expect(empty.success).toBe(false);
    if (!empty.success) {
      expect(empty.error.issues.map((i) => i.message)).toContain(
        `At least one ${farmVocabulary.young}`,
      );
    }
    const big = schema.safeParse({
      ...base,
      kids: Array.from({ length: farmVocabulary.facts.maxLitterSize + 1 }, () => kid),
    });
    expect(big.success).toBe(false);
    if (!big.success) {
      expect(big.error.issues.map((i) => i.message)).toContain(
        `A ${farmVocabulary.parturition} delivers at most ${farmVocabulary.facts.maxLitterSize} ${farmVocabulary.youngPlural} on this farm`,
      );
    }
  });
});
