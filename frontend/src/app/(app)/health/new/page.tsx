"use client";

/** Backend task action_urls still point at v1's /health/new?task_id=… path.
 *  The new UI records events in a dialog on /health, so bounce over,
 *  preserving the query string — the health page auto-opens the dialog. */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function HealthNewRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace(`/health${window.location.search}`);
  }, [router]);

  return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
}
