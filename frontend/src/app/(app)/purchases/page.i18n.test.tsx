/**
 * Purchases page i18n: with the language set to te the list chrome and the
 * new-batch dialog render in Telugu, and the zod validation messages resolve
 * through the per-language schema (health page precedent). Also pins that the
 * catalog's money-floor message stays byte-identical to the shared
 * MIN_PERSISTED_MONEY_MESSAGE constant other forms use.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { MIN_PERSISTED_MONEY_MESSAGE } from "@/lib/persisted-numbers";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import PurchasesPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/purchases",
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

beforeEach(() => {
  server.use(
    http.get("/api/purchases", () =>
      HttpResponse.json({ batches: [], total: 0, limit: 50, offset: 0 }),
    ),
  );
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

async function openNewBatchInTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  renderWithProviders(
    <LanguageProvider>
      <PurchasesPage />
    </LanguageProvider>,
  );
  await user.click(await screen.findByRole("button", { name: "కొత్త బ్యాచ్" }));
  const dialog = await screen.findByRole("dialog", { name: "కొత్త కొనుగోలు బ్యాచ్" });
  return { user, dialog };
}

describe("PurchasesPage new-batch dialog — Telugu", () => {
  it("renders the dialog title, labels and review action in Telugu", async () => {
    const { dialog } = await openNewBatchInTelugu();

    expect(within(dialog).getByText("బ్యాచ్ వివరాలు")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("తేదీ *")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("సంఖ్య *")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("సరఫరాదారు")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "బ్యాచ్ సమీక్షించండి" }),
    ).toBeInTheDocument();
    // No stray English label survives on the form's key fields.
    expect(within(dialog).queryByText("Batch details")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Review batch")).not.toBeInTheDocument();
  });

  it("shows zod validation messages in Telugu", async () => {
    const { user, dialog } = await openNewBatchInTelugu();

    const count = within(dialog).getByLabelText("సంఖ్య *");
    await user.clear(count);
    await user.type(count, "0");
    await user.click(within(dialog).getByRole("button", { name: "బ్యాచ్ సమీక్షించండి" }));

    expect(
      await within(dialog).findByText("కనీసం 1 మేక", { selector: "p[role='alert']" }),
    ).toBeInTheDocument();
  });

  it("shows the review step's consequence copy in Telugu", async () => {
    const { user, dialog } = await openNewBatchInTelugu();

    await user.click(within(dialog).getByRole("button", { name: "బ్యాచ్ సమీక్షించండి" }));

    await within(dialog).findByText("కొనుగోలు పరిణామాలను సమీక్షించండి");
    expect(within(dialog).getByText("1 ఆడ మేక")).toBeInTheDocument();
    expect(within(dialog).getByText("45-రోజుల క్వారంటైన్ షెడ్యూల్")).toBeInTheDocument();
    expect(within(dialog).getByText("ఖర్చు మొత్తం లేదు")).toBeInTheDocument();
  });

  it("keeps the catalog money floor identical to the shared persisted-money constant", () => {
    expect(translate("en", "purchases.validation.moneyMin")).toBe(MIN_PERSISTED_MONEY_MESSAGE);
  });

  it("keeps key purchases translations resolving to Telugu, never the raw key", () => {
    expect(translate("te", "purchases.dialog.title")).toBe("కొత్త కొనుగోలు బ్యాచ్");
    expect(translate("te", "purchases.toast.created")).toBe("కొనుగోలు బ్యాచ్ సృష్టించబడింది.");
    expect(translate("te", "purchases.validation.dateFuture")).toBe(
      "తేదీ భవిష్యత్తులో ఉండకూడదు",
    );
    // English instances stay byte-identical to the pre-i18n copy.
    expect(translate("en", "purchases.empty.title")).toBe("No purchase batches yet.");
    expect(translate("en", "purchases.list.description_many", { count: 2 })).toBe(
      "2 batches recorded",
    );
  });
});
