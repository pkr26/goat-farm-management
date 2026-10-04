/**
 * Task-title arguments skip null values and format dates only for date-named fields.
 */

import { describe, expect, it } from "vitest";

import { resolveTaskTitle } from "./task-title";

describe("resolveTaskTitle null interpolation args", () => {
  it("skips a null arg the template uses, leaving its placeholder unfilled", () => {
    const rendered = resolveTaskTitle(
      {
        title: "fallback",
        title_key: "feed_reorder",
        title_args: { ingredient: "Maize", qty_on_hand: null, reorder_level: 50 },
      },
      "en",
    );
    expect(rendered).toBe("Reorder Maize: {qty_on_hand} kg on hand (reorder level 50 kg)");
    expect(rendered).not.toContain("null");
  });

  it("skips an undefined arg the template uses, leaving its placeholder unfilled", () => {
    const rendered = resolveTaskTitle(
      {
        title: "fallback",
        title_key: "feed_reorder",
        title_args: {
          ingredient: "Maize",
          qty_on_hand: undefined,
          reorder_level: 50,
        } as Record<string, unknown>,
      },
      "en",
    );
    expect(rendered).toBe("Reorder Maize: {qty_on_hand} kg on hand (reorder level 50 kg)");
    expect(rendered).not.toContain("undefined");
  });

  it("interpolates an ISO-date-valued arg raw unless it is named date/*_date", () => {
    // Under m09869 (`name === "date"` → `!==`) this arg routes through
    // formatDate and renders "Reorder 1 Oct 2026: …" — the raw pass-through
    // below is the contract: the date formatting arm is keyed on the ARG
    // NAME (the backend taskGen catalog's date field names), never on the
    // value's shape (2026-10-01 audit, 10-4).
    const rendered = resolveTaskTitle(
      {
        title: "fallback",
        title_key: "feed_reorder",
        title_args: { ingredient: "2026-10-01", qty_on_hand: 10, reorder_level: 5 },
      },
      "en",
    );
    expect(rendered).toBe("Reorder 2026-10-01: 10 kg on hand (reorder level 5 kg)");
  });

  it("formats an ISO date through formatDate for the *_date arg name", () => {
    // The positive arm of the same contract: a `*_date`-named arg holding an
    // ISO date IS locale-formatted (en renders "1 Oct 2026").
    const rendered = resolveTaskTitle(
      {
        title: "fallback",
        title_key: "pregnancy_check",
        title_args: { tag: "G-1", breeding_date: "2026-10-01" },
      },
      "en",
    );
    expect(rendered).toBe("Pregnancy check: G-1 (bred 1 Oct 2026)");
  });
});
