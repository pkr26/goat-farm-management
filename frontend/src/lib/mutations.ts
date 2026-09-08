/** Shared mutation-error rendering: the write surfaces' catch blocks all
 * reduce an unknown failure to one displayable sentence. */

import { ApiError } from "@/lib/api-client";

/** Server-authored detail wins (the backend has no i18n contract yet); the
 * fallback is the caller's — translated where the surface is wired to the
 * language catalog (`t("common.somethingWentWrong")`). */
export function mutationError(err: unknown, fallback = "Something went wrong"): string {
  return err instanceof ApiError ? err.detail : fallback;
}
