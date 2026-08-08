/**
 * taskPrefill: product/disease hints parsed from a linked duty's
 * title, so recorded health events match the vaccination templates instead
 * of leaving both optional fields blank.
 */

import { describe, expect, it } from "vitest";

import type { TaskOut } from "@/api/generated/models";

import { taskPrefill } from "./page";

function makeTask(overrides: Partial<TaskOut>): TaskOut {
  return {
    id: 1,
    title: "Task",
    due_date: "2026-08-07",
    status: "PENDING",
    category: "OTHER",
    auto_generated: true,
    animal_id: null,
    purchase_batch_id: null,
    breeding_record_id: null,
    assigned_role_id: null,
    assigned_user_id: null,
    recur_days: null,
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
    skipped_by_id: null,
    ...overrides,
  };
}

describe("taskPrefill (3-1)", () => {
  it("quarantine deworming duty → drug product + Deworming target", () => {
    expect(
      taskPrefill(
        makeTask({
          category: "DEWORMING",
          title: "[Sharma Traders #2] Day 4: deworm — Albendazole/Closantel oral + Ivermectin SC",
          purchase_batch_id: 2,
        }),
      ),
    ).toEqual({
      product_name: "Albendazole/Closantel oral + Ivermectin SC",
      disease_target: "Deworming",
    });
  });

  it.each([
    ["[B #2] Day 10: vaccinate PPR (live viral, SC)", "PPR"],
    ["[B #2] Day 20: vaccinate ET + Tetanus (toxoid, SC)", "ET + Tetanus"],
    ["[B #2] Day 30: vaccinate Goat Pox (live viral, SC)", "Goat Pox"],
    ["[B #2] Day 40: vaccinate FMD (killed, SC)", "FMD"],
  ])("quarantine vaccine duty %s → disease target %s", (title, target) => {
    expect(
      taskPrefill(makeTask({ category: "VACCINE", title, purchase_batch_id: 2 })),
    ).toEqual({ disease_target: target });
  });

  it("pre-kidding ET+TT duty → the ET + TT pre-kidding template, tag stripped", () => {
    expect(
      taskPrefill(
        makeTask({
          category: "VACCINE",
          title: "Pre-kidding ET+TT vaccine: G-003",
          animal_id: 3,
          animal_tag: "G-003",
        }),
      ),
    ).toEqual({ disease_target: "ET + TT pre-kidding" });
  });

  it("plain deworming duty without a drug part → target only", () => {
    expect(
      taskPrefill(makeTask({ category: "DEWORMING", title: "Deworm Kaveri", animal_id: 3 })),
    ).toEqual({ product_name: undefined, disease_target: "Deworming" });
  });

  it("manual vaccine duty with a free-text title → title as the target", () => {
    expect(
      taskPrefill(makeTask({ category: "VACCINE", title: "PPR vaccination" })),
    ).toEqual({ disease_target: "PPR vaccination" });
  });

  it("non-health categories give no hints", () => {
    expect(taskPrefill(makeTask({ category: "CLEANING", title: "Scrub feeders" }))).toEqual(
      {},
    );
  });
});
