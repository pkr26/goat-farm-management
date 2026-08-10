import { describe, expect, it, vi } from "vitest";

import {
  permittedTaskActionPath,
  taskFormNotDueYet,
  taskSkipUnavailable,
  type TaskActionState,
} from "@/lib/task-action-access";

describe("permittedTaskActionPath", () => {
  it.each([
    ["/breeding/7/ultrasound", "breeding.manage"],
    ["/kidding/new?breeding_id=7", "kidding.manage"],
    ["/health/new?task_id=8", "health.manage"],
  ])("requires the target module permission for %s", (path, permission) => {
    expect(permittedTaskActionPath(path, () => false)).toBeNull();
    const can = vi.fn((candidate: string) => candidate === permission);
    expect(permittedTaskActionPath(path, can)).toBe(path);
    expect(can).toHaveBeenCalledWith(permission);
  });

  it("fails closed for external, malformed and unknown application paths", () => {
    const can = () => true;
    expect(permittedTaskActionPath("https://evil.example/health/new", can)).toBeNull();
    expect(permittedTaskActionPath("//evil.example/health/new", can)).toBeNull();
    expect(permittedTaskActionPath("/breeding/%2e%2e/finance", can)).toBeNull();
    expect(permittedTaskActionPath("/health/%2f../finance", can)).toBeNull();
    expect(permittedTaskActionPath("/animals/1", can)).toBeNull();
    expect(permittedTaskActionPath(null, can)).toBeNull();
  });
});

// REGRESSION — mirrors of backend api/tasks.py::skip and the linked-duty
// due-date guard in api/health.py::_record_event_mutation. Skip used to be
// offered for quarantine-gate and weaning duties (deterministic 409), and the
// "Open form" link for future VACCINE/DEWORMING duties whose write can only
// 409 until the due date.

function makeState(overrides: Partial<TaskActionState>): TaskActionState {
  return {
    category: "OTHER",
    auto_generated: false,
    animal_id: null,
    purchase_batch_id: null,
    due_date: "2026-08-10",
    ...overrides,
  };
}

describe("taskSkipUnavailable", () => {
  it("blocks skip for auto-generated batch-linked quarantine duties", () => {
    expect(
      taskSkipUnavailable(
        makeState({ category: "VACCINE", auto_generated: true, purchase_batch_id: 42 }),
      ),
    ).toBe(true);
    // Category is irrelevant: every generated batch-linked row is a gate.
    expect(
      taskSkipUnavailable(
        makeState({ category: "BUCKET_MOVE", auto_generated: true, purchase_batch_id: 42 }),
      ),
    ).toBe(true);
  });

  it("blocks skip for auto-generated animal-linked weaning duties", () => {
    expect(
      taskSkipUnavailable(
        makeState({ category: "WEANING", auto_generated: true, animal_id: 7 }),
      ),
    ).toBe(true);
  });

  it("keeps skip for duties the backend can accept", () => {
    // Manual duties are never gated, batch-linked or not.
    expect(taskSkipUnavailable(makeState({ purchase_batch_id: 42 }))).toBe(false);
    expect(taskSkipUnavailable(makeState({ category: "WEANING", animal_id: 7 }))).toBe(false);
    // A generated weaning row without a linked animal has no RECOVERY set to
    // strand, so the backend accepts the skip.
    expect(
      taskSkipUnavailable(makeState({ category: "WEANING", auto_generated: true })),
    ).toBe(false);
    // Animal-linked generated BUCKET_MOVE fails open: the pregnancy→DELIVERY
    // flavor is legitimately skippable and TaskOut carries no bucket state to
    // tell it apart from the postpartum RECOVERY flavor.
    expect(
      taskSkipUnavailable(
        makeState({ category: "BUCKET_MOVE", auto_generated: true, animal_id: 7 }),
      ),
    ).toBe(false);
    // Ordinary generated protocol duties (animal-linked vaccine, …) skip fine.
    expect(
      taskSkipUnavailable(
        makeState({ category: "VACCINE", auto_generated: true, animal_id: 7 }),
      ),
    ).toBe(false);
  });
});

describe("taskFormNotDueYet", () => {
  const today = "2026-08-10";

  it("locks the health form of future VACCINE/DEWORMING duties", () => {
    for (const category of ["VACCINE", "DEWORMING"] as const) {
      expect(
        taskFormNotDueYet(makeState({ category, due_date: "2026-08-11" }), today),
      ).toBe(true);
      // Due today (or overdue) unlocks the form.
      expect(
        taskFormNotDueYet(makeState({ category, due_date: today }), today),
      ).toBe(false);
      expect(
        taskFormNotDueYet(makeState({ category, due_date: "2026-08-09" }), today),
      ).toBe(false);
    }
  });

  it("never locks ultrasound/kidding forms — early recording is legitimate", () => {
    for (const category of ["ULTRASOUND", "KIDDING_DUE"] as const) {
      expect(
        taskFormNotDueYet(makeState({ category, due_date: "2026-08-11" }), today),
      ).toBe(false);
    }
  });
});
