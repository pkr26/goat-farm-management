/**
 * English-literal scanner for the i18n gate (ITEM 5).
 *
 * Shared by src/lib/i18n/gate.test.ts (the ratchet) and this script's CLI
 * mode (baseline regeneration). It scans JSX text children AND the
 * user-facing string attributes (title, description, placeholder, label,
 * aria-label, alt) of every non-test .tsx file under src/app and
 * src/components, returning `"<relpath>: <literal>"` entries for English
 * copy that is not routed through the language catalog.
 *
 * Run `node scripts/scan-english-literals.mjs --write` to regenerate
 * src/lib/i18n/english-literal-baseline.json after translating literals
 * (the gate fails on stale baseline entries, so regeneration is part of
 * every localization change — and the pinned ceiling in gate.test.ts only
 * ever moves DOWN).
 */

import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const SRC_ROOT = join(import.meta.dirname, "..", "src");
const SCANNED_DIRS = ["app", "components"];
const BASELINE_PATH = join(import.meta.dirname, "..", "src", "lib", "i18n", "english-literal-baseline.json");

// Copy that is not translatable prose: symbols, units, brand, pure numbers.
const NON_TEXTUAL =
  /^(?:[—–\-•.…:|,/()#%*0-9\s]+|EN|తెలుగు|Herdly|₹.*|kg|d)$/;

// JSX text children: `>Some English copy<`. Punctuation beyond the original
// audit set (ellipsis, sentence enders, parentheses) is included so a
// trailing "…" can no longer hide a literal from the gate.
const JSX_TEXT = />\s*([A-Z][A-Za-z0-9 ,'&%/—–….!?:()\-]{3,80})\s*</g;

// User-facing string attributes on JSX elements.
const ATTRIBUTE_NAMES =
  "title|description|placeholder|label|aria-label|alt|subtitle|summary";
const ATTRIBUTE = new RegExp(
  `\\b(?:${ATTRIBUTE_NAMES})=["']([A-Za-z][A-Za-z0-9 ,'&%/—–….!?:()\\-]{3,80})["']`,
  "g",
);

function allSourceFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".") || entry === "generated") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...allSourceFiles(full));
    else if (/\.tsx$/.test(entry) && !/\.test\./.test(entry)) out.push(full);
  }
  return out;
}

export function scanEnglishLiterals() {
  const found = new Set();
  for (const dir of SCANNED_DIRS) {
    for (const file of allSourceFiles(join(SRC_ROOT, dir))) {
      const text = readFileSync(file, "utf8");
      const rel = file.replace(SRC_ROOT, "");
      for (const regex of [JSX_TEXT, ATTRIBUTE]) {
        regex.lastIndex = 0;
        for (const match of text.matchAll(regex)) {
          const literal = match[1].trim();
          if (NON_TEXTUAL.test(literal)) continue;
          found.add(`${rel}: ${literal}`);
        }
      }
    }
  }
  return [...found].sort();
}

if (process.argv[1] === import.meta.filename && process.argv.includes("--write")) {
  writeFileSync(BASELINE_PATH, `${JSON.stringify(scanEnglishLiterals(), null, 2)}\n`);
  console.log(`baseline regenerated: ${scanEnglishLiterals().length} entries`);
}
