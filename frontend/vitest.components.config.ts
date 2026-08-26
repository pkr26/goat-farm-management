/** Scoped vitest config used ONLY by the mutation-testing harness.
 *
 * The shadcn primitives in src/components/ui/ are rendered by nearly every page
 * test, so Stryker's related-test set for them is the whole suite (~2,400 tests
 * per mutant) — computationally infeasible here.
 *
 * Scoping to src/components' own tests measures what those components' DEDICATED
 * tests pin down. Primitives with no dedicated test will surface as NoCoverage,
 * which is the accurate and actionable finding: they have no tests of their own.
 *
 * NB: deliberately NOT mergeConfig — that concatenates arrays, so the base
 * `src/**` include would survive and the scope would silently be a no-op.
 * Not used by `pnpm test`.
 */
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    globals: true,
    maxWorkers: 2,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/components/**/*.test.tsx", "src/hooks/**/*.test.tsx"],
  },
});
