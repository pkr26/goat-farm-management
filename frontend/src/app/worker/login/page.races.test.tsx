import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TokenOut } from "@/api/generated/models";
import { ApiError, authSessionEpochValue, setAccessToken, setCurrentFarmId, type RequestScope } from "@/lib/api-client";
import { useAuth, type AuthState } from "@/lib/auth-context";
import { LANGUAGE_STORAGE_KEY } from "@/lib/i18n";
import { server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import WorkerLoginPage from "./page";
import { WorkerShell } from "../layout";

const { replace, router, toastSuccess, pinRequest } = vi.hoisted(() => {
  const replace = vi.fn();
  return { replace, router: { replace, push: vi.fn(), prefetch: vi.fn() },
    toastSuccess: vi.fn(), pinRequest: vi.fn<(init: RequestInit) => Promise<TokenOut>>() };
});
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/worker/login", useSearchParams: () => new URLSearchParams(),
}));
vi.mock("sonner", () => ({ toast: { success: toastSuccess, error: vi.fn(), info: vi.fn() } }));
vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    // A transport may finish parsing an already-received success despite a
    // later abort. Exercise the page's ownership fence independently of the
    // fetch layer, while retaining real AuthProvider and revocation requests.
    apiFetch: <T,>(path: string, init: RequestInit = {}, scope?: RequestScope): Promise<T> =>
      path === "/api/auth/worker-login" ? pinRequest(init) as Promise<T> : actual.apiFetch<T>(path, init, scope),
  };
});

const ALICE: TokenOut = { access_token: "alice-grant", token_type: "bearer", user: { id: 11, email: "alice@farm.in", name: "Alice", must_change_password: false } };
const BOB: TokenOut = { access_token: "bob-grant", token_type: "bearer", user: { id: 12, email: "bob@farm.in", name: "Bob", must_change_password: false } };
const FARMS = [{ id: 3, name: "Tablet Farm", location: null, timezone: "Asia/Kolkata", role: null }];
let auth: AuthState | null = null;
let revocations: { bearer: string | null; credentials: RequestCredentials }[];
function AuthProbe() {
  const state = useAuth();
  useEffect(() => { auth = state; }, [state]);
  return <span data-testid="current-worker">{state.user?.name ?? "signed out"}</span>;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function renderLogin() {
  const view = renderWithProviders(<><AuthProbe /><WorkerLoginPage /></>, createTestQueryClient());
  await view.waitForAuthIdle();
  await screen.findByTestId("worker-roster");
  return view;
}
async function enterPin(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name }));
  for (const digit of "4321") await user.click(screen.getByTestId(`pin-key-${digit}`));
  await user.click(screen.getByTestId("pin-sign-in"));
}
async function back(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Who is working?" }));
  await screen.findByTestId("worker-roster");
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("herdly.tabletFarm", "3");
  setAccessToken(null); setCurrentFarmId(null);
  auth = null; revocations = [];
  replace.mockClear(); toastSuccess.mockClear(); pinRequest.mockReset();
  server.use(
    http.post("/api/auth/refresh", () => HttpResponse.json({ detail: "No session" }, { status: 401 })),
    http.get("/api/auth/worker-roster", () => HttpResponse.json({ items: [{ membership_id: 11, display_name: "Alice" }, { membership_id: 12, display_name: "Bob" }], next_after_membership_id: null })),
    http.get("/api/auth/farms", () => HttpResponse.json(FARMS)),
    http.post("/api/auth/logout-session", ({ request }) => {
      revocations.push({ bearer: request.headers.get("Authorization"), credentials: request.credentials });
      return new HttpResponse(null, { status: 204 });
    }),
  );
});

describe("manager setup inside the real worker shell", () => {
  async function enterManagerSetup() {
    localStorage.removeItem("herdly.tabletFarm");
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "en");
    server.use(http.post("/api/auth/login", async ({ request }) => {
      expect(await request.json()).toEqual({ email: "alice@farm.in", password: "manager-password-1234", tablet_setup: true });
      return HttpResponse.json(ALICE);
    }));
    const view = renderWithProviders(<><AuthProbe /><WorkerShell><WorkerLoginPage /></WorkerShell></>, createTestQueryClient());
    await view.waitForAuthIdle();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("worker-setup-start"));
    await user.type(screen.getByLabelText("Email"), "alice@farm.in");
    await user.type(screen.getByLabelText("Password"), "manager-password-1234");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    return { view, user };
  }

  it("keeps setup mounted when the manager commits, then revokes only at deliberate pinning", async () => {
    const { user } = await enterManagerSetup();
    await screen.findByTestId("worker-setup-farms");
    expect(screen.getByTestId("current-worker")).toHaveTextContent("Alice");
    expect(screen.queryByTestId("worker-identity")).not.toBeInTheDocument();
    expect(revocations).toEqual([]);
    expect(replace).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Tablet Farm" }));
    await screen.findByTestId("worker-roster");
    expect(localStorage.getItem("herdly.tabletFarm")).toBe("3");
    await waitFor(() => expect(revocations.map((item) => item.bearer)).toEqual(["Bearer alice-grant"]));
    expect(screen.getByTestId("current-worker")).toHaveTextContent("signed out");
    expect(replace).toHaveBeenLastCalledWith("/worker/login");
  });

  it("still revokes the committed manager when the setup page is genuinely abandoned", async () => {
    const { view } = await enterManagerSetup();
    await screen.findByTestId("worker-setup-farms");
    expect(revocations).toEqual([]);
    view.rerender(<><AuthProbe /><WorkerShell><p>Another worker page</p></WorkerShell></>);
    await waitFor(() => expect(revocations.map((item) => item.bearer)).toEqual(["Bearer alice-grant"]));
    expect(screen.getByTestId("current-worker")).toHaveTextContent("signed out");
  });
});

describe("worker PIN intent cancellation", () => {
  it("Back aborts Alice, lets Bob sign in, and revokes only Alice's late grant", async () => {
    const alice = deferred<TokenOut>();
    pinRequest.mockImplementationOnce(() => alice.promise).mockResolvedValueOnce(BOB);
    const user = userEvent.setup();
    await renderLogin();
    await enterPin(user, "Alice");
    const signal = pinRequest.mock.calls[0][0].signal;
    await back(user);
    expect(signal?.aborted).toBe(true);
    await enterPin(user, "Bob");
    await waitFor(() => expect(screen.getByTestId("current-worker")).toHaveTextContent("Bob"));
    expect(pinRequest).toHaveBeenCalledTimes(2);
    const bobEpoch = authSessionEpochValue();
    replace.mockClear(); toastSuccess.mockClear();
    await act(async () => { alice.resolve(ALICE); await alice.promise; });
    await waitFor(() => expect(revocations).toContainEqual({ bearer: "Bearer alice-grant", credentials: "omit" }));
    expect(authSessionEpochValue()).toBe(bobEpoch);
    expect(screen.getByTestId("current-worker")).toHaveTextContent("Bob");
    expect(revocations.some((entry) => entry.bearer === "Bearer bob-grant")).toBe(false);
    expect(replace).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("Alice's late failure cannot clear Bob's pending PIN or release Bob's busy state", async () => {
    const alice = deferred<TokenOut>(); const bob = deferred<TokenOut>();
    pinRequest.mockImplementationOnce(() => alice.promise).mockImplementationOnce(() => bob.promise);
    const user = userEvent.setup();
    await renderLogin();
    await enterPin(user, "Alice"); await back(user); await enterPin(user, "Bob");
    await act(async () => { alice.reject(new ApiError(401, "Invalid PIN.")); await alice.promise.catch(() => {}); });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTestId("pin-dots")).toHaveTextContent("••••");
    expect(screen.getByTestId("pin-sign-in")).toBeDisabled();
    await act(async () => { bob.resolve(BOB); await bob.promise; });
    await waitFor(() => expect(screen.getByTestId("current-worker")).toHaveTextContent("Bob"));
  });

  it("Back during Alice's staged farm discovery tears down and revokes her owned family", async () => {
    const farms = deferred<void>(); let aliceDiscovering = false;
    pinRequest.mockResolvedValueOnce(ALICE).mockResolvedValueOnce(BOB);
    server.use(http.get("/api/auth/farms", async ({ request }) => {
      if (request.headers.get("Authorization") === "Bearer alice-grant") { aliceDiscovering = true; await farms.promise; }
      return HttpResponse.json(FARMS);
    }));
    const user = userEvent.setup();
    await renderLogin(); await enterPin(user, "Alice");
    await waitFor(() => expect(aliceDiscovering).toBe(true));
    await back(user);
    await waitFor(() => expect(revocations.some((entry) => entry.bearer === "Bearer alice-grant")).toBe(true));
    expect(screen.getByTestId("current-worker")).toHaveTextContent("signed out");
    await enterPin(user, "Bob");
    await waitFor(() => expect(screen.getByTestId("current-worker")).toHaveTextContent("Bob"));
    const bobEpoch = authSessionEpochValue();
    replace.mockClear(); toastSuccess.mockClear();
    await act(async () => { farms.resolve(); await farms.promise; });
    expect(authSessionEpochValue()).toBe(bobEpoch);
    expect(screen.getByTestId("current-worker")).toHaveTextContent("Bob");
    expect(revocations.some((entry) => entry.bearer === "Bearer bob-grant")).toBe(false);
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("cancelling an Alice stage already superseded by Bob leaves Bob's session intact", async () => {
    const farms = deferred<void>(); let aliceDiscovering = false;
    pinRequest.mockResolvedValueOnce(ALICE);
    server.use(http.get("/api/auth/farms", async ({ request }) => {
      if (request.headers.get("Authorization") === "Bearer alice-grant") { aliceDiscovering = true; await farms.promise; }
      return HttpResponse.json(FARMS);
    }));
    const user = userEvent.setup();
    await renderLogin(); await enterPin(user, "Alice");
    await waitFor(() => expect(aliceDiscovering).toBe(true));
    await act(async () => { await auth!.signIn(BOB.access_token, BOB.user); });
    const bobEpoch = authSessionEpochValue();
    replace.mockClear(); toastSuccess.mockClear();
    await back(user);
    await waitFor(() => expect(revocations).toContainEqual({ bearer: "Bearer alice-grant", credentials: "omit" }));
    // Back can legitimately re-render the returning Bob session's redirect.
    // Alice's later farm response must not add another navigation.
    replace.mockClear();
    await act(async () => { farms.resolve(); await farms.promise; });
    expect(authSessionEpochValue()).toBe(bobEpoch);
    expect(screen.getByTestId("current-worker")).toHaveTextContent("Bob");
    expect(revocations.some((entry) => entry.bearer === "Bearer bob-grant")).toBe(false);
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("confirmed unpin cancels the abandoned PIN and keeps the tablet unpinned after its late success", async () => {
    const alice = deferred<TokenOut>(); pinRequest.mockImplementationOnce(() => alice.promise);
    const user = userEvent.setup();
    await renderLogin(); await enterPin(user, "Alice"); await back(user);
    await user.click(screen.getByRole("button", { name: "Change farm" }));
    await user.click(await screen.findByTestId("worker-unpin-confirm"));
    expect(localStorage.getItem("herdly.tabletFarm")).toBeNull();
    expect(await screen.findByTestId("worker-setup-start")).toBeInTheDocument();
    await act(async () => { alice.resolve(ALICE); await alice.promise; });
    await waitFor(() => expect(revocations).toContainEqual({ bearer: "Bearer alice-grant", credentials: "omit" }));
    expect(screen.getByTestId("current-worker")).toHaveTextContent("signed out");
    expect(localStorage.getItem("herdly.tabletFarm")).toBeNull();
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });
});
