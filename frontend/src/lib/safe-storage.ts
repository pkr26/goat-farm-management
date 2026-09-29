/**
 * The single guarded Web-Storage accessor (2026-09-28 audit: the same
 * fail-closed guard had been copied privately into auth-context, the i18n
 * provider, idempotent-request, the offline queue and the worker layout).
 *
 * Site data can be blocked for the origin — reading `window.localStorage`
 * itself throws SecurityError — or the realm can be missing entirely (SSR).
 * Every caller treats null as "nothing persisted": degrade, never throw.
 */
export function safeStorage(kind: "local" | "session"): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}
