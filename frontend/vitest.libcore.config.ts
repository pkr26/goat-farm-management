/** Scoped vitest config used ONLY by the mutation-testing harness.
 *
 * api-client / auth-context / idempotent-request are imported (transitively) by
 * almost every test, so Stryker's "related tests" set for them is the entire
 * suite — ~2,400 tests per mutant, which puts a full run at 6-8 hours.
 *
 * Restricting `include` to each module's own dedicated tests makes the
 * measurement far faster AND STRICTER: a mutant that some page test happens to
 * kill incidentally is now reported as survived, surfacing more genuine gaps.
 *
 * NB: this deliberately does NOT use mergeConfig — that concatenates array
 * options, so the base `src/**` include would survive and the scope would be a
 * no-op (it silently ran 2,445 tests before this was written this way).
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
    // Globs, not an explicit file list: new dedicated test files (e.g. the
    // *.copy/*.branches/*.behaviour ones added by the mutation-hardening round)
    // must be picked up automatically, or a re-measurement silently ignores them.
    include: [
      "src/lib/api-client*.test.ts",
      "src/lib/auth-context*.test.tsx",
      "src/lib/idempotent-request*.test.ts",
    ],
  },
});
