// Per-test contributions replace previous coverage; publish only passing, stable inputs.
import { existsSync, mkdtempSync, readdirSync, realpathSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { atomicJSON, digest, inputs, readJSON } from "./mutate_identity.mjs";
import { FRONTEND, runVitest } from "./mutate_run.mjs";

export function listTestFiles(root) {
  const files = [];
  function walk(relative) {
    for (const entry of readdirSync(path.join(root, relative), { withFileTypes: true })) {
      const file = path.join(relative, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (/\.test\.(ts|tsx)$/.test(file)) files.push(file);
    }
  }
  walk("src"); return files.sort();
}
export function coverageContribution(raw, root) {
  const files = {};
  for (const [absolute, data] of Object.entries(raw)) {
    const relative = path.relative(realpathSync(root), existsSync(absolute) ? realpathSync(absolute) : absolute);
    if (!relative.startsWith("src/") || /\.test\./.test(relative) || relative.startsWith("src/test/") || relative.startsWith("src/api/generated/")) continue;
    const lines = new Set();
    for (const [statement, count] of Object.entries(data.s ?? {})) if (count > 0) {
      const location = data.statementMap[statement];
      if (location) for (let line = location.start.line; line <= location.end.line; line++) lines.add(line);
    }
    files[relative] = [...lines].sort((a, b) => a - b);
  }
  return files;
}
export function buildCoverageMap({ previous, currentInputs, testFiles, replacements }) {
  const sourceHashes = Object.fromEntries(Object.entries(currentInputs).filter(([file]) => file.startsWith("src/") && !/\.test\./.test(file)));
  const harnessHashes = Object.fromEntries(Object.entries(currentInputs).filter(([file]) => !file.startsWith("src/")));
  const compatible = previous?.schema === 2 && digest(previous.sourceHashes) === digest(sourceHashes) && digest(previous.harnessHashes) === digest(harnessHashes);
  const contributions = {};
  for (const testFile of testFiles) {
    const replacement = replacements[testFile];
    const old = compatible && previous.contributions?.[testFile];
    const files = replacement ?? (old?.testSha === currentInputs[testFile] ? old.files : null);
    if (files) contributions[testFile] = { testSha: currentInputs[testFile], files };
  }
  const files = {};
  testFiles.forEach((testFile, index) => {
    for (const [file, lines] of Object.entries(contributions[testFile]?.files ?? {})) {
      if (!sourceHashes[file]) continue;
      for (const line of lines) (files[file] ??= {})[line] = [...((files[file] ?? {})[line] ?? []), index];
    }
  });
  return { schema: 2, complete: testFiles.length > 0 && testFiles.every((testFile) => contributions[testFile]), generatedAt: new Date().toISOString(), inputs: currentInputs, sourceHashes, harnessHashes, testFiles, contributions, files };
}
export async function collectCoverage({ root = FRONTEND, only = null, run = runVitest, concurrency = Number(process.env.COVER_CONCURRENCY ?? 2) } = {}) {
  const mapPath = path.join(root, "mutation/coverage-map.json");
  const before = inputs(root);
  const testFiles = listTestFiles(root);
  const selected = only ? testFiles.filter((file) => only.some((pattern) => file.includes(pattern))) : testFiles;
  if (!selected.length) throw new Error("No coverage test files selected");
  const previous = only && existsSync(mapPath) ? readJSON(mapPath) : null;
  const scratch = mkdtempSync(path.join(os.tmpdir(), "herdly-frontend-coverage-"));
  const replacements = {};
  const queue = selected.slice();
  const failures = [];
  try {
    await Promise.all(Array.from({ length: Math.min(concurrency, queue.length) }, async () => {
      while (queue.length) {
        const file = queue.shift();
        const reportDirectory = path.join(scratch, String(testFiles.indexOf(file)));
        const result = await run(null, [file], 600_000, { root, extraArgs: ["--testTimeout=240000", "--hookTimeout=240000", "--coverage.enabled", "--coverage.reporter=json", `--coverage.reportsDirectory=${reportDirectory}`, "--coverage.include=src/**", "--coverage.exclude=src/**/*.test.*", "--coverage.exclude=src/test/**", "--coverage.exclude=src/api/generated/**"] });
        const report = path.join(reportDirectory, "coverage-final.json");
        if (result.verdict !== "SURVIVED" || !existsSync(report)) { failures.push({ file, verdict: result.verdict, tail: result.out }); continue; }
        replacements[file] = coverageContribution(readJSON(report), root);
      }
    }));
    if (failures.length) throw new Error(`Coverage failed; published map unchanged: ${JSON.stringify(failures)}`);
    if (digest(before) !== digest(inputs(root))) throw new Error("Inputs changed while collecting coverage; published map unchanged");
    const map = buildCoverageMap({ previous, currentInputs: before, testFiles, replacements });
    atomicJSON(mapPath, map);
    return map;
  } finally { rmSync(scratch, { recursive: true, force: true }); }
}
export async function main(args = process.argv.slice(2)) {
  const index = args.indexOf("--only");
  const only = index < 0 ? null : args[index + 1].split(",");
  if (args.includes("--dry-run")) { console.log(JSON.stringify({ selected: listTestFiles(FRONTEND).filter((file) => !only || only.some((pattern) => file.includes(pattern))) })); return; }
  const map = await collectCoverage({ only });
  console.log(JSON.stringify({ complete: map.complete, testFiles: map.testFiles.length, sourceFiles: Object.keys(map.files).length }));
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main().catch((error) => { console.error(error); process.exitCode = 1; });
