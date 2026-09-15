/**
 * i18n layer: the pure resolver (English fallback for missing Telugu keys,
 * {var} interpolation), the LanguageProvider's localStorage persistence and
 * <html lang> side-effect, the LanguageToggle control, and the providerless
 * default (English, no crash) that every pre-existing page test relies on.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { LanguageToggle } from "@/components/language-toggle";
import en from "@/lib/i18n/en";
import te from "@/lib/i18n/te";
import {
  interpolate,
  LANGUAGES,
  LANGUAGE_STORAGE_KEY,
  LanguageProvider,
  translate,
  useLanguage,
  useT,
} from "@/lib/i18n";

describe("translate — pure resolver", () => {
  it("resolves English keys from the English catalog", () => {
    expect(translate("en", "tasks.title")).toBe("Tasks");
    expect(translate("en", "common.cancel")).toBe("Cancel");
  });

  it("resolves Telugu keys from the Telugu catalog", () => {
    expect(translate("te", "tasks.title")).toBe("పనులు");
    expect(translate("te", "tasks.tab.awaiting")).toBe("ధృవీకరణ కోసం వేచివున్నవి");
  });

  it("falls back to English when Telugu has no entry for a key", () => {
    // The catalog type is Partial by design; simulate a not-yet-translated
    // key and prove the fallback is the English text, never the raw key.
    const key = "tasks.form.assignToWorker";
    const original = te[key];
    delete te[key];
    try {
      expect(translate("te", key)).toBe(en[key]);
    } finally {
      te[key] = original;
    }
  });

  it("keeps the shipped Telugu catalog at full key parity with English", () => {
    // Every key this campaign wires is worker-critical; a new English key
    // without its Telugu twin would leak English onto a translated surface.
    const enKeys = Object.keys(en).sort();
    expect(Object.keys(te).sort()).toEqual(enKeys);
  });

  it("interpolates {var} tokens and leaves unknown tokens verbatim", () => {
    expect(interpolate("every {days}d", { days: 7 })).toBe("every 7d");
    expect(interpolate("by {name}", { name: "Raju" })).toBe("by Raju");
    expect(interpolate("{known} {unknown}", { known: "x" })).toBe("x {unknown}");
    expect(translate("te", "tasks.viaRole", { role: "Vet" })).toBe("Vet ద్వారా");
  });

  it("marks the skip reason as required in both languages (skip is blocked without one)", () => {
    // The skip dialog disables its confirm until a reason is typed (backend
    // min_length=1), so the label must not claim "(optional)".
    expect(translate("en", "tasks.skip.reason")).toBe("Reason *");
    expect(translate("te", "tasks.skip.reason")).toBe("కారణం *");
    expect(translate("te", "tasks.skip.reason")).not.toContain("ఐచ్ఛికం");
  });

  it("lists exactly English and Telugu", () => {
    expect([...LANGUAGES]).toEqual(["en", "te"]);
  });
});

/** Probe components that consume the context the way real pages do. */
function LanguageProbe() {
  const t = useT();
  const { language, setLanguage } = useLanguage();
  return (
    <div>
      <output data-testid="language">{language}</output>
      <output data-testid="title">{t("tasks.title")}</output>
      <button type="button" onClick={() => setLanguage("te")}>
        set-te
      </button>
    </div>
  );
}

describe("LanguageProvider", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.lang = "en";
  });
  afterEach(() => {
    localStorage.clear();
    document.documentElement.lang = "en";
  });

  it("defaults to English and marks <html lang>", () => {
    renderProvider();
    expect(screen.getByTestId("language")).toHaveTextContent("en");
    expect(screen.getByTestId("title")).toHaveTextContent("Tasks");
    expect(document.documentElement.lang).toBe("en");
  });

  it("switching to Telugu updates rendered copy, html lang and localStorage", async () => {
    const user = userEvent.setup();
    renderProvider();

    await user.click(screen.getByRole("button", { name: "set-te" }));

    expect(screen.getByTestId("language")).toHaveTextContent("te");
    expect(screen.getByTestId("title")).toHaveTextContent("పనులు");
    expect(document.documentElement.lang).toBe("te");
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("te");
  });

  it("adopts a persisted Telugu choice on mount", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    renderProvider();

    await waitFor(() => expect(screen.getByTestId("language")).toHaveTextContent("te"));
    expect(screen.getByTestId("title")).toHaveTextContent("పనులు");
  });

  it("ignores a corrupted stored value (anything but 'te' is English)", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "hindi");
    renderProvider();

    // Give the adoption effect a tick to (not) apply.
    await act(async () => {});
    expect(screen.getByTestId("language")).toHaveTextContent("en");
  });
});

describe("LanguageToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.lang = "en";
  });
  afterEach(() => {
    localStorage.clear();
    document.documentElement.lang = "en";
  });

  it("exposes EN and తెలుగు options with pressed state, and persists a switch", async () => {
    const user = userEvent.setup();
    render(
      <LanguageProvider>
        <LanguageToggle />
      </LanguageProvider>,
    );

    const en = screen.getByRole("button", { name: "EN" });
    const te = screen.getByRole("button", { name: "తెలుగు" });
    expect(en).toHaveAttribute("aria-pressed", "true");
    expect(te).toHaveAttribute("aria-pressed", "false");

    await user.click(te);

    expect(te).toHaveAttribute("aria-pressed", "true");
    expect(en).toHaveAttribute("aria-pressed", "false");
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("te");
    expect(document.documentElement.lang).toBe("te");
  });
});

describe("useT without a provider", () => {
  it("renders English and never throws", () => {
    render(<LanguageProbe />);
    expect(screen.getByTestId("language")).toHaveTextContent("en");
    expect(screen.getByTestId("title")).toHaveTextContent("Tasks");
  });
});

function renderProvider() {
  return render(
    <LanguageProvider>
      <LanguageProbe />
    </LanguageProvider>,
  );
}
