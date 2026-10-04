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
 * suite runs on a clean machine/CI. All specs share that one
 * farm's state, so they run strictly serially (workers: 1).
 */
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  workers: 1,
  // A committed test.only would otherwise narrow the whole CI gate to one
  // test and still exit 0.
  forbidOnly: !!process.env.CI,
  // CI retries exist to absorb infrastructure noise only: any test that needs a retry
  // is recorded with status "flaky" by the JSON reporter below, and the e2e workflow
  // fails the job on a non-zero flaky count — a genuinely racy product behavior can no
  // longer land on main as "passing". Local runs stay at retries: 0 so a flake is
  // always visible in the console.
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI
    ? [
        ["list"],
        ["html", { open: "never" }],
        // Machine-readable verdicts for the workflow's flaky gate; the file
        // rides along in the uploaded playwright-report artifact.
        ["json", { outputFile: "playwright-report/results.json" }],
      ]
    : [["list"]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  // CI runs this configuration once per browser in isolated jobs/databases.
  // Locally, Chromium remains the fast default; set E2E_BROWSER explicitly
  // to reproduce a Firefox or WebKit failure.
  //
  // Worker journeys get three deliberately small device gates: the primary
  // Android phone, an actual tablet breakpoint in landscape, and Mobile
  // Safari/WebKit for service-worker + IndexedDB engine coverage. Desktop
  // projects skip those files because their md:hidden card assertions are
  // intentionally false at desktop geometry.
  projects: [
    {
      name: browserName,
      use: { ...devices[BROWSER_DEVICES[browserName]] },
      testIgnore: /mobile-worker-journey\.spec\.ts|worker-tablet-journey\.spec\.ts/,
    },
    ...(browserName === "chromium"
      ? [
          {
            name: "Mobile Chrome",
            use: { ...devices["Pixel 7"] },
            testMatch: /mobile-worker-journey\.spec\.ts|worker-tablet-journey\.spec\.ts/,
          },
          {
            name: "Tablet Chrome landscape",
            use: { ...devices["Galaxy Tab S9 landscape"] },
            testMatch: /worker-tablet-journey\.spec\.ts/,
          },
        ]
      : []),
    ...(browserName === "webkit"
      ? [
          {
            name: "Mobile Safari",
            use: { ...devices["iPhone 13"] },
            testMatch: /worker-tablet-journey\.spec\.ts/,
          },
        ]
      : []),
  ],
  webServer: [
    {
      command: process.env.CI
        ? "pnpm build && cp -R .next/static .next/standalone/.next/static && ([ -d public ] && cp -R public .next/standalone/public || true) && HOSTNAME=127.0.0.1 PORT=3000 node .next/standalone/server.js"
        : "pnpm dev",
      cwd: ".",
      url: "http://localhost:3000/login",
      reuseExistingServer: !process.env.CI,
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
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
