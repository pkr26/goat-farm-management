/**
 * Container/orchestrator liveness probe for the Next server itself.
 *
 * Deliberately local: no auth, no farm context, no backend dependency — a
 * health probe must not fail because the API is down. Filesystem routes take
 * precedence over the next.config.ts rewrites, so this handler shadows the
 * same-origin `/healthz` backend proxy (which `src/lib/backend-rewrites.ts`
 * still declares); the backend's own DB-aware probes remain reachable through
 * the Next origin at `/readyz`, whose rewrite is untouched. `force-dynamic`
 * plus `Cache-Control: no-store` keep the route out of every cache layer —
 * a stale "ok" must never mask a dead process.
 */
export const dynamic = "force-dynamic";

export function GET(): Response {
  return new Response("ok", {
    status: 200,
    headers: { "Cache-Control": "no-store" },
  });
}
