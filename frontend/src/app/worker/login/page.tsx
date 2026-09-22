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
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { LoginOut, TokenOut } from "@/api/generated/models";
import { useWorkerRosterApiAuthWorkerRosterGet } from "@/api/generated/endpoints";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch, ApiError } from "@/lib/api-client";
import { useAuth, type FarmEntry } from "@/lib/auth-context";
import { useT } from "@/lib/i18n";
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
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- localStorage exists only client-side; reading it during render would break SSR hydration
    setTabletFarmId(readTabletFarmId());
  }, []);

  const rosterQuery = useWorkerRosterApiAuthWorkerRosterGet(
    { farm_id: tabletFarmId ?? 0 },
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
    try {
      const body = await apiFetch<{ access_token: string; user: { id: number; email: string; name: string | null } }>(
        "/api/auth/worker-login",
        {
          method: "POST",
          body: JSON.stringify({
            farm_id: tabletFarmId,
            membership_id: selected.membership_id,
            pin: finalPin,
          }),
        },
      );
      await signIn(body.access_token, {
        id: body.user.id,
        email: body.user.email,
        name: body.user.name,
      });
      // signIn's farm discovery auto-selects list[0] when nothing is stored;
      // a pinned tablet belongs on ITS farm. getFarms() reads the list this
      // signIn just committed (React state has not re-rendered yet), and a
      // worker who is not actually a member of the pinned farm keeps the
      // discovery fallback rather than being forced into a farm they lack.
      const pinned = getFarms().find((farm) => farm.id === tabletFarmId);
      if (pinned) selectFarm(pinned.id, pinned.timezone);
      toast.success(`${selected.display_name} ✓`);
      router.replace("/worker");
    } catch (error) {
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
      setBusy(false);
    }
  }

  async function submitSetupCredentials() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const body = await apiFetch<LoginOut>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
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
      await signIn(body.access_token, body.user);
      setSetupStep("choose-farm");
    } catch {
      setError(t("worker.setup.failed"));
    } finally {
      setBusy(false);
    }
  }

  async function submitSetupCode() {
    if (busy || mfaToken === null) return;
    setBusy(true);
    setError(null);
    try {
      const body = await apiFetch<TokenOut>("/api/auth/totp/challenge", {
        method: "POST",
        body: JSON.stringify({ mfa_token: mfaToken, code }),
      });
      setupRef.current = true;
      await signIn(body.access_token, body.user);
      setMfaToken(null);
      setSetupStep("choose-farm");
    } catch {
      setCode("");
      setError(t("worker.setup.failed"));
    } finally {
      setBusy(false);
    }
  }

  async function pinFarm(farm: FarmEntry) {
    writeTabletFarmId(farm.id);
    setTabletFarmId(farm.id);
    setSetupStep(null);
    setMfaToken(null);
    toast.success(t("worker.setup.pinnedToast"));
    // The manager's session must not linger on a shared tablet: ending it
    // (which also wipes any offline queue) leaves only the worker PIN door.
    await signOut();
    setupRef.current = false;
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
                    setSelected(entry);
                    setPin("");
                    setError(null);
                  }}
                >
                  {entry.display_name}
                </Button>
              </li>
            ))}
          </ul>
        )}
        <Button
          variant="ghost"
          onClick={() => {
            try {
              window.localStorage.removeItem(TABLET_FARM_STORAGE_KEY);
            } catch {
              /* nothing persisted */
            }
            setTabletFarmId(null);
          }}
        >
          {t("worker.login.backToFarms")}
        </Button>
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
          aria-label="Delete digit"
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
        onClick={() => {
          setSelected(null);
          setPin("");
          setError(null);
        }}
      >
        ← {t("worker.login.title")}
      </Button>
    </div>
  );
}
