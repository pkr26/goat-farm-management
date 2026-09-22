/**
 * Server-error mapping (ITEM 5, 2026-09-21 playbook).
 *
 * The backend attaches a machine-readable `code` to the four highest-stakes
 * statuses — 401 UNAUTHENTICATED, 403 PERMISSION_DENIED, 422
 * VALIDATION_ERROR, 429 RATE_LIMITED — and those codes render through the
 * language catalog here, so a Telugu worker sees Telugu where it matters
 * most regardless of the English `detail` wording.
 *
 * Below that, the historically pinned byte-exact English phrases keep
 * mapping specific domain sentences (duty conflicts, PIN errors, …) that
 * have no status-level code; anything unmapped passes the server text
 * through unchanged. When a pinned backend message changes wording, its
 * mapping breaks loudly in the suite below — update the entry and both
 * catalogs in the same change.
 */

import type { TFn } from "@/lib/i18n";

// Backend error code → catalog key. Codes come from the backend's
// ERROR_CODES_BY_STATUS and are stable across detail-wording changes.
const CODES: Record<string, Parameters<TFn>[0]> = {
  UNAUTHENTICATED: "serverErrors.sessionExpired",
  PERMISSION_DENIED: "serverErrors.permissionDenied",
  VALIDATION_ERROR: "serverErrors.validationRejected",
  RATE_LIMITED: "serverErrors.tooManyAttempts",
};

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
// The backend code (checked first) already covers the common shapes; these
// catch code-less responses (proxies, older deploys) with matching text.
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
  code?: string | null,
): string {
  if (code !== undefined && code !== null) {
    const byCode = CODES[code];
    if (byCode !== undefined) return t(byCode);
  }
  const exact = PHRASES[detail];
  if (exact !== undefined) return t(exact);
  if (status !== undefined) {
    for (const rule of STATUS_RULES) {
      if (rule.status === status && rule.test.test(detail)) return t(rule.key);
    }
  }
  return detail;
}
