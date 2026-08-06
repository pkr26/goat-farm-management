"use client";

/**
 * Authenticated app shell: header (brand, farm, user, logout) and the
 * permission-filtered nav — parity with v1's base.html.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { usePermissions } from "@/lib/use-permissions";

const NAV_ITEMS: { href: string; label: string; perm: string }[] = [
  { href: "/dashboard", label: "Dashboard", perm: "dashboard.view" },
  { href: "/animals", label: "Animals", perm: "animals.view" },
  { href: "/buckets", label: "Buckets", perm: "buckets.view" },
  { href: "/breeding", label: "Breeding", perm: "breeding.view" },
  { href: "/kidding", label: "Kidding", perm: "kidding.view" },
  { href: "/health", label: "Health", perm: "health.view" },
  { href: "/purchases", label: "Purchases", perm: "purchases.view" },
  { href: "/feeding", label: "Feeding", perm: "feeding.view" },
  { href: "/tasks", label: "Tasks", perm: "tasks.view" },
  { href: "/finance", label: "Finance", perm: "finance.view" },
  { href: "/reports", label: "Reports", perm: "reports.view" },
  { href: "/team", label: "Team", perm: "team.manage" },
];

export default function AppLayout({ children }: { children: ReactNode }) {
  const { user, farms, farmId, loading, signOut } = useAuth();
  const { can, loading: permsLoading } = usePermissions();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user && !farmId) router.replace("/farm-select");
  }, [loading, user, farmId, router]);

  if (loading || !user || !farmId) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-muted-foreground">Loading…</p>
      </main>
    );
  }

  const farm = farms.find((f) => f.id === farmId);

  return (
    <div className="min-h-screen">
      <header className="border-b">
        <div className="flex items-center gap-4 px-4 py-3">
          <Link href="/dashboard" className="text-lg font-semibold">
            🐐 GoatFarm
          </Link>
          {farm && <span className="font-medium">{farm.name}</span>}
          <Link href="/farm-select" className="text-sm text-primary underline">
            switch farm
          </Link>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-sm text-muted-foreground">
              {user.name ?? user.email}
            </span>
            <Button variant="outline" size="sm" onClick={() => void signOut()}>
              Logout
            </Button>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-4 pb-2">
          {!permsLoading &&
            NAV_ITEMS.filter((item) => can(item.perm)).map((item) => {
              const active = pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                    active
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
        </nav>
      </header>
      <main className="p-4">{children}</main>
    </div>
  );
}
