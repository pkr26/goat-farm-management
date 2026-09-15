/**
 * Health page i18n: the record-event dialog (the most worker-facing form in
 * the app) renders fully in Telugu when the language is te — dialog title,
 * scope radios, field labels, the Advanced section and the zod validation
 * messages all resolve through the health.* catalog keys. Also pins that the
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

import HealthPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/health",
  useSearchParams: () => new URLSearchParams(window.location.search),
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
    http.get("/api/health/events", () =>
      HttpResponse.json({ events: [], total: 0, limit: 50, offset: 0 }),
    ),
    http.get("/api/health/animals", () =>
      HttpResponse.json({ animals: [], total: 0, limit: 50, offset: 0 }),
    ),
    http.get("/api/tasks", () =>
      HttpResponse.json({
        today: [],
        overdue: [],
        upcoming: [],
        awaiting: [],
        completed: [],
        completed_total: 0,
        completed_limit: 50,
        completed_offset: 0,
      }),
    ),
    http.get("/api/health/schedule-templates", () =>
      HttpResponse.json({ templates: [] }),
    ),
  );
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
  window.history.replaceState({}, "", "/health");
});

async function openDialogInTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  renderWithProviders(
    <LanguageProvider>
      <HealthPage />
    </LanguageProvider>,
  );
  // Page chrome and the dialog both render in Telugu now.
  await screen.findByText("నమోదుల చిట్టా");
  await user.click(await screen.findByRole("button", { name: "నమోదు చేర్చు" }));
  const dialog = await screen.findByRole("dialog", { name: "ఆరోగ్య నమోదు చేర్చు" });
  return { user, dialog };
}

describe("HealthPage record dialog — Telugu", () => {
  it("renders the scope radios, field labels and Advanced section in Telugu", async () => {
    const { dialog } = await openDialogInTelugu();

    expect(within(dialog).getByRole("radio", { name: "ఒకే మేక" })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "మొత్తం పెంట" })).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: "కొనుగోలు బ్యాచ్" })).toBeInTheDocument();
    // Required single-animal picker and the ordinary text fields.
    expect(within(dialog).getByText("మేక *")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("మందు పేరు")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("మోతాదు")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("వెట్ (పశు వైద్యుడు)")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("గమనికలు")).toBeInTheDocument();
    // Advanced section heading + helper line.
    expect(within(dialog).getByText("అదనపు ట్రేసబిలిటీ & నిబంధనలు")).toBeInTheDocument();
    // The save action itself.
    expect(
      within(dialog).getByRole("button", { name: "నమోదు సేవ్ చేయండి" }),
    ).toBeInTheDocument();
    // No stray English label survives on the form's key fields.
    expect(within(dialog).queryByText("Animal *")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Dose")).not.toBeInTheDocument();
  });

  it("shows zod validation messages in Telugu", async () => {
    const { user, dialog } = await openDialogInTelugu();

    await user.click(within(dialog).getByRole("button", { name: "నమోదు సేవ్ చేయండి" }));

    // The picker placeholder shares the wording, so pin the error node by id.
    const error = await within(dialog).findByText("మేకను ఎంచుకోండి", {
      selector: "p[role='alert']",
    });
    expect(error).toHaveAttribute("id", "event-animal-error");
  });

  it("keeps the Telugu catalog at parity for the dialog keys", () => {
    // Spot-check key translations resolve to Telugu, never the raw key or
    // the English fallback.
    expect(translate("te", "health.form.title")).toBe("ఆరోగ్య నమోదు చేర్చు");
    expect(translate("te", "health.form.withdrawalLabel")).toBe("విత్‌డ్రాయల్ ముగింపు తేదీ");
    expect(translate("te", "health.form.isolationStartedLabel")).toBe("ఐసోలేషన్ ప్రారంభ తేదీ");
    expect(translate("te", "health.validation.withdrawalTooLong", { days: 365 })).toBe(
      "విత్‌డ్రాయల్ తేదీ నమోదు తర్వాత 365 రోజులు మించకూడదు",
    );
  });

  it("keeps the catalog money floor identical to the shared persisted-money constant", () => {
    expect(translate("en", "health.validation.moneyMin")).toBe(MIN_PERSISTED_MONEY_MESSAGE);
  });
});
