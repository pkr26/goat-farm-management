/**
 * Animal profile status dialog — aria wiring for the sale-rate pair and the
 * necropsy findings field (2026-09-30 fresh mutation campaign): complements
 * page.dialog-validation.test.tsx, which pins date/sale-price/buyer and the
 * mortality cause/report-date pair but not these fields.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

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

describe("AnimalProfilePage status-dialog aria wiring (rate pair, necropsy)", () => {
  let statusBodies: Record<string, unknown>[];

  beforeEach(() => {
    nav.id = "1";
    statusBodies = [];
    server.use(
      http.get("/api/animals/1", () => HttpResponse.json(PROFILE)),
      http.post("/api/animals/1/status", async ({ request }) => {
        statusBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({}, { status: 201 });
      }),
      http.get("/api/health/restrictions/1", () =>
        HttpResponse.json({ restricted: false, reason: null }),
      ),
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

  it("wires sale weight and price-per-kg to their own alerts when rejected", async () => {
    const user = userEvent.setup();
    await renderProfile();
    const dialog = await openDialog(user, "Change status");

    const weight = within(dialog).getByLabelText(/weight at sale/i);
    const rate = within(dialog).getByLabelText(/price per kg/i);
    expect(weight).not.toHaveAttribute("aria-invalid");
    expect(rate).not.toHaveAttribute("aria-invalid");

    // Sale-dialog DOM caps: buyer name at 120, notes 255 in a 2-row box.
    expect(within(dialog).getByLabelText(/buyer name/i)).toHaveAttribute("maxlength", "120");
    const saleNotes = within(dialog).getByLabelText(/^notes/i);
    expect(saleNotes).toHaveAttribute("maxlength", "255");
    expect(saleNotes).toHaveAttribute("rows", "2");

    fireEvent.change(weight, { target: { value: "-5" } });
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(document.getElementById("status-sale-weight-error")).not.toBeNull(),
    );
    expect(weight).toHaveAttribute("aria-invalid", "true");
    expect(weight).toHaveAttribute("aria-describedby", "status-sale-weight-error");
    expect(statusBodies).toHaveLength(0);

    // The rate is only reachable with a positive weight on file; clearing
    // the weight afterwards leaves the remembered rate meaningless — the
    // refine must flag the (now disabled) rate field itself.
    fireEvent.change(weight, { target: { value: "30" } });
    fireEvent.change(rate, { target: { value: "500" } });
    fireEvent.change(weight, { target: { value: "" } });
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(document.getElementById("status-price-per-kg-error")).not.toBeNull(),
    );
    expect(rate).toHaveAttribute("aria-invalid", "true");
    expect(rate).toHaveAttribute("aria-describedby", "status-price-per-kg-error");
    expect(statusBodies).toHaveLength(0);
  });

  it("wires the necropsy findings textarea when its 4 000-char cap is exceeded", async () => {
    const user = userEvent.setup();
    await renderProfile();
    const dialog = await openDialog(user, "Change status");
    await pickOption(user, within(dialog).getByLabelText(/new status/i), "Dead");
    // Mortality-cause cap: 120 characters.
    expect(within(dialog).getByLabelText("Mortality cause")).toHaveAttribute("maxlength", "120");
    await user.click(
      within(dialog).getByRole("checkbox", { name: /scheduled\/notifiable disease/i }),
    );
    expect(within(dialog).getByLabelText(/suspected disease/i)).toHaveAttribute("maxlength", "120");
    await user.click(within(dialog).getByRole("checkbox", { name: /necropsy performed/i }));

    const findings = within(dialog).getByLabelText(/necropsy findings/i);
    expect(findings).not.toHaveAttribute("aria-invalid");
    // DOM caps the schema enforces: a 3-row box bounded at 4 000 characters.
    expect(findings).toHaveAttribute("rows", "3");
    expect(findings).toHaveAttribute("maxlength", "4000");
    // The textarea's own maxLength clips interactive typing, so reach the cap
    // error with a programmatic over-long value.
    fireEvent.change(findings, { target: { value: "f".repeat(4_001) } });
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

    await waitFor(() =>
      expect(document.getElementById("necropsy-findings-error")).not.toBeNull(),
    );
    expect(findings).toHaveAttribute("aria-invalid", "true");
    expect(findings).toHaveAttribute("aria-describedby", "necropsy-findings-error");
    expect(statusBodies).toHaveLength(0);
  });

  it("caps the clearance reference at 255 characters", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/animals/1", () =>
        HttpResponse.json({
          ...PROFILE,
          animal: {
            ...PROFILE.animal,
            movement_restricted: true,
            restriction_reason: "Scheduled-disease suspicion",
            restriction_version: 1,
          },
        }),
      ),
      http.post("/api/health/restrictions/1/clear", () => HttpResponse.json({})),
    );
    await renderProfile();
    const dialog = await openDialog(user, "Record clearance");
    expect(within(dialog).getByLabelText(/clearance reference/i)).toHaveAttribute(
      "maxlength",
      "255",
    );
  });

  it("caps dialog notes at 255 characters in 2-row boxes", async () => {
    const user = userEvent.setup();
    await renderProfile();
    const weightDialog = await openDialog(user, "Record weight");
    const weightNotes = within(weightDialog).getByLabelText(/notes/i);
    expect(weightNotes).toHaveAttribute("maxlength", "255");
    expect(weightNotes).toHaveAttribute("rows", "2");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const moveDialog = await openDialog(user, "Move bucket");
    const reason = within(moveDialog).getByLabelText(/reason/i);
    expect(reason).toHaveAttribute("maxlength", "255");
    expect(reason).toHaveAttribute("rows", "2");
  });
});
