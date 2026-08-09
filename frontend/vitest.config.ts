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
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
