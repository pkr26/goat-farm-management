"use client";

/**
 * Root global-error boundary — the last resort when even the layout's
 * error boundary (src/app/error.tsx) fails to render. Next requires this
 * file to own its <html>/<body> because the root layout is not mounted in
 * that state. Same tone and contract as error.tsx: log once, explain, offer
 * a retry. Copy and <html lang> resolve from the stored language without
 * the provider — by definition the provider tree is the thing that crashed
 * (2026-09-28 audit, I3).
 */

import { TriangleAlert } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { readStoredLanguage, translate } from "@/lib/i18n";

export default function GlobalError({
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
    <html lang={language}>
      <body>
        <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
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
      </body>
    </html>
  );
}
