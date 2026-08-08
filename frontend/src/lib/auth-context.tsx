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
  setAccessToken,
  setCurrentFarmId,
  setOnAuthFailure,
} from "@/lib/api-client";

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
  const [farmId, setFarmIdState] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  // Guards the forced-logout path: N concurrent 401s with a failed refresh
  // must run the cleanup once, not N times.
  const forcedLogout = useRef(false);

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
    localStorage.removeItem(FARM_STORAGE_KEY);
    setUser(null);
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

  const refreshFarms = useCallback(async () => {
    const list = await apiFetch<FarmEntry[]>("/api/auth/farms");
    setFarms(list);
    const stored = Number(localStorage.getItem(FARM_STORAGE_KEY));
    const valid = list.find((f) => f.id === stored) ?? list[0];
    if (valid) selectFarm(valid.id);
    else {
      setFarmIdState(null);
      setCurrentFarmId(null);
    }
  }, [selectFarm]);

  const signIn = useCallback(
    async (accessToken: string, u: SessionUser) => {
      forcedLogout.current = false;
      setAccessToken(accessToken);
      setUser(u);
      await refreshFarms();
    },
    [refreshFarms],
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
    (async () => {
      try {
        const resp = await fetch("/api/auth/refresh", {
          method: "POST",
          credentials: "include",
        });
        if (resp.ok) {
          const body = await resp.json();
          setAccessToken(body.access_token);
          setUser(body.user);
          await refreshFarms();
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
