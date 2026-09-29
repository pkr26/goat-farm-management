import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    // fileURLToPath (not .pathname) so paths with spaces/percent-encodable
    // characters resolve to the real filesystem location.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    globals: true,
    // A high-core workstation otherwise launches enough independent jsdom
    // processes to thrash memory and make unrelated 5 s interaction tests
    // fail nondeterministically. Two workers retain useful parallelism while
    // keeping the full release gate stable on both laptops and CI runners.
    maxWorkers: 2,
    // CI runners are 2-core shared machines: interaction tests that finish
    // well inside 5 s locally can exceed it under coverage instrumentation
    // there, and a timing failure is indistinguishable from a real
    // regression in the report. Keep the tight local budget (it catches
    // accidental sleeps and missing awaits) but give CI the headroom.
    testTimeout: process.env.CI ? 20_000 : 5_000,
    hookTimeout: process.env.CI ? 30_000 : 10_000,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "html"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/api/generated/**", "src/test/**"],
      thresholds: {
        // Global floors sit a few points under the measured totals on main
        // (2026-09-22: 93.7/92.5/93.6/95.4) so ordinary fluctuation never
        // fails CI while a real regression still trips them (B7 sanity-check,
        // same honesty fix as the backend fail_under).
        statements: 90,
        branches: 87,
        functions: 90,
        lines: 90,
        // Per-glob floors (B8, 2026-09-21 audit): the global averages
        // previously hid an entire page at 0% — the screening review queue.
        // Each surface now carries its own floor, well under its measured
        // level (app/(app) ~97/96, components ~82/83, lib ~97/97) but far
        // above zero, so no page can ever again ship untested. The route
        // group's parentheses must be escaped: picomatch parses bare
        // `(app)` as an extglob alternation, which matches NOTHING — the
        // unescaped key was dead config until 2026-09-22.
        "src/app/[(]app[)]/**": { statements: 80, branches: 60, functions: 80, lines: 80 },
        "src/app/**": { statements: 55, branches: 50, functions: 55, lines: 55 },
        "src/components/**": { statements: 75, branches: 70, functions: 80, lines: 75 },
        "src/lib/**": { statements: 85, branches: 85, functions: 85, lines: 85 },
        // Layer two (2026-09-28 audit, T1): single-file globs for the files
        // the aggregates let sit at 0% (proxy.ts, error.tsx, manifest.ts,
        // (app)/layout.tsx, healthz/route.ts) or ~15% (worker/layout.tsx) —
        // a one-file aggregate IS the file, so these are per-file floors.
        // vitest 4.1.11's `perFile: true` cannot express this layer: the
        // flag is global to the whole thresholds object (verified in
        // node_modules/vitest/dist/chunks/coverage.*.js — checkThresholds
        // applies it to every set, and resolveGlobThresholds strips perFile
        // from globs), so it would demote every aggregate floor above to a
        // per-file floor and instantly fail ~20 files the aggregates
        // legitimately tolerate (offline-queue.ts at 50% inside lib's 85).
        // Floors sit ~15–20 points under each file's measured level
        // (proxy/error/manifest/healthz/(app)layout 100, worker/layout
        // 98.5/87.8/90.5/100 with its tests, 2026-09-28), so a file dropping
        // back to zero fails loudly while benign edits don't. Residual gap,
        // accepted: a NEW file shipping untested is still only caught by the
        // aggregate layer — this list pins the holes already repaired (T2).
        "src/proxy.ts": { statements: 80, branches: 80, functions: 80, lines: 80 },
        "src/app/error.tsx": { statements: 80, branches: 80, functions: 80, lines: 80 },
        "src/app/manifest.ts": { statements: 80, branches: 80, functions: 80, lines: 80 },
        "src/app/healthz/route.ts": { statements: 80, branches: 80, functions: 80, lines: 80 },
        "src/app/[(]app[)]/layout.tsx": { statements: 80, branches: 80, functions: 80, lines: 80 },
        "src/app/worker/layout.tsx": { statements: 80, branches: 70, functions: 70, lines: 80 },
      },
    },
  },
});
