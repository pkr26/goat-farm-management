/**
 * Animal profile page — a second pass over the exact text the page promises
 * its operators: the header description, the back link for every permitted
 * origin, the em-dash/blank fallbacks in the detail and history tables,
 * health-event traceability, the accessible wiring of each validation message,
 * in-flight button labels, and the clearance dialog's reference guard/reset.
 */

import { configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import AnimalProfilePage from "./page";

const nav = vi.hoisted(() => ({ id: "1", search: "" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals/1",
  useSearchParams: () => new URLSearchParams(nav.search),
  useParams: () => ({ id: nav.id }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const toastMock = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

beforeAll(() => {
  // Dialog validation round-trips can outrun the 1 s findBy*/waitFor default
  // when this file shares a loaded machine with the rest of the gate.
  configure({ asyncUtilTimeout: 3000 });
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

/** A response the test releases by hand, to observe in-flight button labels. */
function createGate() {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { gate, release };
}

function cardFor(title: string): HTMLElement {
  return screen.getByText(title).closest('[data-slot="card"]') as HTMLElement;
}

function bodyRows(card: HTMLElement): HTMLElement[] {
  return within(card).getAllByRole("row").slice(1);
}

function cellTexts(row: HTMLElement): string[] {
  return Array.from(row.querySelectorAll("td")).map((cell) => cell.textContent ?? "");
}

/** The <dd> rendered next to a <dt> label in the Details card. */
function detailValue(label: string): string {
  return screen.getByText(label).nextElementSibling?.textContent ?? "";
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
  sale_weight_kg: null,
  sale_price_per_kg: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  movement_restricted: false,
  restriction_reason: null,
  suspected_scheduled_disease: false,
  suspected_disease: null,
  authority_notified_at: null,
  restriction_cleared_at: null,
  restriction_cleared_by_id: null,
  restriction_clearance_reference: null,
  restriction_version: 0,
  mortality_cause: null,
  mortality_cause_code: null,
  disposal_method: null,
  necropsy_done: false,
  necropsy_findings: null,
  mortality_reported_at: null,
  notes: "Calm doe, good milker.",
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
  days_in_current_bucket: 12,
};

const HEALTH_EVENT = {
  animal_id: 1,
  purchase_batch_id: null,
  dose: null,
  route: null,
  vet_name: null,
  schedule_template_name: null,
  next_due_authority: null,
  product_manufactured_on: null,
  product_expires_on: null,
  vaccine_valid_until: null,
  official_tag_number: null,
  administered_by: null,
  authority_notified_at: null,
  isolation_started_at: null,
  notes: null,
};

const PROFILE = {
  animal: ANIMAL,
  kids: [],
  kids_total: 0,
  kids_offset: 0,
  weights: [
    { id: 11, date: "2026-07-01", weight_kg: 28.44, bcs: 3, notes: "Pre-monsoon" },
    { id: 12, date: "2026-08-01", weight_kg: 32.5, bcs: null, notes: null },
  ],
  weights_total: 2,
  weights_offset: 0,
  moves: [
    {
      id: 31,
      from_bucket: null,
      to_bucket: "QUARANTINE",
      reason: null,
      effective_date: "2026-06-20",
      // Naive UTC, as the backend serialises BucketMoveOut.moved_at: 20:15 UTC
      // on 20 Jun is already 01:45 on 21 Jun for the Asia/Kolkata test farm.
      moved_at: "2026-06-20T20:15:00",
    },
    {
      id: 32,
      from_bucket: "QUARANTINE",
      to_bucket: "FEMALE_KIDS",
      reason: "Weaned into the doeling pen",
      effective_date: "2026-07-01",
      moved_at: "2026-07-01T04:30:00",
    },
    {
      id: 33,
      from_bucket: "FEMALE_KIDS",
      to_bucket: "PREGNANCY_EARLY",
      reason: "Confirmed pregnant",
      effective_date: "2026-07-25",
      moved_at: "2026-07-25T06:00:00",
    },
  ],
  moves_total: 3,
  moves_offset: 0,
  health_events: [
    {
      ...HEALTH_EVENT,
      id: 41,
      date: "2026-07-10",
      type: "VACCINE",
      product_name: "PPR vaccine",
      disease_target: null,
      cost: 45.5,
      next_due_date: "2027-07-10",
      product_lot: "PPR-2607",
      certificate_number: "CERT-41",
      withdrawal_until: "2026-07-20",
      suspected_scheduled_disease: true,
    },
    {
      ...HEALTH_EVENT,
      id: 42,
      date: "2026-07-12",
      type: "DEWORMING",
      product_name: null,
      disease_target: null,
      cost: null,
      next_due_date: null,
      product_lot: null,
      certificate_number: null,
      withdrawal_until: null,
      suspected_scheduled_disease: false,
    },
  ],
  health_events_total: 2,
  health_events_offset: 0,
  breedings: [],
  breedings_total: 0,
  breedings_offset: 0,
  history_limit: 25,
};

function profileWith(animal: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  return { ...PROFILE, ...extra, animal: { ...ANIMAL, ...animal } };
}

describe("AnimalProfilePage rendering and dialog contracts", () => {
  let getCalls: number;
  let weightBodies: Record<string, unknown>[];
  let moveBodies: Record<string, unknown>[];
  let statusBodies: Record<string, unknown>[];
  let clearanceBodies: Record<string, unknown>[];

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
    nav.search = "";
    getCalls = 0;
    weightBodies = [];
    moveBodies = [];
    statusBodies = [];
    clearanceBodies = [];
    vi.clearAllMocks();
    useProfileHandler();
    server.use(
      http.post("/api/animals/1/weight", async ({ request }) => {
        weightBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          { id: 99, date: "2026-08-06", weight_kg: 30, bcs: null, notes: null },
          { status: 201 },
        );
      }),
      http.post("/api/animals/1/move", async ({ request }) => {
        moveBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({}, { status: 201 });
      }),
      http.post("/api/animals/1/status", async ({ request }) => {
        statusBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({}, { status: 201 });
      }),
      http.post("/api/health/restrictions/1/clear", async ({ request }) => {
        clearanceBodies.push((await request.json()) as Record<string, unknown>);
        return new HttpResponse(null, { status: 204 });
      }),
      http.get("/api/health/restrictions/1", ({ request }) => {
        const params = new URL(request.url).searchParams;
        return HttpResponse.json({
          animal_id: 1,
          restriction_version: 0,
          active: false,
          actions: [],
          total: 0,
          limit: Number(params.get("limit") ?? 25),
          offset: Number(params.get("offset") ?? 0),
        });
      }),
    );
  });

  async function renderProfile() {
    const view = renderWithProviders(<AnimalProfilePage />);
    await screen.findByRole("heading", { level: 1, name: /G-001/ });
    return view;
  }

  async function openDialog(user: User, button: string) {
    await user.click(screen.getByRole("button", { name: button }));
    return await screen.findByRole("dialog");
  }

  describe("header", () => {
    it("describes the animal by breed, sex and humanised bucket", async () => {
      await renderProfile();

      expect(screen.getByText("Osmanabadi · Female · Pregnancy A")).toBeInTheDocument();
    });

    it("describes a buck as Male in the same header line", async () => {
      useProfileHandler(profileWith({ sex: "M", current_bucket: "MALE_KIDS" }));
      await renderProfile();

      expect(screen.getByText("Osmanabadi · Male · Male kids")).toBeInTheDocument();
    });

    it("renders only the tag and the status badge when the animal has no name", async () => {
      useProfileHandler(profileWith({ name: null }));
      await renderProfile();

      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/^G-001\s*Active$/);
    });

    it("labels and targets the back link for every permitted origin", async () => {
      const origins = [
        ["", "/animals", "Back to animals"],
        ["?returnTo=%2Ftasks", "/tasks", "Back to tasks"],
        ["?returnTo=%2Fhealth", "/health", "Back to health"],
        ["?returnTo=%2Fdashboard", "/dashboard", "Back to dashboard"],
      ] as const;

      for (const [search, href, label] of origins) {
        nav.search = search;
        const view = renderWithProviders(<AnimalProfilePage />);
        await screen.findByRole("heading", { level: 1, name: /G-001/ });

        const back = screen.getAllByRole("link")[0];
        expect(back).toHaveAttribute("href", href);
        expect(back).toHaveTextContent(label);
        view.unmount();
      }
    });
  });

  describe("details card fallbacks", () => {
    it("shows a dash for a purchase with no recorded seller", async () => {
      useProfileHandler(
        profileWith({
          source: "PURCHASED",
          purchase_date: "2026-01-15",
          purchase_price: 12500,
          seller_name: null,
        }),
      );
      await renderProfile();

      expect(detailValue("Seller")).toBe("—");
    });

    it("dashes an unknown mortality cause and answers No when nothing was suspected", async () => {
      useProfileHandler(
        profileWith({
          status: "DEAD",
          status_date: "2026-07-30",
          mortality_cause: null,
          mortality_cause_code: null,
          disposal_method: null,
        estimated_dob: null,
          necropsy_done: false,
          necropsy_findings: null,
          suspected_scheduled_disease: false,
        }),
      );
      renderWithProviders(<AnimalProfilePage />);
      await screen.findByText("Status date");

      expect(detailValue("Mortality cause")).toBe("—");
      expect(detailValue("Scheduled disease suspected")).toBe("No");
    });

    it("answers Yes when a scheduled disease was suspected at death", async () => {
      useProfileHandler(
        profileWith({
          status: "DEAD",
          status_date: "2026-07-30",
          mortality_cause: "Suspected PPR",
          suspected_scheduled_disease: true,
        }),
      );
      renderWithProviders(<AnimalProfilePage />);
      await screen.findByText("Status date");

      expect(detailValue("Mortality cause")).toBe("Suspected PPR");
      expect(detailValue("Scheduled disease suspected")).toBe("Yes");
    });

    it("withholds the scheduled-disease answer without health.view", async () => {
      // The API fails the flag closed to `false` for readers without
      // health.view, so printing "No" would assert a fact nobody was told.
      server.use(permissionsHandler(["animals.view"]));
      useProfileHandler(
        profileWith({
          status: "DEAD",
          status_date: "2026-07-30",
          suspected_scheduled_disease: false,
        }),
      );
      renderWithProviders(<AnimalProfilePage />);
      await screen.findByText("Status date");

      expect(detailValue("Scheduled disease suspected")).toBe("—");
    });

    it("shows a dash when a cleared restriction carries no reference", async () => {
      useProfileHandler(
        profileWith({
          restriction_cleared_at: "2026-08-07T10:15:00Z",
          restriction_clearance_reference: null,
        }),
      );
      await renderProfile();

      expect(detailValue("Restriction cleared")).toBe("7 Aug 2026, 3:45 pm");
      expect(detailValue("Clearance reference")).toBe("—");
    });

    it("falls back to a generic hold message when no restriction reason was given", async () => {
      useProfileHandler(
        profileWith({
          movement_restricted: true,
          restriction_reason: null,
          restriction_version: 1,
        }),
      );
      await renderProfile();

      expect(screen.getByText("A health hold is active for this animal.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Move bucket" })).not.toBeInTheDocument();
    });
  });

  describe("history tables", () => {
    it("renders the move history with a dash for the intake move and blank reasons", async () => {
      await renderProfile();
      const rows = bodyRows(cardFor("Bucket moves (3)"));

      // Bucket cells carry the humanised enum labels.
      expect(cellTexts(rows[0])).toEqual([
        "20 Jun 2026",
        "21 Jun 2026, 1:45 am",
        "—",
        "Quarantine",
        "",
      ]);
      expect(cellTexts(rows[1])).toEqual([
        "1 Jul 2026",
        "1 Jul 2026, 10:00 am",
        "Quarantine",
        "Female kids",
        "Weaned into the doeling pen",
      ]);
      expect(cellTexts(rows[2])).toEqual([
        "25 Jul 2026",
        "25 Jul 2026, 11:30 am",
        "Female kids",
        "Pregnancy A",
        "Confirmed pregnant",
      ]);
    });

    it("leaves the notes cell empty for a weight recorded without notes", async () => {
      await renderProfile();
      const rows = bodyRows(cardFor("Weight history (2)"));

      expect(cellTexts(rows[0])).toEqual(["1 Jul 2026", "28.4 kg", "3", "Pre-monsoon"]);
      expect(cellTexts(rows[1])).toEqual(["1 Aug 2026", "32.5 kg", "—", ""]);
    });

    it("joins health-event traceability and dashes an untraceable event", async () => {
      await renderProfile();
      const rows = bodyRows(cardFor("Health events (2)"));

      // Type cells carry the humanised event labels.
      expect(cellTexts(rows[0])).toEqual([
        "10 Jul 2026",
        "Vaccination",
        "PPR vaccine",
        "₹45.50",
        "10 Jul 2027",
        "Lot PPR-2607 · Cert CERT-41 · Withdrawal to 20 Jul 2026 · Scheduled-disease hold",
      ]);
      expect(cellTexts(rows[1])).toEqual([
        "12 Jul 2026",
        "Deworming",
        "—",
        "—",
        "—",
        "—",
      ]);
    });

    it("shows a dash for a restriction action with no disease target", async () => {
      server.use(
        http.get("/api/health/restrictions/1", () =>
          HttpResponse.json({
            animal_id: 1,
            restriction_version: 1,
            active: true,
            actions: [
              {
                id: 5,
                restriction_version: 1,
                action: "Placed",
                acted_at: "2026-07-01T09:00:00Z",
                acted_by_id: 7,
                action_reference: "HEALTH-EVENT-41",
                disease_target: null,
                health_event_id: 41,
              },
            ],
            total: 1,
            limit: 25,
            offset: 0,
          }),
        ),
      );
      await renderProfile();
      await screen.findByText("Movement restriction audit (1)");
      const rows = bodyRows(cardFor("Movement restriction audit (1)"));

      expect(cellTexts(rows[0])).toEqual([
        "1",
        "Placed",
        "1 Jul 2026, 2:30 pm",
        "HEALTH-EVENT-41",
        "—",
      ]);
    });

    it("offers a retry when the restriction audit fails without a server detail", async () => {
      server.use(http.get("/api/health/restrictions/1", () => HttpResponse.error()));
      await renderProfile();

      expect(
        await screen.findByText("Could not load the restriction audit."),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Retry audit" })).toBeInTheDocument();
    });
  });

  describe("page-level guards", () => {
    it("shows a generic message when the profile request fails without a detail", async () => {
      server.use(http.get("/api/animals/1", () => HttpResponse.error()));
      renderWithProviders(<AnimalProfilePage />);

      expect(await screen.findByText("Could not load this animal.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Retry animal profile" })).toBeInTheDocument();
    });

    it("rejects an exponent-style id instead of coercing it to another animal", async () => {
      let coercedAnimalCalls = 0;
      server.use(
        http.get("/api/animals/100", () => {
          coercedAnimalCalls += 1;
          return HttpResponse.json(PROFILE);
        }),
      );
      nav.id = "1e2";
      renderWithProviders(<AnimalProfilePage />);

      expect(await screen.findByText("Invalid animal id.")).toBeInTheDocument();
      expect(coercedAnimalCalls).toBe(0);
      expect(getCalls).toBe(0);
    });
  });

  describe("record weight dialog", () => {
    it("rejects a future weight date and links the message to the date field", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      const date = within(dialog).getByLabelText(/^Date/);
      setInput(date, addDays(farmToday(), 1));
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "30");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));

      expect(
        await within(dialog).findByText("Date can't be in the future"),
      ).toBeInTheDocument();
      expect(date).toHaveAccessibleDescription("Date can't be in the future");
      expect(date).toHaveAttribute("aria-invalid", "true");
      expect(weightBodies).toHaveLength(0);
    });

    it("links the weight and BCS messages to their own inputs", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      const weight = within(dialog).getByLabelText(/weight \(kg\)/i);
      const bcs = within(dialog).getByLabelText(/bcs/i);
      setInput(bcs, "6");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));

      expect(
        await within(dialog).findByText("Weight must be greater than 0"),
      ).toBeInTheDocument();
      expect(weight).toHaveAccessibleDescription("Weight must be greater than 0");
      expect(bcs).toHaveAccessibleDescription(/expected number to be <=5/);
      expect(weightBodies).toHaveLength(0);
    });

    it("labels the weight submit button while the write is in flight", async () => {
      const { gate, release } = createGate();
      server.use(
        http.post("/api/animals/1/weight", async ({ request }) => {
          weightBodies.push((await request.json()) as Record<string, unknown>);
          await gate;
          return HttpResponse.json(
            { id: 99, date: "2026-08-06", weight_kg: 31, bcs: null, notes: null },
            { status: 201 },
          );
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      setInput(within(dialog).getByLabelText(/weight \(kg\)/i), "31");
      const save = within(dialog).getByRole("button", { name: "Save" });
      await user.click(save);

      await waitFor(() => expect(save).toHaveTextContent("Saving…"));
      expect(save).toBeDisabled();

      release();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });
  });

  describe("move bucket dialog", () => {
    it("keeps the placeholder until a bucket is chosen, then shows its label", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      const trigger = within(dialog).getByRole("combobox");

      expect(trigger).toHaveTextContent("Choose bucket…");

      await pickOption(user, trigger, "Pregnancy B");
      await waitFor(() => expect(trigger).toHaveTextContent("Pregnancy B"));
      await user.click(within(dialog).getByRole("button", { name: "Move" }));

      await waitFor(() => expect(moveBodies).toHaveLength(1));
      expect(moveBodies[0]).toEqual({ to_bucket: "PREGNANCY_LATE", reason: null });
    });

    it("links the missing-bucket message to the select trigger", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      await user.click(within(dialog).getByRole("button", { name: "Move" }));

      const message = await within(dialog).findByRole("alert");
      const trigger = within(dialog).getByRole("combobox");
      expect(message.textContent).toBeTruthy();
      expect(trigger).toHaveAttribute("aria-invalid", "true");
      expect(trigger).toHaveAccessibleDescription(message.textContent as string);
      expect(moveBodies).toHaveLength(0);
    });

    it("labels the move submit button while the write is in flight", async () => {
      const { gate, release } = createGate();
      server.use(
        http.post("/api/animals/1/move", async ({ request }) => {
          moveBodies.push((await request.json()) as Record<string, unknown>);
          await gate;
          return HttpResponse.json({}, { status: 201 });
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      await pickOption(user, within(dialog).getByRole("combobox"), "Resting");
      const move = within(dialog).getByRole("button", { name: "Move" });
      await user.click(move);

      await waitFor(() => expect(move).toHaveTextContent("Moving…"));
      expect(move).toBeDisabled();

      release();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });
  });

  describe("change status dialog", () => {
    it("rejects a future status date and links the message to the date field", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const date = within(dialog).getByLabelText(/^Date/);
      setInput(date, addDays(farmToday(), 1));
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      expect(
        await within(dialog).findByText("Date can't be in the future"),
      ).toBeInTheDocument();
      expect(date).toHaveAccessibleDescription("Date can't be in the future");
      expect(statusBodies).toHaveLength(0);
    });

    it("links the sale price message to its input", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const price = within(dialog).getByLabelText(/sale price/i);
      setInput(price, "-100");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      const message = await within(dialog).findByText(/expected number to be >=0/);
      expect(price).toHaveAccessibleDescription(message.textContent as string);
      expect(statusBodies).toHaveLength(0);
    });

    it("links both mortality date messages to their own inputs", async () => {
      const user = userEvent.setup();
      const tomorrow = addDays(farmToday(), 1);
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await pickOption(user, within(dialog).getByLabelText(/new status/i), "DEAD");
      const reported = within(dialog).getByLabelText("Mortality reported date");
      setInput(reported, tomorrow);
      await user.click(
        within(dialog).getByRole("checkbox", {
          name: "Suspected scheduled/notifiable disease",
        }),
      );
      setInput(within(dialog).getByLabelText("Suspected disease *"), "PPR");
      const authority = within(dialog).getByLabelText("Authority notified date");
      setInput(authority, tomorrow);
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      expect(
        await within(dialog).findAllByText("Date can't be in the future"),
      ).toHaveLength(2);
      expect(reported).toHaveAccessibleDescription("Date can't be in the future");
      expect(authority).toHaveAccessibleDescription("Date can't be in the future");
      expect(statusBodies).toHaveLength(0);
    });

    it("does not let hidden mortality answers veto an unrelated status change", async () => {
      const user = userEvent.setup();
      const tomorrow = addDays(farmToday(), 1);
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const combo = () => within(dialog).getByLabelText(/new status/i);
      await pickOption(user, combo(), "DEAD");
      setInput(within(dialog).getByLabelText("Mortality cause"), "c".repeat(121));
      setInput(within(dialog).getByLabelText("Mortality reported date"), tomorrow);
      await user.click(
        within(dialog).getByRole("checkbox", {
          name: "Suspected scheduled/notifiable disease",
        }),
      );
      setInput(within(dialog).getByLabelText("Suspected disease *"), "d".repeat(121));
      setInput(within(dialog).getByLabelText("Authority notified date"), tomorrow);

      // Every mortality answer above is invalid on its own terms. Switching
      // away from DEAD drops them, so none may block — or reach — a sale.
      await pickOption(user, combo(), "SOLD");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toEqual({
        new_status: "SOLD",
        date: null,
        sale_price: null,
        sale_weight_kg: null,
        sale_price_per_kg: null,
        buyer_name: null,
        notes: null,
        mortality_cause: null,
        mortality_cause_code: null,
        disposal_method: null,
        estimated_dob: null,
        necropsy_done: false,
        necropsy_findings: null,
        mortality_reported_at: null,
        suspected_scheduled_disease: false,
        suspected_disease: null,
        authority_notified_at: null,
      });
    });

    it("labels the status confirm button while the write is in flight", async () => {
      const { gate, release } = createGate();
      server.use(
        http.post("/api/animals/1/status", async ({ request }) => {
          statusBodies.push((await request.json()) as Record<string, unknown>);
          await gate;
          return HttpResponse.json({}, { status: 201 });
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const confirm = within(dialog).getByRole("button", { name: "Confirm" });
      await user.click(confirm);

      await waitFor(() => expect(confirm).toHaveTextContent("Saving…"));
      expect(confirm).toBeDisabled();

      release();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });
  });

  describe("clearance dialog", () => {
    const RESTRICTED = profileWith({
      movement_restricted: true,
      restriction_reason: "Scheduled-disease suspicion",
      restriction_version: 1,
    });

    it("links a rejected clearance to the reference field", async () => {
      useProfileHandler(RESTRICTED);
      server.use(
        http.post("/api/health/restrictions/1/clear", async ({ request }) => {
          clearanceBodies.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json(
            { detail: "Clearance reference must name the issuing authority" },
            { status: 400 },
          );
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      const reference = within(dialog).getByLabelText("Clearance reference *");
      const confirm = within(dialog).getByRole("button", { name: "Confirm clearance" });
      // The reference is the whole point of the audit entry, so the button
      // stays inert until one is typed.
      expect(confirm).toBeDisabled();

      await user.type(reference, "VET-2026");
      await user.click(confirm);

      const message = await within(dialog).findByRole("alert");
      expect(message).toHaveTextContent("Clearance reference must name the issuing authority");
      expect(reference).toHaveAttribute("aria-invalid", "true");
      expect(reference).toHaveAccessibleDescription(
        "Clearance reference must name the issuing authority",
      );
      expect(reference).toHaveValue("VET-2026");
    });

    it("confirms the audit trail in the toast and never prefills the next hold", async () => {
      let cleared = false;
      server.use(
        http.get("/api/animals/1", () => {
          getCalls += 1;
          // A second hold is placed while the first clearance is recorded.
          return HttpResponse.json(
            cleared
              ? profileWith({
                  movement_restricted: true,
                  restriction_reason: "Fresh contact-tracing hold",
                  restriction_version: 2,
                })
              : RESTRICTED,
          );
        }),
        http.post("/api/health/restrictions/1/clear", async ({ request }) => {
          clearanceBodies.push((await request.json()) as Record<string, unknown>);
          cleared = true;
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      await user.type(
        within(dialog).getByLabelText("Clearance reference *"),
        "VET-CLEAR-2026-18",
      );
      await user.click(within(dialog).getByRole("button", { name: "Confirm clearance" }));

      await waitFor(() => expect(clearanceBodies).toHaveLength(1));
      await waitFor(() =>
        expect(toastMock.success).toHaveBeenCalledWith(
          "Movement restriction cleared with an audit reference.",
        ),
      );
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await screen.findByText("Fresh contact-tracing hold");

      const reopened = await openDialog(user, "Record clearance");
      expect(within(reopened).getByLabelText("Clearance reference *")).toHaveValue("");
      expect(
        within(reopened).getByRole("button", { name: "Confirm clearance" }),
      ).toBeDisabled();
    });

    it("labels the clearance button while the write is in flight", async () => {
      const { gate, release } = createGate();
      useProfileHandler(RESTRICTED);
      server.use(
        http.post("/api/health/restrictions/1/clear", async ({ request }) => {
          clearanceBodies.push((await request.json()) as Record<string, unknown>);
          await gate;
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      await user.type(within(dialog).getByLabelText("Clearance reference *"), "VET-SLOW-1");
      const confirm = within(dialog).getByRole("button", { name: "Confirm clearance" });
      await user.click(confirm);

      await waitFor(() => expect(confirm).toHaveTextContent("Recording…"));

      release();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("parks the retry while the superseded episode is still the one on screen", async () => {
      useProfileHandler(RESTRICTED);
      server.use(
        http.post("/api/health/restrictions/1/clear", async ({ request }) => {
          clearanceBodies.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json(
            { detail: "Movement restriction was superseded; refresh the current episode" },
            { status: 409 },
          );
        }),
      );
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record clearance");
      await user.type(within(dialog).getByLabelText("Clearance reference *"), "VET-STALE-7");
      await user.click(within(dialog).getByRole("button", { name: "Confirm clearance" }));

      // The refresh returns the same episode version, so the operator is still
      // looking at the restriction the server just rejected.
      const parked = await within(dialog).findByRole("button", {
        name: "Waiting for current episode…",
      });
      expect(parked).toBeDisabled();
      expect(
        within(dialog).getByRole("button", { name: "Refresh episode" }),
      ).toBeInTheDocument();
      expect(clearanceBodies).toHaveLength(1);
    });
  });
});
