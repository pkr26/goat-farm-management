/** @type {import('@stryker-mutator/api/core').PartialStrykerOptions} */
const config = {
  $schema: "./node_modules/@stryker-mutator/core/schema/stryker-schema.json",
  plugins: ["@stryker-mutator/vitest-runner"],
  testRunner: "vitest",
  vitest: {
    configFile: "vitest.config.ts",
    related: true,
  },
  mutate: ["src/components/**/*.{ts,tsx}", "!src/components/**/*.test.{ts,tsx}"],
  reporters: ["clear-text", "progress-append-only", "json"],
  jsonReporter: { fileName: "reports/mutation/components.json" },
  tempDirName: ".stryker-tmp-components",
  incremental: true,
  incrementalFile: "reports/stryker-components-incremental.json",
  cleanTempDir: "always",
  concurrency: 4,
  ignoreStatic: true,
  dryRunTimeoutMinutes: 30,
  timeoutMS: 15000,
  thresholds: { high: 90, low: 80, break: 75 },
};

export default config;
