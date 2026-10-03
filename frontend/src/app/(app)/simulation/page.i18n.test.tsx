/**
 * Simulation is a dense, data-driven editor.  This pins the Telugu route
 * through its page chrome, accessible controls, generated field labels and
 * a representative validation message so a newly added English literal
 * cannot quietly leak onto the operator-facing surface.
 */

import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider, translate, useLanguage } from "@/lib/i18n";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import SimulationPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/simulation",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const MANAGE_PERMS = ["simulation.view", "simulation.manage"];

beforeEach(() => {
  server.use(
    permissionsHandler(MANAGE_PERMS),
    http.get("/api/simulation/defaults/breeds", () =>
      HttpResponse.json({ breeds: ["osmanabadi"], systems: ["stall_fed"] }),
    ),
    http.get("/api/simulation/defaults", () =>
      HttpResponse.json({
        meta: { horizon_months: 60, start_year_month: "2026-01" },
        herd: { does: 50, bucks: 2 },
        costs: { misc_overhead_per_month: 1000 },
        finance: { interest_rate_annual: 0.12 },
      }),
    ),
    http.get("/api/simulation/scenarios", () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
  );
});

afterEach(() => {
  localStorage.clear();
  document.documentElement.lang = "en";
});

describe("SimulationPage Telugu localization", () => {
  it.each(["en", "te"] as const)("uses localized %s headings and accessible help for every supported assumption section", async (language) => {
    server.use(http.get("/api/simulation/defaults", () => HttpResponse.json({
      meta: { horizon_months: 60, start_year_month: "2026-01" },
      herd: { does: 50, bucks: 2 },
      reproduction: {}, mortality: {}, culling: {}, growth: {},
      sales: {}, feed: {}, costs: {}, finance: {}, risk: {}, optimization: {},
    })));
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
    const user = userEvent.setup();
    renderWithProviders(<LanguageProvider><SimulationPage /></LanguageProvider>);
    await screen.findByLabelText(language === "en" ? "Horizon Months" : "కాల పరిమితి నెలలు");

    const expectedLabels = language === "en"
      ? ["Meta", "Herd", "Reproduction", "Mortality", "Culling", "Growth", "Sales", "Feed", "Costs", "Finance", "Risk", "Optimization"]
      : ["ప్రాథమిక వివరాలు", "మంద", "సంతానోత్పత్తి", "మరణాల రేటు", "మంద నుంచి తొలగింపు", "పెరుగుదల", "అమ్మకాలు", "మేత", "ఖర్చులు", "ఆర్థిక వివరాలు", "ప్రమాదం", "ఉత్తమ ఎంపికల విశ్లేషణ"];
    expect(Array.from(document.querySelectorAll("summary"), summary => summary.textContent)).toEqual(expectedLabels);
    for (const label of expectedLabels) {
      const summary = screen.getByText(label, { selector: "summary" });
      expect(summary.querySelector("button, [tabindex], a[href]")).toBeNull();
      const help = screen.getByRole("button", {
        name: `${translate(language, "simulation.accessibility.explainField", { label })}?`,
      });
      expect(help.closest("summary")).toBeNull();
      await user.click(help);
      const dialog = screen.getByRole("dialog");
      expect(within(dialog).getByRole("heading")).toHaveTextContent(
        translate(language, "simulation.assumptions.sectionTitle", { section: label }),
      );
      await user.keyboard("{Escape}");
    }
  });

  it("localizes the access-denied state", async () => {
    server.use(permissionsHandler([]));
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    renderWithProviders(
      <LanguageProvider>
        <SimulationPage />
      </LanguageProvider>,
    );

    expect(await screen.findByText("మీకు ఈ పేజీకి యాక్సెస్ లేదు.")).toBeInTheDocument();
  });

  it("localizes page chrome, accessible actions and generated assumption labels", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    renderWithProviders(
      <LanguageProvider>
        <SimulationPage />
      </LanguageProvider>,
    );

    const horizonMonths = await screen.findByLabelText("కాల పరిమితి నెలలు");

    expect(await screen.findByRole("heading", { name: "అనుకరణ" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "అనుకరణ విభాగాలు" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "అనుకరణ నడపండి" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "డిఫాల్ట్‌లను లోడ్ చేయండి" })).toBeInTheDocument();
    expect(horizonMonths).toHaveValue(60);
    expect(screen.getByLabelText("ఇతర పరోక్ష ఖర్చు ప్రతి నెల")).toHaveValue(1000);
    expect(screen.getAllByText("అంచనాలు")).toHaveLength(2);
    expect(screen.queryByText("Horizon Months")).not.toBeInTheDocument();
    expect(document.documentElement.lang).toBe("te");
  });

  it("renders page validation text in Telugu instead of the English fallback", async () => {
    const validation = translate("te", "simulation.validation.startYearMonth");
    server.use(
      http.get("/api/simulation/defaults", () =>
        HttpResponse.json({
          meta: { horizon_months: 60, start_year_month: "2026-013" },
          herd: { does: 50, bucks: 2 },
        }),
      ),
    );
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    renderWithProviders(
      <LanguageProvider>
        <SimulationPage />
      </LanguageProvider>,
    );

    expect(await screen.findByText(validation, { selector: "p[role='alert']" })).toBeInTheDocument();
    expect(validation).not.toBe(translate("en", "simulation.validation.startYearMonth"));
  });

  it("localizes extracted field-help and numeric-validation controls", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    renderWithProviders(
      <LanguageProvider>
        <SimulationPage />
      </LanguageProvider>,
    );

    const horizonMonths = await screen.findByLabelText("కాల పరిమితి నెలలు");
    expect(
      screen.getByRole("button", { name: "కాల పరిమితి నెలలు వివరించండి?" }),
    ).toHaveAttribute("title", "కాల పరిమితి నెలలు అంటే ఏమిటి?");

    fireEvent.change(horizonMonths, { target: { value: "" } });
    expect(await screen.findByText("విలువ అవసరం.")).toBeInTheDocument();
  });
});

function LanguageSwitchForTest() {
  const { setLanguage } = useLanguage();
  return <button onClick={() => setLanguage("te")}>Switch Telugu</button>;
}

describe("Simulation help language changes", () => {
  it("updates an open help body and its facts when the selected language changes", async () => {
    renderWithProviders(<LanguageProvider><LanguageSwitchForTest /><SimulationPage /></LanguageProvider>);
    const switcher = screen.getByRole("button", { name: "Switch Telugu" });
    await screen.findByLabelText("Horizon Months");
    fireEvent.click(screen.getByRole("button", { name: "Explain Horizon Months?" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/How many months the projection runs/)).toBeInTheDocument();
    fireEvent.click(switcher);
    expect(await within(dialog).findByText(/అంచనా నడిచే నెలల సంఖ్య/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/How many months the projection runs/)).toBeNull();
    expect(within(dialog).getByRole("heading")).toHaveTextContent("కాల పరిమితి నెలలు");
  });
});
