/**
 * Animals list page i18n: the create-animal dialog (the page's most
 * worker-facing form) renders fully in Telugu when the language is te —
 * dialog title, field labels, the quarantine note and the save action all
 * resolve through the animals.create.* catalog keys — and the zod validation
 * messages follow the same language because the dialog rebuilds its schema
 * from the active `t`. Mirrors animals/[id]/page.i18n.test.tsx.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import AnimalsPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/animals",
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

const ANIMAL = {
  id: 1,
  tag_number: "G-001",
  name: "Lakshmi",
  breed: "Osmanabadi",
  sex: "F",
  date_of_birth: "2025-05-10",
  estimated_dob: null,
  birth_type: "SINGLE",
  source: "BORN",
  dam_id: null,
  sire_id: null,
  birth_weight: 2.4,
  current_bucket: "LACTATING",
  status: "ACTIVE",
  status_date: null,
  sale_price: null,
  purchase_date: null,
  purchase_price: null,
  seller_name: null,
  cull_candidate: false,
  notes: null,
  created_at: "2026-01-01T05:30:00Z",
  age_months: 14,
  latest_weight_kg: 32.5,
};

beforeEach(() => {
  server.use(
    http.get("/api/animals", () => HttpResponse.json({ animals: [ANIMAL], total: 1 })),
  );
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

async function openCreateDialogInTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  renderWithProviders(
    <LanguageProvider>
      <AnimalsPage />
    </LanguageProvider>,
  );
  // Page chrome and the table card render in Telugu now.
  await screen.findByText("1 మేక");
  await user.click(await screen.findByRole("button", { name: "మేక చేర్చు" }));
  const dialog = await screen.findByRole("dialog", { name: "మేక చేర్చు" });
  return { user, dialog };
}

describe("AnimalsPage create dialog — Telugu", () => {
  it("renders the field labels, quarantine note and save action in Telugu", async () => {
    const { dialog } = await openCreateDialogInTelugu();

    expect(within(dialog).getByLabelText("ట్యాగ్ నంబర్")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("పేరు")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("లింగం *")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("మూలం *")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("జాతి")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("పుట్టిన తేదీ")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("అంచనా పుట్టిన తేదీ")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("ప్రవేశ బరువు (కి.గ్రా)")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("గమనికలు")).toBeInTheDocument();
    // The source defaults to a purchase, so the quarantine explainer shows.
    expect(
      within(dialog).getByText(
        "కొనుగోలు చేసిన మేకలు క్వారంటైన్‌లోకి వెళ్లాలి. ఈ మేకను ఉత్పాదక పెంటలోకి తరలించే ముందు క్వారంటైన్ ప్రోటోకాల్ పూర్తి చేయండి.",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("* గుర్తించిన ఫీల్డ్‌లు తప్పనిసరి.")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "మేకను సేవ్ చేయండి" }),
    ).toBeInTheDocument();
    // No stray English label survives on the form's key fields.
    expect(within(dialog).queryByText("Save animal")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Tag number")).not.toBeInTheDocument();
  });

  it("shows zod validation messages in Telugu", async () => {
    const { user, dialog } = await openCreateDialogInTelugu();

    await user.type(within(dialog).getByLabelText("ప్రవేశ బరువు (కి.గ్రా)"), "9999");
    await user.click(within(dialog).getByRole("button", { name: "మేకను సేవ్ చేయండి" }));

    const error = await within(dialog).findByText("ఈ ఫారం జాతికి గరిష్ఠంగా 150 కి.గ్రా", {
      selector: "p[role='alert']",
    });
    expect(error).toHaveAttribute("id", "create-weight-error");
  });

  it("keeps the Telugu catalog at parity for the page and picker keys", () => {
    // Spot-check key translations resolve to Telugu, never the raw key or
    // the English fallback.
    expect(translate("te", "animals.create.title")).toBe("మేక చేర్చు");
    expect(translate("te", "animals.list.description_many", { count: 5 })).toBe("5 మేకలు");
    expect(
      translate("te", "animals.validation.bucketSex", { sex: "ఆడ", bucket: "FEMALE_KIDS" }),
    ).toBe("FEMALE_KIDSలోకి ఆడ మేకలు మాత్రమే వెళ్లాలి");
    expect(translate("te", "animals.validation.breedingMinAge", { animal: "మగ మేక", min: 12 })).toBe(
      "BREEDINGలోకి వెళ్లడానికి మగ మేక కనీసం 12 నెలల వయస్సు ఉండాలి",
    );
    expect(translate("te", "picker.animal.placeholder")).toBe("మేకను ఎంచుకోండి");
    expect(translate("te", "picker.batch.optionLabel", { id: 7, count: 3 })).toBe(
      "బ్యాచ్ #7 — క్వారంటైన్‌లో 3 యాక్టివ్",
    );
  });
});
