/**
 * usePermissions — fresh-domain mutation campaign kills (2026-09): the
 * pre-farm bootstrap window must report loading, not an empty permission
 * verdict.
 */

import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { usePermissions } from "./use-permissions";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ farmId: null }),
}));

function Probe() {
  const perms = usePermissions();
  return <span data-testid="loading">{String(perms.loading)}</span>;
}

describe("usePermissions — campaign kills", () => {
  it("stays in loading while the auth bootstrap has not produced a farm", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <Probe />
      </QueryClientProvider>,
    );

    // The permissions query is disabled with farmId null, and a disabled
    // query's isLoading is false — only the explicit farmId check keeps
    // pages from flashing a false "no access" verdict here.
    expect(screen.getByTestId("loading")).toHaveTextContent("true");
  });
});
