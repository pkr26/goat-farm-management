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
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, type ReactNode } from "react";

import { Logo } from "@/components/logo";
import { AccountDialog } from "@/components/account-dialog";
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
  useSidebar,
} from "@/components/ui/sidebar";
import { useAuth } from "@/lib/auth-context";
import { firstPermittedPath } from "@/lib/permission-navigation";
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

/** Match a module root or one of its nested routes, without treating a
 * similarly prefixed sibling (for example `/animals-archive`) as active. */
function isActiveRoute(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** The sidebar lives in its own component so it can consume `useSidebar()`,
 * which is only available *below* the SidebarProvider that the shell creates.
 * Below the mobile breakpoint the sidebar is a modal Sheet with a backdrop and
 * a body scroll lock; a client-side nav does not unmount the provider, so
 * without an explicit close the drawer stays over the page it just opened. */
function AppSidebar({
  groups,
  pathname,
  landingHref,
  landingLabel,
  permsError,
}: {
  groups: { label: string; items: NavItem[] }[];
  pathname: string;
  landingHref: string | null;
  landingLabel: string;
  permsError: boolean;
}) {
  const { isMobile, setOpenMobile } = useSidebar();
  const closeOnMobile = () => {
    if (isMobile) setOpenMobile(false);
  };

  return (
    <Sidebar>
      <SidebarHeader className="p-4">
        {landingHref ? (
          <Link
            href={landingHref}
            aria-label={`GoatFarm — go to ${landingLabel}`}
            onClick={closeOnMobile}
          >
            <Logo />
          </Link>
        ) : (
          <div aria-label="GoatFarm">
            <Logo />
          </div>
        )}
      </SidebarHeader>
      <SidebarContent>
        {/* A failed permissions call must not look like "no access" (7-6). */}
        {permsError && (
          <p className="px-4 py-2 text-sm text-destructive">
            Could not load your permissions — refresh the page to try again.
          </p>
        )}
        {groups.map((group) => (
          <SidebarGroup key={group.label}>
            <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      render={<Link href={item.href} />}
                      isActive={isActiveRoute(pathname, item.href)}
                      tooltip={item.label}
                      onClick={closeOnMobile}
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
  );
}

function AppLayoutContent({ children }: { children: ReactNode }) {
  const { user, farms, farmId, loading, signOut } = useAuth();
  const { can, loading: permsLoading, isError: permsError } = usePermissions();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const farmRedirectIntent = useRef<string | null>(null);

  useEffect(() => {
    const shouldRedirect = !loading && Boolean(user) && !farmId;
    if (!shouldRedirect) {
      farmRedirectIntent.current = null;
      return;
    }
    const intent = `${pathname}->/farm-select`;
    if (farmRedirectIntent.current === intent) return;
    farmRedirectIntent.current = intent;
    router.replace("/farm-select");
  }, [loading, user, farmId, pathname, router]);

  if (loading || !user || !farmId) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p role="status" aria-live="polite" className="text-muted-foreground">
          Loading…
        </p>
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
  const landingItem = visibleGroups[0]?.items[0];
  const landingHref = !permsLoading && !permsError ? firstPermittedPath(can) : null;
  const search = searchParams.toString();
  const returnTo = `${pathname}${search ? `?${search}` : ""}`;
  const farmSelectHref = `/farm-select?returnTo=${encodeURIComponent(returnTo)}`;

  return (
    <SidebarProvider>
      <AppSidebar
        groups={visibleGroups}
        pathname={pathname}
        landingHref={landingHref}
        landingLabel={landingItem?.label ?? "access status"}
        permsError={permsError}
      />
      <SidebarInset>
        <header className="flex h-14 shrink-0 items-center gap-2 border-b bg-background px-4">
          <SidebarTrigger />
          {farm && (
            <span className="truncate text-sm font-medium">{farm.name}</span>
          )}
          <Link
            href={farmSelectHref}
            className="text-sm text-muted-foreground hover:text-primary"
          >
            switch farm
          </Link>
          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
            <span className="hidden text-sm text-muted-foreground sm:inline">
              {user.name ?? user.email}
            </span>
            <AccountDialog name={user.name ?? null} email={user.email} />
            <Button variant="outline" size="sm" onClick={() => void signOut()}>
              Logout
            </Button>
          </div>
        </header>
        <div className="flex-1 bg-muted/40">
          <main
            key={farmId}
            className="mx-auto w-full max-w-7xl px-4 py-6 md:px-6"
          >
            {children}
          </main>
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen items-center justify-center">
          <p role="status" aria-live="polite" className="text-muted-foreground">
            Loading…
          </p>
        </main>
      }
    >
      <AppLayoutContent>{children}</AppLayoutContent>
    </Suspense>
  );
}
