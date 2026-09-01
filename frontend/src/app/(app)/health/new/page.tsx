"use client";

/** Backend task action_urls still point at v1's /health/new?task_id=… path.
 *  The new UI records events in a dialog on /health, so redirect there,
 *  preserving the query string — the health page auto-opens the dialog. */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";

import { InlineLoading } from "@/components/skeletons";

function HealthNewRedirectContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const search = searchParams.toString();
  const dispatchedUrl = useRef<string | null>(null);

  useEffect(() => {
    const url = `/health${search ? `?${search}` : ""}`;
    if (dispatchedUrl.current === url) return;
    dispatchedUrl.current = url;
    router.replace(url);
  }, [router, search]);

  // A redirect has no page structure to mirror — the shared inline spinner
  // beats a bare "Loading…" paragraph.
  return <InlineLoading>Opening the health event form…</InlineLoading>;
}

export default function HealthNewRedirect() {
  return (
    <Suspense fallback={<InlineLoading>Opening the health event form…</InlineLoading>}>
      <HealthNewRedirectContent />
    </Suspense>
  );
}
