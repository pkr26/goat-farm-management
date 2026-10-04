import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { e2eCredentials, pickSelectOption, signIn, uniqueTag } from "./helpers";

test.beforeEach(async ({ page }) => {
  // These journeys assert English copy explicitly. The worker shell otherwise
  // defaults an unconfigured shared tablet to Telugu.
  await page.addInitScript(() => localStorage.setItem("herdly.language", "en"));
});

async function createTaskOnlyRole(request: APIRequestContext, name: string) {
  const credentials = e2eCredentials();
  const login = await request.post("http://localhost:8000/api/auth/login", {
    data: { email: credentials.email, password: credentials.password },
  });
  expect(login.ok()).toBeTruthy();
  const { access_token } = await login.json() as { access_token: string };
  const farms = await request.get("http://localhost:8000/api/auth/farms", {
    headers: { Authorization: `Bearer ${access_token}` },
  });
  const farm = (await farms.json() as { id: number; name: string }[]).find(item => item.name === credentials.farmName);
  expect(farm).toBeDefined();
  const response = await request.post("http://localhost:8000/api/team/roles", {
    headers: { Authorization: `Bearer ${access_token}`, "X-Farm-Id": String(farm!.id), "Idempotency-Key": randomUUID() },
    data: { name, permissions: ["tasks.view", "tasks.complete"] },
  });
  expect(response.status(), await response.text()).toBe(201);
}

async function addWorker(page: Page, name: string, email: string, role: string, credential: string, pin = false) {
  await page.goto("/team");
  await page.getByRole("button", { name: "Add worker", exact: true }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add worker", exact: true });
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog.getByLabel("Email *", { exact: true }).fill(email);
  if (pin) await dialog.getByRole("radio", { name: "Tablet PIN", exact: true }).check();
  await dialog.getByLabel(pin ? "Worker PIN" : "Password (min 12 chars) *", { exact: true }).fill(credential);
  await pickSelectOption(dialog, "Role *", role);
  await dialog.getByRole("button", { name: "Add worker", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("row", { name: new RegExp(email) })).toBeVisible();
}

test("task-only password worker completes first password rotation on the worker board", async ({ page, request }) => {
  const name = uniqueTag("Password Worker");
  const email = `${uniqueTag("password-worker")}@goatfarm.test`;
  const role = uniqueTag("Task only");
  const temporary = "temporary-pass-1234";
  await createTaskOnlyRole(request, role);
  await signIn(page);
  await addWorker(page, name, email, role, temporary);
  await page.getByRole("button", { name: "Logout", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(temporary);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/worker$/, { timeout: 20_000 });
  await expect(page.getByTestId("worker-identity")).toHaveText(name);
  await page.getByTestId("worker-change-password").click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Current password for password change", { exact: true }).fill(temporary);
  await dialog.getByLabel("New password", { exact: true }).fill("rotated-worker-pass-1234");
  await dialog.getByLabel("Confirm new password", { exact: true }).fill("rotated-worker-pass-1234");
  await dialog.getByRole("button", { name: "Change password", exact: true }).click();
  await expect(page.getByText("Password changed. Other signed-in sessions were revoked.", { exact: true })).toBeVisible();
  await expect(page.getByTestId("worker-change-password")).toBeHidden();
  await expect(page).toHaveURL(/\/worker$/);
  await expect(page.locator("nav")).toHaveCount(0);
});

test("owner creates and resets a PIN worker through Team and pins the tablet through the UI", async ({ page, request }) => {
  const credentials = e2eCredentials();
  const name = uniqueTag("PIN Worker");
  const email = `${uniqueTag("pin-worker")}@goatfarm.test`;
  const role = uniqueTag("PIN duties");
  const pin = "432198765432";
  await createTaskOnlyRole(request, role);
  await signIn(page);
  await addWorker(page, name, email, role, "432198", true);
  const row = page.getByRole("row", { name: new RegExp(email) });
  await row.getByRole("button", { name: "Reset tablet PIN", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: `Tablet PIN for ${name}`, exact: true });
  await dialog.getByLabel("Worker PIN", { exact: true }).fill(pin);
  await dialog.getByRole("button", { name: "Save PIN", exact: true }).click();
  await expect(dialog).toBeHidden();
  await page.getByRole("button", { name: "Logout", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/worker/login");
  await page.getByTestId("worker-setup-start").click();
  await page.locator("#worker-setup-email").fill(credentials.email);
  await page.locator("#worker-setup-password").fill(credentials.password);
  await page.getByTestId("worker-setup-credentials").getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByTestId("worker-setup-farms").getByRole("button", { name: credentials.farmName, exact: true }).click();
  await page.getByRole("button", { name: new RegExp(name) }).click();
  for (const digit of pin) await page.getByTestId(`pin-key-${digit}`).click();
  await page.getByTestId("pin-sign-in").click();
  await expect(page).toHaveURL(/\/worker$/, { timeout: 20_000 });
  await expect(page.getByTestId("worker-identity")).toHaveText(name);
  await expect(page.getByTestId("worker-change-password")).toHaveCount(0);
  await expect(page.locator("nav")).toHaveCount(0);
});
