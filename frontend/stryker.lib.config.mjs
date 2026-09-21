/** @type {import('@stryker-mutator/api/core').PartialStrykerOptions} */
const config = {
  $schema: "./node_modules/@stryker-mutator/core/schema/stryker-schema.json",
  plugins: ["@stryker-mutator/vitest-runner"],
  testRunner: "vitest",
  vitest: {
    configFile: "vitest.config.ts",
    related: true,
  },
  mutate: [
    // *.{ts,tsx}, not just *.ts: auth-context.tsx and i18n/index.tsx are the
    // session/i18n core — a .ts-only glob silently excluded them from the
    // score this shard appeared to cover (2026-09-20 audit P1-11).
    "src/lib/**/*.{ts,tsx}",
    "src/hooks/**/*.{ts,tsx}",
    "src/api/custom-instance.ts",
    "!src/lib/**/*.test.{ts,tsx}",
    "!src/hooks/**/*.test.{ts,tsx}",
  ],
  reporters: ["clear-text", "json"],
  jsonReporter: { fileName: "reports/mutation/lib.json" },
  tempDirName: ".stryker-tmp-lib",
  incremental: true,
  incrementalFile: "reports/stryker-lib-incremental.json",
  cleanTempDir: "always",
  concurrency: 8,
  ignoreStatic: true,
  dryRunTimeoutMinutes: 30,
  timeoutMS: 15000,
  thresholds: { high: 90, low: 80, break: 75 },
};

export default config;
