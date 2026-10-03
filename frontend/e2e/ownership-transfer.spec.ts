import { randomUUID } from "node:crypto";

import { expect, test, type APIResponse } from "@playwright/test";

import type {
  FarmOut,
  MembershipOut,
  PermissionsOut,
  TeamOut,
  TokenOut,
} from "../src/api/generated/models";

async function body<T>(response: APIResponse, status: number): Promise<T> {
  expect(response.status(), await response.text()).toBe(status);
  return (await response.json()) as T;
}

test("an owner transfers a separate farm to a password-rotated member through Team", async ({ page, request }) => {
  test.setTimeout(120_000);
  // This fixture owns its own farm and identities. Transferring the shared
  // global-setup farm would revoke the other browser specs' owner authority.
  const suffix = randomUUID();
  const ownerEmail = `transfer-owner-${suffix}@goatfarm.test`;
  const ownerPassword = `Owner-${suffix}-pass`;
  const recipientEmail = `transfer-recipient-${suffix}@goatfarm.test`;
  const temporaryPassword = `Temporary-${suffix}-pass`;
  const recipientPassword = `Rotated-${suffix}-pass`;
  const recipientName = `Transfer recipient ${suffix}`;
  const registered = await body<TokenOut>(await request.post("/api/auth/register", {
    data: { email: ownerEmail, password: ownerPassword, name: `Transfer owner ${suffix}` },
  }), 201);
  const farm = await body<FarmOut>(await request.post("/api/auth/farms", {
    data: { name: `Ownership transfer ${suffix}`, timezone: "America/Phoenix", location: "Isolated browser regression" },
    headers: { Authorization: `Bearer ${registered.access_token}`, "Idempotency-Key": randomUUID() },
  }), 201);
  const ownerHeaders = { Authorization: `Bearer ${registered.access_token}`, "X-Farm-Id": String(farm.id) };
  const roster = await body<TeamOut>(await request.get("/api/team", { headers: ownerHeaders }), 200);
  const cleaner = roster.roles.find((role) => role.code === "CLEANER");
  if (!cleaner) throw new Error("The isolated farm did not seed its CLEANER role.");
  const member = await body<MembershipOut>(await request.post("/api/team/workers", {
    headers: { ...ownerHeaders, "Idempotency-Key": randomUUID() },
    data: { email: recipientEmail, name: recipientName, password: temporaryPassword, role_id: cleaner.id },
  }), 201);
  expect(member.pin_set).toBe(false);
  const recipientLogin = await body<TokenOut>(await request.post("/api/auth/login", {
    data: { email: recipientEmail, password: temporaryPassword },
  }), 200);
  const rotated = await body<TokenOut>(await request.post("/api/auth/change-password", {
    headers: { Authorization: `Bearer ${recipientLogin.access_token}` },
    data: { current_password: temporaryPassword, new_password: recipientPassword },
  }), 200);
  const priorAuthority = await body<PermissionsOut>(await request.get("/api/auth/permissions", {
    headers: { Authorization: `Bearer ${rotated.access_token}`, "X-Farm-Id": String(farm.id) },
  }), 200);
  expect(priorAuthority.is_owner).toBe(false);
  expect(priorAuthority.permissions).not.toContain("team.manage");

  await page.goto("/login");
  // Retry filling across initial hydration, as in the shared sign-in helper.
  await expect(async () => {
    await page.getByLabel("Email", { exact: true }).fill(ownerEmail);
    await page.getByLabel("Password", { exact: true }).fill(ownerPassword);
    await expect(page.getByLabel("Email", { exact: true })).toHaveValue(ownerEmail);
    await expect(page.getByLabel("Password", { exact: true })).toHaveValue(ownerPassword);
  }).toPass({ timeout: 15_000 });
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 20_000 });
  await page.goto("/team");
  await expect(page.getByRole("row", { name: new RegExp(recipientEmail.replaceAll(".", "\\.")) })).toBeVisible();
  await page.getByRole("button", { name: "Transfer ownership", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Transfer farm ownership", exact: true });
  await expect(dialog).toContainText("must sign in again");
  const submit = dialog.getByRole("button", { name: "Transfer ownership", exact: true });
  await expect(submit).toBeDisabled();
  await dialog.locator("#transfer-recipient").click();
  await page.getByRole("option", { name: `${recipientName} — ${recipientEmail}`, exact: true }).click();
  await dialog.getByLabel("Your current password", { exact: true }).fill(ownerPassword);
  await expect(submit).toBeDisabled();
  await dialog.getByRole("checkbox", {
    name: "I understand that the selected member will become the owner and that I will lose owner authority.",
    exact: true,
  }).check();
  await expect(submit).toBeEnabled();
  const transferred = page.waitForResponse((response) => response.url().endsWith(`/api/auth/farms/${farm.id}/transfer-ownership`) && response.request().method() === "POST");
  await submit.click();
  expect((await transferred).status()).toBe(200);
  await expect(page).toHaveURL(/\/farm-select$/, { timeout: 20_000 });

  // The old owner loses farm authority, and the recipient's earlier session
  // is revoked. A new sign-in receives real owner authority for this farm.
  expect((await request.get("/api/auth/permissions", { headers: ownerHeaders })).status()).toBe(404);
  const formerFarms = await body<FarmOut[]>(await request.get("/api/auth/farms", {
    headers: { Authorization: `Bearer ${registered.access_token}` },
  }), 200);
  expect(formerFarms.some((item) => item.id === farm.id)).toBe(false);
  expect((await request.get("/api/auth/farms", {
    headers: { Authorization: `Bearer ${rotated.access_token}` },
  })).status()).toBe(401);
  const newOwner = await body<TokenOut>(await request.post("/api/auth/login", {
    data: { email: recipientEmail, password: recipientPassword },
  }), 200);
  const newAuthority = await body<PermissionsOut>(await request.get("/api/auth/permissions", {
    headers: { Authorization: `Bearer ${newOwner.access_token}`, "X-Farm-Id": String(farm.id) },
  }), 200);
  expect(newAuthority.is_owner).toBe(true);
  expect(newAuthority.permissions).toEqual(expect.arrayContaining(["team.manage", "finance.manage"]));
  const newOwnerFarms = await body<FarmOut[]>(await request.get("/api/auth/farms", {
    headers: { Authorization: `Bearer ${newOwner.access_token}` },
  }), 200);
  expect(newOwnerFarms.find((item) => item.id === farm.id)?.role).toBeNull();
});
