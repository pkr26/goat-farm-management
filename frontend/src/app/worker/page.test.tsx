/**
 * Worker tablet pages (ITEM 2 Phase 2): the PIN-pad login (roster → tap →
 * PIN → worker-login → signIn) and the duty board's offline-aware completion.
 */

import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";

import { permissionsHandler, server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { settle } from "@/test/settle";
import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "@/lib/i18n";
import { readWorkerOutbox } from "@/lib/worker-outbox";

import WorkerLoginPage from "./login/page";
import WorkerBoardPage from "./page";

const { pushMock, replaceMock, signOutMock, selectFarmMock, getFarmsMock, toastSuccess, workerAuth } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  signOutMock: vi.fn(),
  selectFarmMock: vi.fn(),
  getFarmsMock: vi.fn<() => { id: number; name: string; location: null; timezone: string; role: null }[]>(() => []),
  toastSuccess: vi.fn(),
  workerAuth: { mustChangePassword: false },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/worker",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, info: vi.fn(), error: vi.fn() },
}));

const signIn = vi.fn();

vi.mock("@/lib/auth-context", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useAuth: () => ({
    signIn,
    signOut: signOutMock,
    user: { id: 7, email: "pin@farm.in", name: "Pin Worker", must_change_password: workerAuth.mustChangePassword },
    farmId: 3,
    farms: [{ id: 3, name: "Tablet Farm", location: null, timezone: "Asia/Kolkata", role: null }],
    loading: false,
    selectFarm: selectFarmMock,
    refreshFarms: vi.fn(),
    updateUser: vi.fn(),
    getFarms: getFarmsMock,
  }),
}));

const ROSTER = {
  items: [
    { membership_id: 11, display_name: "Lakshmi" },
    { membership_id: 12, display_name: "Ravi" },
  ],
};

const BOARD = (id: number, title: string, due: string) => ({
  id,
  title,
  due_date: due,
  category: "OTHER",
  status: "PENDING",
  priority: null,
  notes: null,
  animal_id: null,
  animal_tag: null,
  assigned_role_id: null,
  assigned_user_id: 7,
  assigned_role_name: null,
  breeding_record_id: null,
  purchase_batch_id: null,
  recurring_series_id: null,
  recur_days: null,
  auto_generated: false,
  action_url: null,
  completed_by_id: null,
  completed_at: null,
  verified_by_id: null,
  verified_at: null,
  verification_note: null,
  rejected_by_id: null,
  rejected_at: null,
  skip_reason: null,
  skipped_by_id: null,
  skipped_at: null,
  created_at: "2026-09-20T05:00:00Z",
  updated_at: "2026-09-20T05:00:00Z",
  title_key: null,
  title_args: {},
});

function today() {
  return new Date().toISOString().slice(0, 10);
}

beforeEach(() => {
  workerAuth.mustChangePassword = false;
  vi.stubGlobal("indexedDB", new IDBFactory());
  vi.stubGlobal("structuredClone", (value: unknown) => JSON.parse(JSON.stringify(value)));
  setAccessToken("worker-test-token", 7); setCurrentFarmId("3");
  server.use(
    http.post("/api/auth/refresh", () => HttpResponse.json({ access_token: "worker-test-token", user: { id: 7, email: "pin@farm.in", name: "Pin Worker", must_change_password: false } })),
    http.get("/api/auth/farms", () => HttpResponse.json([{ id: 3, name: "Tablet Farm", location: null, timezone: "Asia/Kolkata", role: null }])),
  );
  pushMock.mockClear();
  replaceMock.mockClear();
  signOutMock.mockClear();
  selectFarmMock.mockClear();
  getFarmsMock.mockReset().mockReturnValue([]);
  signIn.mockReset();
  toastSuccess.mockClear();
  window.localStorage.clear();
});

describe("WorkerLoginPage", () => {
  it("does not establish a PIN identity after the page is left while login is pending", async () => {
    localStorage.setItem("herdly.tabletFarm", "3");
    let release!: () => void;
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
      http.post("/api/auth/worker-login", async () => {
        await new Promise<void>((resolve) => { release = resolve; });
        return HttpResponse.json({ access_token: "late-worker", user: { id: 7, email: "worker@farm.in", must_change_password: false } });
      }),
    );
    const user = userEvent.setup();
    const view = renderWithProviders(<WorkerLoginPage />, createTestQueryClient());
    await user.click(await screen.findByRole("button", { name: "Lakshmi" }));
    for (const digit of "4321") await user.click(screen.getByTestId(`pin-key-${digit}`));
    await user.click(screen.getByTestId("pin-sign-in"));
    await waitFor(() => expect(release).toBeTypeOf("function"));
    view.unmount();
    release();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(signIn).not.toHaveBeenCalled();
  });

  it("cancelling a pending manager request cannot install the late manager identity", async () => {
    let release!: () => void;
    server.use(http.post("/api/auth/login", async () => {
      await new Promise<void>((resolve) => { release = resolve; });
      return HttpResponse.json({ access_token: "late-manager", user: { id: 1, email: "owner@farm.in", must_change_password: false } });
    }));
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());
    await user.click(await screen.findByTestId("worker-setup-start"));
    await user.type(screen.getByLabelText("Email"), "owner@farm.in");
    await user.type(screen.getByLabelText("Password"), "owner-pass-123");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(release).toBeTypeOf("function"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    release();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(signIn).not.toHaveBeenCalled();
    expect(await screen.findByText("No farm on this tablet")).toBeInTheDocument();
  });
  it("pins the tablet farm, lists names, and signs in by PIN", async () => {
    window.localStorage.setItem("herdly.tabletFarm", "3");
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
    );
    server.use(
      http.post("/api/auth/worker-login", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        expect(body).toEqual({ farm_id: 3, membership_id: 11, pin: "4321" });
        return HttpResponse.json({
          access_token: "tablet-token",
          token_type: "bearer",
          user: { id: 7, email: "pin@farm.in", name: "Lakshmi", must_change_password: false },
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());

    await user.click(await screen.findByRole("button", { name: "Lakshmi" }));
    for (const digit of ["4", "3", "2", "1"]) {
      await user.click(screen.getByRole("button", { name: new RegExp(`pin ${digit}`, "i") }));
    }
    await user.click(screen.getByTestId("pin-sign-in"));

    await waitFor(() => expect(signIn).toHaveBeenCalled());
    expect(replaceMock).toHaveBeenCalledWith("/worker");
  });

  it("shows the no-farm setup state when the tablet is unpinned", async () => {
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());
    expect(
      await screen.findByText("No farm on this tablet"),
    ).toBeInTheDocument();
  });

  it("labels the roster-back action without a glyph decoration (2026-10-01 audit, 05-3)", async () => {
    // The page's own convention is "No glyph decorations in UI copy": the
    // back action carried a literal "←" that contradicted it. The accessible
    // name is the roster title alone.
    window.localStorage.setItem("herdly.tabletFarm", "3");
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());

    await user.click(await screen.findByRole("button", { name: "Lakshmi" }));

    expect(
      await screen.findByRole("button", { name: "Who is working?" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "← Who is working?" }),
    ).not.toBeInTheDocument();
  });

  it("runs the manager setup flow: credentials → TOTP code → pick farm → pin + sign out", async () => {
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
    );
    server.use(
      http.post("/api/auth/login", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        expect(body).toEqual({ email: "owner@farm.in", password: "owner-pass-123", tablet_setup: true });
        // Password accepted, second factor demanded (LoginOut mfa arm).
        return HttpResponse.json({ mfa_token: "challenge-token" });
      }),
    );
    server.use(
      http.post("/api/auth/totp/challenge", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        expect(body).toEqual({ mfa_token: "challenge-token", code: "123456" });
        return HttpResponse.json({
          access_token: "manager-token",
          token_type: "bearer",
          user: { id: 1, email: "owner@farm.in", name: "Owner", must_change_password: false },
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());

    // Unpinned tablet → setup entry point.
    await user.click(await screen.findByTestId("worker-setup-start"));

    // Credentials step.
    await user.type(screen.getByLabelText("Email"), "owner@farm.in");
    await user.type(screen.getByLabelText("Password"), "owner-pass-123");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // TOTP challenge step (recovery codes ride the same endpoint).
    const codeBox = await screen.findByTestId("worker-setup-code");
    expect(codeBox).toBeInTheDocument();
    await user.type(screen.getByLabelText("Verification code"), "123456");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // Farm choice from the signed-in manager's farm list.
    expect(await screen.findByTestId("worker-setup-farms")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Tablet Farm" }));

    // The farm is pinned and the manager's session does not linger.
    await waitFor(() => expect(signOutMock).toHaveBeenCalled());
    expect(window.localStorage.getItem("herdly.tabletFarm")).toBe("3");
    // The roster takes over as the workers' door.
    expect(await screen.findByRole("button", { name: "Lakshmi" })).toBeInTheDocument();
  });

  it("pins the farm straight through when the manager has no TOTP", async () => {
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
    );
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          access_token: "manager-token",
          token_type: "bearer",
          user: { id: 1, email: "owner@farm.in", name: "Owner", must_change_password: false },
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());

    await user.click(await screen.findByTestId("worker-setup-start"));
    await user.type(screen.getByLabelText("Email"), "owner@farm.in");
    await user.type(screen.getByLabelText("Password"), "owner-pass-123");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await user.click(await screen.findByRole("button", { name: "Tablet Farm" }));
    await waitFor(() => expect(signOutMock).toHaveBeenCalled());
    expect(window.localStorage.getItem("herdly.tabletFarm")).toBe("3");
    expect(signIn).toHaveBeenCalledWith(
      "manager-token",
      expect.objectContaining({ email: "owner@farm.in" }),
      { sessionOnly: true },
    );
  });

  it("answers a failed manager sign-in with its own message", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ detail: "Invalid email or password." }, { status: 401 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());

    await user.click(await screen.findByTestId("worker-setup-start"));
    await user.type(screen.getByLabelText("Email"), "owner@farm.in");
    await user.type(screen.getByLabelText("Password"), "wrong-pass");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(
      await screen.findByText("Sign-in failed. Check your details and try again."),
    ).toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("herdly.tabletFarm")).toBeNull();
  });

  it("answers an offline setup attempt with the connection message, never 'check your details'", async () => {
    // The request never reached the server, so the credentials were never
    // judged — mapping a transport failure onto "check your details" sends
    // the manager re-typing a good password (2026-09-28 audit). Mirrors the
    // PIN flow's ApiError/non-ApiError split.
    server.use(http.post("/api/auth/login", () => HttpResponse.error()));
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());

    await user.click(await screen.findByTestId("worker-setup-start"));
    await user.type(screen.getByLabelText("Email"), "owner@farm.in");
    await user.type(screen.getByLabelText("Password"), "owner-pass-123");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(
      await screen.findByText(/No connection — the PIN never reached the server/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Check your details/)).not.toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
  });

  it("unpinning the tablet needs a deliberate confirm (W2)", async () => {
    window.localStorage.setItem("herdly.tabletFarm", "3");
    server.use(http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)));
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());

    await screen.findByTestId("worker-roster");
    await user.click(screen.getByRole("button", { name: "Change farm" }));

    // One stray tap only opens the confirm dialog — the farm stays pinned.
    expect(await screen.findByText("Change this tablet's farm?")).toBeInTheDocument();
    expect(window.localStorage.getItem("herdly.tabletFarm")).toBe("3");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByText("Change this tablet's farm?")).not.toBeInTheDocument(),
    );
    expect(window.localStorage.getItem("herdly.tabletFarm")).toBe("3");

    // The deliberate confirm unpins and offers the manager setup again.
    await user.click(screen.getByRole("button", { name: "Change farm" }));
    await user.click(await screen.findByTestId("worker-unpin-confirm"));
    expect(window.localStorage.getItem("herdly.tabletFarm")).toBeNull();
    expect(await screen.findByText("No farm on this tablet")).toBeInTheDocument();
  });

  it("cancelling setup at the farm choice signs the manager back out (W3)", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          access_token: "manager-token",
          token_type: "bearer",
          user: { id: 1, email: "owner@farm.in", name: "Owner", must_change_password: false },
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());

    await user.click(await screen.findByTestId("worker-setup-start"));
    await user.type(screen.getByLabelText("Email"), "owner@farm.in");
    await user.type(screen.getByLabelText("Password"), "owner-pass-123");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // Farm choice reached: the manager session is committed but unpinned.
    await screen.findByTestId("worker-setup-farms");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(signOutMock).toHaveBeenCalled());
    expect(replaceMock).toHaveBeenCalledWith("/worker/login");
  });

  it("signs the manager out if the setup page is left mid-flow (W3)", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({
          access_token: "manager-token",
          token_type: "bearer",
          user: { id: 1, email: "owner@farm.in", name: "Owner", must_change_password: false },
        }),
      ),
    );
    const user = userEvent.setup();
    const view = renderWithProviders(<WorkerLoginPage />, createTestQueryClient());

    await user.click(await screen.findByTestId("worker-setup-start"));
    await user.type(screen.getByLabelText("Email"), "owner@farm.in");
    await user.type(screen.getByLabelText("Password"), "owner-pass-123");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByTestId("worker-setup-farms");

    // Navigating away mid-setup is an abandon: the manager session ends.
    view.unmount();
    expect(signOutMock).toHaveBeenCalled();
  });

  it("answers a wrong PIN with its own message", async () => {
    window.localStorage.setItem("herdly.tabletFarm", "3");
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
      http.post("/api/auth/worker-login", () =>
        HttpResponse.json({ detail: "Invalid PIN." }, { status: 401 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());
    await user.click(await screen.findByRole("button", { name: "Ravi" }));
    for (const digit of ["1", "2", "3", "4"]) {
      fireEvent.click(
        screen.getByRole("button", { name: new RegExp(`pin ${digit}`, "i") }),
      );
    }
    await user.click(screen.getByTestId("pin-sign-in"));
    expect(await screen.findByText("Wrong PIN — try again.")).toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
  });

  it("answers an offline PIN attempt with a connection message, never 'Wrong PIN'", async () => {
    window.localStorage.setItem("herdly.tabletFarm", "3");
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
      // HttpResponse.error() is the MSW-native network failure: the request
      // never reached the server, so the PIN was never judged.
      http.post("/api/auth/worker-login", () => HttpResponse.error()),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());
    await user.click(await screen.findByRole("button", { name: "Ravi" }));
    for (const digit of ["1", "2", "3", "4"]) {
      fireEvent.click(
        screen.getByRole("button", { name: new RegExp(`pin ${digit}`, "i") }),
      );
    }
    await user.click(screen.getByTestId("pin-sign-in"));
    expect(
      await screen.findByText(/No connection — the PIN never reached the server/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Wrong PIN/)).not.toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
  });

  it("answers a rate-limited attempt with the wait message", async () => {
    window.localStorage.setItem("herdly.tabletFarm", "3");
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
      http.post("/api/auth/worker-login", () =>
        HttpResponse.json(
          { detail: "Too many attempts.", code: "RATE_LIMITED" },
          { status: 429 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());
    await user.click(await screen.findByRole("button", { name: "Ravi" }));
    for (const digit of ["1", "2", "3", "4"]) {
      fireEvent.click(
        screen.getByRole("button", { name: new RegExp(`pin ${digit}`, "i") }),
      );
    }
    await user.click(screen.getByTestId("pin-sign-in"));
    expect(
      await screen.findByText("Too many attempts — wait a few minutes and try again."),
    ).toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
  });

  it("selects the pinned farm after sign-in when the worker belongs to several farms", async () => {
    window.localStorage.setItem("herdly.tabletFarm", "3");
    // Membership discovery ordered another farm first; the tablet's pinned
    // farm must still win over the list[0] auto-select fallback.
    getFarmsMock.mockReturnValue([
      { id: 9, name: "Other Farm", location: null, timezone: "Asia/Kolkata", role: null },
      { id: 3, name: "Tablet Farm", location: null, timezone: "Asia/Kolkata", role: null },
    ]);
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
      http.post("/api/auth/worker-login", () =>
        HttpResponse.json({
          access_token: "tablet-token",
          token_type: "bearer",
          user: { id: 7, email: "pin@farm.in", name: "Lakshmi", must_change_password: false },
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());
    await user.click(await screen.findByRole("button", { name: "Lakshmi" }));
    for (const digit of ["4", "3", "2", "1"]) {
      fireEvent.click(
        screen.getByRole("button", { name: new RegExp(`pin ${digit}`, "i") }),
      );
    }
    await user.click(screen.getByTestId("pin-sign-in"));

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/worker"));
    expect(selectFarmMock).toHaveBeenCalledWith(3, "Asia/Kolkata");
  });

  it("keeps the discovery fallback when the worker is not a member of the pinned farm", async () => {
    window.localStorage.setItem("herdly.tabletFarm", "3");
    getFarmsMock.mockReturnValue([
      { id: 9, name: "Other Farm", location: null, timezone: "Asia/Kolkata", role: null },
    ]);
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
      http.post("/api/auth/worker-login", () =>
        HttpResponse.json({
          access_token: "tablet-token",
          token_type: "bearer",
          user: { id: 7, email: "pin@farm.in", name: "Lakshmi", must_change_password: false },
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());
    await user.click(await screen.findByRole("button", { name: "Lakshmi" }));
    for (const digit of ["4", "3", "2", "1"]) {
      fireEvent.click(
        screen.getByRole("button", { name: new RegExp(`pin ${digit}`, "i") }),
      );
    }
    await user.click(screen.getByTestId("pin-sign-in"));

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/worker"));
    expect(selectFarmMock).not.toHaveBeenCalled();
  });
});

describe("WorkerBoardPage", () => {
  function renderBoard() {
    return renderWithProviders(<WorkerBoardPage />, createTestQueryClient());
  }

  it.each([
    ["en", "This password was set by the farm owner — change it before using the farm.", "Change password"],
    ["te", "ఈ పాస్‌వర్డ్‌ను ఫారం యజమాని సెట్ చేశారు — ఫారం వాడే ముందు మార్చండి.", "పాస్‌వర్డ్ మార్చండి"],
  ])("explains first password rotation in %s and fetches duties only after the user requirement clears", async (language, guidance, action) => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
    workerAuth.mustChangePassword = true;
    let taskReads = 0;
    server.use(http.get("/api/tasks", () => {
      taskReads += 1;
      return HttpResponse.json({ today: [BOARD(1, "Duty after password rotation", today())], overdue: [], upcoming: [], awaiting: [], completed: [],
        totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 } });
    }));
    const board = <LanguageProvider><WorkerBoardPage /></LanguageProvider>;
    const view = renderWithProviders(board, createTestQueryClient());
    expect(await screen.findByText(guidance)).toBeInTheDocument();
    expect(screen.getByText(action)).toBeInTheDocument();
    expect(screen.getByTestId("worker-password-required")).toBeInTheDocument();
    await settle();
    expect(taskReads).toBe(0);
    expect(screen.queryByTestId("worker-duty-1")).not.toBeInTheDocument();
    expect(screen.queryByText("Something went wrong.")).not.toBeInTheDocument();

    // AccountDialog installs the refreshed UserOut after actual rotation.
    // Re-render that changed auth hook state rather than bypassing the gate.
    workerAuth.mustChangePassword = false;
    view.rerender(<LanguageProvider><WorkerBoardPage /></LanguageProvider>);
    expect(await screen.findByTestId("worker-duty-1")).toBeInTheDocument();
    expect(taskReads).toBe(1);
    expect(screen.queryByTestId("worker-password-required")).not.toBeInTheDocument();
    expect(screen.queryByText(guidance)).not.toBeInTheDocument();
  });

  it("renders overdue and today duties as large cards", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [BOARD(1, "Feed the bucks", today())],
          overdue: [BOARD(2, "Clean water trough", "2026-09-01")],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 1, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
    );
    renderBoard();

    expect(await screen.findByText("Feed the bucks")).toBeInTheDocument();
    expect(screen.getByText("Clean water trough")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Overdue \(1\)/ })).toBeInTheDocument();
    expect(screen.getByTestId("complete-1")).toBeInTheDocument();
  });

  it("keeps cached duties visible but clearly marks a failed refresh as stale", async () => {
    let reads = 0;
    const payload = {
      today: [BOARD(1, "Feed the bucks", today())],
      overdue: [],
      upcoming: [],
      awaiting: [],
      completed: [],
      totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
    };
    server.use(
      http.get("/api/tasks", () => {
        reads += 1;
        return reads === 2
          ? HttpResponse.json({ detail: "refresh unavailable" }, { status: 503 })
          : HttpResponse.json(payload);
      }),
    );
    const queryClient = createTestQueryClient();
    const user = userEvent.setup();
    renderWithProviders(<WorkerBoardPage />, queryClient);
    expect(await screen.findByText("Feed the bucks")).toBeInTheDocument();

    await act(async () => {
      await queryClient.refetchQueries();
    });

    const warning = await screen.findByRole("alert");
    expect(warning).toHaveTextContent("Could not refresh duties.");
    expect(warning).toHaveTextContent("Actions remain available");
    expect(screen.getByText("Feed the bucks")).toBeInTheDocument();
    expect(screen.getByTestId("complete-1")).toBeEnabled();
    expect(screen.getByTestId("complete-1")).toHaveAccessibleName(
      /saved against the last checked board/i,
    );

    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(reads).toBe(3);
  });

  it("renders a keyed duty title in the worker's language, not the raw English payload", async () => {
    // 2026-09-28 audit, H5: the backend ships title_key/title_args precisely
    // so the Telugu-first board never shows the payload's English title.
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [
            {
              ...BOARD(1, "Kidding due: G-101", today()),
              title_key: "kidding_due",
              title_args: { tag: "G-101" },
            },
          ],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
    );
    renderWithProviders(
      <LanguageProvider>
        <WorkerBoardPage />
      </LanguageProvider>,
      createTestQueryClient(),
    );

    expect(await screen.findByText("ప్రసవం రానుంది: G-101")).toBeInTheDocument();
    expect(screen.queryByText("Kidding due: G-101")).not.toBeInTheDocument();
  });

  it("renders a keyed duty title through the English catalog by default", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [
            {
              ...BOARD(1, "legacy English title", today()),
              title_key: "kidding_due",
              title_args: { tag: "G-101" },
            },
          ],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
    );
    renderBoard();

    expect(await screen.findByText("Kidding due: G-101")).toBeInTheDocument();
    expect(screen.queryByText("legacy English title")).not.toBeInTheDocument();
  });

  it("completes a duty and refetches", async () => {
    let completed = 0;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [BOARD(1, "Feed the bucks", today())],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
      http.post("/api/tasks/1/complete", ({ request }) => {
        completed += 1;
        expect(request.headers.get("Idempotency-Key")).toMatch(/.+/);
        return HttpResponse.json(BOARD(1, "Feed the bucks", today()));
      }),
    );
    const user = userEvent.setup();
    renderBoard();

    await user.click(await screen.findByTestId("complete-1"));
    await waitFor(() => expect(completed).toBe(1));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Marked done."));
  });

  it("does not toast or refetch when the farm scope changes mid-flight (2026-10-01 audit, 07-L2)", async () => {
    let boardGets = 0;
    let releaseComplete!: () => void;
    const parked = new Promise<void>((resolve) => {
      releaseComplete = resolve;
    });
    server.use(
      http.get("/api/tasks", () => {
        boardGets += 1;
        return HttpResponse.json({
          today: [BOARD(1, "Feed the bucks", today())],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        });
      }),
      http.post("/api/tasks/1/complete", async () => {
        await parked;
        return HttpResponse.json(BOARD(1, "Feed the bucks", today()));
      }),
    );
    const user = userEvent.setup();
    renderBoard();

    await user.click(await screen.findByTestId("complete-1"));
    expect(boardGets).toBe(1);

    // The write is on the wire; the worker's shift ends / farm switches
    // before the answer lands (the epoch bump is what farmScope() reads).
    setCurrentFarmId("99");
    try {
      releaseComplete();
      await settle(100);

      // The success continuation is fenced: no "Marked done." toast for a
      // scope the worker already left, and no board refetch under the NEW
      // scope either.
      expect(toastSuccess).not.toHaveBeenCalled();
      expect(boardGets).toBe(1);
    } finally {
      setCurrentFarmId(null);
    }
  });

  it("queues the completion when the network fails, with the same key", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [BOARD(1, "Feed the bucks", today())],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
      // HttpResponse.error() is the MSW-native network failure (a thrown
      // TypeError inside a handler would surface as a 500 RESPONSE).
      http.post("/api/tasks/1/complete", () => HttpResponse.error()),
    );
    const user = userEvent.setup();
    renderBoard();

    await user.click(await screen.findByTestId("complete-1"));
    await waitFor(async () => expect(await readWorkerOutbox({ actorScope: "7", farmScope: "3" })).toHaveLength(1));
    const record = (await readWorkerOutbox({ actorScope: "7", farmScope: "3" }))[0];
    expect(record.path).toBe("/api/tasks/1/complete");
    expect(record.idempotencyKey).toMatch(/.+/);
    expect(record.actorScope).toBe("7");
    expect(record.farmScope).toBe("3");
  });

  it("shows the empty state for a clear board", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 0, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
    );
    renderBoard();
    expect(await screen.findByText("No duties right now")).toBeInTheDocument();
  });

  it("links a form-linked duty to its form with returnTo=/worker when permitted", async () => {
    server.use(
      permissionsHandler(["tasks.view", "tasks.complete", "health.manage"]),
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [{ ...BOARD(3, "Herd vaccination round", today()), action_url: "/health/new?task_id=3" }],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
    );
    renderBoard();

    const openForm = await screen.findByTestId("open-form-3");
    expect(openForm).toHaveAttribute("href", "/health/new?task_id=3&returnTo=%2Fworker");
  });

  it("hides the form link when the worker lacks the target module's permission", async () => {
    server.use(
      permissionsHandler(["tasks.view", "tasks.complete"]),
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [{ ...BOARD(3, "Herd vaccination round", today()), action_url: "/health/new?task_id=3" }],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
    );
    renderBoard();

    expect(await screen.findByText("Herd vaccination round")).toBeInTheDocument();
    expect(screen.queryByTestId("open-form-3")).not.toBeInTheDocument();
  });

  // 2026-09-28 audit: the badge compared against the UTC date, so a duty due
  // yesterday-in-IST lost its overdue badge every night between 00:00 and
  // 05:30 IST (the tasks board compares against farmToday() for this reason).
  it("badges overdue by the farm's calendar day inside the IST 00:00–05:30 window", async () => {
    vi.setSystemTime(new Date("2026-09-28T23:30:00Z")); // 2026-09-29 05:00 IST
    try {
      server.use(
        http.get("/api/tasks", () =>
          HttpResponse.json({
            today: [
              BOARD(1, "Yesterday's spray round", "2026-09-28"),
              BOARD(2, "Today's feed run", "2026-09-29"),
            ],
            overdue: [],
            upcoming: [],
            awaiting: [],
            completed: [],
            totals: { today: 2, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
          }),
        ),
      );
      renderBoard();

      // 2026-09-28 is already yesterday on the farm's calendar (2026-09-29):
      // the badge must show even though the UTC date still reads 2026-09-28.
      const pastCard = await screen.findByTestId("worker-duty-1");
      expect(
        pastCard.querySelector('[data-slot="badge"][data-variant="destructive"]'),
      ).not.toBeNull();
      expect(
        screen
          .getByTestId("worker-duty-2")
          .querySelector('[data-slot="badge"]'),
      ).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("tells a signed-in worker without tasks.view that the ACCOUNT lacks access, not that the tablet has no farm", async () => {
    // The tablet IS pinned (the layout lets the page render) — the old copy
    // claimed "No farm on this tablet", sending workers to re-pin a healthy
    // tablet instead of asking a manager for duty access (2026-09-28 audit).
    server.use(permissionsHandler(["health.view"]));
    renderBoard();

    expect(await screen.findByText("No duty access on this account")).toBeInTheDocument();
    expect(screen.queryByText("No farm on this tablet")).not.toBeInTheDocument();
  });

  it("persists the skip reason in the device language, not a fixed English string", async () => {
    // The reason is user-authored content (like a typed reason): it reads
    // back to managers in the duty history, so it follows the device's
    // language instead of the hardcoded "Tablet skip" (2026-09-28 audit).
    let skipBody: Record<string, unknown> | null = null;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [BOARD(1, "Feed the bucks", today())],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
      http.post("/api/tasks/1/skip", async ({ request }) => {
        skipBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(BOARD(1, "Feed the bucks", today()));
      }),
    );
    const user = userEvent.setup();
    renderBoard();

    await user.click(await screen.findByTestId("skip-1"));
    await waitFor(() => expect(skipBody).not.toBeNull());
    expect(skipBody).toEqual({ reason: "Skipped on the tablet" });
  });

  it("completes a duty on an origin without crypto.randomUUID (2026-10-01 audit, 05-1)", async () => {
    // crypto.randomUUID exists only in secure contexts (HTTPS/localhost); the
    // worker shell explicitly serves plain-http tablet origins. Calling it
    // directly threw BEFORE the try block, so Complete/Skip silently no-opped
    // — no request, no optimistic strike-through, no toast. The key must mint
    // through the getRandomValues fallback (same fake as the
    // idempotent-request suite).
    let receivedKey: string | null = null;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [BOARD(1, "Feed the bucks", today())],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
      http.post("/api/tasks/1/complete", ({ request }) => {
        receivedKey = request.headers.get("Idempotency-Key");
        return HttpResponse.json(BOARD(1, "Feed the bucks", today()));
      }),
    );
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.set(Array.from({ length: 16 }, (_, index) => index));
      return bytes;
    });
    vi.stubGlobal("crypto", { getRandomValues });
    try {
      const user = userEvent.setup();
      renderBoard();

      await user.click(await screen.findByTestId("complete-1"));

      // The mutation reached the server and carried a v4-shaped key minted
      // by the fallback (deterministic bytes 0..15).
      await waitFor(() => expect(receivedKey).not.toBeNull());
      expect(receivedKey).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f");
      await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Marked done."));
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("keeps every in-flight card disabled independently (2026-10-01 audit, 05-4)", async () => {
    // busyId was ONE slot: starting card B's mutation overwrote it and
    // re-enabled card A's Complete/Skip while A's POST was still on the wire
    // (a board refetch mid-flight restores the struck row from server truth,
    // so the buttons ARE mounted again), and a second tap fired a fresh
    // idempotency key into a spurious 409 toast. A Set of busy ids fences
    // each card by its own mutation.
    let releaseFirst!: () => void;
    const firstParked = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [BOARD(1, "Feed the bucks", today()), BOARD(2, "Water round", today())],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 2, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
      http.post("/api/tasks/1/complete", async () => {
        await firstParked;
        return HttpResponse.json(BOARD(1, "Feed the bucks", today()));
      }),
      http.post("/api/tasks/2/complete", () =>
        HttpResponse.json(BOARD(2, "Water round", today())),
      ),
    );
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    renderWithProviders(<WorkerBoardPage />, queryClient);

    await user.click(await screen.findByTestId("complete-1"));
    // The optimistic strike-through removed card 1's actions; a mid-flight
    // board refetch brings the still-PENDING row (and its buttons) back.
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["/api/tasks"] });
    });
    expect(await screen.findByTestId("complete-1")).toBeDisabled();

    // Card B's mutation must not re-enable card A mid-flight.
    await user.click(screen.getByTestId("complete-2"));
    expect(screen.getByTestId("complete-1")).toBeDisabled();

    releaseFirst();
    await waitFor(() => expect(screen.getByTestId("complete-1")).toBeEnabled());
  });
});
