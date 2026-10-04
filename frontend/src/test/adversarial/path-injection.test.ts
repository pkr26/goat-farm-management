/**
 * ADVERSARIAL AUDIT B4/B5 — URL & backend-URL injection fuzz.
 *
 * Attack corpus: every classic open-redirect / traversal / smuggling spelling
 * thrown at the three gatekeepers that stand between attacker-controlled URL
 * state (returnTo, task.action_url, animals_page_path) and <Link href>:
 *   - safeAppPath            (utils.ts — same-origin path sanitiser)
 *   - permittedAppPath       (permission-navigation.ts — same-farm returnTo)
 *   - permittedAppPathFromList (cross-farm variant, record-id demotion)
 *   - permittedTaskActionPath  (task-action-access.ts — backend action urls)
 *
 * Invariant under attack: the accepted result is ALWAYS a canonical absolute
 * app path starting with a single "/", never changes under URL normalisation,
 * and never borrows a permission from a different module.
 */

import { describe, expect, it } from "vitest";

import {
  permittedAppPath,
  permittedAppPathFromList,
  withReturnTo,
} from "@/lib/permission-navigation";
import { permittedTaskActionPath } from "@/lib/task-action-access";
import { safeAppPath } from "@/lib/utils";

const ALL = [
  "dashboard.view",
  "animals.view",
  "animals.create",
  "buckets.view",
  "breeding.view",
  "breeding.manage",
  "kidding.view",
  "kidding.manage",
  "health.view",
  "health.manage",
  "feeding.view",
  "purchases.view",
  "tasks.view",
  "finance.view",
  "simulation.view",
  "reports.view",
  "team.manage",
];
const canAll = (code: string) => ALL.includes(code);

/** Every spelling an adversary tries on a returnTo-style parameter. */
const ATTACK_CORPUS: string[] = [
  "//evil.example/tasks",
  "\\\\evil.example/tasks",
  "/\\evil.example/tasks",
  "/\n/evil.example/tasks",
  "/\t/evil.example/tasks",
  "/\u0000/evil.example/tasks",
  "https://evil.example/tasks",
  "http://evil.example",
  "javascript:alert(1)",
  "data:text/html,<script>alert(1)</script>",
  "vbscript:msgbox",
  "  /tasks",
  "/tasks",
  "/tasks/../finance",
  "/tasks/./overdue",
  "/tasks/%2e%2e/finance",
  "/%74asks",
  "/tasks%2F..%2Ffinance",
  "/tasks?next=//evil.example",
  "/tasks#fragment",
  "/tasks#",
  "/tasks?",
  "/tasks?returnTo=//evil.example",
  "/TASKS",
  "/tasks/",
  "//tasks",
  "///tasks",
  "/tasks\\..\\finance",
  "/tasks\u202e/finance",
  "/tasks%00",
  "%2F%2Fevil.example",
  "/api/auth/permissions",
  "/api/anything",
  "/healthz",
  "/readyz",
  "/farm-select?returnTo=//evil.example",
  "/no-access",
  "/login",
  "/register",
  "/nonexistent-module",
  "/animals/999999999999999999999",
  "/animals/0",
  "/animals/-1",
  "/animals/1e2",
  "/animals/abc",
  "/animals/%31",
];

describe("ADV B4: safeAppPath under attack", () => {
  it.each(ATTACK_CORPUS)("input %j is canonical-or-rejected, never mutated", (raw) => {
    const result = safeAppPath(raw);
    if (result === null) return; // rejected — fine
    // Accepted values must be the EXACT input, absolute, single-slash origin.
    expect(result).toBe(raw);
    expect(result.startsWith("/")).toBe(true);
    expect(result.startsWith("//")).toBe(false);
    expect(result.startsWith("\\")).toBe(false);
    // And must not change under URL canonicalisation (no smuggled dot/percent).
    const parsed = new URL(result, "https://goatfarm.invalid");
    expect(parsed.pathname).toBe(result.split(/[?#]/, 1)[0]);
    expect(parsed.origin).toBe("https://goatfarm.invalid");
  });

  it("rejects every non-string runtime value an attacker could smuggle", () => {
    expect(safeAppPath(null)).toBeNull();
    expect(safeAppPath(undefined)).toBeNull();
    expect(safeAppPath("")).toBeNull();
  });
});

describe("ADV B4: permittedAppPath under attack (same-farm returnTo)", () => {
  it.each(ATTACK_CORPUS)("input %j never yields a cross-module or off-app path", (raw) => {
    const result = permittedAppPath(raw, canAll);
    if (result === null) return;
    expect(result.startsWith("/")).toBe(true);
    expect(result.startsWith("//")).toBe(false);
    // The returned path (minus query/hash) must still resolve to a permitted module.
    const path = result.split(/[?#]/, 1)[0];
    expect(path).not.toMatch(/^\/api\//);
    expect(path).not.toBe("/healthz");
    expect(path).not.toBe("/readyz");
  });

  it("cannot borrow a module permission it does not hold", () => {
    const noFinance = (code: string) => code !== "finance.view" && canAll(code);
    expect(permittedAppPath("/finance", noFinance)).toBeNull();
    expect(permittedAppPath("/finance?month=2026-01", noFinance)).toBeNull();
    // manage routes check their own permission, not the module's view
    const noCreate = (code: string) => code !== "animals.create" && canAll(code);
    expect(permittedAppPath("/animals/new", noCreate)).toBeNull();
    // trailing-slash spelling of an id-free route stays valid (documented)
    expect(permittedAppPath("/animals/new/", canAll)).toBe("/animals/new/");
  });
});

describe("ADV B5: permittedTaskActionPath under attack (backend action_url)", () => {
  const canBreeding = (code: string) => code === "breeding.manage";
  const canNothing = () => false;

  it.each([
    "/breeding/%2e%2e/finance",
    "/breeding/%2F..%2Ffinance",
    "/breeding/../../finance",
    "/breeding/..%2Ffinance",
    "/breeding/%5c..%5cfinance",
    "//evil.example/breeding",
    "/breeding?next=//evil.example",
    "/finance",
    "/tasks",
    "/animals/5",
    "/breeding/../kidding",
    "javascript:alert(1)",
  ])("action_url %j is rejected or breeding/kidding/health-scoped", (raw) => {
    const result = permittedTaskActionPath(raw, canBreeding);
    if (result === null) return;
    expect(result.startsWith("/breeding")).toBe(true);
    expect(result).not.toMatch(/%2e|%2f|%5c/i);
  });

  it("breeding.manage cannot open a health or kidding action url", () => {
    expect(permittedTaskActionPath("/health/new?task_id=1", canBreeding)).toBeNull();
    expect(permittedTaskActionPath("/kidding/new?breeding_id=1", canBreeding)).toBeNull();
    expect(permittedTaskActionPath("/breeding?ultrasound_id=1", canNothing)).toBeNull();
  });
});

describe("ADV B4: permittedAppPathFromList — cross-farm record-id demotion", () => {
  it("never keeps a record id across a farm switch", () => {
    expect(permittedAppPathFromList("/animals/7", ALL)).toBe("/animals");
    expect(permittedAppPathFromList("/breeding/3/ultrasound", ALL)).toBe("/breeding");
    expect(permittedAppPathFromList("/health/schedule/12", ALL)).toBe("/health");
    expect(permittedAppPathFromList("/animals/new", ALL)).toBe("/animals/new");
    expect(permittedAppPathFromList("/animals/new/", ALL)).toBe("/animals/new/");
  });

  it.each(ATTACK_CORPUS)("farm-switch variant of %j stays on-app", (raw) => {
    const result = permittedAppPathFromList(raw, ALL);
    if (result === null) return;
    expect(result.startsWith("/")).toBe(true);
    expect(result.startsWith("//")).toBe(false);
    expect(result.split(/[?#]/, 1)[0]).not.toMatch(/^\/api\//);
  });
});

describe("ADV B4: withReturnTo cannot smuggle state into the destination", () => {
  it("overwrites an existing returnTo, never appends a second one", () => {
    const url = withReturnTo("/tasks?returnTo=//evil.example", "/dashboard");
    expect(new URLSearchParams(url.split("?")[1]).getAll("returnTo")).toEqual([
      "/dashboard",
    ]);
    expect(url.startsWith("/tasks?returnTo=")).toBe(true);
  });
});
