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
  refreshSessionDetailed,
  setAccessToken,
  setCurrentFarmId,
  setOnAuthFailure,
} from "@/lib/api-client";
import { clearPersistedIdempotencyRequestState } from "@/lib/idempotent-request";
import { clearWorkerOutboxBackoff } from "@/lib/worker-outbox";
import { endOfflineShift } from "@/lib/worker-offline-shift";
import { safeStorage } from "@/lib/safe-storage";
import { setActiveFarmTimezone } from "@/lib/format";

// Raw auth responses stay anchored to generated contract models so backend
// schema drift breaks tsc here instead of silently diverging.
export type SessionUser = UserOut;
export type FarmEntry = FarmOut;

// Bootstrap retry budget for TRANSIENT refresh failures only.
const BOOTSTRAP_REFRESH_ATTEMPTS = 3;
const failedEstablishmentEpochs = new WeakMap<object, number>();

/** A rejected sign-in may have cleared its own staged session. This lets its
 * form release the attempt after that intentional epoch change, while an
 * unrelated/newer session still invalidates every old continuation. */
export function failedSessionEstablishmentEpoch(error: unknown): number | null {
  return error !== null && typeof error === "object"
    ? failedEstablishmentEpochs.get(error) ?? null : null;
}

export interface AuthState {
  user: SessionUser | null;
  farms: FarmEntry[];
  farmId: number | null;
  loading: boolean;
  selectFarm: (farmId: number, timezone?: string) => void;
  signIn: (accessToken: string, user: SessionUser, options?: { sessionOnly?: boolean }) => Promise<void>;
  signOut: (options?: { sessionOnly?: boolean }) => Promise<void>;
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
const FARM_STORAGE_KEY = "goatfarm.farmId";
const AUTH_EVENT_STORAGE_KEY = "goatfarm.authEvent";
const AUTH_BROADCAST_CHANNEL = "goatfarm.auth";
const AUTH_LOGOUT_EVENT_PREFIX = "logout:";
let authEventSequence = 0;
const PUBLIC_PATHS = ["/login", "/register", "/worker/login", "/worker/offline"];

/**
 * Forced and cross-tab logout returns worker surfaces to the PIN pad. PIN-only
 * workers cannot use the manager password form. Explicit sign-out keeps its
 * caller-selected destination.
 */
function forcedLogoutDestination(currentPathname: string): "/worker/login" | "/login" {
  return currentPathname.startsWith("/worker") ? "/worker/login" : "/login";
}

/** Site data can be blocked for the origin or over quota, and the realm is
 * missing under SSR. The farm selection is a convenience — degrade to
 * "nothing persisted" rather than throwing out of a session transition. The
 * guarded realm accessor is shared: lib/safe-storage.ts. */
function readStoredFarmId(): number | null {
  try {
    const raw = safeStorage("local")?.getItem(FARM_STORAGE_KEY) ?? null;
    if (raw === null) return null;
    const stored = Number(raw);
    return Number.isSafeInteger(stored) && stored > 0 ? stored : null;
  } catch {
    return null;
  }
}

function writeStoredFarmId(id: number): void {
  try {
    safeStorage("local")?.setItem(FARM_STORAGE_KEY, String(id));
  } catch {
    /* blocked or over quota: the selection just won't survive a reload */
  }
}

function clearStoredFarmId(): void {
  try {
    safeStorage("local")?.removeItem(FARM_STORAGE_KEY);
  } catch {
    /* nothing was persisted to clear */
  }
}

/** Publish session death independently of farm selection. Removing an
 * already-absent farm key emits no storage event, so farmless accounts need
 * their own always-changing signal. BroadcastChannel covers browsers where
 * site storage is blocked; the storage event remains the broad fallback. */
function publishCrossTabLogout(): void {
  authEventSequence += 1;
  const event = `${AUTH_LOGOUT_EVENT_PREFIX}${Date.now()}:${authEventSequence}`;
  try {
    safeStorage("local")?.setItem(AUTH_EVENT_STORAGE_KEY, event);
  } catch {
    /* BroadcastChannel below may still be available. */
  }
  try {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(AUTH_BROADCAST_CHANNEL);
    channel.postMessage(event);
    channel.close();
  } catch {
    /* The visibility-validation fallback handles fully blocked environments. */
  }
}

// A key REMOVAL is the cross-tab signal for "the session is dead — mirror the teardown"
// (signOut / clearSession both remove the key). A farm revocation must broadcast
// something else entirely: "this selection is gone but the session lives". Tombstoning
// the value instead of removing it lets the storage listener tell the two apart, so a
// revoked membership sends the other tabs to /farm-select instead of destroying valid
// sessions. readStoredFarmId already treats the tombstone as "no selection" because
// Number("revoked:…") is NaN.
const FARM_STORAGE_REVOKED_PREFIX = "revoked:";

function writeStoredFarmRevoked(id: number): void {
  try {
    safeStorage("local")?.setItem(
      FARM_STORAGE_KEY,
      `${FARM_STORAGE_REVOKED_PREFIX}${id}`,
    );
  } catch {
    /* blocked or over quota: the tombstone just won't persist */
  }
}

function revokedFarmIdFromStorage(value: string | null): number | null {
  if (value === null || !value.startsWith(FARM_STORAGE_REVOKED_PREFIX)) {
    return null;
  }
  const stored = Number(value.slice(FARM_STORAGE_REVOKED_PREFIX.length));
  return Number.isSafeInteger(stored) && stored > 0 ? stored : null;
}

/** This tab's own read of the revoke tombstone (storage events only fire in
 * OTHER tabs, so a freshly opened tab must look at the raw value itself). */
function readStoredFarmRevokedId(): number | null {
  try {
    return revokedFarmIdFromStorage(
      safeStorage("local")?.getItem(FARM_STORAGE_KEY) ?? null,
    );
  } catch {
    return null;
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
  const [user, setUserState] = useState<SessionUser | null>(null);
  // Cross-tab events can arrive after a session commits but before React's
  // passive effects. Publish this identity together with the state update.
  const userRef = useRef<SessionUser | null>(null);
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

  const commitUser = useCallback((next: SessionUser | null) => {
    userRef.current = next;
    setUserState(next);
  }, []);

  useEffect(() => {
    // React Strict Mode rehearses cleanup/setup without discarding refs.
    mounted.current = true;
    return () => {
      mounted.current = false;
      farmRefreshGeneration.current += 1;
      sessionEstablishmentGeneration.current += 1;
    };
  }, []);

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

  /** Tear down credentials, views, and the cached shift on every session
   * exit. Saved actions retain their original actor/farm and idempotency key;
   * the next user cannot view or send them. Only a server acknowledgement
   * settles an action. A logout never deletes unresolved field work. */
  const clearSession = useCallback(
    () => {
      farmRefreshGeneration.current += 1;
      sessionEstablishmentGeneration.current += 1;
      queryClient.clear();
      clearPersistedIdempotencyRequestState();
      setAccessToken(null);
      setCurrentFarmId(null);
      setActiveFarmTimezone(null);
      commitUser(null);
      farmsRef.current = [];
      setFarms([]);
      farmIdRef.current = null;
      setFarmIdState(null);
      // Last, so the in-memory teardown above can never be left half applied.
      clearStoredFarmId();
      // Pending actor/farm scoped writes survive every session teardown.
      // Clearing identity and query state prevents the next worker from
      // seeing/replaying them; deletion is not a prerequisite for handover.
      // The 429 drain backoff belongs to the session either way — leaving it
      // set would gate the next actor's first drain behind the previous
      // actor's Retry-After hint.
      clearWorkerOutboxBackoff();
      void endOfflineShift().catch(() => {});
    },
    [queryClient, commitUser],
  );

  const signOut = useCallback((options?: { sessionOnly?: boolean }): Promise<void> => {
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
    publishCrossTabLogout();
    // Fire the revocation while the bearer token is still installed, but
    // never block local teardown on it. A request that neither resolves nor
    // rejects would otherwise leave a shared terminal signed in.
    const revoked = apiFetch(options?.sessionOnly ? "/api/auth/logout-session" : "/api/auth/logout", { method: "POST" }).catch(() => {
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
      if (readStoredFarmRevokedId() !== null) {
        // A revoke tombstone says "your farm was revoked — choose explicitly"
        // in EVERY tab, including one opened after the revocation. Falling
        // back to list[0] here would auto-land this tab on a farm the
        // revoked tab deliberately refused to enter, splitting the tabs
        // across farms. farmId stays null so the shell sends everyone to
        // /farm-select; the tombstone is consumed by the explicit selection
        // that follows (selectFarm overwrites the key) or by sign-out
        // teardown — never by removeItem, which other tabs read as a
        // session teardown.
        queryClient.cancelQueries();
        queryClient.clear();
        setCurrentFarmId(null);
        setActiveFarmTimezone(null);
        return;
      }
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
    // before an old-farm response can repopulate them, and tombstone the
    // now-invalid persisted selection so other tabs learn the farm was
    // revoked without mistaking it for a session teardown. Deliberately do
    // NOT fall back to list[0] — silently landing the operator in a farm
    // they never chose invites acting on the wrong herd's data. farmId
    // stays null, so the app shell redirects to /farm-select and the
    // membership list rendered above makes the choice an explicit one.
    queryClient.cancelQueries();
    queryClient.clear();
    farmIdRef.current = null;
    setFarmIdState(null);
    setCurrentFarmId(null);
    setActiveFarmTimezone(null);
    if (preferred !== null) writeStoredFarmRevoked(preferred);
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
    async (accessToken: string, u: SessionUser, revokeOnFailure: boolean, sessionOnly = false) => {
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
        // One transient failure of the post-login farms read must not destroy the
        // just-created session. Retry once before any teardown path runs.
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
        commitUser(u);
        if (
          farmGeneration !== farmRefreshGeneration.current &&
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
          // Revoke the staged grant before reporting failure. Normal sign-in
          // rotated a refresh cookie; transient tablet setup instead revokes
          // only its exact bearer family without touching that cookie.
          // Fired while the bearer is still installed but never awaited, for
          // the same reason as signOut: a black-holed connection would
          // otherwise hang the login form forever with a live token.
          void apiFetch(sessionOnly ? "/api/auth/logout-session" : "/api/auth/logout", { method: "POST" }).catch(() => {
            // A network-wide outage can also block logout; local state is
            // still cleared and the refresh cookie remains httpOnly.
          });
        }
        // A failed establishment is a session death, not a handover choice: queued
        // writes (possibly this actor's, preserved by an earlier forced logout) must
        // survive the retry.
        clearSession();
        if (error !== null && typeof error === "object") {
          failedEstablishmentEpochs.set(error, authSessionEpochValue());
        }
        throw error;
      }
    },
    [applyFarmList, clearSession, commitUser],
  );

  const signIn = useCallback(
    async (accessToken: string, u: SessionUser, options?: { sessionOnly?: boolean }) => {
      forcedLogout.current = false;
      await establishSession(accessToken, u, true, options?.sessionOnly);
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
      // Fired when a refresh attempt is rejected (expired/revoked/reused refresh
      // token). One cleanup per logout transition, identical to signOut's, then to the
      // surface-appropriate login — never a retry loop. window.location over a pathname
      // closure: this handler is registered once and must decide from where the failure
      // actually fired. Session death, not an explicit handover: the queued offline
      // writes must survive for redelivery after re-login — the drain never replays
      // them under a different actor.
      if (forcedLogout.current) return;
      forcedLogout.current = true;
      publishCrossTabLogout();
      clearSession();
      router.replace(forcedLogoutDestination(window.location.pathname));
    };
    return setOnAuthFailure(handleAuthFailure);
  }, [clearSession, router]);

  // Cross-tab sync: localStorage storage events fire only in OTHER tabs, which is
  // exactly the audience — a farm switch or a sign-out in one tab previously left every
  // other tab on the stale farm (its next write targeted the previous tenant) or on a
  // dead session.
  useEffect(() => {
    const mirrorLogout = () => {
      if (userRef.current === null || forcedLogout.current) return;
      forcedLogout.current = true;
      clearSession();
      router.replace(forcedLogoutDestination(window.location.pathname));
    };
    const onStorage = (event: StorageEvent) => {
      if (
        event.key === AUTH_EVENT_STORAGE_KEY &&
        event.newValue?.startsWith(AUTH_LOGOUT_EVENT_PREFIX)
      ) {
        mirrorLogout();
        return;
      }
      if (event.key !== FARM_STORAGE_KEY) return;
      if (event.newValue === null) {
        // Another tab tore its session down (clearSession removes the key):
        // mirror the forced-logout cleanup here instead of letting requests
        // 401 one by one against a revoked family. The queue-wipe decision
        // belongs to its original actor/farm; every form of session teardown
        // retains unresolved work, including this mirrored transition.
        mirrorLogout();
        return;
      }
      const revokedId = revokedFarmIdFromStorage(event.newValue);
      if (revokedId !== null) {
        // Another tab discovered this farm was revoked. The session itself is
        // still valid: drop only this tab's selection (and the stale list
        // entry) so the app shell lands on /farm-select — never the teardown
        // a key removal announces. Tabs sitting on a different farm are
        // unaffected.
        if (userRef.current === null) return;
        if (farmIdRef.current !== revokedId) return;
        queryClient.cancelQueries();
        queryClient.clear();
        farmIdRef.current = null;
        setFarmIdState(null);
        setCurrentFarmId(null);
        setActiveFarmTimezone(null);
        const remaining = farmsRef.current.filter(
          (farm) => farm.id !== revokedId,
        );
        farmsRef.current = remaining;
        setFarms(remaining);
        return;
      }
      const stored = Number(event.newValue);
      if (
        Number.isSafeInteger(stored) &&
        stored > 0 &&
        stored !== farmIdRef.current &&
        farmsRef.current.some((farm) => farm.id === stored)
      ) {
        selectFarm(stored);
      }
    };
    let channel: BroadcastChannel | null = null;
    try {
      if (typeof BroadcastChannel !== "undefined") {
        channel = new BroadcastChannel(AUTH_BROADCAST_CHANNEL);
        channel.addEventListener("message", (event: MessageEvent<unknown>) => {
          if (
            typeof event.data === "string" &&
            event.data.startsWith(AUTH_LOGOUT_EVENT_PREFIX)
          ) {
            mirrorLogout();
          }
        });
      }
    } catch {
      channel = null;
    }

    let storageAvailable = false;
    try {
      const storage = safeStorage("local");
      if (storage) {
        const probeKey = `${AUTH_EVENT_STORAGE_KEY}.probe`;
        storage.setItem(probeKey, "1");
        storage.removeItem(probeKey);
        storageAvailable = true;
      }
    } catch {
      storageAvailable = false;
    }
    const validateVisibleSession = () => {
      if (document.visibilityState !== "visible" || userRef.current === null) return;
      void apiFetch<SessionUser>("/api/auth/me").catch(() => {
        // Authoritative 401s invoke the registered auth-failure handler;
        // transient/network failures deliberately preserve the session.
      });
    };
    if (!storageAvailable && channel === null) {
      document.addEventListener("visibilitychange", validateVisibleSession);
    }
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("storage", onStorage);
      document.removeEventListener("visibilitychange", validateVisibleSession);
      channel?.close();
    };
  }, [clearSession, router, selectFarm, queryClient]);

  useEffect(() => {
    if (initialRefreshStarted.current) return;
    initialRefreshStarted.current = true;
    (async () => {
      // The cached offline-shift route is deliberately useful without a
      // server session. Release its render gate before touching the network:
      // captive portals and black-holed Wi-Fi often report navigator.onLine
      // as true, and three refresh timeouts must not hide committed duties.
      const releaseOfflineGate = pathname === "/worker/offline";
      if (releaseOfflineGate) setLoading(false);
      try {
        // Offline cached pages have no server authentication. Release the
        // loading gate immediately so the bounded shift can be recovered;
        // a refresh timeout must not hide already committed local work.
        if (typeof navigator !== "undefined" && navigator.onLine === false) return;
        // "unavailable" (5xx/408/429/network/non-JSON) is NOT the server saying the
        // session is over — collapsing it into the signed-out path let one transient
        // blip at tab-open sign the operator out of a perfectly valid session. Retry
        // those with a short backoff; only an authoritative "rejected" (or an exhausted
        // retry budget) may land in the signed-out redirect.
        let body: Awaited<ReturnType<typeof refreshSession>> = null;
        for (let attempt = 0; attempt < BOOTSTRAP_REFRESH_ATTEMPTS; attempt += 1) {
          const outcome = await refreshSessionDetailed();
          if (outcome.kind === "session") {
            body = outcome.body;
            break;
          }
          if (outcome.kind === "rejected") {
            await endOfflineShift().catch(() => {});
            break;
          }
          if (attempt < BOOTSTRAP_REFRESH_ATTEMPTS - 1) {
            await new Promise((resolve) => window.setTimeout(resolve, 750));
          }
        }
        if (body && mounted.current) {
          // A farms failure here must not revoke the refresh family this call
          // just rotated: the user is not watching an error message, so a
          // plain reload has to be able to recover the valid session.
          await establishSession(body.access_token, body.user, false);
        }
      } catch {
        // Refresh threw (client-side transport error): stay signed out.
        // loading settles in finally and the redirect effect sends /login.
      } finally {
        // setState after unmount is a React no-op; no mounted re-check needed.
        if (!releaseOfflineGate) setLoading(false);
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
      // The explicit-logout transition has landed once the signed-out user sits on a
      // public path: release the latch so a later client-side Back navigation to a
      // protected route redirects to /login again instead of parking on the app gate's
      // loading skeleton forever (release the latch after each completed logout).
      if (!loading && !user && forcedLogout.current && PUBLIC_PATHS.includes(pathname)) {
        forcedLogout.current = false;
      }
      signedOutRedirectIntent.current = null;
      return;
    }
    const destination = forcedLogoutDestination(pathname);
    const intent = `${pathname}->${destination}`;
    if (signedOutRedirectIntent.current === intent) return;
    signedOutRedirectIntent.current = intent;
    router.replace(destination);
  }, [loading, user, pathname, router]);

  const updateUser = useCallback((u: SessionUser) => {
    // setState after unmount is a React no-op; no mounted re-check needed.
    commitUser(u);
  }, [commitUser]);

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
