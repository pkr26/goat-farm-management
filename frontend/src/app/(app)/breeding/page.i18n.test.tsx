/**
 * Breeding page i18n: with the language set to te the list chrome and the
 * add-breeding dialog render in Telugu, and the zod validation messages
 * resolve through the per-language schema (health page precedent). Also pins
 * that the English instances stay byte-identical to the pre-i18n copy.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BreedingRecordOut } from "@/api/generated/models";
import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import BreedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/breeding",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
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

const RECORD: BreedingRecordOut = {
  id: 1,
  doe_id: 10,
  buck_id: 20,
  semen_sire_name: null,
  breeding_date: "2026-07-01",
  method: "NATURAL",
  heat_cycle_number: 1,
  ultrasound_date: "2026-08-02",
  ultrasound_result_date: null,
  ultrasound_done: false,
  pregnant: null,
  kid_count_detected: null,
  expected_kidding_date: null,
  outcome: "PENDING",
  loss_date: null,
  loss_cause: null,
  loss_notes: null,
  loss_recorded_by_id: null,
  loss_recorded_at: null,
  created_at: "2026-01-01T00:00:00Z",
  has_kidding: false,
  doe_tag: "G-010",
  buck_tag: "G-020",
};

beforeEach(() => {
  server.use(
    http.get("/api/breeding", () =>
      HttpResponse.json({
        records: [RECORD],
        candidate_availability: { eligible_doe_count: 1, eligible_buck_count: 1 },
        total: 1,
        limit: 50,
        offset: 0,
      }),
    ),
  );
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

async function openAddDialogInTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  renderWithProviders(
    <LanguageProvider>
      <BreedingPage />
    </LanguageProvider>,
  );
  await user.click(await screen.findByRole("button", { name: "సంతానోత్పత్తి జోడించండి" }));
  const dialog = await screen.findByRole("dialog", { name: "సంతానోత్పత్తి జోడించండి" });
  return { user, dialog };
}

describe("BreedingPage add-breeding dialog — Telugu", () => {
  it("renders the page chrome and dialog labels in Telugu", async () => {
    const { dialog } = await openAddDialogInTelugu();

    // Page chrome localized behind the dialog (text queries: the modal hides
    // the background from role queries).
    expect(screen.getByText("సంతానోత్పత్తి", { selector: "h1" })).toBeInTheDocument();
    expect(screen.getByText("సంతానోత్పత్తి నమోదులు")).toBeInTheDocument();
    // The form's key fields carry Telugu labels and the Telugu submit action.
    expect(within(dialog).getByRole("button", { name: "ఆడ మేక *" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "మగ మేక *" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("సంతానోత్పత్తి తేదీ *")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "సంతానోత్పత్తి సేవ్ చేయండి" }),
    ).toBeInTheDocument();
    // No stray English label survives on the form's key fields.
    expect(within(dialog).queryByText("Breeding date *")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Save breeding")).not.toBeInTheDocument();
  });

  it("shows zod validation messages in Telugu", async () => {
    const { user, dialog } = await openAddDialogInTelugu();

    await user.click(
      within(dialog).getByRole("button", { name: "సంతానోత్పత్తి సేవ్ చేయండి" }),
    );

    expect(
      await within(dialog).findByText("ఒక ఆడ మేకను ఎంచుకోండి", {
        selector: "p[role='alert']",
      }),
    ).toHaveAttribute("id", "breeding-doe-error");
  });

  it("keeps key breeding translations resolving to Telugu, never the raw key", () => {
    expect(translate("te", "breeding.add")).toBe("సంతానోత్పత్తి జోడించండి");
    expect(translate("te", "breeding.toast.saved")).toBe("సంతానోత్పత్తి సేవ్ అయింది.");
    expect(
      translate("te", "breeding.validation.selectFemale", { noun: translate("te", "breeding.noun.female") }),
    ).toBe("ఒక ఆడ మేకను ఎంచుకోండి");
    // English instances stay byte-identical to the pre-i18n copy.
    expect(
      translate("en", "breeding.validation.selectFemale", { noun: "doe" }),
    ).toBe("Select a doe");
    expect(translate("en", "breeding.empty.title")).toBe("No breeding records yet.");
    expect(
      translate("en", "breeding.form.noEligibleMales", { nouns: "bucks", nounsCap: "Bucks" }),
    ).toBe(
      "No eligible bucks are available. Bucks on hold, in quarantine, or otherwise restricted cannot be selected.",
    );
  });
});
