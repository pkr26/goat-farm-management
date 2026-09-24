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

  const files = {}; // rel src path -> { line -> Set(testIdx) }
  let done = 0;
  let failures = 0;

  const runOne = async (idx, tf) => {
    const reportDir = path.join(scratch, `c${idx}`);
    const args = [
      path.join(FRONTEND, "node_modules", "vitest", "vitest.mjs"),
      "run",
      "--config",
      "vitest.mutation.config.ts",
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
      const killer = setTimeout(() => p.kill("SIGKILL"), 240_000);
      p.on("close", (code) => {
        clearTimeout(killer);
        resolve({ code, out });
      });
    });
    done += 1;
    if (done % 25 === 0) console.log(`  ${done}/${testFiles.length}`);

    const jsonPath = path.join(reportDir, "coverage-final.json");
    if (res.code !== 0 || !existsSync(jsonPath)) {
      failures += 1;
      console.log(`  [WARN] ${tf} exit=${res.code} — excluded from map`);
      return;
    }
    const cov = JSON.parse(readFileSync(jsonPath, "utf8"));
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
          (entry[l] ??= new Set()).add(idx);
        }
      }
    }
    rmSync(reportDir, { recursive: true, force: true });
  };

  const queue = testFiles.map((tf, idx) => [idx, tf]);
  const workers = Array.from({ length: Math.min(CONCURRENCY, queue.length) }, async () => {
    while (queue.length) {
      const next = queue.shift();
      if (!next) break;
      await runOne(next[0], next[1]);
    }
  });
  await Promise.all(workers);

  const serialized = {};
  for (const [f, lines] of Object.entries(files)) {
    serialized[f] = {};
    for (const [l, set] of Object.entries(lines)) serialized[f][l] = [...set].sort((a, b) => a - b);
  }
  writeFileSync(
    path.join(FRONTEND, "mutation", "coverage-map.json"),
    JSON.stringify({ testFiles, files: serialized }),
  );
  rmSync(scratch, { recursive: true, force: true });
  const totalLines = Object.values(serialized).reduce((a, f) => a + Object.keys(f).length, 0);
  console.log(`map written: ${Object.keys(serialized).length} source files, ${totalLines} covered lines, ${failures} failing test runs`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
