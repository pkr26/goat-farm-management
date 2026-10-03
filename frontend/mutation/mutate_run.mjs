// In-memory mutations, exact-selection clean baselines, and immutable attempt identity.
import { appendFileSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { digest, hash, inputs, latestCompatible, readJSON, records, validateContext, validateEdits } from "./mutate_identity.mjs";

export const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export function createCampaign({ root = FRONTEND, full = false, filesOverride = null, timeoutMs = null } = {}) {
  const manifestPath = path.join(root, "mutation/manifest.json");
  const coveragePath = path.join(root, "mutation/coverage-map.json");
  const manifest = readJSON(manifestPath);
  const coverage = readJSON(coveragePath);
  const current = inputs(root);
  if (coverage.schema !== 2 || !coverage.complete || digest(current) !== digest(coverage.inputs)) throw new Error("Coverage is incomplete or stale; rebuild mutation coverage against current inputs");
  const policy = { version: 2, full, filesOverride, timeoutMs, rounds: [1, 8, 25], timeout: "inconclusive", kill: "assertion-only", node: process.version };
  const provenance = { manifestSha: hash(readFileSync(manifestPath)), coverageSha: hash(readFileSync(coveragePath)), inputs: current, policy };
  return { root, manifest, coverage, policy, provenance, id: digest(provenance), runId: randomUUID(), baselines: new Map() };
}
export function coveringTests(mutant, campaign) {
  const indices = campaign.coverage.files[mutant.file]?.[String(mutant.line)] ?? [];
  return [...new Set(indices.map((index) => campaign.coverage.testFiles[index]).filter(Boolean))];
}
export function guardMutant(mutantId, root = FRONTEND) {
  if (!mutantId) return;
  const manifest = readJSON(path.join(root, "mutation/manifest.json"));
  const mutant = manifest.mutants.find((item) => item.id === mutantId);
  if (!mutant) throw new Error("MUTATION_INVALID: mutant missing");
  const target = path.resolve(root, mutant.file);
  if (!target.startsWith(`${path.resolve(root, "src")}${path.sep}`)) throw new Error("MUTATION_INVALID: target outside source directory");
  const source = readFileSync(target, "utf8");
  if (hash(source) !== manifest.fileMeta[mutant.file]?.sha256) throw new Error("MUTATION_INVALID: source fingerprint changed");
  validateEdits(source, mutant);
  validateContext(source, mutant);
}
export function classifyVitest(code, receipt) {
  if (!receipt || !Array.isArray(receipt.tests) || receipt.reason !== "passed" && receipt.reason !== "failed") return "INFRA_ERROR";
  if (receipt.suiteErrors?.length || receipt.unhandledErrors?.length || receipt.tests.some((test) => Object.values(test.hooks ?? {}).some((state) => state !== "pass"))) return "INFRA_ERROR";
  const failures = receipt.tests.filter((test) => test.state === "fail");
  if (code === 0 && failures.length === 0 && receipt.tests.some((test) => test.state === "pass")) return "SURVIVED";
  if (code === 1 && failures.length > 0 && failures.every((test) => test.errors?.length > 0 && test.errors.every((error) => error.name === "AssertionError"))) return "KILLED";
  return "INFRA_ERROR";
}
export async function runVitest(mutantId, testFiles, timeoutMs, { root = FRONTEND, extraArgs = [] } = {}) {
  const started = Date.now();
  if (!testFiles.length) return { verdict: "INFRA_ERROR", ms: 0, out: "Empty selection refused" };
  try { guardMutant(mutantId, root); } catch (error) { return { verdict: "INVALID", ms: 0, out: String(error) }; }
  const directory = mkdtempSync(path.join(os.tmpdir(), "herdly-vitest-receipt-"));
  const receiptPath = path.join(directory, "receipt.json");
  try {
    return await new Promise((resolve) => {
      const args = [path.join(root, "node_modules/vitest/vitest.mjs"), "run", "--config", "vitest.mutation.config.ts", "--reporter", path.join(root, "mutation/vitest_receipt.mjs"), ...extraArgs, ...testFiles];
      const child = spawn(process.execPath, args, { cwd: root, env: { ...process.env, MUTANT_ID: mutantId ?? "", MUTATION_RECEIPT_PATH: receiptPath }, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"] });
      let out = "";
      let timedOut = false;
      const collect = (data) => { out = (out + data).slice(-200_000); };
      child.stdout.on("data", collect); child.stderr.on("data", collect);
      const timer = setTimeout(() => {
        timedOut = true;
        try { if (process.platform !== "win32") process.kill(-child.pid, "SIGKILL"); else child.kill("SIGKILL"); } catch { /* process already ended */ }
      }, timeoutMs);
      child.on("error", (error) => { clearTimeout(timer); resolve({ verdict: "INFRA_ERROR", ms: Date.now() - started, out: String(error), exitCode: null }); });
      child.on("close", (code) => {
        clearTimeout(timer);
        let receipt;
        try { receipt = readJSON(receiptPath); } catch { /* crashed before receipt */ }
        const verdict = timedOut ? "INCONCLUSIVE_TIMEOUT" : mutantId && out.includes("MUTATION_INVALID:") ? "INVALID" : classifyVitest(code, receipt);
        resolve({ verdict, ms: Date.now() - started, out: out.slice(-3000), exitCode: code, receipt });
      });
    });
  } finally { rmSync(directory, { recursive: true, force: true }); }
}
function sample(files, count) {
  if (files.length <= count) return files;
  return Array.from({ length: count }, (_, index) => files[Math.floor(index * files.length / count)]);
}
export async function judge(mutant, campaign, { filesOverride = campaign.policy.filesOverride, full = campaign.policy.full, run = runVitest } = {}) {
  const tests = filesOverride ? [...new Set(filesOverride)] : coveringTests(mutant, campaign);
  const result = { id: mutant.id, mutantDigest: digest(mutant), campaignId: campaign.id, provenance: campaign.provenance, runId: campaign.runId, attemptId: randomUUID(), statusPolicy: campaign.policy, totalTests: tests.length };
  try { guardMutant(mutant.id, campaign.root); } catch (error) { return { ...result, verdict: "INVALID", tests: 0, error: String(error) }; }
  if (digest(inputs(campaign.root)) !== digest(campaign.provenance.inputs)) return { ...result, verdict: "INFRA_ERROR", tests: 0, error: "Inputs changed during campaign" };
  if (!tests.length) return { ...result, verdict: "NO_COVERAGE", tests: 0, selectionMode: "complete" };
  const rounds = full || filesOverride ? [tests] : [...new Set([1, 8, 25].map((size) => Math.min(size, tests.length)))].map((size) => sample(tests, size));
  let ms = 0;
  let outcome;
  for (const files of rounds) {
    const timeout = campaign.policy.timeoutMs ?? 420_000 + 25_000 * files.length;
    const selectionSha = digest(files);
    const key = digest({ selectionSha, timeout });
    let baseline = campaign.baselines.get(key);
    if (!baseline) {
      baseline = await run(null, files, timeout, { root: campaign.root });
      if (baseline.verdict === "SURVIVED") campaign.baselines.set(key, baseline);
    }
    const complete = files.length === tests.length;
    const selectionMode = complete ? filesOverride ? "explicit" : "complete" : "sampled";
    if (baseline.verdict !== "SURVIVED") return { ...result, verdict: baseline.verdict === "INCONCLUSIVE_TIMEOUT" ? "INCONCLUSIVE_TIMEOUT" : "INFRA_ERROR", tests: files.length, selectionSha, selectionMode, baseline, error: "Clean exact-selection baseline did not pass" };
    const attempt = await run(mutant.id, files, timeout, { root: campaign.root });
    ms += baseline.ms + attempt.ms;
    outcome = { ...result, verdict: attempt.verdict, tests: files.length, selectionSha, selectionMode, baseline, ms, exitCode: attempt.exitCode, receipt: attempt.receipt, tail: attempt.out };
    if (digest(inputs(campaign.root)) !== digest(campaign.provenance.inputs)) return { ...outcome, verdict: "INFRA_ERROR", error: "Inputs changed during execution" };
    if (attempt.verdict !== "SURVIVED" || complete) return outcome;
  }
  return outcome;
}
export function loadDone(campaign, file = path.join(campaign.root, "mutation/results.jsonl")) {
  return new Set([...latestCompatible(records(file), campaign)].filter(([, row]) => ["KILLED", "SURVIVED", "INVALID"].includes(row.verdict)).map(([id]) => id));
}
export async function smoke(campaign, { run = runVitest, judgeRun = judge } = {}) {
  const checks = [];
  try { for (const mutant of campaign.manifest.mutants) guardMutant(mutant.id, campaign.root); checks.push(true); } catch { checks.push(false); }
  const target = campaign.manifest.mutants.find((mutant) => coveringTests(mutant, campaign).length > 0 && mutant.op === "compare");
  if (!target) checks.push(false);
  else {
    const files = coveringTests(target, campaign);
    checks.push((await run(null, files, 120_000, { root: campaign.root })).verdict === "SURVIVED");
    const first = await judgeRun(target, campaign, { full: true });
    const second = await judgeRun(target, campaign, { full: true });
    checks.push(first.verdict === "KILLED", second.verdict === "KILLED");
  }
  const success = checks.every(Boolean);
  console.log(JSON.stringify({ smoke: success ? "passed" : "failed", checks }));
  return success;
}
export async function main(args = process.argv.slice(2), { root = FRONTEND } = {}) {
  const value = (name) => { const index = args.indexOf(name); return index < 0 ? null : args[index + 1]; };
  const campaign = createCampaign({ root, full: args.includes("--full"), filesOverride: value("--files")?.split(",") ?? null });
  if (args.includes("--smoke")) { if (!await smoke(campaign)) process.exitCode = 1; return; }
  const only = value("--only")?.split(",");
  const done = args.includes("--redo") ? new Set() : loadDone(campaign);
  let queue = campaign.manifest.mutants.filter((mutant) => !done.has(mutant.id) && (!only || only.some((pattern) => mutant.id === pattern || mutant.file.includes(pattern) || mutant.op === pattern)));
  if (value("--sample")) queue = sample(queue, Number(value("--sample")));
  if (args.includes("--dry-run")) { console.log(JSON.stringify({ campaignId: campaign.id, targets: queue.map((mutant) => mutant.id), full: campaign.policy.full })); return; }
  const count = queue.length;
  const counts = {};
  await Promise.all(Array.from({ length: Math.min(Number(process.env.MUT_WORKERS ?? 2), queue.length) }, async () => {
    while (queue.length) {
      const mutant = queue.shift();
      let record;
      try { record = await judge(mutant, campaign); }
      catch (error) { record = { id: mutant.id, mutantDigest: digest(mutant), campaignId: campaign.id, provenance: campaign.provenance, runId: campaign.runId, attemptId: randomUUID(), verdict: "INFRA_ERROR", error: String(error) }; }
      appendFileSync(path.join(root, "mutation/results.jsonl"), `${JSON.stringify(record)}\n`);
      counts[record.verdict] = (counts[record.verdict] ?? 0) + 1;
    }
  }));
  console.log(JSON.stringify({ attempts: count, counts }));
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main().catch((error) => { console.error(error); process.exitCode = 1; });
