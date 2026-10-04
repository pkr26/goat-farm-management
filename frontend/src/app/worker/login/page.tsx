"use client";

/**
 * Worker tablet sign-in (ITEM 2 Phase 2): tap your name, enter your PIN.
 *
 * The farm is pinned once by a manager through the setup flow below (the
 * roster endpoint is unauthenticated, so the PIN pad works before any session
 * exists). Success runs the normal signIn() so the whole app's session
 * machinery is shared.
 */

import { ClipboardList, Delete, Fingerprint } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { LoginOut, TokenOut } from "@/api/generated/models";
import { useWorkerRosterApiAuthWorkerRosterGet } from "@/api/generated/endpoints";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch, ApiError, authSessionEpochValue, revokeTabletSetupSession } from "@/lib/api-client";
import { failedSessionEstablishmentEpoch, useAuth, type FarmEntry } from "@/lib/auth-context";
import { useT } from "@/lib/i18n";
import { safeStorage } from "@/lib/safe-storage";
import { readOfflineShift } from "@/lib/worker-offline-shift";
import { mapServerError } from "@/lib/server-error-phrases";
import { TABLET_FARM_STORAGE_KEY, readTabletFarmId, writeTabletFarmId } from "@/app/worker/layout";

type RosterEntry = { membership_id: number; display_name: string };

type SetupStep = "credentials" | "code" | "choose-farm";



export default function WorkerLoginPage() {
  const t = useT();
  const router = useRouter();
  const { signIn, signOut, farmId, farms, selectFarm, getFarms } = useAuth();
  const [tabletFarmId, setTabletFarmId] = useState<number | null>(null);
  const [selected, setSelected] = useState<RosterEntry | null>(null);
  const [rosterPages, setRosterPages] = useState<{ farmId: number | null; cursors: number[] }>({ farmId: null, cursors: [0] });
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offlineShiftAvailable, setOfflineShiftAvailable] = useState(false);
  // Manager setup state: the tablet starts unpinned, a manager/owner signs in
  // once (password + optional TOTP/recovery code) and picks the farm.
  const [setupStep, setSetupStep] = useState<SetupStep | null>(null);
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  // True while the signed-in user is the setup manager, not a worker: keeps
  // the returning-session redirect below out of the middle of setup.
  const setupRef = useRef(false);
  const attemptGeneration = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const setupSession = useRef<{ epoch: number; accessToken: string } | null>(null);
  const establishingPinSession = useRef<{ epoch: number; accessToken: string } | null>(null);
  const [unpinOpen, setUnpinOpen] = useState(false);

  // Back, unpin and unmount all abandon the same authority-establishing
  // attempt. Cancellation must also cover signIn's pending farm discovery:
  // its token is already staged before that promise settles. Revoke only
  // that family; a newer session owns its own local state and cookie.
  const cancelAttempt = useCallback(() => {
    attemptGeneration.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    const staged = establishingPinSession.current;
    establishingPinSession.current = null;
    if (staged === null) return false;
    // The captured bearer is the exact grant to revoke, even if generic
    // session teardown is superseded while waiting for an auth-cookie lock.
    void revokeTabletSetupSession(staged.accessToken).catch(() => {});
    if (staged.epoch === authSessionEpochValue()) {
      // signOut captures this epoch's bearer before clearing local state.
      // logout-session revokes exactly that family without touching cookies.
      void signOut({ sessionOnly: true });
      return true;
    }
    return false;
  }, [signOut]);

  const abandonSetupSession = useCallback(() => {
    const staged = setupSession.current;
    setupSession.current = null;
    setupRef.current = false;
    if (staged === null) return false;
    if (staged.epoch === authSessionEpochValue()) {
      void signOut({ sessionOnly: true });
      return true;
    }
    // A replacement worker owns local state. The temporary manager's exact
    // bearer still needs revocation even after its epoch has been superseded.
    void revokeTabletSetupSession(staged.accessToken).catch(() => {});
    return false;
  }, [signOut]);

  function backToRoster() {
    const endedStagedSession = cancelAttempt();
    setSelected(null);
    setPin("");
    setError(null);
    setBusy(false);
    if (endedStagedSession) router.replace("/worker/login");
  }

  // W3 (2026-09-28 audit): an interrupted manager setup must not leave a
  // live manager session on the shared tablet. pinFarm/cancelSetup clear
  // setupRef on their own paths; any OTHER exit from this page (navigation
  // away mid-flow) signs the manager out here.
  useEffect(() => {
    return () => {
      cancelAttempt();
      abandonSetupSession();
    };
  }, [cancelAttempt, abandonSetupSession]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- localStorage exists only client-side; reading it during render would break SSR hydration
    setTabletFarmId(readTabletFarmId());
    let active = true;
    void readOfflineShift().then((shift) => { if (active) setOfflineShiftAvailable(shift !== null); }).catch(() => {});
    return () => { active = false; };
  }, []);

  const rosterQuery = useWorkerRosterApiAuthWorkerRosterGet(
    { farm_id: tabletFarmId ?? 0, after_membership_id: rosterPages.farmId === tabletFarmId ? rosterPages.cursors.at(-1) ?? 0 : 0 },
    { query: { enabled: tabletFarmId !== null } },
  );
  const roster =
    rosterQuery.data?.status === 200 ? rosterQuery.data.data : undefined;

  // A returning session with a farm goes straight to the board.
  useEffect(() => {
    if (
      !setupRef.current &&
      farmId !== null &&
      tabletFarmId !== null &&
      farmId === tabletFarmId
    ) {
      router.replace("/worker");
    }
  }, [farmId, tabletFarmId, router]);

  async function submit(finalPin: string) {
    if (selected === null || tabletFarmId === null || busy) return;
    setBusy(true);
    setError(null);
    const attempt = ++attemptGeneration.current;
    const controller = new AbortController();
    requestController.current?.abort(); requestController.current = controller;
    let expectedEpoch = authSessionEpochValue();
    const isCurrent = () => attemptGeneration.current === attempt && !controller.signal.aborted &&
      expectedEpoch === authSessionEpochValue();
    try {
      // TokenOut is the generated contract (the login page anchors to it
      // deliberately so backend renames break tsc); the inline type here was
      // a drift risk (2026-10-01 audit, 05-Info).
      const body = await apiFetch<TokenOut>(
        "/api/auth/worker-login",
        {
          method: "POST",
          signal: controller.signal,
          body: JSON.stringify({
            farm_id: tabletFarmId,
            membership_id: selected.membership_id,
            pin: finalPin,
          }),
        },
      );
      if (!isCurrent()) {
        void revokeTabletSetupSession(body.access_token).catch(() => {}); return;
      }
      const establishment = signIn(body.access_token, body.user);
      expectedEpoch = authSessionEpochValue();
      establishingPinSession.current = { epoch: expectedEpoch, accessToken: body.access_token };
      await establishment;
      if (!isCurrent()) return;
      establishingPinSession.current = null;
      // signIn's farm discovery auto-selects list[0] when nothing is stored;
      // a pinned tablet belongs on ITS farm. getFarms() reads the list this
      // signIn just committed (React state has not re-rendered yet), and a
      // worker who is not actually a member of the pinned farm keeps the
      // discovery fallback rather than being forced into a farm they lack.
      const pinned = getFarms().find((farm) => farm.id === tabletFarmId);
      if (pinned) selectFarm(pinned.id, pinned.timezone);
      // No glyph decorations in UI copy — the name alone is the confirmation
      // (no-emoji/symbol convention, 2026-09-28 audit).
      toast.success(selected.display_name);
      router.replace("/worker");
    } catch (error) {
      const failedEpoch = failedSessionEstablishmentEpoch(error);
      if (attemptGeneration.current === attempt && !controller.signal.aborted && failedEpoch === authSessionEpochValue()) {
        expectedEpoch = failedEpoch;
      }
      if (!isCurrent()) return;
      establishingPinSession.current = null;
      setPin("");
      // 401 is the server judging the PIN; any other ApiError carries the
      // server's own mapped message (429 rate limit, 5xx, …). A non-ApiError
      // (TypeError/AbortError) never reached the server at all — saying
      // "Wrong PIN" for an offline tablet sends the worker re-typing a
      // perfectly good PIN.
      setError(
        error instanceof ApiError
          ? error.status === 401
            ? t("worker.login.wrongPin")
            : mapServerError(t, error.detail, error.status, error.code)
          : t("worker.login.networkError"),
      );
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }

  async function submitSetupCredentials() {
    if (busy) return;
    setBusy(true);
    setError(null);
    const attempt = ++attemptGeneration.current;
    const controller = new AbortController();
    requestController.current?.abort(); requestController.current = controller;
    let expectedEpoch = authSessionEpochValue();
    const isCurrent = () => attemptGeneration.current === attempt && !controller.signal.aborted &&
      expectedEpoch === authSessionEpochValue();
    try {
      const body = await apiFetch<LoginOut>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password, tablet_setup: true }),
        signal: controller.signal,
      });
      if (!isCurrent()) {
        if (body.access_token) void revokeTabletSetupSession(body.access_token).catch(() => {});
        return;
      }
      if (body.mfa_token) {
        setMfaToken(body.mfa_token);
        setSetupStep("code");
        return;
      }
      if (!body.access_token || !body.user) {
        setError(t("worker.setup.failed"));
        return;
      }
      setupRef.current = true;
      const establishing = signIn(body.access_token, body.user, { sessionOnly: true });
      expectedEpoch = authSessionEpochValue();
      setupSession.current = { epoch: expectedEpoch, accessToken: body.access_token };
      await establishing;
      if (isCurrent()) setSetupStep("choose-farm");
    } catch (error) {
      const failedEpoch = failedSessionEstablishmentEpoch(error);
      if (attemptGeneration.current === attempt && !controller.signal.aborted && failedEpoch === authSessionEpochValue()) {
        expectedEpoch = failedEpoch;
        setupSession.current = null; setupRef.current = false;
      }
      if (!isCurrent()) return;
      // Mirror the PIN flow's split: an ApiError is the server judging the
      // credentials; a non-ApiError never reached the server, and "check your
      // details" sends the manager re-typing a perfectly good password
      // (2026-09-28 audit).
      setError(
        error instanceof ApiError
          ? t("worker.setup.failed")
          : t("worker.login.networkError"),
      );
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }

  async function submitSetupCode() {
    if (busy || mfaToken === null) return;
    setBusy(true);
    setError(null);
    const attempt = ++attemptGeneration.current;
    const controller = new AbortController();
    requestController.current?.abort(); requestController.current = controller;
    let expectedEpoch = authSessionEpochValue();
    const isCurrent = () => attemptGeneration.current === attempt && !controller.signal.aborted &&
      expectedEpoch === authSessionEpochValue();
    try {
      const body = await apiFetch<TokenOut>("/api/auth/totp/challenge", {
        method: "POST",
        body: JSON.stringify({ mfa_token: mfaToken, code }),
        signal: controller.signal,
      });
      if (!isCurrent()) {
        void revokeTabletSetupSession(body.access_token).catch(() => {}); return;
      }
      setupRef.current = true;
      const establishing = signIn(body.access_token, body.user, { sessionOnly: true });
      expectedEpoch = authSessionEpochValue();
      setupSession.current = { epoch: expectedEpoch, accessToken: body.access_token };
      await establishing;
      if (isCurrent()) { setMfaToken(null); setSetupStep("choose-farm"); }
    } catch (error) {
      const failedEpoch = failedSessionEstablishmentEpoch(error);
      if (attemptGeneration.current === attempt && !controller.signal.aborted && failedEpoch === authSessionEpochValue()) {
        expectedEpoch = failedEpoch;
        setupSession.current = null; setupRef.current = false;
      }
      if (!isCurrent()) return;
      setCode("");
      // Same split as the credentials step above: the server answered (bad
      // code) versus the request never left the tablet (network).
      setError(
        error instanceof ApiError
          ? t("worker.setup.failed")
          : t("worker.login.networkError"),
      );
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }

  async function pinFarm(farm: FarmEntry) {
    if (setupSession.current?.epoch !== authSessionEpochValue()) {
      abandonSetupSession();
      setSetupStep(null);
      setBusy(false);
      return;
    }
    if (!writeTabletFarmId(farm.id)) { setError(t("worker.setup.failed")); return; }
    setTabletFarmId(farm.id);
    setSetupStep(null);
    setMfaToken(null);
    toast.success(t("worker.setup.pinnedToast"));
    // Revoke only this temporary setup family and return to the PIN door.
    // Clear the setup flag BEFORE signOut so the unmount teardown (W3)
    // cannot fire a second sign-out; signOut navigates to the manager's
    // /login, so hand the tablet back to the workers' PIN pad explicitly.
    setupRef.current = false;
    setupSession.current = null;
    attemptGeneration.current += 1; requestController.current?.abort();
    const revocation = signOut({ sessionOnly: true });
    router.replace("/worker/login");
    await revocation;
  }

  async function cancelSetup() {
    // Abandoning midway must not leave the manager's session behind (W3).
    const hadManagerSession = abandonSetupSession();
    attemptGeneration.current += 1; requestController.current?.abort();
    setupRef.current = false;
    setSetupStep(null);
    setMfaToken(null);
    setCode("");
    setError(null);
    setBusy(false);
    if (hadManagerSession) {
      // The manager signed in but never pinned: end that session, then hand
      // the tablet back to the PIN pad — signOut itself navigates to the
      // manager's /login form.
      router.replace("/worker/login");
    }
  }

  function confirmUnpin() {
    const endedStagedSession = cancelAttempt();
    try {
      safeStorage("local")?.removeItem(TABLET_FARM_STORAGE_KEY);
    } catch {
      /* nothing persisted */
    }
    setUnpinOpen(false);
    setSelected(null);
    setPin("");
    setError(null);
    setBusy(false);
    setTabletFarmId(null);
    if (endedStagedSession) router.replace("/worker/login");
  }

  function pressDigit(digit: string) {
    if (busy) return;
    const next = (pin + digit).slice(0, 12);
    setPin(next);
    setError(null);
  }

  if (tabletFarmId === null) {
    if (setupStep === "choose-farm") {
      return (
        <div className="mx-auto max-w-xl space-y-4 p-6" data-testid="worker-setup-farms">
          <h1 className="text-2xl font-semibold">{t("worker.setup.chooseFarm")}</h1>
          {farms.length === 0 ? (
            <EmptyState
              icon={ClipboardList}
              title={t("worker.setup.noFarms")}
              description={t("worker.setup.description")}
            />
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {farms.map((farm) => (
                <li key={farm.id}>
                  <Button
                    variant="outline"
                    className="h-16 w-full justify-center text-lg"
                    data-testid={`setup-farm-${farm.id}`}
                    onClick={() => void pinFarm(farm)}
                  >
                    {farm.name}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <Button
            variant="ghost"
            className="w-full"
            onClick={() => void cancelSetup()}
          >
            {t("common.cancel")}
          </Button>
        </div>
      );
    }

    if (setupStep === "code") {
      return (
        <div className="mx-auto max-w-sm space-y-4 p-6" data-testid="worker-setup-code">
          <h1 className="text-2xl font-semibold">{t("worker.setup.code")}</h1>
          <p className="text-muted-foreground">{t("worker.setup.codeHint")}</p>
          <form
            className="space-y-2"
            onSubmit={(event) => {
              event.preventDefault();
              void submitSetupCode();
            }}
          >
            <Label htmlFor="worker-setup-code">{t("worker.setup.code")}</Label>
            <Input
              id="worker-setup-code"
              className="h-14 text-center text-2xl tracking-[0.3em]"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              autoComplete="one-time-code"
              inputMode="text"
              autoFocus
            />
            {error && (
              <p role="alert" className="text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" className="h-14 w-full text-lg" disabled={busy || code.length < 6}>
              {t("worker.setup.continue")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="w-full"
              onClick={() => void cancelSetup()}
            >
              {t("common.cancel")}
            </Button>
          </form>
        </div>
      );
    }

    if (setupStep === "credentials") {
      return (
        <div className="mx-auto max-w-sm space-y-4 p-6" data-testid="worker-setup-credentials">
          <h1 className="text-2xl font-semibold">{t("worker.setup.title")}</h1>
          <p className="text-muted-foreground">{t("worker.setup.description")}</p>
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              void submitSetupCredentials();
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="worker-setup-email">{t("worker.setup.email")}</Label>
              <Input
                id="worker-setup-email"
                type="email"
                className="h-14 text-lg"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="username"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="worker-setup-password">{t("worker.setup.password")}</Label>
              <Input
                id="worker-setup-password"
                type="password"
                className="h-14 text-lg"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
              />
            </div>
            {error && (
              <p role="alert" className="text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" className="h-14 w-full text-lg" disabled={busy}>
              {t("worker.setup.continue")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="w-full"
              onClick={() => void cancelSetup()}
            >
              {t("common.cancel")}
            </Button>
          </form>
        </div>
      );
    }

    return (
      <div className="flex min-h-dvh items-center justify-center p-6">
        <EmptyState
          icon={ClipboardList}
          title={t("worker.needFarm.title")}
          description={t("worker.needFarm.description")}
        >
          <Button
            className="mt-2"
            data-testid="worker-setup-start"
            onClick={() => {
              setError(null);
              setSetupStep("credentials");
            }}
          >
            <Fingerprint aria-hidden /> {t("worker.setup.title")}
          </Button>
        </EmptyState>
      </div>
    );
  }

  if (selected === null) {
    return (
      <div className="mx-auto max-w-xl space-y-4 p-6">
        <h1 className="text-2xl font-semibold">{t("worker.login.title")}</h1>
        <p className="text-muted-foreground">{t("worker.login.description")}</p>
        {offlineShiftAvailable && <a href="/worker/offline" className="inline-flex min-h-11 items-center rounded-lg border px-4 py-2 font-medium">{t("worker.offlineShift.continue")}</a>}
        {rosterQuery.isPending ? (
          <p role="status" aria-live="polite" className="text-muted-foreground">
            {t("common.loading")}
          </p>
        ) : !roster ? (
          <EmptyState
            icon={ClipboardList}
            title={t("common.somethingWentWrong")}
            description={t("worker.login.loadFailed")}
          >
            <Button variant="outline" onClick={() => void rosterQuery.refetch()}>
              {t("worker.login.retry")}
            </Button>
          </EmptyState>
        ) : roster.items.length === 0 ? (
          <EmptyState
            icon={Fingerprint}
            title={t("worker.needFarm.title")}
            description={t("worker.login.loadFailed")}
          />
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2" data-testid="worker-roster">
            {roster.items.map((entry) => (
              <li key={entry.membership_id}>
                <Button
                  variant="outline"
                  className="h-16 w-full justify-center text-lg"
                  onClick={() => {
                    cancelAttempt();
                    setSelected(entry);
                    setPin("");
                    setError(null);
                    setBusy(false);
                  }}
                >
                  {entry.display_name}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {roster && <nav aria-label={t("worker.login.title")} className="flex gap-3">
          <Button variant="outline" className="h-11" disabled={rosterQuery.isFetching || rosterPages.farmId !== tabletFarmId || rosterPages.cursors.length <= 1}
            onClick={() => setRosterPages((pages) => ({ farmId: tabletFarmId, cursors: pages.cursors.slice(0, -1) }))}>
            {t("pagination.previous")}
          </Button>
          <Button variant="outline" className="h-11" disabled={rosterQuery.isFetching || roster.next_after_membership_id == null}
            onClick={() => { if (roster.next_after_membership_id != null) setRosterPages((pages) => ({ farmId: tabletFarmId, cursors: [...(pages.farmId === tabletFarmId ? pages.cursors : [0]), roster.next_after_membership_id!] })); }}>
            {t("pagination.next")}
          </Button>
        </nav>}
        {/* Unpinning is a destructive action on a shared tablet: it needs a
            deliberate confirm, not one stray tap (2026-09-28 audit, W2). */}
        <Button variant="ghost" onClick={() => setUnpinOpen(true)}>
          {t("worker.login.backToFarms")}
        </Button>
        <Dialog open={unpinOpen} onOpenChange={setUnpinOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("worker.login.unpinTitle")}</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">
              {t("worker.login.unpinBody")}
            </p>
            <DialogFooter>
              <Button variant="outline" onClick={() => setUnpinOpen(false)}>
                {t("common.cancel")}
              </Button>
              <Button
                variant="destructive"
                onClick={confirmUnpin}
                data-testid="worker-unpin-confirm"
              >
                {t("worker.login.unpinConfirm")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-sm space-y-6 p-6" data-testid="worker-pin-pad">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">{selected.display_name}</h1>
        <p className="text-muted-foreground">{t("worker.login.description")}</p>
      </div>

      <div className="space-y-2">
        <p className="text-center font-mono text-3xl tracking-[0.5em]" data-testid="pin-dots">
          {"•".repeat(pin.length) || "—"}
        </p>
        {error && (
          <p role="alert" className="text-center text-destructive">
            {error}
          </p>
        )}
      </div>

      <div className="grid grid-cols-3 gap-3">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((digit) => (
          <Button
            key={digit}
            variant="outline"
            className="h-16 text-2xl"
            onClick={() => pressDigit(digit)}
            aria-label={`${t("worker.login.pinLabel")} ${digit}`}
            data-testid={`pin-key-${digit}`}
          >
            {digit}
          </Button>
        ))}
        <Button
          variant="ghost"
          className="h-16"
          onClick={() => setPin(pin.slice(0, -1))}
          aria-label={t("worker.login.deleteDigit")}
        >
          <Delete aria-hidden className="size-6" />
        </Button>
        <Button
          variant="outline"
          className="h-16 text-2xl"
          onClick={() => pressDigit("0")}
          data-testid="pin-key-0"
        >
          0
        </Button>
        <Button
          className="h-16 text-lg"
          disabled={busy || pin.length < 4}
          onClick={() => void submit(pin)}
          data-testid="pin-sign-in"
        >
          {t("worker.login.signIn")}
        </Button>
      </div>

      <Button
        variant="ghost"
        className="w-full"
        onClick={backToRoster}
      >
        {/* No glyph decorations in UI copy — the title alone is the back
            action (the stray "←" contradicted this page's own convention,
            2026-10-01 audit, 05-3). */}
        {t("worker.login.title")}
      </Button>
    </div>
  );
}
