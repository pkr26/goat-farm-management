import { fileURLToPath } from "node:url";
import { defineConfig } from "../../../frontend/node_modules/@playwright/test/index.mjs";
import base from "../../../frontend/playwright.config.ts";

const frontend = fileURLToPath(new URL("../../../frontend/", import.meta.url));
const backend = fileURLToPath(new URL("../../../backend/", import.meta.url));
const auditBrowser = process.env.AUDIT_RUN_LABEL ?? process.env.E2E_BROWSER ?? "chromium";
export default defineConfig({
  ...base,
  testDir: `${frontend}/e2e`,
  globalSetup: `${frontend}/e2e/global-setup.ts`,
  retries: 0,
  outputDir: fileURLToPath(new URL(`./browser-${auditBrowser}-artifacts`, import.meta.url)),
  reporter: [
    ["list"],
    ["json", { outputFile: fileURLToPath(new URL(`./browser-${auditBrowser}-results.json`, import.meta.url)) }],
  ],
  webServer: [
    {
      command: "HOSTNAME=127.0.0.1 PORT=3000 node .next/standalone/server.js",
      cwd: frontend,
      url: "http://localhost:3000/login",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: ".venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000",
      cwd: backend,
      url: "http://localhost:8000/openapi.json",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
