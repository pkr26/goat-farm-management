/**
 * Optimistic status updates for the duty board (TanStack Query).
 *
 * Completing a duty on a rural connection should strike the row through
 * instantly: the patch is written into every cached /api/tasks board payload
 * before the request settles, and the returned rollback restores the exact
 * prior row when the mutation fails (the caller then surfaces the error and
 * leaves the board untouched).
 *
 * The rollback is scoped to the ONE duty that was patched. Restoring
 * whole-board snapshots let a concurrent mutation's failure rollback clobber
 * another's optimistic strike-through: duty A's rollback restored A's
 * pre-patch snapshot, which predated duty B's patch, un-striking B while B's
 * write was still queued or in flight.
 */

import type { QueryClient } from "@tanstack/react-query";

import type { TaskOut, TaskTabsOut } from "@/api/generated/models";

/** The generated hook answers the axios-shaped {status, data} envelope. */
type TaskBoardResponse = { status: number; data: TaskTabsOut };

const BOARD_QUERY_KEY = ["/api/tasks"] as const;

const BOARD_TABS = ["today", "overdue", "upcoming", "awaiting", "completed"] as const;

function findTaskInBoard(
  board: TaskBoardResponse | undefined,
  taskId: number,
): TaskOut | undefined {
  if (!board || board.status !== 200) return undefined;
  for (const tab of BOARD_TABS) {
    const found = board.data[tab].find((task) => task.id === taskId);
    if (found) return found;
  }
  return undefined;
}

function replaceTaskInBoard(data: TaskTabsOut, replacement: TaskOut): TaskTabsOut {
  const replace = (tasks: TaskOut[]) =>
    tasks.map((task) => (task.id === replacement.id ? replacement : task));
  return {
    ...data,
    today: replace(data.today),
    overdue: replace(data.overdue),
    upcoming: replace(data.upcoming),
    awaiting: replace(data.awaiting),
    completed: replace(data.completed),
  };
}

/** Does the row still carry every value this patch wrote? A refetch may have
 * replaced the board with server truth between patch and rollback; that
 * truth outranks the snapshot. */
function carriesPatch(task: TaskOut, patch: Partial<TaskOut>): boolean {
  return Object.entries(patch).every(
    ([field, value]) => task[field as keyof TaskOut] === value,
  );
}

export function applyOptimisticTaskPatch(
  queryClient: QueryClient,
  taskId: number,
  patch: Partial<TaskOut>,
): () => void {
  // Per-board snapshot of ONLY the patched row's prior state: the rollback
  // can then restore this duty without touching any other row's current
  // (possibly someone else's optimistic) value.
  const boards = queryClient.getQueriesData<TaskBoardResponse>({
    queryKey: BOARD_QUERY_KEY,
  });
  const beforeByBoard = new Map<readonly unknown[], TaskOut | undefined>();
  for (const [key, board] of boards) {
    beforeByBoard.set(key, findTaskInBoard(board, taskId));
  }
  queryClient.setQueriesData<TaskBoardResponse>({ queryKey: BOARD_QUERY_KEY }, (old) => {
    if (!old || old.status !== 200) return old;
    const current = findTaskInBoard(old, taskId);
    if (current === undefined) return old;
    return { ...old, data: replaceTaskInBoard(old.data, { ...current, ...patch }) };
  });
  return () => {
    for (const [key, before] of beforeByBoard) {
      // A board that never held the duty was never patched — nothing to
      // restore (and no new array identity to hand React for free).
      if (before === undefined) continue;
      queryClient.setQueryData<TaskBoardResponse>(key, (old) => {
        if (!old || old.status !== 200) return old;
        const current = findTaskInBoard(old, taskId);
        if (current === undefined || !carriesPatch(current, patch)) return old;
        return { ...old, data: replaceTaskInBoard(old.data, before) };
      });
    }
  };
}
