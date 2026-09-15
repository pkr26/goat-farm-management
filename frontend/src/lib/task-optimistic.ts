/**
 * Optimistic status updates for the duty board (TanStack Query).
 *
 * Completing a duty on a rural connection should strike the row through
 * instantly: the patch is written into every cached /api/tasks board payload
 * before the request settles, and the returned rollback restores the exact
 * prior cache entries when the mutation fails (the caller then surfaces the
 * error and leaves the board untouched).
 */

import type { QueryClient } from "@tanstack/react-query";

import type { TaskOut, TaskTabsOut } from "@/api/generated/models";

/** The generated hook answers the axios-shaped {status, data} envelope. */
type TaskBoardResponse = { status: number; data: TaskTabsOut };

const BOARD_QUERY_KEY = ["/api/tasks"] as const;

export function applyOptimisticTaskPatch(
  queryClient: QueryClient,
  taskId: number,
  patch: Partial<TaskOut>,
): () => void {
  const snapshots = queryClient.getQueriesData<TaskBoardResponse>({
    queryKey: BOARD_QUERY_KEY,
  });
  queryClient.setQueriesData<TaskBoardResponse>({ queryKey: BOARD_QUERY_KEY }, (old) => {
    if (!old || old.status !== 200) return old;
    const patchList = (tasks: TaskOut[]) =>
      tasks.map((task) => (task.id === taskId ? { ...task, ...patch } : task));
    return {
      ...old,
      data: {
        ...old.data,
        today: patchList(old.data.today),
        overdue: patchList(old.data.overdue),
        upcoming: patchList(old.data.upcoming),
        awaiting: patchList(old.data.awaiting),
        completed: patchList(old.data.completed),
      },
    };
  });
  return () => {
    for (const [key, data] of snapshots) {
      queryClient.setQueryData(key, data);
    }
  };
}
