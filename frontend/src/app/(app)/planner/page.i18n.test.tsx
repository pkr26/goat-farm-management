/**
 * Planner page i18n: with the language set to te the page chrome (targets
 * card, plan-name input, basis card) and the inline target-validation
 * messages all resolve through the planner.* catalog keys — no hardcoded
 * English survives on the surface a farmer plans in.
 */

import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate } from "@/lib/i18n";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import PlannerPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/planner",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const GOAT_DEFAULTS = {
  meta: { horizon_months: 60, start_year_month: "2026-01" },
  herd: { does: 50, bucks: 2 },
  reproduction: { gestation_months: 5, conception_rate: 0.85, litter_size: 1.6 },
  growth: { sale_age_months: 9 },
  mortality: { kid_pre_weaning: 0.15, kid_post_weaning: 0.05 },
  sales: { meat_price_per_kg: 400 },
};

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

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

function registerPlannerHandlers() {
  server.use(
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi", "sirohi"], systems: ["stall_fed", "semi_intensive"] }),
    ),
    http.get("/api/simulation/defaults", () => HttpResponse.json(GOAT_DEFAULTS)),
    http.get("/api/planner/plans", () =>
      HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
    ),
  );
}

async function renderInTelugu() {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
  const user = userEvent.setup();
  renderWithProviders(
    <LanguageProvider>
      <PlannerPage />
    </LanguageProvider>,
  );
  await screen.findByText("అమ్మకపు లక్ష్యాలు");
  return { user };
}

describe("PlannerPage — Telugu", () => {
  it("renders the targets editor and plan-name input in Telugu", async () => {
    registerPlannerHandlers();
    await renderInTelugu();

    expect(screen.getByLabelText("ప్లాన్ పేరు")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "లక్ష్యం చేర్చండి" })).toBeInTheDocument();
    expect(screen.getByText("ప్లాన్ ఆధారం")).toBeInTheDocument();
    expect(screen.getByLabelText("ప్లాన్ ప్రారంభం")).toBeInTheDocument();
    // No stray English on the headline chrome.
    expect(screen.queryByText("Sale targets")).not.toBeInTheDocument();
    expect(screen.queryByText("Plan basis")).not.toBeInTheDocument();
  });

  it("shows the inline target-validation message in Telugu", async () => {
    registerPlannerHandlers();
    const { user } = await renderInTelugu();

    await user.click(screen.getByRole("button", { name: "లక్ష్యం చేర్చండి" }));
    // The desktop targets table carries the month input; "2026-13" is not a
    // real month (the mutation tests drive the same control in English).
    const desktopTable = document.querySelector(
      `[class~="md:block"] table[class*="min-w-[720px]"]`,
    ) as HTMLElement;
    const monthInput = within(desktopTable).getByLabelText("అమ్మకపు నెల");
    fireEvent.change(monthInput, { target: { value: "2026-13" } });

    expect(
      await screen.findByText("లక్ష్యం 1: సరైన నెల ఎంచుకోండి (YYYY-MM)."),
    ).toBeInTheDocument();
  });

  it("keeps key planner translations resolving to Telugu, never the raw key", () => {
    expect(translate("te", "planner.delete.title")).toBe("ప్లాన్ తొలగించాలా?");
    expect(translate("te", "planner.toast.saved", { name: "పండుగ" })).toBe(
      "ప్లాన్ “పండుగ” సేవ్ చేయబడింది.",
    );
    expect(translate("te", "planner.validation.monthAfterStart", {
      label: "లక్ష్యం 1",
      start: "జన 2027",
    })).toBe("లక్ష్యం 1: నెల ప్లాన్ ప్రారంభం (జన 2027) తర్వాత ఉండాలి.");
    // English instances stay byte-identical to the pre-i18n copy.
    expect(translate("en", "planner.targets.emptyTitle")).toBe("No sale targets yet");
  });
});
