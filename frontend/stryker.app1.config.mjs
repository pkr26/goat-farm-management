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
    "src/app/**/*.{ts,tsx}",
    "!src/app/**/*.test.{ts,tsx}",
    "!src/app/(app)/simulation/**",
    "!src/app/(app)/health/**",
    "!src/app/(app)/animals/**",
  ],
  reporters: ["clear-text", "progress-append-only", "json"],
  jsonReporter: { fileName: "reports/mutation/app1.json" },
  tempDirName: ".stryker-tmp-app1",
  ignorePatterns: [".stryker-tmp*/**", "reports/**", "coverage/**"],
  incremental: true,
  incrementalFile: "reports/stryker-app1-incremental.json",
  cleanTempDir: "always",
  concurrency: 8,
  ignoreStatic: true,
  dryRunTimeoutMinutes: 30,
  timeoutMS: 15000,
  thresholds: { high: 90, low: 80, break: 75 },
};

export default config;
