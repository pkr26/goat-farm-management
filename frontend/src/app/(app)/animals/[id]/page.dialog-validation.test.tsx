/**
 * Animal profile page — dialog branch coverage complementing page.test.tsx:
 * the per-field `aria-invalid` / `role="alert"` wiring of the Record weight,
 * Move bucket and Change status forms (clean and rejected), the weight date
 * boundary, the label and disabled states that hold for the whole submit
 * attempt, the bucket echoed by the closed Select trigger, and the
 * hidden-field cleanup that stops a stale sale or mortality value from
 * silently blocking a later status change — plus the estimated-DOB remedy
 * a DOB-less male sale must collect and carry (2026-09-20 audit P1-7).
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
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

/** A field the user has not broken yet carries no invalid state at all: an
 * `aria-invalid="false"` on every input is noise screen readers announce. */
function expectPristine(field: HTMLElement) {
  expect(field).not.toHaveAttribute("aria-invalid");
  expect(field).not.toHaveAttribute("aria-describedby");
}

function expectRejected(field: HTMLElement, message: string | RegExp) {
  expect(field).toHaveAttribute("aria-invalid", "true");
  expect(field).toHaveAccessibleDescription(message);
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
  dam_id: null,
  sire_id: null,
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
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
  days_in_current_bucket: 12,
};

const PROFILE = {
  animal: ANIMAL,
  kids: [],
  kids_total: 0,
  kids_offset: 0,
  weights: [],
  weights_total: 0,
  weights_offset: 0,
  moves: [],
  moves_total: 0,
  moves_offset: 0,
  health_events: [],
  health_events_total: 0,
  health_events_offset: 0,
  breedings: [],
  breedings_total: 0,
  breedings_offset: 0,
  history_limit: 25,
};

describe("AnimalProfilePage dialog branches", () => {
  let weightBodies: Record<string, unknown>[];
  let moveBodies: Record<string, unknown>[];
  let statusBodies: Record<string, unknown>[];

  beforeEach(() => {
    nav.id = "1";
    nav.search = "";
    weightBodies = [];
    moveBodies = [];
    statusBodies = [];
    vi.clearAllMocks();
    server.use(
      http.get("/api/animals/1", () => HttpResponse.json(PROFILE)),
      http.post("/api/animals/1/weight", async ({ request }) => {
        weightBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          { id: 99, date: farmToday(), weight_kg: 30, bcs: null, notes: null },
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

  describe("record weight dialog", () => {
    it("opens with every field free of invalid state", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");

      expectPristine(within(dialog).getByLabelText(/^Date/));
      expectPristine(within(dialog).getByLabelText(/weight \(kg\)/i));
      expectPristine(within(dialog).getByLabelText(/bcs/i));
      expectPristine(within(dialog).getByLabelText(/notes/i));
      expect(within(dialog).queryAllByRole("alert")).toHaveLength(0);
    });

    it("marks every rejected weight field invalid and names its own error", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      const date = within(dialog).getByLabelText(/^Date/);
      const weight = within(dialog).getByLabelText(/weight \(kg\)/i);
      const bcs = within(dialog).getByLabelText(/bcs/i);
      const notes = within(dialog).getByLabelText(/notes/i);

      setInput(date, addDays(farmToday(), 1));
      setInput(bcs, "0");
      setInput(notes, "n".repeat(256));
      await user.click(within(dialog).getByRole("button", { name: "Save" }));

      expect(
        await within(dialog).findByText("Date can't be in the future"),
      ).toBeInTheDocument();
      expectRejected(date, "Date can't be in the future");
      expectRejected(weight, "Weight must be greater than 0");
      expectRejected(bcs, /expected number to be >=1/);
      expectRejected(notes, "Notes cannot exceed 255 characters");
      // Each message is announced, not merely painted red.
      expect(within(dialog).getAllByRole("alert")).toHaveLength(4);
      expect(weightBodies).toHaveLength(0);
    });

    it("accepts today as the weight date and drops the invalid state once fixed", async () => {
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

      // Today is the boundary the `max` attribute advertises, so it must be
      // accepted rather than treated as "future".
      setInput(date, farmToday());
      await user.click(within(dialog).getByRole("button", { name: "Save" }));

      await waitFor(() => expect(weightBodies).toHaveLength(1));
      expect(weightBodies[0]).toEqual({
        date: farmToday(),
        weight_kg: 30,
        bcs: null,
        notes: null,
      });
    });

    it("owns the weight form for the whole submit attempt and hands it back on rejection", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Record weight");
      const form = dialog.querySelector("form") as HTMLFormElement;
      const fieldset = dialog.querySelector("fieldset") as HTMLFieldSetElement;
      const save = within(dialog).getByRole("button", { name: "Save" });
      expect(save).not.toHaveAttribute("disabled");

      // Validation is asynchronous: the button has to claim the submit before
      // the resolver answers, or a second click queues a duplicate write.
      fireEvent.submit(form);
      expect(save).toHaveTextContent(/^Saving…$/);
      expect(save).toHaveAttribute("disabled");
      expect(fieldset).toBeDisabled();

      expect(
        await within(dialog).findByText("Weight must be greater than 0"),
      ).toBeInTheDocument();
      expect(save).toHaveTextContent(/^Save$/);
      expect(save).not.toHaveAttribute("disabled");
      expect(fieldset).toBeEnabled();
      expect(weightBodies).toHaveLength(0);
    });
  });

  describe("move bucket dialog", () => {
    it("opens with the bucket trigger and reason free of invalid state", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");

      expectPristine(within(dialog).getByRole("combobox"));
      expectPristine(within(dialog).getByLabelText(/reason/i));
      expect(within(dialog).queryAllByRole("alert")).toHaveLength(0);
    });

    it("marks the bucket trigger and reason invalid when the move is rejected", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      const trigger = within(dialog).getByRole("combobox");
      const reason = within(dialog).getByLabelText(/reason/i);

      setInput(reason, "r".repeat(256));
      await user.click(within(dialog).getByRole("button", { name: "Move" }));

      const alerts = await within(dialog).findAllByRole("alert");
      expect(alerts).toHaveLength(2);
      // The enum rejection names the buckets the API accepts, so the trigger
      // itself must carry it — the message sits below a Base UI button, not an
      // input, and is otherwise unreachable from the control.
      expectRejected(trigger, /^Invalid option: expected one of "QUARANTINE"/);
      expectRejected(reason, "Reason cannot exceed 255 characters");
      expect(moveBodies).toHaveLength(0);
    });

    it("echoes the chosen bucket in the closed trigger", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      const trigger = within(dialog).getByRole("combobox");
      expect(trigger).toHaveTextContent("Choose bucket…");

      await pickOption(user, trigger, "Resting");

      expect(trigger).toHaveTextContent("Resting");
      expect(trigger).not.toHaveTextContent("Choose bucket…");
    });

    it("owns the move form for the whole submit attempt and hands it back on rejection", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Move bucket");
      const form = dialog.querySelector("form") as HTMLFormElement;
      const fieldset = dialog.querySelector("fieldset") as HTMLFieldSetElement;
      const move = within(dialog).getByRole("button", { name: "Move" });
      expect(move).not.toHaveAttribute("disabled");

      fireEvent.submit(form);
      expect(move).toHaveTextContent(/^Moving…$/);
      expect(move).toHaveAttribute("disabled");
      expect(fieldset).toBeDisabled();

      await within(dialog).findAllByRole("alert");
      expect(move).toHaveTextContent(/^Move$/);
      expect(move).not.toHaveAttribute("disabled");
      expect(fieldset).toBeEnabled();
      expect(moveBodies).toHaveLength(0);
    });
  });

  describe("change status dialog", () => {
    it("opens with the status, sale and mortality fields free of invalid state", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");

      expectPristine(within(dialog).getByLabelText(/^Date/));
      expectPristine(within(dialog).getByLabelText(/sale price/i));
      expectPristine(within(dialog).getByLabelText(/buyer name/i));

      await pickOption(user, within(dialog).getByLabelText(/new status/i), "DEAD");
      expectPristine(within(dialog).getByLabelText("Mortality cause"));
      expectPristine(within(dialog).getByLabelText("Mortality reported date"));
      expect(within(dialog).queryAllByRole("alert")).toHaveLength(0);
    });

    it("marks every rejected sale field invalid and names its own error", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const date = within(dialog).getByLabelText(/^Date/);
      const price = within(dialog).getByLabelText(/sale price/i);
      const buyer = within(dialog).getByLabelText(/buyer name/i);

      setInput(date, addDays(farmToday(), 1));
      setInput(price, "-100");
      setInput(buyer, "b".repeat(121));
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      expect(
        await within(dialog).findByText("Date can't be in the future"),
      ).toBeInTheDocument();
      expectRejected(date, "Date can't be in the future");
      expect(price).toHaveAttribute("aria-invalid", "true");
      expectRejected(buyer, "Buyer name cannot exceed 120 characters");
      expect(statusBodies).toHaveLength(0);
    });

    it("marks every rejected mortality field invalid and names its own error", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await pickOption(user, within(dialog).getByLabelText(/new status/i), "DEAD");
      const cause = within(dialog).getByLabelText("Mortality cause");
      const reported = within(dialog).getByLabelText("Mortality reported date");

      setInput(cause, "c".repeat(121));
      setInput(reported, addDays(farmToday(), 1));
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      expect(
        await within(dialog).findByText("Mortality cause cannot exceed 120 characters"),
      ).toBeInTheDocument();
      expectRejected(cause, "Mortality cause cannot exceed 120 characters");
      expectRejected(reported, "Date can't be in the future");
      expect(statusBodies).toHaveLength(0);
    });

    it("reports no scheduled disease for a DEAD status left unreported", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      await pickOption(user, within(dialog).getByLabelText(/new status/i), "DEAD");
      await user.type(within(dialog).getByLabelText("Mortality cause"), "Bloat");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      await waitFor(() => expect(statusBodies).toHaveLength(1));
      // The statutory escalation is opt-in: an unchecked report must reach the
      // API as a plain false, never as "DEAD implies notifiable".
      expect(statusBodies[0]).toEqual({
        new_status: "DEAD",
        date: null,
        sale_price: null,
        sale_weight_kg: null,
        sale_price_per_kg: null,
        buyer_name: null,
        notes: null,
        mortality_cause: "Bloat",
        mortality_cause_code: null,
        disposal_method: null,
        estimated_dob: null,
        mortality_reported_at: null,
        necropsy_done: false,
        necropsy_findings: null,
        suspected_scheduled_disease: false,
        suspected_disease: null,
        authority_notified_at: null,
      });
    });

    it("discards a sale price typed before the status became DEAD", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      // A price the user abandons must not survive as a hidden, unfixable
      // validation failure once the sale fields are gone.
      setInput(within(dialog).getByLabelText(/sale price/i), "-100");
      await pickOption(user, within(dialog).getByLabelText(/new status/i), "DEAD");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toMatchObject({ new_status: "DEAD", sale_price: null });
    });

    it("discards a mortality date typed before the status left DEAD", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const combo = () => within(dialog).getByLabelText(/new status/i);
      await pickOption(user, combo(), "DEAD");
      setInput(
        within(dialog).getByLabelText("Mortality reported date"),
        addDays(farmToday(), 1),
      );
      await pickOption(user, combo(), "CULLED");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toMatchObject({
        new_status: "CULLED",
        mortality_reported_at: null,
      });
      expect(within(dialog).queryByText("Date can't be in the future")).not.toBeInTheDocument();
    });

    it("keeps a sale price when moving between two sale-capable statuses", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      setInput(within(dialog).getByLabelText(/sale price/i), "4500");
      await pickOption(user, within(dialog).getByLabelText(/new status/i), "CULLED");

      expect(within(dialog).getByLabelText(/sale price/i)).toHaveValue(4500);
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toMatchObject({ new_status: "CULLED", sale_price: 4500 });
    });

    it("keeps a rejected sale price flagged when switching to the other sale-capable status", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const price = within(dialog).getByLabelText(/sale price/i);
      setInput(price, "-100");
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      expect(await within(dialog).findByText(/expected number to be >=0/)).toBeInTheDocument();

      // CULLED books proceeds exactly like SOLD, so the rejected amount is
      // still live — clearing the flag would hand back a form that only looks
      // fixed.
      await pickOption(user, within(dialog).getByLabelText(/new status/i), "CULLED");
      expect(within(dialog).getByLabelText(/sale price/i)).toHaveValue(-100);
      expect(within(dialog).getByText(/expected number to be >=0/)).toBeInTheDocument();
      expect(within(dialog).getByLabelText(/sale price/i)).toHaveAttribute(
        "aria-invalid",
        "true",
      );
      expect(statusBodies).toHaveLength(0);
    });

    it("keeps the mortality details when DEAD is picked again", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const combo = () => within(dialog).getByLabelText(/new status/i);
      await pickOption(user, combo(), "DEAD");
      await user.type(within(dialog).getByLabelText("Mortality cause"), "Bloat");
      await user.click(
        within(dialog).getByRole("checkbox", {
          name: "Suspected scheduled/notifiable disease",
        }),
      );

      await pickOption(user, combo(), "DEAD");

      expect(within(dialog).getByLabelText("Mortality cause")).toHaveValue("Bloat");
      expect(
        within(dialog).getByRole("checkbox", {
          name: "Suspected scheduled/notifiable disease",
        }),
      ).toBeChecked();
    });

    it("owns the status form for the whole submit attempt and hands it back on rejection", async () => {
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const form = dialog.querySelector("form") as HTMLFormElement;
      const fieldset = dialog.querySelector("fieldset") as HTMLFieldSetElement;
      const confirm = within(dialog).getByRole("button", { name: "Confirm" });
      setInput(within(dialog).getByLabelText(/^Date/), addDays(farmToday(), 1));

      fireEvent.submit(form);
      expect(confirm).toHaveTextContent(/^Saving…$/);
      expect(confirm).toHaveAttribute("disabled");
      expect(fieldset).toBeDisabled();

      expect(
        await within(dialog).findByText("Date can't be in the future"),
      ).toBeInTheDocument();
      expect(confirm).toHaveTextContent(/^Confirm$/);
      expect(confirm).not.toHaveAttribute("disabled");
      expect(fieldset).toBeEnabled();
      expect(statusBodies).toHaveLength(0);
    });
  });

  describe("DOB-less male sale estimate (2026-09-20 audit P1-7)", () => {
    // A buck with neither a recorded nor an estimated birth date cannot pass
    // the backend's meat-sale age floor, and DOB is not editable after
    // creation — the estimate is the only field-editable remedy, so the SOLD
    // dialog must collect it instead of letting the sale 422 server-side.
    function useAnimalWith(animal: Record<string, unknown>) {
      server.use(
        http.get("/api/animals/1", () =>
          HttpResponse.json({ ...PROFILE, animal: { ...ANIMAL, ...animal } }),
        ),
      );
    }

    function useDoblessBuck() {
      useAnimalWith({ sex: "M", date_of_birth: null, estimated_dob: null });
    }

    it("renders a date input for the estimate on a DOB-less male SOLD", async () => {
      useDoblessBuck();
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");

      const estimate = within(dialog).getByLabelText(/estimated date of birth/i);
      expect(estimate).toHaveAttribute("type", "date");
      // Pristine: no invalid state, and the describedby points at the
      // explanatory hint rather than an error.
      expect(estimate).not.toHaveAttribute("aria-invalid");
      expect(estimate).toHaveAccessibleDescription(
        /the estimate is saved to the animal so the meat-sale age floor/i,
      );
    });

    it("blocks the sale without an estimate and never posts", async () => {
      useDoblessBuck();
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const estimate = within(dialog).getByLabelText(/estimated date of birth/i);

      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      expect(
        await within(dialog).findByText(
          "This male has no birth or estimated date on record — estimate one so the sale age floor can be verified",
        ),
      ).toBeInTheDocument();
      expectRejected(
        estimate,
        "This male has no birth or estimated date on record — estimate one so the sale age floor can be verified",
      );
      expect(statusBodies).toHaveLength(0);
    });

    it("posts the chosen estimate with the SOLD payload", async () => {
      useDoblessBuck();
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");
      const estimate = addDays(farmToday(), -400);

      setInput(within(dialog).getByLabelText(/estimated date of birth/i), estimate);
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toMatchObject({
        new_status: "SOLD",
        estimated_dob: estimate,
      });
    });

    it.each([
      {
        label: "a male with a recorded DOB",
        animal: { sex: "M" },
      },
      {
        label: "a female without any birth date",
        animal: { sex: "F", date_of_birth: null, estimated_dob: null },
      },
      {
        label: "a male whose estimate is already on record",
        animal: { sex: "M", date_of_birth: null, estimated_dob: "2025-05-01" },
      },
    ])("never asks $label for an estimate and posts estimated_dob null", async ({ animal }) => {
      useAnimalWith(animal);
      const user = userEvent.setup();
      await renderProfile();
      const dialog = await openDialog(user, "Change status");

      expect(
        within(dialog).queryByLabelText(/estimated date of birth/i),
      ).not.toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

      await waitFor(() => expect(statusBodies).toHaveLength(1));
      expect(statusBodies[0]).toMatchObject({ estimated_dob: null });
    });
  });
});
