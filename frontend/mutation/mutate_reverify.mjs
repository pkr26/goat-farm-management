// Reverify current compatible attempts with every coverer and optional gap tests.
import { appendFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { latestCompatible, records } from "./mutate_identity.mjs";
import { createCampaign, coveringTests, FRONTEND, judge } from "./mutate_run.mjs";

export async function main(args = process.argv.slice(2), { root = FRONTEND } = {}) {
  const flag = (name, fallback) => { const index = args.indexOf(name); return index < 0 ? fallback : args[index + 1]; };
  const extra = flag("--extra-files", "").split(",").filter(Boolean);
  // Source/test/harness identity is the same; original execution policy may differ.
  const original = createCampaign({ root });
  const rows = records(path.join(root, "mutation/results.jsonl"));
  const initialRows = latestCompatible(rows, original);
  const fullRows = latestCompatible(rows, createCampaign({ root, full: true }));
  const previous = new Map();
  for (const row of rows) if (initialRows.get(row.id) === row || fullRows.get(row.id) === row) previous.set(row.id, row);
  const targets = original.manifest.mutants.filter((mutant) => previous.get(mutant.id)?.verdict === flag("--status", "SURVIVED") && mutant.file.startsWith(flag("--file-prefix", "")));
  console.log(JSON.stringify({ targets: targets.map((mutant) => mutant.id), full: true, extra }));
  if (!args.includes("--write") || args.includes("--dry-run")) return;
  const campaign = createCampaign({ root, full: true });
  const queue = targets.slice();
  await Promise.all(Array.from({ length: Math.min(Number(flag("--workers", 2)), queue.length) }, async () => {
    while (queue.length) {
      const mutant = queue.shift();
      const filesOverride = extra.length ? [...new Set([...coveringTests(mutant, campaign), ...extra])] : null;
      const record = await judge(mutant, campaign, { full: true, filesOverride });
      appendFileSync(path.join(root, "mutation/results.jsonl"), `${JSON.stringify({ ...record, reverified: true })}\n`);
    }
  }));
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main().catch((error) => { console.error(error); process.exitCode = 1; });
