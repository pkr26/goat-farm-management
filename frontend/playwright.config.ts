import { defineConfig, devices } from "@playwright/test";

/**
 * E2E against the real stack: Next.js dev server (3000) + FastAPI (8000)
 * + PostgreSQL. Both servers are started by Playwright.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
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
