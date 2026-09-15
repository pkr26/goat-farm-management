/**
 * applyOptimisticTaskPatch: the patch lands in every cached /api/tasks board
 * (any params variant), and the rollback restores the exact prior entries.
 */

import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import type { TaskOut, TaskTabsOut } from "@/api/generated/models";

import { applyOptimisticTaskPatch } from "./task-optimistic";

function task(overrides: Partial<TaskOut>): TaskOut {
  return {
    id: 1,
    title: "Task",
    title_key: null,
    title_args: {},
    due_date: "2026-09-14",
    status: "PENDING",
    category: "OTHER",
    auto_generated: false,
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
    action_url: null,
    ...overrides,
  };
}

function board(tasks: TaskOut[]): { status: number; data: TaskTabsOut } {
  return {
    status: 200,
    data: {
      today: tasks,
      overdue: [],
      upcoming: [],
      awaiting: [],
      completed: [],
      today_total: tasks.length,
      today_offset: 0,
      overdue_total: 0,
      overdue_offset: 0,
      upcoming_total: 0,
      upcoming_offset: 0,
      awaiting_total: 0,
      awaiting_offset: 0,
      active_limit: 50,
      completed_total: 0,
      completed_limit: 50,
      completed_offset: 0,
    },
  };
}

describe("applyOptimisticTaskPatch", () => {
  it("patches the task in every tab bucket and rolls back to the snapshot", () => {
    const client = new QueryClient();
    const key = ["/api/tasks", { active_limit: 50 }];
    const original = board([task({ id: 7 }), task({ id: 8 })]);
    client.setQueryData(key, original);

    const rollback = applyOptimisticTaskPatch(client, 7, { status: "DONE" });

    const patched = client.getQueryData<typeof original>(key)!;
    expect(patched.data.today[0].status).toBe("DONE");
    expect(patched.data.today[1].status).toBe("PENDING");
    // Untouched board facts survive the patch.
    expect(patched.data.today_total).toBe(2);
    expect(patched.status).toBe(200);

    rollback();
    expect(client.getQueryData<typeof original>(key)!.data.today[0].status).toBe("PENDING");
  });

  it("leaves non-200 envelopes and boards without the task alone", () => {
    const client = new QueryClient();
    const errorKey = ["/api/tasks", { active_limit: 50, offset: 50 }];
    client.setQueryData(errorKey, { status: 500, data: null });
    const otherKey = ["/api/tasks", { active_limit: 50 }];
    const other = board([task({ id: 1 })]);
    client.setQueryData(otherKey, other);

    const rollback = applyOptimisticTaskPatch(client, 7, { status: "DONE" });
    expect(client.getQueryData(errorKey)).toEqual({ status: 500, data: null });
    expect(client.getQueryData<typeof other>(otherKey)!.data.today[0].status).toBe("PENDING");
    rollback();
  });
});
