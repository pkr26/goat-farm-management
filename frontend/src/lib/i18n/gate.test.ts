/**
 * i18n gate (ITEM 5, 2026-09-21 playbook).
 *
 * Two one-way ratchets:
 *
 * 1. **Catalog parity** — every key in `en.ts` must exist in `te.ts` and
 *    vice versa. The MessageKey union enforces this at compile time for
 *    consumers; this test names the invariant explicitly and fails with a
 *    readable diff.
 * 2. **English-literal snapshot** — `english-literal-baseline.json` is the
 *    audited inventory of raw English JSX text still awaiting Telugu. The
 *    gate fails when a NEW literal appears (ship the string in BOTH catalogs
 *    and render it through t(...)), and the baseline is expected to only
 *    shrink — regenerating it after localizations records the win.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import en from "@/lib/i18n/en";
import te from "@/lib/i18n/te";

const SRC_ROOT = join(import.meta.dirname, "..", "..");
const SCANNED_DIRS = ["app", "components"] as const;
const BASELINE_PATH = join(import.meta.dirname, "english-literal-baseline.json");

// JSX text children that are not translatable copy: symbols, brand, units.
const NON_TEXTUAL = /^(?:[—–\-•.…:|,/()#%*0-9\s]+|EN|తెలుగు|Herdly|₹.*|kg|d)$/;

function allSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".") || entry === "generated") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...allSourceFiles(full));
    else if (/\.tsx$/.test(entry) && !/\.test\./.test(entry)) out.push(full);
  }
  return out;
}

function scanEnglishLiterals(): string[] {
  const found = new Set<string>();
  for (const dir of SCANNED_DIRS) {
    for (const file of allSourceFiles(join(SRC_ROOT, dir))) {
      const text = readFileSync(file, "utf8");
      const rel = file.replace(SRC_ROOT, "");
      for (const match of text.matchAll(/>\s*([A-Z][A-Za-z0-9 ,'&%/—–-]{3,60})\s*</g)) {
        const literal = match[1].trim();
        if (NON_TEXTUAL.test(literal)) continue;
        found.add(`${rel}: ${literal}`);
      }
    }
  }
  return [...found].sort();
}

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

  it("no new untranslated English JSX text beyond the audited baseline", () => {
    const baseline: string[] = JSON.parse(readFileSync(BASELINE_PATH, "utf8"));
    const baselineSet = new Set(baseline);
    const current = scanEnglishLiterals();
    const added = current.filter((entry) => !baselineSet.has(entry));
    expect(
      added,
      [
        "New hardcoded English JSX text. Add the string to BOTH en.ts and te.ts,",
        "render it through t(...), then (if a baseline literal disappeared)",
        "regenerate the baseline with:",
        "  node -e \"require('fs').writeFileSync('src/lib/i18n/english-literal-baseline.json', JSON.stringify(require('./scan'), null, 2))\"",
        "— or simply update the JSON by removing the literals you translated.",
      ].join(" "),
    ).toEqual([]);
  });
});
