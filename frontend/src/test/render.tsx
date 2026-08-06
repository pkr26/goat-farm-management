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
import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

import { AuthProvider } from "@/lib/auth-context";

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

export function renderWithProviders(
  ui: ReactElement,
  queryClient: QueryClient = createTestQueryClient(),
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AuthProvider>{children}</AuthProvider>
      </QueryClientProvider>
    );
  }
  return { queryClient, ...render(ui, { wrapper: Wrapper }) };
}
