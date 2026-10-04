/** Shared contract for durable worker actions and the legacy queue import. */

/** Kept stable so worker-outbox can recover writes from older deployments. */
export const OFFLINE_QUEUE_STORAGE_KEY = "goatfarm:offlineQueue:v1";

export type QueueScopes = { actorScope: string; farmScope: string };

/** Only these endpoints support replay with the original Idempotency-Key. */
export function isOfflineQueueableMutation(path: string, method?: string): boolean {
  if ((method ?? "GET").toUpperCase() !== "POST") return false;
  return /^\/api\/tasks\/\d+\/(complete|skip)$/.test(path);
}
