// Builds the line -> covering-test-file map used to select tests per mutant.
//
// Vitest 4 has no per-test coverage, so we run each test FILE once with
// coverage (v8 provider, json reporter) and record which source lines that
// file's run executed. Output: mutation/coverage-map.json
//
//   { testFiles: ["src/lib/utils.test.ts", ...],
//     files: { "src/lib/utils.ts": { "<line>": [0, 4, 9], ... } } }
//
// line numbers are 1-based (matching manifest mutants). Lines not present
// were never executed by any test file.
//
// Runs are parallelised (default 6 concurrent single-file vitest processes);
// each writes its coverage JSON to a scratch dir which is parsed and deleted.

import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CONCURRENCY = Number(process.env.COVER_CONCURRENCY ?? 6);

function listTestFiles() {
  // Respect the base vitest include: src/**/*.test.{ts,tsx}
  // `vitest list` prints "<file> > <test name>" lines; keep the file part.
  const out = execFileSync(
    process.execPath,
    [
      path.join(FRONTEND, "node_modules", "vitest", "vitest.mjs"),
      "list",
      "--config",
      "vitest.mutation.config.ts",
    ],
    { cwd: FRONTEND, encoding: "utf8" },
  )
    .split("\n")
    .map((l) => l.trim().split(" > ")[0])
    .filter((l) => /\.test\.(ts|tsx)$/.test(l));
  return [...new Set(out)];
}

async function main() {
  const scratch = path.join(FRONTEND, "mutation", "cov-scratch");
  rmSync(scratch, { recursive: true, force: true });
  mkdirSync(scratch, { recursive: true });

  const testFiles = listTestFiles();
  console.log(`test files: ${testFiles.length}`);

  // --only <substr,substr>: (re)run just the named test files and MERGE the
  // result into an existing coverage-map.json (testFiles union, line sets
  // intersected per file so dropped coverage is preserved by other runs).
  const onlyIdx = process.argv.indexOf("--only");
  const onlyPats =
    onlyIdx !== -1 && process.argv[onlyIdx + 1]
      ? process.argv[onlyIdx + 1].split(",").map((s) => s.trim())
      : null;

  // line -> Set(testIdx) keyed by test FILE PATH when merging (index space of
  // the old map must not be reused); converted to indices at the end.
  const files = {}; // rel src path -> { line -> Set<string testFile> }
  const knownTestFiles = []; // final ordered union
  const existing = path.join(FRONTEND, "mutation", "coverage-map.json");
  if (onlyPats && existsSync(existing)) {
    const old = JSON.parse(readFileSync(existing, "utf8"));
    knownTestFiles.push(...old.testFiles);
    for (const [f, lines] of Object.entries(old.files)) {
      const entry = (files[f] ??= {});
      for (const [l, idxs] of Object.entries(lines)) {
        const set = (entry[l] ??= new Set());
        // NB: Set.add is single-argument — a spread call silently keeps only
        // the first coverer and corrupts every selection built from the map.
        for (const i of idxs) set.add(old.testFiles[i]);
      }
    }
  }
  const selected = onlyPats
    ? testFiles.filter((tf) => onlyPats.some((p) => tf.includes(p)))
    : testFiles;
  console.log(`running: ${selected.length} test files`);
  let done = 0;
  let failures = 0;

  const runOne = async (idx, tf) => {
    const reportDir = path.join(scratch, `c${idx}`);
    const args = [
      path.join(FRONTEND, "node_modules", "vitest", "vitest.mjs"),
      "run",
      "--config",
      "vitest.mutation.config.ts",
      "--testTimeout=240000",
      "--hookTimeout=240000",
      "--coverage.enabled",
      "--coverage.reporter=json",
      `--coverage.reportsDirectory=${reportDir}`,
      "--coverage.include=src/**",
      "--coverage.exclude=src/**/*.test.*",
      "--coverage.exclude=src/test/**",
      "--coverage.exclude=src/api/generated/**",
      "--coverage.all=false",
      tf,
    ];
    const res = await new Promise((resolve) => {
      const p = spawn(process.execPath, args, {
        cwd: FRONTEND,
        stdio: ["ignore", "pipe", "pipe"],
      });
      let out = "";
      p.stdout.on("data", (d) => (out += d));
      p.stderr.on("data", (d) => (out += d));
      const killer = setTimeout(() => p.kill("SIGKILL"), 600_000);
      p.on("close", (code) => {
        clearTimeout(killer);
        resolve({ code, out });
      });
    });
    done += 1;
    if (done % 25 === 0) console.log(`  ${done}/${selected.length}`);

    const jsonPath = path.join(reportDir, "coverage-final.json");
    if (res.code !== 0 || !existsSync(jsonPath)) {
      failures += 1;
      console.log(`  [WARN] ${tf} exit=${res.code} — excluded from map`);
      return;
    }
    const cov = JSON.parse(readFileSync(jsonPath, "utf8"));
    if (!knownTestFiles.includes(tf)) knownTestFiles.push(tf);
    for (const [absFile, data] of Object.entries(cov)) {
      const rel = path.relative(FRONTEND, absFile);
      if (!rel.startsWith("src/")) continue;
      const entry = (files[rel] ??= {});
      for (const [sid, count] of Object.entries(data.s ?? {})) {
        if (!count) continue;
        const loc = data.statementMap[sid];
        if (!loc) continue;
        // attribute the whole statement range line-wise (start line .. end line)
        for (let l = loc.start.line; l <= loc.end.line; l++) {
          (entry[l] ??= new Set()).add(tf);
        }
      }
    }
    rmSync(reportDir, { recursive: true, force: true });
  };

  const queue = selected.map((tf, idx) => [idx, tf]);
  const workers = Array.from({ length: Math.min(CONCURRENCY, queue.length) }, async () => {
    while (queue.length) {
      const next = queue.shift();
      if (!next) break;
      await runOne(next[0], next[1]);
    }
  });
  await Promise.all(workers);

  const index = new Map(knownTestFiles.map((tf, i) => [tf, i]));
  const serialized = {};
  for (const [f, lines] of Object.entries(files)) {
    serialized[f] = {};
    for (const [l, set] of Object.entries(lines)) {
      serialized[f][l] = [...set]
        .map((tf) => index.get(tf))
        .filter((i) => i !== undefined)
        .sort((a, b) => a - b);
    }
  }
  writeFileSync(
    path.join(FRONTEND, "mutation", "coverage-map.json"),
    JSON.stringify({ testFiles: knownTestFiles, files: serialized }),
  );
  rmSync(scratch, { recursive: true, force: true });
  const totalLines = Object.values(serialized).reduce((a, f) => a + Object.keys(f).length, 0);
  console.log(`map written: ${Object.keys(serialized).length} source files, ${totalLines} covered lines, ${failures} failing test runs`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
