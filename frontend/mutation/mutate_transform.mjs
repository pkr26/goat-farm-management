// Apply exactly one verified edit in memory; application files are never written.
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { hash, validateEdits } from "./mutate_identity.mjs";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export function mutationTransformPlugin({ root = FRONTEND } = {}) {
  const id = process.env.MUTANT_ID;
  let mutant;
  let fingerprint;
  if (id) {
    const manifestBytes = readFileSync(path.join(root, "mutation/manifest.json"));
    if (process.env.MUTATION_MANIFEST_SHA && hash(manifestBytes) !== process.env.MUTATION_MANIFEST_SHA) throw new Error("MUTATION_INVALID: manifest fingerprint changed");
    const manifest = JSON.parse(manifestBytes);
    mutant = manifest.mutants.find((item) => item.id === id);
    fingerprint = mutant && manifest.fileMeta[mutant.file]?.sha256;
    if (!mutant || !fingerprint) throw new Error(`MUTATION_INVALID: unknown mutant ${id}`);
  }
  return {
    name: "herdly-mutation-transform",
    enforce: "pre",
    transform(code, id) {
      if (!mutant || id.split("?")[0] !== path.resolve(root, mutant.file)) return null;
      if (hash(code) !== fingerprint) throw new Error("MUTATION_INVALID: source fingerprint changed");
      validateEdits(code, mutant);
      let out = code;
      for (const edit of [...mutant.edits].sort((a, b) => b.start - a.start)) out = out.slice(0, edit.start) + edit.text + out.slice(edit.end);
      return { code: out, map: null };
    },
  };
}
