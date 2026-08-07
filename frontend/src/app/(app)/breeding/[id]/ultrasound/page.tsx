"use client";

/** Backend task action_urls still point at v1's /breeding/{id}/ultrasound
 *  path. The new UI records ultrasound results in a dialog on /breeding, so
 *  bounce over — the breeding page auto-opens the dialog for the record. */

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

export default function BreedingUltrasoundRedirect() {
  const router = useRouter();
  const params = useParams<{ id: string }>();

  useEffect(() => {
    router.replace(`/breeding?ultrasound_id=${params.id}`);
  }, [router, params.id]);

  return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
}
