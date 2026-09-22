/**
 * i18n gate (ITEM 5, 2026-09-21 playbook).
 *
 * Three one-way ratchets:
 *
 * 1. **Catalog parity** — every key in `en.ts` must exist in `te.ts` and
 *    vice versa (the runtime catalogs; TypeScript alone would not catch a
 *    missing Telugu key).
 * 2. **English-literal snapshot** — `english-literal-baseline.json` is the
 *    audited inventory of raw English JSX text (and user-facing string
 *    attributes) still awaiting Telugu. The gate fails when a NEW literal
 *    appears (ship the string in BOTH catalogs and render it through
 *    t(...)) and when a baseline entry goes STALE (a literal was translated
 *    — regenerate the baseline so the inventory stays honest).
 * 3. **Shrink-only ceiling** — the baseline length is pinned. It may be
 *    lowered (localization wins shrink it) but never raised: hand-growing
 *    the JSON to whitelist future English cannot pass review quietly.
 *
 * Regenerate after translating: `node scripts/scan-english-literals.mjs --write`
 * (and lower ENGLISH_LITERAL_CEILING to the new length in the same change).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import en from "@/lib/i18n/en";
import te from "@/lib/i18n/te";
import { scanEnglishLiterals } from "../../../scripts/scan-english-literals.mjs";

const BASELINE_PATH = join(import.meta.dirname, "english-literal-baseline.json");

/** The baseline may only shrink. Lower this number in the same change that
 * regenerates the baseline after localization work — never raise it.
 * (History: 357 at the 2026-09-21 audit → 457 when the scanner learned to
 * read string attributes; every localization pass since then only lowers
 * it.) */
const ENGLISH_LITERAL_CEILING = 457;

describe("i18n gate (ITEM 5)", () => {
  it("the two catalogs carry exactly the same keys", () => {
    const enKeys = new Set(Object.keys(en));
    const teKeys = new Set(Object.keys(te));
    const missingInTe = [...enKeys].filter((key) => !teKeys.has(key));
    const missingInEn = [...teKeys].filter((key) => !enKeys.has(key));
    expect(
      { missingInTe, missingInEn },
      "every string must land in BOTH en.ts and te.ts",
    ).toEqual({ missingInTe: [], missingInEn: [] });
  });

  it("no untranslated English beyond the audited baseline (added AND stale checked)", () => {
    const baseline: string[] = JSON.parse(readFileSync(BASELINE_PATH, "utf8"));
    const baselineSet = new Set(baseline);
    const current = scanEnglishLiterals();
    const currentSet = new Set(current);
    const added = current.filter((entry) => !baselineSet.has(entry));
    const stale = baseline.filter((entry) => !currentSet.has(entry));
    expect(
      { added, stale },
      [
        "`added`: new hardcoded English JSX text/attributes — add the string to BOTH",
        "en.ts and te.ts and render it through t(...).",
        "`stale`: baseline entries that no longer exist — a literal was translated;",
        "regenerate with `node scripts/scan-english-literals.mjs --write` and lower",
        "ENGLISH_LITERAL_CEILING in gate.test.ts to the new baseline length.",
      ].join(" "),
    ).toEqual({ added: [], stale: [] });
  });

  it("the baseline only ever shrinks (pinned ceiling)", () => {
    const baseline: string[] = JSON.parse(readFileSync(BASELINE_PATH, "utf8"));
    expect(
      baseline.length,
      [
        "the English-literal baseline grew past its pinned ceiling — hand-growing the",
        "JSON to whitelist new English is not the ratchet; translate the string instead",
      ].join(" "),
    ).toBeLessThanOrEqual(ENGLISH_LITERAL_CEILING);
  });
});
