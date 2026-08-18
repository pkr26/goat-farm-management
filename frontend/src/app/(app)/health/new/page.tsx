"use client";

/** Backend task action_urls still point at v1's /health/new?task_id=… path.
 *  The new UI records events in a dialog on /health, so redirect there,
 *  preserving the query string — the health page auto-opens the dialog. */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

function HealthNewRedirectContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const search = searchParams.toString();

  useEffect(() => {
    router.replace(`/health${search ? `?${search}` : ""}`);
  }, [router, search]);

  return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
}

export default function HealthNewRedirect() {
  return (
    <Suspense fallback={<p className="py-10 text-center text-muted-foreground">Loading…</p>}>
      <HealthNewRedirectContent />
    </Suspense>
  );
}
