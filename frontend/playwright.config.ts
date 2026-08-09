import { defineConfig, devices } from "@playwright/test";

const BROWSER_DEVICES = {
  chromium: "Desktop Chrome",
  firefox: "Desktop Firefox",
  webkit: "Desktop Safari",
} as const;

const requestedBrowser = process.env.E2E_BROWSER ?? "chromium";
if (!(requestedBrowser in BROWSER_DEVICES)) {
  throw new Error(`Unsupported E2E_BROWSER: ${requestedBrowser}`);
}
const browserName = requestedBrowser as keyof typeof BROWSER_DEVICES;

/**
 * E2E against the real stack: Next.js (production server in CI, dev locally)
 * on 3000 + FastAPI on 8000
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
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  // CI runs this configuration once per browser in isolated jobs/databases.
  // Locally, Chromium remains the fast default; set E2E_BROWSER explicitly
  // to reproduce a Firefox or WebKit failure.
  projects: [{ name: browserName, use: { ...devices[BROWSER_DEVICES[browserName]] } }],
  webServer: [
    {
      command: process.env.CI
        ? "pnpm build && cp -R .next/static .next/standalone/.next/static && HOSTNAME=127.0.0.1 PORT=3000 node .next/standalone/server.js"
        : "pnpm dev",
      cwd: ".",
      url: "http://localhost:3000/login",
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      // In CI the backend is pip-installed at the system level (no .venv);
      // locally `.venv/bin/uvicorn` is the convention. `E2E_UVICORN` overrides both.
      command:
        process.env.E2E_UVICORN
        ?? (process.env.CI ? "uvicorn app.main:app --port 8000" : "../backend/.venv/bin/uvicorn app.main:app --port 8000"),
      cwd: "../backend",
      url: "http://localhost:8000/openapi.json",
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
