"use client";

/** Route-level error boundary for the app shell — same tone as the pages'
 *  inline "Could not load …" states, plus a retry. */

import { useEffect } from "react";

import { Button } from "@/components/ui/button";

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

  return (
    <div className="space-y-3 py-10 text-center">
      <p className="text-sm text-destructive">Something went wrong loading this page.</p>
      <Button variant="outline" onClick={reset}>
        Try again
      </Button>
    </div>
  );
}
