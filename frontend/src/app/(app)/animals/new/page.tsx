"use client";

/** v1's /animals/new form is a dialog on /animals now, so redirect there —
 *  ?new=1 makes the animals page auto-open the create dialog. */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";

import { InlineLoading } from "@/components/skeletons";

function AnimalsNewRedirectContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectStarted = useRef(false);

  // Stryker disable ArrayDeclaration: the redirectStarted ref latch makes repeat runs inert, so a constant dep list cannot change the one-time redirect
  useEffect(() => {
    // React Strict Mode replays mount effects in development. Dispatch this
    // query-param redirect once per mounted shim so two identical Next
    // transitions cannot race each other.
    if (redirectStarted.current) return;
    redirectStarted.current = true;
    // Preserve the caller's query (a permission-aware returnTo, for example)
    // and merge new=1 into it, exactly like the sibling shims.
    const params = new URLSearchParams(searchParams.toString());
    params.set("new", "1");
    const query = params.toString();
    // Stryker disable next-line StringLiteral: params always carries new=1, so the bare-path fallback arm is unreachable
    router.replace(query ? `/animals?${query}` : "/animals");
    // Stryker restore StringLiteral
  }, [router, searchParams]);
  // Stryker restore ArrayDeclaration

  // A redirect has no page structure to mirror — the shared inline spinner
  // beats a bare "Loading…" paragraph.
  return <InlineLoading />;
}

export default function AnimalsNewRedirect() {
  // useSearchParams() suspends during static prerendering; the shim's own
  // spinner doubles as the fallback so the boundary never flashes blank.
  return (
    <Suspense fallback={<InlineLoading />}>
      <AnimalsNewRedirectContent />
    </Suspense>
  );
}
