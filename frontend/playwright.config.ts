import { defineConfig, devices } from "@playwright/test";

/**
 * E2E against the real stack: Next.js dev server (3000) + FastAPI (8000)
 * + PostgreSQL. Both servers are started by Playwright.
 *
 * globalSetup provisions a fresh user + farm per run via the API, so the
 * suite runs on a clean machine/CI (audit 10-H1). All specs share that one
 * farm's state, so they run strictly serially (workers: 1).
 */
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "pnpm dev",
      cwd: ".",
      url: "http://localhost:3000/login",
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: "../backend/.venv/bin/uvicorn app.main:app --port 8000",
      cwd: "../backend",
      url: "http://localhost:8000/openapi.json",
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
