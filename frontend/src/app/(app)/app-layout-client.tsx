"use client";

/**
 * Authenticated app shell: left sidebar (brand + permission-filtered grouped
 * nav) and a slim sticky topbar (farm switcher, theme, account, logout).
 */

import {
  Baby,
  Boxes,
  CalendarCheck,
  CalendarClock,
  ChartColumn,
  ClipboardList,
  FlaskConical,
  HeartPulse,
  IndianRupee,
  LayoutDashboard,
  LogOut,
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
import { PermissionsError } from "@/components/permissions-error";
import { ThemeToggle } from "@/components/theme-toggle";
import { APP_NAME } from "@/lib/brand";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
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
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { usePermissions } from "@/lib/use-permissions";
import { cn } from "@/lib/utils";

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
        href: "/planner",
        label: "Planner",
        perm: "simulation.view",
        icon: CalendarCheck,
      },
      {
        href: "/simulation",
        label: "Simulation",
        perm: "simulation.view",
        icon: FlaskConical,
      },
      {
        href: "/ops-simulation",
        label: "Ops Simulation",
        perm: "simulation.view",
        icon: CalendarClock,
      },
      { href: "/reports", label: "Reports", perm: "reports.view", icon: ChartColumn },
      { href: "/team", label: "Team", perm: "team.manage", icon: Users },
    ],
  },
];

/** Route → document.title suffix; keeps browser tabs identifiable. */
const ROUTE_TITLES: [RegExp, string][] = [
  [/^\/dashboard/, "Dashboard"],
  [/^\/animals\/new/, "Add animal"],
  [/^\/animals\/\d+/, "Animal"],
  [/^\/animals/, "Animals"],
  [/^\/buckets/, "Buckets"],
  [/^\/breeding\/[^/]+\/ultrasound/, "Ultrasound"],
  [/^\/breeding/, "Breeding"],
  [/^\/kidding\/new/, "Record birth"],
  [/^\/kidding/, "Births"],
  [/^\/health\/new/, "Add health event"],
  [/^\/health\/schedule/, "Vaccination schedule"],
  [/^\/health/, "Health"],
  [/^\/feeding\/inventory/, "Feed inventory"],
  [/^\/feeding\/recipes/, "Feed recipes"],
  [/^\/feeding/, "Feeding"],
  [/^\/purchases/, "Purchases"],
  [/^\/tasks/, "Tasks"],
  [/^\/finance/, "Finance"],
  [/^\/planner/, "Planner"],
  [/^\/simulation/, "Simulation"],
  [/^\/ops-simulation/, "Ops Simulation"],
  [/^\/reports/, "Reports"],
  [/^\/team/, "Team"],
  [/^\/no-access/, "No access"],
];

function useDocumentTitle(pathname: string) {
  useEffect(() => {
    const match = ROUTE_TITLES.find(([pattern]) => pattern.test(pathname));
    document.title = match
      ? `${match[1]} · ${APP_NAME}`
      : `${APP_NAME} — Livestock farm management`;
  }, [pathname]);
}

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
  permsRefetch,
}: {
  groups: { label: string; items: NavItem[] }[];
  permsRefetch: () => void;
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
            aria-label={`${APP_NAME} — go to ${landingLabel}`}
            onClick={closeOnMobile}
          >
            <Logo />
          </Link>
        ) : (
          <div aria-label={APP_NAME}>
            <Logo />
          </div>
        )}
      </SidebarHeader>
      <SidebarContent>
        {/* A failed permissions call must not look like "no access" (7-6). */}
        {permsError && <PermissionsError onRetry={() => void permsRefetch()} />}
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
                      <item.icon aria-hidden="true" />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter className="px-4 pb-4">
        <p className="text-[0.68rem] leading-relaxed text-muted-foreground/70">
          Goat farm management
        </p>
      </SidebarFooter>
    </Sidebar>
  );
}

/** Pill-style farm indicator that links to the farm picker. Reads as a
 *  switcher (name + type + chevrons) while staying a plain, keyboard-
 *  accessible link. */
function FarmSwitcher({
  farmName,
  typeLabel,
  href,
}: {
  farmName: string;
  typeLabel: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      aria-label={`Switch farm — current: ${farmName}`}
      className={cn(
        // Wide enough that real farm names don't truncate at laptop widths;
        // the type chip is the first thing to yield (hidden below sm).
        "group/farm flex h-9 min-w-0 max-w-96 items-center gap-2 rounded-lg border bg-card px-2.5 text-sm font-medium shadow-xs transition-colors",
        "hover:border-primary/40 hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
      )}
    >
      <span className="truncate">{farmName}</span>
      <span className="hidden shrink-0 rounded-md bg-primary/10 px-1.5 py-0.5 text-[0.65rem] font-semibold text-primary sm:inline">
        {typeLabel}
      </span>
      <span
        aria-hidden="true"
        className="ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform group-hover/farm:translate-y-px"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-3.5">
          <path d="m7 15 5 5 5-5" />
          <path d="m7 9 5-5 5 5" />
        </svg>
      </span>
    </Link>
  );
}

function AppLayoutContent({
  children,
  defaultOpen,
}: {
  children: ReactNode;
  defaultOpen: boolean;
}) {
  const { user, farms, farmId, loading, signOut } = useAuth();
  const { can, loading: permsLoading, isError: permsError, refetch: permsRefetch } = usePermissions();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const farmRedirectIntent = useRef<string | null>(null);
  useDocumentTitle(pathname);

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
        <div className="flex flex-col items-center gap-3" role="status" aria-live="polite">
          <span className="animate-pulse">
            <Logo />
          </span>
          <p role="status" aria-live="polite" className="text-sm text-muted-foreground">Loading…</p>
        </div>
      </main>
    );
  }

  const farm = farms.find((f) => f.id === farmId);
  const vocabulary = farmVocabulary;
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
    <SidebarProvider defaultOpen={defaultOpen}>
      {/* First focusable element in the shell: on desktop the sidebar is an
       * in-flow sibling that precedes the content, so the skip link must be
       * rendered before it — keyboard users otherwise tab the whole sidebar
       * nav on every page before reaching content. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-2 focus:z-50 focus:rounded-lg focus:bg-primary focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-foreground focus:shadow"
      >
        Skip to content
      </a>
      <AppSidebar
        groups={visibleGroups}
        pathname={pathname}
        landingHref={landingHref}
        landingLabel={landingItem?.label ?? "access status"}
        permsError={permsError}
        permsRefetch={() => void permsRefetch()}
      />
      <SidebarInset>
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b bg-background/85 px-4 backdrop-blur-md">
          <SidebarTrigger />
          {farm && (
            <FarmSwitcher
              farmName={farm.name}
              typeLabel={vocabulary.typeLabel}
              href={farmSelectHref}
            />
          )}
          <div className="ml-auto flex items-center gap-1.5">
            <ThemeToggle />
            <span className="mx-1 hidden h-5 w-px bg-border sm:block" aria-hidden="true" />
            <AccountDialog name={user.name ?? null} email={user.email} />
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              aria-label="Logout"
              onClick={() => void signOut()}
            >
              <LogOut aria-hidden="true" className="size-3.5" />
              <span className="hidden sm:inline">Logout</span>
              <span className="sr-only sm:hidden">Logout</span>
            </Button>
          </div>
        </header>
        {user.must_change_password ? (
          <div
            role="alert"
            className="border-b border-warning/40 bg-warning-tint px-4 py-2 text-sm text-warning-tint-foreground"
          >
            This password was set by the farm owner — change it (Account → Change
            password) before continuing. Farm pages and actions stay blocked
            until you do.
          </div>
        ) : null}
        <div className="flex-1 bg-muted/40">
          <main
            id="main-content"
            key={farmId}
            tabIndex={-1}
            className="mx-auto w-full max-w-7xl px-4 py-6 outline-none md:px-6"
          >
            {children}
          </main>
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}

export function AppLayoutClient({
  children,
  defaultOpen,
}: {
  children: ReactNode;
  defaultOpen: boolean;
}) {
  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen items-center justify-center">
          <div className="flex flex-col items-center gap-3" role="status" aria-live="polite">
            <span className="animate-pulse">
              <Logo />
            </span>
            <p role="status" aria-live="polite" className="text-sm text-muted-foreground">Loading…</p>
          </div>
        </main>
      }
    >
      <AppLayoutContent defaultOpen={defaultOpen}>{children}</AppLayoutContent>
    </Suspense>
  );
}
