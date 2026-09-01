/**
 * Purchases page branch guards (companion to page.extended.test.tsx): what the
 * page puts on the wire and what it draws while it waits — the list/detail
 * request bounds, the gates in front of the permission and list queries, the
 * fallbacks for counters the API omits, the pluralised card description, the
 * empty-state action gate, the detail header's separators, and the new-batch
 * flow's boundary validation, consequence copy, whitespace trims and aria
 * wiring.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { farmToday } from "@/lib/format";
import { ALL_PERMISSIONS, permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import PurchasesPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/purchases",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

type User = ReturnType<typeof userEvent.setup>;

function localToday(): string {
  return farmToday();
}

/** Server-side hold: the response waits until the test releases it, so the
 *  in-flight window is a state the assertions can stand still in. */
function responseGate() {
  let open!: () => void;
  const held = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { held, open: () => open() };
}

/** Collapse the JSX text nodes of one element so a header's separators and
 *  spacing can be asserted exactly rather than by substring. */
function joinedText(el: HTMLElement): string {
  return (el.textContent ?? "").replace(/\s+/g, " ").trim();
}

const BATCH_FULL = {
  id: 5,
  date: "2026-01-05",
  supplier: "Sharma Goat Farm",
  count: 50,
  sex: "F",
  avg_age_months: 8,
  avg_weight_kg: 18.5,
  total_price: 150000,
  notes: "foundation stock",
  animals_created: 50,
  open_tasks: 3,
};

/** Both counters are optional in PurchaseBatchOut, and supplier/price are
 * nullable — a batch recorded with none of them still has to render. */
const BATCH_BARE = {
  id: 6,
  date: "2026-02-10",
  supplier: null,
  count: 20,
  sex: "M",
  avg_age_months: null,
  avg_weight_kg: null,
  total_price: null,
  notes: null,
};

const ANIMAL_STUB = {
  id: 11,
  tag_number: "B5-001",
  sex: "F",
  current_bucket: "QUARANTINE",
  status: "ACTIVE",
};

let listUrls: string[] = [];
let detailUrls: string[] = [];

beforeEach(() => {
  listUrls = [];
  detailUrls = [];
});

function listHandler(batches: unknown[], total = batches.length) {
  return http.get("/api/purchases", ({ request }) => {
    listUrls.push(request.url);
    return HttpResponse.json({ batches, total, limit: 50, offset: 0 });
  });
}

/** One handler for every batch id: the page must pick which one it asks for. */
function detailHandler(animalsTotal = 1) {
  return http.get("/api/purchases/:batchId", ({ request, params }) => {
    detailUrls.push(request.url);
    const requested = new URL(request.url).searchParams.get("animals_offset");
    return HttpResponse.json({
      batch: String(params.batchId) === "6" ? BATCH_BARE : BATCH_FULL,
      animals: [ANIMAL_STUB],
      tasks: [],
      animals_total: animalsTotal,
      animals_limit: 100,
      animals_offset: Number(requested ?? "0"),
    });
  });
}

async function renderLoaded() {
  renderWithProviders(<PurchasesPage />);
  expect(await screen.findByText("#5")).toBeInTheDocument();
}

function row(id: string): HTMLElement {
  return screen.getByText(id).closest("tr") as HTMLElement;
}

function lastDetailUrl(): URL {
  return new URL(detailUrls[detailUrls.length - 1]);
}

describe("PurchasesPage list request and states", () => {
  beforeEach(() => {
    server.use(listHandler([BATCH_FULL, BATCH_BARE], 2));
  });

  it("asks for the first page with the bounds the list endpoint expects", async () => {
    await renderLoaded();

    const asked = new URL(listUrls[0]);
    expect(asked.pathname).toBe("/api/purchases");
    expect(asked.searchParams.get("limit")).toBe("50");
    expect(asked.searchParams.get("offset")).toBe("0");
  });

  it("falls back to 0 for the animal and open-task counters the API omits", async () => {
    await renderLoaded();

    expect(within(row("#5")).getAllByRole("cell")[7]).toHaveTextContent(/^50$/);
    const bare = within(row("#6")).getAllByRole("cell");
    expect(bare[7]).toHaveTextContent(/^0$/);
    expect(bare[8]).toHaveTextContent(/^0$/);
  });

  it("describes the card by the API total, singular for a lone batch", async () => {
    server.use(listHandler([BATCH_FULL], 1));
    renderWithProviders(<PurchasesPage />);

    expect(await screen.findByText("1 batch recorded")).toBeInTheDocument();
  });

  it("pluralises the card description once a second batch exists", async () => {
    await renderLoaded();

    expect(screen.getByText("2 batches recorded")).toBeInTheDocument();
  });

  it("waits on the permission set instead of reporting no access", async () => {
    const gate = responseGate();
    let permCalls = 0;
    server.use(
      http.get("/api/auth/permissions", async () => {
        permCalls += 1;
        await gate.held;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );
    const { container } = renderWithProviders(<PurchasesPage />);
    await waitFor(() => expect(permCalls).toBe(1));

    // The header stays up and a skeleton fills the data region — the page no
    // longer collapses to a bare "Loading…" line while the probe is out.
    expect(screen.getByRole("heading", { name: "Purchase batches" })).toBeInTheDocument();
    expect(container.querySelector('[data-slot="skeleton"]')).not.toBeNull();
    expect(screen.queryByText("You don't have access to this page.")).not.toBeInTheDocument();

    gate.open();
    expect(await screen.findByText("#5")).toBeInTheDocument();
  });

  it("waits on the list instead of reporting a failure it has not had", async () => {
    const gate = responseGate();
    let listCalls = 0;
    server.use(
      http.get("/api/purchases", async () => {
        listCalls += 1;
        await gate.held;
        return HttpResponse.json({ batches: [BATCH_FULL], total: 1, limit: 50, offset: 0 });
      }),
    );
    renderWithProviders(<PurchasesPage />);
    await waitFor(() => expect(listCalls).toBe(1));

    expect(screen.getByRole("heading", { name: "Purchase batches" })).toBeInTheDocument();
    expect(screen.getByText("Loading purchase batches…")).toBeInTheDocument();
    expect(screen.queryByText("Could not load purchase batches.")).not.toBeInTheDocument();

    gate.open();
    expect(await screen.findByText("#5")).toBeInTheDocument();
  });

  it("falls back to a generic message when the list fails without a detail", async () => {
    server.use(http.get("/api/purchases", () => HttpResponse.error()));
    renderWithProviders(<PurchasesPage />);

    expect(await screen.findByText("Could not load purchase batches.")).toBeInTheDocument();
  });

  it("offers the first-batch action to a manager", async () => {
    server.use(listHandler([], 0));
    renderWithProviders(<PurchasesPage />);

    expect(await screen.findByText("No purchase batches yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add your first batch" })).toBeInTheDocument();
  });

  it("hides the first-batch action from a view-only user", async () => {
    server.use(permissionsHandler(["purchases.view"]), listHandler([], 0));
    renderWithProviders(<PurchasesPage />);

    expect(await screen.findByText("No purchase batches yet.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Add your first batch" }),
    ).not.toBeInTheDocument();
  });
});

describe("PurchasesPage batch detail requests", () => {
  beforeEach(() => {
    server.use(listHandler([BATCH_FULL, BATCH_BARE], 2), detailHandler());
  });

  async function openDetail(id: string) {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(row(id)).getByRole("button", { name: "View" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("asks for no batch detail until a row is opened", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    expect(detailUrls).toEqual([]);

    await user.click(within(row("#5")).getByRole("button", { name: "View" }));
    await screen.findByRole("dialog");

    await waitFor(() => expect(detailUrls).toHaveLength(1));
    expect(lastDetailUrl().pathname).toBe("/api/purchases/5");
  });

  it("bounds the animals page it asks the detail endpoint for", async () => {
    const { dialog } = await openDetail("#5");
    await within(dialog).findByText("Animals created (1)");

    expect(lastDetailUrl().searchParams.get("animals_limit")).toBe("100");
    expect(lastDetailUrl().searchParams.get("animals_offset")).toBe("0");
  });

  it("restarts the animal page when another batch is opened straight away", async () => {
    server.use(detailHandler(250));
    const { user, dialog } = await openDetail("#5");
    await within(dialog).findByText("Animals created (250)");

    await user.click(within(dialog).getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(lastDetailUrl().searchParams.get("animals_offset")).toBe("100"),
    );

    // The dialog is keyed by batch id, so the next batch must not inherit the
    // page offset the previous one was left on. The open dialog hides the list
    // from the accessibility tree, so this row's control is addressed by text.
    fireEvent.click(within(row("#6")).getByText("View"));

    await waitFor(() => expect(lastDetailUrl().pathname).toBe("/api/purchases/6"));
    expect(lastDetailUrl().searchParams.get("animals_offset")).toBe("0");
  });

  it("joins date, supplier, headcount and price with single separators", async () => {
    const { dialog } = await openDetail("#5");

    const header = await within(dialog).findByText(/5 Jan 2026/);
    expect(joinedText(header)).toBe("5 Jan 2026 · Sharma Goat Farm · 50 head · ₹1,50,000");
  });

  it("leaves no stray separator when supplier and price are missing", async () => {
    const { dialog } = await openDetail("#6");

    const header = await within(dialog).findByText(/10 Feb 2026/);
    expect(joinedText(header)).toBe("10 Feb 2026 · 20 head");
  });

  it("falls back to a generic message when the detail fails without a detail", async () => {
    server.use(http.get("/api/purchases/:batchId", () => HttpResponse.error()));
    const { dialog } = await openDetail("#5");

    expect(await within(dialog).findByText("Could not load the batch.")).toBeInTheDocument();
  });
});

describe("PurchasesPage new-batch flow", () => {
  let postCalls = 0;
  let postBody: Record<string, unknown> | null = null;

  beforeEach(() => {
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    postCalls = 0;
    postBody = null;
    server.use(
      listHandler([BATCH_FULL], 1),
      http.post("/api/purchases/new", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...BATCH_FULL, id: 7 }, { status: 201 });
      }),
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New batch" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  function setField(dialog: HTMLElement, label: RegExp, value: string) {
    fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
  }

  async function review(user: User, dialog: HTMLElement) {
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));
    expect(await within(dialog).findByText("Review purchase consequences")).toBeInTheDocument();
  }

  async function reviewAndConfirm(user: User, dialog: HTMLElement) {
    await review(user, dialog);
    expect(postCalls).toBe(0);
    await user.click(within(dialog).getByRole("button", { name: "Confirm and create" }));
  }

  it("labels the submit button while the review is being validated", async () => {
    const { dialog } = await openDialog();

    // The resolver runs asynchronously: the click leaves the form submitting
    // until it settles, and the button must say so rather than look idle.
    fireEvent.click(within(dialog).getByRole("button", { name: "Review batch" }));
    expect(within(dialog).getByRole("button", { name: "Reviewing…" })).toBeDisabled();

    expect(await within(dialog).findByText("Review purchase consequences")).toBeInTheDocument();
  });

  it("accepts the earliest purchase year the backend stores", async () => {
    const { user, dialog } = await openDialog();

    setField(dialog, /^Date/, "2000-01-01");
    await review(user, dialog);

    expect(within(dialog).queryByText("Date must be year 2000 or later")).not.toBeInTheDocument();
    expect(within(dialog).getByText("1 Jan 2000")).toBeInTheDocument();
  });

  it("rejects a supplier past the API's 120-character cap, accepting the boundary", async () => {
    const { user, dialog } = await openDialog();

    setField(dialog, /Supplier/, "s".repeat(121));
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));

    expect(await within(dialog).findByText("At most 120 characters")).toBeInTheDocument();
    expect(postCalls).toBe(0);

    setField(dialog, /Supplier/, "s".repeat(120));
    await review(user, dialog);
    expect(postCalls).toBe(0);
  });

  it("marks the notes field invalid only while it holds an error", async () => {
    const { user, dialog } = await openDialog();
    const notes = within(dialog).getByLabelText(/Notes/);

    expect(notes).not.toHaveAttribute("aria-invalid");

    fireEvent.change(notes, { target: { value: "n".repeat(4_001) } });
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));

    expect(await within(dialog).findByText("Notes cannot exceed 4000 characters")).toBeInTheDocument();
    expect(notes).toHaveAttribute("aria-invalid", "true");
  });

  it("restores the animal-stub copy when the checkbox is switched back on", async () => {
    const { user, dialog } = await openDialog();
    const box = within(dialog).getByRole("checkbox");

    expect(within(dialog).getByText(/45-day quarantine protocol/)).toBeInTheDocument();

    await user.click(box);
    expect(box).not.toBeChecked();
    expect(
      within(dialog).getByText(/records only the batch and any purchase expense/),
    ).toBeInTheDocument();

    await user.click(box);
    expect(box).toBeChecked();
    expect(within(dialog).getByText(/45-day quarantine protocol/)).toBeInTheDocument();

    await reviewAndConfirm(user, dialog);
    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ create_animals: true });
  });

  it("spells out the consequences of a single female goat", async () => {
    const { user, dialog } = await openDialog();

    await review(user, dialog);

    expect(within(dialog).getByText("1 female goat")).toBeInTheDocument();
    expect(within(dialog).getByText("1 in QUARANTINE")).toBeInTheDocument();
    expect(within(dialog).getByText("45-day quarantine schedule")).toBeInTheDocument();
    expect(within(dialog).getByText("No expense amount")).toBeInTheDocument();
  });

  it("spells out the consequences of several male goats", async () => {
    const { user, dialog } = await openDialog();

    const count = within(dialog).getByLabelText(/Count/);
    await user.clear(count);
    await user.type(count, "2");
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Male" }));
    await review(user, dialog);

    expect(within(dialog).getByText("2 male goats")).toBeInTheDocument();
  });

  it("reports no animal stubs at all when stub creation is off", async () => {
    const { user, dialog } = await openDialog();

    await user.click(within(dialog).getByRole("checkbox"));
    await review(user, dialog);

    expect(within(dialog).getByText("None")).toBeInTheDocument();
    expect(within(dialog).getByText("None (no animal stubs)")).toBeInTheDocument();
  });

  it("announces the in-flight create on the confirm button", async () => {
    const gate = responseGate();
    server.use(
      http.post("/api/purchases/new", async () => {
        postCalls += 1;
        await gate.held;
        return HttpResponse.json({ ...BATCH_FULL, id: 7 }, { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();

    // A payload of its own: the held request must not be joined by the
    // identical default batch another test in this file submits.
    setField(dialog, /Supplier/, "Held Farm");
    await reviewAndConfirm(user, dialog);

    expect(await within(dialog).findByText("Creating…")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Back and edit" })).toBeDisabled();

    gate.open();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("confirms the created batch and reopens on a blank form", async () => {
    const { user, dialog } = await openDialog();

    setField(dialog, /Supplier/, "Sharma Goat Farm");
    await reviewAndConfirm(user, dialog);

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Purchase batch created."));
    expect(toast.error).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).getByText("New purchase batch")).toBeInTheDocument();
    expect(within(reopened).getByLabelText(/^Date/)).toHaveValue(localToday());
    expect(within(reopened).getByLabelText(/Supplier/)).toHaveValue("");
    expect(within(reopened).getByRole("checkbox")).toBeChecked();
  });

  it("sends null for a supplier and notes that are only whitespace", async () => {
    const { user, dialog } = await openDialog();

    setField(dialog, /Supplier/, "   ");
    setField(dialog, /Notes/, "  ");
    await reviewAndConfirm(user, dialog);

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ supplier: null, notes: null });
  });

  it("falls back to a generic toast when the create fails without a detail", async () => {
    server.use(
      http.post("/api/purchases/new", () => {
        postCalls += 1;
        return HttpResponse.error();
      }),
    );
    const { user, dialog } = await openDialog();

    await reviewAndConfirm(user, dialog);

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Something went wrong"));
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
