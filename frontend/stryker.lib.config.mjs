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
    "src/lib/**/*.ts",
    "src/hooks/**/*.ts",
    "src/api/custom-instance.ts",
    "!src/lib/**/*.test.ts",
    "!src/hooks/**/*.test.ts",
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
