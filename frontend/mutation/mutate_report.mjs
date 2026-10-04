// Compatible latest-attempt folding. Historical percentages are not release evidence.
import path from "node:path";
import { pathToFileURL } from "node:url";
import { atomicJSON, digest, hash, inputs, latestCompatible, readMutationArtifact, records } from "./mutate_identity.mjs";
import { FRONTEND } from "./mutate_run.mjs";

export function summarize(campaign, rows) {
  const latest = latestCompatible(rows, campaign);
  const counts = {};
  let sampled = 0;
  let killed = 0;
  let survived = 0;
  for (const mutant of campaign.manifest.mutants) {
    const row = latest.get(mutant.id);
    const verdict = row?.verdict ?? "PENDING";
    counts[verdict] = (counts[verdict] ?? 0) + 1;
    if (row && row.selectionMode !== "complete") sampled++;
    if (row?.selectionMode === "complete" && row.baseline?.verdict === "SURVIVED") {
      if (verdict === "KILLED") killed++;
      if (verdict === "SURVIVED") survived++;
    }
  }
  return { campaignId: campaign.id, total: campaign.manifest.mutants.length, counts, sampled, scored: killed + survived, killed, survived, score: killed + survived ? killed / (killed + survived) : null, untrustedHistoricalRows: rows.filter((row) => row.campaignId !== campaign.id).length };
}
export function report({ root = FRONTEND, campaignId = null } = {}) {
  const manifestBytes = readMutationArtifact(root, "manifest.json");
  const manifest = JSON.parse(manifestBytes);
  const coverageBytes = readMutationArtifact(root, "coverage-map.json");
  const coverage = JSON.parse(coverageBytes);
  const manifestSha = hash(manifestBytes);
  const coverageSha = hash(coverageBytes);
  const current = inputs(root);
  const rows = records(path.join(root, "mutation/results.jsonl"));
  const eligible = rows.filter((row) => row.provenance && digest(row.provenance) === row.campaignId && row.provenance.manifestSha === manifestSha && row.provenance.coverageSha === coverageSha && digest(row.provenance.inputs) === digest(current) && coverage.schema === 2 && coverage.complete && digest(coverage.inputs) === digest(current) && (!campaignId || row.campaignId === campaignId));
  const selected = eligible.at(-1);
  const campaign = { id: selected?.campaignId ?? "unmeasured", manifest };
  return summarize(campaign, rows);
}
export function main(args = process.argv.slice(2)) {
  const index = args.indexOf("--campaign-id");
  const summary = report({ campaignId: index < 0 ? null : args[index + 1] });
  console.log(JSON.stringify(summary));
  if (!args.includes("--dry-run")) atomicJSON(path.join(FRONTEND, "mutation/measurement-current.json"), summary);
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
