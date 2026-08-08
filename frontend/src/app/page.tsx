"use client";

/** Entry hub: redirect the user based on auth state. */

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth-context";
import { firstPermittedPath } from "@/lib/permission-navigation";
import { usePermissions } from "@/lib/use-permissions";

export default function RootPage() {
  const { user, farmId, loading } = useAuth();
  const permissions = usePermissions();
  const router = useRouter();
  const landingPath = firstPermittedPath(permissions.can);

  useEffect(() => {
    if (loading) return;
    if (!user) router.replace("/login");
    else if (!farmId) router.replace("/farm-select");
    else if (permissions.isError) router.replace("/farm-select");
    else if (!permissions.loading) router.replace(landingPath);
  }, [loading, user, farmId, permissions.loading, permissions.isError, landingPath, router]);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3">
      <Loader2 className="size-6 animate-spin text-primary" />
      <p className="text-muted-foreground">Loading…</p>
    </main>
  );
}
