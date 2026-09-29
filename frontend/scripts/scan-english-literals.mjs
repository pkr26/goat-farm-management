/**
 * English-literal scanner for the i18n gate (ITEM 5).
 *
 * Shared by src/lib/i18n/gate.test.ts (the ratchet) and this script's CLI
 * mode (baseline regeneration). It scans every non-test .tsx file under
 * src/app and src/components, returning `"<relpath>: <literal>"` entries
 * for English copy that is not routed through the language catalog:
 *
 *  1. JSX text children (`>Some English copy<`).
 *  2. User-facing string attributes (title, description, placeholder,
 *     label, aria-label, alt, subtitle, summary, emptyMessage, helperText)
 *     plus camelCase copy props ending in Title/Description/Label/
 *     Placeholder/Message/HelperText (dialogTitle, searchLabel, …) — and
 *     the same attributes as TEMPLATE literals
 *     (`aria-label={\`Switch farm — current: ${farm}\`}`, 2026-09-29).
 *  3. String literals in JSX expression containers: `{"text"}`,
 *     `{busy ? "Saving…" : "Add"}`, `{ready && "Done."}` and template
 *     literal containers (`{\`${count} saved\`}`).
 *  4. Default-parameter string literals (`placeholder = "Pick an animal"`).
 *  5. String/template first args to toast.success/info/error/warning(...).
 *  6. Zod message literals: trailing string/template args of
 *     .min/.max/.refine/.superRefine/.regex/.email/.url/… calls, and
 *     `message:`/`required_error:`/`invalid_type_error:` property values.
 *  7. `const …MESSAGE… = "…"` exports (e.g. lib/persisted-numbers.ts).
 *
 * src/lib/*.ts files are scanned for categories 5–7 only (no JSX there).
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
const LIB_DIR = "lib";
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
  "title|description|placeholder|label|aria-label|alt|subtitle|summary|emptyMessage|helperText";
const ATTRIBUTE = new RegExp(
  `\\b(?:${ATTRIBUTE_NAMES})=["']([A-Za-z][A-Za-z0-9 ,'&%/—–….!?:()#\\-]{3,80})["']`,
  "g",
);

// camelCase copy props that the flat whitelist above cannot see
// (dialogTitle=, dialogDescription=, searchLabel=, searchPlaceholder=,
// noAccessMessage=, centerLabel=, …). Same value shape as ATTRIBUTE.
const CAMEL_ATTRIBUTE = new RegExp(
  `\\b[A-Za-z]*(?:Title|Description|Label|Placeholder|Message|HelperText)=["']([A-Za-z][A-Za-z0-9 ,'&%/—–….!?:()#\\-]{3,120})["']`,
  "g",
);

// The same copy attributes as template literals — the interpolated
// aria-labels (`aria-label={\`Switch farm — current: ${farmName}\`}`) were a
// proven scanner blind spot (2026-09-29 audit). Interpolation is stripped
// before the prose filter, so `…current: ${farmName}\` still reads as copy.
const TEMPLATE_ATTRIBUTE = new RegExp(
  `\\b(?:${ATTRIBUTE_NAMES})=\\{?\\\`([^\\\`]{4,200})\\\``,
  "g",
);

// String literals in JSX expression containers: `{"text"}`,
// `{cond ? "a" : "b"}`, `{cond && "text"}` — plus template literal
// containers (`{\`${count} saved\`}`).
const CONTAINER = /\{\s*"([^"]{4,300})"\s*\}/g;
const CONTAINER_TEMPLATE = /\{\s*`([^`]{4,300})`\s*\}/g;
const TERNARY = /[?:]\s*"([^"]{4,300})"/g;
const LOGICAL_AND = /&&\s*"([^"]{4,300})"/g;

// Default-parameter string literals: `(placeholder = "Pick an animal")`,
// `({ emptyMessage = "Nothing here." })`.
const DEFAULT_PARAM = /[,({]\s*[A-Za-z_$][\w$]*\s*=\s*"([^"]{4,300})"/g;

// String/template first args to toast.success/info/error/warning(...).
const TOAST =
  /\btoast\.(?:success|info|error|warning)\s*\(\s*(?:"([^"]{4,300})"|`([^`]{4,300})`)/g;

// message:/required_error:/invalid_type_error: property values (zod issue
// config objects and friends).
const MESSAGE_PROPERTY =
  /\b(?:message|required_error|invalid_type_error)\s*:\s*"([^"]{4,300})"/g;

// Exported message constants, e.g. MIN_PERSISTED_MONEY_MESSAGE in
// lib/persisted-numbers.ts.
const MESSAGE_CONSTANT =
  /\bconst\s+[A-Za-z0-9_$]*MESSAGE[A-Za-z0-9_$]*\s*=\s*"([^"]{4,300})"/g;

// Zod message-bearing calls. The args capture tolerates one level of
// nesting (arrow predicates, Number(...) wraps) and quoted segments; string
// literals inside are then filtered to prose so predicate operands
// (`tz !== "Factory"`) and path arrays (`["confirm_password"]`) stay out.
const ZOD_CALL =
  /\.\s*(?:min|max|refine|superRefine|superrefine|regex|email|url|nonnegative|positive|length|gt|lt|gte|lte)\s*\(((?:[^()"'`]|\([^()]*\)|"[^"]*"|'[^']*'|`[^`]*`){0,500}?)\)/g;
const STRING_IN_ARGS = /"([^"]{4,300})"|`([^`]{4,300})`/g;

// A prose literal starts uppercase and runs 4–200 chars of copy characters
// (curly quotes and semicolons included: plan-conflict toasts use both).
const PROSE = /^[A-Z][A-Za-z0-9 ,'&%/—–….!?:()\-₹;“”]{3,199}$/;
// All-caps tokens are codes, not copy: HTTP methods, enum values.
const CODE_TOKEN = /^[A-Z0-9_]+$/;

function stripInterpolation(value) {
  return value.replace(/\$\{[^}]*\}/g, "");
}

/**
 * Filter a candidate to English display prose. Returns the normalized
 * literal (whitespace collapsed, interpolation kept for stability) or null.
 * `multiWord` demands a phrase — single capitalized words inside zod args
 * are enum/code operands, not messages.
 */
function proseLiteral(raw, { multiWord = false } = {}) {
  const candidate = stripInterpolation(raw).replace(/\s+/g, " ").trim();
  if (!PROSE.test(candidate)) {
    // A template that OPENS with interpolation reads as mid-sentence prose
    // (`${count} records could not be sent.`) — a lowercase start is fine
    // when the literal itself proves it is interpolated MULTI-WORD copy.
    // The space requirement keeps identifier slugs
    // (`clearance-reference-${animalId}`) and cache keys out; the no-hyphen
    // rule keeps className templates out (every Tailwind atom is a
    // hyphen-joined token, prose is not).
    const interpolatedProse =
      raw.includes("${") &&
      /\s/.test(candidate) &&
      !/[\w]-[\w]/.test(candidate) &&
      /^[a-z][A-Za-z0-9 ,'&%/—–….!?:()₹;“”]{3,199}$/.test(candidate);
    if (!interpolatedProse) return null;
  }
  if (NON_TEXTUAL.test(candidate)) return null;
  if (CODE_TOKEN.test(candidate)) return null;
  // All-caps runs joined by separators are date/number formats or compound
  // codes ("YYYY-MM-DD", "DD/MM/YYYY"), never prose.
  if (/^[A-Z0-9]+(?:[-_/][A-Z0-9]+)+$/.test(candidate)) return null;
  // Slash-bearing, space-free tokens are identifiers, not copy
  // ("Asia/Kolkata", import-ish paths).
  if (candidate.includes("/") && !/\s/.test(candidate)) return null;
  if (multiWord && !/[\s.!?…]/.test(candidate)) return null;
  return raw.replace(/\s+/g, " ").trim();
}

/**
 * All English literals in one source text. `jsx: false` restricts to the
 * non-JSX categories (toasts, zod messages, message constants) for the
 * .ts files under src/lib.
 */
export function scanTextLiterals(text, { jsx = true } = {}) {
  const found = new Set();
  const collect = (regex, pick, options) => {
    regex.lastIndex = 0;
    for (const match of text.matchAll(regex)) {
      const literal = proseLiteral(pick(match), options);
      if (literal) found.add(literal);
    }
  };
  if (jsx) {
    for (const regex of [JSX_TEXT, ATTRIBUTE, CAMEL_ATTRIBUTE]) {
      regex.lastIndex = 0;
      for (const match of text.matchAll(regex)) {
        const literal = match[1].trim();
        if (NON_TEXTUAL.test(literal)) continue;
        found.add(literal);
      }
    }
    collect(CONTAINER, (m) => m[1]);
    collect(CONTAINER_TEMPLATE, (m) => m[1]);
    collect(TERNARY, (m) => m[1]);
    collect(LOGICAL_AND, (m) => m[1]);
    collect(DEFAULT_PARAM, (m) => m[1]);
    collect(TEMPLATE_ATTRIBUTE, (m) => m[1], { multiWord: true });
  }
  collect(TOAST, (m) => m[1] ?? m[2]);
  collect(MESSAGE_PROPERTY, (m) => m[1]);
  collect(MESSAGE_CONSTANT, (m) => m[1]);
  ZOD_CALL.lastIndex = 0;
  for (const call of text.matchAll(ZOD_CALL)) {
    STRING_IN_ARGS.lastIndex = 0;
    for (const match of call[1].matchAll(STRING_IN_ARGS)) {
      const literal = proseLiteral(match[1] ?? match[2], { multiWord: true });
      if (literal) found.add(literal);
    }
  }
  return found;
}

function allSourceFiles(dir, extensions) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".") || entry === "generated") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...allSourceFiles(full, extensions));
    else if (extensions.test(entry) && !/\.test\./.test(entry)) out.push(full);
  }
  return out;
}

export function scanEnglishLiterals() {
  const found = new Set();
  for (const dir of SCANNED_DIRS) {
    for (const file of allSourceFiles(join(SRC_ROOT, dir), /\.tsx$/)) {
      const rel = file.replace(SRC_ROOT, "");
      for (const literal of scanTextLiterals(readFileSync(file, "utf8"))) {
        found.add(`${rel}: ${literal}`);
      }
    }
  }
  // Library modules carry no JSX, but their message constants and zod
  // messages render on screen all the same (lib/i18n IS the catalog).
  for (const file of allSourceFiles(join(SRC_ROOT, LIB_DIR), /\.ts$/)) {
    if (file.includes(`${join(SRC_ROOT, LIB_DIR)}/i18n/`)) continue;
    const rel = file.replace(SRC_ROOT, "");
    for (const literal of scanTextLiterals(readFileSync(file, "utf8"), { jsx: false })) {
      found.add(`${rel}: ${literal}`);
    }
  }
  return [...found].sort();
}

if (process.argv[1] === import.meta.filename && process.argv.includes("--write")) {
  writeFileSync(BASELINE_PATH, `${JSON.stringify(scanEnglishLiterals(), null, 2)}\n`);
  console.log(`baseline regenerated: ${scanEnglishLiterals().length} entries`);
}
