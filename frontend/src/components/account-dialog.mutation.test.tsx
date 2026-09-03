/**
 * Mutation-hardening for src/components/account-dialog.tsx.
 *
 * Pins the trigger's identity contract (the aria-label composed from the
 * display name or email, and the two-letter initials badge built from the
 * trimmed uppercased identity) and the synchronous busy label the password
 * submit button must adopt the instant the action lock is taken — before
 * react-hook-form has flipped isSubmitting on its own.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountDialog } from "./account-dialog";

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  authSessionEpochValue: vi.fn(),
  mutateAsync: vi.fn(),
  refreshSessionDetailed: vi.fn(),
  signOut: vi.fn(),
  updateUser: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("@/api/generated/endpoints", () => ({
  useChangePasswordApiAuthChangePasswordPost: () => ({
    mutateAsync: mocks.mutateAsync,
  }),
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
  useAuth: () => ({ signOut: mocks.signOut, updateUser: mocks.updateUser }),
}));

vi.mock("@/lib/format", () => ({ farmToday: () => "2026-08-17" }));
vi.mock("sonner", () => ({ toast: { success: mocks.toastSuccess } }));

async function openAccount(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^Account/ }));
  return screen.getByRole("dialog", { name: "Account & password" });
}

async function fillValidPasswordChange(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
) {
  await user.type(
    within(dialog).getByLabelText("Current password for password change"),
    "old-password",
  );
  await user.type(within(dialog).getByLabelText("New password"), "new-password-123");
  await user.type(
    within(dialog).getByLabelText("Confirm new password"),
    "new-password-123",
  );
}

describe("AccountDialog trigger identity", () => {
  beforeEach(() => {
    mocks.apiFetch.mockReset();
    mocks.authSessionEpochValue.mockReset().mockReturnValue(1);
    mocks.mutateAsync.mockReset().mockResolvedValue({ status: 200 });
    mocks.refreshSessionDetailed.mockReset().mockResolvedValue({
      kind: "session",
      body: {
        access_token: "rotated",
        user: { id: 1, email: "owner@example.test", name: "Owner" },
      },
    });
    mocks.signOut.mockReset().mockResolvedValue(undefined);
    mocks.updateUser.mockReset();
    mocks.toastSuccess.mockReset();
  });

  it("composes the trigger label from the display name and shows its initials", () => {
    render(<AccountDialog name="Ada Lovelace" email="ada@example.test" />);

    const trigger = screen.getByRole("button", { name: "Account — Ada Lovelace" });
    const initials = trigger.querySelector("span");
    expect(initials).toHaveTextContent("AD");
    expect(trigger).toHaveTextContent("Ada Lovelace");
  });

  it("trims the identity before deriving the two-letter initials", () => {
    render(<AccountDialog name="  Grace  Hopper  " email="grace@example.test" />);

    const initials = screen
      .getByRole("button", { name: /^Account/ })
      .querySelector("span");
    expect(initials).toHaveTextContent("GR");
  });

  it("falls back to the email for the label and its initials", () => {
    render(<AccountDialog name={null} email="owner@example.test" />);

    const trigger = screen.getByRole("button", {
      name: "Account — owner@example.test",
    });
    expect(trigger.querySelector("span")).toHaveTextContent("OW");
    expect(trigger).toHaveTextContent("owner@example.test");
  });

  it("starts every password field empty rather than undefined", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    expect(
      within(dialog).getByLabelText("Current password for password change"),
    ).toHaveValue("");
    expect(within(dialog).getByLabelText("New password")).toHaveValue("");
    expect(within(dialog).getByLabelText("Confirm new password")).toHaveValue("");

    // Empty submission must surface the schema's own copy, which proves the
    // form's values are strings (""), not undefined defaults.
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(
      await within(dialog).findByText("Current password is required"),
    ).toBeInTheDocument();
  });
});

describe("AccountDialog action labels", () => {
  beforeEach(() => {
    mocks.apiFetch.mockReset();
    mocks.authSessionEpochValue.mockReset().mockReturnValue(1);
    mocks.mutateAsync.mockReset().mockResolvedValue({ status: 200 });
    mocks.refreshSessionDetailed.mockReset().mockResolvedValue({
      kind: "session",
      body: {
        access_token: "rotated",
        user: { id: 1, email: "owner@example.test", name: "Owner" },
      },
    });
    mocks.signOut.mockReset().mockResolvedValue(undefined);
    mocks.updateUser.mockReset();
    mocks.toastSuccess.mockReset();
  });

  it("flips the submit button to its busy label as soon as the lock is taken", async () => {
    let resolveMutation: ((value: { status: number }) => void) | undefined;
    mocks.mutateAsync.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveMutation = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await fillValidPasswordChange(user, dialog);

    // fireEvent (not await user.click) so the assertion runs in the window
    // after the synchronous action lock but before react-hook-form marks the
    // form as submitting on its own.
    fireEvent.click(within(dialog).getByRole("button", { name: "Change password" }));

    const busy = within(dialog).getByRole("button", { name: "Changing…" });
    expect(busy).toBeDisabled();
    expect(within(dialog).getByLabelText("New password")).toBeDisabled();

    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledOnce());
    await act(async () => resolveMutation?.({ status: 200 }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("labels the export button as busy while it prepares", async () => {
    let resolveExport: ((value: unknown) => void) | undefined;
    mocks.apiFetch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveExport = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));

    expect(mocks.apiFetch).toHaveBeenCalledWith("/api/auth/account/export");
    expect(within(dialog).getByRole("button", { name: "Preparing…" })).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Change password" }),
    ).toBeDisabled();

    await act(async () => resolveExport?.({ account: { id: 1 } }));
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Download my data" }),
      ).toBeEnabled(),
    );
  });

  it("runs only one export no matter how fast the clicks land", async () => {
    let rejectExport: ((reason: unknown) => void) | undefined;
    mocks.apiFetch.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectExport = reject;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    const download = within(dialog).getByRole("button", { name: "Download my data" });
    fireEvent.click(download);
    fireEvent.click(download);
    fireEvent.click(download);

    expect(mocks.apiFetch).toHaveBeenCalledTimes(1);
    await act(async () => rejectExport?.(new Error("offline")));
  });
});
