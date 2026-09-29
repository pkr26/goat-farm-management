/**
 * Insurance page i18n: the register dialog renders in Telugu when the
 * language is te, and the zod validation messages (the page's last hardcoded
 * English) resolve through the insurance.validation.* catalog keys. Also pins
 * the catalog money floor byte-identical to MIN_PERSISTED_MONEY_MESSAGE.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { InsurancePolicyOut } from "@/api/generated/models";
import { addDays, farmToday } from "@/lib/format";
import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { MIN_PERSISTED_MONEY_MESSAGE } from "@/lib/persisted-numbers";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import InsurancePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/finance/insurance",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI Select relies on.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = vi.fn();
});

const POLICY: InsurancePolicyOut = {
  id: 1,
  policy_number: "POL-2026-001",
  insurer: "Oriental Insurance",
  animal_id: 11,
  animal_tag: "G-011",
  sum_insured: 12000,
  premium: 480,
  start_date: "2026-01-10",
  renewal_date: addDays(farmToday(), 20),
  status: "active",
  notes: null,
  created_at: "2026-01-10T05:30:00Z",
  claim_date: null,
  claimed_at: null,
  claimed_by_id: null,
};

beforeEach(() => {
  server.use(
    http.get("/api/finance/insurance", () =>
      HttpResponse.json({ policies: [POLICY], total: 1, limit: 50, offset: 0 }),
    ),
    http.get("/api/animals", () => HttpResponse.json({ animals: [], total: 0 })),
  );
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

describe("InsurancePage register dialog — Telugu", () => {
  it("renders the dialog in Telugu and shows zod validation in Telugu", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    const user = userEvent.setup();
    renderWithProviders(
      <LanguageProvider>
        <InsurancePage />
      </LanguageProvider>,
    );
    // The register loads with Telugu chrome (policy number renders in both
    // the mobile card list and the desktop table).
    await screen.findAllByText("POL-2026-001");
    await user.click(screen.getByRole("button", { name: "పాలసీ నమోదు చేయి" }));
    const dialog = await screen.findByRole("dialog", { name: "పాలసీ నమోదు చేయి" });

    expect(within(dialog).getByLabelText("పాలసీ నంబర్ *")).toBeInTheDocument();
    expect(within(dialog).queryByText("Policy number *")).not.toBeInTheDocument();

    // Submit the blank form: every required-field message arrives in Telugu.
    await user.click(within(dialog).getByRole("button", { name: "పాలసీ నమోదు చేయి" }));
    const error = await within(dialog).findByText("పాలసీ నంబర్ తప్పనిసరి", {
      selector: "p[role='alert']",
    });
    expect(error).toHaveAttribute("id", "policy-number-error");
    expect(within(dialog).getByText("భీమా సంస్థ తప్పనిసరి")).toBeInTheDocument();
    expect(within(dialog).getByText("ప్రీమియం తప్పనిసరి")).toBeInTheDocument();
    expect(within(dialog).getByText("నవీకరణ తేదీ తప్పనిసరి")).toBeInTheDocument();
  });

  it("keeps the Telugu catalog at parity for the validation keys", () => {
    expect(translate("te", "insurance.validation.policyNumberRequired")).toBe(
      "పాలసీ నంబర్ తప్పనిసరి",
    );
    expect(
      translate("te", "insurance.validation.renewalAfterCurrent", { date: "5 జన 2027" }),
    ).toBe("ప్రస్తుత నవీకరణ తేదీ 5 జన 2027 తర్వాత ఉండాలి");
  });

  it("keeps the catalog money floor identical to the shared persisted-money constant", () => {
    expect(translate("en", "insurance.validation.moneyMin")).toBe(MIN_PERSISTED_MONEY_MESSAGE);
  });
});
