"use client";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

/**
 * Shared dead-end-free permissions failure: announces as an alert and
 * offers an in-place retry instead of telling the operator to refresh.
 */
export function PermissionsError({ onRetry }: { onRetry: () => void }) {
  const t = useT();
  return (
    <div role="alert" className="space-y-3">
      <p className="text-sm text-destructive">{t("permissionsError.loadFailed")}</p>
      <Button type="button" variant="outline" size="sm" onClick={onRetry}>
        {t("permissionsError.retry")}
      </Button>
    </div>
  );
}
