import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync, readdirSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import ts from "typescript";

export const hash = (value) => createHash("sha256").update(value).digest("hex");
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  return value;
}
export const digest = (value) => hash(JSON.stringify(canonical(value)));
export const readJSON = (file) => JSON.parse(readFileSync(file, "utf8"));
export function atomicJSON(file, value) {
  const temporary = `${file}.${randomUUID()}.tmp`;
  writeFileSync(temporary, JSON.stringify(value));
  renameSync(temporary, file);
}
export function inputs(root) {
  const files = {};
  function walk(directory) {
    if (!existsSync(path.join(root, directory))) return;
    for (const entry of readdirSync(path.join(root, directory), { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const relative = path.join(directory, entry.name);
      if (entry.isDirectory()) walk(relative);
      else if (entry.isFile() && (!directory.startsWith("mutation") || entry.name.endsWith(".mjs"))) files[relative] = hash(readFileSync(path.join(root, relative)));
    }
  }
  walk("src"); walk("mutation"); walk("patches");
  for (const file of ["pnpm-lock.yaml", "package.json", "vitest.mutation.config.ts", "vitest.setup.ts", "tsconfig.json"]) if (existsSync(path.join(root, file))) files[file] = hash(readFileSync(path.join(root, file)));
  for (const file of readdirSync(root).filter((file) => /^(?:vite|vitest).*\.config\.(?:ts|mjs|js)$/.test(file))) files[file] = hash(readFileSync(path.join(root, file)));
  return files;
}
export function validateEdits(source, mutant) {
  let previousEnd = 0;
  if (!Array.isArray(mutant.edits) || !mutant.edits.length) throw new Error("MUTATION_INVALID: missing edits");
  for (const edit of [...mutant.edits].sort((a, b) => a.start - b.start)) {
    if (!Number.isInteger(edit.start) || !Number.isInteger(edit.end) || edit.start < previousEnd || edit.start < 0 || edit.end < edit.start || edit.end > source.length || typeof edit.original !== "string" || typeof edit.text !== "string" || source.slice(edit.start, edit.end) !== edit.original) throw new Error("MUTATION_INVALID: stale or overlapping original edit span");
    previousEnd = edit.end;
  }
}
export function validateContext(source, mutant) {
  let changed = source;
  for (const edit of [...mutant.edits].sort((a, b) => b.start - a.start)) changed = changed.slice(0, edit.start) + edit.text + changed.slice(edit.end);
  const options = { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.Preserve, noResolve: true, noLib: true };
  const sourceFile = ts.createSourceFile(mutant.file, changed, options.target, true, mutant.file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const host = ts.createCompilerHost(options);
  host.getSourceFile = (file) => file === mutant.file ? sourceFile : undefined;
  const program = ts.createProgram([mutant.file], options, host);
  const contextCodes = new Set([1104, 1105, 1107, 1163, 1308, 1359]);
  const errors = [...program.getSyntacticDiagnostics(sourceFile), ...program.getSemanticDiagnostics(sourceFile).filter((diagnostic) => contextCodes.has(diagnostic.code))];
  if (errors.length) throw new Error(`MUTATION_INVALID: full-file syntax/context: ${errors.map((error) => error.code).join(",")}`);
}
export function records(file) {
  if (!existsSync(file)) return [];
  return readFileSync(file, "utf8").split("\n").flatMap((line) => {
    try { const item = JSON.parse(line); return typeof item.id === "string" && typeof item.verdict === "string" ? [item] : []; } catch { return []; }
  });
}
export function latestCompatible(rows, campaign) {
  const mutants = new Map(campaign.manifest.mutants.map((mutant) => [mutant.id, digest(mutant)]));
  const latest = new Map();
  for (const row of rows) if (row.campaignId === campaign.id && row.mutantDigest === mutants.get(row.id) && row.provenance && digest(row.provenance) === campaign.id) latest.set(row.id, row);
  return latest;
}
