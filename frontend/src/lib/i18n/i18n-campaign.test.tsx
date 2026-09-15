/**
 * i18n layer — fresh-domain mutation campaign kills (2026-09): the
 * interpolation fallback, providerless default context, storage-key
 * namespace, blocked-storage recovery, enum-label language resolution, and
 * the language toggle's active styling.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LanguageToggle } from "@/components/language-toggle";
import { enumLabel } from "@/lib/enum-labels";
import { interpolate } from "@/lib/i18n";
import { LanguageProvider, LANGUAGE_STORAGE_KEY, useLanguage, useT } from "@/lib/i18n";

describe("interpolate — campaign kills", () => {
  it("returns the template untouched when no vars are given", () => {
    expect(interpolate("Hi {name}")).toBe("Hi {name}");
    expect(interpolate("Hi {name}", { name: "Raju" })).toBe("Hi Raju");
    // Unknown tokens stay verbatim so missing variables are reviewable.
    expect(interpolate("Hi {name}", { other: "x" })).toBe("Hi {name}");
  });
});

describe("providerless default context — campaign kills", () => {
  it("behaves exactly like English with a no-op setter", () => {
    let seen = "";
    function Bare() {
      const t = useT();
      seen = t("common.close");
      return null;
    }
    render(<Bare />);
    expect(seen).toBe("Close");
  });
});

describe("LanguageProvider — campaign kills", () => {
  it("persists the choice under the namespaced storage key", async () => {
    const user = userEvent.setup();
    function Probe() {
      const { language } = useLanguage();
      return <span data-testid="lang">{language}</span>;
    }
    render(
      <LanguageProvider>
        <Probe />
        <LanguageToggle />
      </LanguageProvider>,
    );

    await user.click(screen.getByRole("button", { name: /తెలుగు|Te/i }));
    await waitFor(() => expect(screen.getByTestId("lang")).toHaveTextContent("te"));
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("te");
  });

  it("marks the active language button with the segmented-control styling", () => {
    render(
      <LanguageProvider>
        <LanguageToggle />
      </LanguageProvider>,
    );
    const group = screen.getByRole("group", { name: "Language / భాష" });
    // The segmented container's own chip styling.
    expect(group.className).toContain("rounded-lg");
    const english = screen.getByRole("button", { name: "EN" });
    expect(english.className).toContain("bg-primary/10");
    expect(english.className).toContain("text-xs");
    const telugu = screen.getByRole("button", { name: /తెలుగు/ });
    expect(telugu.className).not.toContain("bg-primary/10");
  });

  it("recovers to English when reading storage throws", () => {
    const original = window.localStorage.getItem;
    Object.defineProperty(window.localStorage, "getItem", {
      configurable: true,
      value: () => {
        throw new Error("SecurityError");
      },
    });
    try {
      function Probe() {
        const { language } = useLanguage();
        return <span data-testid="lang">{language}</span>;
      }
      render(
        <LanguageProvider>
          <Probe />
        </LanguageProvider>,
      );
      expect(screen.getByTestId("lang")).toHaveTextContent("en");
    } finally {
      Object.defineProperty(window.localStorage, "getItem", {
        configurable: true,
        value: original,
      });
    }
  });
});

describe("enumLabel — campaign kills", () => {
  it("falls back to a titled value for unknown codes", () => {
    expect(enumLabel("status", "MYSTERY" as never)).toBe("Mystery");
    expect(enumLabel("status", null)).toBe("—");
    expect(enumLabel("status", undefined)).toBe("—");
    expect(enumLabel("status", "")).toBe("—");
  });

  it("resolves Telugu labels for translated kinds and English otherwise", () => {
    // sex carries a Telugu glossary entry.
    expect(enumLabel("sex", "F", "te")).toBe("ఆడ");
    expect(enumLabel("sex", "F", "en")).toBe("Female");
    // Every kind now carries a full Telugu map.
    expect(enumLabel("status", "ACTIVE", "te")).toBe("సక్రియం");
    expect(enumLabel("lossCause", "INJURY", "te")).toBe("గాయం");
  });
});

void vi;
