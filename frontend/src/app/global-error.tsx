"use client";

/**
 * Root global-error boundary — the last resort when even the layout's
 * error boundary (src/app/error.tsx) fails to render. Next requires this
 * file to own its <html>/<body> because the root layout is not mounted in
 * that state. Same tone and contract as error.tsx: log once, explain, offer
 * a retry.
 */

import { TriangleAlert } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";

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

  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
          <span className="inline-flex size-10 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
            <TriangleAlert className="size-5" aria-hidden="true" />
          </span>
          <p className="text-sm font-medium text-destructive">
            Something went wrong. Please try again.
          </p>
          <Button variant="outline" onClick={reset}>
            Try again
          </Button>
        </div>
      </body>
    </html>
  );
}
