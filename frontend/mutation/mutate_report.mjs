// Report generator for the frontend mutation campaign.
//
// Reads manifest.json + results.jsonl (+ coverage-map.json) and writes
// mutation/report.md (per-module table, per-operator scores, every survivor
// with its diff) and mutation/survivors.json (machine-readable, for the
// fix-every-survivor analysis pass).

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const manifest = JSON.parse(readFileSync(path.join(FRONTEND, "mutation", "manifest.json"), "utf8"));
const results = new Map();
if (existsSync(path.join(FRONTEND, "mutation", "results.jsonl"))) {
  for (const line of readFileSync(path.join(FRONTEND, "mutation", "results.jsonl"), "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      const r = JSON.parse(line);
      results.set(r.id, r);
    } catch { /* ignore partial */ }
  }
}

const mutants = manifest.mutants;
const get = (id) => results.get(id);
const verdictOf = (m) => get(m.id)?.verdict ?? "PENDING";

const counts = {};
for (const m of mutants) counts[verdictOf(m)] = (counts[verdictOf(m)] ?? 0) + 1;

// --- headline -------------------------------------------------------------
const executed = ["KILLED", "SURVIVED", "TIMEOUT"].reduce((a, k) => a + (counts[k] ?? 0), 0);
const killed = (counts.KILLED ?? 0) + (counts.TIMEOUT ?? 0);
const survived = counts.SURVIVED ?? 0;
const score = killed + survived > 0 ? ((killed / (killed + survived)) * 100).toFixed(1) : "n/a";

// --- per-file table --------------------------------------------------------
const byFile = {};
for (const m of mutants) {
  const e = (byFile[m.file] ??= {});
  const v = verdictOf(m);
  e[v] = (e[v] ?? 0) + 1;
  e.total = (e.total ?? 0) + 1;
}

function fmtScore(e) {
  const k = (e.KILLED ?? 0) + (e.TIMEOUT ?? 0);
  const s = e.SURVIVED ?? 0;
  return k + s === 0 ? "—" : `${((k / (k + s)) * 100).toFixed(1)}%`;
}

const lines = [];
lines.push(`# Frontend Mutation Testing Report — ${new Date().toISOString().slice(0, 10)}`);
lines.push("");
lines.push(`Manifest: ${mutants.length} mutants over ${manifest.fileCount} files`);
lines.push(`(${manifest.operatorSet.join("; ")}).`);
lines.push("");
lines.push("## Headline");
lines.push("");
lines.push("| Metric | Value |");
lines.push("|---|---|");
lines.push(`| Mutants executed | ${executed} |`);
lines.push(`| Killed by tests | ${counts.KILLED ?? 0} |`);
lines.push(`| Killed by timeout | ${counts.TIMEOUT ?? 0} |`);
lines.push(`| **Survived** | **${survived}** |`);
lines.push(`| On lines no test covers | ${counts.NO_COVERAGE ?? 0} |`);
lines.push(`| Runner errors | ${counts.ERROR ?? 0} |`);
lines.push(`| Not yet run | ${counts.PENDING ?? 0} |`);
lines.push(`| **Mutation score (covered code)** | **${score}%** |`);
lines.push("");

// per-operator
const byOp = {};
for (const m of mutants) {
  const e = (byOp[m.op] ??= {});
  e[verdictOf(m)] = (e[verdictOf(m)] ?? 0) + 1;
}
lines.push("Per-operator scores: " +
  Object.entries(byOp)
    .map(([op, e]) => {
      const k = (e.KILLED ?? 0) + (e.TIMEOUT ?? 0);
      const s = e.SURVIVED ?? 0;
      const sc = k + s === 0 ? "n/a" : `${((k / (k + s)) * 100).toFixed(1)}%`;
      return `${op} ${sc}`;
    })
    .join(", "));
lines.push("");

// per-file (worst covered score first)
lines.push("## Per-file scores (covered mutants only)");
lines.push("");
lines.push("| File | Mutants | Killed | Survived | NoCov | Score |");
lines.push("|---|---|---|---|---|---|");
const fileRows = Object.entries(byFile)
  .map(([f, e]) => ({ f, e, score: fmtScore(e) }))
  .sort((a, b) => {
    const as = a.e.SURVIVED ?? 0, bs = b.e.SURVIVED ?? 0;
    const ar = parseFloat(a.score) ?? -1, br = parseFloat(b.score) ?? -1;
    if (ar !== br) return ar - br;
    return bs - as;
  });
for (const { f, e, score } of fileRows) {
  lines.push(`| ${f} | ${e.total} | ${(e.KILLED ?? 0) + (e.TIMEOUT ?? 0)} | ${e.SURVIVED ?? 0} | ${e.NO_COVERAGE ?? 0} | ${score} |`);
}
lines.push("");

// survivors with diffs
const sources = new Map();
function srcOf(rel) {
  if (!sources.has(rel)) sources.set(rel, readFileSync(path.join(FRONTEND, rel), "utf8").split("\n"));
  return sources.get(rel);
}
function diffOf(m) {
  const src = srcOf(m.file);
  // rebuild the mutated line(s) from the edits (single-line mutants only here)
  const off = m.edits[0].start;
  // find line index containing offset:
  let acc = 0, li = 0;
  while (li < src.length) {
    const len = src[li].length + 1;
    if (acc + len > off) break;
    acc += len;
    li++;
  }
  const lineText = src[li] ?? "";
  const orig = lineText.trim();
  // apply edits within this line (approximate display: replace first edit only)
  let mutated = lineText;
  for (const e of [...m.edits].sort((a, b) => b.start - a.start)) {
    mutated = mutated.slice(0, e.start - acc) + e.text + mutated.slice(e.end - acc);
  }
  return `L${m.line}: \`${orig.trim()}\` → \`${mutated.trim()}\``;
}

const survivors = mutants
  .filter((m) => verdictOf(m) === "SURVIVED")
  .map((m) => ({
    id: m.id,
    file: m.file,
    line: m.line,
    op: m.op,
    desc: m.desc,
    diff: diffOf(m),
    capped: get(m.id)?.capped ?? false,
    tests: get(m.id)?.totalTests ?? null,
  }));

writeFileSync(path.join(FRONTEND, "mutation", "survivors.json"), JSON.stringify(survivors, null, 2));

lines.push(`## Survivors (${survivors.length})`);
lines.push("");
const byFileSurv = {};
for (const s of survivors) (byFileSurv[s.file] ??= []).push(s);
for (const [f, ss] of Object.entries(byFileSurv)) {
  lines.push(`### ${f} (${ss.length})`);
  lines.push("");
  for (const s of ss) lines.push(`- \`${s.id}\` ${s.diff} (${s.op}: ${s.desc})${s.capped ? " ⚠capped-sample" : ""}`);
  lines.push("");
}

writeFileSync(path.join(FRONTEND, "mutation", "report.md"), lines.join("\n"));
console.log(lines.slice(0, 40).join("\n"));
console.log(`\nreport.md + survivors.json written; ${survivors.length} survivors`);
