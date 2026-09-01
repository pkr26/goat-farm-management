"use client";

/** Entry hub: redirect the user based on auth state. */

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { useAuth } from "@/lib/auth-context";
import { firstPermittedPath } from "@/lib/permission-navigation";
import { usePermissions } from "@/lib/use-permissions";


export default function RootPage() {
  const { user, farmId, loading } = useAuth();
  const permissions = usePermissions();
  const router = useRouter();
  const landingPath = firstPermittedPath(permissions.can);
  const dispatchedRedirect = useRef<string | null>(null);

  useEffect(() => {
    const destination = loading
      ? null
      : !user
        ? "/login"
        : !farmId || permissions.isError
          ? "/farm-select"
          : !permissions.loading
            ? landingPath
            : null;
    if (destination === null) {
      dispatchedRedirect.current = null;
      return;
    }
    if (dispatchedRedirect.current === destination) return;
    dispatchedRedirect.current = destination;
    router.replace(destination);
  }, [loading, user, farmId, permissions.loading, permissions.isError, landingPath, router]);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3">
      <Loader2 className="size-6 animate-spin text-primary" />
      <p role="status" aria-live="polite" className="text-muted-foreground">Loading…</p>
    </main>
  );
}
