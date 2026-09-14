/**
 * Branch coverage for AccountDialog: the password schema's cross-field rule,
 * the synchronous single-action lock, the state close() has to clear, and the
 * late-settling recovery paths. account-dialog.test.tsx covers the happy paths
 * and the session-epoch fences; this file pins the branches those flows do not
 * reach.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

async function fillPasswordChange(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
  { confirmation = "new-password-123" }: { confirmation?: string } = {},
) {
  await user.type(
    within(dialog).getByLabelText("Current password for password change"),
    "old-password",
  );
  await user.type(within(dialog).getByLabelText("New password"), "new-password-123");
  await user.type(within(dialog).getByLabelText("Confirm new password"), confirmation);
}

/** Lets an already-queued microtask chain (validation, a settled fetch) run. */
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("AccountDialog branches", () => {
  beforeEach(() => {
    mocks.apiFetch.mockReset();
    mocks.authSessionEpochValue.mockReset();
    mocks.mutateAsync.mockReset();
    mocks.refreshSessionDetailed.mockReset();
    mocks.signOut.mockReset();
    mocks.updateUser.mockReset();
    mocks.toastSuccess.mockReset();
    mocks.mutateAsync.mockResolvedValue({ status: 200 });
    mocks.authSessionEpochValue.mockReturnValue(1);
    mocks.refreshSessionDetailed.mockResolvedValue({
      kind: "session",
      body: {
        access_token: "rotated",
        user: { id: 1, email: "owner@example.test", name: "Owner" },
      },
    });
    mocks.signOut.mockResolvedValue(undefined);
  });

  it("blames the confirmation field when the two new passwords differ", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await fillPasswordChange(user, dialog, { confirmation: "new-password-124" });

    await user.click(within(dialog).getByRole("button", { name: "Change password" }));

    const confirmation = within(dialog).getByLabelText("Confirm new password");
    expect(await within(dialog).findByText("Passwords do not match")).toBeInTheDocument();
    expect(confirmation).toHaveAttribute("aria-invalid", "true");
    expect(confirmation).toHaveAttribute(
      "aria-describedby",
      "account-confirm-password-error",
    );
    expect(confirmation).toHaveAccessibleDescription("Passwords do not match");
    // The mismatch belongs to the confirmation alone: the new password itself
    // is valid and must not be flagged.
    expect(within(dialog).getByLabelText("New password")).not.toHaveAttribute("aria-invalid");
    expect(mocks.mutateAsync).not.toHaveBeenCalled();
  });

  it("points each invalid password field at its own message", async () => {
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: "Change password" }));

    const current = await within(dialog).findByLabelText(
      "Current password for password change",
    );
    expect(current).toHaveAttribute("aria-describedby", "account-current-password-error");
    expect(current).toHaveAccessibleDescription("Current password is required");
    const next = within(dialog).getByLabelText("New password");
    expect(next).toHaveAttribute("aria-describedby", "account-new-password-error");
    expect(next).toHaveAccessibleDescription("Password must be at least 12 characters");
    const confirmation = within(dialog).getByLabelText("Confirm new password");
    expect(confirmation).toHaveAttribute("aria-describedby", "account-confirm-password-error");
    expect(confirmation).toHaveAccessibleDescription("Confirm the new password");
  });

  it("refuses a second account action delivered in the same event batch", async () => {
    mocks.apiFetch.mockImplementation(() => new Promise(() => undefined));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    const download = within(dialog).getByRole("button", { name: "Download my data" });

    // Both clicks are handled before React can re-render the disabled state,
    // so the synchronous ref lock is the only thing that can refuse the
    // second one.
    await act(async () => {
      download.click();
      download.click();
    });

    expect(mocks.apiFetch).toHaveBeenCalledOnce();
    expect(within(dialog).getByRole("button", { name: "Preparing…" })).toBeDisabled();
  });

  it("does not submit a password change while another action holds the lock", async () => {
    mocks.apiFetch.mockImplementation(() => new Promise(() => undefined));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await fillPasswordChange(user, dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Download my data" }));
    expect(within(dialog).getByRole("button", { name: "Preparing…" })).toBeDisabled();

    // A submit can still reach the form (an Enter key in a field, a stray
    // requestSubmit); refusing it must not leave the browser to navigate.
    const form = within(dialog).getByRole("form", { name: "Change password" });
    expect(fireEvent.submit(form)).toBe(false);

    await settle();
    expect(mocks.mutateAsync).not.toHaveBeenCalled();
  });

  it("does not refresh the session for a password response other than 200", async () => {
    mocks.mutateAsync.mockResolvedValue({ status: 204 });
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await fillPasswordChange(user, dialog);

    await user.click(within(dialog).getByRole("button", { name: "Change password" }));

    await waitFor(() =>
      expect(mocks.toastSuccess).toHaveBeenCalledWith(
        "Password changed. Other signed-in sessions were revoked.",
      ),
    );
    // Only a 200 carries the rotated session, so nothing should be pulled
    // through the refresh path here.
    expect(mocks.refreshSessionDetailed).not.toHaveBeenCalled();
    expect(mocks.signOut).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("does not close a reopened dialog when a rejected refresh lands late", async () => {
    let resolveRefresh: ((value: { kind: "rejected" }) => void) | undefined;
    mocks.refreshSessionDetailed.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    let dialog = await openAccount(user);
    await fillPasswordChange(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(mocks.refreshSessionDetailed).toHaveBeenCalledOnce());

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await openAccount(user);
    await act(async () => resolveRefresh?.({ kind: "rejected" }));

    // The sign-out still has to happen, but the dialog the user has since
    // reopened is a new one and must stay where they put it.
    expect(mocks.signOut).toHaveBeenCalledOnce();
    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      "Password changed. Sign in again to continue.",
    );
    expect(screen.getByRole("dialog", { name: "Account & password" })).toBeInTheDocument();
  });

  it("does not close a reopened dialog when an unreachable refresh lands late", async () => {
    let resolveRefresh: ((value: { kind: "unavailable" }) => void) | undefined;
    mocks.refreshSessionDetailed.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    let dialog = await openAccount(user);
    await fillPasswordChange(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(mocks.refreshSessionDetailed).toHaveBeenCalledOnce());

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await openAccount(user);
    await act(async () => resolveRefresh?.({ kind: "unavailable" }));

    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      "Password changed. Reconnecting to your session…",
    );
    expect(mocks.signOut).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Account & password" })).toBeInTheDocument();
  });

  it("downloads a JSON blob from an anchor attached to the document", async () => {
    const payload = { account: { id: 1, email: "owner@example.test" } };
    mocks.apiFetch.mockResolvedValue(payload);
    const createObjectURL = vi.fn((blob: Blob) => {
      void blob;
      return "blob:account-export";
    });
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
    let connectedWhenClicked: boolean | undefined;
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        connectedWhenClicked = document.body.contains(this);
      });
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);

    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));

    await waitFor(() =>
      expect(mocks.toastSuccess).toHaveBeenCalledWith(
        "Your account data export was downloaded.",
      ),
    );
    // A download only starts from an anchor that is in the document, and the
    // saved file has to be typed as JSON for the browser to name it sensibly.
    expect(connectedWhenClicked).toBe(true);
    const blob = createObjectURL.mock.calls[0][0];
    expect(blob.type).toBe("application/json");
    expect(await blob.text()).toBe(JSON.stringify(payload, null, 2));
    click.mockRestore();
  });

  it("does not fire the export success toast after the dialog closed mid-download", async () => {
    // RT-O-8: the download itself was user-initiated and may finish, but the
    // completion toast belongs to the dialog lifecycle — closing the dialog
    // bumps dialogEpoch, so a late success must not surface a stale toast.
    let resolveExport: ((payload: unknown) => void) | undefined;
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

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await act(async () => resolveExport?.({ account: { id: 1 } }));

    expect(mocks.toastSuccess).not.toHaveBeenCalledWith(
      "Your account data export was downloaded.",
    );
  });

  it("clears a password error when the dialog is reopened", async () => {
    mocks.mutateAsync.mockRejectedValue(new ApiError(400, "The current password is incorrect."));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    let dialog = await openAccount(user);
    await fillPasswordChange(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The current password is incorrect.",
    );

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await openAccount(user);

    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("New password")).toHaveValue("");
  });

  it("clears an export error when the dialog is reopened", async () => {
    mocks.apiFetch.mockRejectedValue(new ApiError(503, "Export service unavailable."));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    let dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Download my data" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Export service unavailable.",
    );

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await openAccount(user);

    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("clears a deletion error when the dialog is reopened", async () => {
    mocks.apiFetch.mockRejectedValue(new ApiError(409, "Owned farms block account deletion."));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    let dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    await user.type(
      within(dialog).getByLabelText("Current password to delete account"),
      "owner-password",
    );
    await user.click(within(dialog).getByRole("button", { name: "Delete account and access" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Owned farms block account deletion.",
    );

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));

    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("clears a deletion error when the confirmation is cancelled and reopened", async () => {
    mocks.apiFetch.mockRejectedValue(new ApiError(409, "Owned farms block account deletion."));
    const user = userEvent.setup();
    render(<AccountDialog name="Owner" email="owner@example.test" />);
    const dialog = await openAccount(user);
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));
    await user.type(
      within(dialog).getByLabelText("Current password to delete account"),
      "owner-password",
    );
    await user.click(within(dialog).getByRole("button", { name: "Delete account and access" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Owned farms block account deletion.",
    );

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await user.click(within(dialog).getByRole("button", { name: "Delete my account…" }));

    // Cancelling is a fresh start: the previous refusal must not greet the
    // user again the moment they reopen the confirmation.
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });
});
