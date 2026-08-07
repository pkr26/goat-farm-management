"use client";

/** Backend task action_urls still point at v1's /kidding/new?breeding_id=…
 *  path. The new UI records kiddings in a dialog on /kidding, so redirect there,
 *  preserving the query string — the kidding page auto-opens the dialog. */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function KiddingNewRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace(`/kidding${window.location.search}`);
  }, [router]);

  return <p className="py-10 text-center text-muted-foreground">Loading…</p>;
}
