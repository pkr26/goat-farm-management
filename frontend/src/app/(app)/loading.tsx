"use client";

import { PageSkeleton } from "@/components/skeletons";
import { readStoredLanguage, translate } from "@/lib/i18n";

/** Route-level loading fallback for the app shell — mirrors the page
 *  layout (stat row + content cards) so navigation feels continuous.
 *  Client component resolving the stored language directly: the spoken
 *  announcement localizes even where the provider is unavailable. */
export default function Loading() {
  const language = readStoredLanguage();
  return (
    <div role="status" aria-live="polite" aria-label={translate(language, "error.loading")}>
      <span className="sr-only">{translate(language, "common.loading")}</span>
      <PageSkeleton stats={4} cards={2} />
    </div>
  );
}
