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
  selectFarm: (farmId: number) => void;
  signIn: (accessToken: string, user: SessionUser) => Promise<void>;
  signOut: () => Promise<void>;
  refreshFarms: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);
const FARM_STORAGE_KEY = "goatfarm.farmId";
const PUBLIC_PATHS = ["/login", "/register"];

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
    (id: number) => {
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
      setActiveFarmTimezone(selected?.timezone);
      localStorage.setItem(FARM_STORAGE_KEY, String(id));
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
    localStorage.removeItem(FARM_STORAGE_KEY);
    setUser(null);
    farmsRef.current = [];
    setFarms([]);
    setFarmIdState(null);
  }, [queryClient]);

  const signOut = useCallback(async () => {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch {
      /* cookie may already be gone */
    }
    clearSession();
    router.push("/login");
  }, [router, clearSession]);

  const applyFarmList = useCallback((list: FarmEntry[]) => {
    farmsRef.current = list;
    setFarms(list);
    const stored = Number(localStorage.getItem(FARM_STORAGE_KEY));
    const valid = list.find((f) => f.id === stored) ?? list[0];
    if (valid) selectFarm(valid.id);
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
   * login page claiming failure while `user` is already authenticated. */
  const establishSession = useCallback(
    async (accessToken: string, u: SessionUser) => {
      setAccessToken(accessToken);
      try {
        const list = await apiFetch<FarmEntry[]>("/api/auth/farms");
        setUser(u);
        applyFarmList(list);
      } catch (error) {
        // Login/register already rotated an httpOnly refresh cookie. Revoke
        // that server session before reporting failure; otherwise a reload
        // could silently sign in after this supposedly transactional step.
        try {
          await apiFetch("/api/auth/logout", { method: "POST" });
        } catch {
          // A network-wide outage can also block logout; local state is still
          // cleared and the server-side refresh cookie remains httpOnly.
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
      await establishSession(accessToken, u);
    },
    [establishSession],
  );

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
    if (initialRefreshStarted.current) return;
    initialRefreshStarted.current = true;
    (async () => {
      try {
        const body = await refreshSession();
        if (body) {
          await establishSession(body.access_token, body.user);
        }
      } catch {
        // Refresh failed (network error or non-JSON body): stay signed out.
        // loading settles in finally and the redirect effect sends /login.
      } finally {
        setLoading(false);
      }
    })();
    return () => setOnAuthFailure(null);
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
