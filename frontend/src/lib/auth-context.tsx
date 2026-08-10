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
  refreshSession,
  setAccessToken,
  setCurrentFarmId,
  setOnAuthFailure,
} from "@/lib/api-client";
import { setActiveFarmTimezone } from "@/lib/format";

// Derived from the generated contract models so backend schema drift breaks
// tsc here instead of silently diverging.
export type SessionUser = UserOut;
/** /api/auth/farms entries always carry `role` (null = owner). */
export type FarmEntry = FarmOut & { role: string | null };

interface AuthState {
  user: SessionUser | null;
  farms: FarmEntry[];
  farmId: number | null;
  loading: boolean;
  selectFarm: (farmId: number, timezone?: string) => void;
  signIn: (accessToken: string, user: SessionUser) => Promise<void>;
  signOut: () => Promise<void>;
  refreshFarms: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);
const FARM_STORAGE_KEY = "goatfarm.farmId";
const PUBLIC_PATHS = ["/login", "/register"];

/** Site data can be blocked for the origin (reading `window.localStorage`
 * itself throws SecurityError) or over quota. The farm selection is a
 * convenience — degrade to "nothing persisted" rather than throwing out of a
 * session transition, mirroring idempotent-request's availableSessionStorage.
 */
function readStoredFarmId(): number | null {
  try {
    if (typeof window === "undefined") return null;
    const stored = Number(window.localStorage.getItem(FARM_STORAGE_KEY));
    return Number.isInteger(stored) ? stored : null;
  } catch {
    return null;
  }
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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [farms, setFarms] = useState<FarmEntry[]>([]);
  const farmsRef = useRef<FarmEntry[]>([]);
  const [farmId, setFarmIdState] = useState<number | null>(null);
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

  const selectFarm = useCallback(
    (id: number, timezone?: string) => {
      // Cancel in-flight queries BEFORE clearing: cache keys are URL-only
      // (no farm id), so any request that was already on the wire with the
      // OLD X-Farm-Id header would otherwise resolve into the fresh cache
      // and briefly render previous-farm data.
      queryClient.cancelQueries();
      queryClient.clear();
      setFarmIdState(id);
      setCurrentFarmId(String(id));
      const selected = farmsRef.current.find((farm) => farm.id === id) as
        | (FarmEntry & { timezone?: string })
        | undefined;
      setActiveFarmTimezone(timezone ?? selected?.timezone);
      writeStoredFarmId(id);
    },
    [queryClient],
  );

  /** Full local session teardown — shared by signOut and the forced-logout
   *  (refresh rejected, e.g. a rotated/reused refresh token now 401s) path so
   *  both behave identically. */
  const clearSession = useCallback(() => {
    queryClient.clear();
    setAccessToken(null);
    setCurrentFarmId(null);
    setActiveFarmTimezone(null);
    setUser(null);
    farmsRef.current = [];
    setFarms([]);
    setFarmIdState(null);
    // Last, so the in-memory teardown above can never be left half applied.
    clearStoredFarmId();
  }, [queryClient]);

  const signOut = useCallback(async () => {
    // Fire the revocation while the bearer token is still installed, but never
    // block the local teardown on it. A request that neither resolves nor
    // rejects (black-holed connection) would otherwise leave a shared terminal
    // signed in, with the token, farm and whole query cache intact.
    const revoked = apiFetch("/api/auth/logout", { method: "POST" }).catch(() => {
      /* cookie may already be gone */
    });
    clearSession();
    router.push("/login");
    await revoked;
  }, [router, clearSession]);

  const applyFarmList = useCallback((list: FarmEntry[]) => {
    farmsRef.current = list;
    setFarms(list);
    const stored = readStoredFarmId();
    const valid = list.find((f) => f.id === stored) ?? list[0];
    if (valid) selectFarm(valid.id, valid.timezone);
    else {
      setFarmIdState(null);
      setCurrentFarmId(null);
      setActiveFarmTimezone(null);
    }
  }, [selectFarm]);

  const refreshFarms = useCallback(async () => {
    const list = await apiFetch<FarmEntry[]>("/api/auth/farms");
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
      setAccessToken(accessToken, u.id);
      try {
        const list = await apiFetch<FarmEntry[]>("/api/auth/farms");
        setUser(u);
        applyFarmList(list);
      } catch (error) {
        if (revokeOnFailure) {
          // Login/register already rotated an httpOnly refresh cookie. Revoke
          // that server session before reporting failure; otherwise a reload
          // could silently sign in after this supposedly transactional step.
          try {
            await apiFetch("/api/auth/logout", { method: "POST" });
          } catch {
            // A network-wide outage can also block logout; local state is
            // still cleared and the refresh cookie remains httpOnly.
          }
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
    },
    [establishSession],
  );

  // Registration owns a module-level singleton in api-client, so it must be
  // paired with a deregistration on EVERY invocation. Keeping it separate from
  // the once-only bootstrap below is what guarantees that.
  useEffect(() => {
    setOnAuthFailure(() => {
      // Fired when a refresh attempt is rejected (expired/revoked/reused
      // refresh token). One cleanup per logout transition, identical to
      // signOut's, then straight to /login — never a retry loop.
      if (forcedLogout.current) return;
      forcedLogout.current = true;
      clearSession();
      router.push("/login");
    });
    return () => setOnAuthFailure(null);
  }, [clearSession, router]);

  useEffect(() => {
    if (initialRefreshStarted.current) return;
    initialRefreshStarted.current = true;
    (async () => {
      try {
        const body = await refreshSession();
        if (body) {
          // A farms failure here must not revoke the refresh family this call
          // just rotated: the user is not watching an error message, so a
          // plain reload has to be able to recover the valid session.
          await establishSession(body.access_token, body.user, false);
        }
      } catch {
        // Refresh failed (network error or non-JSON body): stay signed out.
        // loading settles in finally and the redirect effect sends /login.
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!loading && !user && !PUBLIC_PATHS.includes(pathname)) {
      router.push("/login");
    }
  }, [loading, user, pathname, router]);

  const value = useMemo(
    () => ({ user, farms, farmId, loading, selectFarm, signIn, signOut, refreshFarms }),
    [user, farms, farmId, loading, selectFarm, signIn, signOut, refreshFarms],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
