"use client";

/** Backend task action_urls still point at v1's /breeding/{id}/ultrasound
 *  path. The new UI records ultrasound results in a dialog on /breeding, so
 *  redirect there — the breeding page auto-opens the dialog for the record.
 *  The incoming query string is preserved (a worker-tablet deep link carries
 *  returnTo=/worker so closing the dialog returns to the board), mirroring
 *  the /kidding/new shim. */

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";

import { useT } from "@/lib/i18n";
import { PageHeader } from "@/components/page-header";
import { PageSkeleton } from "@/components/skeletons";

function BreedingUltrasoundRedirectContent() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const t = useT();
  // Read the query from the router, not window.location, and key the effect
  // on the composed value (same reasoning as the /kidding/new shim: a
  // query-only navigation reuses this component, so a stale effect would
  // drop a second link followed before the first replace committed).
  const search = new URLSearchParams(searchParams.toString());
  search.set("ultrasound_id", params.id);
  const target = `/breeding?${search.toString()}`;
  const dispatchedUrl = useRef<string | null>(null);

  useEffect(() => {
    if (dispatchedUrl.current === target) return;
    dispatchedUrl.current = target;
    router.replace(target);
  }, [router, target]);

  // The redirect is near-instant, but a bare "Loading…" line reads as a broken
  // app on a slow connection — mirror the destination page's shape instead.
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("breeding.title")}
        description={t("breeding.ultrasoundRedirect.description")}
      />
      <div role="status" aria-live="polite">
        <span className="sr-only">{t("breeding.ultrasoundRedirect.loading")}</span>
        <PageSkeleton cards={2} />
      </div>
    </div>
  );
}

function UltrasoundRedirectFallback() {
  const t = useT();
  return (
    <div role="status" aria-live="polite">
      <span className="sr-only">{t("breeding.ultrasoundRedirect.loading")}</span>
      <PageSkeleton cards={2} />
    </div>
  );
}

export default function BreedingUltrasoundRedirect() {
  return (
    <Suspense fallback={<UltrasoundRedirectFallback />}>
      <BreedingUltrasoundRedirectContent />
    </Suspense>
  );
}
