import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

// Do not suppress advisories in pnpm configuration. One upstream advisory
// has no release fix; it is accepted only with this exact reviewed patch and
// executable checks of the installed dependency. Every other advisory fails.
const result = spawnSync("pnpm", ["audit", "--json"], { encoding: "utf8" });
if (result.error) throw result.error;
if (result.status !== 0 && result.status !== 1) throw new Error("Dependency scan failed.");
let report;
try { report = JSON.parse(result.stdout); }
catch { throw new Error("Dependency scan did not return a valid report."); }
assert.ok(report.metadata?.vulnerabilities && report.advisories, "Incomplete dependency scan");
const findings = Object.values(report.advisories);
let locallyMitigated = 0;
for (const advisory of findings) {
  const accepted = advisory.module_name === "braces" &&
    advisory.url === "https://github.com/advisories/GHSA-vfj7-8cjw-p6xm" &&
    advisory.findings?.length > 0 && advisory.findings.every((item) => item.version === "3.0.3");
  if (!accepted) {
    console.error(`${advisory.severity}: ${advisory.module_name}: ${advisory.title} (${advisory.url})`);
    process.exitCode = 1;
    continue;
  }
  const config = JSON.parse(readFileSync(new URL("../package.json", import.meta.url)));
  assert.equal(config.pnpm?.patchedDependencies?.["braces@3.0.3"], "patches/braces@3.0.3.patch");
  const patch = readFileSync(new URL("../patches/braces@3.0.3.patch", import.meta.url));
  assert.equal(createHash("sha256").update(patch).digest("hex"),
    "8fab0322b9f1c3d7b38a26559a19954b1cada336e9d798542a99ba428579a78c");
  const check = spawnSync(process.execPath, ["scripts/verify-dependency-mitigations.mjs"], {
    encoding: "utf8", timeout: 10_000,
  });
  if (check.error) throw check.error;
  assert.equal(check.status, 0, check.stderr || "Local mitigation checks failed");
  console.log(check.stdout.trim());
  console.log(`Locally mitigated, upstream still unresolved: ${advisory.url}`);
  locallyMitigated++;
}
const total = Object.values(report.metadata.vulnerabilities).reduce((sum, value) => sum + value, 0);
assert.equal(total, findings.length, "Unrecognized dependency report shape");
console.log(`Dependency scan: ${findings.length - locallyMitigated} unmitigated, ${locallyMitigated} locally mitigated.`);
