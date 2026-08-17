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
    "src/**/*.{ts,tsx}",
    "!src/**/*.test.{ts,tsx}",
    "!src/api/generated/**",
    "!src/test/**",
  ],
  reporters: ["clear-text", "progress", "html", "json"],
  ignorePatterns: [
    ".next/**",
    "coverage/**",
    "e2e/**",
    "playwright-report/**",
    "reports/**",
    ".stryker-tmp*/**",
    "test-results/**",
  ],
  incremental: true,
  cleanTempDir: "always",
  concurrency: 2,
  timeoutMS: 15000,
  thresholds: {
    high: 90,
    low: 80,
    break: 75,
  },
};

export default config;
