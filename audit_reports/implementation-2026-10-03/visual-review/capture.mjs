/** Read-only visual capture against the running synthetic E2E stack.
 * Uses direct Playwright browser contexts; never invokes test/globalSetup.
 * Credentials stay in process memory and are redacted from error output.
 * No worker, PIN, role, farm, or ownership form is submitted.
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const artifactRoot = dirname(fileURLToPath(import.meta.url));
const afterFix = process.argv.includes("--after");
const output = afterFix ? resolve(artifactRoot, "after") : artifactRoot;
await mkdir(output, { recursive: true });
const repo = resolve(artifactRoot, "../../..");
const require = createRequire(resolve(repo, "frontend/package.json"));
const { chromium, expect } = require("@playwright/test");
const credentials = JSON.parse(await readFile(resolve(repo, "frontend/e2e/.e2e-state.json"), "utf8"));
const redact = (text) => String(text).replaceAll(credentials.email, "[redacted]").replaceAll(credentials.password, "[redacted]");
const browser = await chromium.launch({ headless: true });
const captures = [];
const mutationRequests = [];
const contexts = [];
const ownedLoginSessions = [];

async function context(options, language, theme) {
  const current = await browser.newContext({ baseURL: "http://localhost:3000", ...options });
  contexts.push(current);
  await current.addInitScript(({ language, theme }) => {
    localStorage.setItem("herdly.language", language);
    localStorage.setItem("theme", theme);
  }, { language, theme });
  current.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/") && !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
      mutationRequests.push({ method: request.method(), path: url.pathname });
    }
  });
  current.on("response", async (response) => {
    if (new URL(response.url()).pathname === "/api/auth/login" && response.status() === 200) {
      const data = await response.json().catch(() => null);
      if (data?.access_token) ownedLoginSessions.push({ context: current, accessToken: data.access_token });
    }
  });
  return current;
}

async function capture(page, name, state) {
  if (afterFix && !["03-", "04-", "07-", "08-"].some(prefix => name.startsWith(prefix))) return;
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: resolve(output, name), animations: "disabled" });
  captures.push({ file: name, ...state, viewport: page.viewportSize(), urlPath: new URL(page.url()).pathname });
  console.log(`Captured ${name}`);
}

async function scrollAssumptions(page) {
  await page.locator("#sim-assumptions").scrollIntoViewIfNeeded();
  await page.evaluate(() => {
    const card = document.getElementById("sim-assumptions");
    if (card) window.scrollBy(0, card.getBoundingClientRect().top - 82);
  });
}

try {
  const desktop = await context({ viewport: { width: 1440, height: 1100 } }, "en", "light");
  const page = await desktop.newPage();
  await page.goto("/login");
  await expect(async () => {
    await page.getByLabel("Email", { exact: true }).fill(credentials.email);
    await page.getByLabel("Password", { exact: true }).fill(credentials.password);
    expect(await page.evaluate(({ email, password }) => {
      const inputs = Array.from(document.querySelectorAll("input"));
      return inputs.some(input => input.value === email) && inputs.some(input => input.value === password);
    }, { email: credentials.email, password: credentials.password })).toBe(true);
  }).toPass({ timeout: 15_000 });
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 20_000 });

  for (const language of ["en", "te"]) {
    await page.goto("/simulation");
    await page.getByRole("button", { name: language === "en" ? "EN" : "తెలుగు", exact: true }).click();
    const label = language === "en" ? "Herd" : "మంద";
    const summary = page.locator("summary").filter({ hasText: new RegExp(`^${label}$`) });
    await expect(summary).toBeVisible();
    const details = summary.locator("..");
    await expect(details).toHaveAttribute("open", "");
    await scrollAssumptions(page);
    await capture(page, language === "en" ? "01-simulation-en-expanded.png" : "03-simulation-te-expanded.png", {
      language, theme: "light", state: "Assumptions editor with Meta and Herd expanded; section help outside summary",
      summariesWithInteractiveDescendants: await page.locator("summary").evaluateAll(summaries => summaries.filter(summary => summary.querySelector("button, a[href], input, select, textarea, [tabindex]:not([tabindex='-1'])")).length),
    });
    await summary.click();
    await expect(details).not.toHaveAttribute("open", "");
    const help = page.getByRole("button", { name: language === "en" ? /^Explain Herd\s*\?$/ : /^మంద వివరించండి\s*\?$/ });
    await help.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(details).not.toHaveAttribute("open", "");
    await capture(page, language === "en" ? "02-simulation-en-collapsed-help.png" : "04-simulation-te-collapsed-help.png", {
      language, theme: "light", state: "Herd collapsed; sibling help opened with Enter; disclosure stays collapsed",
    });
    await page.keyboard.press("Escape");
  }

  for (const language of ["en", "te"]) {
    await page.goto("/team");
    await page.getByRole("button", { name: language === "en" ? "EN" : "తెలుగు", exact: true }).click();
    await page.getByRole("button", { name: language === "en" ? "Add worker" : "కార్మికుడిని చేర్చండి", exact: true }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("radio", { name: language === "en" ? "Tablet PIN" : "టాబ్లెట్ పిన్", exact: true }).check();
    await capture(page, language === "en" ? "05-team-pin-add-en.png" : "06-team-pin-add-te.png", {
      language, theme: "light", state: "Add worker dialog with Tablet PIN selected; fields empty; no generation or submission",
    });
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: language === "en" ? "Transfer ownership" : "యాజమాన్యం బదిలీ చేయి", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await capture(page, language === "en" ? "07-ownership-transfer-en.png" : "08-ownership-transfer-te.png", {
      language, theme: "light", state: "Ownership transfer dialog opened without selecting recipient, password, acknowledgement, or submit",
      recipientTriggerWidth: (await page.locator("#transfer-recipient").boundingBox())?.width,
      visibleRecipientPrompt: await page.locator("#transfer-recipient").innerText(),
    });
    await page.keyboard.press("Escape");
  }

  await page.getByRole("button", { name: "EN", exact: true }).click();
  await page.getByRole("button", { name: "Logout", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/, { timeout: 20_000 });
  await desktop.close();

  const mobileLogin = await context({ viewport: { width: 390, height: 844 }, isMobile: true, deviceScaleFactor: 1, hasTouch: true, colorScheme: "dark" }, "en", "dark");
  const login = await mobileLogin.newPage();
  await login.goto("/login");
  await expect(login.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await expect(login.locator("html")).toHaveClass(/dark/);
  await capture(login, "09-mobile-login-en-dark.png", { language: "en", theme: "dark", state: "Signed-out web login; empty fields; mobile viewport" });
  await mobileLogin.close();

  const mobileWorker = await context({ viewport: { width: 390, height: 844 }, isMobile: true, deviceScaleFactor: 1, hasTouch: true, colorScheme: "dark" }, "te", "dark");
  const worker = await mobileWorker.newPage();
  await worker.goto("/worker/login");
  await worker.getByTestId("worker-setup-start").click();
  await expect(worker.getByTestId("worker-setup-credentials")).toBeVisible();
  await expect(worker.locator("html")).toHaveClass(/dark/);
  await capture(worker, "10-mobile-worker-setup-te-dark.png", { language: "te", theme: "dark", state: "Unconfigured tablet setup credentials; empty fields; no sign-in, pinning, or worker mutation" });
  await mobileWorker.close();

  const unexpectedMutations = mutationRequests.filter(({ path }) => !["/api/auth/login", "/api/auth/refresh", "/api/auth/logout", "/api/auth/logout-session"].includes(path));
  if (unexpectedMutations.length) throw new Error("Unexpected domain mutation during read-only visual capture");
  await writeFile(resolve(output, "capture-results.json"), JSON.stringify({
    status: "captured",
    capturedAtUtc: new Date().toISOString(),
    browser: "Chromium direct private contexts",
    application: "existing production frontend localhost:3000 and synthetic E2E API localhost:8000",
    globalSetupInvoked: false,
    sharedE2EStateWritten: false,
    domainFormSubmissions: 0,
    phase: afterFix ? "after approved visual corrections" : "before visual corrections",
    buildId: (await readFile(resolve(repo, "frontend/.next/BUILD_ID"), "utf8")).trim(),
    mutationRequests,
    captures,
  }, null, 2) + "\n");
} catch (error) {
  console.error(redact(error instanceof Error ? error.message : error));
  process.exitCode = 1;
} finally {
  // Revoke only bearer families created by this capture process. These
  // contexts are private and never contain another actor's authority.
  for (const { context: current, accessToken } of ownedLoginSessions) {
    await current.request.post("http://localhost:3000/api/auth/logout-session", {
      headers: { Authorization: `Bearer ${accessToken}` },
    }).catch(() => {});
  }
  for (const current of contexts) await current.close().catch(() => {});
  await browser.close();
}
