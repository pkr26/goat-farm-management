"use client";

/** Backend task action_urls still point at v1's /breeding/{id}/ultrasound
 *  path. The new UI records ultrasound results in a dialog on /breeding, so
 *  redirect there — the breeding page auto-opens the dialog for the record. */

import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

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

  return <p role="status" aria-live="polite" className="py-10 text-center text-muted-foreground">Loading…</p>;
}
