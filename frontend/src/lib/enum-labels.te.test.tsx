/**
 * Telugu enum labels: every kind resolves in Telugu, unknown codes fall back
 * to Title Case (never the raw SCREAMING_SNAKE code), and the default tracks
 * the active UI language (English outside a language provider).
 */

import { afterEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { setActiveLanguage } from "@/lib/active-language";
import { LanguageProvider, LANGUAGE_STORAGE_KEY, useLanguage } from "@/lib/i18n";

import { enumLabel, useEnumLabel } from "./enum-labels";

afterEach(() => setActiveLanguage("en"));

describe("enumLabel — Telugu map", () => {
  it("translates worker-facing codes", () => {
    expect(enumLabel("sex", "M", "te")).toBe("మగ");
    expect(enumLabel("sex", "F", "te")).toBe("ఆడ");
    expect(enumLabel("taskCategory", "FEED", "te")).toBe("మేత");
    expect(enumLabel("taskCategory", "CLEANING", "te")).toBe("శుభ్రం చేయడం");
    expect(enumLabel("shift", "MORNING", "te")).toBe("ఉదయం");
    expect(enumLabel("shift", "NIGHT", "te")).toBe("రాత్రి");
    expect(enumLabel("bucket", "MALE_KIDS", "te")).toBe("మగ పిల్లలు");
  });

  it("translates every enum kind, and never leaks a raw code", () => {
    // Every kind now carries a full Telugu map.
    expect(enumLabel("status", "ACTIVE", "te")).toBe("సక్రియం");
    expect(enumLabel("status", "SOLD", "te")).toBe("అమ్మబడింది");
    expect(enumLabel("taskCategory", "QUARANTINE", "te")).toBe("క్వారంటైన్");
    expect(enumLabel("bucket", "QUARANTINE", "te")).toBe("క్వారంటైన్");
    expect(enumLabel("bucket", "RESTING", "te")).toBe("విశ్రాంతి");
    expect(enumLabel("eventType", "VACCINE", "te")).toBe("టీకా");
    expect(enumLabel("outcome", "CONFIRMED_PREGNANT", "te")).toBe("గర్భం ధృవీకరించబడింది");
    expect(enumLabel("mortalityCause", "PNEUMONIA", "te")).toBe("న్యుమోనియా");
    expect(enumLabel("lossCause", "DISEASE", "te")).toBe("వ్యాధి");
    // Unknown values still never render as SCREAMING_SNAKE.
    expect(enumLabel("taskCategory", "SOME_NEW_CODE", "te")).toBe("Some New Code");
  });

  it("defaults to English outside a language provider", () => {
    expect(enumLabel("sex", "M")).toBe("Male");
    expect(enumLabel("sex", "M", "en")).toBe("Male");
    expect(enumLabel("taskCategory", "FEED")).toBe("Feeding");
    expect(enumLabel("taskCategory", null, "te")).toBe("—");
  });

  it("defaults to the active UI language when the lang arg is omitted", () => {
    setActiveLanguage("te");
    expect(enumLabel("sex", "M")).toBe("మగ");
    expect(enumLabel("status", "DEAD")).toBe("మరణించింది");
    setActiveLanguage("en");
    expect(enumLabel("sex", "M")).toBe("Male");
  });

  it("keeps the lowercase insurance wire values readable (SCREAMING_CASE maps)", () => {
    expect(enumLabel("insuranceStatus", "active")).toBe("Active");
    expect(enumLabel("insuranceStatus", "lapsed", "te")).toBe("రద్దైంది");
    expect(enumLabel("insuranceStatus", "claimed")).toBe("Claimed");
  });
});

describe("useEnumLabel — reactive binding", () => {
  function Probe() {
    const el = useEnumLabel();
    const { setLanguage } = useLanguage();
    return (
      <div>
        <output data-testid="label">{el("status", "SOLD")}</output>
        <button type="button" onClick={() => setLanguage("te")}>
          switch
        </button>
      </div>
    );
  }

  it("re-renders in the active language when it changes", async () => {
    window.localStorage.clear();
    const user = userEvent.setup();
    render(
      <LanguageProvider>
        <Probe />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("label")).toHaveTextContent("Sold");
    await user.click(screen.getByRole("button", { name: "switch" }));
    await waitFor(() =>
      expect(screen.getByTestId("label")).toHaveTextContent("అమ్మబడింది"),
    );
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("te");
    window.localStorage.clear();
  });
});
