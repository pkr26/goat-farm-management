import assert from "node:assert/strict";
import { createRequire } from "node:module";

// Resolve the actual build-tool dependency chain, so a missing lockfile patch
// cannot be hidden by checking a different braces installation.
let requireFrom = createRequire(import.meta.url);
let bracesPath;
for (const dependency of ["eslint-config-next", "@next/eslint-plugin-next", "fast-glob", "micromatch", "braces"]) {
  bracesPath = requireFrom.resolve(dependency);
  requireFrom = createRequire(bracesPath);
}
const braces = requireFrom(bracesPath);
const rejectsDepth = (action) => assert.throws(action, (error) =>
  error instanceof SyntaxError && /supported depth of 128/.test(error.message));

for (const [open, close] of [["{", "}"], ["(", ")"]]) {
  const nested = open.repeat(4_000) + "a" + close.repeat(4_000);
  for (const action of [braces.parse, braces.compile, braces.expand, braces.stringify]) {
    rejectsDepth(() => action(nested));
    rejectsDepth(() => action(open.repeat(4_000) + "a"));
  }
}
// Exported walkers also accept ASTs. Their own guard must hold even when an
// upstream caller bypasses the parser.
let ast = { type: "text", value: "a" };
for (let depth = 0; depth < 200; depth++) {
  const parent = { type: "brace", nodes: [ast], ranges: 0 };
  ast.parent = parent;
  ast = parent;
}
ast.type = "root";
for (const action of [braces.compile, braces.expand, braces.stringify]) {
  rejectsDepth(() => action(ast));
}
assert.deepEqual(braces.expand("src/{app,lib}/**/*.{ts,tsx}"), [
  "src/app/**/*.ts", "src/app/**/*.tsx", "src/lib/**/*.ts", "src/lib/**/*.tsx",
]);
assert.deepEqual(braces.expand("item{1..3}"), ["item1", "item2", "item3"]);
assert.match(braces.compile("file.{js,ts}"), /js\|ts/);
assert.equal(braces.stringify(braces.parse("literal\\{brace\\}")), "literal{brace}");
console.log("Patched braces: 19 depth-guard checks and 4 ordinary-pattern checks passed.");
