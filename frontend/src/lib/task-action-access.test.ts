import { describe, expect, it, vi } from "vitest";

import {
  permittedTaskActionPath,
  taskFormNotDueYet,
  taskSkipUnavailable,
  type TaskActionState,
} from "@/lib/task-action-access";

describe("permittedTaskActionPath", () => {
  it.each([
    ["/breeding", "breeding.manage"],
    ["/breeding/7/ultrasound", "breeding.manage"],
    ["/kidding", "kidding.manage"],
    ["/kidding/new?breeding_id=7", "kidding.manage"],
    ["/health", "health.manage"],
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

  // safeAppPath only rejects "%" inside the *pathname*, so an encoded
  // traversal/separator parked in the query or hash reaches this function
  // intact. It must still fail closed: the value is echoed back into a link
  // whose target module was authorized from the pathname alone, and the
  // encoded bytes survive into whatever the destination form does with them.
  it.each([
    "/health/new?returnTo=%2e%2e/finance",
    "/health/new?returnTo=%2E%2E/finance",
    "/health/new?next=%2ffinance",
    "/health/new?next=%5cfinance",
    "/breeding/7?returnTo=%2e%2e",
    "/kidding/new#%2e%2e",
  ])("rejects an encoded traversal or separator outside the pathname (%j)", (path) => {
    expect(permittedTaskActionPath(path, () => true)).toBeNull();
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

  it("blocks skip for auto-generated animal-linked BUCKET_MOVE duties", () => {
    // Mirrors the WEANING case above: the backend's postpartum-RECOVERY guard
    // (api/tasks.py::skip) covers both categories, and TaskOut carries no
    // bucket state to separate this from the legitimately-skippable
    // pregnancy→DELIVERY flavor, so it fails closed like WEANING does.
    expect(
      taskSkipUnavailable(
        makeState({ category: "BUCKET_MOVE", auto_generated: true, animal_id: 7 }),
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
    // A generated BUCKET_MOVE row without a linked animal has no RECOVERY set
    // to strand, so the backend accepts the skip.
    expect(
      taskSkipUnavailable(makeState({ category: "BUCKET_MOVE", auto_generated: true })),
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
