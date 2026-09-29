/**
 * i18n gate (ITEM 5, 2026-09-21 playbook).
 *
 * Three one-way ratchets:
 *
 * 1. **Catalog parity** — every key in `en.ts` must exist in `te.ts` and
 *    vice versa (the runtime catalogs; TypeScript alone would not catch a
 *    missing Telugu key).
 * 1b. **Placeholder parity** — every `{var}` interpolated by the English
 *    template must be interpolated by its Telugu twin and vice versa. A
 *    dropped placeholder ships a garbled sentence (2026-09-29 audit: te's
 *    opsSim.validation.needsBred lost {bucket}, leaving a dangling dative).
 * 2. **English-literal snapshot** — `english-literal-baseline.json` is the
 *    audited inventory of raw English UI copy (JSX text, user-facing string
 *    attributes and copy props, expression-container strings, default
 *    parameters, toasts, zod messages) still awaiting Telugu. The gate
 *    fails when a NEW literal appears (ship the string in BOTH catalogs and
 *    render it through t(...)) and when a baseline entry goes STALE (a
 *    literal was translated — regenerate the baseline so the inventory
 *    stays honest).
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
 * read string attributes → 753 at the 2026-09-28 ONE-TIME scanner-driven
 * expansion — AUDIT_REPORT_2026-09-28 H7: the scanner now also sees JSX
 * expression-container strings, camelCase copy props (emptyMessage,
 * dialogTitle, …), default-parameter strings, toast.* literals and zod
 * messages, so the baseline finally matches the true untranslated
 * inventory. The ratchet remains shrink-only from here: localization wins
 * lower it, nothing raises it. → 437 when the animal profile page
 * (animals/[id]) moved to the animalDetail.* catalog keys and the
 * already-translated planner/purchases entries left the inventory. → 156
 * when the animals list page and the shared animal/health-target pickers
 * moved to the animals.list/create/validation/toast/filter.* and picker.*
 * keys (the duplicated bucket-sex maps also folded into lib/bucket-sex.ts).
 * → 72 when the breeding and kidding pages moved to the breeding.* and
 * kidding.* catalog keys. → 23 when the account dialog and the shared
 * remote picker moved to the account.* and picker.remote.* keys (the
 * already-converted worker-board and sidebar entries left with them).
 * → 3 when the boundary components (permission gate, permissions error,
 * stale-data notice, charts, theme toggle, landing loading), the health
 * events fallback, the ultrasound redirect shim, the breeding candidate
 * picker and the farm-switcher aria-label moved to the catalogs, and the
 * scanner learned template-literal attributes and containers
 * (2026-09-29 audit). The 3 survivors are deliberate: the SEO metadata
 * description in app/layout.tsx and the two assertion-anchor constants in
 * lib/persisted-numbers.ts (never rendered directly).) */
const ENGLISH_LITERAL_CEILING = 3;

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

  it("the two catalogs interpolate exactly the same {placeholders}", () => {
    const variablesOf = (template: string): Set<string> =>
      new Set([...template.matchAll(/\{(\w+)\}/g)].map((match) => match[1]!));
    const drifted: Record<string, { onlyEn: string[]; onlyTe: string[] }> = {};
    for (const [key, english] of Object.entries(en)) {
      const telugu = te[key as keyof typeof te];
      if (telugu === undefined) continue; // key parity is the test above
      const enVars = variablesOf(english);
      const teVars = variablesOf(telugu);
      const onlyEn = [...enVars].filter((name) => !teVars.has(name));
      const onlyTe = [...teVars].filter((name) => !enVars.has(name));
      if (onlyEn.length > 0 || onlyTe.length > 0) {
        drifted[key] = { onlyEn, onlyTe };
      }
    }
    expect(
      drifted,
      [
        "a translation dropped or added an interpolation placeholder — the sentence",
        "renders garbled. Keep every {var} from the English template in the Telugu",
        "twin (a no-case language may repeat the same var; it may not drop one).",
      ].join(" "),
    ).toEqual({});
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
