/**
 * Account dialog TOTP section (2026-09-16): enrollment (password → secret →
 * activate), the active state, and disable — including error surfacing.
 */

import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api-client";

import { AccountDialog } from "./account-dialog";

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  authSessionEpochValue: vi.fn(),
  mutateAsync: vi.fn(),
  refreshSessionDetailed: vi.fn(),
  signOut: vi.fn(),
  toastSuccess: vi.fn(),
  updateUser: vi.fn(),
  user: null as { id: number; email: string; name: string | null; totp_state: string | null } | null,
}));

vi.mock("@/api/generated/endpoints", () => ({
  useChangePasswordApiAuthChangePasswordPost: () => ({ mutateAsync: mocks.mutateAsync }),
}));

vi.mock("@/lib/api-client", () => {
  class MockApiError extends Error {
    status: number;
    detail: string;

    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  }
  return {
    ApiError: MockApiError,
    apiFetch: mocks.apiFetch,
    authSessionEpochValue: mocks.authSessionEpochValue,
    refreshSessionDetailed: mocks.refreshSessionDetailed,
  };
});

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ signOut: mocks.signOut, updateUser: mocks.updateUser, user: mocks.user }),
}));

vi.mock("@/lib/format", () => ({ farmToday: () => "2026-08-17" }));
vi.mock("sonner", () => ({ toast: { success: mocks.toastSuccess } }));

async function openAccount(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^Account/ }));
  return screen.getByRole("dialog", { name: "Account & password" });
}

describe("AccountDialog two-factor section", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authSessionEpochValue.mockReturnValue(1);
    mocks.user = {
      id: 1,
      email: "owner@goatfarm.test",
      name: "Owner",
      totp_state: null,
    };
  });

  it("enrolls: password → secret + otpauth link → code activates", async () => {
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/auth/totp/enroll") {
        return {
          secret: "JBSWY3DPEHPK3PXP",
          otpauth_uri: "otpauth://totp/Herdly:owner@goatfarm.test?secret=JBSWY3DPEHPK3PXP",
        };
      }
      if (path === "/api/auth/totp/confirm") {
        // Activation mints the one-time recovery codes (ITEM 7).
        return {
          codes: ["AAAAA-BBBBB", "CCCCC-DDDDD", "EEEEE-FFFFF", "GGGGG-HHHHH", "IIIII-JJJJJ",
                  "KKKKK-LLLLL", "MMMMM-NNNNN", "OOOOO-PPPPP", "QQQQQ-RRRRR", "SSSSS-TTTTT"],
        };
      }
      throw new Error(`unexpected ${path}`);
    });
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);

    await user.click(screen.getByRole("button", { name: /enable two-factor/i }));
    const totp = screen.getByRole("region", { name: /two-factor authentication/i });
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.click(within(totp).getByRole("button", { name: /start enrollment/i }));

    const link = await within(totp).findByRole("link", { name: /otpauth:\/\//i });
    expect(link.getAttribute("href")).toContain("secret=JBSWY3DPEHPK3PXP");
    expect(within(totp).getByText("JBSWY3DPEHPK3PXP")).toBeTruthy();

    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^activate$/i }));

    await waitFor(() =>
      expect(mocks.updateUser).toHaveBeenCalledWith(
        expect.objectContaining({ totp_state: "ACTIVE" }),
      ),
    );
    expect(mocks.toastSuccess).toHaveBeenCalled();
    // ITEM 7: the reveal block shows every code exactly once, with the
    // shown-once warning.
    expect(await within(totp).findByText("AAAAA-BBBBB")).toBeTruthy();
    expect(within(totp).getByText("SSSSS-TTTTT")).toBeTruthy();
    expect(
      within(totp).getByText(/Shown only once — copy or print them now/i),
    ).toBeTruthy();
  });

  it("regenerates recovery codes after password + code proof, revoking the old reveal", async () => {
    mocks.user = {
      id: 1,
      email: "owner@goatfarm.test",
      name: "Owner",
      totp_state: "ACTIVE",
    };
    const calls: string[] = [];
    mocks.apiFetch.mockImplementation(async (path: string, init?: { body?: string }) => {
      calls.push(path);
      if (path === "/api/auth/totp/recovery/regenerate") {
        expect(JSON.parse(init?.body ?? "{}")).toEqual({
          current_password: "owner-password-1",
          code: "123456",
        });
        return { codes: ["ZZZZZ-ZZZZZ", "YYYYY-YYYYY"] };
      }
      throw new Error(`unexpected ${path}`);
    });
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);

    const totp = screen.getByRole("region", { name: /two-factor authentication/i });
    await user.click(
      within(totp).getByRole("button", { name: /regenerate recovery codes/i }),
    );
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^regenerate codes$/i }));

    expect(await within(totp).findByText("ZZZZZ-ZZZZZ")).toBeTruthy();
    expect(within(totp).getByText("YYYYY-YYYYY")).toBeTruthy();
    expect(calls).toEqual(["/api/auth/totp/recovery/regenerate"]);
  });

  it("surfaces a wrong password from the enroll endpoint inline", async () => {
    mocks.apiFetch.mockRejectedValue(
      new ApiError(400, "Current password is incorrect."),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);

    await user.click(screen.getByRole("button", { name: /enable two-factor/i }));
    const totp = screen.getByRole("region", { name: /two-factor authentication/i });
    await user.type(within(totp).getByLabelText(/current password/i), "wrong");
    await user.click(within(totp).getByRole("button", { name: /start enrollment/i }));

    expect(await within(totp).findByText("Current password is incorrect.")).toBeTruthy();
    // Still on the password step.
    expect(within(totp).getByLabelText(/current password/i)).toBeTruthy();
  });

  it("active state offers disable and clears the enrollment on success", async () => {
    mocks.user = {
      id: 1,
      email: "owner@goatfarm.test",
      name: "Owner",
      totp_state: "ACTIVE",
    };
    mocks.apiFetch.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);

    const totp = screen.getByRole("region", { name: /two-factor authentication/i });
    expect(within(totp).getByText(/^On:/i)).toBeTruthy();
    await user.click(within(totp).getByRole("button", { name: /disable two-factor/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /disable two-factor/i }));

    await waitFor(() =>
      expect(mocks.updateUser).toHaveBeenCalledWith(
        expect.objectContaining({ totp_state: null }),
      ),
    );
  });
});

/** Mutation-hardening round 2 (2026-09-23 deep-mutation campaign): the
 *  epoch fences in confirm/disable/regenerate had no tests on either side —
 *  a fresh failure had to surface, a stale one (dialog closed mid-flight)
 *  had to stay silent, and the busy flags wiring the disable/regen buttons
 *  were never asserted. Also pins the recovery-copy label lifecycle and the
 *  6/128 character input caps. */
describe("AccountDialog TOTP busy, epoch fences and recovery-copy state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authSessionEpochValue.mockReturnValue(1);
    mocks.user = {
      id: 1,
      email: "owner@goatfarm.test",
      name: "Owner",
      totp_state: null,
    };
  });

  function deferred<T>() {
    let settle!: (value: T | PromiseLike<T>) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
      settle = res;
      reject = rej;
    });
    return { promise, settle, reject };
  }

  async function openActiveTotp(user: ReturnType<typeof userEvent.setup>) {
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);
    return screen.getByRole("region", { name: /two-factor authentication/i });
  }

  it("shows a fresh disable failure inline and falls back to the network message", async () => {
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    mocks.apiFetch.mockRejectedValueOnce(new ApiError(400, "Code rejected."));
    mocks.apiFetch.mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup();
    const totp = await openActiveTotp(user);

    await user.click(within(totp).getByRole("button", { name: /disable two-factor/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^disable two-factor$/i }));
    expect(await within(totp).findByText("Code rejected.")).toBeTruthy();

    // A non-ApiError transport failure renders the generic network phrase.
    await user.click(within(totp).getByRole("button", { name: /^disable two-factor$/i }));
    expect(await within(totp).findByText(/network error — try again/i)).toBeTruthy();
    // busy cleared: the confirm button is enabled again after both failures.
    expect(within(totp).getByRole("button", { name: /^disable two-factor$/i })).toBeEnabled();
  });

  it("disables the destructive button and shows the busy label while a disable is in flight", async () => {
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    const gate = deferred<void>();
    mocks.apiFetch.mockImplementation(() => gate.promise);
    const user = userEvent.setup();
    const totp = await openActiveTotp(user);

    await user.click(within(totp).getByRole("button", { name: /disable two-factor/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^disable two-factor$/i }));

    const busy = within(totp).getByRole("button", { name: /^disabling…$/i });
    expect(busy).toBeDisabled();
    await act(async () => gate.settle(undefined));
    // Fresh completion clears busy: the idle disable button is usable again.
    await waitFor(() =>
      expect(within(totp).getByRole("button", { name: /disable two-factor/i })).toBeEnabled(),
    );
  });

  it("keeps disable/regenerate disabled while an unrelated account action runs", async () => {
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    const gate = deferred<unknown>();
    mocks.apiFetch.mockImplementation(() => gate.promise);
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    const dialog = await openAccount(user);
    const totp = within(dialog).getByRole("region", { name: /two-factor authentication/i });

    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));
    expect(within(totp).getByRole("button", { name: /disable two-factor/i })).toBeDisabled();
    expect(
      within(totp).getByRole("button", { name: /regenerate recovery codes/i }),
    ).toBeDisabled();
    await act(async () => gate.settle({ account: { id: 1 } }));
    await waitFor(() =>
      expect(within(totp).getByRole("button", { name: /disable two-factor/i })).toBeEnabled(),
    );
  });

  it("a stale disable failure after the dialog closed never resurfaces", async () => {
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    const gate = deferred<void>();
    mocks.apiFetch.mockImplementation(() => gate.promise);
    const user = userEvent.setup();
    const totp = await openActiveTotp(user);

    await user.click(within(totp).getByRole("button", { name: /disable two-factor/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^disable two-factor$/i }));
    // Close mid-flight (bumps the epoch), then the rejection lands.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await act(async () => gate.reject(new ApiError(400, "Code rejected.")));

    const dialog = await openAccount(user);
    const reopened = within(dialog).getByRole("region", { name: /two-factor authentication/i });
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/code rejected/i)).not.toBeInTheDocument();
  });

  it("a stale activate failure after the dialog closed never resurfaces", async () => {
    const enrollGate = deferred<{ secret: string; otpauth_uri: string }>();
    const confirmGate = deferred<{ codes: string[] }>();
    mocks.apiFetch.mockImplementation((path: string) =>
      path === "/api/auth/totp/enroll"
        ? enrollGate.promise
        : path === "/api/auth/totp/confirm"
          ? confirmGate.promise
          : Promise.reject(new Error(`unexpected ${path}`)),
    );
    const user = userEvent.setup();
    const totp = await openActiveTotp(user);

    await user.click(screen.getByRole("button", { name: /enable two-factor/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.click(within(totp).getByRole("button", { name: /start enrollment/i }));
    await act(async () =>
      enrollGate.settle({
        secret: "JBSWY3DPEHPK3PXP",
        otpauth_uri: "otpauth://totp/Herdly:x?secret=JBSWY3DPEHPK3PXP",
      }),
    );
    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^activate$/i }));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await act(async () => confirmGate.reject(new ApiError(400, "Code rejected.")));

    const dialog = await openAccount(user);
    const reopened = within(dialog).getByRole("region", { name: /two-factor authentication/i });
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("a stale regenerate failure after the dialog closed never resurfaces", async () => {
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    const gate = deferred<{ codes: string[] }>();
    mocks.apiFetch.mockImplementation(() => gate.promise);
    const user = userEvent.setup();
    const totp = await openActiveTotp(user);

    await user.click(within(totp).getByRole("button", { name: /regenerate recovery codes/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^regenerate codes$/i }));
    expect(within(totp).getByRole("button", { name: /^regenerating…$/i })).toBeDisabled();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await act(async () => gate.reject(new ApiError(400, "Code rejected.")));

    const dialog = await openAccount(user);
    const reopened = within(dialog).getByRole("region", { name: /two-factor authentication/i });
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("cycles the recovery-copy label: Copy → Copied → Copy on a fresh reveal", async () => {
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    const clipboard = vi.fn().mockResolvedValue(undefined);
    let first = true;
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/auth/totp/recovery/regenerate") {
        return first
          ? { codes: ["AAAAA-BBBBB", "CCCCC-DDDDD"] }
          : { codes: ["EEEEE-FFFFF", "GGGGG-HHHHH"] };
      }
      throw new Error(`unexpected ${path}`);
    });
    const user = userEvent.setup();
    // AFTER setup(): user-event installs its own navigator.clipboard stub,
    // which would otherwise shadow this spy before the component reads it.
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: clipboard },
      configurable: true,
    });
    const totp = await openActiveTotp(user);

    const revealCodes = async () => {
      await user.click(within(totp).getByRole("button", { name: /regenerate recovery codes/i }));
      await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
      await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
      await user.click(within(totp).getByRole("button", { name: /^regenerate codes$/i }));
    };

    await revealCodes();
    // Fresh reveal starts un-copied…
    expect(within(totp).getByRole("button", { name: /^copy codes$/i })).toBeTruthy();
    // …clicking copy flips the label exactly once.
    await user.click(within(totp).getByRole("button", { name: /^copy codes$/i }));
    expect(await within(totp).findByRole("button", { name: /^recovery codes copied$/i })).toBeTruthy();
    expect(clipboard).toHaveBeenCalledTimes(1);

    first = false;
    await revealCodes();
    // A new reveal revokes the copied state.
    expect(within(totp).getByRole("button", { name: /^copy codes$/i })).toBeTruthy();
    expect(within(totp).queryByRole("button", { name: /^recovery codes copied$/i })).toBeNull();
  });

  it("caps TOTP inputs at 6 digits and passwords at 128 characters", async () => {
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    mocks.apiFetch.mockResolvedValue(undefined);
    const user = userEvent.setup();
    const totp = await openActiveTotp(user);

    await user.click(within(totp).getByRole("button", { name: /disable two-factor/i }));
    const password = within(totp).getByLabelText(/current password/i) as HTMLInputElement;
    const code = within(totp).getByLabelText(/authenticator code/i) as HTMLInputElement;
    await user.type(password, "x".repeat(129));
    await user.type(code, "1234567");
    expect(password.value.length).toBe(128);
    expect(code.value).toBe("123456");
  });
});

/** Same hardening round: enroll/confirm flows — busy labels, fresh-error
 *  surfacing (ApiError detail vs generic network phrase), busy clearing
 *  after a fresh settle, the copied-label reset on a SECOND activation, and
 *  the 6/128 input caps of every TOTP field across all four modes. */
describe("AccountDialog enroll/confirm hardening", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authSessionEpochValue.mockReturnValue(1);
    mocks.user = {
      id: 1,
      email: "owner@goatfarm.test",
      name: "Owner",
      totp_state: null,
    };
  });

  it("busy-labels and disables start/activate, surfaces fresh errors, and clears busy", async () => {
    const enrollGate = deferred<{ secret: string; otpauth_uri: string }>();
    const confirmGate = deferred<{ codes: string[] }>();
    mocks.apiFetch.mockImplementation((path: string) =>
      path === "/api/auth/totp/enroll"
        ? enrollGate.promise
        : path === "/api/auth/totp/confirm"
          ? confirmGate.promise
          : Promise.reject(new Error(`unexpected ${path}`)),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);
    const totp = screen.getByRole("region", { name: /two-factor authentication/i });

    await user.click(screen.getByRole("button", { name: /enable two-factor/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.click(within(totp).getByRole("button", { name: /start enrollment/i }));
    expect(within(totp).getByRole("button", { name: /^starting…$/i })).toBeDisabled();
    await act(async () =>
      enrollGate.settle({
        secret: "JBSWY3DPEHPK3PXP",
        otpauth_uri: "otpauth://totp/Herdly:x?secret=JBSWY3DPEHPK3PXP",
      }),
    );

    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^activate$/i }));
    expect(within(totp).getByRole("button", { name: /^activating…$/i })).toBeDisabled();
    // Fresh ApiError failure: the detail lands inline and busy clears.
    await act(async () => confirmGate.reject(new ApiError(400, "Code rejected.")));
    expect(await within(totp).findByText("Code rejected.")).toBeTruthy();
    expect(within(totp).getByRole("button", { name: /^activate$/i })).toBeEnabled();

    // A non-ApiError failure renders the generic network phrase instead.
    mocks.apiFetch.mockImplementation((path: string) =>
      path === "/api/auth/totp/confirm"
        ? Promise.reject(new Error("offline"))
        : Promise.reject(new Error(`unexpected ${path}`)),
    );
    await user.click(within(totp).getByRole("button", { name: /^activate$/i }));
    expect(await within(totp).findByText(/network error — try again/i)).toBeTruthy();
    expect(within(totp).getByRole("button", { name: /^activate$/i })).toBeEnabled();
  });

  it("resets the copied label on a second activation and stays operable after it", async () => {
    const clipboard = vi.fn().mockResolvedValue(undefined);
    let activations = 0;
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/auth/totp/enroll") {
        return { secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/Herdly:x?secret=JBSWY3DPEHPK3PXP" };
      }
      if (path === "/api/auth/totp/confirm") {
        activations += 1;
        return {
          codes: [`${activations}AAAA-BBBBB`, `${activations}CCCCC-DDDDD`],
        };
      }
      throw new Error(`unexpected ${path}`);
    });
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: clipboard },
      configurable: true,
    });

    const activateOnce = async (totp: HTMLElement) => {
      await user.click(screen.getByRole("button", { name: /enable two-factor/i }));
      await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
      await user.click(within(totp).getByRole("button", { name: /start enrollment/i }));
      await user.type(await within(totp).findByLabelText(/authenticator code/i), "123456");
      await user.click(within(totp).getByRole("button", { name: /^activate$/i }));
      await waitFor(() => expect(within(totp).getByRole("button", { name: /^copy codes$/i })).toBeTruthy());
    };

    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);
    let totp = screen.getByRole("region", { name: /two-factor authentication/i });
    await activateOnce(totp);
    await user.click(within(totp).getByRole("button", { name: /^copy codes$/i }));
    expect(await within(totp).findByRole("button", { name: /^recovery codes copied$/i })).toBeTruthy();

    // Turn TOTP off, then activate again: the fresh reveal must arrive
    // un-copied and with busy fully cleared.
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: null };
    rerenderFresh();
    await openAccount(user);
    totp = screen.getByRole("region", { name: /two-factor authentication/i });
    await activateOnce(totp);
    expect(within(totp).getByRole("button", { name: /^copy codes$/i })).toBeTruthy();
    expect(within(totp).queryByRole("button", { name: /^recovery codes copied$/i })).toBeNull();
  });

  it("caps every TOTP input: enroll/confirm/disable/regen", async () => {
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/auth/totp/enroll") {
        return { secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/Herdly:x?secret=JBSWY3DPEHPK3PXP" };
      }
      throw new Error(`unexpected ${path}`);
    });
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);
    const totp = screen.getByRole("region", { name: /two-factor authentication/i });

    // enroll password
    await user.click(screen.getByRole("button", { name: /enable two-factor/i }));
    const enrollPassword = within(totp).getByLabelText(/current password/i) as HTMLInputElement;
    await user.type(enrollPassword, "x".repeat(129));
    expect(enrollPassword.value.length).toBe(128);
    await user.click(within(totp).getByRole("button", { name: /start enrollment/i }));

    // confirm code
    const confirmCode = await within(totp).findByLabelText(/authenticator code/i);
    await user.type(confirmCode as HTMLInputElement, "1234567");
    expect((confirmCode as HTMLInputElement).value).toBe("123456");

    // disable + regen modes (ACTIVE user)
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    rerenderFresh();
    await openAccount(user);
    for (const mode of [/disable two-factor/i, /regenerate recovery codes/i] as const) {
      const section = screen.getByRole("region", { name: /two-factor authentication/i });
      await user.click(within(section).getByRole("button", { name: mode }));
      const password = within(section).getByLabelText(/current password/i) as HTMLInputElement;
      const code = within(section).getByLabelText(/authenticator code/i) as HTMLInputElement;
      await user.type(password, "y".repeat(129));
      await user.type(code, "7654321");
      expect(password.value.length, String(mode)).toBe(128);
      expect(code.value, String(mode)).toBe("765432");
      await user.click(within(section).getByRole("button", { name: /cancel/i }));
    }
  });

  it("a fresh regenerate failure surfaces inline, and success leaves later modes operable", async () => {
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    let fail = true;
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/auth/totp/recovery/regenerate") {
        if (fail) throw new ApiError(400, "Code rejected.");
        return { codes: ["EEEEE-FFFFF", "GGGGG-HHHHH"] };
      }
      throw new Error(`unexpected ${path}`);
    });
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);
    const totp = screen.getByRole("region", { name: /two-factor authentication/i });

    const reveal = async () => {
      await user.click(within(totp).getByRole("button", { name: /regenerate recovery codes/i }));
      await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
      await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
      await user.click(within(totp).getByRole("button", { name: /^regenerate codes$/i }));
    };

    await reveal();
    expect(await within(totp).findByText("Code rejected.")).toBeTruthy();
    expect(within(totp).getByRole("button", { name: /^regenerate codes$/i })).toBeEnabled();

    fail = false;
    // The failed attempt stays in regen mode; retry the same form.
    await user.click(within(totp).getByRole("button", { name: /^regenerate codes$/i }));
    expect(await within(totp).findByText("EEEEE-FFFFF")).toBeTruthy();
    // busy from the fresh success must be cleared: disable-mode confirm usable.
    await user.click(within(totp).getByRole("button", { name: /disable two-factor/i }));
    expect(within(totp).getByRole("button", { name: /^disable two-factor$/i })).toBeEnabled();
  });
});

function rerenderFresh() {
  cleanup();
  render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
}

// Shared deferred helper (module level so both hardening describes see it).
function deferred<T>() {
  let settle!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    settle = res;
    reject = rej;
  });
  return { promise, settle, reject };
}

/** Round 2 tail: each TOTP mode renders NO alert paragraph while no failure
 *  has happened — an always-rendered (empty) alert would be screen-reader
 *  noise announcing a phantom error. */
describe("AccountDialog TOTP alert absence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authSessionEpochValue.mockReturnValue(1);
    mocks.user = {
      id: 1,
      email: "owner@goatfarm.test",
      name: "Owner",
      totp_state: null,
    };
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/auth/totp/enroll") {
        return { secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/Herdly:x?secret=JBSWY3DPEHPK3PXP" };
      }
      throw new Error(`unexpected ${path}`);
    });
  });

  it("no alert in enroll, confirm, disable, or regen modes before any failure", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);
    let totp = screen.getByRole("region", { name: /two-factor authentication/i });

    // enroll mode
    await user.click(screen.getByRole("button", { name: /enable two-factor/i }));
    expect(within(totp).queryByRole("alert")).not.toBeInTheDocument();
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.click(within(totp).getByRole("button", { name: /start enrollment/i }));

    // confirm mode (after a successful enroll)
    await within(totp).findByLabelText(/authenticator code/i);
    expect(within(totp).queryByRole("alert")).not.toBeInTheDocument();

    // disable + regen modes for an ACTIVE user
    mocks.user = { id: 1, email: "owner@goatfarm.test", name: "Owner", totp_state: "ACTIVE" };
    cleanup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);
    totp = screen.getByRole("region", { name: /two-factor authentication/i });
    for (const mode of [/disable two-factor/i, /regenerate recovery codes/i] as const) {
      await user.click(within(totp).getByRole("button", { name: mode }));
      expect(within(totp).queryByRole("alert"), String(mode)).not.toBeInTheDocument();
      await user.click(within(totp).getByRole("button", { name: /cancel/i }));
    }
  });
});

/** Phase C fixes (2026-09-23 campaign): a stale settle must not leave the
 *  reopened dialog's TOTP controls locked, and an empty-string display name
 *  falls back to the email everywhere the trigger shows identity. */
describe("AccountDialog stale-busy recovery and empty-name fallback", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authSessionEpochValue.mockReturnValue(1);
    mocks.user = {
      id: 1,
      email: "owner@goatfarm.test",
      name: "Owner",
      totp_state: "ACTIVE",
    };
  });

  it("stays operable after a disable settles stale (post-close)", async () => {
    const gate = deferred<void>();
    mocks.apiFetch.mockImplementation(() => gate.promise);
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@goatfarm.test" />);
    await openAccount(user);
    let totp = screen.getByRole("region", { name: /two-factor authentication/i });

    await user.click(within(totp).getByRole("button", { name: /disable two-factor/i }));
    await user.type(within(totp).getByLabelText(/current password/i), "owner-password-1");
    await user.type(within(totp).getByLabelText(/authenticator code/i), "123456");
    await user.click(within(totp).getByRole("button", { name: /^disable two-factor$/i }));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // The fenced-off response lands late.
    await act(async () => gate.settle(undefined));

    const dialog = await openAccount(user);
    const reopened = within(dialog).getByRole("region", { name: /two-factor authentication/i });
    // TOTP mode must still be enterable and its confirm usable — not locked
    // by a stuck busy flag from the dead session.
    await user.click(within(reopened).getByRole("button", { name: /disable two-factor/i }));
    expect(within(reopened).getByRole("button", { name: /^disable two-factor$/i })).toBeEnabled();
  });

  it("falls back to the email when the display name is an empty string", () => {
    render(<AccountDialog name="" email="owner@goatfarm.test" />);
    expect(screen.getByRole("button", { name: "Account — owner@goatfarm.test" })).toBeTruthy();
  });
});
