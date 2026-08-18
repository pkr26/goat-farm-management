"use client";

/** v1's /animals/new form is a dialog on /animals now, so redirect there —
 *  ?new=1 makes the animals page auto-open the create dialog. */

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

export default function AnimalsNewRedirect() {
  const router = useRouter();
  const redirectStarted = useRef(false);

  useEffect(() => {
    // React Strict Mode replays mount effects in development. Dispatch this
    // query-param redirect once per mounted shim so two identical Next
    // transitions cannot race each other.
    if (redirectStarted.current) return;
    redirectStarted.current = true;
    router.replace("/animals?new=1");
  }, [router]);

  return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
}
