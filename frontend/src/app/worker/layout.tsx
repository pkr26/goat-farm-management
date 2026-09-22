"use client";

/**
 * Worker tablet shell (ITEM 2 Phase 2, 2026-09-21 playbook).
 *
 * A deliberately minimal surface OUTSIDE the (app) group: no sidebar, no
 * dense tables — big touch targets, farm + worker identity, an offline/queue
 * badge, and one End-shift button. Gates mirror the app shell: signed-out →
 * /login, signed-in without a farm → /worker/login.
 */

import { LogOut, WifiOff } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { LANGUAGE_STORAGE_KEY, useLanguage, useT } from "@/lib/i18n";
import {
  drainOfflineQueue,
  offlineQueueDepth,
  startOfflineQueueWorkers,
  wipeOfflineQueue,
} from "@/lib/offline-queue";
import { usePermissions } from "@/lib/use-permissions";

/** One-time manager setup pins the tablet's farm (worker login page). */
export const TABLET_FARM_STORAGE_KEY = "herdly.tabletFarm";

function readTabletFarmId(): number | null {
  try {
    const raw = window.localStorage.getItem(TABLET_FARM_STORAGE_KEY);
    if (raw === null) return null;
    const value = Number(raw);
    return Number.isSafeInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

export function WorkerShell({ children }: { children: ReactNode }) {
  const { user, farmId, farms, signOut, loading } = useAuth();
  const perms = usePermissions();
  const router = useRouter();
  const pathname = usePathname();
  const t = useT();
  const { setLanguage } = useLanguage();
  const [online, setOnline] = useState(true);
  const [depth, setDepth] = useState(0);

  // Telugu-first: field workers are the primary audience of this surface; a
  // manager who chose a language keeps their choice. Writing storage before
  // the provider's first mount read makes the very first render Telugu;
  // setLanguage covers arrival by client-side navigation.
  useEffect(() => {
    try {
      if (window.localStorage.getItem(LANGUAGE_STORAGE_KEY) === null) {
        setLanguage("te");
      }
    } catch {
      /* blocked storage: the default language simply stays */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Register the service worker (PWA shell + static cache; /api is always
  // network-first in sw.js, so no operational data is ever served stale).
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        /* non-installed contexts (http:// IP dev) simply run without */
      });
    }
  }, []);

  useEffect(() => {
    setOnline(navigator.onLine);
    const onOnline = () => setOnline(true);
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  // Drain workers + a live queue-depth badge for the shell.
  useEffect(() => {
    if (user === null || farmId === null) return;
    const scopes = () =>
      user !== null && farmId !== null
        ? { actorScope: String(user.id), farmScope: String(farmId) }
        : null;
    const stop = startOfflineQueueWorkers(scopes);
    const tick = window.setInterval(() => setDepth(offlineQueueDepth()), 1500);
    setDepth(offlineQueueDepth());
    // Arriving online with a queue: drain immediately.
    void drainOfflineQueue(scopes() ?? { actorScope: "", farmScope: "" });
    return () => {
      stop();
      window.clearInterval(tick);
    };
  }, [user, farmId]);

  // Session gates: mirror the app shell's, aimed at the worker surface.
  useEffect(() => {
    if (loading) return;
    if (user === null && pathname !== "/worker/login") {
      router.replace("/login");
    }
  }, [loading, user, pathname, router]);

  if (loading) {
    return (
      <main className="flex min-h-dvh items-center justify-center">
        <p role="status" aria-live="polite" className="text-lg text-muted-foreground">
          {t("common.loading")}
        </p>
      </main>
    );
  }

  if (user === null) {
    return <main className="min-h-dvh">{children}</main>;
  }

  const farm = farms.find((f) => f.id === farmId);

  async function endShift() {
    // End shift is a shared-device handover: the queued writes belong to the
    // departing worker's session and must not leak to the next one.
    wipeOfflineQueue();
    await signOut();
    router.replace("/worker/login");
  }

  return (
    <div className="min-h-dvh bg-background">
      <header className="sticky top-0 z-10 border-b bg-card px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-xs text-muted-foreground">{farm?.name ?? t("worker.title")}</p>
            <p className="text-lg font-semibold" data-testid="worker-identity">
              {user.name ?? user.email}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {!online && (
              <span className="inline-flex items-center gap-1 rounded-full border border-destructive/40 px-3 py-1 text-sm text-destructive">
                <WifiOff aria-hidden className="size-4" /> {t("worker.offlineBadge")}
              </span>
            )}
            {depth > 0 && (
              <span
                className="rounded-full bg-secondary px-3 py-1 text-sm"
                data-testid="worker-queue-depth"
              >
                {t("worker.queued", { count: depth })}
              </span>
            )}
            <Button variant="destructive" onClick={() => void endShift()} data-testid="end-shift">
              <LogOut aria-hidden /> {t("worker.endShift")}
            </Button>
          </div>
        </div>
        {!perms.loading && perms.isError && (
          <p role="alert" className="mt-2 text-sm text-destructive">
            {t("common.somethingWentWrong")}
          </p>
        )}
      </header>
      <main id="main-content" className="mx-auto max-w-3xl space-y-6 p-4">
        {children}
      </main>
    </div>
  );
}

export default function WorkerLayout({ children }: { children: ReactNode }) {
  // The login page renders inside the shell too (big-type name list), but the
  // shell's session chrome hides itself when signed out.
  return <WorkerShell>{children}</WorkerShell>;
}

export { readTabletFarmId };
