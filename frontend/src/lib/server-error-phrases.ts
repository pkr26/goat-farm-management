/**
 * Server-error phrase mapping (ITEM 5, 2026-09-21 playbook).
 *
 * The backend has no i18n contract yet; it answers with fixed English
 * detail strings. The highest-stakes ones — validation, denials, rate
 * limits, session expiry — are pinned here and render through the language
 * catalog, so a Telugu worker sees Telugu where it matters most. Anything
 * unmapped passes the server text through unchanged (today's behavior).
 *
 * The table keys on the backend's EXACT strings. When a backend message
 * changes wording, its mapping breaks loudly in the suite below — update
 * the entry and both catalogs in the same change.
 */

import type { TFn } from "@/lib/i18n";

// English detail (byte-exact) → catalog key.
const PHRASES: Record<string, Parameters<TFn>[0]> = {
  "Invalid PIN.": "serverErrors.invalidPin",
  "This duty is not assigned to you": "serverErrors.notAssigned",
  "Task is not pending": "serverErrors.taskNotPending",
  "Use the linked form to complete this duty": "serverErrors.useLinkedForm",
  "This duty is not due yet": "serverErrors.notDueYet",
  "Current password is incorrect.": "serverErrors.currentPasswordIncorrect",
  "Already reviewed — refresh to see the current status.": "serverErrors.alreadyReviewed",
  "Idempotency-Key was already used with a different request":
    "serverErrors.idempotencyConflict",
};

// Status-shaped rules where the exact string is less stable than the shape.
const STATUS_RULES: Array<{
  status: number;
  test: RegExp;
  key: Parameters<TFn>[0];
}> = [
  { status: 401, test: /session|token|sign in|Invalid or expired/i, key: "serverErrors.sessionExpired" },
  { status: 403, test: /owner|role|permission|not allow/i, key: "serverErrors.permissionDenied" },
  { status: 429, test: /.*/, key: "serverErrors.tooManyAttempts" },
];

/** Translate one backend error into the active language, or pass it through. */
export function mapServerError(
  t: TFn,
  detail: string,
  status?: number,
): string {
  const exact = PHRASES[detail];
  if (exact !== undefined) return t(exact);
  if (status !== undefined) {
    for (const rule of STATUS_RULES) {
      if (rule.status === status && rule.test.test(detail)) return t(rule.key);
    }
  }
  return detail;
}
