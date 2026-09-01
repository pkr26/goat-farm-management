"use client";

import { Button } from "@/components/ui/button";

/**
 * Shared dead-end-free permissions failure: announces as an alert and
 * offers an in-place retry instead of telling the operator to refresh.
 * Copy is kept identical to the historical string — tests and muscle
 * memory depend on it.
 */
export function PermissionsError({ onRetry }: { onRetry: () => void }) {
  return (
    <div role="alert" className="space-y-3">
      <p className="text-sm text-destructive">
        Could not load your permissions — refresh the page to try again.
      </p>
      <Button type="button" variant="outline" size="sm" onClick={onRetry}>
        Retry permissions
      </Button>
    </div>
  );
}
