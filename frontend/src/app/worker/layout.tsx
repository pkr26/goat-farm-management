"use client";

/**
 * Worker tablet shell.
 *
 * A deliberately minimal surface OUTSIDE the (app) group: no sidebar, no
 * dense tables — big touch targets, farm + worker identity, an offline/queue
 * badge, and one End-shift button. Gates: this shell redirects signed-out
 * sessions to the /worker/login PIN pad; the
 * signed-in-without-a-farm gate lives in worker/page.tsx.
 */

import { LogOut, WifiOff } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { AccountDialog } from "@/components/account-dialog";
import { LanguageToggle } from "@/components/language-toggle";
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
import { currentRequestScope, type RequestScope } from "@/lib/api-client";
import { LANGUAGE_STORAGE_KEY, useLanguage, useT } from "@/lib/i18n";
import { safeStorage } from "@/lib/safe-storage";
import { formatFarmDateTime } from "@/lib/format";
import {
  clearAcceptedWorkerReceipts,
  confirmOfflineWorkerDraft,
  discardOfflineWorkerDraft,
  readLegacyQueueQuarantine,
  readWorkerOutbox,
  resolveWorkerReviewReceipt,
  startWorkerOutbox,
  type WorkerOperation,
} from "@/lib/worker-outbox";
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
export function writeTabletFarmId(farmId: number): boolean {
  try {
    const storage = safeStorage("local");
    if (storage === null || !Number.isSafeInteger(farmId) || farmId <= 0) return false;
    storage.setItem(TABLET_FARM_STORAGE_KEY, String(farmId));
    return storage.getItem(TABLET_FARM_STORAGE_KEY) === String(farmId);
  } catch {
    return false;
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
  const [outbox, setOutbox] = useState<{ scopeKey: string; records: WorkerOperation[]; error: boolean }>({ scopeKey: "", records: [], error: false });
  const scopeKey = `${user?.id ?? ""}:${farmId ?? ""}`;
  const receipts = outbox.scopeKey === scopeKey ? outbox.records : [];
  const depth = receipts.filter((record) => record.state === "pending").length;
  const reviewCount = receipts.filter((record) => record.state === "review").length;
  const storageError = outbox.scopeKey === scopeKey && outbox.error;
  /** Non-null = the unsent-duties confirm is open for that many queued
   * records (snapshot at open time, so the copy can't shift under the
   * dialog while the 1.5s badge tick continues). */
  const [endShiftConfirmation, setEndShiftConfirmation] = useState<{ count: number; scope: RequestScope } | null>(null);
  const [acceptedCleanupConfirmation, setAcceptedCleanupConfirmation] = useState<{ ids: string[]; scope: RequestScope } | null>(null);
  const [reviewDismissal, setReviewDismissal] = useState<{ record: WorkerOperation; scope: RequestScope } | null>(null);
  const receiptActionFlight = useRef(false);
  const [receiptActionBusy, setReceiptActionBusy] = useState(false);
  const [draftApprovalBusy, setDraftApprovalBusy] = useState<ReadonlySet<string>>(new Set());
  const liveScope = currentRequestScope();
  const ownsScope = (scope: RequestScope) => {
    const current = currentRequestScope();
    return current?.actorScope === scope.actorScope && current.farmScope === scope.farmScope &&
      current.sessionEpoch === scope.sessionEpoch && current.farmEpoch === scope.farmEpoch;
  };
  const cleanupConfirmation = acceptedCleanupConfirmation !== null && ownsScope(acceptedCleanupConfirmation.scope)
    ? acceptedCleanupConfirmation : null;
  const dismissalConfirmation = reviewDismissal !== null && ownsScope(reviewDismissal.scope)
    ? reviewDismissal : null;
  const endShiftPendingCount = endShiftConfirmation !== null &&
    liveScope?.sessionEpoch === endShiftConfirmation.scope.sessionEpoch &&
    liveScope.farmEpoch === endShiftConfirmation.scope.farmEpoch
      ? endShiftConfirmation.count : null;

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
          // Force an update check on every shell mount: the browser otherwise throttles
          // sw.js revalidation to ~once per 24h, which is what kept tablets pinned to
          // their install-time build.
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
    // Read module state at every asynchronous edge. A closed-over React
    // user/farm from the previous effect is not a live authorization check.
    const scopes = () => {
      const current = currentRequestScope();
      return current?.actorScope === String(user.id) && current.farmScope === String(farmId) ? current : null;
    };
    return startWorkerOutbox(scopes, (records) => {
      setOutbox({ scopeKey: `${user.id}:${farmId}`, records, error: false });
    }, () => setOutbox({ scopeKey: `${user.id}:${farmId}`, records: [], error: true }),
    // Rotation is an authentication prerequisite, not a rejection of the
    // saved duty. Keep receipts visible without converting pending work to
    // definitive 403 review receipts before the worker can rotate it.
    () => !user.must_change_password);
  }, [user, farmId, t]);

  // Session gates: mirror the app shell's, aimed at the worker surface. PIN-only
  // workers hold no password, so a signed-out session belongs on the PIN pad
  // (/worker/login), not the manager's email/password form — the board page has the
  // same gate; both must point the same way.
  useEffect(() => {
    if (loading) return;
    if (user === null && pathname !== "/worker/login" && pathname !== "/worker/offline") {
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

  // These public pages own their own session transitions. Keep the same
  // parent element when setup commits its temporary manager: replacing it
  // with authenticated chrome would unmount the login page and trigger its
  // abandonment revocation before it can show the farm choices.
  if (user === null || pathname === "/worker/login" || pathname === "/worker/offline") {
    return (
      <div className="min-h-dvh">
        <header className="flex justify-end border-b bg-card px-3 py-2">
          <LanguageToggle />
        </header>
        <main>{children}</main>
      </div>
    );
  }

  const farm = farms.find((f) => f.id === farmId);

  async function endShift() {
    const revocation = signOut();
    router.replace("/worker/login");
    await revocation;
  }

  async function requestEndShift() {
    const scope = currentRequestScope();
    if (scope === null) return;
    try {
      const records = await readWorkerOutbox(scope);
      const live = currentRequestScope();
      if (live?.sessionEpoch !== scope.sessionEpoch || live.farmEpoch !== scope.farmEpoch) return;
      const pending = records.filter((record) => record.state !== "sent").length;
      if (pending > 0) setEndShiftConfirmation({ count: pending, scope });
      else await endShift();
    } catch { setOutbox({ scopeKey, records: receipts, error: true }); toast.error(t("worker.queueFull")); }
  }

  async function exportReceipts() {
    const scope = currentRequestScope();
    if (scope === null) return;
    try {
      const records = await readWorkerOutbox(scope);
      const legacyQuarantine = await readLegacyQueueQuarantine();
      const live = currentRequestScope();
      if (live?.sessionEpoch !== scope.sessionEpoch || live.farmEpoch !== scope.farmEpoch) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify({
        version: 3,
        operations: records,
        legacy_quarantine: legacyQuarantine,
      }, null, 2)], { type: "application/json" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "herdly-duty-receipts.json";
      anchor.click(); URL.revokeObjectURL(url);
    } catch { setOutbox({ scopeKey, records: receipts, error: true }); }
  }

  async function requestAcceptedCleanup() {
    const scope = currentRequestScope();
    if (scope === null || scope.actorScope !== String(user?.id) || scope.farmScope !== String(farmId) ||
      receiptActionFlight.current) return;
    receiptActionFlight.current = true; setReceiptActionBusy(true);
    try {
      const records = await readWorkerOutbox(scope);
      if (!ownsScope(scope)) return;
      const ids = records.filter((record) => record.state === "sent").map((record) => record.id);
      if (ids.length > 0) setAcceptedCleanupConfirmation({ ids, scope });
    } catch {
      if (ownsScope(scope)) toast.error(t("worker.receipts.clearAcceptedFailed"));
    } finally { receiptActionFlight.current = false; setReceiptActionBusy(false); }
  }

  async function confirmAcceptedCleanup() {
    const confirmation = acceptedCleanupConfirmation;
    if (confirmation === null || !ownsScope(confirmation.scope) || receiptActionFlight.current) return;
    receiptActionFlight.current = true; setReceiptActionBusy(true);
    try {
      const cleared = await clearAcceptedWorkerReceipts(confirmation.scope, confirmation.ids);
      if (!ownsScope(confirmation.scope)) return;
      setAcceptedCleanupConfirmation(null);
      const clearedIds = new Set(confirmation.ids);
      setOutbox((previous) => previous.scopeKey === `${confirmation.scope.actorScope}:${confirmation.scope.farmScope}`
        ? { ...previous, records: previous.records.filter((record) => !clearedIds.has(record.id)) }
        : previous);
      toast.success(t("worker.receipts.clearAcceptedSuccess", { count: cleared }));
    } catch {
      if (ownsScope(confirmation.scope)) toast.error(t("worker.receipts.clearAcceptedFailed"));
    } finally { receiptActionFlight.current = false; setReceiptActionBusy(false); }
  }

  async function approveOfflineDraft(record: WorkerOperation) {
    const scope = currentRequestScope();
    if (scope === null || draftApprovalBusy.has(record.id)) return;
    setDraftApprovalBusy((previous) => new Set(previous).add(record.id));
    try {
      const confirmed = await confirmOfflineWorkerDraft(scope, record.id);
      if (!ownsScope(scope)) return;
      if (!confirmed) throw new Error("Draft is no longer confirmable");
      setOutbox((previous) => previous.scopeKey === `${scope.actorScope}:${scope.farmScope}`
        ? { ...previous, records: previous.records.map((item) => item.id === record.id
          ? { ...item, state: "pending", reason: undefined, confirmedAt: Date.now() }
          : item) }
        : previous);
      toast.success(t("worker.receipts.offlineConfirmed"));
    } catch {
      if (ownsScope(scope)) toast.error(t("worker.receipts.offlineConfirmFailed"));
    } finally {
      setDraftApprovalBusy((previous) => {
        const next = new Set(previous); next.delete(record.id); return next;
      });
    }
  }

  async function discardOfflineDraft(record: WorkerOperation) {
    const scope = currentRequestScope();
    if (scope === null || draftApprovalBusy.has(record.id)) return;
    setDraftApprovalBusy((previous) => new Set(previous).add(record.id));
    try {
      const discarded = await discardOfflineWorkerDraft(scope, record.id);
      if (!ownsScope(scope)) return;
      if (!discarded) throw new Error("Draft is no longer discardable");
      setOutbox((previous) => previous.scopeKey === `${scope.actorScope}:${scope.farmScope}`
        ? { ...previous, records: previous.records.filter((item) => item.id !== record.id) }
        : previous);
      toast.success(t("worker.receipts.offlineDiscarded"));
    } catch {
      if (ownsScope(scope)) toast.error(t("worker.receipts.offlineDiscardFailed"));
    } finally {
      setDraftApprovalBusy((previous) => {
        const next = new Set(previous); next.delete(record.id); return next;
      });
    }
  }

  async function retryReviewReceipt(record: WorkerOperation) {
    const scope = currentRequestScope();
    if (scope === null || draftApprovalBusy.has(record.id)) return;
    setDraftApprovalBusy((previous) => new Set(previous).add(record.id));
    try {
      const retried = await resolveWorkerReviewReceipt(scope, record.id, "retry");
      if (!ownsScope(scope)) return;
      if (!retried) throw new Error("Receipt is no longer retryable");
      setOutbox((previous) => previous.scopeKey === `${scope.actorScope}:${scope.farmScope}`
        ? { ...previous, records: previous.records.map((item) => item.id === record.id
          ? {
              ...item,
              state: "pending",
              reason: undefined,
              status: undefined,
              settledAt: undefined,
              lastRetriedAt: Date.now(),
            }
          : item) }
        : previous);
      toast.success(t("worker.receipts.retrySuccess"));
    } catch {
      if (ownsScope(scope)) toast.error(t("worker.receipts.retryFailed"));
    } finally {
      setDraftApprovalBusy((previous) => {
        const next = new Set(previous); next.delete(record.id); return next;
      });
    }
  }

  function requestReviewDismissal(record: WorkerOperation) {
    const scope = currentRequestScope();
    if (scope === null || draftApprovalBusy.has(record.id) ||
      record.actorScope !== scope.actorScope || record.farmScope !== scope.farmScope) return;
    setReviewDismissal({ record, scope });
  }

  async function confirmReviewDismissal() {
    const confirmation = reviewDismissal;
    if (confirmation === null || !ownsScope(confirmation.scope) ||
      draftApprovalBusy.has(confirmation.record.id)) return;
    setDraftApprovalBusy((previous) => new Set(previous).add(confirmation.record.id));
    try {
      const dismissed = await resolveWorkerReviewReceipt(
        confirmation.scope,
        confirmation.record.id,
        "dismiss",
      );
      if (!ownsScope(confirmation.scope)) return;
      if (!dismissed) throw new Error("Receipt is no longer dismissible");
      setOutbox((previous) => previous.scopeKey ===
        `${confirmation.scope.actorScope}:${confirmation.scope.farmScope}`
        ? { ...previous, records: previous.records.filter((item) => item.id !== confirmation.record.id) }
        : previous);
      setReviewDismissal(null);
      toast.success(t("worker.receipts.dismissSuccess"));
    } catch {
      if (ownsScope(confirmation.scope)) toast.error(t("worker.receipts.dismissFailed"));
    } finally {
      setDraftApprovalBusy((previous) => {
        const next = new Set(previous); next.delete(confirmation.record.id); return next;
      });
    }
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
            <LanguageToggle />
            {user.must_change_password && (
              <AccountDialog name={user.name} email={user.email} passwordOnly />
            )}
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
            {reviewCount > 0 && <span className="rounded-full border border-destructive/40 px-3 py-1 text-sm text-destructive">{t("worker.receipts.reviewCount", { count: reviewCount })}</span>}
            {/* h-11: the worker surface's ≥44px touch floor — the default
             * h-9 left End shift at 36px. A non-empty
             * queue routes through the confirm dialog: the badge promised
             * "will send when online", so silently discarding those writes
             * on handover must be an explicit choice. */}
            <Button
              variant="destructive"
              className="h-11"
              onClick={() => void requestEndShift()}
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
        {storageError && <p role="alert" className="text-destructive">{t("worker.queueFull")}</p>}
        {receipts.length > 0 && <details className="rounded-lg border bg-card p-4">
          <summary className="min-h-11 cursor-pointer font-semibold">{t("worker.receipts.title")}</summary>
          <p className="my-2 text-sm text-muted-foreground">{t("worker.receipts.description")}</p>
          <ul className="space-y-2" data-testid="worker-receipts">
            {receipts.slice(-20).reverse().map((record) => <li key={record.id} className="rounded border p-3 text-sm">
              <p>{t("worker.receipts.task", { id: record.path.match(/\/tasks\/(\d+)/)?.[1] ?? record.id })} · {t(`worker.receipts.${record.state}`)}</p>
              <p className="text-muted-foreground">{formatFarmDateTime(new Date(record.queuedAt).toISOString())}</p>
              {record.state === "review" && record.reason === "offline-untrusted" &&
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button variant="outline" className="h-11"
                    data-testid={`worker-confirm-offline-${record.id}`}
                    disabled={draftApprovalBusy.has(record.id)}
                    onClick={() => void approveOfflineDraft(record)}>
                    {t("worker.receipts.confirmOffline")}
                  </Button>
                  <Button variant="ghost" className="h-11"
                    data-testid={`worker-discard-offline-${record.id}`}
                    disabled={draftApprovalBusy.has(record.id)}
                    onClick={() => void discardOfflineDraft(record)}>
                    {t("worker.receipts.discardOffline")}
                  </Button>
                </div>}
              {record.state === "review" && record.reason !== "offline-untrusted" &&
                <div className="mt-2 space-y-2">
                  <p className="text-muted-foreground">{t("worker.receipts.reviewHelp")}</p>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" className="h-11"
                      data-testid={`worker-retry-review-${record.id}`}
                      disabled={draftApprovalBusy.has(record.id)}
                      onClick={() => void retryReviewReceipt(record)}>
                      {t("worker.receipts.retry")}
                    </Button>
                    <Button variant="ghost" className="h-11"
                      data-testid={`worker-dismiss-review-${record.id}`}
                      disabled={draftApprovalBusy.has(record.id)}
                      onClick={() => requestReviewDismissal(record)}>
                      {t("worker.receipts.dismiss")}
                    </Button>
                  </div>
                </div>}
            </li>)}
          </ul>
          <div className="mt-3 flex flex-wrap gap-3">
            <Button variant="outline" className="h-11" onClick={() => void exportReceipts()}>{t("worker.receipts.export")}</Button>
            {receipts.some((record) => record.state === "sent") && <Button variant="outline" className="h-11"
              data-testid="worker-clear-accepted" disabled={receiptActionBusy} onClick={() => void requestAcceptedCleanup()}>
              {t("worker.receipts.clearAccepted")}
            </Button>}
          </div>
        </details>}
        {children}
      </main>
      <Dialog
        open={endShiftPendingCount !== null}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) setEndShiftConfirmation(null);
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
              onClick={() => setEndShiftConfirmation(null)}
              data-testid="end-shift-cancel"
            >
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              className="h-11"
              onClick={() => {
                const scope = currentRequestScope();
                if (scope?.sessionEpoch === endShiftConfirmation?.scope.sessionEpoch &&
                  scope?.farmEpoch === endShiftConfirmation?.scope.farmEpoch) void endShift();
                setEndShiftConfirmation(null);
              }}
              data-testid="end-shift-confirm"
            >
              {t("worker.endShiftConfirm.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={cleanupConfirmation !== null} onOpenChange={(open) => {
        if (!open && !receiptActionFlight.current) setAcceptedCleanupConfirmation(null);
      }}>
        <DialogContent role="alertdialog">
          <DialogHeader>
            <DialogTitle>{t("worker.receipts.clearAcceptedTitle")}</DialogTitle>
            <DialogDescription>{t("worker.receipts.clearAcceptedDescription", { count: cleanupConfirmation?.ids.length ?? 0 })}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" className="h-11" disabled={receiptActionBusy}
              data-testid="worker-clear-accepted-cancel" onClick={() => setAcceptedCleanupConfirmation(null)}>{t("common.cancel")}</Button>
            <Button variant="destructive" className="h-11" disabled={receiptActionBusy}
              data-testid="worker-clear-accepted-confirm" onClick={() => void confirmAcceptedCleanup()}>{t("worker.receipts.clearAcceptedConfirm")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={dismissalConfirmation !== null} onOpenChange={(open) => {
        if (!open && (dismissalConfirmation === null ||
          !draftApprovalBusy.has(dismissalConfirmation.record.id))) setReviewDismissal(null);
      }}>
        <DialogContent role="alertdialog">
          <DialogHeader>
            <DialogTitle>{t("worker.receipts.dismissTitle")}</DialogTitle>
            <DialogDescription>{t("worker.receipts.dismissDescription", {
              id: dismissalConfirmation?.record.path.match(/\/tasks\/(\d+)/)?.[1] ??
                dismissalConfirmation?.record.id ?? "",
            })}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" className="h-11"
              disabled={dismissalConfirmation !== null && draftApprovalBusy.has(dismissalConfirmation.record.id)}
              data-testid="worker-dismiss-review-cancel" onClick={() => setReviewDismissal(null)}>
              {t("common.cancel")}
            </Button>
            <Button variant="destructive" className="h-11"
              disabled={dismissalConfirmation !== null && draftApprovalBusy.has(dismissalConfirmation.record.id)}
              data-testid="worker-dismiss-review-confirm" onClick={() => void confirmReviewDismissal()}>
              {t("worker.receipts.dismissConfirm")}
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
