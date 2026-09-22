"use client";

/**
 * Authenticated app shell: left sidebar (brand + permission-filtered grouped
 * nav) and a slim sticky topbar (farm switcher, theme, account, logout).
 */

import { Baby, BarChart3, Boxes, CalendarCheck, CalendarClock, Camera, ChartColumn, ClipboardList, FlaskConical, HeartPulse, IndianRupee, LayoutDashboard, LogOut, PawPrint, ShoppingCart, Stethoscope, type LucideIcon, Users, Wheat } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, type ReactNode } from "react";

import { Logo } from "@/components/logo";
import { AccountDialog } from "@/components/account-dialog";
import { LanguageToggle } from "@/components/language-toggle";
import { PermissionsError } from "@/components/permissions-error";
import { ThemeToggle } from "@/components/theme-toggle";
import { APP_NAME } from "@/lib/brand";
import { useT, type MessageKey } from "@/lib/i18n";
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

type NavItem = {
  href: string;
  labelKey: MessageKey;
  perm: string;
  icon: LucideIcon;
  // Owner-console entries ignore `perm` and show only for farm owners —
  // ownership, not a permission code, is what the cross-farm API accepts.
  ownerOnly?: boolean;
};

/** Sidebar labels resolve through the i18n catalog so low-literacy Telangana
 * workers can navigate; the English catalog strings are byte-identical to
 * the pre-i18n labels. */
const NAV_GROUPS: { labelKey: MessageKey; items: NavItem[] }[] = [
  {
    labelKey: "nav.group.overview",
    items: [
      {
        href: "/dashboard",
        labelKey: "nav.dashboard",
        perm: "dashboard.view",
        icon: LayoutDashboard,
      },
      {
        href: "/owner",
        labelKey: "nav.owner",
        perm: "dashboard.view",
        icon: BarChart3,
        ownerOnly: true,
      },
    ],
  },
  {
    labelKey: "nav.group.herd",
    items: [
      { href: "/animals", labelKey: "nav.animals", perm: "animals.view", icon: PawPrint },
      { href: "/buckets", labelKey: "nav.buckets", perm: "buckets.view", icon: Boxes },
      {
        href: "/breeding",
        labelKey: "nav.breeding",
        perm: "breeding.view",
        icon: HeartPulse,
      },
      { href: "/kidding", labelKey: "nav.kidding", perm: "kidding.view", icon: Baby },
    ],
  },
  {
    labelKey: "nav.group.healthFeed",
    items: [
      { href: "/health", labelKey: "nav.health", perm: "health.view", icon: Stethoscope },
      {
        href: "/screening",
        labelKey: "nav.screening",
        perm: "health.view",
        icon: Camera,
      },
      { href: "/feeding", labelKey: "nav.feeding", perm: "feeding.view", icon: Wheat },
    ],
  },
  {
    labelKey: "nav.group.operations",
    items: [
      {
        href: "/purchases",
        labelKey: "nav.purchases",
        perm: "purchases.view",
        icon: ShoppingCart,
      },
      { href: "/tasks", labelKey: "nav.tasks", perm: "tasks.view", icon: ClipboardList },
    ],
  },
  {
    labelKey: "nav.group.business",
    items: [
      { href: "/finance", labelKey: "nav.finance", perm: "finance.view", icon: IndianRupee },
      {
        href: "/planner",
        labelKey: "nav.planner",
        perm: "simulation.view",
        icon: CalendarCheck,
      },
      {
        href: "/simulation",
        labelKey: "nav.simulation",
        perm: "simulation.view",
        icon: FlaskConical,
      },
      {
        href: "/ops-simulation",
        labelKey: "nav.opsSimulation",
        perm: "simulation.view",
        icon: CalendarClock,
      },
      { href: "/reports", labelKey: "nav.reports", perm: "reports.view", icon: ChartColumn },
      { href: "/team", labelKey: "nav.team", perm: "team.manage", icon: Users },
    ],
  },
];

/** Route → document.title suffix; keeps browser tabs identifiable. */
const ROUTE_TITLES: [RegExp, MessageKey][] = [
  [/^\/dashboard/, "doc.title.dashboard"],
  [/^\/animals\/new/, "doc.title.addAnimal"],
  [/^\/animals\/\d+/, "doc.title.animal"],
  [/^\/animals/, "doc.title.animals"],
  [/^\/buckets/, "doc.title.buckets"],
  [/^\/breeding\/[^/]+\/ultrasound/, "doc.title.ultrasound"],
  [/^\/breeding/, "doc.title.breeding"],
  [/^\/kidding\/new/, "doc.title.recordBirth"],
  [/^\/kidding/, "doc.title.births"],
  [/^\/health\/new/, "doc.title.addHealthEvent"],
  [/^\/health\/schedule/, "doc.title.vaccinationSchedule"],
  [/^\/health/, "doc.title.health"],
  [/^\/screening/, "doc.title.screening"],
  [/^\/feeding\/inventory/, "doc.title.feedInventory"],
  [/^\/feeding\/recipes/, "doc.title.feedRecipes"],
  [/^\/feeding/, "doc.title.feeding"],
  [/^\/purchases/, "doc.title.purchases"],
  [/^\/tasks/, "doc.title.tasks"],
  [/^\/finance/, "doc.title.finance"],
  [/^\/planner/, "doc.title.planner"],
  [/^\/simulation/, "doc.title.simulation"],
  [/^\/ops-simulation/, "doc.title.opsSimulation"],
  [/^\/reports/, "doc.title.reports"],
  [/^\/team/, "doc.title.team"],
  [/^\/no-access/, "doc.title.noAccess"],
];

function useDocumentTitle(pathname: string) {
  const t = useT();
  useEffect(() => {
    const match = ROUTE_TITLES.find(([pattern]) => pattern.test(pathname));
    document.title = match
      ? `${t(match[1])} · ${APP_NAME}`
      : `${APP_NAME} — Livestock farm management`;
  }, [pathname, t]);
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
  groups: { labelKey: MessageKey; items: NavItem[] }[];
  permsRefetch: () => void;
  pathname: string;
  landingHref: string | null;
  landingLabel: string;
  permsError: boolean;
}) {
  const { isMobile, setOpenMobile } = useSidebar();
  const t = useT();
  const closeOnMobile = () => {
    // Stryker disable next-line ConditionalExpression: the effect runs on mount (sheet already closed) and on isMobile transitions only, which jsdom cannot deliver
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
          <SidebarGroup key={group.labelKey}>
            <SidebarGroupLabel>{t(group.labelKey)}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      render={<Link href={item.href} />}
                      isActive={isActiveRoute(pathname, item.href)}
                      tooltip={t(item.labelKey)}
                      onClick={closeOnMobile}
                    >
                      <item.icon aria-hidden="true" />
                      <span>{t(item.labelKey)}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter className="px-4 pb-4">
        {/* Full-strength muted token: the /70 tint sat under 4.5:1 on the
            sidebar background (sub-AA microtext, 2026-09-21 audit). */}
        <p className="text-[0.68rem] leading-relaxed text-muted-foreground">
          {t("shell.tagline")}
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
      <span className="truncate" dir="auto">{farmName}</span>
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
  const {
    can,
    loading: permsLoading,
    isError: permsError,
    refetch: permsRefetch,
  } = usePermissions();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const t = useT();
  // The owner console serves "owns >= 1 farm" (the backend's rule), which is
  // wider than the CURRENT farm's is_owner: an owner switched into a farm
  // they merely manage keeps the entry. /api/auth/farms marks owned farms
  // with role === null.
  const ownsAnyFarm = farms.some((f) => f.role === null);
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

  // Stryker disable next-line ConditionalExpression, LogicalOperator: while loading, user and farmId are still null (or commit in the same batched render), so the loading operand never changes the outcome
  if (loading || !user || !farmId) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <div className="flex flex-col items-center gap-3" role="status" aria-live="polite">
          <span className="animate-pulse">
            <Logo />
          </span>
          <p role="status" aria-live="polite" className="text-sm text-muted-foreground">{t("common.loading")}</p>
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
        items: group.items.filter((item) =>
          item.ownerOnly ? ownsAnyFarm && can(item.perm) : can(item.perm),
        ),
      })).filter((group) => group.items.length > 0);
  const landingItem = visibleGroups[0]?.items[0];
  const landingHref = !permsLoading && !permsError ? firstPermittedPath(can) : null;
  // Without a permitted module (or while permissions are unknown) the brand
  // link still points somewhere safe, but its label must not promise a page.
  const landingLabel =
    landingItem && !permsLoading && !permsError ? t(landingItem.labelKey) : "access status";
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
        {t("shell.skipToContent")}
      </a>      <AppSidebar
        groups={visibleGroups}
        pathname={pathname}
        landingHref={landingHref}
        landingLabel={landingLabel}
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
            <LanguageToggle />
            <ThemeToggle />
            <span className="mx-1 hidden h-5 w-px bg-border sm:block" aria-hidden="true" />
            <AccountDialog name={user.name ?? null} email={user.email} />
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              aria-label={t("common.logout")}
              onClick={() => void signOut()}
            >
              <LogOut aria-hidden="true" className="size-3.5" />
              <span className="hidden sm:inline">{t("common.logout")}</span>
              <span className="sr-only sm:hidden">{t("common.logout")}</span>
            </Button>
          </div>
        </header>
        {user.must_change_password ? (
          <div
            role="alert"
            className="border-b border-warning/40 bg-warning-tint px-4 py-2 text-sm text-warning-tint-foreground"
          >
            {t("shell.passwordChangeNotice")}
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
  const t = useT();
  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen items-center justify-center">
          <div className="flex flex-col items-center gap-3" role="status" aria-live="polite">
            <span className="animate-pulse">
              <Logo />
            </span>
            <p role="status" aria-live="polite" className="text-sm text-muted-foreground">{t("common.loading")}</p>
          </div>
        </main>
      }
    >
      <AppLayoutContent defaultOpen={defaultOpen}>{children}</AppLayoutContent>
    </Suspense>
  );
}
