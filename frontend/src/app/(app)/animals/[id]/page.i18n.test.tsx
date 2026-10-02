/**
 * Animal profile page i18n: the record-weight dialog (the page's most
 * worker-facing form) renders fully in Telugu when the language is te —
 * dialog title, field labels and the save action all resolve through the
 * animalDetail.* catalog keys — and the zod validation messages follow the
 * same language because the dialog rebuilds its schema from the active `t`.
 * Mirrors health/page.i18n.test.tsx.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalProfilePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals/1",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ id: "1" }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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

beforeEach(() => {
  server.use(
    http.get("/api/animals/1", () => HttpResponse.json(PROFILE)),
    http.get("/api/health/restrictions/1", () =>
      HttpResponse.json({
        animal_id: 1,
        restriction_version: 0,
        active: false,
        actions: [],
        total: 0,
        limit: 25,
        offset: 0,
      }),
    ),
  );
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

async function openWeightDialogInTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  renderWithProviders(
    <LanguageProvider>
      <AnimalProfilePage />
    </LanguageProvider>,
  );
  // Page chrome renders in Telugu now: the Details card and history titles.
  await screen.findByText("వివరాలు");
  await user.click(await screen.findByRole("button", { name: "బరువు నమోదు చేయండి" }));
  const dialog = await screen.findByRole("dialog", { name: "బరువు నమోదు చేయండి" });
  return { user, dialog };
}

describe("AnimalProfilePage record-weight dialog — Telugu", () => {
  it("renders the dialog title, field labels and save action in Telugu", async () => {
    const { dialog } = await openWeightDialogInTelugu();

    expect(within(dialog).getByLabelText("తేదీ (డిఫాల్ట్ ఈరోజు)")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("బరువు (కిలోలు) *")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("గమనికలు")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "సేవ్ చేయండి" }),
    ).toBeInTheDocument();
    // The empty-history cards behind the dialog localized too.
    expect(screen.getByText("బరువు చరిత్ర (0)")).toBeInTheDocument();
    expect(screen.getByText("ఇంకా బరువు నమోదులు లేవు.")).toBeInTheDocument();
    // No stray English label survives on the form's key fields.
    expect(within(dialog).queryByText("Weight (kg) *")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Notes")).not.toBeInTheDocument();
  });

  it("shows zod validation messages in Telugu", async () => {
    const { user, dialog } = await openWeightDialogInTelugu();

    await user.click(within(dialog).getByRole("button", { name: "సేవ్ చేయండి" }));

    const error = await within(dialog).findByText("బరువు 0 కంటే ఎక్కువ ఉండాలి", {
      selector: "p[role='alert']",
    });
    expect(error).toHaveAttribute("id", "weight-kg-error");
  });

  it("keeps the Telugu catalog at parity for the page keys", () => {
    // Spot-check key translations resolve to Telugu, never the raw key or
    // the English fallback.
    expect(translate("te", "animalDetail.actions.moveBucket")).toBe("పెంట మార్చండి");
    expect(translate("te", "animalDetail.weights.title", { count: 2 })).toBe("బరువు చరిత్ర (2)");
    expect(
      translate("te", "animalDetail.validation.weightTooLargeSpecies", { max: 150 }),
    ).toBe("ఈ ఫారమ్ జాతికి బరువు గరిష్ఠంగా 150 కిలోలు ఉండాలి");
    expect(translate("te", "animalDetail.detail.ageMonths", { count: 14 })).toBe("14 నెలలు");
  });

  it("renders every weight through the localized kg unit token (2026-10-01 audit, 05-3)", async () => {
    // The birth/latest/detail and weights-table suffixes were hardcoded
    // "kg"; they must resolve through the common.kg catalog token so the
    // unit localizes with the rest of the sentence.
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    server.use(
      http.get("/api/animals/1", () =>
        HttpResponse.json({
          ...PROFILE,
          weights: [{ id: 11, date: "2026-07-01", weight_kg: 28.44, bcs: 3, notes: null }],
          weights_total: 1,
        }),
      ),
    );
    renderWithProviders(
      <LanguageProvider>
        <AnimalProfilePage />
      </LanguageProvider>,
    );

    await screen.findByText("వివరాలు");
    // Birth weight (2.4) in the Details card and the history row's 28.4
    // both carry the Telugu unit.
    expect((await screen.findAllByText("2.4 కిలో")).length).toBeGreaterThan(0);
    expect(screen.getByText("28.4 కిలో")).toBeInTheDocument();
    expect(screen.queryByText(/2\.4 kg/)).not.toBeInTheDocument();
    expect(screen.queryByText(/28\.4 kg/)).not.toBeInTheDocument();
  });
});
