/**
 * Scoped vitest config used ONLY by the mutation-testing harness
 * (mutation/mutate_cover.mjs + mutation/mutate_run.mjs).
 *
 * The mutation itself happens in-memory via mutation/mutate_transform.mjs
 * (env MUTANT_ID selects the mutant; see that file). Test selection is made
 * by the runner via CLI file arguments, so `include` stays the base suite's.
 *
 * NB: deliberately NOT mergeConfig — that concatenates array options (the
 * base include plus ours); this file repeats the base settings instead.
 * Not used by `pnpm test`.
 */
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import { mutationTransformPlugin } from "./mutation/mutate_transform.mjs";

export default defineConfig({
  plugins: [mutationTransformPlugin(), react()],
  // Per-process scratch cache: several mutated vitest processes run in
  // parallel and must neither share nor reuse each other's caches.
  cacheDir: path.join(tmpdir(), `vitest-mut-${process.pid}`),
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    globals: true,
    // The runner parallelises by spawning processes; keep each lean.
    maxWorkers: 1,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Some single-file runs still bundle sizeable page suites; give them
    // room beyond the 5 s local budget without unbounded waits. The runner
    // enforces its own wall-clock timeout on top.
    testTimeout: process.env.CI ? 20_000 : 15_000,
    hookTimeout: process.env.CI ? 30_000 : 20_000,
    coverage: { enabled: false },
  },
});
