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
// ERROR_CODES_BY_STATUS (401/403/422/429) and the RFC 9457-style 409
// conflict families (2026-09-29), all stable across detail-wording changes.
const CODES: Record<string, Parameters<TFn>[0]> = {
  UNAUTHENTICATED: "serverErrors.sessionExpired",
  PERMISSION_DENIED: "serverErrors.permissionDenied",
  VALIDATION_ERROR: "serverErrors.validationRejected",
  RATE_LIMITED: "serverErrors.tooManyAttempts",
  // 409 conflict families: one status, several failure classes — the backend
  // now attaches the family code so clients never parse English prose.
  LIFECYCLE_CONFLICT: "serverErrors.lifecycleConflict",
  STANDING_QUOTA_CONFLICT: "serverErrors.quotaExceeded",
  STALE_STATE_CONFLICT: "serverErrors.staleState",
};

// English detail (byte-exact) → catalog key.
const PHRASES: Record<string, Parameters<TFn>[0]> = {
  "Invalid PIN.": "serverErrors.invalidPin",
  "This duty is not assigned to you": "serverErrors.notAssigned",
  "Task is not pending": "serverErrors.taskNotPending",
  "Use the linked form to complete this duty": "serverErrors.useLinkedForm",
  "This duty is not due yet": "serverErrors.notDueYet",
  "Current password is incorrect.": "serverErrors.currentPasswordIncorrect",
  "Idempotency-Key was already used with a different request":
    "serverErrors.idempotencyConflict",
  // Wrong-lifecycle answer the kidding router has answered with 409 since
  // the A3 convention; the byte-exact detail has no tag interpolation, so a
  // pinned phrase covers it (2026-09-29 audit).
  "Kidding requires a confirmed pregnancy": "serverErrors.kiddingNeedsPregnancy",
};

// Status-shaped rules where the exact string is less stable than the shape.
// The backend code (checked first) already covers the common shapes; these
// catch code-less responses (proxies, older deploys) with matching text.
// 409 carries no machine code (the backend reserves codes for
// 401/403/422/429), and since the A3 convention made every wrong-lifecycle
// answer a 409, the RECURRING lifecycle shapes get targeted rules — never a
// blanket rule: a specific 409 ("Owned farms block account deletion.") is
// more actionable than any generic conflict sentence, so unmapped specifics
// still pass through (2026-09-29 audit, Wave-3 completeness).
const STATUS_RULES: Array<{
  status: number;
  test: RegExp;
  key: Parameters<TFn>[0];
}> = [
  { status: 401, test: /session|token|sign in|Invalid or expired/i, key: "serverErrors.sessionExpired" },
  { status: 403, test: /owner|role|permission|not allow/i, key: "serverErrors.permissionDenied" },
  // Terminal-status replays interpolate the animal's tag, so a byte-exact
  // phrase cannot pin them: "D-123 is already sold." — match the shape.
  { status: 409, test: /is already (sold|dead|culled)/i, key: "serverErrors.alreadyTerminal" },
  { status: 429, test: /.*/, key: "serverErrors.tooManyAttempts" },
];

/** Translate one backend error into the active language, or pass it through.
 * Resolution order: byte-pinned PHRASES first (a specific, actionable
 * sentence like "Task is not pending" always beats its family generic),
 * then the machine-readable code (family generic — stable across wording),
 * then status-shaped rules, then passthrough of the specific detail. */
export function mapServerError(
  t: TFn,
  detail: string,
  status?: number,
  code?: string | null,
): string {
  const exact = PHRASES[detail];
  if (exact !== undefined) return t(exact);
  if (code !== undefined && code !== null) {
    const byCode = CODES[code];
    if (byCode !== undefined) return t(byCode);
  }
  if (status !== undefined) {
    for (const rule of STATUS_RULES) {
      if (rule.status === status && rule.test.test(detail)) return t(rule.key);
    }
  }
  return detail;
}
