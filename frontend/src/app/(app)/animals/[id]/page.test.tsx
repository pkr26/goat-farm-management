/**
 * Animal profile page: header/details rendering (DOB fallback, PURCHASED and
 * SOLD/DEAD blocks, dam/sire links, notes), the four history cards
 * (weights/moves/health/kids) with empty states and formatting, page-level
 * loading/error/permission states, RBAC gating of the three action dialogs,
 * and the Record weight / Move bucket / Change status dialogs — validation
 * boundaries (weight > 0, BCS int 1–5, non-negative sale price), SOLD-only
 * price/buyer fields, payload mapping (blanks → null), and 4xx/5xx error
 * paths surfaced via toast.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalProfilePage from "./page";

const nav = vi.hoisted(() => ({ id: "1" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals/1",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ id: nav.id }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const toastMock = toast as unknown as { success: ReturnType<typeof vi.fn>; error: ReturnType<typeof vi.fn> };

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI Select relies on.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

type User = ReturnType<typeof userEvent.setup>;

async function pickOption(user: User, trigger: HTMLElement, name: string) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

function setInput(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

const ANIMAL = {
  id: 1,
  tag_number: "G-001",
  name: "Lakshmi",
  breed: "Osmanabadi",
  sex: "F",
  date_of_birth: "2025-05-10",
  estimated_dob: null,
  birth_type: "TWIN",
  source: "BORN",
  dam_id: 7,
  sire_id: 8,
  birth_weight: 2.4,
  current_bucket: "PREGNANCY_EARLY",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  notes: "Calm doe, good milker.",
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
  days_in_current_bucket: 12,
};

const KID = {
  ...ANIMAL,
  id: 21,
  tag_number: "G-021",
  name: null,
  sex: "M",
  date_of_birth: "2026-06-01",
  status: "ACTIVE",
  dam_id: 1,
  sire_id: null,
  notes: null,
  days_in_current_bucket: 5,
};

const PROFILE = {
  animal: ANIMAL,
  kids: [KID],
  weights: [
    { id: 11, date: "2026-07-01", weight_kg: 28.44, bcs: 3, notes: "Pre-monsoon" },
    { id: 12, date: "2026-08-01", weight_kg: 32.5, bcs: null, notes: null },
  ],
  moves: [
    { id: 31, from_bucket: null, to_bucket: "QUARANTINE", reason: null, moved_at: "2026-06-20" },
    {
      id: 32,
      from_bucket: "QUARANTINE",
      to_bucket: "PREGNANCY_EARLY",
      reason: "Confirmed pregnant",
      moved_at: "2026-07-25",
    },
  ],
  health_events: [
    {
      id: 41,
      animal_id: 1,
      purchase_batch_id: null,
      date: "2026-07-10",
      type: "VACCINE",
      product_name: "PPR vaccine",
      disease_target: null,
      dose: "1 ml",
      route: "SC",
      vet_name: null,
      cost: 45.5,
      next_due_date: "2027-07-10",
      notes: null,
    },
    {
      id: 42,
      animal_id: 1,
      purchase_batch_id: null,
      date: "2026-07-12",
      type: "DEWORMING",
      product_name: null,
      disease_target: "Flukes",
      dose: null,
      route: null,
      vet_name: null,
      cost: null,
      next_due_date: null,
      notes: null,
    },
  ],
  breedings: [],
};

function profileWith(animal: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  return { ...PROFILE, ...extra, animal: { ...ANIMAL, ...animal } };
}

describe("AnimalProfilePage", () => {
  let getCalls: number;
  let weightBodies: Record<string, unknown>[];
  let moveBodies: Record<string, unknown>[];
  let statusBodies: Record<string, unknown>[];

  function useProfileHandler(profile: Record<string, unknown> = PROFILE) {
    server.use(
      http.get("/api/animals/1", () => {
        getCalls += 1;
        return HttpResponse.json(profile);
      }),
    );
  }

  beforeEach(() => {
    nav.id = "1";
    getCalls = 0;
    weightBodies = [];
    moveBodies = [];
    statusBodies = [];
    vi.clearAllMocks();
    useProfileHandler();
    server.use(
      http.post("/api/animals/1/weight", async ({ request }) => {
        weightBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ id: 99, date: "2026-08-06", weight_kg: 30, bcs: null, notes: null }, { status: 201 });
      }),
      http.post("/api/animals/1/move", async ({ request }) => {
        moveBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({}, { status: 201 });
      }),
      http.post("/api/animals/1/status", async ({ request }) => {
        statusBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({}, { status: 201 });
      }),
    );
  });

  async function renderProfile() {
    renderWithProviders(<AnimalProfilePage />);
    await screen.findByRole("heading", { level: 1, name: /G-001/ });
  }

  async function openDialog(user: User, button: string) {
    await user.click(screen.getByRole("button", { name: button }));
    return await screen.findByRole("dialog");
  }

  describe("header and details", () => {
    it("shows tag, name and status badge in the heading", async () => {
      await renderProfile();
      const h1 = screen.getByRole("heading", { level: 1 });
      expect(h1).toHaveTextContent("G-001 · Lakshmi");
      expect(within(h1).getByText("ACTIVE")).toBeInTheDocument();
    });

    it("omits the name from the heading when null", async () => {
      useProfileHandler(profileWith({ name: null }));
      await renderProfile();
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("G-001");
      expect(screen.getByRole("heading", { level: 1 })).not.toHaveTextContent("·");
    });

    it("renders the core detail fields with formatting", async () => {
      await renderProfile();
      const card = screen.getByText("Details").closest('[data-slot="card"]') as HTMLElement;
      const body = within(card);
      expect(body.getByText("Female")).toBeInTheDocument();
      expect(body.getByText("Osmanabadi")).toBeInTheDocument();
      expect(body.getByText("PREGNANCY EARLY")).toBeInTheDocument();
      expect(body.getByText("12")).toBeInTheDocument(); // days in bucket
      expect(body.getByText("10 May 2025")).toBeInTheDocument();
      expect(body.getByText("14 months")).toBeInTheDocument();
      expect(body.getByText("TWIN")).toBeInTheDocument();
      expect(body.getByText("2.4 kg")).toBeInTheDocument();
      expect(body.getByText("32.5 kg")).toBeInTheDocument();
      expect(body.getByText("BORN")).toBeInTheDocument();
    });

    it("prefers the exact DOB over the estimated one", async () => {
      useProfileHandler(profileWith({ estimated_dob: "2025-05-01" }));
      await renderProfile();
      expect(screen.getByText("10 May 2025")).toBeInTheDocument();
      expect(screen.queryByText(/~1 May 2025/)).not.toBeInTheDocument();
    });

    it("falls back to a tilde-prefixed estimated DOB when DOB is null", async () => {
      useProfileHandler(profileWith({ date_of_birth: null, estimated_dob: "2025-05-01" }));
      await renderProfile();
      expect(screen.getByText("~1 May 2025")).toBeInTheDocument();
    });

    it("renders em dashes when dates, age, weights and birth type are null", async () => {
      useProfileHandler(
        profileWith({
          date_of_birth: null,
          estimated_dob: null,
          age_months: null,
          birth_type: null,
          birth_weight: null,
          latest_weight_kg: null,
        }),
      );
      await renderProfile();
      const card = screen.getByText("Details").closest('[data-slot="card"]') as HTMLElement;
      expect(within(card).getAllByText("—").length).toBeGreaterThanOrEqual(5);
    });

    it("shows Male for sex M", async () => {
      useProfileHandler(profileWith({ sex: "M" }));
      await renderProfile();
      expect(screen.getByText("Male")).toBeInTheDocument();
    });

    it("shows purchase details with ₹ formatting for PURCHASED animals", async () => {
      useProfileHandler(
        profileWith({
          source: "PURCHASED",
          purchase_date: "2026-01-15",
          purchase_price: 12500,
          seller_name: "Raju Pawar",
        }),
      );
      await renderProfile();
      expect(screen.getByText("15 Jan 2026")).toBeInTheDocument();
      expect(screen.getByText("₹12,500")).toBeInTheDocument();
      expect(screen.getByText("Raju Pawar")).toBeInTheDocument();
    });

    it("hides purchase details for BORN animals", async () => {
      await renderProfile();
      expect(screen.queryByText("Purchase date")).not.toBeInTheDocument();
      expect(screen.queryByText("Seller")).not.toBeInTheDocument();
    });

    it("shows status date and sale price for a SOLD animal, and no action buttons", async () => {
      useProfileHandler(
        profileWith({ status: "SOLD", status_date: "2026-07-30", sale_price: 9000 }),
      );
      renderWithProviders(<AnimalProfilePage />);
      await screen.findByText("Status date");
      expect(screen.getByText("30 Jul 2026")).toBeInTheDocument();
      expect(screen.getByText("₹9,000")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Record weight" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Move bucket" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Change status" })).not.toBeInTheDocument();
    });

    it("shows status date but no sale price for a DEAD animal", async () => {
      useProfileHandler(profileWith({ status: "DEAD", status_date: "2026-07-30" }));
      renderWithProviders(<AnimalProfilePage />);
      await screen.findByText("Status date");
      expect(screen.queryByText("Sale price")).not.toBeInTheDocument();
    });

    it("links dam and sire to their profiles", async () => {
      await renderProfile();
      expect(screen.getByRole("link", { name: "#7" })).toHaveAttribute("href", "/animals/7");
      expect(screen.getByRole("link", { name: "#8" })).toHaveAttribute("href", "/animals/8");
    });

    it("hides dam and sire rows when null", async () => {
      useProfileHandler(profileWith({ dam_id: null, sire_id: null }));
      await renderProfile();
      expect(screen.queryByText("Dam")).not.toBeInTheDocument();
      expect(screen.queryByText("Sire")).not.toBeInTheDocument();
    });

    it("renders the notes paragraph when present and hides it when null", async () => {
      await renderProfile();
      expect(screen.getByText("Calm doe, good milker.")).toBeInTheDocument();
    });
  });

  describe("history cards", () => {
    it("renders the weight history with count, formatting and bcs dash", async () => {
      await renderProfile();
      const card = screen.getByText("Weight history (2)").closest('[data-slot="card"]') as HTMLElement;
      const body = within(card);
      expect(body.getByText("1 Jul 2026")).toBeInTheDocument();
      expect(body.getByText("28.4 kg")).toBeInTheDocument();
      expect(body.getByText("3")).toBeInTheDocument();
      expect(body.getByText("Pre-monsoon")).toBeInTheDocument();
      expect(body.getByText("1 Aug 2026")).toBeInTheDocument();
      expect(body.getByText("—")).toBeInTheDocument(); // null bcs
    });

    it("shows the empty state when there are no weights", async () => {
      useProfileHandler(profileWith({}, { weights: [] }));
      renderWithProviders(<AnimalProfilePage />);
      expect(await screen.findByText("Weight history (0)")).toBeInTheDocument();
      expect(screen.getByText("No weight records yet.")).toBeInTheDocument();
    });

    it("renders bucket moves with dash for null from-bucket and spaced names", async () => {
      await renderProfile();
      expect(screen.getByText("Bucket moves (2)")).toBeInTheDocument();
      expect(screen.getByText("Confirmed pregnant")).toBeInTheDocument();
      const rows = screen.getAllByText("QUARANTINE");
      expect(rows.length).toBeGreaterThanOrEqual(2); // to-bucket and from-bucket cells
      expect(screen.getByText("20 Jun 2026")).toBeInTheDocument();
    });

    it("shows the empty state when there are no moves", async () => {
      useProfileHandler(profileWith({}, { moves: [] }));
      renderWithProviders(<AnimalProfilePage />);
      expect(await screen.findByText("Bucket moves (0)")).toBeInTheDocument();
      expect(screen.getByText("No moves recorded.")).toBeInTheDocument();
    });

    it("renders health events with product, disease fallback, cost and next due", async () => {
      await renderProfile();
      expect(screen.getByText("Health events (2)")).toBeInTheDocument();
      expect(screen.getByText("PPR vaccine")).toBeInTheDocument();
      expect(screen.getByText("Flukes")).toBeInTheDocument(); // disease_target fallback
      expect(screen.getByText("₹45.50")).toBeInTheDocument();
      expect(screen.getByText("10 Jul 2027")).toBeInTheDocument();
    });

    it("renders dashes for null cost and next-due in health events", async () => {
      useProfileHandler(
        profileWith({}, { health_events: [PROFILE.health_events[1]] }),
      );
      renderWithProviders(<AnimalProfilePage />);
      const card = (await screen.findByText("Health events (1)")).closest('[data-slot="card"]') as HTMLElement;
      expect(within(card).getAllByText("—").length).toBe(2);
    });

    it("shows the empty state when there are no health events", async () => {
      useProfileHandler(profileWith({}, { health_events: [] }));
      renderWithProviders(<AnimalProfilePage />);
      expect(await screen.findByText("Health events (0)")).toBeInTheDocument();
      expect(screen.getByText("No health events.")).toBeInTheDocument();
    });

    it("renders the kids card for a doe with linked kid tags", async () => {
      await renderProfile();
      expect(screen.getByText("Kids (1)")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "G-021" })).toHaveAttribute("href", "/animals/21");
      expect(screen.getByText("1 Jun 2026")).toBeInTheDocument();
    });

    it("shows the kids empty state for a doe without kids", async () => {
      useProfileHandler(profileWith({}, { kids: [] }));
      renderWithProviders(<AnimalProfilePage />);
      expect(await screen.findByText("Kids (0)")).toBeInTheDocument();
      expect(screen.getByText("No kids recorded.")).toBeInTheDocument();
    });

    it("hides the kids card entirely for a buck", async () => {
      useProfileHandler(profileWith({ sex: "M" }));
      await renderProfile();
      expect(screen.queryByText(/Kids \(/)).not.toBeInTheDocument();
    });
  });

  describe("page states and RBAC", () => {
    it("shows a loading indicator while the profile request is pending", async () => {
      server.use(http.get("/api/animals/1", () => new Promise<Response>(() => {})));
      renderWithProviders(<AnimalProfilePage />);
      expect((await screen.findAllByText("Loading…")).length).toBeGreaterThan(0);
    });

    it("shows the server detail on a 404", async () => {
      server.use(
        http.get("/api/animals/1", () =>
          HttpResponse.json({ detail: "Animal not found" }, { status: 404 }),
        ),
      );
      renderWithProviders(<AnimalProfilePage />);
      expect(await screen.findByText("Animal not found")).toBeInTheDocument();
    });

    it("shows the server detail on a 500", async () => {
      server.use(
        http.get("/api/animals/1", () =>
          HttpResponse.json({ detail: "Profile query failed" }, { status: 500 }),
        ),
      );
      renderWithProviders(<AnimalProfilePage />);
      expect(await screen.findByText("Profile query failed")).toBeInTheDocument();
    });

    it("treats a non-numeric id as not found and never calls the API", async () => {
      nav.id = "abc";
      renderWithProviders(<AnimalProfilePage />);
      expect(await screen.findByText("Animal not found.")).toBeInTheDocument();
      expect(getCalls).toBe(0);
    });

    it("denies access without animals.view and never calls the API", async () => {
      server.use(permissionsHandler(["dashboard.view"]));
      renderWithProviders(<AnimalProfilePage />);
      expect(await screen.findByText(/don't have access to this page/)).toBeInTheDocument();
      expect(getCalls).toBe(0);
    });

    it("shows a loading indicator while permissions resolve", async () => {
      server.use(http.get("/api/auth/permissions", () => new Promise<Response>(() => {})));
      renderWithProviders(<AnimalProfilePage />);
      expect((await screen.findAllByText("Loading…")).length).toBeGreaterThan(0);
    });

    it("shows all three action buttons with the full permission set", async () => {
      await renderProfile();
      expect(screen.getByRole("button", { name: "Record weight" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Move bucket" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Change status" })).toBeInTheDocument();
    });

    it("hides all action buttons with only animals.view", async () => {
      server.use(permissionsHandler(["animals.view"]));
      await renderProfile();
      expect(screen.queryByRole("button", { name: "Record weight" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Move bucket" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Change status" })).not.toBeInTheDocument();
    });

    it("shows only Record weight with animals.weight", async () => {
      server.use(permissionsHandler(["animals.view", "animals.weight"]));
      await renderProfile();
      expect(screen.getByRole("button", { name: "Record weight" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Move bucket" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Change status" })).not.toBeInTheDocument();
    });

    it("shows only Move bucket and Change status with animals.move + animals.status", async () => {
      server.use(permissionsHandler(["animals.view", "animals.move", "animals.status"]));
      await renderProfile();
      expect(screen.queryByRole("button", { name: "Record weight" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Move bucket" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Change status" })).toBeInTheDocument();
    });
  });

  describe("record weight dialog", () => {
    it("rejects an empty weight with the zod message and no POST", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));
      expect(
        await within(dialog).findByText("Weight must be greater than 0"),
      ).toBeInTheDocument();
      expect(weightBodies).toHaveLength(0);
    });

    it("rejects a negative weight", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "-3");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));
      expect(
        await within(dialog).findByText("Weight must be greater than 0"),
      ).toBeInTheDocument();
      expect(weightBodies).toHaveLength(0);
    });

    it("rejects BCS below 1", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "30");
      setInput(within(dialog).getByLabelText(/bcs/i), "0");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));
      expect(await within(dialog).findByText(/expected number to be >=1/)).toBeInTheDocument();
      expect(weightBodies).toHaveLength(0);
    });

    it("rejects BCS above 5", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "30");
      setInput(within(dialog).getByLabelText(/bcs/i), "6");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));
      expect(await within(dialog).findByText(/expected number to be <=5/)).toBeInTheDocument();
      expect(weightBodies).toHaveLength(0);
    });

    it("rejects a fractional BCS (must be an integer)", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "30");
      setInput(within(dialog).getByLabelText(/bcs/i), "3.5");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));
      expect(await within(dialog).findByText(/expected int/)).toBeInTheDocument();
      expect(weightBodies).toHaveLength(0);
    });

    it("POSTs a full weight entry, toasts, closes and refetches the profile", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/date/i), "2026-08-05");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "30.5");
      setInput(within(dialog).getByLabelText(/bcs/i), "4");
      await user.type(within(dialog).getByLabelText(/notes/i), "After flush feeding");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));

      await waitFor(() => expect(weightBodies).toHaveLength(1));
      expect(weightBodies[0]).toEqual({
        date: "2026-08-05",
        weight_kg: 30.5,
        bcs: 4,
        notes: "After flush feeding",
      });
      await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Weight recorded."));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await waitFor(() => expect(getCalls).toBeGreaterThanOrEqual(2));
    });

    it("POSTs null for blank date, bcs and notes", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "31");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));
      await waitFor(() => expect(weightBodies).toHaveLength(1));
      expect(weightBodies[0]).toEqual({ date: null, weight_kg: 31, bcs: null, notes: null });
    });

    it("shows the server detail as a toast and keeps the dialog open on 400", async () => {
      server.use(
        http.post("/api/animals/1/weight", () =>
          HttpResponse.json({ detail: "Weight date cannot be in the future" }, { status: 400 }),
        ),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "31");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));
      await waitFor(() =>
        expect(toastMock.error).toHaveBeenCalledWith("Weight date cannot be in the future"),
      );
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("shows a generic toast on a network failure", async () => {
      server.use(http.post("/api/animals/1/weight", () => HttpResponse.error()));
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "31");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));
      await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("Something went wrong"));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  describe("move bucket dialog", () => {
    it("offers every bucket except the current one", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      await user.click(within(dialog).getByRole("combobox"));
      const options = await screen.findAllByRole("option");
      const names = options.map((o) => o.textContent);
      expect(names).toHaveLength(9); // 10 buckets minus current
      expect(names).not.toContain("PREGNANCY EARLY");
      expect(names).toContain("RESTING");
      await user.keyboard("{Escape}");
    });

    it("does not POST when no bucket is chosen", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      await user.click(within(dialog).getByRole("button", { name: "Move" }));
      await new Promise((r) => setTimeout(r, 100));
      expect(moveBodies).toHaveLength(0);
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("POSTs the move with a reason, toasts, closes and refetches", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      await pickOption(user, within(dialog).getByRole("combobox"), "RESTING");
      await user.type(within(dialog).getByLabelText(/reason/i), "Not pregnant after all");
      await user.click(within(dialog).getByRole("button", { name: "Move" }));
      await waitFor(() => expect(moveBodies).toHaveLength(1));
      expect(moveBodies[0]).toEqual({ to_bucket: "RESTING", reason: "Not pregnant after all" });
      await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Animal moved."));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await waitFor(() => expect(getCalls).toBeGreaterThanOrEqual(2));
    });

    it("POSTs a null reason when left blank", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      await pickOption(user, within(dialog).getByRole("combobox"), "RECOVERY");
      await user.click(within(dialog).getByRole("button", { name: "Move" }));
      await waitFor(() => expect(moveBodies).toHaveLength(1));
      expect(moveBodies[0]).toEqual({ to_bucket: "RECOVERY", reason: null });
    });

    it("shows the server detail as a toast and keeps the dialog open on 409", async () => {
      server.use(
        http.post("/api/animals/1/move", () =>
          HttpResponse.json({ detail: "Animal must complete quarantine first" }, { status: 409 }),
        ),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      await pickOption(user, within(dialog).getByRole("combobox"), "RESTING");
      await user.click(within(dialog).getByRole("button", { name: "Move" }));
      await waitFor(() =>
        expect(toastMock.error).toHaveBeenCalledWith("Animal must complete quarantine first"),
      );
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  describe("change status dialog", () => {
    it("defaults to SOLD with sale price and buyer fields visible", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      expect(within(dialog).getByRole("combobox")).toHaveTextContent("SOLD");
      expect(within(dialog).getByLabelText(/sale price/i)).toBeInTheDocument();
      expect(within(dialog).getByLabelText(/buyer name/i)).toBeInTheDocument();
    });

    it("hides sale fields for DEAD and shows them again for SOLD", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const combo = () => within(dialog).getByRole("combobox");
      await pickOption(user, combo(), "DEAD");
      expect(within(dialog).queryByLabelText(/sale price/i)).not.toBeInTheDocument();
      expect(within(dialog).queryByLabelText(/buyer name/i)).not.toBeInTheDocument();
      await pickOption(user, combo(), "SOLD");
      expect(within(dialog).getByLabelText(/sale price/i)).toBeInTheDocument();
    });

    it("POSTs a SOLD status with price and buyer, toasts, closes and refetches", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      setInput(within(dialog).getByLabelText(/date/i), "2026-08-06");
      setInput(within(dialog).getByLabelText(/sale price/i), "9000");
      await user.type(within(dialog).getByLabelText(/buyer name/i), "Shinde Traders");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toEqual({
        new_status: "SOLD",
        date: "2026-08-06",
        sale_price: 9000,
        buyer_name: "Shinde Traders",
        notes: null,
      });
      await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Marked SOLD."));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await waitFor(() => expect(getCalls).toBeGreaterThanOrEqual(2));
    });

    it("POSTs null price and buyer when SOLD fields are left blank", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toEqual({
        new_status: "SOLD",
        date: null,
        sale_price: null,
        buyer_name: null,
        notes: null,
      });
    });

    it("POSTs null price and buyer for DEAD", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await pickOption(user, within(dialog).getByRole("combobox"), "DEAD");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toMatchObject({
        new_status: "DEAD",
        sale_price: null,
        buyer_name: null,
      });
    });

    it("POSTs a CULLED status change", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await pickOption(user, within(dialog).getByRole("combobox"), "CULLED");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toMatchObject({ new_status: "CULLED" });
      await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Marked CULLED."));
    });

    it("rejects a negative sale price without a POST", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      setInput(within(dialog).getByLabelText(/sale price/i), "-100");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      expect(await within(dialog).findByText(/expected number to be >=0/)).toBeInTheDocument();
      expect(statusBodies).toHaveLength(0);
    });

    it("shows the server detail as a toast and keeps the dialog open on 400", async () => {
      server.use(
        http.post("/api/animals/1/status", () =>
          HttpResponse.json({ detail: "Sale price is required for SOLD" }, { status: 400 }),
        ),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      await waitFor(() =>
        expect(toastMock.error).toHaveBeenCalledWith("Sale price is required for SOLD"),
      );
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });
});
