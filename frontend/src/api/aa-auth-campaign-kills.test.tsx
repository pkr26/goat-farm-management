/**
 * Early-collected auth kills (2026-09 mutation campaign).
 *
 * Stryker's per-test runner executes a mutant's covering tests in dry-run
 * collection order and stops at the first failure. The kills below target
 * auth-context mutants covered by ~2700 tests; in src/lib their killing
 * assertions only ran after the 15 s per-mutant budget expired (Timeout
 * classification). This file sorts before every src/app and src/lib suite,
 * so the same assertions land first and the mutants die fast. The originals
 * stay in src/lib/auth-context.campaign.test.tsx as the readable suite.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "@/lib/auth-context";
import { server, TEST_ACCESS_TOKEN, TEST_FARMS, TEST_USER } from "@/test/msw-server";
import { createTestQueryClient } from "@/test/render";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function gate<T>() {
  let release!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

function renderProvider() {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

async function expectIdle() {
  await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
}

describe("auth-context early kills", () => {
  beforeEach(() => {
    replaceMock.mockClear();
    window.localStorage.clear();
  });

  it("starts the synchronous farm ref genuinely empty", async () => {
    const { promise, release } = gate<typeof TEST_FARMS>();
    server.use(http.get("/api/auth/farms", () => promise.then((f) => HttpResponse.json(f))));
    renderProvider();
    expect(screen.getByTestId("farms-ref-count")).toHaveTextContent("0");
    release(TEST_FARMS);
    await expectIdle();
  });

  it("honors the selectFarm mounted guard and query cancellation", async () => {
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </QueryClientProvider>,
    );
    await expectIdle();
    const cancelSpy = vi.spyOn(queryClient, "cancelQueries");
    await act(async () => {
      screen.getByRole("button", { name: "select-2" }).click();
    });
    expect(cancelSpy).toHaveBeenCalled();
  });

  it("selects the persisted farm even when it is not first", async () => {
    window.localStorage.setItem("goatfarm.farmId", "2");
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          ...TEST_FARMS,
          { id: 2, name: "Second", location: "Solapur", role: null, timezone: "Asia/Kolkata" },
        ]),
      ),
    );
    renderProvider();
    await expectIdle();
    expect(screen.getByTestId("farmId")).toHaveTextContent("2");
  });

  it("stages the access token before the membership read", async () => {
    server.use(
      http.get("/api/auth/farms", ({ request }) =>
        request.headers.get("Authorization") === `Bearer ${TEST_ACCESS_TOKEN}`
          ? HttpResponse.json(TEST_FARMS)
          : new HttpResponse(null, { status: 401 }),
      ),
    );
    renderProvider();
    await expectIdle();
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
  });

  it("keeps a newer establishment's identity when an older one resolves late", async () => {
    renderProvider();
    await expectIdle();
    const gates = [gate<typeof TEST_FARMS>(), gate<typeof TEST_FARMS>()];
    let call = 0;
    server.use(
      http.get("/api/auth/farms", () => gates[call++]!.promise.then((f) => HttpResponse.json(f))),
    );
    await act(async () => {
      screen.getByRole("button", { name: "sign-in-a" }).click();
    });
    await act(async () => {
      screen.getByRole("button", { name: "sign-in-b" }).click();
    });
    await act(async () => {
      gates[1]!.release(TEST_FARMS);
    });
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("b@goatfarm.test"));
    await act(async () => {
      gates[0]!.release(TEST_FARMS);
    });
    expect(screen.getByTestId("user")).toHaveTextContent("b@goatfarm.test");
  });

  it("re-publishes the context value when the user changes", async () => {
    renderProvider();
    await expectIdle();
    await act(async () => {
      screen.getByRole("button", { name: "update-user-later" }).click();
    });
    expect(await screen.findByText("renamed@goatfarm.test")).toBeInTheDocument();
  });
});

function Probe() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(auth.loading)}</span>
      <span data-testid="user">{auth.user ? auth.user.email : "none"}</span>
      <span data-testid="farmId">{auth.farmId === null ? "none" : String(auth.farmId)}</span>
      <span data-testid="farms-ref-count">{auth.getFarms().length}</span>
      <button onClick={() => auth.selectFarm(2)}>select-2</button>
      <button
        onClick={() =>
          void auth
            .signIn(TEST_ACCESS_TOKEN, { id: 1, email: "a@goatfarm.test", name: "A" })
            .catch(() => undefined)
        }
      >
        sign-in-a
      </button>
      <button
        onClick={() =>
          void auth
            .signIn(TEST_ACCESS_TOKEN, { id: 1, email: "b@goatfarm.test", name: "B" })
            .catch(() => undefined)
        }
      >
        sign-in-b
      </button>
      <button
        onClick={() =>
          auth.updateUser({ ...TEST_USER, email: "renamed@goatfarm.test" })
        }
      >
        update-user-later
      </button>
    </div>
  );
}
