/**
 * Return the requested browser storage, or null during SSR or when site data is
 * blocked. Accessing the storage property itself may throw SecurityError; callers
 * must handle an unavailable store.
 */
export function safeStorage(kind: "local" | "session"): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}
