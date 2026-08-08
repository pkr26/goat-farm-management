/**
 * Purchases page: batch list (dates, Indian ₹ prices, fallback dashes, open
 * task badges), new-batch dialog (count/age/weight/price/date validation, sex
 * default/override + payload mapping), per-batch detail dialog (created
 * animals, open quarantine tasks) and RBAC gating.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { farmToday } from "@/lib/format";

import PurchasesPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/purchases",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function localToday(): string {
  return farmToday();
}

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

const TASK_DONE = { ...TASK_PENDING, id: 92, title: "PPR vaccine", status: "DONE" };

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

function listHandler(batches: unknown[]) {
  return http.get("/api/purchases", () =>
    HttpResponse.json({ batches, total: batches.length, limit: 50, offset: 0 }),
  );
}

async function renderLoaded() {
  renderWithProviders(<PurchasesPage />);
  expect(await screen.findByText("Sharma Goat Farm")).toBeInTheDocument();
}

describe("PurchasesPage batch list", () => {
  beforeEach(() => {
    server.use(listHandler([BATCH_1, BATCH_2]));
  });

  it("renders each batch with formatted date, age, weight and Indian ₹ price", async () => {
    await renderLoaded();

    const row = screen.getByText("Sharma Goat Farm").closest("tr") as HTMLElement;
    const cells = within(row).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("#5");
    expect(cells[1]).toHaveTextContent("5 Jan 2026");
    expect(cells[3]).toHaveTextContent(/^50$/); // count
    expect(cells[4]).toHaveTextContent("8 mo");
    expect(cells[5]).toHaveTextContent("18.5 kg");
    expect(cells[6]).toHaveTextContent("₹1,50,000");
  });

  it("shows — fallbacks for missing supplier, age, weight and price", async () => {
    await renderLoaded();

    const row = screen.getByText("#6").closest("tr") as HTMLElement;
    const cells = within(row).getAllByRole("cell");
    expect(cells[2]).toHaveTextContent("—"); // supplier
    expect(cells[4]).toHaveTextContent("—"); // avg age
    expect(cells[5]).toHaveTextContent("—"); // avg weight
    expect(cells[6]).toHaveTextContent("—"); // total price
  });

  it("badges open quarantine task counts, plain 0 when none", async () => {
    await renderLoaded();

    const row1 = screen.getByText("#5").closest("tr") as HTMLElement;
    expect(within(row1).getAllByRole("cell")[8]).toHaveTextContent(/^3$/);
    const row2 = screen.getByText("#6").closest("tr") as HTMLElement;
    expect(within(row2).getAllByRole("cell")[8]).toHaveTextContent(/^0$/);
  });

  it("shows the empty message when no batches exist", async () => {
    server.use(listHandler([]));
    renderWithProviders(<PurchasesPage />);
    expect(await screen.findByText("No purchase batches yet.")).toBeInTheDocument();
  });

  it("shows the error detail when the list GET fails", async () => {
    server.use(
      http.get("/api/purchases", () =>
        HttpResponse.json({ detail: "purchases down" }, { status: 500 }),
      ),
    );
    renderWithProviders(<PurchasesPage />);
    expect(await screen.findByText("purchases down")).toBeInTheDocument();
  });
});

describe("PurchasesPage RBAC", () => {
  it("denies access without purchases.view and never calls the endpoint", async () => {
    let calls = 0;
    server.use(
      permissionsHandler(["animals.view"]),
      http.get("/api/purchases", () => {
        calls += 1;
        return HttpResponse.json({ batches: [], total: 0, limit: 50, offset: 0 });
      }),
    );
    renderWithProviders(<PurchasesPage />);

    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    expect(calls).toBe(0);
  });

  it("hides New batch for a purchases.view-only user", async () => {
    server.use(permissionsHandler(["purchases.view"]), listHandler([BATCH_1]));
    renderWithProviders(<PurchasesPage />);
    expect(await screen.findByText("Sharma Goat Farm")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New batch" })).not.toBeInTheDocument();
  });
});

describe("PurchasesPage new-batch dialog", () => {
  let postCalls: number;
  let postBody: Record<string, unknown> | null;
  let listCalls: number;

  beforeEach(() => {
    postCalls = 0;
    postBody = null;
    listCalls = 0;
    server.use(
      http.get("/api/purchases", () => {
        listCalls += 1;
        return HttpResponse.json({ batches: [BATCH_1], total: 1, limit: 50, offset: 0 });
      }),
      http.post("/api/purchases/new", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...BATCH_1, id: 7 }, { status: 201 });
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

  function setDate(dialog: HTMLElement, value: string) {
    fireEvent.change(within(dialog).getByLabelText(/^Date/), { target: { value } });
  }

  it("opens with today, count 50 and create_animals pre-checked", async () => {
    const { dialog } = await openDialog();

    expect(within(dialog).getByLabelText(/^Date/)).toHaveValue(localToday());
    expect(within(dialog).getByLabelText(/Count/)).toHaveValue(50);
    expect(within(dialog).getByRole("checkbox")).toBeChecked();
  });

  it("rejects a non-integer count", async () => {
    const { user, dialog } = await openDialog();

    const count = within(dialog).getByLabelText(/Count/);
    await user.clear(count);
    await user.type(count, "2.5");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByText("Count must be a whole number")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a count above 1000", async () => {
    const { user, dialog } = await openDialog();

    const count = within(dialog).getByLabelText(/Count/);
    await user.clear(count);
    await user.type(count, "1001");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByText("At most 1000 animals")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a zero count", async () => {
    const { user, dialog } = await openDialog();

    const count = within(dialog).getByLabelText(/Count/);
    await user.clear(count);
    await user.type(count, "0");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByText("At least 1 animal")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("requires a date", async () => {
    const { user, dialog } = await openDialog();

    setDate(dialog, "");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByText("Date is required")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a date before year 2000", async () => {
    const { user, dialog } = await openDialog();

    setDate(dialog, "1999-12-31");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByText("Date must be year 2000 or later")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a future date", async () => {
    const { user, dialog } = await openDialog();

    setDate(dialog, "2099-01-01");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByText("Date cannot be in the future")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a negative average age", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Avg age/), "-1");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByText("Cannot be negative")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects an average age above 240 months", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Avg age/), "241");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByText("At most 240 months")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a negative average weight", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Avg weight/), "-0.5");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByText("Cannot be negative")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("rejects a negative total price", async () => {
    const { user, dialog } = await openDialog();

    await user.type(within(dialog).getByLabelText(/Total price/), "-100");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    expect(await within(dialog).findByText("Cannot be negative")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("POSTs nulls for every blank optional on a minimal submit", async () => {
    const { user, dialog } = await openDialog();
    const callsBefore = listCalls;

    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toEqual({
      date: localToday(),
      supplier: null,
      count: 50,
      sex: "F",
      avg_age_months: null,
      avg_weight_kg: null,
      total_price: null,
      notes: null,
      create_animals: true,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(listCalls).toBeGreaterThan(callsBefore));
  });

  it("POSTs trimmed values and honors an unchecked create_animals", async () => {
    const { user, dialog } = await openDialog();

    setDate(dialog, "2026-01-05");
    await user.type(within(dialog).getByLabelText(/Supplier/), "  Sharma Goat Farm  ");
    const count = within(dialog).getByLabelText(/Count/);
    await user.clear(count);
    await user.type(count, "12");
    await user.type(within(dialog).getByLabelText(/Avg age/), "8");
    await user.type(within(dialog).getByLabelText(/Avg weight/), "18.5");
    await user.type(within(dialog).getByLabelText(/Total price/), "150000");
    await user.type(within(dialog).getByLabelText(/Notes/), "  foundation stock  ");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toEqual({
      date: "2026-01-05",
      supplier: "Sharma Goat Farm",
      count: 12,
      sex: "F",
      avg_age_months: 8,
      avg_weight_kg: 18.5,
      total_price: 150000,
      notes: "foundation stock",
      create_animals: false,
    });
  });

  it("opens with Female preselected and POSTs sex M when Male is picked", async () => {
    const { user, dialog } = await openDialog();

    const sexTrigger = within(dialog).getByRole("combobox");
    expect(sexTrigger).toHaveTextContent("Female");
    await user.click(sexTrigger);
    await user.click(await screen.findByRole("option", { name: "Male" }));
    expect(sexTrigger).toHaveTextContent("Male");
    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(postBody).toMatchObject({ sex: "M" });
  });

  it("keeps the dialog open on a server error", async () => {
    server.use(
      http.post("/api/purchases/new", () => {
        postCalls += 1;
        return HttpResponse.json({ detail: "duplicate batch" }, { status: 400 });
      }),
    );
    const { user, dialog } = await openDialog();

    await user.click(within(dialog).getByRole("button", { name: "Create batch" }));

    await waitFor(() => expect(postCalls).toBe(1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("PurchasesPage batch detail dialog", () => {
  beforeEach(() => {
    server.use(
      listHandler([BATCH_1]),
      http.get("/api/purchases/5", () =>
        HttpResponse.json({ batch: BATCH_1, animals: [ANIMAL_STUB], tasks: [TASK_PENDING, TASK_DONE] }),
      ),
    );
  });

  async function openDetail() {
    const user = userEvent.setup();
    await renderLoaded();
    const row = screen.getByText("#5").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "View" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/^Animals created \(/);
    return { user, dialog };
  }

  it("shows the batch header with date, supplier, headcount and ₹ price", async () => {
    const { dialog } = await openDetail();

    expect(within(dialog).getByText("Batch #5")).toBeInTheDocument();
    const header = within(dialog).getByText(/5 Jan 2026/);
    expect(header).toHaveTextContent("Sharma Goat Farm");
    expect(header).toHaveTextContent("50 head");
    expect(header).toHaveTextContent("₹1,50,000");
  });

  it("lists created animals with links to their pages", async () => {
    const { dialog } = await openDetail();

    const link = within(dialog).getByRole("link", { name: "B5-001" });
    expect(link).toHaveAttribute("href", "/animals/11");
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("QUARANTINE")).toBeInTheDocument();
    expect(within(row).getByText("ACTIVE")).toBeInTheDocument();
  });

  it("shows only PENDING tasks as open quarantine tasks", async () => {
    const { dialog } = await openDetail();

    expect(within(dialog).getByText("Open quarantine tasks (1)")).toBeInTheDocument();
    expect(within(dialog).getByText("Deworm batch")).toBeInTheDocument();
    expect(within(dialog).getByText("8 Jan 2026")).toBeInTheDocument();
    // The DONE task is filtered out.
    expect(within(dialog).queryByText("PPR vaccine")).not.toBeInTheDocument();
  });

  it("shows empty-state copy when the batch has no animals or open tasks", async () => {
    server.use(
      http.get("/api/purchases/5", () =>
        HttpResponse.json({ batch: BATCH_1, animals: [], tasks: [TASK_DONE] }),
      ),
    );
    const { dialog } = await openDetail();

    expect(within(dialog).getByText("Animals created (0)")).toBeInTheDocument();
    expect(within(dialog).getByText("No animal stubs for this batch.")).toBeInTheDocument();
    expect(within(dialog).getByText("Open quarantine tasks (0)")).toBeInTheDocument();
    expect(within(dialog).getByText("No open quarantine tasks.")).toBeInTheDocument();
  });

  it("shows the error detail when the detail GET fails", async () => {
    server.use(
      http.get("/api/purchases/5", () =>
        HttpResponse.json({ detail: "batch missing" }, { status: 404 }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = screen.getByText("#5").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "View" }));

    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("batch missing")).toBeInTheDocument();
  });
});
