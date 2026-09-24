// Survivor triage aid: groups survivors by file, prints each with source
// context (original line + the mutant's diff description) and covering-test
// stats, so genuine test gaps can be separated from equivalent mutants
// quickly during the fix pass.
//
// Usage: node mutation/mutate_triage.mjs [file-substr-filter]

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const filter = process.argv[2] ?? "";

const manifest = JSON.parse(readFileSync(path.join(FRONTEND, "mutation", "manifest.json"), "utf8"));
const covMap = JSON.parse(readFileSync(path.join(FRONTEND, "mutation", "coverage-map.json"), "utf8"));
const survivors = JSON.parse(readFileSync(path.join(FRONTEND, "mutation", "survivors.json"), "utf8"));

const sources = new Map();
function srcLines(rel) {
  if (!sources.has(rel)) sources.set(rel, readFileSync(path.join(FRONTEND, rel), "utf8").split("\n"));
  return sources.get(rel);
}

const groups = {};
for (const s of survivors) {
  if (filter && !s.file.includes(filter)) continue;
  (groups[s.file] ??= []).push(s);
}

for (const [file, ss] of Object.entries(groups).sort()) {
  console.log(`\n=== ${file} (${ss.length} survivors) ===`);
  const src = srcLines(file);
  for (const s of ss.sort((a, b) => a.line - b.line)) {
    const ctx = src
      .slice(Math.max(0, s.line - 2), Math.min(src.length, s.line + 1))
      .map((l, i) => `    ${String(Math.max(1, s.line - 1) + i).padStart(4)} | ${l}`)
      .join("\n");
    const per = covMap.files[file]?.[String(s.line)] ?? [];
    const names = per.map((i) => covMap.testFiles[i]).filter((t) => t.includes(path.basename(file, path.extname(file)).split(".")[0]));
    console.log(
      `\n  [${s.id}] L${s.line} ${s.op}: ${s.desc}${s.capped ? " ⚠capped" : ""} (dedicated covering: ${names.length ? names.join(", ") : "none"})\n${ctx}`,
    );
  }
}
console.log(`\ntotal survivors in view: ${Object.values(groups).reduce((a, g) => a + g.length, 0)}`);
