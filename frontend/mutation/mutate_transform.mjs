// Vite transform plugin that applies ONE mutant (selected via env MUTANT_ID)
// in-memory during the vitest run. No source file is ever written, so any
// number of vitest processes can run mutants in parallel on the same tree.
//
// The plugin runs with enforce: "pre" (before esbuild/babel), doing a plain
// slice-replace of the mutant's byte edits on the ORIGINAL source text —
// offsets were computed against that exact text at generation time, so no
// re-parsing is needed. A sha guard in the runner (not here) verifies the
// file is unchanged since generation.

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

let mutant = null; // {file, edits}
let loaded = false;

function load() {
  loaded = true;
  const id = process.env.MUTANT_ID;
  if (!id) return;
  const manifest = JSON.parse(readFileSync(path.join(FRONTEND, "mutation", "manifest.json"), "utf8"));
  const m = manifest.mutants.find((x) => x.id === id);
  if (!m) {
    throw new Error(`MUTANT_ID=${id} not found in manifest`);
  }
  mutant = { file: path.join(FRONTEND, m.file), edits: m.edits, rel: m.file };
}

export function mutationTransformPlugin() {
  return {
    name: "herdly-mutation-transform",
    enforce: "pre",
    transform(code, id) {
      if (!loaded) load();
      if (!mutant) return null;
      const abs = id.split("?")[0];
      if (abs !== mutant.file) return null;
      // Edits are non-overlapping; apply descending so offsets stay valid.
      let out = code;
      for (const e of [...mutant.edits].sort((a, b) => b.start - a.start)) {
        out = out.slice(0, e.start) + e.text + out.slice(e.end);
      }
      return { code: out, map: null };
    },
  };
}
