import path from "node:path";
import { writeFile } from "node:fs/promises";

import { request } from "@playwright/test";

/**
 * Provision a fresh user + farm per e2e run via the real API, so the suite
 * runs on any clean machine/CI (no pre-existing dev account, audit 10-H1).
 * Credentials land in `.e2e-state.json` (gitignored) for helpers.ts to read.
 *
 * Runs after the webServers (Next + FastAPI) are up. Note: the backend
 * rate-limits registrations per client IP (10/5min by default), so very
 * frequent back-to-back local runs can trip a 429 — wait out the window.
 */

const API_BASE = process.env.E2E_API_URL ?? "http://localhost:8000";

export const STATE_FILE = path.join(__dirname, ".e2e-state.json");

export interface E2EState {
  email: string;
  password: string;
  farmName: string;
}

export default async function globalSetup(): Promise<void> {
  const suffix = `${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
  const state: E2EState = {
    email: `e2e-${suffix}@goatfarm.test`,
    password: "e2e-pass-1234",
    farmName: `E2E Farm ${suffix}`,
  };

  const ctx = await request.newContext({ baseURL: API_BASE });
  try {
    const reg = await ctx.post("/api/auth/register", {
      data: { email: state.email, password: state.password, name: "E2E Runner" },
    });
    if (!reg.ok()) {
      throw new Error(`e2e global setup: register failed ${reg.status()}: ${await reg.text()}`);
    }
    const { access_token } = (await reg.json()) as { access_token: string };
    const farm = await ctx.post("/api/auth/farms", {
      data: { name: state.farmName, location: null },
      headers: { Authorization: `Bearer ${access_token}` },
    });
    if (!farm.ok()) {
      throw new Error(`e2e global setup: farm create failed ${farm.status()}: ${await farm.text()}`);
    }
  } finally {
    await ctx.dispose();
  }

  await writeFile(STATE_FILE, `${JSON.stringify(state, null, 2)}\n`, "utf8");
}
