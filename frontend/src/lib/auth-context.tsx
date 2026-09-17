"use client";

/**
 * Auth + active-farm state. The access token lives in memory (api-client);
 * on first load we silently try /api/auth/refresh (httpOnly cookie). The
 * selected farm persists in localStorage and is sent as X-Farm-Id.
 */

import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { FarmOut, UserOut } from "@/api/generated/models";
import {
  apiFetch,
  authSessionEpochValue,
  refreshSession,
  setAccessToken,
  setCurrentFarmId,
  setOnAuthFailure,
} from "@/lib/api-client";
import { clearPersistedIdempotencyRequestState } from "@/lib/idempotent-request";
import { setActiveFarmTimezone } from "@/lib/format";

// Raw auth responses stay anchored to generated contract models so backend
// schema drift breaks tsc here instead of silently diverging.
export type SessionUser = UserOut;
export type FarmEntry = FarmOut;

export interface AuthState {
  user: SessionUser | null;
  farms: FarmEntry[];
  farmId: number | null;
  loading: boolean;
  selectFarm: (farmId: number, timezone?: string) => void;
  signIn: (accessToken: string, user: SessionUser) => Promise<void>;
  signOut: () => Promise<void>;
  refreshFarms: () => Promise<void>;
  /** Replace the in-memory user after a self-service change (e.g. the
   *  must-change-password flag clearing on rotation) without re-running
   *  session establishment. */
  updateUser: (user: SessionUser) => void;
  /** Synchronous read of the freshest membership list. React state only
   *  updates on re-render, but a login continuation needs the list its own
   *  signIn call just committed — e.g. to skip a farmless account's doomed
   *  /api/auth/permissions request (no X-Farm-Id → 422). */
  getFarms: () => FarmEntry[];
}

const AuthContext = createContext<AuthState | null>(null);
// Stryker disable next-line StringLiteral: a module-level initializer cannot be attributed to the asserting test by per-test coverage; the key is pinned verbatim by the persistence suite
const FARM_STORAGE_KEY = "goatfarm.farmId";
// Stryker disable next-line ArrayDeclaration, StringLiteral: a module-level initializer cannot be attributed to the asserting test by per-test coverage; the list is pinned by the redirect suite
const PUBLIC_PATHS = ["/login", "/register"];

/** Site data can be blocked for the origin (reading `window.localStorage`
 * itself throws SecurityError) or over quota. The farm selection is a
 * convenience — degrade to "nothing persisted" rather than throwing out of a
 * session transition, mirroring idempotent-request's availableSessionStorage.
 */
function readStoredFarmId(): number | null {
  try {
    if (typeof window === "undefined") return null;
    const raw = window.localStorage.getItem(FARM_STORAGE_KEY);
    if (raw === null) return null;
    const stored = Number(raw);
    // Stryker disable BlockStatement: the catch's only statement returns null, and the sole caller reads this via ??, which treats the mutant's undefined exactly like null
    return Number.isSafeInteger(stored) && stored > 0 ? stored : null;
  } catch {
    return null;
  }
  // Stryker restore BlockStatement
}

function writeStoredFarmId(id: number): void {
  try {
    window.localStorage.setItem(FARM_STORAGE_KEY, String(id));
  } catch {
    /* blocked or over quota: the selection just won't survive a reload */
  }
}

function clearStoredFarmId(): void {
  try {
    window.localStorage.removeItem(FARM_STORAGE_KEY);
  } catch {
    /* nothing was persisted to clear */
  }
}

function isAuthSessionChangedError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: unknown }).name === "AuthSessionChangedError"
  );
}

function providerUnmountedError(): Error {
  const error = new Error("The authentication provider was unmounted.");
  error.name = "AbortError";
  return error;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [farms, setFarms] = useState<FarmEntry[]>([]);
  const farmsRef = useRef<FarmEntry[]>([]);
  const [farmId, setFarmIdState] = useState<number | null>(null);
  // Keep the live choice independently of localStorage. Persistence is only
  // a convenience and may be blocked; an in-flight membership refresh must
  // not revert a farm the operator selected while that request was running.
  const farmIdRef = useRef<number | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  // Guards the forced-logout path: N concurrent 401s with a failed refresh
  // must run the cleanup once, not N times.
  const forcedLogout = useRef(false);
  // React 19 Strict Mode fires the mount effect twice in dev; refresh tokens
  // are one-time-use, so the second /api/auth/refresh would 401 and its
  // finally block would flip loading=false before the first call's
  // refreshFarms had a chance to set farmId — misfiring the /farm-select
  // redirect. Guard the initial refresh to at most one in-flight call.
  const initialRefreshStarted = useRef(false);
  // Stryker disable next-line BooleanLiteral: the mount effect below overwrites the initial value before any continuation can observe it
  const mounted = useRef(true);
  // Latest-wins fence for explicit membership refreshes. Two reads can
  // observe different server snapshots and arrive in reverse order.
  const farmRefreshGeneration = useRef(0);
  // Identity establishment and membership snapshots have related but
  // distinct ownership. An explicit refresh may supersede an establishment's
  // older farm list, but it cannot commit the staged token's UserOut itself.
  const sessionEstablishmentGeneration = useRef(0);
  // Starting a newer refresh is not the same as successfully applying it. An
  // establishment may use its older-but-valid list as a fallback while that
  // refresh is pending or after it fails; only a committed newer snapshot
  // suppresses the establishment list permanently.
  const appliedFarmGeneration = useRef(0);
  const signedOutRedirectIntent = useRef<string | null>(null);
  const signOutFlight = useRef<{
    /** Epoch immediately after this flight cleared its owning session. */
    teardownEpoch: number;
    task: Promise<void>;
  } | null>(null);

  // Stryker disable ArrayDeclaration: a constant string dep never changes, so the effect still runs exactly once
  useEffect(() => {
    // React Strict Mode rehearses cleanup/setup without discarding refs.
    mounted.current = true;
    return () => {
      mounted.current = false;
      farmRefreshGeneration.current += 1;
      // Stryker disable next-line AssignmentOperator: only observable in a StrictMode double-mount rehearsal; the refs die with the instance on a real unmount
      sessionEstablishmentGeneration.current += 1;
    };
  }, []);
  // Stryker restore ArrayDeclaration

  const selectFarm = useCallback(
    (id: number, timezone?: string) => {
      if (!mounted.current) return;
      // Cancel in-flight queries BEFORE clearing: cache keys are URL-only
      // (no farm id), so any request that was already on the wire with the
      // OLD X-Farm-Id header would otherwise resolve into the fresh cache
      // and briefly render previous-farm data.
      queryClient.cancelQueries();
      queryClient.clear();
      farmIdRef.current = id;
      setFarmIdState(id);
      setCurrentFarmId(String(id));
      // FarmOut.timezone is required on the generated contract, so the
      // cached entry carries the authoritative zone without a local widen.
      const selected = farmsRef.current.find((farm) => farm.id === id);
      setActiveFarmTimezone(timezone ?? selected?.timezone);
      writeStoredFarmId(id);
    },
    [queryClient],
  );

  /** Full local session teardown — shared by signOut and the forced-logout
   *  (refresh rejected, e.g. a rotated/reused refresh token now 401s) path so
   *  both behave identically. */
  const clearSession = useCallback(() => {
    farmRefreshGeneration.current += 1;
    // Stryker disable next-line AssignmentOperator: any clearSession that could race an in-flight establishment first flips the access token (null), whose epoch bump already invalidates that establishment on both its resolved and rejected paths
    sessionEstablishmentGeneration.current += 1;
    queryClient.clear();
    clearPersistedIdempotencyRequestState();
    setAccessToken(null);
    setCurrentFarmId(null);
    setActiveFarmTimezone(null);
    setUser(null);
    farmsRef.current = [];
    setFarms([]);
    farmIdRef.current = null;
    setFarmIdState(null);
    // Last, so the in-memory teardown above can never be left half applied.
    clearStoredFarmId();
  }, [queryClient]);

  const signOut = useCallback((): Promise<void> => {
    if (!mounted.current) return Promise.resolve();
    const existingFlight = signOutFlight.current;
    // Coalesce duplicate requests only while the locally signed-out session
    // created by that flight is still current. A new sign-in advances the
    // auth epoch; its later sign-out must not be swallowed by an older,
    // slow /logout request that is still waiting on the network.
    if (
      existingFlight &&
      existingFlight.teardownEpoch === authSessionEpochValue()
    ) {
      return existingFlight.task;
    }
    // Suppress the generic signed-out redirect effect for this explicit
    // transition; signOut owns the one navigation below.
    forcedLogout.current = true;
    // Fire the revocation while the bearer token is still installed, but
    // never block local teardown on it. A request that neither resolves nor
    // rejects would otherwise leave a shared terminal signed in.
    const revoked = apiFetch("/api/auth/logout", { method: "POST" }).catch(() => {
      /* cookie may already be gone */
    });
    clearSession();
    router.replace("/login");
    const task = (async () => {
      await revoked;
    })();
    const flight = { teardownEpoch: authSessionEpochValue(), task };
    signOutFlight.current = flight;
    const release = () => {
      if (signOutFlight.current === flight) signOutFlight.current = null;
    };
    void task.then(release, release);
    return task;
  }, [router, clearSession]);

  const applyFarmList = useCallback((list: FarmEntry[]) => {
    if (!mounted.current) return;
    farmsRef.current = list;
    setFarms(list);
    const preferred = farmIdRef.current ?? readStoredFarmId();
    if (preferred === null && list.length > 0) {
      // No choice exists anywhere (first sign-in on this device): the
      // server's ordering is the only signal, so auto-select list[0] and let
      // single-farm operators skip the picker entirely.
      const [first] = list;
      selectFarm(first.id, first.timezone);
      return;
    }
    const valid = preferred === null ? undefined : list.find((f) => f.id === preferred);
    if (valid) {
      selectFarm(valid.id, valid.timezone);
      return;
    }
    // The preferred farm was revoked (or the list emptied entirely). A farm
    // transition is still happening: abort and drop URL-only query keys
    // before an old-farm response can repopulate them, and remove the
    // now-invalid persisted selection. Deliberately do NOT fall back to
    // list[0] — silently landing the operator in a farm they never chose
    // invites acting on the wrong herd's data. farmId stays null, so the
    // app shell redirects to /farm-select and the membership list rendered
    // above makes the choice an explicit one.
    queryClient.cancelQueries();
    queryClient.clear();
    farmIdRef.current = null;
    setFarmIdState(null);
    setCurrentFarmId(null);
    setActiveFarmTimezone(null);
    clearStoredFarmId();
  }, [queryClient, selectFarm]);

  const refreshFarms = useCallback(async () => {
    if (!mounted.current) return;
    const generation = ++farmRefreshGeneration.current;
    let list: FarmEntry[];
    try {
      list = await apiFetch<FarmEntry[]>("/api/auth/farms");
    } catch (error) {
      // A superseded failure is no longer actionable: a newer refresh owns
      // both the displayed list and any error UI its caller might show.
      if (!mounted.current || generation !== farmRefreshGeneration.current) return;
      throw error;
    }
    if (!mounted.current || generation !== farmRefreshGeneration.current) return;
    appliedFarmGeneration.current = generation;
    applyFarmList(list);
  }, [applyFarmList]);

  /** Stage a token only long enough to discover its farms, then commit the
   * React session atomically. A transient farms failure must not leave the
   * login page claiming failure while `user` is already authenticated.
   *
   * `revokeOnFailure` decides whether the failure also destroys the server
   * session, and only the login/register path may ask for that. */
  const establishSession = useCallback(
    async (accessToken: string, u: SessionUser, revokeOnFailure: boolean) => {
      if (!mounted.current) throw providerUnmountedError();
      // Any membership read started by the previous session is stale even if
      // it later happens to resolve with a superficially valid list.
      const establishmentGeneration = ++sessionEstablishmentGeneration.current;
      const farmGeneration = ++farmRefreshGeneration.current;
      setAccessToken(accessToken, u.id);
      // Read AFTER installing the token: setAccessToken is what bumps the
      // epoch, so capturing it earlier would make every call supersede itself
      // and permanently skip the teardown below.
      const ownedEpoch = authSessionEpochValue();
      try {
        // FE-3 (2026-09-16): one transient failure of the post-login farms
        // read must not destroy the just-created session. Retry once before
        // any teardown path runs.
        let list: FarmEntry[];
        try {
          list = await apiFetch<FarmEntry[]>("/api/auth/farms");
        } catch (retryable) {
          if (
            !mounted.current ||
            establishmentGeneration !== sessionEstablishmentGeneration.current ||
            isAuthSessionChangedError(retryable) ||
            authSessionEpochValue() !== ownedEpoch
          ) {
            throw retryable;
          }
          await new Promise((resolve) => setTimeout(resolve, 750));
          list = await apiFetch<FarmEntry[]>("/api/auth/farms");
        }
        if (!mounted.current) throw providerUnmountedError();
        // A newer establishment owns both identity and membership. An
        // explicit refresh owns only the membership snapshot: it must not
        // strand this staged token with the previous (or no) React user.
        if (
          establishmentGeneration !== sessionEstablishmentGeneration.current
        ) return;
        setUser(u);
        if (
          farmGeneration !== farmRefreshGeneration.current &&
          // Stryker disable next-line EqualityOperator: appliedFarmGeneration is only ever set to a generation captured by refreshFarms, so it can never equal this establishment's freshly captured farmGeneration
          appliedFarmGeneration.current > farmGeneration
        ) return;
        appliedFarmGeneration.current = farmGeneration;
        applyFarmList(list);
      } catch (error) {
        if (
          !mounted.current ||
          establishmentGeneration !== sessionEstablishmentGeneration.current ||
          isAuthSessionChangedError(error) ||
          authSessionEpochValue() !== ownedEpoch
        ) {
          // A newer establishment or session already superseded this
          // call (e.g. a second sign-in raced this one's farms fetch); that
          // newer owner controls clearSession/revoke, so tearing down here
          // would destroy its state.
          // The error name alone is not enough: a transport failure or a
          // broken body stream rejects before any epoch assert can run, so
          // compare the epoch directly as well.
          throw error;
        }
        if (revokeOnFailure) {
          // Login/register already rotated an httpOnly refresh cookie. Revoke
          // that server session before reporting failure; otherwise a reload
          // could silently sign in after this supposedly transactional step.
          // Fired while the bearer is still installed but never awaited, for
          // the same reason as signOut: a black-holed connection would
          // otherwise hang the login form forever with a live token.
          void apiFetch("/api/auth/logout", { method: "POST" }).catch(() => {
            // A network-wide outage can also block logout; local state is
            // still cleared and the refresh cookie remains httpOnly.
          });
        }
        clearSession();
        throw error;
      }
    },
    [applyFarmList, clearSession],
  );

  const signIn = useCallback(
    async (accessToken: string, u: SessionUser) => {
      forcedLogout.current = false;
      await establishSession(accessToken, u, true);
      // `loading` was otherwise written only by the one-shot bootstrap IIFE's
      // finally. If that bootstrap is still parked on an unanswered
      // /api/auth/farms, a completed sign-in would render the app shell's
      // "Loading…" gate forever. A committed session outranks a pending
      // bootstrap, so release the gate here too; the bootstrap's own later
      // write is then a no-op.
      setLoading(false);
    },
    [establishSession],
  );

  // Registration owns a module-level singleton in api-client, so it must be
  // paired with a deregistration on EVERY invocation. Keeping it separate from
  // the once-only bootstrap below is what guarantees that.
  useEffect(() => {
    const handleAuthFailure = () => {
      // Fired when a refresh attempt is rejected (expired/revoked/reused
      // refresh token). One cleanup per logout transition, identical to
      // signOut's, then straight to /login — never a retry loop.
      if (forcedLogout.current) return;
      forcedLogout.current = true;
      clearSession();
      router.replace("/login");
    };
    return setOnAuthFailure(handleAuthFailure);
  }, [clearSession, router]);

  useEffect(() => {
    if (initialRefreshStarted.current) return;
    initialRefreshStarted.current = true;
    (async () => {
      try {
        const body = await refreshSession();
        // Stryker disable next-line ConditionalExpression, LogicalOperator: the false variant is regression-covered by the session-restore tests; the true variant is equivalent (a null body deref crashes into the same catch as skipping)
        if (body && mounted.current) {
          // A farms failure here must not revoke the refresh family this call
          // just rotated: the user is not watching an error message, so a
          // plain reload has to be able to recover the valid session.
          await establishSession(body.access_token, body.user, false);
        }
      } catch {
        // Refresh failed (network error or non-JSON body): stay signed out.
        // loading settles in finally and the redirect effect sends /login.
      } finally {
        // setState after unmount is a React no-op; no mounted re-check needed.
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const shouldRedirect =
      !loading &&
      !user &&
      !forcedLogout.current &&
      !PUBLIC_PATHS.includes(pathname);
    if (!shouldRedirect) {
      signedOutRedirectIntent.current = null;
      return;
    }
    const intent = `${pathname}->/login`;
    if (signedOutRedirectIntent.current === intent) return;
    signedOutRedirectIntent.current = intent;
    router.replace("/login");
  }, [loading, user, pathname, router]);

  // Stryker disable ArrayDeclaration: setUser is stable, so a constant dep list only changes the callback's identity, never its behavior
  const updateUser = useCallback((u: SessionUser) => {
    // setState after unmount is a React no-op; no mounted re-check needed.
    setUser(u);
  }, []);
  // Stryker restore ArrayDeclaration

  // Stryker disable next-line ArrayDeclaration: the callback reads only a ref; a constant dep list cannot change its behavior
  const getFarms = useCallback(() => farmsRef.current, []);

  const value = useMemo(
    () => ({
      user,
      farms,
      farmId,
      loading,
      selectFarm,
      signIn,
      signOut,
      refreshFarms,
      updateUser,
      getFarms,
    }),
    [user, farms, farmId, loading, selectFarm, signIn, signOut, refreshFarms, updateUser, getFarms],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
