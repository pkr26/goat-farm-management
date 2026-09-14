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
        statements: 85,
        branches: 80,
        functions: 85,
        lines: 85,
      },
    },
  },
});
