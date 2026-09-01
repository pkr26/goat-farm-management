/**
 * Tests for usePermissions: the hook that gates nav/buttons (the server
 * returns the granted permission set for the active farm; with no active
 * farm the endpoint must not be called at all). Rendered through the real
 * AuthProvider bootstrap (default MSW handlers), with the permissions
 * endpoint overridden per scenario.
 */

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, delay, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { useAuth } from "@/lib/auth-context";
import { usePermissions } from "@/lib/use-permissions";
import { ALL_PERMISSIONS, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

/** Renders the hook's outputs into the DOM for assertion. */
function Probe({ codes = [] }: { codes?: string[] }) {
  const { loading, isError, error, isOwner, can } = usePermissions();
  const { loading: authLoading } = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="auth-loading">{String(authLoading)}</span>
      <span data-testid="is-error">{String(isError)}</span>
      <span data-testid="error">
        {error instanceof Error ? error.message : error == null ? "none" : String(error)}
      </span>
      <span data-testid="is-owner">{String(isOwner)}</span>
      {codes.map((code) => (
        <span key={code} data-testid={`can:${code}`}>
          {String(can(code))}
        </span>
      ))}
    </div>
  );
}

async function expectSettled(testId: string, value: string) {
  await waitFor(() =>
    expect(screen.getByTestId(testId)).toHaveTextContent(value),
  );
}

describe("usePermissions — farm owner", () => {
  it("grants can() for every permission in the catalog", async () => {
    renderWithProviders(<Probe codes={ALL_PERMISSIONS} />);
    // A granted permission flipping to true proves the payload was applied.
    await waitFor(() =>
      expect(screen.getByTestId("can:animals.view")).toHaveTextContent("true"),
    );
    for (const code of ALL_PERMISSIONS) {
      expect(screen.getByTestId(`can:${code}`)).toHaveTextContent("true");
    }
  });

  it("stops loading once the permissions response arrives", async () => {
    renderWithProviders(<Probe />);
    await expectSettled("loading", "false");
  });
});

describe("usePermissions — worker with a granted subset", () => {
  const WORKER_PERMISSIONS = ["animals.view", "tasks.view", "tasks.complete"];

  /** Overrides the endpoint and resolves once the response has been served. */
  function useWorkerPermissions(permissions: string[]) {
    let served = false;
    server.use(
      http.get("/api/auth/permissions", async () => {
        // Small delay so the loading=true phase is observable in the DOM.
        await delay(30);
        served = true;
        return HttpResponse.json({ is_owner: false, permissions });
      }),
    );
    return () => served;
  }

  async function waitForServed(served: () => boolean) {
    // The endpoint was hit, then React applied the payload (loading settles).
    await waitFor(() => expect(served()).toBe(true));
    await expectSettled("loading", "false");
  }

  it("grants can() only for the permissions the server returned", async () => {
    useWorkerPermissions(WORKER_PERMISSIONS);
    renderWithProviders(
      <Probe
        codes={[
          "animals.view",
          "tasks.view",
          "tasks.complete",
          "animals.create",
          "team.manage",
          "finance.manage",
        ]}
      />,
    );
    // A granted permission flipping to true proves the payload was applied.
    await waitFor(() =>
      expect(screen.getByTestId("can:animals.view")).toHaveTextContent("true"),
    );
    expect(screen.getByTestId("can:tasks.view")).toHaveTextContent("true");
    expect(screen.getByTestId("can:tasks.complete")).toHaveTextContent("true");
    expect(screen.getByTestId("can:animals.create")).toHaveTextContent("false");
    expect(screen.getByTestId("can:team.manage")).toHaveTextContent("false");
    expect(screen.getByTestId("can:finance.manage")).toHaveTextContent("false");
  });

  it("denies can() for unknown permission codes", async () => {
    const served = useWorkerPermissions(WORKER_PERMISSIONS);
    renderWithProviders(<Probe codes={["does.not.exist"]} />);
    await waitForServed(served);
    expect(screen.getByTestId("can:does.not.exist")).toHaveTextContent("false");
  });

  it("denies everything for a worker with an empty permission set", async () => {
    const served = useWorkerPermissions([]);
    renderWithProviders(<Probe codes={["animals.view", "dashboard.view"]} />);
    await waitForServed(served);
    expect(screen.getByTestId("can:animals.view")).toHaveTextContent("false");
    expect(screen.getByTestId("can:dashboard.view")).toHaveTextContent("false");
  });
});

describe("usePermissions — no active farm", () => {
  it("does not call the endpoint and denies everything", async () => {
    let permissionCalls = 0;
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.json([])),
      http.get("/api/auth/permissions", () => {
        permissionCalls += 1;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );

    renderWithProviders(<Probe codes={["animals.view"]} />);

    // Settled signal, not a wall-clock sleep: auth-loading
    // flips false only after the whole bootstrap (refresh + farms fetch +
    // farm selection) has finished. With no farms the active farm stays null
    // forever, so the permissions query can never become enabled afterwards.
    await waitFor(() =>
      expect(screen.getByTestId("auth-loading")).toHaveTextContent("false"),
    );

    expect(permissionCalls).toBe(0);
    expect(screen.getByTestId("can:animals.view")).toHaveTextContent("false");
    expect(screen.getByTestId("is-owner")).toHaveTextContent("false");
  });
});

describe("usePermissions — loading and error states", () => {
  it("reports loading true while the permissions request is in flight", async () => {
    server.use(
      http.get("/api/auth/permissions", async () => {
        await delay(150);
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );

    renderWithProviders(<Probe />);

    // Once the farm is selected the query fires; while delayed, loading is true.
    await expectSettled("loading", "true");
    // Then it settles.
    await expectSettled("loading", "false");
  });

  it("denies everything and stops loading when the endpoint errors", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    renderWithProviders(<Probe codes={["animals.view"]} />);

    await expectSettled("loading", "false");
    expect(screen.getByTestId("can:animals.view")).toHaveTextContent("false");
  });

  it("flags isError when the endpoint fails", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    renderWithProviders(<Probe />);

    // With farmId set, loading tracks the query itself, so waiting on the
    // error flag is enough (loading has already settled false by then).
    await waitFor(() =>
      expect(screen.getByTestId("is-error")).toHaveTextContent("true"),
    );
    expect(screen.getByTestId("error")).toHaveTextContent("boom");
    expect(screen.getByTestId("is-owner")).toHaveTextContent("false");
  });

  it("fails closed instead of reusing stale grants after a refetch error", async () => {
    const { queryClient } = renderWithProviders(
      <Probe codes={["animals.view"]} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("can:animals.view")).toHaveTextContent("true"),
    );
    expect(screen.getByTestId("is-owner")).toHaveTextContent("true");

    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 }),
      ),
    );
    await queryClient.invalidateQueries();

    await waitFor(() =>
      expect(screen.getByTestId("is-error")).toHaveTextContent("true"),
    );
    expect(screen.getByTestId("can:animals.view")).toHaveTextContent("false");
    expect(screen.getByTestId("is-owner")).toHaveTextContent("false");
    expect(screen.getByTestId("error")).toHaveTextContent("permissions unavailable");
  });

  it("does not flag isError on a successful response", async () => {
    renderWithProviders(<Probe codes={["animals.view"]} />);

    // A granted permission flipping to true proves the payload was applied.
    await waitFor(() =>
      expect(screen.getByTestId("can:animals.view")).toHaveTextContent("true"),
    );
    expect(screen.getByTestId("is-error")).toHaveTextContent("false");
  });

  it("exposes is_owner from the payload (needed for the verify-self guard, 7-4)", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS }),
      ),
    );

    renderWithProviders(<Probe />);

    await waitFor(() =>
      expect(screen.getByTestId("is-owner")).toHaveTextContent("true"),
    );
  });

  it("exposes is_owner false for a worker membership", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({
          is_owner: false,
          permissions: ["animals.view"],
        }),
      ),
    );

    renderWithProviders(<Probe codes={["animals.view"]} />);

    await waitFor(() =>
      expect(screen.getByTestId("can:animals.view")).toHaveTextContent("true"),
    );
    expect(screen.getByTestId("is-owner")).toHaveTextContent("false");
  });
});
