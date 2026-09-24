// Parallel mutation campaign runner (frontend).
//
// For every mutant in mutation/manifest.json:
//   * look up the covering test files for its (file, line) in
//     mutation/coverage-map.json — no covering file => NO_COVERAGE
//   * spawn `vitest run --config vitest.mutation.config.ts <files>` with
//     env MUTANT_ID=<id>; the in-process transform applies the mutation
//   * exit 0 => SURVIVED, non-zero => KILLED, wall-clock kill => TIMEOUT
//     (counted killed, re-verifiable), infra failure => ERROR (retried once)
//
// Sets larger than SAMPLE (25) are first run as a spread sample; survivors
// escalate to the full set (capped at FULL_CAP, flagged `capped`). This
// mirrors the backend 15->60 escalating scheme.
//
// Resumable: results append to mutation/results.jsonl; done ids are skipped.
// `--only <substr,substr,...>` filters mutants (by id, file, or op).
// `--smoke` runs harness self-checks instead of the campaign.
// No file on disk is ever mutated (transform-in-memory), so N processes on
// one tree are safe; workers here are concurrent spawns inside one runner.

import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RESULTS = path.join(FRONTEND, "mutation", "results.jsonl");

const WORKERS = Number(process.env.MUT_WORKERS ?? 6);
// Escalating rounds (mirrors the backend 15->60 scheme): start with the
// single most-likely killer (the module's dedicated test file), then widen.
// The terminal round caps at FULL_CAP files; covering sets larger than that
// are flagged `capped` and verified later against the full set.
const ROUND_SIZES = [1, 8, 25];
const FULL_CAP = 25;

const manifest = JSON.parse(readFileSync(path.join(FRONTEND, "mutation", "manifest.json"), "utf8"));
const covMap = JSON.parse(readFileSync(path.join(FRONTEND, "mutation", "coverage-map.json"), "utf8"));

function sha(rel) {
  return createHash("sha256").update(readFileSync(path.join(FRONTEND, rel), "utf8"), "utf8").digest("hex");
}

function spreadSample(indices, n) {
  if (indices.length <= n) return indices;
  const step = indices.length / n;
  const out = [];
  for (let i = 0; i < n; i++) out.push(indices[Math.floor(i * step)]);
  return [...new Set(out)];
}

function coveringTests(mutant) {
  const perFile = covMap.files[mutant.file];
  if (!perFile) return [];
  const idxs = perFile[String(mutant.line)];
  return idxs ? idxs.map((i) => covMap.testFiles[i]) : [];
}

/** Test files dedicated to this source module (name-related), ordered by
 *  likely killing power: exact <stem>.test.tsx, then <stem>.mutation.test.*,
 * then the rest. */
function dedicatedTests(mutant, covering) {
  const dir = path.dirname(mutant.file);
  const stem = path.basename(mutant.file).replace(/\.(ts|tsx)$/, "");
  const inDir = covering.filter((t) => t.startsWith(dir + "/"));
  const exact = inDir.filter((t) =>
    new RegExp(`/${stem}\\.test\\.(ts|tsx)$`).test(t),
  );
  const mutation = inDir.filter((t) =>
    t.includes(`/${stem}.mutation.test.`) && !exact.includes(t),
  );
  const rest = inDir.filter((t) => !exact.includes(t) && !mutation.includes(t));
  return [...exact, ...mutation, ...rest];
}

function withDedicatedFirst(mutant, chosen, covering) {
  const ded = dedicatedTests(mutant, covering);
  const dedChosen = ded.filter((t) => chosen.includes(t));
  const rest = chosen.filter((t) => !dedChosen.includes(t));
  return [...dedChosen, ...rest];
}

function runVitest(mutantId, testFiles, timeoutMs) {
  const args = [
    path.join(FRONTEND, "node_modules", "vitest", "vitest.mjs"),
    "run",
    "--config",
    "vitest.mutation.config.ts",
    ...testFiles,
  ];
  return new Promise((resolve) => {
    const t0 = Date.now();
    const p = spawn(process.execPath, args, {
      cwd: FRONTEND,
      env: { ...process.env, MUTANT_ID: mutantId ?? "" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    p.stdout.on("data", (d) => {
      out += d;
      if (out.length > 200_000) out = out.slice(-100_000);
    });
    p.stderr.on("data", (d) => {
      out += d;
    });
    let timedOut = false;
    const killer = setTimeout(() => {
      timedOut = true;
      p.kill("SIGKILL");
    }, timeoutMs);
    p.on("close", (code) => {
      clearTimeout(killer);
      const ms = Date.now() - t0;
      if (timedOut) return resolve({ verdict: "TIMEOUT", ms, out: out.slice(-3000), exitCode: code });
      const noTests = /No test files found/i.test(out);
      if (noTests) return resolve({ verdict: "ERROR", ms, out: out.slice(-3000), exitCode: code });
      resolve({
        verdict: code === 0 ? "SURVIVED" : "KILLED",
        ms,
        out: out.slice(-3000),
        exitCode: code,
      });
    });
    p.on("error", (err) => {
      clearTimeout(killer);
      resolve({ verdict: "ERROR", ms: Date.now() - t0, out: String(err), exitCode: -1 });
    });
  });
}

async function judge(mutant, forceFull = false, filesOverride = null) {
  if (filesOverride) {
    let res = await runVitest(mutant.id, filesOverride, 240_000 + 20_000 * filesOverride.length);
    if (res.verdict === "ERROR") res = await runVitest(mutant.id, filesOverride, 240_000 + 20_000 * filesOverride.length);
    return {
      id: mutant.id,
      verdict: res.verdict,
      tests: filesOverride.length,
      totalTests: filesOverride.length,
      ms: res.ms,
      forced: true,
      exitCode: res.exitCode ?? null,
      tail: res.out?.slice(-1500),
    };
  }
  const tests = coveringTests(mutant);
  if (tests.length === 0) {
    return { id: mutant.id, verdict: "NO_COVERAGE", tests: 0, ms: 0, capped: false };
  }
  const budget = (tests) => 240_000 + 20_000 * tests.length;
  const run = async (files) => {
    let res = await runVitest(mutant.id, files, budget(files));
    if (res.verdict === "ERROR") res = await runVitest(mutant.id, files, budget(files));
    return res;
  };

  if (forceFull) {
    const full = tests.length > 60 ? spreadSample(tests, 60) : tests;
    const res = await run(full);
    return {
      id: mutant.id,
      verdict: res.verdict,
      tests: full.length,
      totalTests: tests.length,
      ms: res.ms,
      capped: tests.length > 60,
      exitCode: res.exitCode ?? null,
      tail: res.verdict === "SURVIVED" || res.verdict === "ERROR" ? res.out : undefined,
    };
  }

  const ded = dedicatedTests(mutant, tests);
  let ms = 0;
  let capped = tests.length > FULL_CAP;
  let lastFiles = [];
  // Small covering sets (typical page modules, <= FULL_CAP+few): a middle
  // 8-file round adds little — jump from the dedicated file to the whole set.
  const rounds =
    tests.length <= FULL_CAP + 8
      ? [1, Math.min(FULL_CAP, tests.length)]
      : ROUND_SIZES;
  for (let round = 0; round < rounds.length; round++) {
    const size = rounds[round];
    // Round 1: best dedicated file alone. Later rounds: spread over the
    // whole covering set with dedicated files kept at the front.
    let files;
    if (round === 0) {
      // A module with no dedicated tests (e.g. one extracted next to its
      // consumers) must still run ONE covering file — an empty file list
      // would make vitest run the ENTIRE suite.
      files = ded.length ? ded.slice(0, 1) : tests.slice(0, 1);
    } else {
      files = withDedicatedFirst(
        mutant,
        spreadSample(tests, size),
        tests,
      );
    }
    lastFiles = files;
    const res = await run(files);
    ms += res.ms;
    if (res.verdict === "KILLED" || res.verdict === "TIMEOUT") {
      return {
        id: mutant.id,
        verdict: res.verdict,
        tests: files.length,
        totalTests: tests.length,
        ms,
        round: round + 1,
        capped,
        exitCode: res.exitCode ?? null,
      };
    }
    if (res.verdict === "ERROR") {
      return {
        id: mutant.id,
        verdict: "ERROR",
        tests: files.length,
        totalTests: tests.length,
        ms,
        exitCode: res.exitCode ?? null,
        tail: res.out,
      };
    }
    // SURVIVED: definitive if we just ran the entire covering set
    if (size >= tests.length) {
      return {
        id: mutant.id,
        verdict: "SURVIVED",
        tests: files.length,
        totalTests: tests.length,
        ms,
        round: round + 1,
        capped: false,
        exitCode: res.exitCode ?? null,
      };
    }
  }
  return {
    id: mutant.id,
    verdict: "SURVIVED",
    tests: lastFiles.length,
    totalTests: tests.length,
    ms,
    round: ROUND_SIZES.length,
    capped,
    exitCode: 0,
  };
}

function loadDone() {
  const done = new Set();
  if (existsSync(RESULTS)) {
    for (const line of readFileSync(RESULTS, "utf8").split("\n")) {
      if (!line.trim()) continue;
      try {
        done.add(JSON.parse(line).id);
      } catch {
        /* partial line from a killed run — ignore */
      }
    }
  }
  return done;
}

async function smoke() {
  // 1. All target files unchanged since generation.
  let drift = 0;
  for (const [rel, meta] of Object.entries(manifest.fileMeta)) {
    if (sha(rel) !== meta.sha256) {
      console.log(`[SMOKE-FAIL] file drifted since generation: ${rel}`);
      drift++;
    }
  }
  console.log(drift === 0 ? "[SMOKE-OK] no source drift vs manifest" : "[SMOKE-FAIL] drift above");

  // 2. Unmutated run of one lib test file passes (config sanity).
  const base = await runVitest(null, ["src/lib/utils.test.ts"], 120_000);
  console.log(base.verdict === "KILLED" ? "[SMOKE-FAIL] baseline run failed:\n" + base.out : "[SMOKE-OK] unmutated run passes");

  // 3. A utils.ts compare mutant is applied and killed (transform sanity).
  const target = manifest.mutants.find(
    (m) => m.file === "src/lib/utils.ts" && m.op === "compare" && coveringTests(m).length > 0,
  );
  if (!target) {
    console.log("[SMOKE-FAIL] no covered utils.ts compare mutant found");
  } else {
    const r1 = await judge(target, true);
    console.log(
      r1.verdict === "KILLED"
        ? `[SMOKE-OK] mutant ${target.id} (${target.desc}) killed — transform applies`
        : `[SMOKE-FAIL] expected kill, got ${r1.verdict}: ${r1.tail ?? ""}`,
    );
    const r2 = await judge(target, true);
    console.log(
      r2.verdict === r1.verdict
        ? "[SMOKE-OK] deterministic on repeat"
        : `[SMOKE-FAIL] nondeterministic verdict ${r1.verdict} -> ${r2.verdict}`,
    );
  }

  // 4. Two DIFFERENT mutants of the same file yield (possibly) different
  //    verdicts — proves no stale transform cache between runs.
  const t2 = manifest.mutants.find(
    (m) => m.file === "src/lib/utils.ts" && m.op !== target?.op && coveringTests(m).length > 0,
  );
  if (t2) {
    const r3 = await judge(t2, true);
    console.log(`[SMOKE-INFO] second mutant ${t2.id} (${t2.desc}) -> ${r3.verdict} (cross-run cache check manual)`);
  }
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes("--smoke")) return smoke();

  // Verify all shas before touching anything.
  for (const [rel, meta] of Object.entries(manifest.fileMeta)) {
    if (sha(rel) !== meta.sha256) {
      console.error(`source drifted vs manifest: ${rel} — regenerate manifest + coverage map first`);
      process.exit(2);
    }
  }

  let mutants = manifest.mutants;
  const onlyIdx = args.indexOf("--only");
  if (onlyIdx !== -1 && args[onlyIdx + 1]) {
    const pats = args[onlyIdx + 1].split(",").map((s) => s.trim());
    mutants = mutants.filter((m) => pats.some((p) => m.id === p || m.file.includes(p) || m.op === p));
  }
  const forceFull = args.includes("--full");
  // --files f1,f2: run these test files directly instead of map selection —
  // used when NEW tests (absent from the coverage map) should judge a mutant.
  const filesIdx = args.indexOf("--files");
  const filesOverride =
    filesIdx !== -1 && args[filesIdx + 1] && !args[filesIdx + 1].startsWith("--")
      ? args[filesIdx + 1].split(",")
      : null;

  const done = args.includes("--redo") ? new Set() : loadDone();
  let queue;
  let sampledIds = null;
  const sampleIdx = args.indexOf("--sample");
  if (sampleIdx !== -1 && args[sampleIdx + 1]) {
    // stratified sizing sample: round-robin across the manifest order
    const n = Number(args[sampleIdx + 1]);
    const step = Math.max(1, Math.floor(mutants.length / n));
    const sel = [];
    for (let i = 0; i < mutants.length && sel.length < n; i += step) sel.push(mutants[i]);
    queue = sel.filter((m) => !done.has(m.id));
    sampledIds = new Set(queue.map((m) => m.id));
    console.log(`sizing sample: ${queue.length} mutants`);
  } else {
    queue = mutants.filter((m) => !done.has(m.id));
    // lib/components/hooks/api before the big src/app surface: same total
    // work, but the logic-dense modules (where fixable survivors concentrate
    // and rounds are cheap) finish first for early analysis.
    const prio = (m) => (m.file.startsWith("src/app/") ? 1 : 0);
    queue.sort((a, b) => prio(a) - prio(b));
  }
  console.log(
    `mutants: ${mutants.length} total, ${mutants.length - queue.length} already done/skipped, running ${queue.length} with ${WORKERS} workers`,
  );

  const counts = {};
  const roundCounts = {};
  const t0 = Date.now();
  let completed = 0;

  const write = (rec) => {
    counts[rec.verdict] = (counts[rec.verdict] ?? 0) + 1;
    if (rec.round) roundCounts[rec.round] = (roundCounts[rec.round] ?? 0) + 1;
    appendFileSync(RESULTS, JSON.stringify(rec) + "\n");
    completed++;
    const every = sampledIds ? 1 : 25;
    if (completed % every === 0 || completed === queue.length) {
      const rate = completed / ((Date.now() - t0) / 1000);
      const eta = ((queue.length - completed) / rate / 60).toFixed(1);
      console.log(
        `[${completed}/${queue.length}] ${rate.toFixed(2)}/s eta ${eta}min ${JSON.stringify(counts)} rounds=${JSON.stringify(roundCounts)}`,
      );
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(WORKERS, queue.length) }, async () => {
      while (queue.length) {
        const m = queue.shift();
        if (!m) break;
        try {
          write(await judge(m, forceFull, filesOverride));
        } catch (e) {
          write({ id: m.id, verdict: "ERROR", error: String(e) });
        }
      }
    }),
  );
  console.log("campaign complete:", JSON.stringify(counts));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
