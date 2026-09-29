/**
 * Feeding plan worker-facing buttons/toasts through the i18n catalog:
 * English by default, Telugu when the language choice is persisted.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider, LANGUAGE_STORAGE_KEY, translate } from "@/lib/i18n";
import { MIN_PERSISTED_KG_MESSAGE } from "@/lib/persisted-numbers";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/msw-server";

import FeedingPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/feeding",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function planHandler() {
  return http.get("/api/feeding/plan", () =>
    HttpResponse.json({
      lines: [],
      records: [],
      records_total: 0,
      records_limit: 200,
      dispensed_totals: [],
    }),
  );
}

describe("FeedingPage language wiring", () => {
  beforeEach(() => {
    server.use(
      planHandler(),
      http.get("/api/feeding/recipes", () =>
        HttpResponse.json({ recipes: [], allocation: [] }),
      ),
      http.post("/api/feeding/dispense", () => new HttpResponse(null, { status: 204 })),
    );
  });
  afterEach(() => {
    localStorage.removeItem(LANGUAGE_STORAGE_KEY);
    document.documentElement.lang = "en";
  });

  it("renders the dispensing controls in English by default", async () => {
    renderWithProviders(
      <LanguageProvider>
        <FeedingPage />
      </LanguageProvider>,
    );

    expect(
      await screen.findByRole("button", { name: "Record dispensing" }),
    ).toBeInTheDocument();
  });

  it("renders the dispensing controls and toast in Telugu", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    const user = userEvent.setup();
    renderWithProviders(
      <LanguageProvider>
        <FeedingPage />
      </LanguageProvider>,
    );

    const trigger = await screen.findByRole("button", { name: "మేత నమోదు చేయండి" });
    expect(document.documentElement.lang).toBe("te");

    await user.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "మేత నమోదు చేయండి" });
    // An empty plan prefills the only available recipe (the virtual dry
    // roughage code); the operator just types the quantity.
    await user.type(within(dialog).getByLabelText("పరిమాణం (కి.గ్రా) *"), "5");
    await user.click(within(dialog).getByRole("button", { name: "నమోదు చేయండి" }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("మేత నమోదు అయింది."));
  });

  it("renders the dispense dialog fields and zod validation in Telugu", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    const user = userEvent.setup();
    renderWithProviders(
      <LanguageProvider>
        <FeedingPage />
      </LanguageProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "మేత నమోదు చేయండి" }));
    const dialog = await screen.findByRole("dialog", { name: "మేత నమోదు చేయండి" });

    // Field labels resolve through the feeding.dispense.* catalog keys.
    expect(within(dialog).getByLabelText("పరిమాణం (కి.గ్రా) *")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("తేదీ")).toBeInTheDocument();
    expect(within(dialog).getByText("రెసిపీ *")).toBeInTheDocument();
    expect(within(dialog).queryByText("Recipe *")).not.toBeInTheDocument();

    // A zero quantity fails the per-language schema with a Telugu message.
    await user.type(within(dialog).getByLabelText("పరిమాణం (కి.గ్రా) *"), "0");
    await user.click(within(dialog).getByRole("button", { name: "నమోదు చేయండి" }));
    expect(
      await within(dialog).findByText("పరిమాణం 0 కంటే ఎక్కువ ఉండాలి", {
        selector: "p[role='alert']",
      }),
    ).toBeInTheDocument();
  });

  it("keeps the catalog kg floor identical to the shared persisted-kg constant", () => {
    // Same pin as health/purchases for the money floor: the shared
    // lib/persisted-numbers constants stay English until every consumer is
    // converted, so the catalog value must match byte-for-byte.
    expect(translate("en", "feeding.validation.kgMin")).toBe(MIN_PERSISTED_KG_MESSAGE);
  });
});
