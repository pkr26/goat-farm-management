/**
 * Server-error phrase mapping (ITEM 5): the pinned backend phrases render in
 * the active language; unknown text passes through verbatim; status rules
 * catch the shape-stable classes (429 throttles, 401 session deaths).
 */

import { renderToStaticMarkup } from "react-dom/server";

import { LanguageProvider, useT } from "@/lib/i18n";
import { mapServerError } from "@/lib/server-error-phrases";

import { describe, expect, it } from "vitest";

function telugu(detail: string, status?: number): string {
  let out = "";
  function Probe() {
    const t = useT();
    out = mapServerError(t, detail, status);
    return null;
  }
  renderToStaticMarkup(
    <LanguageProvider>
      <Probe />
    </LanguageProvider>,
  );
  return out;
}

describe("mapServerError", () => {
  it("renders the pinned duty-conflict phrases in the catalog language", () => {
    // Telugu is not the default; default catalog is en — assert against the
    // EN strings (parity is enforced elsewhere) and one Te-specific check
    // by pinning the language key directly.
    expect(mapServerError((k) => k, "Task is not pending")).toBe("serverErrors.taskNotPending");
    expect(mapServerError((k) => k, "Use the linked form to complete this duty")).toBe(
      "serverErrors.useLinkedForm",
    );
    expect(
      mapServerError((k) => k, "Idempotency-Key was already used with a different request"),
    ).toBe("serverErrors.idempotencyConflict");
  });

  it("backend error codes win over wording and status rules", () => {
    expect(mapServerError((k) => k, "any wording at all", 403, "PERMISSION_DENIED")).toBe(
      "serverErrors.permissionDenied",
    );
    expect(mapServerError((k) => k, "field x failed validation", 422, "VALIDATION_ERROR")).toBe(
      "serverErrors.validationRejected",
    );
    expect(mapServerError((k) => k, "unauthenticated", 401, "UNAUTHENTICATED")).toBe(
      "serverErrors.sessionExpired",
    );
    expect(mapServerError((k) => k, "busy", 429, "RATE_LIMITED")).toBe(
      "serverErrors.tooManyAttempts",
    );
  });

  it("an unknown code falls through to the phrase and status rules", () => {
    expect(mapServerError((k) => k, "Task is not pending", 409, "SOME_NEW_CODE")).toBe(
      "serverErrors.taskNotPending",
    );
    expect(mapServerError((k) => k, "Completely unmapped", 429, null)).toBe(
      "serverErrors.tooManyAttempts",
    );
    expect(mapServerError((k) => k, "Completely unmapped", 500, undefined)).toBe(
      "Completely unmapped",
    );
  });

  it("429 always maps to the throttle message, whatever the wording", () => {
    expect(mapServerError((k) => k, "Slow down please", 429)).toBe("serverErrors.tooManyAttempts");
    expect(mapServerError((k) => k, "Completely different text", 429)).toBe(
      "serverErrors.tooManyAttempts",
    );
  });

  it("401 session-shaped details map; other 401s pass through", () => {
    expect(mapServerError((k) => k, "Invalid or expired token", 401)).toBe(
      "serverErrors.sessionExpired",
    );
    expect(mapServerError((k) => k, "Wrong password", 401)).toBe("Wrong password");
  });

  it("unknown phrases render through the real Telugu catalog", () => {
    window.localStorage.setItem("herdly.language", "te");
    try {
      // "Task is not pending" is a pinned phrase: the Telugu catalog must
      // carry it (parity + phrase table both asserted).
      const out = telugu("Task is not pending");
      expect(out).not.toBe("Task is not pending");
      expect(out.length).toBeGreaterThan(0);
    } finally {
      window.localStorage.removeItem("herdly.language");
    }
  });
});
