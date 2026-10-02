"use client";

/**
 * Worker tablet shell (ITEM 2 Phase 2, 2026-09-21 playbook).
 *
 * A deliberately minimal surface OUTSIDE the (app) group: no sidebar, no
 * dense tables — big touch targets, farm + worker identity, an offline/queue
 * badge, and one End-shift button. Gates: this shell redirects signed-out
 * sessions to the /worker/login PIN pad (2026-09-28 audit, W1); the
 * signed-in-without-a-farm gate lives in worker/page.tsx.
 */

import { LogOut, WifiOff } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAuth } from "@/lib/auth-context";
import { LANGUAGE_STORAGE_KEY, useLanguage, useT } from "@/lib/i18n";
import { safeStorage } from "@/lib/safe-storage";
import {
  clearOfflineQueueDrainBackoff,
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
    const raw = safeStorage("local")?.getItem(TABLET_FARM_STORAGE_KEY) ?? null;
    if (raw === null) return null;
    const value = Number(raw);
    return Number.isSafeInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

/** Persist the tablet's farm after a manager picks it in the setup flow. A
 * blocked/corrupted store simply leaves the tablet unpinned: the next load
 * falls back to the setup screen instead of a half-pinned roster. */
export function writeTabletFarmId(farmId: number): void {
  try {
    safeStorage("local")?.setItem(TABLET_FARM_STORAGE_KEY, String(farmId));
  } catch {
    /* storage blocked: setup reruns next load */
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
  /** Non-null = the unsent-duties confirm is open for that many queued
   * records (snapshot at open time, so the copy can't shift under the
   * dialog while the 1.5s badge tick continues). */
  const [endShiftPendingCount, setEndShiftPendingCount] = useState<number | null>(null);

  // Telugu-first: field workers are the primary audience of this surface; a
  // manager who chose a language keeps their choice. Writing storage before
  // the provider's first mount read makes the very first render Telugu;
  // setLanguage covers arrival by client-side navigation.
  useEffect(() => {
    try {
      if (safeStorage("local")?.getItem(LANGUAGE_STORAGE_KEY) === null) {
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
      navigator.serviceWorker
        .register("/sw.js")
        .then((registration) => {
          // Force an update check on every shell mount: the browser otherwise
          // throttles sw.js revalidation to ~once per 24h, which is what kept
          // tablets pinned to their install-time build (2026-09-28 audit, H1).
          registration.update().catch(() => {
            /* offline tablets simply keep the current worker */
          });
        })
        .catch(() => {
          /* non-installed contexts (http:// IP dev) simply run without */
        });
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount-time truth sync: no online/offline event fires on first paint
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
    // The badge and the end-shift confirm count with the SAME scope filter
    // the drain uses: a foreign actor's or farm's residue (a crash before
    // teardown, or records a forced logout deliberately preserved for their
    // owner) must not inflate the number this worker is shown or the
    // "deletes them permanently" copy (2026-10-01 audit, 07-L3).
    const scopedQueueDepth = () => {
      const current = scopes();
      return current === null ? 0 : offlineQueueDepth(current);
    };
    const stop = startOfflineQueueWorkers(scopes, (rejected) => {
      // A drain settled records the server definitively refused: the duties
      // stay PENDING and reappear on the board, but the recorded
      // completions are gone — say so instead of dropping them silently
      // (2026-09-29 audit).
      toast.error(
        t(
          rejected === 1 ? "worker.offlineRejected_one" : "worker.offlineRejected_many",
          { count: rejected },
        ),
      );
    });
    const tick = window.setInterval(() => setDepth(scopedQueueDepth()), 1500);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the badge must reflect the queue depth this session inherited, not wait 1.5s
    setDepth(scopedQueueDepth());
    // Arriving online with a queue: drain immediately. The effect's guard
    // makes scopes() non-null here; the callback form keeps that coupling
    // visible instead of a dead empty-scope fallback (2026-09-29 audit).
    const initialScopes = scopes();
    if (initialScopes !== null) void drainOfflineQueue(initialScopes);
    return () => {
      stop();
      window.clearInterval(tick);
    };
  }, [user, farmId, t]);

  // Session gates: mirror the app shell's, aimed at the worker surface.
  // PIN-only workers hold no password, so a signed-out session belongs on the
  // PIN pad (/worker/login), not the manager's email/password form — the
  // board page has the same gate; both must point the same way
  // (2026-09-28 audit, W1).
  useEffect(() => {
    if (loading) return;
    if (user === null && pathname !== "/worker/login") {
      router.replace("/worker/login");
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
    // departing worker's session and must not leak to the next one. The
    // 429 drain backoff is the same session's — clear it so the next
    // actor's first drain isn't gated by this session's Retry-After
    // (clearSession clears it too; belt and braces for the worker path).
    wipeOfflineQueue();
    clearOfflineQueueDrainBackoff();
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
            {/* h-11: the worker surface's ≥44px touch floor — the default
             * h-9 left End shift at 36px (2026-09-28 audit, W9). A non-empty
             * queue routes through the confirm dialog: the badge promised
             * "will send when online", so silently discarding those writes
             * on handover must be an explicit choice (2026-09-29 audit, M2). */}
            <Button
              variant="destructive"
              className="h-11"
              onClick={() =>
                depth > 0 ? setEndShiftPendingCount(depth) : void endShift()
              }
              data-testid="end-shift"
            >
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
      <Dialog
        open={endShiftPendingCount !== null}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) setEndShiftPendingCount(null);
        }}
      >
        <DialogContent role="alertdialog">
          <DialogHeader>
            <DialogTitle>{t("worker.endShiftConfirm.title")}</DialogTitle>
            <DialogDescription>
              {t(
                endShiftPendingCount === 1
                  ? "worker.endShiftConfirm.description_one"
                  : "worker.endShiftConfirm.description_many",
                { count: endShiftPendingCount ?? 0 },
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              className="h-11"
              onClick={() => setEndShiftPendingCount(null)}
              data-testid="end-shift-cancel"
            >
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              className="h-11"
              onClick={() => {
                setEndShiftPendingCount(null);
                void endShift();
              }}
              data-testid="end-shift-confirm"
            >
              {t("worker.endShiftConfirm.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function WorkerLayout({ children }: { children: ReactNode }) {
  // The login page renders inside the shell too (big-type name list), but the
  // shell's session chrome hides itself when signed out.
  return <WorkerShell>{children}</WorkerShell>;
}

export { readTabletFarmId };
