"use client";

/**
 * Auth + active-farm state. The access token lives in memory (api-client);
 * on first load we silently try /api/auth/refresh (httpOnly cookie). The
 * selected farm persists in localStorage and is sent as X-Farm-Id.
 */

import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  apiFetch,
  setAccessToken,
  setCurrentFarmId,
  setOnAuthFailure,
} from "@/lib/api-client";

export interface SessionUser {
  id: number;
  email: string;
  name: string | null;
}

export interface FarmEntry {
  id: number;
  name: string;
  location: string | null;
  role: string | null; // null = owner
}

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

  const selectFarm = useCallback((id: number) => {
    setFarmIdState(id);
    setCurrentFarmId(String(id));
    localStorage.setItem(FARM_STORAGE_KEY, String(id));
  }, []);

  const signOut = useCallback(async () => {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch {
      /* cookie may already be gone */
    }
    setAccessToken(null);
    setCurrentFarmId(null);
    localStorage.removeItem(FARM_STORAGE_KEY);
    setUser(null);
    setFarms([]);
    setFarmIdState(null);
    router.push("/login");
  }, [router]);

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
      setAccessToken(accessToken);
      setUser(u);
      await refreshFarms();
    },
    [refreshFarms],
  );

  useEffect(() => {
    setOnAuthFailure(() => {
      setUser(null);
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
