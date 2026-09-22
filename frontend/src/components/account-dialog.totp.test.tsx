/**
 * Account dialog TOTP section (2026-09-16): enrollment (password → secret →
 * activate), the active state, and disable — including error surfacing.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
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
