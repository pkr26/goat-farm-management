import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "@/lib/auth-context";
import { createTestQueryClient } from "@/test/render";
import { server } from "@/test/msw-server";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/worker/offline",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function Probe() {
  const { loading } = useAuth();
  return <span data-testid="loading">{String(loading)}</span>;
}

describe("offline auth bootstrap", () => {
  it("releases the cached offline route while an online-reported refresh is black-holed", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    let started = false;
    server.use(
      http.post("/api/auth/refresh", async () => {
        started = true;
        await gate;
        return HttpResponse.json({ detail: "Unavailable" }, { status: 503 });
      }),
    );
    const queryClient = createTestQueryClient();
    function Wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={queryClient}><AuthProvider>{children}</AuthProvider></QueryClientProvider>;
    }
    const view = render(<Probe />, { wrapper: Wrapper });

    await waitFor(() => expect(started).toBe(true));
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
    release();
    view.unmount();
  });
});
