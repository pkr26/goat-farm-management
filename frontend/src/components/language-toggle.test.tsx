/**
 * LanguageToggle chip sizing: the EN/తెలుగు options are primary affordances
 * in the app-shell header and on the login page, so they meet the codebase's
 * 36px touch-target floor (button.tsx sm) rather than the old 28px h-7.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { LanguageToggle } from "@/components/language-toggle";
import { LanguageProvider } from "@/lib/i18n";

describe("LanguageToggle touch target", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.lang = "en";
  });
  afterEach(() => {
    localStorage.clear();
    document.documentElement.lang = "en";
  });

  it("renders both chips at the 36px floor", () => {
    render(
      <LanguageProvider>
        <LanguageToggle />
      </LanguageProvider>,
    );

    expect(screen.getByRole("button", { name: "EN" })).toHaveClass("h-9");
    expect(screen.getByRole("button", { name: "తెలుగు" })).toHaveClass("h-9");
  });
});
