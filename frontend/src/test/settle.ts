/**
 * Settle-window helper for effect/debounce assertions.
 *
 * Use `await settle()` when a test must wait out a REAL timer window
 * (debounce intervals, URL-commit timers) before asserting that something
 * did NOT happen — `expect(calls).toBe(0)` or `queryBy(...).not.toBe...`.
 * `waitFor` cannot express that: it passes trivially at t=0 for absence
 * assertions, so the fixed window IS the assertion's meaning.
 *
 * For anything that SHOULD eventually happen, never use this — use
 * `waitFor`/`findBy*`, which poll instead of sleeping.
 *
 * The default window (50ms) matches the debounce timers the app uses; tests
 * that need a longer window pass an explicit duration with a comment saying
 * which timer they are waiting out.
 */
export function settle(ms = 50): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
