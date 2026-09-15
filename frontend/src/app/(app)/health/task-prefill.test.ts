/**
 * taskPrefill: product/disease hints parsed from a linked duty's
 * title, so recorded health events match the vaccination templates instead
 * of leaving both optional fields blank.
 */

import { describe, expect, it } from "vitest";

import type { TaskOut } from "@/api/generated/models";

import { taskPrefill } from "./task-prefill";

function makeTask(overrides: Partial<TaskOut>): TaskOut {
  return {
    id: 1,
    title: "Task",
    title_key: null,
    title_args: {},
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
    recurring_series_id: null,
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
    skipped_by_id: null,
    skipped_at: null,
    skip_reason: null,
    rejected_by_id: null,
    rejected_at: null,
    created_at: "2026-01-01T00:00:00Z",
    created_by_id: null,
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

  it("does not strip an animal tag unless it is the title's exact suffix", () => {
    expect(
      taskPrefill(
        makeTask({
          category: "VACCINE",
          title: "PPR campaign",
          animal_tag: "G-003",
        }),
      ),
    ).toEqual({ disease_target: "PPR campaign" });
  });

  it("gives no hint for a vaccine duty whose title is only scaffolding", () => {
    expect(
      taskPrefill(
        makeTask({ category: "VACCINE", title: "[Supplier #2] Day 4:   " }),
      ),
    ).toEqual({});
  });

  it.each([
    ["Review [old] PPR", "Review [old] PPR"],
    ["[Supplier #2]PPR", "PPR"],
    ["Reminder Day 4: PPR", "Reminder Day 4: PPR"],
    ["Day4:PPR", "PPR"],
    ["Day 40:PPR", "PPR"],
    ["Days 4-10:PPR", "PPR"],
    ["vaccinate PPR(live)", "PPR"],
    ["vaccinate PPR", "PPR"],
    ["ET + TT booster", "ET + TT pre-kidding"],
    ["  PPR campaign  ", "PPR campaign"],
  ])("preserves exact parser boundaries for %j", (title, target) => {
    expect(taskPrefill(makeTask({ category: "VACCINE", title }))).toEqual({
      disease_target: target,
    });
  });

  it("strips an exact animal-tag suffix before using a free-text vaccine title", () => {
    expect(
      taskPrefill(
        makeTask({
          category: "VACCINE",
          title: "PPR booster: G-003",
          animal_tag: "G-003",
        }),
      ),
    ).toEqual({ disease_target: "PPR booster" });
  });

  it("returns no empty target for a whitespace-only vaccine title", () => {
    expect(taskPrefill(makeTask({ category: "VACCINE", title: "   " }))).toEqual({});
  });

  it("non-health categories give no hints", () => {
    expect(taskPrefill(makeTask({ category: "CLEANING", title: "Scrub feeders" }))).toEqual(
      {},
    );
  });

  it.each([
    ["Vaccinate  (PPR live viral, SC)", "(PPR live viral, SC)"],
    ["vaccinate\n\nGoat Pox", "Goat Pox"],
  ])("reads past the whole gap after \"vaccinate\", not just one space, in %j", (title, target) => {
    expect(taskPrefill(makeTask({ category: "VACCINE", title }))).toEqual({
      disease_target: target,
    });
  });

  it("gives a blank target for a duty that says \"vaccinate\" and nothing else", () => {
    // The capture falls back onto the padding here, and the record form only
    // prefills a truthy hint - so this has to come back blank rather than as a
    // lone space typed into the disease field.
    expect(
      taskPrefill(makeTask({ category: "VACCINE", title: "[B #2] Day 10: vaccinate  " })),
    ).toEqual({ disease_target: "" });
  });
});
