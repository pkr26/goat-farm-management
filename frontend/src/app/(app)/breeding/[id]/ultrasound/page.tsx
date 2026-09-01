"use client";

/** Backend task action_urls still point at v1's /breeding/{id}/ultrasound
 *  path. The new UI records ultrasound results in a dialog on /breeding, so
 *  redirect there — the breeding page auto-opens the dialog for the record. */

import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { PageHeader } from "@/components/page-header";
import { PageSkeleton } from "@/components/skeletons";

export default function BreedingUltrasoundRedirect() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const dispatchedUrl = useRef<string | null>(null);

  useEffect(() => {
    const search = new URLSearchParams({ ultrasound_id: params.id });
    const url = `/breeding?${search.toString()}`;
    if (dispatchedUrl.current === url) return;
    dispatchedUrl.current = url;
    router.replace(url);
  }, [router, params.id]);

  // The redirect is near-instant, but a bare "Loading…" line reads as a broken
  // app on a slow connection — mirror the destination page's shape instead.
  return (
    <div className="space-y-6">
      <PageHeader
        title="Breeding"
        description="Opening the ultrasound check for this breeding record…"
      />
      <div role="status" aria-live="polite">
        <span className="sr-only">Loading the ultrasound check…</span>
        <PageSkeleton cards={2} />
      </div>
    </div>
  );
}
