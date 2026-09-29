/**
 * Kidding page i18n: with the language set to te the queue chrome and the
 * record-kidding dialog render in Telugu, and the zod validation messages
 * resolve through the per-language schema (health page precedent). Also pins
 * that the English instances stay byte-identical to the pre-i18n copy.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BreedingRecordOut, KiddingListOut } from "@/api/generated/models";
import { addDays, farmToday } from "@/lib/format";
import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import KiddingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/kidding",
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

/** MIN_GESTATION_DAYS is 100, so a doe bred 120 days ago may deliver today. */
const BRED_ON = addDays(farmToday(), -120);

const UPCOMING: BreedingRecordOut = {
  id: 12,
  doe_id: 10,
  buck_id: 20,
  semen_sire_name: null,
  breeding_date: BRED_ON,
  method: "NATURAL",
  heat_cycle_number: 1,
  ultrasound_date: addDays(BRED_ON, 32),
  ultrasound_result_date: null,
  ultrasound_done: true,
  pregnant: true,
  kid_count_detected: 2,
  expected_kidding_date: addDays(farmToday(), 10),
  outcome: "CONFIRMED_PREGNANT",
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

const PAYLOAD: KiddingListOut = {
  records: [],
  upcoming: [UPCOMING],
  upcoming_total: 1,
  upcoming_limit: 25,
  upcoming_offset: 0,
  overdue: [],
  overdue_total: 0,
  overdue_limit: 25,
  overdue_offset: 0,
  total: 0,
  limit: 50,
  offset: 0,
};

beforeEach(() => {
  server.use(http.get("/api/kidding", () => HttpResponse.json(PAYLOAD)));
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

async function openRecordDialogInTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  renderWithProviders(
    <LanguageProvider>
      <KiddingPage />
    </LanguageProvider>,
  );
  const sectionTitle = await screen.findByText("రాబోయేవి (తదుపరి 30 రోజులు)");
  const section = sectionTitle.closest("[data-slot='card']") as HTMLElement;
  // The Record action renders twice (below-md card + desktop table row).
  await user.click(
    within(section).getAllByRole("button", { name: "ప్రసవం నమోదు చేయండి" })[0]!,
  );
  const dialog = await screen.findByRole("dialog", { name: "ప్రసవం నమోదు చేయండి" });
  return { user, dialog };
}

describe("KiddingPage record-kidding dialog — Telugu", () => {
  it("renders the queue chrome and dialog labels in Telugu", async () => {
    const { dialog } = await openRecordDialogInTelugu();

    // Page chrome localized behind the dialog (text query: the modal hides
    // the background from role queries).
    expect(screen.getByText("ప్రసవం", { selector: "h1" })).toBeInTheDocument();
    // The form's key fields carry Telugu labels and the Telugu submit action.
    expect(within(dialog).getByLabelText("ప్రసవం తేదీ *")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("పిల్ల 1 ట్యాగ్ (ఖాళీ అయితే ఆటో)")).toBeInTheDocument();
    expect(within(dialog).getByText("మాస్టైటిస్ అనుమానం")).toBeInTheDocument();
    expect(within(dialog).getByText("2 పిల్లలు జాబితాలో ఉన్నాయి")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "ప్రసవం సేవ్ చేయండి" }),
    ).toBeInTheDocument();
    // No stray English label survives on the form's key fields.
    expect(within(dialog).queryByText("Mastitis suspected")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Save kidding")).not.toBeInTheDocument();
  });

  it("shows zod validation messages in Telugu", async () => {
    const { user, dialog } = await openRecordDialogInTelugu();

    await user.clear(within(dialog).getByLabelText("ప్రసవం తేదీ *"));
    await user.click(within(dialog).getByRole("button", { name: "ప్రసవం సేవ్ చేయండి" }));

    expect(
      await within(dialog).findByText("చెల్లుబాటు అయ్యే తేదీ ఎంచుకోండి", {
        selector: "p[role='alert']",
      }),
    ).toHaveAttribute("id", "kidding-date-error");
  });

  it("keeps key kidding translations resolving to Telugu, never the raw key", () => {
    expect(translate("te", "kidding.record", { parturition: "ప్రసవం" })).toBe(
      "ప్రసవం నమోదు చేయండి",
    );
    expect(translate("te", "kidding.toast.recorded")).toBe("ప్రసవం నమోదు అయింది.");
    expect(translate("te", "kidding.validation.youngMin", { young: "పిల్ల" })).toBe(
      "కనీసం ఒక పిల్ల",
    );
    // English instances stay byte-identical to the pre-i18n copy.
    expect(translate("en", "kidding.history.title", { parturitions: "kiddings" })).toBe(
      "Recent kiddings",
    );
    expect(
      translate("en", "kidding.validation.litterMax", {
        parturition: "kidding",
        max: 4,
        youngPlural: "kids",
      }),
    ).toBe("A kidding delivers at most 4 kids on this farm");
  });
});
