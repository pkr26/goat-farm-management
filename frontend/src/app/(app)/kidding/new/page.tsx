"use client";

/** Backend task action_urls still point at v1's /kidding/new?breeding_id=…
 *  path. The new UI records kiddings in a dialog on /kidding, so redirect there,
 *  preserving the query string — the kidding page auto-opens the dialog. */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

function KiddingNewRedirectContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Read the query from the router, not window.location, and key the effect on
  // it. A query-only navigation reuses this component instead of remounting,
  // so with a `[router]` dependency the effect fires once per mount: a second
  // /kidding/new?breeding_id=… link followed before the first replace committed
  // would be silently dropped, landing the operator on the PREVIOUS doe's
  // kidding dialog. The sibling /breeding/[id]/ultrasound shim already keys on
  // its changing value for the same reason.
  const search = searchParams.toString();

  useEffect(() => {
    router.replace(`/kidding${search ? `?${search}` : ""}`);
  }, [router, search]);

  return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
}

export default function KiddingNewRedirect() {
  return (
    <Suspense fallback={<p className="py-10 text-center text-muted-foreground">Loading…</p>}>
      <KiddingNewRedirectContent />
    </Suspense>
  );
}
