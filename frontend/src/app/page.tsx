"use client";

/** Entry hub: redirect the user based on auth state. */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth-context";

export default function RootPage() {
  const { user, farmId, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    if (!user) router.replace("/login");
    else if (!farmId) router.replace("/farm-select");
    else router.replace("/dashboard");
  }, [loading, user, farmId, router]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      <p className="text-muted-foreground">Loading…</p>
    </main>
  );
}
