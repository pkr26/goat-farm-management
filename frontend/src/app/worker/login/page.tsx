"use client";

/**
 * Worker tablet sign-in (ITEM 2 Phase 2): tap your name, enter your PIN.
 *
 * The farm is pinned once by a manager (localStorage); the roster endpoint is
 * unauthenticated, so this page works before any session exists. Success runs
 * the normal signIn() so the whole app's session machinery is shared.
 */

import { ClipboardList, Delete, Fingerprint } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { useWorkerRosterApiAuthWorkerRosterGet } from "@/api/generated/endpoints";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { useT } from "@/lib/i18n";
import { TABLET_FARM_STORAGE_KEY, readTabletFarmId } from "@/app/worker/layout";

type RosterEntry = { membership_id: number; display_name: string };



export default function WorkerLoginPage() {
  const t = useT();
  const router = useRouter();
  const { signIn, farmId } = useAuth();
  const [tabletFarmId, setTabletFarmId] = useState<number | null>(null);
  const [selected, setSelected] = useState<RosterEntry | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
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
    if (farmId !== null && tabletFarmId !== null && farmId === tabletFarmId) {
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
      toast.success(`${selected.display_name} ✓`);
      router.replace("/worker");
    } catch {
      setPin("");
      setError(t("worker.login.wrongPin"));
    } finally {
      setBusy(false);
    }
  }

  function pressDigit(digit: string) {
    if (busy) return;
    const next = (pin + digit).slice(0, 12);
    setPin(next);
    setError(null);
  }

  if (tabletFarmId === null) {
    return (
      <div className="flex min-h-dvh items-center justify-center p-6">
        <EmptyState
          icon={ClipboardList}
          title={t("worker.needFarm.title")}
          description={t("worker.needFarm.description")}
        />
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
