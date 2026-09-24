// AST-based mutant manifest generator for FRONTEND mutation testing.
//
// Mirrors backend/mutation/mutate_gen.py: one mutation per site, TS/TSX via
// the TypeScript compiler API. Operators:
//
//   * compare swaps:  === <-> !==,  == <-> !=,  < <-> <=,  > <-> >=
//   * logical swaps:  && <-> ||,   ?? -> ||
//   * drop `not` (unary !)
//   * arithmetic swaps: + <-> -,  * <-> /,  & <-> |
//   * true <-> false (expression position only)
//   * int off-by-one: n -> n+1 (and n -> n-1 when n > 0)
//   * ternary branch swap: c ? t : f -> c ? f : t
//   * break <-> continue (unlabeled only)
//
// Excluded zones: everything type-position (annotations, literal types,
// `as` casts — the AST walk only touches expression nodes), string/template
// *text* (interpolated ${} expressions ARE visited), import/export
// specifiers. String literals are never mutated (error-message noise).
//
// Each mutant carries byte-offset edits into the ORIGINAL source so the
// runtime transform (mutate_transform.mjs) can slice-replace without
// re-parsing. No file is ever written — mutation is applied in-process by
// the vitest transform hook, so parallel workers on one tree are safe.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ts = require("typescript");

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const COMPARE_SWAPS = new Map([
  [ts.SyntaxKind.EqualsEqualsEqualsToken, "!=="],
  [ts.SyntaxKind.ExclamationEqualsEqualsToken, "==="],
  [ts.SyntaxKind.EqualsEqualsToken, "!="],
  [ts.SyntaxKind.ExclamationEqualsToken, "=="],
  [ts.SyntaxKind.LessThanToken, "<="],
  [ts.SyntaxKind.LessThanEqualsToken, "<"],
  [ts.SyntaxKind.GreaterThanToken, ">="],
  [ts.SyntaxKind.GreaterThanEqualsToken, ">"],
]);
const BINOP_SWAPS = new Map([
  [ts.SyntaxKind.AmpersandAmpersandToken, "||"],
  [ts.SyntaxKind.BarBarToken, "&&"],
  [ts.SyntaxKind.QuestionQuestionToken, "||"],
  [ts.SyntaxKind.PlusToken, "-"],
  [ts.SyntaxKind.MinusToken, "+"],
  [ts.SyntaxKind.AsteriskToken, "/"],
  [ts.SyntaxKind.SlashToken, "*"],
  [ts.SyntaxKind.AmpersandToken, "|"],
  [ts.SyntaxKind.BarToken, "&"],
]);

function walkSources(dir, out) {
  for (const name of readdirSync(dir).sort()) {
    const full = path.join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) {
      walkSources(full, out);
      continue;
    }
    if (!/\.(ts|tsx)$/.test(name)) continue;
    if (/\.d\.ts$/.test(name)) continue;
    if (/\.test\.(ts|tsx)$/.test(name)) continue;
    const rel = path.relative(FRONTEND, full);
    // Generated API client, test scaffolding: out of scope.
    if (rel.startsWith("src/api/generated/") || rel.startsWith("src/test/")) continue;
    out.push(rel);
  }
  return out;
}

function opName(kind) {
  const k = ts.SyntaxKind[kind];
  return k.replace(/Token$/, "").toLowerCase();
}

function generate() {
  const files = walkSources(path.join(FRONTEND, "src"), []);
  const fileMeta = {};
  const mutants = [];

  let nextId = 1;
  for (const rel of files) {
    const full = path.join(FRONTEND, rel);
    const text = readFileSync(full, "utf8");
    const scriptKind = rel.endsWith(".tsx")
      ? ts.ScriptKind.TSX
      : ts.ScriptKind.TS;
    const sf = ts.createSourceFile(full, text, ts.ScriptTarget.ES2022, true, scriptKind);
    fileMeta[rel] = {
      sha256: createHash("sha256").update(text, "utf8").digest("hex"),
      lines: sf.getLineStarts().length,
    };

    const push = (edits, op, description) => {
      const primary = edits[0];
      const pos = primary.start;
      const { line, character } = sf.getLineAndCharacterOfPosition(pos);
      mutants.push({
        id: `m${String(nextId++).padStart(5, "0")}`,
        file: rel,
        // 1-based line for coverage-map lookup
        line: line + 1,
        col: character + 1,
        op,
        desc: description,
        edits,
      });
    };

    const visit = (node) => {
      // --- type-position guard: literal `true`/`false` inside LiteralTypeNode
      const parent = node.parent;
      if (
        parent &&
        ts.isLiteralTypeNode(parent) &&
        (node.kind === ts.SyntaxKind.TrueKeyword || node.kind === ts.SyntaxKind.FalseKeyword)
      ) {
        return; // type land (`type X = true`), not runtime
      }

      if (ts.isBinaryExpression(node)) {
        const tok = node.operatorToken;
        const text = COMPARE_SWAPS.get(tok.kind);
        if (text !== undefined) {
          push(
            [{ start: tok.getStart(sf), end: tok.end, text }],
            "compare",
            `${tok.getText(sf)} -> ${text}`,
          );
        } else {
          const swapped = BINOP_SWAPS.get(tok.kind);
          if (swapped !== undefined) {
            push(
              [{ start: tok.getStart(sf), end: tok.end, text: swapped }],
              "binop",
              `${tok.getText(sf)} -> ${swapped}`,
            );
          }
        }
      } else if (
        ts.isPrefixUnaryExpression(node) &&
        node.operator === ts.SyntaxKind.ExclamationToken
      ) {
        const start = node.getStart(sf);
        push([{ start, end: start + 1, text: "" }], "not", "drop !");
      } else if (
        node.kind === ts.SyntaxKind.TrueKeyword ||
        node.kind === ts.SyntaxKind.FalseKeyword
      ) {
        const start = node.getStart(sf);
        const text = node.kind === ts.SyntaxKind.TrueKeyword ? "false" : "true";
        push([{ start, end: start + (node.kind === ts.SyntaxKind.TrueKeyword ? 4 : 5), text }], "boolconst", `-> ${text}`);
      } else if (ts.isNumericLiteral(node)) {
        const n = Number(node.text.replace(/_/g, ""));
        if (Number.isFinite(n) && !(parent && ts.isLiteralTypeNode(parent))) {
          const start = node.getStart(sf);
          const end = node.end;
          push([{ start, end, text: String(n + 1) }], "intconst", `${node.text} -> ${n + 1}`);
          if (n > 0) {
            push([{ start, end, text: String(n - 1) }], "intconst", `${node.text} -> ${n - 1}`);
          }
        }
      } else if (ts.isConditionalExpression(node)) {
        const tText = sf.text.slice(node.whenTrue.getStart(sf), node.whenTrue.end);
        const fText = sf.text.slice(node.whenFalse.getStart(sf), node.whenFalse.end);
        push(
          [
            { start: node.whenTrue.getStart(sf), end: node.whenTrue.end, text: fText },
            { start: node.whenFalse.getStart(sf), end: node.whenFalse.end, text: tText },
          ],
          "ifexp",
          "swap ternary branches",
        );
      } else if (ts.isBreakStatement(node) && !node.label) {
        const start = node.getStart(sf);
        push([{ start, end: start + 5, text: "continue" }], "loopjump", "break -> continue");
      } else if (ts.isContinueStatement(node) && !node.label) {
        const start = node.getStart(sf);
        push([{ start, end: start + 8, text: "break" }], "loopjump", "continue -> break");
      }

      ts.forEachChild(node, visit);
    };
    visit(sf);
  }

  const manifest = {
    generatedAt: new Date().toISOString(),
    operatorSet: [
      "compare ==/!=/</<=/>/>=", "boolop &&/||/??", "not-drop", "binop +-*/&|",
      "boolconst true/false", "intconst +-1", "ifexp swap", "loopjump break/continue",
    ],
    fileCount: files.length,
    fileMeta,
    mutants,
  };
  writeFileSync(path.join(FRONTEND, "mutation", "manifest.json"), JSON.stringify(manifest));
  console.log(`files: ${files.length}`);
  const byOp = {};
  for (const m of mutants) byOp[m.op] = (byOp[m.op] ?? 0) + 1;
  console.log(`mutants: ${mutants.length}`);
  console.log(JSON.stringify(byOp, null, 2));
  const byDir = {};
  for (const m of mutants) {
    const d = m.file.split("/").slice(0, 2).join("/");
    byDir[d] = (byDir[d] ?? 0) + 1;
  }
  console.log(JSON.stringify(byDir, null, 2));
}

generate();
