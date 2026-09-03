/**
 * Mutation-hardening tests for the purchases page: URL state (batch id /
 * offset parsing, write-through on open/close, adoption of external URL
 * changes), placeholderData settling on page turns, mobile card fallbacks
 * and pluralisation, dialog dismissal semantics, POST payload trimming and
 * aria wiring on validation errors.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import PurchasesPage from "./page";

// Mutable navigation state so a test can seed the URL the page boots from
// and observe/commit write-throughs (F-7).
const nav = vi.hoisted(() => ({ search: "", replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: nav.replace, prefetch: vi.fn() }),
  usePathname: () => "/purchases",
  useSearchParams: () => new URLSearchParams(nav.search),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const BATCH_1 = {
  id: 5,
  date: "2026-01-05",
  supplier: "Sharma Goat Farm",
  count: 50,
  avg_age_months: 8,
  avg_weight_kg: 18.5,
  total_price: 150000,
  notes: "foundation stock",
  animals_created: 50,
  open_tasks: 3,
};

const BATCH_2 = {
  id: 6,
  date: "2026-02-10",
  supplier: null,
  count: 20,
  avg_age_months: null,
  avg_weight_kg: null,
  total_price: null,
  notes: null,
  animals_created: 0,
  open_tasks: 0,
};

const BATCH_3 = {
  id: 7,
  date: "2026-03-01",
  supplier: "Vedham Dairy",
  count: 1,
  avg_age_months: 10,
  avg_weight_kg: 21,
  total_price: 9000,
  notes: null,
  animals_created: 1,
  open_tasks: 1,
};

const TASK_PENDING = {
  id: 91,
  title: "Deworm batch",
  due_date: "2026-01-08",
  status: "PENDING",
  category: "DEWORMING",
  auto_generated: true,
  animal_id: null,
  purchase_batch_id: 5,
  breeding_record_id: null,
  assigned_role_id: null,
  assigned_user_id: null,
  recur_days: null,
  completed_by_id: null,
  completed_at: null,
  verified_by_id: null,
  verified_at: null,
  verification_note: null,
  skipped_by_id: null,
  action_url: null,
};

const ANIMAL_STUB = {
  id: 11,
  tag_number: "B5-001",
  name: null,
  breed: "Osmanabadi",
  sex: "F",
  date_of_birth: null,
  estimated_dob: null,
  birth_type: null,
  source: "PURCHASED",
  dam_id: null,
  sire_id: null,
  birth_weight: null,
  current_bucket: "QUARANTINE",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  purchase_date: "2026-01-05",
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  notes: null,
  created_at: "2026-01-05T05:30:00Z",
};

function detailBody(batchId: number, animals: unknown[], extra = {}) {
  return {
    batch: { ...BATCH_1, id: batchId },
    animals,
    tasks: [TASK_PENDING],
    animals_total: animals.length,
    animals_limit: 100,
    animals_offset: 0,
    ...extra,
  };
}

function listHandler(batches: unknown[], opts: { total?: number; offset?: number } = {}) {
  return http.get("/api/purchases", ({ request }) => {
    const url = new URL(request.url);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    return HttpResponse.json({
      batches,
      total: opts.total ?? batches.length,
      limit: 50,
      offset: opts.offset ?? offset,
    });
  });
}

async function renderLoaded() {
  const utils = renderWithProviders(<PurchasesPage />);
  expect(await screen.findByText("Sharma Goat Farm")).toBeInTheDocument();
  return utils;
}

beforeEach(() => {
  nav.search = "";
  nav.replace.mockReset();
  server.use(listHandler([BATCH_1, BATCH_2, BATCH_3]));
});

describe("PurchasesPage URL state", () => {
  it("opens the batch detail from ?batch=<id> on boot without rewriting the URL", async () => {
    nav.search = "?batch=5";
    server.use(listHandler([BATCH_1]), http.get("/api/purchases/5", () => HttpResponse.json(detailBody(5, [ANIMAL_STUB]))));

    renderWithProviders(<PurchasesPage />);
    expect(await screen.findByText("Batch #5")).toBeInTheDocument();
    expect(await within(screen.getByRole("dialog")).findByText("Animals created (1)")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it.each(["abc", "2.5", "0", "-3", "12x", ""])(
    "ignores a garbage batch param (%s) and renders the plain list",
    async (garbage) => {
      nav.search = `?batch=${garbage}`;
      await renderLoaded();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    },
  );

  it("clamps nothing away: a valid ?offset=50 is sent to the API and reflected in the range label", async () => {
    nav.search = "?offset=50";
    const requests: string[] = [];
    server.use(
      http.get("/api/purchases", ({ request }) => {
        requests.push(new URL(request.url).search);
        return HttpResponse.json({ batches: [BATCH_1], total: 60, limit: 50, offset: 50 });
      }),
    );
    renderWithProviders(<PurchasesPage />);

    expect(await screen.findByText(/Showing 51–60 of 60 purchase batches/)).toBeInTheDocument();
    expect(requests[0]).toContain("offset=50");
  });

  it("writes batch=5 through to the URL on open and strips it on close (replace, no scroll)", async () => {
    server.use(http.get("/api/purchases/5", () => HttpResponse.json(detailBody(5, [ANIMAL_STUB]))));
    const user = userEvent.setup();
    await renderLoaded();

    const row = screen.getByText("#5").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "View" }));
    expect(await screen.findByText("Batch #5")).toBeInTheDocument();
    expect(nav.replace).toHaveBeenCalledWith("/purchases?batch=5", { scroll: false });

    nav.replace.mockClear();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(nav.replace).toHaveBeenCalledWith("/purchases", { scroll: false });
  });

  it("adopts an external URL change to another batch id (no extra rewrite)", async () => {
    server.use(
      http.get("/api/purchases/5", () => HttpResponse.json(detailBody(5, [ANIMAL_STUB]))),
      http.get("/api/purchases/6", () => HttpResponse.json(detailBody(6, [{ ...ANIMAL_STUB, id: 12, tag_number: "B6-001" }]))),
    );
    const user = userEvent.setup();
    const { rerender } = await renderLoaded();

    const row = screen.getByText("#5").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "View" }));
    expect(await screen.findByText("Batch #5")).toBeInTheDocument();

    // Someone (back/forward, shared link) navigates to batch 6 while open.
    nav.search = "?batch=6";
    rerender(<PurchasesPage />);
    expect(await screen.findByText("Batch #6")).toBeInTheDocument();
    expect(screen.queryByText("Batch #5")).not.toBeInTheDocument();
    // Adoption is read-only: it must not push another replace of its own.
    expect(nav.replace).toHaveBeenCalledTimes(1); // only the original write-through
  });

  it("writes the new offset through to the URL on a page turn", async () => {
    let release!: () => void;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/purchases", ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
        if (offset === 0) {
          return HttpResponse.json({ batches: [BATCH_1], total: 60, limit: 50, offset: 0 });
        }
        return parked.then(() =>
          HttpResponse.json({ batches: [{ ...BATCH_3, id: 55 }], total: 60, limit: 50, offset }),
        );
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    nav.replace.mockClear();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(nav.replace).toHaveBeenCalledWith("/purchases?offset=50", { scroll: false });
    release();
    expect(await screen.findByText("#55")).toBeInTheDocument();
  });
});

describe("PurchasesPage placeholderData settling", () => {
  it("keeps the previous list page visible with an Updating notice while a page turn settles", async () => {
    let release!: () => void;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/purchases", ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
        if (offset === 0) {
          return HttpResponse.json({ batches: [BATCH_1], total: 60, limit: 50, offset: 0 });
        }
        return parked.then(() =>
          HttpResponse.json({ batches: [{ ...BATCH_3, id: 55 }], total: 60, limit: 50, offset }),
        );
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Next" }));

    // Previous page still rendered, settling notice shown, controls locked.
    expect(screen.getByText("#5")).toBeInTheDocument();
    expect(screen.getByText("Updating purchase batches…")).toBeInTheDocument();
    const pager = screen.getByRole("navigation", { name: "purchase batches pagination" });
    expect(within(pager).getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(within(pager).getByRole("button", { name: "Next" })).toBeDisabled();

    release();
    expect(await screen.findByText("#55")).toBeInTheDocument();
    expect(screen.queryByText("#5")).not.toBeInTheDocument();
    expect(screen.queryByText("Updating purchase batches…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
  });

  it("keeps the previous animals page inside the detail dialog while the next settles", async () => {
    let release!: () => void;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/purchases/5", ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("animals_offset") ?? 0);
        if (offset === 0) {
          return HttpResponse.json(
            detailBody(5, [ANIMAL_STUB], { animals_total: 120, animals_offset: 0 }),
          );
        }
        return parked.then(() =>
          HttpResponse.json(
            detailBody(5, [{ ...ANIMAL_STUB, id: 12, tag_number: "B5-101" }], {
              animals_total: 120,
              animals_offset: offset,
            }),
          ),
        );
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(screen.getByText("#5").closest("tr") as HTMLElement).getByRole("button", { name: "View" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("B5-001")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Next" }));
    // Previous page kept as placeholder while page 2 is in flight.
    expect(within(dialog).getByText("B5-001")).toBeInTheDocument();
    const pager = within(dialog).getByRole("navigation", { name: "animals pagination" });
    expect(within(pager).getByRole("button", { name: "Next" })).toBeDisabled();

    release();
    expect(await within(dialog).findByText("B5-101")).toBeInTheDocument();
    expect(within(dialog).queryByText("B5-001")).not.toBeInTheDocument();
  });
});

describe("PurchasesPage mobile cards", () => {
  it("falls back to No supplier / No price recorded / — for a sparse batch", async () => {
    await renderLoaded();

    expect(screen.getByText(/No supplier · 20 head/)).toBeInTheDocument();
    expect(screen.getByText(/^No price recorded$/)).toBeInTheDocument();
    expect(screen.getByText(/— avg age · — avg weight · 0 animals created/)).toBeInTheDocument();
  });

  it("pluralises open tasks and created animals (singular and plural)", async () => {
    await renderLoaded();

    expect(screen.getByText(/₹9,000 · 1 open task/)).toBeInTheDocument();
    expect(screen.getByText(/1 animal created/)).toBeInTheDocument();
    expect(screen.getByText(/3 open tasks/)).toBeInTheDocument();
    expect(screen.getByText(/50 animals created/)).toBeInTheDocument();
    expect(screen.queryByText(/1 open tasks/)).not.toBeInTheDocument();
    expect(screen.queryByText(/50 animal created/)).not.toBeInTheDocument();
  });
});

describe("PurchasesPage new-batch dialog dismissal and defaults", () => {
  it("dismiss after review discards the pending batch and reopens to fresh defaults", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("checkbox")).toBeChecked(); // create_animals default

    const count = within(dialog).getByLabelText(/Count/);
    await user.clear(count);
    await user.type(count, "12");
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));
    expect(await within(dialog).findByText("Review purchase consequences")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).getByText("New purchase batch")).toBeInTheDocument();
    expect(within(reopened).queryByText("Review purchase consequences")).not.toBeInTheDocument();
    expect(within(reopened).getByLabelText(/Count/)).toHaveValue(1);
    expect(within(reopened).getByRole("checkbox")).toBeChecked();
    // Female is the default sex on a fresh form.
    expect(within(reopened).getByRole("combobox")).toHaveTextContent("Female");
  });

  it("marks invalid fields with aria-invalid and wires their error messages", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "New batch" }));
    const dialog = await screen.findByRole("dialog");
    const date = within(dialog).getByLabelText(/^Date/);
    const supplier = within(dialog).getByLabelText(/Supplier/);
    const count = within(dialog).getByLabelText(/Count/);

    // Clean form: no field is flagged invalid yet.
    expect(date).not.toHaveAttribute("aria-invalid");
    expect(supplier).not.toHaveAttribute("aria-invalid");
    expect(count).not.toHaveAttribute("aria-invalid");

    fireEvent.change(date, { target: { value: "" } });
    fireEvent.change(supplier, { target: { value: "s".repeat(121) } });
    await user.clear(count);
    await user.type(count, "0");
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));

    expect(await within(dialog).findByText("Date is required")).toBeInTheDocument();
    expect(date).toHaveAttribute("aria-invalid", "true");
    expect(date).toHaveAccessibleDescription("Date is required");

    expect(await within(dialog).findByText("At most 120 characters")).toBeInTheDocument();
    expect(supplier).toHaveAttribute("aria-invalid", "true");
    expect(supplier).toHaveAccessibleDescription("At most 120 characters");

    expect(await within(dialog).findByText("At least 1 animal")).toBeInTheDocument();
    expect(count).toHaveAttribute("aria-invalid", "true");
    expect(count).toHaveAccessibleDescription("At least 1 animal");
  });

  it("POSTs null for whitespace-only supplier and notes, trimmed values otherwise", async () => {
    let postBody: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/purchases/new", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...BATCH_1, id: 9 }, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "New batch" }));
    let dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Supplier/), "   ");
    await user.type(within(dialog).getByLabelText(/Notes/), "   ");
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));
    expect(await within(dialog).findByText("Review purchase consequences")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Confirm and create" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ supplier: null, notes: null });

    await user.click(screen.getByRole("button", { name: "New batch" }));
    dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Supplier/), "  Farm A  ");
    await user.type(within(dialog).getByLabelText(/Notes/), "  note  ");
    await user.click(within(dialog).getByRole("button", { name: "Review batch" }));
    expect(await within(dialog).findByText("Review purchase consequences")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Confirm and create" }));
    await waitFor(() => expect(postBody).toMatchObject({ supplier: "Farm A", notes: "note" }));
  });
});

describe("PurchasesPage detail RBAC and enum labels", () => {
  it("renders animal tag as plain text without animals.view, with enum labels for sex/bucket", async () => {
    server.use(
      permissionsHandler(["purchases.view"]),
      http.get("/api/purchases/5", () =>
        HttpResponse.json(detailBody(5, [{ ...ANIMAL_STUB, sex: "M", current_bucket: "QUARANTINE" }])),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(
      within(screen.getByText("#5").closest("tr") as HTMLElement).getByRole("button", { name: "View" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Animals created (1)")).toBeInTheDocument();

    expect(within(dialog).getByText("B5-001")).toBeInTheDocument();
    expect(within(dialog).queryByRole("link")).not.toBeInTheDocument();
    expect(within(dialog).getByText("Male")).toBeInTheDocument();
    expect(within(dialog).getByText("Quarantine")).toBeInTheDocument();
  });

  it("resolves species bucket chips through the vocabulary, not the raw title case", async () => {
    server.use(
      http.get("/api/purchases/5", () =>
        HttpResponse.json(
          detailBody(5, [
            { ...ANIMAL_STUB, current_bucket: "PREGNANCY_EARLY" },
          ]),
        ),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(
      within(screen.getByText("#5").closest("tr") as HTMLElement).getByRole("button", { name: "View" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Pregnancy A")).toBeInTheDocument();
    expect(within(dialog).queryByText("Pregnancy Early")).not.toBeInTheDocument();
  });
});

describe("PurchasesPage round-2 mutation survivors", () => {
  it("accepts ?batch=1 — the smallest legal id — on boot", async () => {
    nav.search = "?batch=1";
    server.use(
      listHandler([{ ...BATCH_1, id: 1 }]),
      http.get("/api/purchases/1", () =>
        HttpResponse.json(detailBody(1, [{ ...ANIMAL_STUB }])),
      ),
    );
    renderWithProviders(<PurchasesPage />);
    expect(await screen.findByText("Batch #1")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores garbage batch params that arrive as an external URL change", async () => {
    const { rerender } = await renderLoaded();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    nav.search = "?batch=abc";
    rerender(<PurchasesPage />);
    await screen.findByText("Sharma Goat Farm");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    nav.search = "?batch=2.5";
    rerender(<PurchasesPage />);
    await screen.findByText("Sharma Goat Farm");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    nav.search = "?batch=0";
    rerender(<PurchasesPage />);
    await screen.findByText("Sharma Goat Farm");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("adopts ?batch=1 arriving as an external URL change", async () => {
    server.use(
      http.get("/api/purchases/1", () =>
        HttpResponse.json(detailBody(1, [{ ...ANIMAL_STUB }])),
      ),
    );
    const { rerender } = await renderLoaded();

    nav.search = "?batch=1";
    rerender(<PurchasesPage />);
    expect(await screen.findByText("Batch #1")).toBeInTheDocument();
  });

  it("adopts ?offset=50 arriving as an external URL change", async () => {
    const requests: string[] = [];
    server.use(
      http.get("/api/purchases", ({ request }) => {
        requests.push(new URL(request.url).search);
        const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
        return HttpResponse.json({
          batches: [BATCH_1],
          total: 60,
          limit: 50,
          offset,
        });
      }),
    );
    const { rerender } = await renderLoaded();

    nav.search = "?offset=50";
    rerender(<PurchasesPage />);
    expect(await screen.findByText(/Showing 51–60 of 60 purchase batches/)).toBeInTheDocument();
    await waitFor(() =>
      expect(requests.some((search) => search.includes("offset=50"))).toBe(true),
    );
  });

  it("opens the batch detail from the mobile card's View button", async () => {
    server.use(
      http.get("/api/purchases/5", () => HttpResponse.json(detailBody(5, [ANIMAL_STUB]))),
    );
    const user = userEvent.setup();
    await renderLoaded();

    // The phone card for the first batch, rendered below md.
    const mobileCard = screen
      .getByText(/Sharma Goat Farm · 50 head/)
      .closest("div.rounded-xl") as HTMLElement;
    expect(mobileCard).not.toBeNull();
    await user.click(within(mobileCard).getByRole("button", { name: "View" }));
    expect(await screen.findByText("Batch #5")).toBeInTheDocument();
  });

  it("shows the mobile analytics line with age and weight figures", async () => {
    await renderLoaded();
    expect(
      screen.getByText(/8 mo avg age · 18\.5 kg avg weight · 50 animals created/),
    ).toBeInTheDocument();
    expect(screen.getByText(/10 mo avg age · 21 kg avg weight · 1 animal created/)).toBeInTheDocument();
  });

  it("ends the singular mobile price line right after the open-task count", async () => {
    await renderLoaded();
    expect(screen.getByText(/₹9,000 · 1 open task$/)).toBeInTheDocument();
    expect(screen.getByText(/₹1,50,000 · 3 open tasks$/)).toBeInTheDocument();
  });

  it("describes the quarantine protocol in every header branch", async () => {
    const description =
      "Incoming groups of goats — each batch auto-creates its 45-day quarantine protocol.";

    // Main (loaded) branch.
    const loaded = await renderLoaded();
    expect(screen.getByText(description)).toBeInTheDocument();
    loaded.unmount();

    // Permissions-loading branch: header + skeleton while perms are delayed.
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: false, permissions: ["purchases.view"] }, { delay: 400 }),
      ),
    );
    const { unmount } = renderWithProviders(<PurchasesPage />);
    // The perms-loading branch carries the description synchronously (the
    // goat vocabulary needs no farms fetch), while the list never loads yet.
    expect(screen.getByText(description)).toBeInTheDocument();
    unmount();

    // List-loading branch: perms resolve immediately, the batch list hangs.
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: false, permissions: ["purchases.view"] }),
      ),
      http.get("/api/purchases", () => new Promise(() => {})),
    );
    renderWithProviders(<PurchasesPage />);
    expect(await screen.findByText("Loading purchase batches…", undefined, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getByText(description)).toBeInTheDocument();
  });

  it("recovers the page when the permissions retry succeeds", async () => {
    let calls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        calls += 1;
        return calls === 1
          ? new HttpResponse(null, { status: 500 })
          : HttpResponse.json({ is_owner: false, permissions: ["purchases.view"] });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<PurchasesPage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry permissions" }));

    expect(await screen.findByText("Sharma Goat Farm")).toBeInTheDocument();
    expect(calls).toBeGreaterThanOrEqual(2);
  });

  it("retries the batch list after a failure and renders it", async () => {
    let calls = 0;
    server.use(
      http.get("/api/purchases", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "warehouse down" }, { status: 500 })
          : HttpResponse.json({ batches: [BATCH_1], total: 1, limit: 50, offset: 0 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<PurchasesPage />);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("warehouse down")).toBeInTheDocument();
    await user.click(within(alert).getByRole("button", { name: "Retry batches" }));

    expect(await screen.findByText("Sharma Goat Farm")).toBeInTheDocument();
    expect(calls).toBe(2);
  });
});
