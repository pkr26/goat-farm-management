// Phase B re-verification (frontend): re-run every recorded SURVIVED (or
// TIMEOUT) mutant against its COMPLETE covering selection — no spread
// sampling, dedicated files first — plus any extra files passed via
// --extra-files (the gap-test files written after the coverage map froze).
// Mirrors backend mutation/mutate_reverify.py.
//
// Usage:
//   node mutation/mutate_reverify.mjs [--status SURVIVED] [--workers 6]
//        [--extra-files f1,f2,...] [--cap 400] [--write]
//
// Without --write it only prints the plan; with --write it APPENDS new
// verdict records to results.jsonl (id + verdict) — the report generator
// keeps the LAST record per id, so re-verified verdicts supersede.

import { appendFileSync, existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { runVitest } from "./mutate_run.mjs";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RESULTS = path.join(FRONTEND, "mutation", "results.jsonl");

const args = process.argv.slice(2);
const flag = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i !== -1 && args[i + 1] ? args[i + 1] : fallback;
};
const STATUS = flag("--status", "SURVIVED");
const WORKERS = Number(flag("--workers", 6));
const CAP = Number(flag("--cap", 400));
const PREFIX = flag("--file-prefix", "");
const EXTRA = flag("--extra-files", "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);
const WRITE = args.includes("--write");

const manifest = JSON.parse(readFileSync(path.join(FRONTEND, "mutation", "manifest.json"), "utf8"));
const covMap = JSON.parse(readFileSync(path.join(FRONTEND, "mutation", "coverage-map.json"), "utf8"));

const results = new Map();
if (existsSync(RESULTS)) {
  for (const line of readFileSync(RESULTS, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      results.set(JSON.parse(line).id, JSON.parse(line));
    } catch {
      /* partial line */
    }
  }
}

function coveringTests(mutant) {
  const perFile = covMap.files[mutant.file];
  if (!perFile) return [];
  const idxs = perFile[String(mutant.line)];
  return idxs ? idxs.map((i) => covMap.testFiles[i]) : [];
}

function dedicatedTestsOf(mutant, covering) {
  const dir = path.dirname(mutant.file);
  const stem = path.basename(mutant.file).replace(/\.(ts|tsx)$/, "");
  const inDir = covering.filter((t) => t.startsWith(dir + "/"));
  const exact = inDir.filter((t) => new RegExp(`/${stem}\\.test\\.(ts|tsx)$`).test(t));
  const stemMatch = inDir.filter(
    (t) => t.includes(`/${stem}.`) && /\.test\.tsx?$/.test(t) && !exact.includes(t),
  );
  return [...new Set([...exact, ...stemMatch])];
}

function spreadSample(indices, n) {
  if (indices.length <= n) return indices;
  const step = indices.length / n;
  const out = [];
  for (let i = 0; i < n; i++) out.push(indices[Math.floor(i * step)]);
  return [...new Set(out)];
}

async function main() {
  const targets = manifest.mutants.filter(
    (m) =>
      results.get(m.id)?.verdict === STATUS &&
      (!PREFIX || m.file.startsWith(PREFIX)),
  );
  console.log(
    `${STATUS} mutants to re-verify: ${targets.length}` +
      (PREFIX ? ` (file prefix ${PREFIX})` : "") +
      (EXTRA.length ? ` (+${EXTRA.length} extra gap files forced into every run)` : ""),
  );
  if (!WRITE) {
    for (const m of targets.slice(0, 10)) {
      const cov = coveringTests(m);
      console.log(`  ${m.id} ${m.file}:${m.line} covering=${cov.length}`);
    }
    if (targets.length > 10) console.log(`  … and ${targets.length - 10} more (dry run, pass --write)`);
    return;
  }

  const queue = targets.slice();
  const counts = {};
  const t0 = Date.now();
  let done = 0;

  const write = (rec) => {
    counts[rec.verdict] = (counts[rec.verdict] ?? 0) + 1;
    appendFileSync(RESULTS, JSON.stringify(rec) + "\n");
    done += 1;
    if (done % 25 === 0 || done === queue.length) {
      const rate = done / ((Date.now() - t0) / 1000);
      console.log(
        `[${done}/${queue.length}] ${rate.toFixed(2)}/s eta ${(((queue.length - done) / rate) / 60).toFixed(1)}min ${JSON.stringify(counts)}`,
      );
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(WORKERS, queue.length) }, async () => {
      while (queue.length) {
        const m = queue.shift();
        if (!m) break;
        const cov = coveringTests(m);
        if (cov.length === 0) {
          write({ id: m.id, verdict: "NO_COVERAGE", tests: 0, reverified: true });
          continue;
        }
        // Full selection while practical; for ubiquitously-imported modules
        // (api-client: 200+ covering files) a literal full run costs hours
        // per mutant, so re-verify against EVERY DEDICATED file — the
        // module's own suite, the strongest per-module selection that stays
        // computationally sane. Incidental cross-module kills are not hunted.
        const selection = cov.length > 30 ? dedicatedTestsOf(m, cov) : cov;
        if (selection.length === 0) continue;
        const files = [...new Set([...selection, ...EXTRA])];
        const budget = 420_000 + 25_000 * Math.min(files.length, 40);
        let res = await runVitest(m.id, files, budget);
        if (res.verdict === "ERROR") res = await runVitest(m.id, files, budget);
        write({
          id: m.id,
          verdict: res.verdict,
          tests: files.length,
          totalTests: cov.length,
          ms: res.ms,
          reverified: true,
          capped: cov.length > 30,
          exitCode: res.exitCode ?? null,
        });
      }
    }),
  );
  console.log("re-verification complete:", JSON.stringify(counts));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
