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
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BreedingRecordOut, KiddingRecordOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import KiddingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/kidding",
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
function daysFromToday(delta: number): string {
  return localISO(new Date(Date.now() + delta * 86_400_000));
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
    ultrasound_done: true,
    pregnant: true,
    kid_count_detected: 2,
    expected_kidding_date: daysFromToday(10),
    outcome: "CONFIRMED_PREGNANT",
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
    { id: 1, tag: "G-101", sex: "F", birth_weight: 2.4, status: "ALIVE", animal_id: 55 },
    { id: 2, tag: null, sex: "M", birth_weight: null, status: "STILLBORN", animal_id: null },
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
    overdue: BreedingRecordOut[];
  };

  beforeEach(() => {
    listCalls = 0;
    postBody = null;
    payload = { records: [HISTORY], upcoming: [UPCOMING_REC], overdue: [OVERDUE_REC] };
    server.use(
      http.get("/api/kidding", () => {
        listCalls += 1;
        return HttpResponse.json(payload);
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

  it("renders the overdue card with days late and a record button", async () => {
    await renderLoaded();
    expect(
      screen.getByText("⚠ Overdue (past expected date, no kidding recorded)"),
    ).toBeInTheDocument();
    expect(screen.getByText("(5d late)")).toBeInTheDocument();
    const card = screen.getByText(/⚠ Overdue/).closest("[data-slot='card']") as HTMLElement;
    const link = within(card).getByRole("link", { name: "G-010" });
    expect(link).toHaveAttribute("href", "/animals/10");
    expect(
      within(card).getByRole("button", { name: "Record kidding" }),
    ).toBeInTheDocument();
  });

  it("hides the overdue card when nothing is overdue", async () => {
    payload.overdue = [];
    renderWithProviders(<KiddingPage />);
    await screen.findByText("big twins");
    expect(screen.queryByText(/⚠ Overdue/)).not.toBeInTheDocument();
  });

  it("renders upcoming pregnancies with days left and kids detected", async () => {
    await renderLoaded();
    expect(screen.getByText("Upcoming (next 30 days)")).toBeInTheDocument();
    const section = screen.getByText("Upcoming (next 30 days)").parentElement!;
    expect(within(section).getByText("10")).toBeInTheDocument(); // days left
    expect(within(section).getByText("2")).toBeInTheDocument(); // kids detected
    expect(
      within(section).getByRole("button", { name: "Record kidding" }),
    ).toBeInTheDocument();
  });

  it("shows a dash for days left when the expected date is missing", async () => {
    payload.upcoming = [makeBreeding({ id: 13, expected_kidding_date: null, kid_count_detected: null })];
    payload.overdue = [];
    renderWithProviders(<KiddingPage />);
    const section = (await screen.findByText("Upcoming (next 30 days)")).parentElement!;
    expect(within(section).getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("shows the upcoming empty state when nothing is due", async () => {
    payload.upcoming = [];
    payload.overdue = [];
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
    renderWithProviders(<KiddingPage />);
    const section = (await screen.findByText("Recent kiddings")).parentElement!;
    const cell = within(section).getByText("—");
    expect(cell).toBeInTheDocument();
  });

  it("shows the history empty state when no kiddings exist", async () => {
    payload.records = [];
    payload.overdue = [];
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
    const section = screen.getByText("Upcoming (next 30 days)").parentElement!;
    await user.click(within(section).getByRole("button", { name: "Record kidding" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("opens with two kid rows (twins default) and the due-date description", async () => {
    const { dialog } = await openDialog();
    expect(
      within(dialog).getByText(/Doe G-010 · due .+ \(2 detected\)/),
    ).toBeInTheDocument();
    expect(within(dialog).getAllByPlaceholderText("auto")).toHaveLength(2);
    expect(within(dialog).getByLabelText(/kidding date/i)).toHaveValue(TODAY);
    expect(within(dialog).getByLabelText(/kidding date/i)).toHaveAttribute("max", TODAY);
  });

  it("adds kid rows up to 10, then disables the add button", async () => {
    const { user, dialog } = await openDialog();
    const add = within(dialog).getByRole("button", { name: "+ Add kid" });
    for (let i = 0; i < 7; i += 1) await user.click(add);
    expect(within(dialog).getAllByPlaceholderText("auto")).toHaveLength(9);
    await user.click(add);
    expect(within(dialog).getAllByPlaceholderText("auto")).toHaveLength(10);
    expect(add).toBeDisabled();
  });

  it("removes kid rows but never below one", async () => {
    const { user, dialog } = await openDialog();
    const removeButtons = () => within(dialog).getAllByRole("button", { name: "✕" });
    await user.click(removeButtons()[1]);
    expect(within(dialog).getAllByPlaceholderText("auto")).toHaveLength(1);
    expect(removeButtons()[0]).toBeDisabled();
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
    expect(await within(dialog).findByText("Must be ≥ 0")).toBeInTheDocument();
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
      date: TODAY,
      ease: "NORMAL",
      notes: "easy birth",
    });
    expect(postBody!.kids).toEqual([
      { tag: "G-201", sex: "F", birth_weight: 2.5, status: "ALIVE" },
      { tag: null, sex: "F", birth_weight: null, status: "ALIVE" },
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
    await user.click(within(dialog).getByRole("button", { name: "Save kidding" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.ease).toBe("DIFFICULT");
    expect((postBody!.kids as { sex: string; status: string }[])[0]).toMatchObject({
      sex: "M",
      status: "DIED",
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

  it("Cancel closes the dialog without posting", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(postBody).toBeNull();
  });
});
