/**
 * ADVERSARIAL AUDIT C4 — enum-drift tripwires (executed, source-verified).
 *
 * Attack: the backend adds a category/bucket/shift/type to the contract.
 * Orval regenerates the enums; every SELECT immediately offers the new value —
 * but every hand-copied z.enum still rejects it, turning a valid option into a
 * silent inline failure. These tests parse the page sources, extract the
 * hand-copied literal lists, and diff them against the generated enums —
 * the drift fails HERE, not in a farmer's dialog.
 *
 * (Finance/feeding/tasks/health carry hand-copied lists; health's event types
 * are already derived — each case documents which style it is.)
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  DispenseInBucket,
  DispenseInShift,
  HealthEventInType,
  TaskCreateInCategory,
  TransactionInCategory,
} from "@/api/generated/models";

const APP = join(import.meta.dirname, "..", "..", "app", "(app)");

function pageSource(relative: string): string {
  return readFileSync(join(APP, relative), "utf8");
}

/** Extract the members inside the FIRST z.enum([...]) following `anchor`.
 * Supports both quoted literals and `GeneratedEnum.MEMBER` references — the
 * preferred style, which makes drift structurally impossible. */
function zodEnumLiterals(source: string, anchor: string): string[] {
  const anchorIndex = source.indexOf(anchor);
  expect(anchorIndex, `anchor ${anchor} not found in source`).toBeGreaterThan(-1);
  const enumIndex = source.indexOf("z.enum([", anchorIndex);
  expect(enumIndex, `z.enum not found after ${anchor}`).toBeGreaterThan(-1);
  const body = source.slice(enumIndex, source.indexOf("])", enumIndex));
  const quoted = [...body.matchAll(/"([A-Z0-9_]+)"/g)].map((match) => match[1]);
  if (quoted.length > 0) return quoted;
  return [...body.matchAll(/[A-Za-z0-9]+\.([A-Z0-9_]+)/g)].map((match) => match[1]);
}

describe("ADV C4: hand-copied zod enums must equal the generated contract", () => {
  it("finance: txn category list === TransactionInCategory", () => {
    const literals = zodEnumLiterals(pageSource("finance/page.tsx"), "category: z.enum([");
    expect(new Set(literals)).toEqual(new Set(Object.values(TransactionInCategory)));
  });

  it("finance: correction schema inherits the same list (extends txnSchema)", () => {
    const source = pageSource("finance/page.tsx");
    expect(source).toContain("correctionSchema = txnSchema.extend");
  });

  it("feeding: dispense bucket list === DispenseInBucket", () => {
    const literals = zodEnumLiterals(pageSource("feeding/page.tsx"), "bucket: z.enum([");
    expect(new Set(literals)).toEqual(new Set(Object.values(DispenseInBucket)));
  });

  it("feeding: dispense shift list === DispenseInShift", () => {
    const literals = zodEnumLiterals(pageSource("feeding/page.tsx"), "shift: z.enum([");
    expect(new Set(literals)).toEqual(new Set(Object.values(DispenseInShift)));
  });

  it("tasks: manual-duty category list === TaskCreateInCategory", () => {
    const literals = zodEnumLiterals(pageSource("tasks/page.tsx"), "category: z.enum([");
    expect(new Set(literals)).toEqual(new Set(Object.values(TaskCreateInCategory)));
  });

  it("health: event type list === HealthEventInType", () => {
    const literals = zodEnumLiterals(pageSource("health/page.tsx"), "type: z.enum([");
    expect(new Set(literals)).toEqual(new Set(Object.values(HealthEventInType)));
  });

  it("kidding: ease + kid-status lists stay in sync with the contract", async () => {
    // The kidding page hardcodes EASES/KID_STATUSES as const arrays at the top
    // of the module; the generated models carry the authoritative enums.
    const { KiddingCreateInEase, KidInStatus } = await import("@/api/generated/models");
    const source = pageSource("kidding/page.tsx");
    const eases = [...source.matchAll(/EASES = \[([^\]]+)\]/g)][0][1];
    const statuses = [...source.matchAll(/KID_STATUSES = \[([^\]]+)\]/g)][0][1];
    const extract = (block: string) =>
      [...block.matchAll(/"([A-Z]+)"/g)].map((m) => m[1]);
    expect(new Set(extract(eases))).toEqual(new Set(Object.values(KiddingCreateInEase)));
    expect(new Set(extract(statuses))).toEqual(new Set(Object.values(KidInStatus)));
  });
});
