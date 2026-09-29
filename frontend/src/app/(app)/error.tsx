"use client";

/** Route-level error boundary for the app shell — same tone as the pages'
 *  inline "Could not load …" states, plus a retry. Copy resolves from the
 *  stored language without the provider — this boundary must render even
 *  when the provider tree crashed (2026-09-28 audit, I3). */

import { TriangleAlert } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { readStoredLanguage, translate } from "@/lib/i18n";

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  const language = readStoredLanguage();

  return (
    <div className="space-y-3 py-10 text-center">
      <span className="inline-flex size-10 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
        <TriangleAlert className="size-5" aria-hidden="true" />
      </span>
      <p className="text-sm font-medium text-destructive">
        {translate(language, "error.boundary.title")}
      </p>
      <Button variant="outline" onClick={reset}>
        {translate(language, "error.boundary.retry")}
      </Button>
    </div>
  );
}
