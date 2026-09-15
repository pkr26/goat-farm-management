/**
 * Locale-aware date rendering (2026-09 i18n campaign): with the active
 * language on Telugu, formatDate/formatFarmDateTime render through Intl
 * te-IN (Telugu month names); English sessions keep the hand-built
 * byte-identical output.
 */

import { afterEach, describe, expect, it } from "vitest";

import { setActiveLanguage } from "@/lib/active-language";

import { formatDate, formatFarmDateTime, setActiveFarmTimezone } from "./format";

afterEach(() => {
  setActiveLanguage("en");
  setActiveFarmTimezone(null);
});

describe("formatDate — Telugu locale", () => {
  it("renders Telugu month abbreviations when the active language is te", () => {
    setActiveLanguage("te");
    // Intl te-IN short months; assert the shape plus the Telugu month letter
    // rather than a brittle byte-exact string (CLDR abbreviations drift).
    expect(formatDate("2026-08-05")).toMatch(/^5,? .* 2026$/);
    expect(formatDate("2026-08-05")).toContain("ఆగ");
    expect(formatDate("2026-01-05")).toContain("జన");
  });

  it("keeps the English table byte-identical in en sessions", () => {
    setActiveLanguage("en");
    expect(formatDate("2026-08-05")).toBe("5 Aug 2026");
    expect(formatDate("2026-09-05")).toBe("5 Sep 2026");
  });

  it("keeps the em-dash fallbacks in both languages", () => {
    setActiveLanguage("te");
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not-a-date")).toBe("—");
    expect(formatDate("2026-02-30")).toBe("—");
  });
});

describe("formatFarmDateTime — Telugu locale", () => {
  it("renders the instant in te-IN when the active language is te", () => {
    setActiveLanguage("te");
    setActiveFarmTimezone("Asia/Kolkata");
    const rendered = formatFarmDateTime("2026-08-05T14:07:00");
    // 14:07 UTC is 19:37 IST; te-IN renders the Telugu month name.
    expect(rendered).toContain("2026");
    expect(rendered).toContain("ఆగ");
  });

  it("keeps the English assembly byte-identical in en sessions", () => {
    setActiveLanguage("en");
    setActiveFarmTimezone("Asia/Kolkata");
    expect(formatFarmDateTime("2026-08-05T14:07:00")).toBe("5 Aug 2026, 7:37 pm");
  });
});
