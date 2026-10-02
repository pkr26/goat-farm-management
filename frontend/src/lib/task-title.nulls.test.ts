/**
 * Task-title null-arg semantics (2026-09-30 fresh mutation campaign): a
 * null-valued arg that the template WOULD interpolate must be skipped, so
 * the placeholder survives visibly — never rendered as the literal "null".
 *
 * Also pins the date-arm scoping of `localizedArg` (2026-10-01 audit, 10-4):
 * only args NAMED `date`/`*_date` render through formatDate; any other arg
 * holding an ISO-date-SHAPED string interpolates byte-for-byte. Kills
 * mutation m09869 (`name === "date"` → `!==`, task-title.ts:70), formerly
 * documented as a "dead catalog arm" — an equivalence that held only under
 * the backend taskGen catalog's field names, not mathematically.
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
