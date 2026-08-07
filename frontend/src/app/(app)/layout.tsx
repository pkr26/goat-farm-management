"use client";

/**
 * Authenticated app shell: left sidebar (brand + permission-filtered grouped
 * nav) and a slim topbar (farm, theme, user, logout) — parity with v1's
 * base.html.
 */

import {
  Baby,
  Boxes,
  ChartColumn,
  ClipboardList,
  FlaskConical,
  HeartPulse,
  IndianRupee,
  LayoutDashboard,
  PawPrint,
  ShoppingCart,
  Stethoscope,
  Users,
  Wheat,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { useAuth } from "@/lib/auth-context";
import { usePermissions } from "@/lib/use-permissions";

type NavItem = { href: string; label: string; perm: string; icon: LucideIcon };

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Overview",
    items: [
      {
        href: "/dashboard",
        label: "Dashboard",
        perm: "dashboard.view",
        icon: LayoutDashboard,
      },
    ],
  },
  {
    label: "Herd",
    items: [
      { href: "/animals", label: "Animals", perm: "animals.view", icon: PawPrint },
      { href: "/buckets", label: "Buckets", perm: "buckets.view", icon: Boxes },
      {
        href: "/breeding",
        label: "Breeding",
        perm: "breeding.view",
        icon: HeartPulse,
      },
      { href: "/kidding", label: "Kidding", perm: "kidding.view", icon: Baby },
    ],
  },
  {
    label: "Health & Feed",
    items: [
      { href: "/health", label: "Health", perm: "health.view", icon: Stethoscope },
      { href: "/feeding", label: "Feeding", perm: "feeding.view", icon: Wheat },
    ],
  },
  {
    label: "Operations",
    items: [
      {
        href: "/purchases",
        label: "Purchases",
        perm: "purchases.view",
        icon: ShoppingCart,
      },
      { href: "/tasks", label: "Tasks", perm: "tasks.view", icon: ClipboardList },
    ],
  },
  {
    label: "Business",
    items: [
      { href: "/finance", label: "Finance", perm: "finance.view", icon: IndianRupee },
      {
        href: "/simulation",
        label: "Simulation",
        perm: "simulation.view",
        icon: FlaskConical,
      },
      { href: "/reports", label: "Reports", perm: "reports.view", icon: ChartColumn },
      { href: "/team", label: "Team", perm: "team.manage", icon: Users },
    ],
  },
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
  const visibleGroups = permsLoading
    ? []
    : NAV_GROUPS.map((group) => ({
        ...group,
        items: group.items.filter((item) => can(item.perm)),
      })).filter((group) => group.items.length > 0);

  return (
    <SidebarProvider>
      <Sidebar>
        <SidebarHeader className="p-4">
          <Link href="/dashboard" aria-label="GoatFarm dashboard">
            <Logo />
          </Link>
        </SidebarHeader>
        <SidebarContent>
          {visibleGroups.map((group) => (
            <SidebarGroup key={group.label}>
              <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {group.items.map((item) => (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        render={<Link href={item.href} />}
                        isActive={pathname.startsWith(item.href)}
                        tooltip={item.label}
                      >
                        <item.icon />
                        <span>{item.label}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          ))}
        </SidebarContent>
      </Sidebar>
      <SidebarInset>
        <header className="flex h-14 shrink-0 items-center gap-2 border-b bg-background px-4">
          <SidebarTrigger />
          {farm && (
            <span className="truncate text-sm font-medium">{farm.name}</span>
          )}
          <Link
            href="/farm-select"
            className="text-sm text-muted-foreground hover:text-primary"
          >
            switch farm
          </Link>
          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
            <span className="hidden text-sm text-muted-foreground sm:inline">
              {user.name ?? user.email}
            </span>
            <Button variant="outline" size="sm" onClick={() => void signOut()}>
              Logout
            </Button>
          </div>
        </header>
        <div className="flex-1 bg-muted/40">
          <div className="mx-auto w-full max-w-7xl px-4 py-6 md:px-6">
            {children}
          </div>
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
