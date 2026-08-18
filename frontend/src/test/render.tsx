/**
 * Render helper for page/component tests: wraps the UI in a fresh
 * QueryClientProvider + the real AuthProvider. The AuthProvider bootstrap
 * (POST /api/auth/refresh → GET /api/auth/farms → selectFarm) is answered
 * by the default MSW handlers in msw-server.ts, yielding an authenticated
 * session with farm 1 selected — the same state the app reaches after a
 * real login. Pages then fire their own queries (answered per test file
 * via server.use). Test files must mock next/navigation themselves
 * (useRouter/usePathname/useSearchParams).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { useEffect, type ReactElement, type ReactNode } from "react";

import { AuthProvider, useAuth } from "@/lib/auth-context";

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

function AuthLoadingProbe({ onLoadingChange }: { onLoadingChange: (loading: boolean) => void }) {
  const { loading } = useAuth();
  useEffect(() => onLoadingChange(loading), [loading, onLoadingChange]);
  return null;
}

export function renderWithProviders(
  ui: ReactElement,
  queryClient: QueryClient = createTestQueryClient(),
) {
  let authLoading = true;
  const onAuthLoadingChange = (loading: boolean) => {
    authLoading = loading;
  };

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <AuthLoadingProbe onLoadingChange={onAuthLoadingChange} />
          {children}
        </AuthProvider>
      </QueryClientProvider>
    );
  }
  return {
    queryClient,
    waitForAuthIdle: () =>
      waitFor(() => {
        if (authLoading) {
          throw new Error("AuthProvider is still loading");
        }
      }),
    ...render(ui, { wrapper: Wrapper }),
  };
}
