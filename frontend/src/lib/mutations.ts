/** Shared mutation-error rendering: the write surfaces' catch blocks all
 * reduce an unknown failure to one displayable sentence. */

import { ApiError } from "@/lib/api-client";
import { mapServerError } from "@/lib/server-error-phrases";
import { useT } from "@/lib/i18n";

/** Server code/detail wins, translated when the backend attached one of the
 *  stable error codes (validation, denials, rate limits) or the detail is one
 *  of the pinned highest-stakes phrases (duty conflicts, …); the fallback is
 *  the caller's — translated where the surface is wired to the language
 *  catalog (`t("common.somethingWentWrong")`). Every write surface renders
 *  through this hook (2026-09-22: the legacy non-hook variant that returned
 *  raw English server text was retired). */
export function useMutationError(): (err: unknown, fallback?: string) => string {
  const t = useT();
  return (err: unknown, fallback = t("common.somethingWentWrong")) =>
    err instanceof ApiError
      ? mapServerError(t, err.detail, err.status, err.code)
      : fallback;
}
